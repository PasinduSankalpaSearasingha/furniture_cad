"""Real SDK + temporary database tests; never contact OpenAI or saved projects."""
import json
from types import SimpleNamespace
from uuid import uuid4
import httpx2
import pytest
from openai import OpenAI, DefaultHttpxClient
from sqlalchemy import select
from app.ai import openai_client
from app.ai.openai_client import ResponsesGateway, AIServiceError
from app.ai.response_store import WorkflowResponseStore, RESPONSE_PREFIX
from app.ai.schemas import FurnitureVisionResult, QualityReviewResult, JoineryLookup
from app.agents.orchestrator import WorkflowRunner
from app.config import Settings
from app.database import Database, User, Project, AgentWorkflow, AIInvocation, AgentEvent
from examples.ai_demo.mock_gateway import fixtures
from tests.test_ai_provider import response_body
from tests.test_agent_workflows import agents_app


class Clock:
    def __init__(self): self.now = 0
    def monotonic(self): return self.now
    def sleep(self, delay): self.now += delay


@pytest.fixture
def tracked(tmp_path, monkeypatch):
    monkeypatch.setattr(openai_client, 'time', Clock())
    config = Settings(database_url=f'sqlite:///{tmp_path / "background.db"}',
        storage=tmp_path/'outputs', executor='eager', openai_background_mode=True,
        openai_poll_interval_seconds=.25)
    db = Database(config.database_url)
    db.create_schema_for_tests()
    workflow_id = str(uuid4())
    runner = WorkflowRunner(workflow_id, db, config)
    runner.stage = 'test_geometry'
    runner.inputs = {}
    with db.sessions.begin() as session:
        user = User(username='background-test', password_hash='unused-offline-test', role='operator')
        session.add(user); session.flush()
        project = Project(name='Offline background test', owner_id=user.id)
        session.add(project); session.flush()
        session.add(AgentWorkflow(id=workflow_id, project_id=project.id, created_by=user.id,
            mode='ANALYZE', state='RUNNING', stage=runner.stage, lease_token=runner.lease,
            input_data={}, input_hash='test', model_config={'vision':'configured-model'}))

    def gateway(handler, current=runner, **models):
        sdk = OpenAI(api_key='OFFLINE_TEST_KEY', base_url='http://openai.test/v1', max_retries=0,
            http_client=DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
        return ResponsesGateway(config, models or {'vision':'configured-model'}, current.record,
            sdk=sdk, response_store=WorkflowResponseStore(current))

    def restart():
        resumed = WorkflowRunner(workflow_id, db, config)
        resumed.stage, resumed.inputs = runner.stage, {}
        with db.sessions.begin() as session:
            workflow = session.get(AgentWorkflow, workflow_id)
            workflow.lease_token = resumed.lease
        return resumed

    yield SimpleNamespace(config=config, db=db, runner=runner, gateway=gateway, restart=restart)
    db.engine.dispose()


def result(response_id, status='completed', payload=None, output=None):
    body = response_body(fixtures()['FurnitureVisionResult'] if payload is None else payload,
        output=output, status=status)
    body['id'] = response_id
    body.update(parallel_tool_calls=False,tool_choice='auto',tools=[])
    body['usage']['input_tokens_details']['cache_write_tokens']=0
    body['usage']['output_tokens_details']={'reasoning_tokens':0}
    if status in ('queued','in_progress'):
        body['output'], body['usage'] = [], None
    return httpx2.Response(200, json=body)


def test_submits_background_and_saves_id_before_polling_then_replays_completed_result(tracked):
    posts, gets = [], []
    def handler(request):
        if '/models/' in request.url.path:
            return httpx2.Response(200, json={'id':'configured-model'})
        if request.method == 'POST':
            posts.append(json.loads(request.content))
            assert posts[-1]['background'] is True and posts[-1]['store'] is False
            return result('resp_saved', 'queued')
        with tracked.db.sessions() as session:
            invocation = session.scalar(select(AIInvocation))
            assert invocation.response_id == 'resp_saved'
        gets.append(request.url.path)
        assert request.extensions['timeout']['read'] <= 30
        return result('resp_saved', 'in_progress' if len(gets)==1 else 'completed')

    first = tracked.gateway(handler).structured('vision', 'Inspect photo', {}, FurnitureVisionResult)
    second = tracked.gateway(handler, tracked.restart()).structured('vision', 'Inspect photo', {}, FurnitureVisionResult)
    assert first == second and len(posts)==1 and len(gets)==2
    with tracked.db.sessions() as session:
        rows = list(session.scalars(select(AIInvocation)))
        assert len(rows)==1 and rows[0].status=='COMPLETED' and rows[0].usage['input_tokens']==100
        workflow = session.get(AgentWorkflow, tracked.runner.id)
        assert RESPONSE_PREFIX+rows[0].id in workflow.checkpoints
        assert len(list(session.scalars(select(AgentEvent).where(AgentEvent.event=='invocation_started'))))==1


@pytest.mark.parametrize('failure', ['timeout', 'rate_limit', 'server_error'])
def test_transient_get_failures_retry_only_the_saved_response(tracked, failure):
    posts, gets = [], []
    def handler(request):
        if '/models/' in request.url.path:return httpx2.Response(200,json={'id':'configured-model'})
        if request.method=='POST':
            posts.append(1);return result('resp_retry','queued')
        gets.append(1)
        if len(gets)==1:
            if failure=='timeout':raise httpx2.ReadTimeout('offline interruption',request=request)
            return httpx2.Response(429 if failure=='rate_limit' else 503,json={'error':{'code':'temporary'}})
        return result('resp_retry')
    assert tracked.gateway(handler).structured('vision','test',{},FurnitureVisionResult).category=='display cabinet'
    assert len(posts)==1 and len(gets)==2


def test_interrupted_polling_resumes_after_restart_without_submitting_or_billing_twice(tracked):
    posts, gets = [], []
    def handler(request):
        if '/models/' in request.url.path:return httpx2.Response(200,json={'id':'configured-model'})
        if request.method=='POST':
            posts.append(1);return result('resp_resume','queued')
        gets.append(1)
        if len(gets)<=3:raise httpx2.ReadTimeout('offline',request=request)
        return result('resp_resume')
    with pytest.raises(AIServiceError,match='status checks') as error:
        tracked.gateway(handler).structured('vision','test',{},FurnitureVisionResult)
    assert error.value.code=='AI_BACKGROUND_POLL_INTERRUPTED' and error.value.retryable
    with tracked.db.sessions() as session:
        invocation=session.scalar(select(AIInvocation))
        assert invocation.status=='PENDING' and invocation.response_id=='resp_resume' and invocation.usage=={}
    assert tracked.gateway(handler,tracked.restart()).structured('vision','test',{},FurnitureVisionResult).category=='display cabinet'
    assert len(posts)==1 and len(gets)==4


def test_poll_deadline_keeps_response_resumable(tracked):
    posts=[]
    def handler(request):
        if '/models/' in request.url.path:return httpx2.Response(200,json={'id':'configured-model'})
        if request.method=='POST':posts.append(1)
        return result('resp_pending','queued')
    with pytest.raises(AIServiceError) as error:
        tracked.gateway(handler).structured('vision','test',{},FurnitureVisionResult,timeout_seconds=1)
    assert error.value.code=='AI_BACKGROUND_PENDING' and error.value.retryable and len(posts)==1
    with tracked.db.sessions() as session:
        row=session.scalar(select(AIInvocation))
        assert row.status=='PENDING' and row.response_id=='resp_pending'


@pytest.mark.parametrize('terminal,code', [('failed','AI_RESPONSE_FAILED'),
    ('cancelled','AI_RESPONSE_CANCELLED'), ('incomplete','AI_RESPONSE_INCOMPLETE')])
def test_terminal_failure_never_runs_cad_tool_or_falls_back(tracked,terminal,code):
    posts=[]
    def handler(request):
        if '/models/' in request.url.path:return httpx2.Response(200,json={'id':'configured-model'})
        if request.method=='POST':posts.append(1);return result('resp_failed','queued')
        body=response_body(status=terminal)
        body.update(id='resp_failed',error={'code':'server_error','message':'PRIVATE_PROVIDER_TEXT'})
        return httpx2.Response(200,json=body)
    with pytest.raises(AIServiceError) as error:
        tracked.gateway(handler,vision='configured-model',fallback='other-model').structured('vision','test',{},
            QualityReviewResult,tools={'cad_generation':dict(schema=JoineryLookup,description='Offline test',
                handler=lambda args:pytest.fail('Failed response must not execute CAD'))},require_tool='cad_generation')
    assert error.value.code==code and len(posts)==1
    assert 'PRIVATE_PROVIDER_TEXT' not in str(error.value)


def test_expired_response_is_not_mistaken_for_unavailable_model_or_resubmitted(tracked):
    posts=[]
    def handler(request):
        if '/models/' in request.url.path:return httpx2.Response(200,json={'id':'configured-model'})
        if request.method=='POST':posts.append(1);return result('resp_expired','queued')
        return httpx2.Response(404,json={'error':{'code':'response_not_found'}})
    for current in (tracked.runner,tracked.restart()):
        # Install the lease used by this attempt.
        with tracked.db.sessions.begin() as session:
            session.get(AgentWorkflow,tracked.runner.id).lease_token=current.lease
        with pytest.raises(AIServiceError) as error:
            tracked.gateway(handler,current,vision='configured-model',fallback='other-model').structured(
                'vision','test',{},FurnitureVisionResult)
        assert error.value.code=='AI_BACKGROUND_EXPIRED' and not error.value.retryable
    assert len(posts)==1


def test_unknown_submission_outcome_is_not_resubmitted_on_workflow_retry(tracked):
    posts=[]
    def handler(request):
        if '/models/' in request.url.path:return httpx2.Response(200,json={'id':'configured-model'})
        posts.append(1);raise httpx2.ReadTimeout('lost acceptance',request=request)
    with pytest.raises(AIServiceError) as first:
        tracked.gateway(handler).structured('vision','test',{},FurnitureVisionResult)
    assert first.value.code=='AI_TIMEOUT'
    with pytest.raises(AIServiceError) as resumed:
        tracked.gateway(handler,tracked.restart()).structured('vision','test',{},FurnitureVisionResult)
    assert resumed.value.code=='AI_RESPONSE_UNTRACKED' and len(posts)==1


def test_restart_replays_prior_tool_round_and_retrieves_pending_followup(tracked):
    posts=[];polls=[];tools=[]
    call=dict(type='function_call',id='fc_saved',call_id='call_saved',name='lookup',
        arguments='{"materials":["solid_wood"]}',status='completed',**{'async':False,'caller':{'type':'direct'}})
    outputs=[dict(type='reasoning',id='rs_saved',summary=[],encrypted_content='private_opaque_reasoning'),call]
    def handler(request):
        if '/models/' in request.url.path:return httpx2.Response(200,json={'id':'configured-model'})
        if request.method=='POST':
            body=json.loads(request.content);posts.append(body)
            if len(posts)==1:return result('resp_round1',output=outputs)
            assert body['input'][1:3]==outputs
            return result('resp_round2','queued')
        polls.append(1)
        if len(polls)<=3:raise httpx2.ReadTimeout('offline',request=request)
        return result('resp_round2',payload=fixtures()['QualityReviewResult'])
    def execute(current):
        return tracked.gateway(handler,current).structured('vision','test',{},QualityReviewResult,
            tools={'lookup':dict(schema=JoineryLookup,description='Read-only catalog fixture',
                handler=lambda args:tools.append(args.materials) or [])},require_tool='lookup')
    with pytest.raises(AIServiceError) as error:execute(tracked.runner)
    assert error.value.code=='AI_BACKGROUND_POLL_INTERRUPTED'
    assert execute(tracked.restart()).engineering_certified is False
    assert len(posts)==2 and len(polls)==4
    with tracked.db.sessions() as session:
        rows=list(session.scalars(select(AIInvocation)))
        assert len(rows)==2 and sum(row.usage['input_tokens'] for row in rows)==200
        events=[row.details for row in session.scalars(select(AgentEvent))]
        assert 'private_opaque_reasoning' not in json.dumps(events)


def test_corrupted_private_response_checkpoint_stops_replay(tracked):
    calls=[]
    def handler(request):
        calls.append(1)
        if '/models/' in request.url.path:return httpx2.Response(200,json={'id':'configured-model'})
        return result('resp_integrity')
    tracked.gateway(handler).structured('vision','test',{},FurnitureVisionResult)
    with tracked.db.sessions.begin() as session:
        workflow=session.get(AgentWorkflow,tracked.runner.id)
        from copy import deepcopy
        checkpoints=deepcopy(workflow.checkpoints)
        next(iter(checkpoints.values()))['payload']['id']='resp_wrong'
        workflow.checkpoints=checkpoints
    count=len(calls)
    with pytest.raises(AIServiceError) as error:
        tracked.gateway(handler).structured('vision','test',{},FurnitureVisionResult)
    assert error.value.code=='AI_RESPONSE_CACHE_INVALID' and len(calls)==count


def test_foreground_switch_and_poll_interval_bounds(tracked):
    tracked.config.openai_background_mode=False
    def handler(request):
        if '/models/' in request.url.path:return httpx2.Response(200,json={'id':'configured-model'})
        assert request.method=='POST' and 'background' not in json.loads(request.content)
        return result('resp_foreground')
    assert tracked.gateway(handler).structured('vision','test',{},FurnitureVisionResult).category=='display cabinet'
    for interval in (0,11,float('nan'),float('inf')):
        with pytest.raises(ValueError,match='poll interval'):Settings(openai_poll_interval_seconds=interval)


def test_model_metadata_timeout_can_retry_before_any_generation_was_submitted(tracked):
    gets=[];posts=[]
    def handler(request):
        if '/models/' in request.url.path:
            gets.append(1)
            if len(gets)==1:raise httpx2.ReadTimeout('metadata unavailable',request=request)
            return httpx2.Response(200,json={'id':'configured-model'})
        posts.append(1);return result('resp_metadata_retry')
    with pytest.raises(AIServiceError) as error:
        tracked.gateway(handler).structured('vision','test',{},FurnitureVisionResult)
    assert error.value.code=='AI_TIMEOUT' and not posts
    assert tracked.gateway(handler,tracked.restart()).structured('vision','test',{},FurnitureVisionResult)
    assert len(posts)==1


def test_lost_workflow_lease_stops_polling(tracked):
    gets=[]
    def handler(request):
        if '/models/' in request.url.path:return httpx2.Response(200,json={'id':'configured-model'})
        if request.method=='POST':return result('resp_lease','queued')
        gets.append(1)
        tracked.restart()
        return result('resp_lease','in_progress')
    with pytest.raises(AIServiceError) as error:
        tracked.gateway(handler).structured('vision','test',{},FurnitureVisionResult)
    assert error.value.code=='WORKFLOW_LEASE_LOST' and len(gets)==1


def test_resume_prefers_accepted_fallback_even_if_primary_recovers_and_mode_changes(tracked):
    posts=[];gets=[]
    def handler(request):
        if '/models/' in request.url.path:return httpx2.Response(200,json={'id':'configured-model'})
        if request.method=='POST':
            body=json.loads(request.content);posts.append(body['model'])
            if len(posts)==1:
                return httpx2.Response(429,json={'error':{'code':'rate_limit_exceeded'}})
            assert body['model']=='fallback-model'
            return result('resp_fallback','queued')
        gets.append(1)
        if len(gets)<=3:raise httpx2.ReadTimeout('offline',request=request)
        return result('resp_fallback')
    with pytest.raises(AIServiceError) as error:
        tracked.gateway(handler,vision='configured-model',fallback='fallback-model').structured(
            'vision','test',{},FurnitureVisionResult)
    assert error.value.code=='AI_BACKGROUND_POLL_INTERRUPTED'
    tracked.config.openai_background_mode=False
    assert tracked.gateway(handler,tracked.restart(),vision='configured-model',fallback='fallback-model').structured(
        'vision','test',{},FurnitureVisionResult).category=='display cabinet'
    assert posts==['configured-model','fallback-model'] and len(gets)==4


def test_photo_analysis_api_resume_retrieves_saved_response_and_hides_private_checkpoints(agents_app,monkeypatch):
    from app.agents import orchestrator
    client,app,headers,project,image=agents_app
    app.state.settings.ai_models['vision']='gpt-6.1-sol'
    monkeypatch.setattr(openai_client,'time',Clock())
    configuration=client.get('/api/v1/ai/configuration',headers=headers['designer']).json()
    provider_status=client.get('/api/system/openai/status',headers=headers['designer']).json()
    assert configuration['background_mode'] is True and provider_status['background_mode'] is True
    posts=[];gets=[]
    analysis=dict(vision=fixtures()['FurnitureVisionResult'],summary='Offline analysis fixture',
        matching_inputs=[dict(key=key,label=key,unit='mm',estimated_value=value,explanation='Offline fixture estimate')
            for key,value in [('width_mm',600),('depth_mm',400),('height_mm',450)]])
    def handler(request):
        if '/models/' in request.url.path:return httpx2.Response(200,json={'id':'configured-model'})
        if request.url.path.endswith('/responses/input_tokens'):
            return httpx2.Response(200,json={'input_tokens':100})
        if request.method=='POST':
            body=json.loads(request.content);posts.append(body)
            assert body['background'] is True and body['input'][0]['content'][1]['type']=='input_image'
            return result('resp_photo_api','queued')
        gets.append(1)
        if len(gets)<=3:raise httpx2.ReadTimeout('status connection lost',request=request)
        response=result('resp_photo_api',payload=analysis)
        body=response.json();body['usage']['input_tokens_details']['cache_write_tokens']=100
        return httpx2.Response(200,json=body)
    def factory(config,models,record):
        sdk=OpenAI(api_key='OFFLINE_TEST_KEY',base_url='http://openai.test/v1',max_retries=0,
            http_client=DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
        return ResponsesGateway(config,models,record,sdk=sdk)
    monkeypatch.setattr(orchestrator,'ResponsesGateway',factory)
    accepted=client.post('/api/v1/ai/analyze-furniture-photo',headers=headers['operator'],json=dict(
        project_id=project,image_ids=[image],idempotency_key='background-photo-api'))
    assert accepted.status_code==202,accepted.text
    workflow_id=accepted.json()['workflow_id']
    url=f'/api/v1/agents/workflows/{workflow_id}'
    failed=client.get(url,headers=headers['designer']).json()
    assert failed['status']=='FAILED' and failed['error']['code']=='AI_BACKGROUND_POLL_INTERRUPTED'
    assert failed['usage']['input_tokens']==0 and failed['completed_stages']==[]
    assert client.post(url+'/retry',headers=headers['operator']).status_code==202
    completed=client.get(url,headers=headers['designer']).json()
    assert completed['status']=='SUCCEEDED' and completed['attempts']==2
    assert completed['completed_stages']==['photo_analysis','vision']
    assert completed['usage']['input_tokens']==100 and completed['usage_complete'] is True
    assert completed['result']['matching_inputs'][0]['estimated_value']==600
    assert len(posts)==1 and len(gets)==4
    assert not completed['ai_budget']['uncertain'] and completed['ai_budget']['calls']==1
    with app.state.db.sessions() as session:
        rows=list(session.scalars(select(AIInvocation).where(AIInvocation.workflow_id==workflow_id)))
        assert len(rows)==1 and rows[0].response_id=='resp_photo_api'
