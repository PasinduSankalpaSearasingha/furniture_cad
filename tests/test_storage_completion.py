import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.agents import orchestrator
from app.agents.photo_agent import resolve_estimated_geometry
from app.database import AgentWorkflow
from app.geometry import exact_bounds
from app.photo_schema import PhotoDesign
from app.pipeline import canonical_hash
from app.storage_completion import complete_storage_plan,coverage_360,MARKER,POLICY
from app.engineering_cad import geometry_checks
from tests.test_agent_workflows import agents_app
from tests.test_engineering_cad import table_plan,engineering_app,EngineeringGateway,launch


@pytest.fixture(scope='module')
def completed_sideboard():
    plan=PhotoDesign.model_validate_json((Path(__file__).parent/'fixtures/facade_sideboard.json').read_text())
    inputs=dict(known_values=[dict(key=key,value=value) for key,value in [('width_mm',3000),('depth_mm',500),('height_mm',700)]])
    original,spec,values,build,_=resolve_estimated_geometry(plan,inputs,'Completion regression')
    completed,report=complete_storage_plan(original,build)
    final,spec,values,final_build,_=resolve_estimated_geometry(completed,inputs,'Completion regression')
    return original,build,final,final_build,report


def test_incomplete_rear_is_detected_and_completed_without_changing_front(completed_sideboard):
    original,before,final,after,report=completed_sideboard
    assert not coverage_360(original,before)['rear_closed']
    assert coverage_360(final,after)['rear_coverage']==1
    assert len(report['added_parts'])==24 and len(final.parts)==37
    assert sum('drawer' in p.label and p.evidence.startswith(MARKER) for p in final.parts)==16
    for pid in before.shapes:
        a,b=before.shapes[pid].val(),after.shapes[pid].val()
        assert a.Volume()==pytest.approx(b.Volume(),rel=1e-9,abs=1e-5)
        ba,bb=exact_bounds(a),exact_bounds(b)
        assert [getattr(ba,key) for key in ('xmin','xmax','ymin','ymax','zmin','zmax')]==pytest.approx(
            [getattr(bb,key) for key in ('xmin','xmax','ymin','ymax','zmin','zmax')],abs=1e-6)
    assert geometry_checks(after)['assembly_connected']
    assert not coverage_360(final,after)['hidden_construction_verified']


def test_completion_is_idempotent_and_preserves_open_furniture(completed_sideboard):
    _,_,final,build,_=completed_sideboard
    unchanged,_=complete_storage_plan(final,build)
    assert canonical_hash(unchanged.model_dump(mode='json'))==canonical_hash(final.model_dump(mode='json'))
    table=table_plan();_,_,_,table_build,_=resolve_estimated_geometry(table,dict(known_values=[]),'Table')
    assert complete_storage_plan(table,table_build)[1]['applicable'] is False
    raw=table.model_dump(mode='json');raw['furniture_type']='Intentionally open-back cabinet'
    assert complete_storage_plan(PhotoDesign.model_validate(raw),table_build)[1]['applicable'] is False


def simple_cabinet():
    values=dict(width_mm=720,depth_mm=500,height_mm=750,carcass_panel_thickness_mm=18,front_panel_thickness_mm=18)
    parts=[]
    def add(label,size,position):
        parts.append(dict(part_id=f'F{len(parts)+1:03d}',label=label,geometry='box',size=size,position=position,
            material='solid_wood',color_rgb=[.4,.3,.2],evidence='Synthetic exterior cabinet fixture'))
    add('Top',['width_mm','depth_mm',18],['width_mm/2','depth_mm/2','height_mm-9'])
    add('Bottom',['width_mm-36','depth_mm-18',18],['width_mm/2','(depth_mm+18)/2',9])
    for x in (9,'width_mm-9'):add('Side',[18,'depth_mm-18','height_mm-36'],[x,'(depth_mm+18)/2','height_mm/2'])
    add('Door front',['width_mm-36',18,'height_mm-36'],['width_mm/2',9,'height_mm/2'])
    return PhotoDesign(furniture_type='Closed storage cabinet',summary='Synthetic incomplete storage',
        values=[dict(key=key,value=value,explanation='Synthetic estimate') for key,value in values.items()],
        parts=parts,assumptions=['Synthetic test data, unverified'])


@pytest.mark.parametrize('back_height,closed',[(562,True),(0,False),(280,False)])
def test_raised_cabinet_rear_check_excludes_legs_but_detects_missing_back(back_height,closed):
    raw=simple_cabinet().model_dump(mode='json')
    # The cabinet is raised 170 mm on legs, with no base-height parameter.
    raw['parts'][1]['position'][2]=179
    for part in raw['parts'][2:4]:
        part['position'][2]=451;part['size'][2]=562
    raw['parts'][4]['position'][2]=460;raw['parts'][4]['size'][2]=544
    for x,y in [(9,33),(711,33),(9,485),(711,485)]:
        raw['parts'].append(dict(part_id=f'F{len(raw["parts"])+1:03d}',label='Raised cabinet foot',geometry='box',
            size=[18,30,170],position=[x,y,85],material='solid_wood',color_rgb=[.4,.3,.2],evidence='Synthetic support'))
    if back_height:
        raw['parts'].append(dict(part_id='F010',label='Cabinet back',geometry='box',size=[684,12,back_height],
            position=[360,494,170+back_height/2],material='plywood',color_rgb=[.4,.3,.2],evidence='Synthetic rear closure'))
    plan=PhotoDesign.model_validate(raw)
    resolved,_,_,build,_=resolve_estimated_geometry(plan,dict(known_values=[]),'Raised cabinet regression')
    proof=coverage_360(resolved,build)
    assert proof['rear_closed'] is closed
    assert proof['rear_sample_z_range_mm']==pytest.approx([188,732])
    if closed:assert proof['rear_coverage']==1


def test_local_completion_api_builds_new_revision_without_ai_and_is_idempotent(agents_app,monkeypatch):
    client,app,headers,project,image=agents_app
    def forbidden(*args,**kwargs):raise AssertionError('Saved model completion must not initialize an AI gateway')
    monkeypatch.setattr(orchestrator,'ResponsesGateway',forbidden)
    plan=simple_cabinet()
    raw=dict(image_ids=[image],image_id=image,known_values=[dict(key='width_mm',value=720)],
        cad_source='ENGINEERING_CAD',engineering_integration={'version':'CADQUERY_SOLIDS_V1','upstream_commit':__import__('app.engineering_upstream',fromlist=['provenance']).provenance()['commit']})
    def frozen(payload):return dict(payload=payload,sha256=canonical_hash(payload))
    with app.state.db.sessions.begin() as session:
        from app.database import Project
        owner=session.get(Project,project).owner_id
        source=AgentWorkflow(project_id=project,created_by=owner,mode='IMAGE_TO_CAD',state='REVIEW_REQUIRED',
            input_data=raw,input_hash=canonical_hash(raw),model_config={},checkpoints={
                'engineering_plan':frozen(plan.model_dump(mode='json')),
                'engineering_assembly':frozen(dict(furniture_type=plan.furniture_type,assumptions=plan.assumptions,
                    parts=[dict(part_id=p.part_id,label=p.label,description=p.evidence,visible_evidence=p.evidence,
                        evidence_basis='ASSUMED_COMPLETION' if p.part_id=='F005' else 'IMAGE_OBSERVED') for p in plan.parts])),
                'engineering_dimensions':frozen(dict(values=[v.model_dump() for v in plan.values],assumptions=plan.assumptions))})
        session.add(source);session.flush();source_id=source.id
    response=client.post(f'/api/projects/{project}/complete-360',headers=headers['operator'],json=dict(source_workflow_id=source_id))
    assert response.status_code==202,response.text
    state=client.get(response.json()['status_url'],headers=headers['operator']).json()
    assert state['status']=='REVIEW_REQUIRED',state.get('error')
    result=state['result'];proof=result['engineering_cad']['cad_export_agreement']
    assert proof['part_count']==6 and proof['geometry_checks']['full_360']['rear_closed']
    assert {'front','rear','left','right','top','bottom'}<=proof['view_files'].keys()
    assert result['engineering_cad']['workflow']['completion_of']==source_id
    assert result['engineering_cad']['workflow']['assembly']['parts'][4]['evidence_basis']=='ASSUMED_COMPLETION'
    assert not result['engineering_cad']['visual_review']['visual']['accepted']
    assert result['value_provenance']['completion_back_thickness_mm']['source']=='ASSUMED_COMPLETION'
    assert state['usage']['input_tokens']==0
    again=client.post(f'/api/projects/{project}/complete-360',headers=headers['operator'],json=dict(source_workflow_id=source_id))
    assert again.json()['workflow_id']==state['id']
    assert client.post(f'/api/projects/{uuid4()}/complete-360',headers=headers['operator'],json=dict(source_workflow_id=source_id)).status_code==404


def test_new_upload_completes_storage_before_seven_view_review(engineering_app,monkeypatch):
    monkeypatch.setattr('tests.test_engineering_cad.table_plan',lambda width=700:simple_cabinet())
    reviewed_images=[]
    structured=EngineeringGateway.structured
    def spy(self,role,instructions,payload,schema,images=(),**options):
        if schema.__name__=='EngineeringReview':reviewed_images.append(len(images))
        return structured(self,role,instructions,payload,schema,images=images,**options)
    monkeypatch.setattr(EngineeringGateway,'structured',spy)
    state=launch(engineering_app)
    assert state['status']=='REVIEW_REQUIRED',state.get('error')
    result=state['result'];proof=result['engineering_cad']['cad_export_agreement']
    assert proof['part_count']==6 and proof['geometry_checks']['full_360']['rear_closed']
    assert reviewed_images==[1,8]  # Reference alone, then reference plus seven CAD views.
    assert result['value_provenance']['completion_back_thickness_mm']['source']=='ASSUMED_COMPLETION'


def test_completion_rejects_tampered_saved_inputs_and_checkpoints():
    from app.storage_completion_service import completion_checkpoints
    payload=simple_cabinet().model_dump(mode='json')
    source=SimpleNamespace(input_data={},input_hash=canonical_hash({}),checkpoints={
        'engineering_plan':dict(payload=payload,sha256=canonical_hash(payload))})
    source.input_data['notes']='Changed'
    with pytest.raises(ValueError,match='inputs changed'):completion_checkpoints(source,'Integrity test')
    source.input_data={};payload['summary']='Changed'
    with pytest.raises(ValueError,match='checkpoint changed'):completion_checkpoints(source,'Integrity test')
