"""Missing measurements are estimates; explicit user constraints remain immutable."""
import json
import cadquery as cq
import pytest
from app.photo_schema import PhotoDesign
from app.agents.photo_agent import specification,resolve_estimated_geometry
from app.photo_cad import create_photo_furniture
from app.geometry import exact_bounds
from app.pipeline import canonical_hash
from app.database import Revision
from app.estimated_workflow import validate_estimated_revision
from app.agents import orchestrator
from examples.ai_demo.mock_gateway import MockGateway,fixtures
from tests.photo_fixtures import analysis_fixture
from tests.test_agent_workflows import agents_app


def mismatched_plan():
    return PhotoDesign(furniture_type='synthetic rounded envelope',summary='Synthetic optional-dimension regression',
        values=[dict(key=key,value=value,explanation='Synthetic unverified estimate') for key,value in
            [('width_mm',600),('depth_mm',550),('height_mm',800),('corner_radius_mm',12.5)]],
        parts=[dict(part_id='F001',label='Synthetic body',evidence='Synthetic geometry, not a photograph reconstruction',
            geometry='box',size=['width_mm-15.53','depth_mm-25','height_mm'],
            position=['width_mm/2','depth_mm/2','height_mm/2'],corner_radius='corner_radius_mm',
            material='solid_wood',color_rgb=[.5,.3,.1])],assumptions=['All dimensions are synthetic estimates'])


def test_blank_inputs_reconcile_estimates_without_resizing_or_mutating_source():
    source=mismatched_plan();original=source.model_dump(mode='json')
    before=create_photo_furniture(specification(source,{},'Before')[0])
    plan,spec,provenance,after,corrections=resolve_estimated_geometry(source,{},'After')
    assert spec.width_mm==pytest.approx(584.47) and spec.depth_mm==525 and spec.height_mm==800
    assert corrections['width_mm']==dict(estimated_value=600,resolved_value=584.47)
    assert all(value['source']=='AI_ESTIMATED' and not value['verified'] for value in provenance.values())
    assert provenance['width_mm']['basis']=='GENERATED_CAD_ENVELOPE'
    assert provenance['width_mm']['original_estimate']==600
    assert source.model_dump(mode='json')==original
    a,b=before.shapes['F001'].val(),after.shapes['F001'].val()
    assert a.Volume()==pytest.approx(b.Volume(),rel=1e-9)
    assert a.intersect(b).Volume()==pytest.approx(a.Volume(),rel=1e-9)
    assert before.registry['F001'].center_xyz_mm==pytest.approx(after.registry['F001'].center_xyz_mm)
    again=resolve_estimated_geometry(plan,{},'After')
    assert not again[-1] and canonical_hash(again[0].model_dump(mode='json'))==canonical_hash(plan.model_dump(mode='json'))


def test_partial_inputs_preserve_user_height_and_member_details():
    inputs={'known_values':[dict(key='height_mm',value=800),dict(key='corner_radius_mm',value=12.5)]}
    plan,spec,provenance,build,corrections=resolve_estimated_geometry(mismatched_plan(),inputs,'Partial inputs')
    assert spec.height_mm==800 and spec.width_mm==pytest.approx(584.47)
    values={value.key:value.value for value in plan.values}
    assert values['corner_radius_mm']==12.5
    assert provenance['height_mm']['source']=='USER_PROVIDED' and not provenance['height_mm']['verified']
    assert provenance['corner_radius_mm']['source']=='USER_PROVIDED'
    assert set(corrections)=={'width_mm','depth_mm'}


def test_supplied_width_cannot_be_changed_by_estimate_reconciliation():
    plan=mismatched_plan();before=canonical_hash(plan.model_dump(mode='json'))
    with pytest.raises(ValueError,match='Actual width_mm envelope 584.47'):
        resolve_estimated_geometry(plan,{'known_values':[dict(key='width_mm',value=600)]},'Fixed width')
    assert canonical_hash(plan.model_dump(mode='json'))==before


def test_estimation_does_not_hide_invalid_cad():
    raw=mismatched_plan().model_dump(mode='json');raw['parts'][0]['corner_radius']=500
    with pytest.raises(ValueError,match='Corner radius'):
        resolve_estimated_geometry(PhotoDesign.model_validate(raw),{},'Invalid geometry')


def test_photo_api_with_all_inputs_blank_builds_real_cad_without_another_design_call(agents_app):
    client,app,h,project,image=agents_app
    MockGateway.responses={**fixtures(),'PhotoAnalysis':analysis_fixture(),'PhotoDesign':mismatched_plan().model_dump(mode='json')}
    response=client.post('/api/v1/ai/reconstruct-furniture',headers=h['operator'],json=dict(project_id=project,image_ids=[image]))
    assert response.status_code==202,response.text
    result=client.get(response.json()['status_url'],headers=h['operator']).json()
    assert result['status']=='REVIEW_REQUIRED',result
    assert MockGateway.calls==['vision','design','review']
    assert result['result']['specification']['width_mm']==pytest.approx(584.47)
    assert result['result']['value_provenance']['width_mm']['original_estimate']==600
    assert result['result']['approved_for_manufacturing'] is False
    with app.state.db.sessions() as session:
        context=validate_estimated_revision(session.get(Revision,result['revision_id']),session)
        assert context.specification.width_mm==pytest.approx(584.47)
    solid=cq.importers.importStep(str(app.state.settings.storage/result['job_id']/'cabinet.step')).val()
    assert solid.isValid() and exact_bounds(solid).xlen==pytest.approx(584.47,abs=1e-5)


def test_studio_cad_tool_accepts_blank_estimates_and_freezes_the_resolved_plan(agents_app,monkeypatch):
    receipts=[]

    class BlankStudioGateway(MockGateway):
        def structured(self,role,instructions,payload,schema,images=(),tools=None,require_tool=None,**kwargs):
            if schema is PhotoDesign:
                assert require_tool=='cad_generation' and payload['known_values']==[]
                candidate=mismatched_plan()
                receipt=tools['cad_generation']['handler'](candidate)
                assert receipt['valid'],receipt
                receipts.append(receipt)
                self.trace(role,dict(schema=schema.__name__))
                return candidate
            return super().structured(role,instructions,payload,schema,images=images,**kwargs)

    client,app,h,project,image=agents_app
    MockGateway.responses={**fixtures(),'PhotoAnalysis':analysis_fixture()}
    monkeypatch.setattr(orchestrator,'ResponsesGateway',BlankStudioGateway)
    response=client.post(f'/api/projects/{project}/generate-cad',headers=h['operator'],
        json=dict(image_ids=[image],auto_render=False))
    assert response.status_code==202,response.text
    result=client.get(response.json()['status_url'],headers=h['operator']).json()
    assert result['status']=='REVIEW_REQUIRED',result
    assert len(receipts)==1 and receipts[0]['generated_envelope_mm']['width_mm']==pytest.approx(584.47)
    assert result['result']['specification']['width_mm']==pytest.approx(584.47)
    assert result['result']['value_provenance']['width_mm']['source']=='AI_ESTIMATED'
    with app.state.db.sessions() as session:
        context=validate_estimated_revision(session.get(Revision,result['revision_id']),session)
        assert context.specification.width_mm==pytest.approx(584.47)
    assert client.get(f'/api/projects/{project}/download/step',headers=h['operator']).status_code==200
