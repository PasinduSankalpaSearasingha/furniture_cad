"""Synthetic provider tests with real CAD execution; live services are separately opt-in."""
import base64
import json
from pathlib import Path
from copy import deepcopy
import subprocess
import cadquery as cq
import pytest
from pydantic import ValidationError
from PIL import Image
from app.photo_schema import CADFeature, PhotoDesign
from app.cad_operations import apply_features
from app.database import AgentWorkflow, Revision, User, Project
from app.pipeline import canonical_hash, file_hash
from app.ai.openai_client import AIServiceError
from app.rendering.config import RenderOptions
from app.rendering.blender_service import discover_blender, render_geometry
from app.agents import orchestrator
from examples.ai_demo.mock_gateway import MockGateway, png_bytes
from tests.test_agent_workflows import agents_app
from tests.photo_fixtures import analysis_fixture, photo_plan_fixture
from tests.test_ai_provider import gateway, response_body
import httpx2


REVIEW=dict(summary='Synthetic test review; not a live visual evaluation',geometry_differences=['Synthetic contour difference'],
    appearance_differences=[],unknown_geometry=['Hidden joints'],corrections=[dict(correction_id='C001',component_id='F001',
        issue='Synthetic seat thickness change',proposed_operation='update_parameter',parameter_key='seat_thickness_mm',
        new_value=32,instructions='Change seat_thickness_mm to 32.',requires_confirmation=True)],
    engineering_accuracy_verified=False,approved_for_manufacturing=False)


class StudioGateway(MockGateway):
    def __init__(self,config,models,record):
        super().__init__(config,models,record);self.config=config;self.models=models
    def model_for(self,role): return self.models[role]
    def image(self,instructions,prompt,images,quality,size,output_format='png'):
        return super().image(instructions,prompt,images,quality,size)
    def structured(self,role,instructions,payload,schema,images=(),tools=None,require_tool=None,**kwargs):
        if schema.__name__=='PhotoAnalysis': result=analysis_fixture()
        elif schema.__name__=='VisualReview': result=deepcopy(REVIEW)
        else:
            known={v['key']:v['value'] for v in payload.get('known_values',[])}
            result=photo_plan_fixture(**known).model_dump(mode='json')
            if tools:
                response=tools['cad_generation']['handler'](PhotoDesign.model_validate(result))
                assert response['valid'],response
                self.record('tool_called',dict(name='cad_generation',arguments={'synthetic_test':True}))
        self.trace(role,dict(schema=schema.__name__))
        return schema.model_validate(result)


def setup_studio(monkeypatch): monkeypatch.setattr(orchestrator,'ResponsesGateway',StudioGateway)


def workflow_status(client,h,response):
    assert response.status_code==202,response.text
    return client.get(response.json()['status_url'],headers=h['operator']).json()


def test_concept_persists_valid_image_then_selection_to_real_cad_and_parameter_edit(agents_app,monkeypatch):
    setup_studio(monkeypatch);c,app,h,p,image=agents_app
    result=workflow_status(c,h,c.post(f'/api/projects/{p}/generate-concept',headers=h['operator'],json=dict(prompt='Synthetic chair')))
    assert result['status']=='SUCCEEDED',result
    concept=result['result']['images'][0]
    assert concept['kind']=='AI_CONCEPT_IMAGE'
    assert c.get(concept['download_url'],headers=h['operator']).headers['content-type']=='image/png'
    assert c.get(concept['download_url']).status_code==401
    assert c.post(f'/api/projects/{p}/concepts/{concept["id"]}/select',headers=h['operator']).status_code==200
    assert c.get(concept['provenance']['original_download_url'],headers=h['operator']).status_code==200
    assert c.get(concept['provenance']['original_download_url']).status_code==401
    assert c.get(f'/api/projects/{p}',headers=h['operator']).json()['selected_concept_id']==concept['id']
    built=workflow_status(c,h,c.post(f'/api/projects/{p}/generate-cad',headers=h['operator'],json=dict(concept_id=concept['id'],
        known_values=[dict(key='width_mm',value=700)],auto_render=False,idempotency_key='studio-cad')))
    assert built['status']=='REVIEW_REQUIRED',built
    assert built['result']['specification']['width_mm']==700
    assert built['result']['value_provenance']['width_mm']['verified'] is False
    source=app.state.settings.storage/built['job_id']
    assert len(cq.importers.importStep(str(source/'cabinet.step')).solids().vals())==7
    assert c.get(f'/api/projects/{p}/download/step',headers=h['operator']).status_code==200
    edited=workflow_status(c,h,c.patch(f'/api/projects/{p}/parameters',headers=h['operator'],json=dict(
        parameter_changes=[dict(key='width_mm',value=740)],auto_render=False)))
    assert edited['status']=='REVIEW_REQUIRED',edited
    assert edited['revision_id']!=built['revision_id']
    assert edited['result']['specification']['width_mm']==740
    with app.state.db.sessions() as session:
        assert session.get(Revision,built['revision_id']).specification['width_mm']==700
    assert c.get(f'/api/projects/{p}/bom',headers=h['operator']).json()['approved_for_manufacturing'] is False
    assert c.post(f'/api/v1/revisions/{edited["revision_id"]}/release',headers=h['designer']).status_code==409


@pytest.mark.parametrize('operation', ['hole','dowel','confirmat','groove','dado','rabbet','pocket','mortise','tenon','fillet','chamfer'])
def test_controlled_features_modify_real_valid_solids(operation):
    shape=cq.Workplane('XY').box(100,80,30)
    feature=CADFeature(operation=operation,position=[0,0,10] if operation=='tenon' else [0,0,0],
        size=[20,20,20],radius=3,depth=20)
    changed=apply_features(shape,[feature],{})
    assert changed.val().isValid() and len(changed.solids().vals())==1
    assert changed.val().Volume()!=pytest.approx(shape.val().Volume())


def test_invalid_feature_and_arbitrary_operation_rejected():
    with pytest.raises(ValidationError): CADFeature(operation='exec_python')
    with pytest.raises(ValueError,match='did not affect'):
        apply_features(cq.Workplane('XY').box(100,80,30),[CADFeature(operation='hole',position=[999,0,0])],{})
    with pytest.raises(Exception):
        apply_features(cq.Workplane('XY').box(100,80,30),[CADFeature(operation='fillet',radius=1000)],{})


def test_direct_image_sdk_generate_and_edit_use_supported_parameters():
    sent=[]
    def handler(request):
        if request.method=='GET': return httpx2.Response(200,json={'id':'image-model'})
        sent.append(request)
        if request.url.path.endswith('/generations'):
            body=json.loads(request.content);assert body['model']=='image-model' and body['n']==1
            assert body['output_format']=='png' and 'response_format' not in body
        else:
            assert '/edits' in request.url.path and b'filename="reference_0.png"' in request.content
        return httpx2.Response(200,json={'created':1,'data':[{'b64_json':base64.b64encode(png_bytes()).decode()}],
            'usage':{'input_tokens':5,'output_tokens':10,'total_tokens':15}})
    g=gateway(handler,{'image':'image-model'})
    assert g.image_api('Synthetic desk')
    assert g.image_api('Synthetic edit',[png_bytes()])
    assert len(sent)==2


def test_missing_image_data_and_required_tool_fail_explicitly():
    def no_image(request):
        if request.method=='GET': return httpx2.Response(200,json={'id':'image-model'})
        return httpx2.Response(200,json={'created':1,'data':[]})
    with pytest.raises(AIServiceError,match='no image'): gateway(no_image,{'image':'image-model'}).image_api('fixture')
    def no_tool(request):
        if request.method=='GET': return httpx2.Response(200,json={'id':'design-model'})
        assert json.loads(request.content)['tool_choice']=={'type':'function','name':'cad_generation'}
        return httpx2.Response(200,json=response_body(photo_plan_fixture().model_dump(mode='json')))
    with pytest.raises(AIServiceError,match='required CAD tool'):
        gateway(no_tool,{'design':'design-model'}).structured('design','',{},PhotoDesign,tools={'cad_generation':dict(
            schema=PhotoDesign,description='test',handler=lambda p:{})},require_tool='cad_generation')


def test_real_sdk_function_call_executes_cad_and_returns_tool_result():
    from app.agents.photo_agent import specification,preflight
    plan=photo_plan_fixture();sent=[];executed=[]
    def handler(request):
        if request.method=='GET': return httpx2.Response(200,json={'id':'design-model'})
        body=json.loads(request.content);sent.append(body)
        if len(sent)==1:
            return httpx2.Response(200,json=response_body(output=[dict(type='function_call',id='fc_1',call_id='call_1',
                name='cad_generation',arguments=plan.model_dump_json(),status='completed')]))
        output=next(v for v in body['input'] if v.get('type')=='function_call_output')
        assert json.loads(output['output'])['solids']==7
        return httpx2.Response(200,json=response_body(plan.model_dump(mode='json')))
    def cad(arguments):
        spec,_=specification(arguments,{},'SDK CAD test');build=preflight(spec,set());executed.append(build)
        return dict(solids=len(build.parts))
    result=gateway(handler,{'design':'design-model'}).structured('design','',{},PhotoDesign,
        tools={'cad_generation':dict(schema=PhotoDesign,description='Real CAD test',handler=cad)},require_tool='cad_generation')
    assert len(executed)==1 and result==plan and len(sent)==2


def test_blender_absent_and_resource_limits_are_clear(tmp_path):
    with pytest.raises(AIServiceError) as error: discover_blender(str(tmp_path/'missing.exe'))
    assert error.value.code=='BLENDER_NOT_FOUND'
    with pytest.raises(ValidationError): RenderOptions(views=[])
    with pytest.raises(ValidationError): RenderOptions(finish='unknown shader code')


def test_blender_failure_does_not_create_success(tmp_path,monkeypatch):
    from app.config import Settings
    import app.rendering.blender_service as service
    source=tmp_path/'source';source.mkdir();(source/'cabinet.glb').write_bytes(b'not used')
    (source/'parts_registry.json').write_text('[]')
    monkeypatch.setattr(service,'discover_blender',lambda _:Path('trusted-blender'))
    monkeypatch.setattr(service.subprocess,'run',lambda *a,**k:subprocess.CompletedProcess(a,1))
    with pytest.raises(AIServiceError) as error:
        render_geometry(Settings(),source,tmp_path/'failed',RenderOptions(),{})
    assert error.value.code=='BLENDER_PROCESS_FAILED'
    assert not (tmp_path/'failed'/'render_manifest.json').exists()


def test_blender_timeout_does_not_create_success(tmp_path,monkeypatch):
    from app.config import Settings
    import app.rendering.blender_service as service
    source=tmp_path/'source';source.mkdir();(source/'cabinet.glb').write_bytes(b'not used')
    (source/'parts_registry.json').write_text('[]')
    monkeypatch.setattr(service,'discover_blender',lambda _:Path('trusted-blender'))
    def timeout(*a,**kw): raise subprocess.TimeoutExpired('blender',10)
    monkeypatch.setattr(service.subprocess,'run',timeout)
    with pytest.raises(AIServiceError) as error: render_geometry(Settings(),source,tmp_path/'failed',RenderOptions(),{})
    assert error.value.code=='BLENDER_TIMEOUT'


def test_cross_project_image_and_no_reference_rejected(agents_app):
    c,app,h,p,image=agents_app
    other=c.post('/api/projects',headers=h['admin'],json={'name':'Different project'}).json()['id']
    response=c.post(f'/api/projects/{other}/generate-cad',headers=h['admin'],json={'image_ids':[image]})
    assert response.status_code==404
    assert c.post(f'/api/projects/{p}/generate-cad',headers=h['operator'],json={}).status_code==422
    assert c.get('/api/system/blender/status').status_code==401


def test_invalid_provider_raster_never_creates_concept_success(agents_app,monkeypatch):
    class InvalidRasterGateway(StudioGateway):
        def image(self,*args,**kwargs): return base64.b64encode(b'not an image').decode()
    monkeypatch.setattr(orchestrator,'ResponsesGateway',InvalidRasterGateway)
    c,app,h,p,image=agents_app
    result=workflow_status(c,h,c.post(f'/api/projects/{p}/generate-concept',headers=h['operator'],json=dict(prompt='Synthetic bad raster')))
    assert result['status']=='FAILED' and result['error']['code']=='IMAGE_GENERATION_FAILED'
    assert c.get(f'/api/projects/{p}/concepts',headers=h['operator']).json()==[]


def test_correction_cannot_change_fixed_values_and_iteration_limit(agents_app,monkeypatch):
    setup_studio(monkeypatch);c,app,h,p,image=agents_app
    built=workflow_status(c,h,c.post(f'/api/projects/{p}/generate-cad',headers=h['operator'],json=dict(image_ids=[image],
        known_values=[dict(key='width_mm',value=700)],auto_render=False)))
    assert built['status']=='REVIEW_REQUIRED',built
    with app.state.db.sessions.begin() as session:
        raw=dict(project_id=p,refinement_iteration=0)
        review=AgentWorkflow(project_id=p,created_by=session.get(Project,p).owner_id,mode='VISUAL_REVIEW',state='SUCCEEDED',
            revision_id=built['revision_id'],job_id=built['job_id'],input_data=raw,input_hash=canonical_hash(raw),model_config={},
            result={'visual_review':deepcopy(REVIEW)})
        session.add(review);session.flush();rid=review.id
    accepted=workflow_status(c,h,c.post(f'/api/projects/{p}/corrections/{rid}/C001/accept',headers=h['operator'],json={'auto_render':False}))
    assert accepted['status']=='REVIEW_REQUIRED',accepted
    assert accepted['result']['specification']['width_mm']==700
    assert next(v for v in accepted['result']['specification']['photo_design']['values'] if v['key']=='seat_thickness_mm')['value']==32
    with app.state.db.sessions.begin() as session:
        review=session.get(AgentWorkflow,rid);data=deepcopy(review.result);data['visual_review']['corrections'][0].update(parameter_key='width_mm',new_value=900)
        review.result=data
    response=c.post(f'/api/projects/{p}/corrections/{rid}/C001/accept',headers=h['operator'],json={})
    assert response.status_code==409 and 'fixed' in response.text
    with app.state.db.sessions.begin() as session:
        review=session.get(AgentWorkflow,rid);data=deepcopy(review.result);data['visual_review']['corrections'][0].update(parameter_key='seat_thickness_mm',new_value=34)
        review.result=data;review.input_data={**review.input_data,'refinement_iteration':3}
    assert c.post(f'/api/projects/{p}/corrections/{rid}/C001/accept',headers=h['operator'],json={}).status_code==409
