import json
from pathlib import Path
from copy import deepcopy
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import create_app
from app.config import Settings
from app.database import User, AgentWorkflow, AIInvocation, ImageAsset, Revision
from app.auth import hash_password, issue_token
from app.ai.schemas import DIMENSION_FIELDS, DOOR_DIMENSION_FIELDS, CADGenerationRequest
from app.ai.openai_client import AIServiceError
from app.agents.cad_agent import validate_design_specification
from app.agents import orchestrator
from examples.ai_demo.mock_gateway import MockGateway, fixtures, png_bytes


@pytest.fixture
def agents_app(tmp_path,monkeypatch):
    MockGateway.calls,MockGateway.responses,MockGateway.fail_role,MockGateway.fail_once = [],None,None,False
    monkeypatch.setattr(orchestrator,'ResponsesGateway',MockGateway)
    app = create_app(Settings(database_url=f'sqlite:///{tmp_path / "agents.db"}',storage=tmp_path/'outputs',executor='eager',
        ai_enabled=True,photo_refinement_passes=0,photo_numeric_fit_enabled=False,ai_models={k:'MOCK_MODEL_NOT_FOR_API' for k in ('vision','design','joinery','review','render','image')}))
    app.state.db.create_schema_for_tests()
    headers,ids = {},{}
    with app.state.db.sessions.begin() as session:
        for role in ('designer','operator','admin'):
            user = User(username=role,password_hash=hash_password('test-password-123'),role=role)
            session.add(user)
            session.flush()
            ids[role] = user.id
            headers[role] = {'Authorization':'Bearer '+issue_token(session,user,8)}
    with TestClient(app) as client:
        p = client.post('/api/v1/projects',json={'name':'Agent test','reviewer_ids':[ids['designer']]},headers=headers['operator']).json()['id']
        image = client.post(f'/api/v1/projects/{p}/images',headers=headers['operator'],files={'file':('cabinet.png',png_bytes(),'image/png')})
        assert image.status_code==201,image.text
        yield client,app,headers,p,image.json()['id']
    app.state.db.engine.dispose()


def begin(env,**changes):
    client,app,h,p,image = env
    body = dict(project_id=p,image_ids=[image],**changes)
    response = client.post('/api/v1/agents/run-workflow',json=body,headers=h['operator'])
    assert response.status_code==202,response.text
    return response.json()['workflow_id']


def status(env,wid):
    client,app,h,p,image = env
    response = client.get(f'/api/v1/agents/workflows/{wid}',headers=h['designer'])
    assert response.status_code==200,response.text
    return response.json()


def measured_spec(client,h,width=900):
    data = json.loads(Path('examples/cabinet_materials_review.json').read_text())
    data.update(width_mm=width,glass_edge_clearance_mm=1,door_standoff_mm=1)
    for profile in data['material_profiles']:
        response = client.post('/api/v1/catalogs/materials',headers=h['designer'],json=profile)
        assert response.status_code in (201,409),response.text
    measurements = [dict(field=f,value_mm=data[f],provenance='PHYSICALLY_MEASURED' if f in ('width_mm','height_mm','depth_mm') else 'USER_PROVIDED',
        evidence_reference='SYNTHETIC TEST MEASUREMENT / ENGINEERING INTENT, NOT FACTORY APPROVAL')
        for f in DIMENSION_FIELDS+DOOR_DIMENSION_FIELDS]
    return dict(specification=data,measurements=measurements,designer_comment='TEST ONLY: explicit verification of supplied fixture dimensions')


def verify_and_generate(env,wid):
    client,app,h,p,image = env
    verified = client.post(f'/api/v1/designs/{wid}/verify',headers=h['designer'],json=measured_spec(client,h))
    assert verified.status_code==201,verified.text
    generated = client.post(f'/api/v1/designs/{wid}/generate-cad',headers=h['operator'])
    assert generated.status_code==202,generated.text
    return verified.json(),generated.json()


def test_full_collaboration_human_gate_real_cad_and_blocked_release(agents_app):
    client,app,h,p,image = agents_app
    wid = begin(agents_app)
    before = status(agents_app,wid)
    assert before['status']=='WAITING_FOR_DESIGNER'
    assert MockGateway.calls==['vision','design','joinery','review']
    assert before['result']['design']['parameters'][0]['provenance']=='AI_ESTIMATED'
    assert before['result']['design']['parameters'][0]['suggested_value']==700
    assert {'width_mm','glass_thickness_mm'} <= {m['field'] for m in before['result']['design']['required_measurements']}
    assert before['cost']['total'] is None and before['usage']['input_tokens']==400
    assert client.post(f'/api/v1/designs/{wid}/generate-cad',headers=h['operator']).status_code==409
    assert client.post(f'/api/v1/designs/{wid}/verify',headers=h['operator'],json=measured_spec(client,h)).status_code==403
    verified,generated = verify_and_generate(agents_app,wid)
    rid,jid = verified['revision_id'],generated['job_id']
    assert client.get(f'/api/v1/jobs/{jid}',headers=h['operator']).json()['status']=='SUCCEEDED'
    after = status(agents_app,wid)
    assert after['status']=='REVIEW_REQUIRED',after
    assert after['result']['manufacturing_package']['status']=='REVIEW'
    assert len(after['result']['cad']['part_ids'])==10
    quality = after['result']['post_cad_review']
    critical = [i for i in quality['issues'] if i['code'].startswith('DETERMINISTIC_')]
    assert critical and not quality['engineering_certified']
    parts = client.get(f'/api/v1/revisions/{rid}/parts',headers=h['designer']).json()
    assert next(row for row in parts if row['part_id']=='P003')['length_mm']==864  # Human width 900, not AI estimate 700.
    report = client.post(f'/api/v1/designs/{wid}/validate',headers=h['designer'])
    assert report.status_code==200 and report.json()['has_blockers'] is True
    assert client.post(f'/api/v1/designs/{wid}/approve',headers=h['designer'],json={'decision':'APPROVED','comment':'Must remain blocked'}).status_code==409
    assert client.post(f'/api/v1/designs/{wid}/release',headers=h['designer']).status_code==409
    events = client.get(f'/api/v1/agents/workflows/{wid}/events',headers=h['designer']).json()
    assert {'tool_called','designer_verified','cad_completed','workflow_completed'} <= {e['event'] for e in events}
    assert client.get(f'/api/v1/jobs/{jid}/download',headers=h['designer']).content.startswith(b'PK')
    with app.state.db.sessions() as session:
        revision = session.get(Revision,rid)
        assert revision.source_workflow_id==wid and revision.verified_spec_hash==revision.spec_hash
        assert revision.value_provenance['values']['width_mm']['provenance']=='PHYSICALLY_MEASURED'
    reject = client.post(f'/api/v1/designs/{wid}/approve',headers=h['designer'],json={'decision':'CHANGES_REQUESTED','comment':'Supply factory joinery and glazing specifications'})
    assert reject.status_code==201,reject.text


def test_safe_provider_diagnostics_survive_workflow_failure_and_retry_reuses_design(agents_app,monkeypatch):
    client,app,h,p,image = agents_app
    provider = dict(http_status=400,type='invalid_request_error',code='unknown_parameter',
                    param='input[2].async_',request_id='req_workflow_test')
    original = orchestrator.joinery_agent.recommend
    def fail(*args,**kwargs):
        raise AIServiceError('AI_BAD_REQUEST','OpenAI rejected the request format (HTTP 400)',provider=provider)
    monkeypatch.setattr(orchestrator.joinery_agent,'recommend',fail)
    wid = begin(agents_app)
    failed = status(agents_app,wid)
    assert failed['status']=='FAILED' and failed['completed_stages']==['vision','design_0']
    assert failed['error']['provider']==provider
    events = client.get(f'/api/v1/agents/workflows/{wid}/events',headers=h['designer']).json()
    assert events[-1]['event']=='workflow_failed' and events[-1]['details']['provider']==provider
    monkeypatch.setattr(orchestrator.joinery_agent,'recommend',original)
    assert client.post(f'/api/v1/agents/workflows/{wid}/retry',headers=h['operator']).status_code==202
    assert status(agents_app,wid)['status']=='WAITING_FOR_DESIGNER'
    assert MockGateway.calls.count('vision')==1 and MockGateway.calls.count('design')==1


def test_retry_resumes_checkpoints_and_idempotency_prevents_duplicate_llm_calls(agents_app):
    client,app,h,p,image = agents_app
    MockGateway.fail_role,MockGateway.fail_once = 'design',True
    wid = begin(agents_app,idempotency_key='same-request')
    assert status(agents_app,wid)['status']=='FAILED'
    assert status(agents_app,wid)['completed_stages']==['vision']
    # Changes to a server-side catalog must not turn a repeated client request
    # into a new workflow or change the original frozen input snapshots.
    assert client.post('/api/v1/catalogs/joinery',headers=h['designer'],json=dict(
        profile_id='NEW-UNAPPROVED-TEST-PROFILE',joint_type='dowel',compatible_materials=['plywood'])).status_code==201
    same = begin(agents_app,idempotency_key='same-request')
    assert same==wid and MockGateway.calls.count('vision')==1
    response = client.post(f'/api/v1/agents/workflows/{wid}/retry',headers=h['operator'])
    assert response.status_code==202,response.text
    assert status(agents_app,wid)['status']=='WAITING_FOR_DESIGNER'
    assert MockGateway.calls.count('vision')==1 and MockGateway.calls.count('design')==2
    assert client.post(f'/api/v1/agents/workflows/{wid}/retry',headers=h['operator']).status_code==409
    conflict = client.post('/api/v1/agents/run-workflow',headers=h['operator'],json=dict(project_id=p,image_ids=[image],idempotency_key='same-request',notes='changed'))
    assert conflict.status_code==409


def test_correction_loops_and_workflow_retry_budget_are_bounded(agents_app):
    client,app,h,p,image = agents_app
    MockGateway.responses = fixtures()
    MockGateway.responses['QualityReviewResult']['revision_recommended']=True
    wid = begin(agents_app,max_corrections=1)
    assert MockGateway.calls.count('design')==2 and MockGateway.calls.count('review')==2
    assert status(agents_app,wid)['result']['automatic_corrections']==1
    MockGateway.fail_role = 'vision'
    bad = begin(agents_app)
    for _ in range(2): assert client.post(f'/api/v1/agents/workflows/{bad}/retry',headers=h['operator']).status_code==202
    assert status(agents_app,bad)['attempts']==3
    assert client.post(f'/api/v1/agents/workflows/{bad}/retry',headers=h['operator']).status_code==409


def test_images_workflow_events_and_production_files_are_private(agents_app):
    client,app,h,p,image = agents_app
    wid = begin(agents_app)
    with app.state.db.sessions.begin() as session:
        user = User(username='outside',password_hash=hash_password('outside-password'),role='operator')
        session.add(user)
        session.flush()
        outsider = {'Authorization':'Bearer '+issue_token(session,user,8)}
    for path in (f'/api/v1/images/{image}',f'/api/v1/agents/workflows/{wid}',f'/api/v1/agents/workflows/{wid}/events'):
        assert client.get(path).status_code==401
        assert client.get(path,headers=outsider).status_code==404
    assert client.post('/api/v1/ai/analyze-image',headers=outsider,json=dict(project_id=p,image_ids=[image])).status_code==404
    assert client.post(f'/api/v1/projects/{p}/images',headers=h['operator'],files={'file':('bad.svg',b'<svg/>','image/svg+xml')}).status_code==422
    path = app.state.settings.storage/'images'/f'{image}.png'
    path.write_bytes(b'tampered')
    assert client.get(f'/api/v1/images/{image}',headers=h['designer']).status_code==409


def test_ai_estimates_missing_measurements_and_changed_values_cannot_be_verified(agents_app):
    client,app,h,p,image = agents_app
    wid = begin(agents_app)
    data = measured_spec(client,h)
    invalid = deepcopy(data)
    invalid['measurements'][0]['provenance']='AI_ESTIMATED'
    assert client.post(f'/api/v1/designs/{wid}/verify',headers=h['designer'],json=invalid).status_code==422
    invalid = deepcopy(data)
    invalid['measurements'][0]['value_mm'] += 1
    assert client.post(f'/api/v1/designs/{wid}/verify',headers=h['designer'],json=invalid).status_code==422
    invalid = deepcopy(data)
    del invalid['specification']['glass_edge_clearance_mm']
    assert client.post(f'/api/v1/designs/{wid}/verify',headers=h['designer'],json=invalid).status_code==422
    assert client.post(f'/api/v1/designs/{wid}/verify',headers=h['admin'],json=data).status_code==403


def test_unsupported_family_cannot_enter_verified_cad_workflow(agents_app):
    client,app,h,p,image = agents_app
    MockGateway.responses = fixtures()
    MockGateway.responses['FurnitureVisionResult']['components'][0]['kind']='shelf'
    wid = begin(agents_app)
    assert status(agents_app,wid)['status']=='CUSTOM_DESIGN_REVIEW_REQUIRED'
    assert client.post(f'/api/v1/designs/{wid}/verify',headers=h['designer'],json=measured_spec(client,h)).status_code==409


def test_analysis_reuse_and_concept_generation_are_independent_of_cad(agents_app):
    client,app,h,p,image = agents_app
    analyzed = client.post('/api/v1/ai/analyze-image',headers=h['operator'],json=dict(project_id=p,image_ids=[image])).json()['workflow_id']
    assert status(agents_app,analyzed)['status']=='SUCCEEDED'
    proposal = client.post('/api/v1/ai/generate-design',headers=h['operator'],json=dict(project_id=p,source_workflow_id=analyzed)).json()['workflow_id']
    assert status(agents_app,proposal)['status']=='WAITING_FOR_DESIGNER' and MockGateway.calls.count('vision')==1
    concept = client.post('/api/v1/ai/generate-concepts',headers=h['operator'],json=dict(project_id=p,prompt='Studio cabinet concept')).json()['workflow_id']
    result = status(agents_app,concept)
    assert result['status']=='SUCCEEDED',result
    assert result['result']['image']['kind']=='AI_CONCEPT' and result['usage']['generated_images']==1
    asset = result['result']['image']
    assert client.get(asset['download_url'],headers=h['designer']).content.startswith(b'\x89PNG')
    assert client.post('/api/v1/ai/generate-design',headers=h['operator'],json=dict(project_id=p,image_ids=[asset['id']])).status_code==422
    MockGateway.fail_role='image'
    failed = client.post('/api/v1/ai/generate-concepts',headers=h['operator'],json=dict(project_id=p,prompt='Failure test')).json()['workflow_id']
    assert status(agents_app,failed)['error']['code']=='IMAGE_GENERATION_FAILED'


def test_cad_presentations_keep_revision_and_actual_preview_lineage(agents_app):
    client,app,h,p,image = agents_app
    wid = begin(agents_app)
    verified,generated = verify_and_generate(agents_app,wid)
    response = client.post('/api/v1/ai/generate-render',headers=h['designer'],
        json=dict(project_id=p,revision_id=verified['revision_id'],prompt='Studio oak finish and clear glass'))
    assert response.status_code==202,response.text
    result = status(agents_app,response.json()['workflow_id'])
    assert result['status']=='SUCCEEDED',result
    image = result['result']['image']
    source = result['result']['source_cad_preview']
    assert image['kind']=='AI_PRESENTATION' and source['kind']=='CAD_PREVIEW'
    assert image['provenance']['source_revision_id']==verified['revision_id']
    assert source['provenance']['source_job_id']==generated['job_id']
    assert source['provenance']['source_cad_sha256'] and source['provenance']['source_cad_is_authoritative']
    assert source['provenance']['authoritative_geometry'] is False
    assert image['provenance']['authoritative_geometry'] is False


def test_worker_rechecks_designer_verification_and_strict_cad_tool_inputs(agents_app):
    client,app,h,p,image = agents_app
    wid = begin(agents_app)
    data = client.post(f'/api/v1/designs/{wid}/verify',headers=h['designer'],json=measured_spec(client,h)).json()
    with app.state.db.sessions.begin() as session:
        revision = session.get(Revision,data['revision_id'])
        revision.verified_spec_hash='0'*64
    assert client.post(f'/api/v1/designs/{wid}/generate-cad',headers=h['operator']).status_code==409
    assert client.post(f'/api/v1/revisions/{data["revision_id"]}/jobs',headers=h['operator']).status_code==409
    with app.state.db.sessions() as session:
        with pytest.raises(AIServiceError,match='Designer verification'):
            validate_design_specification(CADGenerationRequest(revision_id=data['revision_id'],
                specification_hash=data['specification_hash'],template_id='cabinet.v1'),session)


def test_checkpoint_corruption_blocks_resume_and_never_repeats_completed_vision(agents_app):
    client,app,h,p,image = agents_app
    MockGateway.fail_role,MockGateway.fail_once = 'design',True
    wid = begin(agents_app)
    with app.state.db.sessions.begin() as session:
        workflow = session.get(AgentWorkflow,wid)
        checkpoints = deepcopy(workflow.checkpoints)
        checkpoints['vision']['payload']['category']='tampered'
        workflow.checkpoints=checkpoints
    client.post(f'/api/v1/agents/workflows/{wid}/retry',headers=h['operator'])
    assert status(agents_app,wid)['error']['code']=='CHECKPOINT_INTEGRITY_FAILED'
    assert MockGateway.calls.count('vision')==1 and MockGateway.calls.count('design')==1


def test_usage_uses_versioned_configured_prices_without_rewriting_history(agents_app):
    client,app,h,p,image = agents_app
    rate = dict(model_id='MOCK_MODEL_NOT_FOR_API',currency='USD',input_per_million=2,output_per_million=8,
                evidence_reference='SYNTHETIC TEST PRICES, NOT OPENAI PRICING')
    assert client.post('/api/v1/ai/pricing',headers=h['designer'],json=rate).status_code==403
    assert client.post('/api/v1/ai/pricing',headers=h['admin'],json=rate).status_code==201
    wid = begin(agents_app)
    assert status(agents_app,wid)['cost']['total']=='0.0024'
    rate['input_per_million']=4
    assert client.post('/api/v1/ai/pricing',headers=h['admin'],json=rate).status_code==201
    second = begin(agents_app)
    assert status(agents_app,second)['cost']['total']=='0.0032'
    assert status(agents_app,wid)['cost']['total']=='0.0024'


def test_cad_queue_failure_can_retry_through_existing_job_api_without_losing_workflow(agents_app,monkeypatch):
    from app import main as main_module
    client,app,h,p,image = agents_app
    wid = begin(agents_app)
    response = client.post(f'/api/v1/designs/{wid}/verify',headers=h['designer'],json=measured_spec(client,h))
    assert response.status_code==201,response.text
    original = main_module.dispatch
    def unavailable(*args): raise ConnectionError('synthetic broker outage')
    monkeypatch.setattr(main_module,'dispatch',unavailable)
    failed = client.post(f'/api/v1/designs/{wid}/generate-cad',headers=h['operator'])
    assert failed.status_code==503
    workflow = status(agents_app,wid)
    assert workflow['status']=='CAD_FAILED'
    failed_job = workflow['job_id']
    monkeypatch.setattr(main_module,'dispatch',original)
    retried = client.post(f'/api/v1/jobs/{failed_job}/retry',headers=h['operator'])
    assert retried.status_code==202,retried.text
    completed = status(agents_app,wid)
    assert completed['status']=='REVIEW_REQUIRED' and completed['job_id']!=failed_job
