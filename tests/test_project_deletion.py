"""Deletion authorization, dependency cleanup, active jobs and artifact rollback."""
from pathlib import Path
from uuid import uuid4
import pytest
from sqlalchemy import select,func
from app.database import (Project,Revision,Job,AgentWorkflow,AgentEvent,AIInvocation,
    ImageAsset,Approval,Release,AuditLog,User)
from app.pipeline import canonical_hash
from tests.test_agent_workflows import agents_app


def populate(env):
    client,app,headers,project,image=env;storage=app.state.settings.storage
    with app.state.db.sessions.begin() as session:
        owner=session.get(Project,project).owner_id
        revision=Revision(project_id=project,number=1,specification={},spec_hash=canonical_hash({}),created_by=owner)
        session.add(revision);session.flush()
        job=Job(revision_id=revision.id,state='SUCCEEDED');session.add(job);session.flush()
        workflow=AgentWorkflow(project_id=project,created_by=owner,mode='IMAGE_TO_CAD',state='REVIEW_REQUIRED',
            input_data={},input_hash=canonical_hash({}),model_config={},revision_id=revision.id,job_id=job.id)
        session.add(workflow);session.flush()
        session.add(AgentEvent(workflow_id=workflow.id,event='test',stage='complete'))
        session.add(AIInvocation(workflow_id=workflow.id,stage='test',model='MOCK',request_hash='test',status='COMPLETED'))
        approval=Approval(revision_id=revision.id,job_id=job.id,designer_id=owner,decision='APPROVED',
            comment='Synthetic dependency fixture',spec_hash='test',manifest_hash='test')
        session.add(approval);session.flush()
        session.add(Release(revision_id=revision.id,approval_id=approval.id,package_hash='test'))
        revision.active_job_id=job.id;revision.source_workflow_id=workflow.id
        ids=dict(project=project,revision=revision.id,job=job.id,workflow=workflow.id,image=image)
    paths=[storage/ids['job'],storage/(ids['job']+'_engineering_'+str(uuid4())),
        storage/('delivery_'+ids['workflow']+'_'+str(uuid4())),
        storage/('engineering_preview_'+ids['workflow']+'_0_'+str(uuid4())),
        storage/('dimension_sheet_'+ids['job']+'_v3'),storage/('photo_fidelity_'+ids['workflow']),
        storage/('reference_fit_'+ids['workflow']),storage/('concept_'+ids['workflow']+'_0_'+str(uuid4()))]
    for path in paths:path.mkdir();(path/'fixture.txt').write_text('owned fixture')
    paths.append(storage/'images'/(image+'.png'))
    return ids,paths


@pytest.mark.parametrize('prefix',['/api/projects','/api/v1/projects'])
def test_owner_deletes_project_versions_files_and_private_access(agents_app,prefix):
    env=agents_app;client,app,headers,project,image=env;ids,paths=populate(env)
    other=client.post('/api/projects',headers=headers['operator'],json=dict(name='Keep this design')).json()['id']
    shared=app.state.settings.storage/'unrelated';shared.mkdir();(shared/'keep.txt').write_text('keep')
    # Stored model JSON cannot nominate another project's artifacts for deletion.
    with app.state.db.sessions.begin() as session:
        workflow=session.get(AgentWorkflow,ids['workflow']);workflow.result={'delivery':{'directory':'unrelated'}}
    response=client.delete(prefix+'/'+project,headers=headers['operator'])
    assert response.status_code==200,response.text
    assert response.json()==dict(id=project,status='DELETED',cleanup_pending=False)
    assert all(not path.exists() for path in paths)
    assert (shared/'keep.txt').read_text()=='keep' and not list(app.state.settings.storage.glob('.deleting-project-*'))
    assert [p['id'] for p in client.get('/api/projects',headers=headers['operator']).json()]==[other]
    for path in (f'/api/projects/{project}',f'/api/v1/images/{image}',f'/api/v1/agents/workflows/{ids["workflow"]}',
        f'/api/v1/jobs/{ids["job"]}/files/fixture.txt',f'/api/v1/revisions/{ids["revision"]}/parts'):
        assert client.get(path,headers=headers['operator']).status_code==404,path
    with app.state.db.sessions() as session:
        for model in (Revision,Job,AgentWorkflow,AgentEvent,AIInvocation,ImageAsset,Approval,Release):
            assert session.scalar(select(func.count()).select_from(model))==0,model.__name__
        assert session.scalar(select(AuditLog).where(AuditLog.event=='project_deleted')).resource_id==project


def test_reviewer_and_unrelated_user_cannot_delete_but_admin_can(agents_app):
    client,app,headers,project,image=agents_app
    assert client.delete('/api/projects/'+project).status_code==401
    assert client.delete('/api/projects/'+project,headers=headers['designer']).status_code==403
    other=client.post('/api/projects',headers=headers['designer'],json=dict(name='Other owner')).json()['id']
    assert client.delete('/api/projects/'+other,headers=headers['operator']).status_code==404
    assert client.get('/api/projects/'+project,headers=headers['operator']).status_code==200
    assert client.delete('/api/projects/'+project,headers=headers['admin']).status_code==200
    assert client.get('/api/projects/'+other,headers=headers['designer']).status_code==200


@pytest.mark.parametrize('workflow_state,job_state',[
    ('QUEUED','SUCCEEDED'),('RUNNING','SUCCEEDED'),('CAD_QUEUED','SUCCEEDED'),
    ('REVIEW_REQUIRED','QUEUED'),('REVIEW_REQUIRED','RUNNING')])
def test_in_flight_generation_prevents_deletion(agents_app,workflow_state,job_state):
    client,app,headers,project,image=agents_app;ids,paths=populate(agents_app)
    with app.state.db.sessions.begin() as session:
        session.get(AgentWorkflow,ids['workflow']).state=workflow_state
        session.get(Job,ids['job']).state=job_state
    response=client.delete('/api/projects/'+project,headers=headers['operator'])
    assert response.status_code==409 and 'finish' in response.text
    assert all(path.exists() for path in paths)
    assert client.get('/api/projects/'+project,headers=headers['operator']).status_code==200


def test_transaction_failure_restores_database_and_all_files(agents_app,monkeypatch):
    client,app,headers,project,image=agents_app;ids,paths=populate(agents_app)
    def failure(*a,**k):raise RuntimeError('Synthetic database failure')
    monkeypatch.setattr('app.project_deletion.audit',failure)
    with pytest.raises(RuntimeError,match='Synthetic database failure'):
        client.delete('/api/projects/'+project,headers=headers['operator'])
    assert all(path.exists() for path in paths) and not list(app.state.settings.storage.glob('.deleting-project-*'))
    with app.state.db.sessions() as session:
        assert session.get(Project,project) and session.get(Revision,ids['revision']) and session.get(Job,ids['job'])
        assert session.get(AgentWorkflow,ids['workflow']) and session.get(ImageAsset,image)


def test_outside_storage_path_cannot_be_moved_or_deleted(agents_app,monkeypatch,tmp_path):
    client,app,headers,project,image=agents_app;outside=tmp_path/'outside.txt';outside.write_text('keep')
    monkeypatch.setattr('app.project_deletion.project_artifacts',lambda *a:[outside])
    response=client.delete('/api/projects/'+project,headers=headers['operator'])
    assert response.status_code==409 and outside.read_text()=='keep'
    assert client.get('/api/projects/'+project,headers=headers['operator']).status_code==200


def test_partial_move_failure_restores_files(agents_app,monkeypatch):
    client,app,headers,project,image=agents_app;ids,paths=populate(agents_app);rename=Path.rename;calls=[]
    def move(source,target):
        if '.deleting-project-' in str(target):
            calls.append(str(source))
            if len(calls)==2:raise PermissionError('Synthetic file lock')
        return rename(source,target)
    monkeypatch.setattr(Path,'rename',move)
    response=client.delete('/api/projects/'+project,headers=headers['operator'])
    assert response.status_code==409 and all(path.exists() for path in paths)
    assert client.get('/api/projects/'+project,headers=headers['operator']).status_code==200


def test_redirected_artifact_directory_cannot_remove_another_design(agents_app,monkeypatch):
    from app.project_deletion import safe_directory
    client,app,headers,project,image=agents_app;ids,paths=populate(agents_app)
    other=app.state.settings.storage/'another-design';other.mkdir();(other/'keep.txt').write_text('keep')
    def redirected(root,name):return other if name==ids['job'] else safe_directory(root,name)
    monkeypatch.setattr('app.project_deletion.safe_directory',redirected)
    response=client.delete('/api/projects/'+project,headers=headers['operator'])
    assert response.status_code==409 and (other/'keep.txt').read_text()=='keep'
    assert all(path.exists() for path in paths)
