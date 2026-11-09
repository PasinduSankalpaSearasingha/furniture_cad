"""Single final AI correction with real CAD and synthetic provider responses."""
import base64
from io import BytesIO
import json
from pathlib import Path
from PIL import Image
import pytest
from app.agents.final_correction import FinalCADReview, comparison_tile, VIEWS
from app.ai.openai_client import AIServiceError
from app.database import AgentWorkflow, Revision
from app.agents import orchestrator
from tests.test_agent_workflows import agents_app
from tests.test_engineering_cad import engineering_app, EngineeringGateway, launch


def png(color='red', size=(80, 40)):
    stream=BytesIO();Image.new('RGB',size,color).save(stream,format='PNG');return stream.getvalue()


def test_single_tile_preserves_all_ordered_views_and_original_aspect_ratio():
    pixels, inventory=comparison_tile([png()], {view:png('blue',(40,80)) for view in VIEWS})
    with Image.open(BytesIO(pixels)) as sheet:
        assert sheet.size==(1698,1278)
        assert sheet.getpixel((12+275,12+30+190))==(255,0,0)
        # A wide photo fits inside its cell without stretching to its height.
        assert sheet.getpixel((20,12+30+10))==(255,255,255)
    assert len(inventory)==8
    assert inventory[0]['label']=='REFERENCE PHOTO 1'
    assert [cell['label'] for cell in inventory[1:]]==['ACTUAL CAD / '+view.upper() for view in VIEWS]
    with pytest.raises(ValueError):comparison_tile([png()], {'front':png()})


def test_preview_failure_retains_actual_cad_and_skips_paid_review(engineering_app,monkeypatch):
    from app.agents import final_correction
    from app.cad_meshing import CADTriangleBudgetError
    def failed(*a,**k):raise CADTriangleBudgetError('Synthetic preview budget reached')
    monkeypatch.setattr(final_correction,'render_tile',failed)
    state=launch(engineering_app,output_scope='WHOLE_FURNITURE',final_ai_correction=True)
    assert state['status']=='REVIEW_REQUIRED' and state['error'] is None
    assert EngineeringGateway.calls==['EngineeringDimensions','PhotoDesign']
    result=state['result'];engineering=result['engineering_cad']
    assert engineering['status']=='PREVIEW_UNAVAILABLE_DRAFT'
    report=engineering['workflow']['final_ai_correction']
    assert report['input_tile'] is None and not report['request_reserved'] and not report['response_received']
    assert engineering['cad_export_agreement']['valid_closed_solids']
    assert result['delivery']['items'] and not engineering['visual_review']['visual']['accepted']


def test_export_mesh_failure_still_returns_saved_plan_and_reference_report(engineering_app,monkeypatch):
    from app.cad_meshing import CADTriangleBudgetError
    def failed(*a,**k):raise CADTriangleBudgetError('Synthetic exported mesh budget reached')
    monkeypatch.setattr('app.review_mesh.export_glb',failed)
    state=launch(engineering_app,output_scope='WHOLE_FURNITURE')
    assert state['status']=='REVIEW_REQUIRED' and state['error'] is None
    output=state['result']['available_output']
    assert not output['has_cad'] and output['has_saved_cad_plan']
    assert len(output['cad_plan']['parts'])==5 and output['references'] and output['dimensions']
    assert output['stop_reason']['code']=='CAD_PREVIEW_UNAVAILABLE'
    client,app,headers,_,_=engineering_app
    assert client.get(f'/api/v1/agents/workflows/{state["id"]}/result',headers=headers['operator']).status_code==200


def test_resume_after_second_candidate_preview_failure_reuses_paid_plan(engineering_app,monkeypatch):
    from app.agents import final_correction
    from app.photo_schema import PhotoDesign
    from app.engineering_repairs import EngineeringRepairPatch
    from tests.test_engineering_cad import table_plan
    install_gateway(monkeypatch,'accepted');original=EngineeringGateway.structured
    def provider(self,role,instructions,payload,schema,images=(),tools=None,**options):
        plan=table_plan(720)
        if schema is PhotoDesign:
            self.calls.append('PhotoDesign');plan.parts[1].position[0]=5000
            assert not tools['cad_generation']['handler'](plan)['valid']
            raise AIServiceError('AI_TOOL_LIMIT','Synthetic first attempt exhausted')
        if schema is EngineeringRepairPatch:
            self.calls.append('EngineeringRepairPatch')
            patch=EngineeringRepairPatch(parts=[plan.parts[1]],summary='Restore the displaced leg')
            assert tools['cad_generation']['handler'](patch)['valid']
            return patch
        return original(self,role,instructions,payload,schema,images=images,tools=tools,**options)
    monkeypatch.setattr(EngineeringGateway,'structured',provider)
    render=final_correction.render_tile
    def interrupted(*a,**k):raise RuntimeError('Synthetic interruption before tile completion')
    monkeypatch.setattr(final_correction,'render_tile',interrupted)
    state=launch(engineering_app,output_scope='WHOLE_FURNITURE',final_ai_correction=True)
    assert state['status']=='REVIEW_REQUIRED' and state['error'] is None
    assert state['result']['available_output']['has_saved_cad_plan']
    client,app,headers,_,_=engineering_app
    with app.state.db.sessions() as session:
        assert 'engineering_candidate_1' in session.get(AgentWorkflow,state['id']).checkpoints
    monkeypatch.setattr(final_correction,'render_tile',render)
    assert client.post(f'/api/v1/agents/workflows/{state["id"]}/retry',headers=headers['operator']).status_code==202
    resumed=client.get(f'/api/v1/agents/workflows/{state["id"]}',headers=headers['operator']).json()
    assert resumed['status']=='REVIEW_REQUIRED',resumed.get('error')
    assert EngineeringGateway.calls==['EngineeringDimensions','PhotoDesign','EngineeringRepairPatch','FinalCADReview']


def install_gateway(monkeypatch, mode='patch'):
    original=EngineeringGateway.structured
    captured=[]
    def structured(self,role,instructions,payload,schema,images=(),tools=None,**kwargs):
        if schema is not FinalCADReview:
            return original(self,role,instructions,payload,schema,images=images,tools=tools,**kwargs)
        self.calls.append('FinalCADReview');captured.append((payload,images,kwargs))
        assert role=='review' and len(images)==1 and tools is None
        assert payload['image_input_count']==1 and len(payload['cell_inventory'])==8
        assert self.models.get('fallback','')==''
        with Image.open(BytesIO(base64.b64decode(images[0]['image_url'].split(',',1)[1]))) as sheet:
            assert sheet.width>1000
        if mode=='api_error':raise AIServiceError('AI_AUTHENTICATION_FAILED','Synthetic provider rejection')
        if mode=='crash':raise RuntimeError('Synthetic interruption after request reservation')
        patch=None
        if mode in ('patch','bad_dimension','bad_geometry','bad_scope'):
            if mode=='bad_dimension':
                patch=dict(values=[dict(key='width_mm',value=721,explanation='Intentionally wrong')],summary='Invalid locked size change')
            elif mode=='bad_scope':
                patch=dict(summary='Invalid furniture item reclassification',
                    values=[dict(key=key,value=value,explanation='Unjustified separate-item dimension') for key,value in
                        [('side_item_width_mm',40),('side_item_depth_mm',40),('side_item_height_mm',720)]],
                    separate_items=[dict(item_id='A001',label='Detached leg',part_ids=['F002'],width_key='side_item_width_mm',
                        depth_key='side_item_depth_mm',height_key='side_item_height_mm',evidence='Unjustified reclassification')])
            else:
                part=dict(payload['plan']['parts'][0]);part['corner_radius']=400 if mode=='bad_geometry' else 8
                patch=dict(parts=[part],summary='Synthetic top corner correction')
        return schema.model_validate(dict(accepted=mode=='accepted',findings=[],limitations=['Synthetic review, no physical verification'],
            feature_checks=[dict(feature_id='V001',status='MATCH' if mode=='accepted' else 'MISMATCH',
                component_ids=['F001'],evidence='Synthetic feature check',correction='' if mode=='accepted' else 'Round the top corners')],
            correction=patch))
    monkeypatch.setattr(EngineeringGateway,'structured',structured)
    return captured


def test_one_final_response_applies_patch_rebuilds_and_exports_selected_geometry(engineering_app,monkeypatch):
    captured=install_gateway(monkeypatch)
    state=launch(engineering_app,output_scope='WHOLE_FURNITURE',final_ai_correction=True)
    assert state['status']=='REVIEW_REQUIRED',state
    assert EngineeringGateway.calls==['EngineeringDimensions','PhotoDesign','FinalCADReview']
    assert len(captured)==1
    result=state['result'];report=result['engineering_cad']['workflow']['final_ai_correction']
    assert report['correction_applied'] and report['status']=='CORRECTED_REVIEW_REQUIRED'
    assert report['source_plan_sha256']!=report['selected_plan_sha256']
    assert report['input_tile']['directory']!=report['selected_tile']['directory']
    assert report['requires_human_visual_review'] and not report['approved_for_manufacturing']
    assert result['specification']['photo_design']['parts'][0]['corner_radius']==8
    assert result['specification']['width_mm']==720
    assert not result['engineering_cad']['photo_match']['accepted']
    client,app,h,project,image=engineering_app
    with app.state.db.sessions() as session:
        row=session.get(AgentWorkflow,state['id'])
        assert 'engineering_final_request_reserved' in row.checkpoints
        assert 'engineering_final_ai_response' in row.checkpoints
    folder=app.state.settings.storage/state['job_id']
    assert json.loads((folder/'engineering_workflow.json').read_text())['final_ai_correction']==report
    import cadquery as cq
    solids=cq.importers.importStep(str(folder/'cabinet.step')).solids().vals()
    top=max(solids,key=lambda shape:shape.BoundingBox().xlen*shape.BoundingBox().ylen)
    assert top.Volume()<720*500*30


def test_accepted_comparison_needs_no_correction_or_second_ai_call(engineering_app,monkeypatch):
    install_gateway(monkeypatch,'accepted')
    state=launch(engineering_app,output_scope='WHOLE_FURNITURE',final_ai_correction=True)
    assert state['status']=='REVIEW_REQUIRED',state
    report=state['result']['engineering_cad']['workflow']['final_ai_correction']
    assert report['status']=='VISUALLY_REVIEWED' and not report['correction_applied']
    assert state['result']['engineering_cad']['photo_match']['accepted']
    assert EngineeringGateway.calls.count('FinalCADReview')==1


@pytest.mark.parametrize('mode',['bad_dimension','bad_geometry','bad_scope'])
def test_invalid_final_patch_retains_previous_valid_cad_without_another_request(engineering_app,monkeypatch,mode):
    install_gateway(monkeypatch,mode)
    state=launch(engineering_app,output_scope='WHOLE_FURNITURE',final_ai_correction=True)
    assert state['status']=='REVIEW_REQUIRED',state
    report=state['result']['engineering_cad']['workflow']['final_ai_correction']
    assert report['status']=='CORRECTION_REJECTED' and not report['correction_applied']
    assert state['result']['specification']['photo_design']['parts'][0]['corner_radius']==0
    assert state['result']['specification']['width_mm']==720
    assert EngineeringGateway.calls.count('FinalCADReview')==1


def test_api_failure_keeps_explicitly_unreviewed_draft(engineering_app,monkeypatch):
    install_gateway(monkeypatch,'api_error')
    state=launch(engineering_app,output_scope='WHOLE_FURNITURE',final_ai_correction=True)
    assert state['status']=='REVIEW_REQUIRED',state
    report=state['result']['engineering_cad']['workflow']['final_ai_correction']
    assert report['status']=='FINAL_REVIEW_UNAVAILABLE'
    assert report['error_code']=='AI_AUTHENTICATION_FAILED' and not report['response_received']
    assert not state['result']['engineering_cad']['photo_match']['accepted']
    assert EngineeringGateway.calls.count('FinalCADReview')==1


def retry(env,wid):
    client,app,headers,project,image=env
    response=client.post(f'/api/v1/agents/workflows/{wid}/retry',headers=headers['operator'])
    assert response.status_code==202,response.text
    return client.get(f'/api/v1/agents/workflows/{wid}',headers=headers['operator']).json()


def test_interrupted_reserved_request_is_not_submitted_again_on_retry(engineering_app,monkeypatch):
    install_gateway(monkeypatch,'crash')
    state=launch(engineering_app,output_scope='WHOLE_FURNITURE',final_ai_correction=True)
    assert state['status']=='REVIEW_REQUIRED' and state['result']['available_output']['has_saved_cad_plan'],state
    resumed=retry(engineering_app,state['id'])
    assert resumed['status']=='REVIEW_REQUIRED',resumed
    report=resumed['result']['engineering_cad']['workflow']['final_ai_correction']
    assert report['status']=='INTERRUPTED_FINAL_REQUEST' and not report['response_received']
    assert EngineeringGateway.calls.count('FinalCADReview')==1


def test_saved_final_response_resumes_correction_without_repeating_api(engineering_app,monkeypatch):
    install_gateway(monkeypatch)
    from app.agents import final_correction
    merge=final_correction.merge_repair
    def interrupted(*a,**k):raise RuntimeError('Synthetic crash after the response was checkpointed')
    monkeypatch.setattr(final_correction,'merge_repair',interrupted)
    state=launch(engineering_app,output_scope='WHOLE_FURNITURE',final_ai_correction=True)
    assert state['status']=='REVIEW_REQUIRED' and state['result']['available_output']['has_saved_cad_plan'],state
    monkeypatch.setattr(final_correction,'merge_repair',merge)
    resumed=retry(engineering_app,state['id'])
    assert resumed['status']=='REVIEW_REQUIRED',resumed
    assert resumed['result']['engineering_cad']['workflow']['final_ai_correction']['correction_applied']
    assert EngineeringGateway.calls.count('FinalCADReview')==1


def test_frozen_reexport_preserves_review_without_another_tile_or_ai_call(engineering_app,monkeypatch):
    install_gateway(monkeypatch)
    source=launch(engineering_app,output_scope='WHOLE_FURNITURE',final_ai_correction=True)
    assert source['status']=='REVIEW_REQUIRED',source
    calls=list(EngineeringGateway.calls)
    copied=launch(engineering_app,key='final-reexport',output_scope='WHOLE_FURNITURE',
        engineering_reference_workflow_id=source['id'])
    assert copied['status']=='REVIEW_REQUIRED',copied
    assert copied['result']['engineering_cad']['workflow']['final_ai_correction']==source['result']['engineering_cad']['workflow']['final_ai_correction']
    assert EngineeringGateway.calls==calls


def test_server_setting_disables_final_mode_and_keeps_existing_workflow(engineering_app):
    client,app,h,project,image=engineering_app
    app.state.settings.cad_final_correction_enabled=False
    state=launch(engineering_app,output_scope='WHOLE_FURNITURE',final_ai_correction=True)
    assert state['status']=='REVIEW_REQUIRED',state
    assert state['result']['engineering_cad']['workflow']['final_ai_correction'] is None
    assert EngineeringGateway.calls==['EngineeringDimensions','PhotoDesign','EngineeringReview']


def test_final_comparison_files_follow_project_cleanup_scope(tmp_path):
    from types import SimpleNamespace
    from uuid import uuid4
    from app.project_deletion import project_artifacts
    wid=str(uuid4())
    own=tmp_path/f'engineering_final_{wid}_input_lease';own.mkdir()
    unrelated=tmp_path/f'engineering_final_{uuid4()}_input_lease';unrelated.mkdir()
    paths=project_artifacts(tmp_path,[],[SimpleNamespace(id=wid)],[])
    assert own in paths and unrelated not in paths


def test_real_sdk_sends_one_image_and_one_structured_response_request():
    import httpx2 as httpx
    from tests.test_ai_provider import gateway,response_body
    requests=[]
    def handler(request):
        if request.method=='GET':return httpx.Response(200,json={'id':'configured-model'})
        body=json.loads(request.content);requests.append(body)
        assert body['text']['format']['strict'] is True
        content=body['input'][0]['content']
        assert sum(item['type']=='input_image' for item in content)==1
        return httpx.Response(200,json=response_body(dict(accepted=False,findings=[],limitations=['Synthetic provider'],
            feature_checks=[],correction=None)))
    g=gateway(handler,models={'review':'configured-model'})
    g.config.openai_background_mode=False
    result=g.structured('review','One final correction',{},FinalCADReview,images=[dict(type='input_image',
        image_url='data:image/png;base64,'+base64.b64encode(png()).decode(),detail='high')])
    assert isinstance(result,FinalCADReview) and len(requests)==1
