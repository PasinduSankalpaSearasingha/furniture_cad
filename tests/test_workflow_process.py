"""Real local worker boundary and safe recovery; no paid AI requests."""
import json
import os
import subprocess
import time
from uuid import uuid4
from sqlalchemy import select,func
from app.config import Settings
from app.database import AgentWorkflow,AgentEvent,AIInvocation,User
from app.pipeline import canonical_hash
from app.workflow_process import settings_payload,run_isolated_workflow
from tests.test_agent_workflows import agents_app
from tests.test_engineering_cad import engineering_app,EngineeringGateway,launch


def queued(env):
    _,app,_,project,image=env
    raw=dict(cad_source='ENGINEERING_CAD',image_ids=[image])
    with app.state.db.sessions.begin() as session:
        row=AgentWorkflow(project_id=project,created_by=session.scalar(select(User.id).where(User.role=='operator')),
            mode='IMAGE_TO_CAD',state='QUEUED',input_data=raw,input_hash=canonical_hash(raw),model_config={})
        session.add(row);session.flush();return row.id


def test_settings_snapshot_excludes_live_executors_and_credentials(tmp_path,monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','SYNTHETIC_SECRET_NOT_TO_SERIALIZE')
    config=Settings(storage=tmp_path);config._local_executor=object();config._render_executor=object()
    payload=settings_payload(config);encoded=json.dumps(payload)
    assert '_local_executor' not in payload and '_render_executor' not in payload
    assert 'OPENAI_API_KEY' not in encoded and 'SYNTHETIC_SECRET_NOT_TO_SERIALIZE' not in encoded
    restored=Settings(**payload)
    assert restored.storage==config.storage and restored._local_executor is None


def test_worker_exit_keeps_available_evidence_and_hides_windows_console(agents_app,monkeypatch):
    workflow=queued(agents_app);_,app,_,_,_=agents_app;sent=[]
    def stopped(command,**options):
        sent.append((command,options));payload=json.loads(options['input'])
        assert payload['database_url']==app.state.settings.database_url
        assert 'OPENAI_API_KEY' not in payload
        assert command[1:3]==['-m','app.workflow_worker']
        if os.name=='nt':assert options['creationflags']&subprocess.CREATE_NO_WINDOW
        return subprocess.CompletedProcess(command,1)
    monkeypatch.setattr('app.workflow_process.subprocess.run',stopped)
    run_isolated_workflow(workflow,app.state.settings)
    with app.state.db.sessions() as session:
        row=session.get(AgentWorkflow,workflow)
        assert row.state=='REVIEW_REQUIRED' and row.error is None
        assert row.result['available_output']['references']
        assert not row.result['available_output']['has_cad']
    assert len(sent)==1


def test_worker_exit_does_not_overwrite_a_different_lease(agents_app,monkeypatch):
    workflow=queued(agents_app);_,app,_,_,_=agents_app;other=str(uuid4())
    def changed(command,**options):
        with app.state.db.sessions.begin() as session:
            row=session.get(AgentWorkflow,workflow);row.state='RUNNING';row.lease_token=other
        return subprocess.CompletedProcess(command,1)
    monkeypatch.setattr('app.workflow_process.subprocess.run',changed)
    run_isolated_workflow(workflow,app.state.settings)
    with app.state.db.sessions() as session:
        row=session.get(AgentWorkflow,workflow)
        assert row.state=='RUNNING' and row.lease_token==other and row.result is None


def test_worker_log_creation_failure_does_not_leave_a_queued_job_forever(agents_app):
    workflow=queued(agents_app);_,app,_,_,_=agents_app
    (app.state.settings.storage/'runtime').write_text('Synthetic blocked log directory',encoding='utf-8')
    run_isolated_workflow(workflow,app.state.settings)
    with app.state.db.sessions() as session:
        row=session.get(AgentWorkflow,workflow)
        assert row.state=='REVIEW_REQUIRED' and row.error is None
        assert row.result['available_output']['references']


def test_real_child_exports_saved_cad_while_health_requests_remain_responsive(engineering_app,monkeypatch):
    source=launch(engineering_app,key='process-source',output_scope='WHOLE_FURNITURE')
    assert source['status']=='REVIEW_REQUIRED'
    client,app,headers,_,_=engineering_app;calls=list(EngineeringGateway.calls)
    app.state.settings.executor='local';app.state.settings.local_workflow_process=True
    app.state.settings.ai_enabled=False
    monkeypatch.setenv('OPENAI_BASE_URL','http://127.0.0.1:1')
    started=time.monotonic()
    pending=launch(engineering_app,key='process-reexport',output_scope='WHOLE_FURNITURE',engineering_reference_workflow_id=source['id'])
    assert time.monotonic()-started<3
    health_checks=0;state=pending
    while time.monotonic()-started<60:
        check=time.monotonic();assert client.get('/health/live').status_code==200
        assert time.monotonic()-check<2;health_checks+=1
        state=client.get(f'/api/v1/agents/workflows/{pending["id"]}',headers=headers['operator']).json()
        if state['status'] not in ('QUEUED','RUNNING'):break
        time.sleep(.25)
    assert state['status']=='REVIEW_REQUIRED' and state['result'].get('delivery'),state.get('error') or state.get('result')
    assert health_checks>1 and EngineeringGateway.calls==calls
    with app.state.db.sessions() as session:
        event=session.scalar(select(AgentEvent).where(AgentEvent.workflow_id==pending['id'],AgentEvent.event=='isolated_worker_started'))
        assert event and event.details['pid']!=os.getpid()
        assert session.scalar(select(func.count()).select_from(AIInvocation).where(AIInvocation.workflow_id==pending['id']))==0
