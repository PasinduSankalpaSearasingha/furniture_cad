"""Real curved-profile repair, protected inputs and saved-candidate recovery."""
from pathlib import Path
import pytest
from app.geometry_repairs import EdgeRoundingError,profile_corner_repair,resolve_repaired_geometry
from app.photo_schema import PhotoDesign,number
from app.agents.photo_agent import resolve_estimated_geometry
from app.engineering_cad import geometry_checks,check_supplied_dimensions
from app.storage_completion import coverage_360
from app.pipeline import canonical_hash
from app.database import AgentWorkflow
from app.agents import orchestrator
from app.ai.openai_client import AIServiceError
from tests.test_agent_workflows import agents_app
from tests.test_engineering_cad import engineering_app,EngineeringGateway,table_plan,launch


def cabinet():
    return PhotoDesign.model_validate_json((Path(__file__).parent/'fixtures/rounded_sideboard.json').read_text())


def inputs():
    return dict(output_scope='WHOLE_FURNITURE',known_values=[dict(key=k,value=v) for k,v in
        [('width_mm',2100),('depth_mm',545),('height_mm',858)]])


def test_same_requested_radius_is_applied_to_explicit_profile_corners_with_audit():
    plan=cabinet();digest=canonical_hash(plan.model_dump(mode='json'))
    with pytest.raises(EdgeRoundingError,match='F010.*edge_radius=5'):
        resolve_estimated_geometry(plan,inputs(),'Rounding regression')
    resolved,_,_,build,_,repairs=resolve_repaired_geometry(plan,inputs(),'Rounding regression')
    assert canonical_hash(plan.model_dump(mode='json'))==digest
    assert [r['part_id'] for r in repairs]==['F010','F011']
    assert all(r['radius_mm']==5 and r['requires_visual_review'] and not r['broad_face_rims_rounded'] for r in repairs)
    assert resolved.values==plan.values
    for a,b in zip(plan.parts,resolved.parts):
        assert a.outline==b.outline and a.position==b.position and a.rotation_deg==b.rotation_deg and a.size==b.size
    values={v.key:v.value for v in resolved.values}
    for part in resolved.parts[9:11]:
        assert part.edge_radius==0
        assert part.features[-1].edge_selector=='vertical_corners'
        assert number(part.features[-1].radius,values)==5
    proof=geometry_checks(build)
    assert proof['valid_closed_solids'] and proof['assembly_connected'] and proof['interference_check_passed']
    assert coverage_360(resolved,build)['rear_closed']
    assert all(v['error_mm']<=v['tolerance_mm'] for v in check_supplied_dimensions(build,inputs()['known_values']))
    assert any('broad-face rims remain unrounded' in text for text in build.warnings)


def test_explicit_rounding_inputs_and_impossible_radii_are_not_silently_revised():
    plan=cabinet();error=EdgeRoundingError('F010','Synthetic fillet failure')
    protected=inputs();protected['known_values'].append(dict(key='outer_upright_edge_radius_mm',value=15))
    assert profile_corner_repair(plan,error,protected) is None
    raw=plan.model_dump(mode='json');raw['parts'][9]['edge_radius']=1000
    assert profile_corner_repair(PhotoDesign.model_validate(raw),error,inputs()) is None
    assert profile_corner_repair(plan,ValueError('Other geometry failure'),inputs()) is None


def test_retry_revalidates_saved_geometry_before_requesting_another_plan(engineering_app,monkeypatch):
    client,app,headers,project,image=engineering_app
    dispatch=orchestrator.dispatch_workflow
    monkeypatch.setattr(orchestrator,'dispatch_workflow',lambda *a:None)
    response=client.post(f'/api/projects/{project}/generate-all',headers=headers['operator'],json=dict(image_id=image,**inputs()))
    assert response.status_code==202,response.text
    wid=response.json()['workflow_id'];plan=cabinet()
    def freeze(payload):return dict(payload=payload,sha256=canonical_hash(payload))
    dimensions=dict(values=[v.model_dump(mode='json') for v in plan.values],assumptions=plan.assumptions,
        appearance=dict(summary='Synthetic cabinet comparison',critical_features=[dict(feature_id='V001',label='Cabinet exterior',
            visible_shape='Continuous uprights and raised closed cabinet',relative_layout='Four fronts between uprights')],
            continuity=[],symmetry='Mirrored outer uprights',avoid_substitutions=[]))
    with app.state.db.sessions.begin() as session:
        row=session.get(AgentWorkflow,wid);row.state='FAILED'
        row.checkpoints=dict(engineering_dimensions=freeze(dimensions),engineering_rejected_regression=freeze(
            dict(plan=plan.model_dump(mode='json'),message='F010 edge_radius=5 mm failed')))
    structured=EngineeringGateway.structured
    def gateway(self,role,instructions,payload,schema,**options):
        assert schema is not PhotoDesign,'A saved candidate that validates must not trigger another planning request'
        return structured(self,role,instructions,payload,schema,**options)
    monkeypatch.setattr(EngineeringGateway,'structured',gateway)
    monkeypatch.setattr(orchestrator,'dispatch_workflow',dispatch)
    response=client.post(f'/api/v1/agents/workflows/{wid}/retry',headers=headers['operator'])
    assert response.status_code==202,response.text
    result=client.get(f'/api/v1/agents/workflows/{wid}',headers=headers['operator']).json()
    assert result['status']=='REVIEW_REQUIRED',result.get('error')
    workflow=result['result']['engineering_cad']['workflow']
    assert len(workflow['geometry_repairs'])==2
    assert result['result']['specification']['width_mm']==2100
    events=client.get(f'/api/v1/agents/workflows/{wid}/events',headers=headers['operator']).json()
    assert any(e['event']=='saved_geometry_revalidated' for e in events)


def test_invalid_visual_correction_retains_the_previously_valid_draft(engineering_app,monkeypatch):
    EngineeringGateway.reject_visual=True
    structured=EngineeringGateway.structured;planned=[]
    def gateway(self,role,instructions,payload,schema,images=(),tools=None,**options):
        if schema.__name__ in ('PhotoDesign','EngineeringRepairPatch'):
            planned.append(True)
            if len(planned)>1:
                plan=table_plan(720);plan.parts[1].position[0]='5000'
                from app.engineering_repairs import EngineeringRepairPatch
                patch=EngineeringRepairPatch(parts=[plan.parts[1]],summary='Synthetic invalid visual correction')
                assert not tools['cad_generation']['handler'](patch)['valid']
                raise AIServiceError('AI_TOOL_LIMIT','Synthetic invalid visual correction')
        return structured(self,role,instructions,payload,schema,images=images,tools=tools,**options)
    monkeypatch.setattr(EngineeringGateway,'structured',gateway)
    state=launch(engineering_app,output_scope='WHOLE_FURNITURE')
    assert state['status']=='REVIEW_REQUIRED',state.get('error')
    result=state['result']
    assert result['engineering_cad']['cad_export_agreement']['valid_closed_solids']
    assert result['delivery']['status']=='PHOTO_MATCH_NEEDS_REVISION'
    assert result['approved_for_manufacturing'] is False
    client,_,headers,_,_=engineering_app
    events=client.get(f'/api/v1/agents/workflows/{state["id"]}/events',headers=headers['operator']).json()
    assert any(e['event']=='visual_repair_geometry_rejected' for e in events)
