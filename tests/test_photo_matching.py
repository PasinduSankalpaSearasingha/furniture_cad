"""Photo identity, camera alignment, model routing and non-passing draft delivery."""
import base64
from io import BytesIO
from types import SimpleNamespace
import pytest
from PIL import Image
from pydantic import ValidationError
from app.engineering_schema import EngineeringDimensions,EngineeringReview,FurnitureAppearance,EngineeringPhotoView
from app.photo_matching import checked_visual_review,reference_camera,reference_render_size,reconstruction_models
from app.database import AgentWorkflow
from app.agents import orchestrator
from tests.test_agent_workflows import agents_app
from tests.test_engineering_cad import engineering_app,EngineeringGateway,launch


def appearance():
    return FurnitureAppearance(summary='Broad continuous supports below a curved top',critical_features=[
        dict(feature_id='V001',label='Broad support faces',visible_shape='Wide tapered faces with rounded ends',relative_layout='Paired beneath the top'),
        dict(feature_id='V002',label='Continuous junction',visible_shape='Smooth branching waist',relative_layout='Central connection')],
        continuity=['Support faces blend into the waist'],symmetry='Only the observed left/right pairing',avoid_substitutions=['No thin strips'])


def check(fid,status='MATCH'):
    return dict(feature_id=fid,status=status,component_ids=['F002'],evidence='Synthetic visual observation',correction='Retain the broad face' if status!='MATCH' else '')


@pytest.mark.parametrize('checks',[[check('V001')],[check('V001'),check('V002','MISMATCH')],
    [check('V001'),check('V002','UNVERIFIABLE')],[check('V001'),check('V002'),check('V002')],
    [check('V001'),check('V002'),check('V999')]])
def test_incomplete_or_different_visible_features_cannot_pass(checks):
    review=EngineeringReview(accepted=True,findings=[],limitations=['Synthetic review'],feature_checks=checks)
    assert not checked_visual_review(review,appearance()).accepted
    assert review.accepted  # The stored source response remains unmodified.


def test_every_visible_feature_must_match_and_an_ai_rejection_stays_rejected():
    review=EngineeringReview(accepted=True,findings=[],limitations=['Synthetic review'],feature_checks=[check('V001'),check('V002')])
    assert checked_visual_review(review,appearance()).accepted
    assert not checked_visual_review(review.model_copy(update={'accepted':False}),appearance()).accepted


def test_reference_camera_uses_the_uploaded_photo_view():
    dimensions=EngineeringDimensions(values=[dict(key=k,value=v,explanation='Fixture') for k,v in
        [('width_mm',700),('depth_mm',500),('height_mm',750)]],assumptions=['Fixture'],reference_view=dict(azimuth_deg=-60,elevation_deg=25))
    assert reference_camera({},dimensions)==dict(azimuth_deg=-60,elevation_deg=25)
    assert reference_camera({'source_photo_analysis':{'reference_view':{'azimuth_deg':-125,'elevation_deg':15}}},dimensions)==dict(azimuth_deg=-125,elevation_deg=15)
    with pytest.raises(ValidationError):reference_camera({'source_photo_analysis':{'reference_view':{'azimuth_deg':999,'elevation_deg':15}}},dimensions)


def test_reference_frame_preserves_a_wide_photo_aspect_ratio():
    buffer=BytesIO();Image.new('RGB',(1920,1080)).save(buffer,format='PNG')
    image=dict(image_url='data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode())
    assert reference_render_size([image])==(1280,720)


def test_astra_routing_preserves_measurement_and_other_roles():
    config=SimpleNamespace(ai_models={role:'gpt-6.1-sol' for role in ('vision','design','review','joinery','render')},
        photo_design_model='gpt-6-astra',photo_review_model='gpt-6-astra')
    models=reconstruction_models(config)
    assert models['design']==models['review']=='gpt-6-astra'
    assert models['vision']==models['joinery']==models['render']=='gpt-6.1-sol'
    assert config.ai_models['design']=='gpt-6.1-sol'


def test_new_generation_freezes_the_selected_photo_models(engineering_app,monkeypatch):
    client,app,headers,project,image=engineering_app
    app.state.settings.photo_design_model=app.state.settings.photo_review_model='gpt-6-astra'
    monkeypatch.setattr(orchestrator,'dispatch_workflow',lambda *a:None)
    response=client.post(f'/api/projects/{project}/generate-all',headers=headers['operator'],json=dict(image_id=image))
    assert response.status_code==202,response.text
    with app.state.db.sessions() as session:
        workflow=session.get(AgentWorkflow,response.json()['workflow_id'])
        assert workflow.model_config['design']==workflow.model_config['review']=='gpt-6-astra'
        assert workflow.model_config['vision']=='MOCK_MODEL_NOT_FOR_API'


def test_visual_mismatch_is_delivered_as_a_draft(engineering_app):
    EngineeringGateway.reject_visual=True
    result=launch(engineering_app,output_scope='WHOLE_FURNITURE')
    assert result['status']=='REVIEW_REQUIRED',result.get('error')
    assert result['result']['engineering_cad']['photo_match']['status']=='NEEDS_VISUAL_REVISION'
    assert result['result']['delivery']['status']=='PHOTO_MATCH_NEEDS_REVISION'
    assert result['result']['delivery']['photo_match']['accepted'] is False


def test_actual_cad_is_rendered_at_the_reference_camera(engineering_app,monkeypatch):
    from app import engineering_cad
    structured=EngineeringGateway.structured;render=engineering_cad.render_preview;views=[]
    def gateway(self,role,instructions,payload,schema,**options):
        result=structured(self,role,instructions,payload,schema,**options)
        if schema is EngineeringDimensions:result=result.model_copy(update={'reference_view':EngineeringPhotoView(azimuth_deg=-55,elevation_deg=22)})
        if schema is EngineeringReview and len(options.get('images',[]))>1:
            assert payload['view_inventory'][0]=='reference.png'
            assert payload['solid_checks']['valid_closed_solids']
        return result
    def preview(build,az,el,size=(620,700),meshes=None):
        views.append((az,el,size));return render(build,az,el,size=size,meshes=meshes)
    monkeypatch.setattr(EngineeringGateway,'structured',gateway)
    monkeypatch.setattr(engineering_cad,'render_preview',preview)
    result=launch(engineering_app,output_scope='WHOLE_FURNITURE')
    assert result['status']=='REVIEW_REQUIRED',result.get('error')
    assert views[0][:2]==(-55,22)
    assert result['result']['engineering_cad']['workflow']['reference_view']==dict(azimuth_deg=-55,elevation_deg=22)
