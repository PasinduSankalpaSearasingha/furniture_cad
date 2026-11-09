"""Local background work returns promptly and needs no broker."""
from threading import Event
import time
import pytest
from app.config import Settings
from app.local_executor import LocalExecutor, LocalQueueFull
from app.agents import orchestrator
from app.api import agents as agent_routes
from .test_agent_workflows import agents_app, begin, verify_and_generate


def wait_status(client,headers,path,expected):
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        result=client.get(path,headers=headers)
        assert result.status_code==200,result.text
        if result.json()['status']==expected: return result.json()
        time.sleep(.025)
    raise AssertionError(f'{path} did not reach {expected}: {result.text}')


def test_local_workspace_selects_background_runner(monkeypatch):
    monkeypatch.delenv('JOB_EXECUTOR',raising=False)
    assert Settings(local_workspace=True).executor=='local'
    assert Settings(local_workspace=False).executor=='celery'
    assert Settings(local_workspace=True,executor='celery').executor=='celery'


def test_submission_returns_while_ai_runs_in_background(agents_app,monkeypatch):
    client,app,h,p,image=agents_app
    app.state.settings.executor='local'
    started,release=Event(),Event()
    original=orchestrator.run_workflow
    def delayed(*args):
        started.set()
        if not release.wait(5): raise RuntimeError('Test worker was not released')
        return original(*args)
    monkeypatch.setattr(orchestrator,'run_workflow',delayed)
    try:
        wid=begin(agents_app)
        assert started.wait(1)
        assert client.get('/health/live').status_code==200
        assert client.get(f'/api/v1/agents/workflows/{wid}',headers=h['designer']).json()['status']=='QUEUED'
    finally: release.set()
    result=wait_status(client,h['designer'],f'/api/v1/agents/workflows/{wid}','WAITING_FOR_DESIGNER')
    assert result['completed_stages']==['vision','design_0','joinery_0','review_0']


def test_local_cad_finishes_and_enqueues_post_cad_review(agents_app):
    client,app,h,p,image=agents_app
    app.state.settings.executor='local'
    wid=begin(agents_app)
    wait_status(client,h['designer'],f'/api/v1/agents/workflows/{wid}','WAITING_FOR_DESIGNER')
    verified,generated=verify_and_generate(agents_app,wid)
    wait_status(client,h['designer'],f'/api/v1/jobs/{generated["job_id"]}','SUCCEEDED')
    result=wait_status(client,h['designer'],f'/api/v1/agents/workflows/{wid}','REVIEW_REQUIRED')
    assert result['result']['manufacturing_package']['part_count']==10
    assert result['approved_for_manufacturing'] is False
    exported=client.get(f'/api/v1/agents/workflows/{wid}/result',headers=h['designer'])
    assert exported.status_code==200 and exported.json()['manufacturing_package']['status']=='REVIEW'


def test_previous_broker_failure_can_resume_locally(agents_app,monkeypatch):
    client,app,h,p,image=agents_app
    original=agent_routes.dispatch_workflow
    def unavailable(*args): raise ConnectionError('Synthetic Redis outage')
    monkeypatch.setattr(agent_routes,'dispatch_workflow',unavailable)
    response=client.post('/api/v1/agents/run-workflow',headers=h['operator'],json={'project_id':p,'image_ids':[image]})
    assert response.status_code==503
    wid=response.json()['error']['message']['workflow_id']
    monkeypatch.setattr(agent_routes,'dispatch_workflow',original)
    app.state.settings.executor='local'
    assert client.post(f'/api/v1/agents/workflows/{wid}/retry',headers=h['operator']).status_code==202
    wait_status(client,h['designer'],f'/api/v1/agents/workflows/{wid}','WAITING_FOR_DESIGNER')


def test_background_queue_is_bounded():
    executor=LocalExecutor(capacity=1)
    release=Event()
    try:
        future=executor.submit(release.wait,5)
        with pytest.raises(LocalQueueFull): executor.submit(lambda:None)
        release.set()
        assert future.result(timeout=2) is True
    finally:
        release.set()
        executor.shutdown()
