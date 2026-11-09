import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import create_app
from app.config import Settings
from app.database import User, Job, Revision, Approval, AuditLog, Token
from app.auth import hash_password, issue_token
from .test_cabinet import spec


@pytest.fixture(scope='module')
def workspace(tmp_path_factory):
    root = tmp_path_factory.mktemp('api')
    app = create_app(Settings(database_url=f'sqlite:///{root / "test.db"}',storage=root/'outputs',executor='eager'))
    app.state.db.create_schema_for_tests()
    headers,ids = {},{}
    with app.state.db.sessions.begin() as session:
        for name,role in [('admin','admin'),('designer','designer'),('maker','operator'),('outsider','operator'),('viewer','viewer')]:
            user = User(username=name,password_hash=hash_password('test-password-123'),role=role)
            session.add(user)
            session.flush()
            ids[name] = user.id
            headers[name] = {'Authorization':'Bearer '+issue_token(session,user,8)}
    with TestClient(app) as client:
        p = client.post('/api/v1/projects',json={'name':'Workflow test','reviewer_ids':[ids['designer']]},headers=headers['maker'])
        assert p.status_code==201,p.text
        project_id = p.json()['id']
        payload = spec(include_glazed_door=True,joint_type='dowel_demo').model_dump(mode='json')
        r = client.post(f'/api/v1/projects/{project_id}/revisions',json=payload,headers=headers['maker'])
        assert r.status_code==201,r.text
        revision_id = r.json()['id']
        j = client.post(f'/api/v1/revisions/{revision_id}/jobs',headers=headers['maker'])
        assert j.status_code==202,j.text
        job_id = j.json()['job_id']
        yield client,app,headers,ids,project_id,revision_id,job_id
    app.state.db.engine.dispose()


def test_complete_review_workflow_and_release_block(workspace):
    client,app,h,ids,pid,rid,jid = workspace
    status = client.get(f'/api/v1/jobs/{jid}',headers=h['maker'])
    assert status.json()['status']=='SUCCEEDED',status.text
    assert status.json()['progress']==100
    assert status.json()['design_state']=='VALIDATION_FAILED'
    download = client.get(f'/api/v1/jobs/{jid}/download',headers=h['designer'])
    assert download.status_code==200,download.text
    assert download.content[:2]==b'PK'
    for resource in ('parts','cutting-list','bom','drawings','validation'):
        response = client.get(f'/api/v1/revisions/{rid}/{resource}',headers=h['designer'])
        assert response.status_code==200,response.text
    assert len(client.get(f'/api/v1/revisions/{rid}/parts',headers=h['maker']).json())==10
    assert client.post(f'/api/v1/revisions/{rid}/approvals',headers=h['designer'],
        json={'decision':'APPROVED','comment':'Must be blocked'}).status_code==409
    assert client.post(f'/api/v1/revisions/{rid}/release',headers=h['designer']).status_code==409
    with app.state.db.sessions() as session:
        assert session.scalar(select(Approval).where(Approval.revision_id==rid)) is None
        assert session.scalar(select(AuditLog).where(AuditLog.event=='job_completed')) is not None


def test_authorization_and_private_artifacts(workspace):
    client,app,h,ids,pid,rid,jid = workspace
    for path in (f'/api/v1/jobs/{jid}',f'/api/v1/jobs/{jid}/download',f'/api/v1/revisions/{rid}/parts'):
        assert client.get(path).status_code==401
        assert client.get(path,headers=h['outsider']).status_code==404
    assert client.post(f'/api/v1/revisions/{rid}/approvals',headers=h['maker'],
        json={'decision':'APPROVED','comment':'Not a designer'}).status_code==403
    assert client.post(f'/api/v1/revisions/{rid}/approvals',headers=h['admin'],
        json={'decision':'APPROVED','comment':'Administration is not engineering authority'}).status_code==403
    assert client.post('/api/v1/projects',headers=h['viewer'],json={'name':'Forbidden'}).status_code==403
    assert client.get('/api/v1/audit',headers=h['maker']).status_code==403
    assert client.get(f'/api/v1/jobs/{jid}/files/private.txt',headers=h['maker']).status_code==404


def test_native_cad_downloads_work_with_browser_session(workspace):
    client,app,h,ids,pid,rid,jid=workspace
    assert client.post('/api/v1/workspace/session',json={'username':'maker','password':'test-password-123'}).status_code==200
    try:
        package=client.get(f'/api/v1/jobs/{jid}/download')
        step=client.get(f'/api/v1/jobs/{jid}/files/cabinet.step')
        assert package.status_code==200 and package.content[:2]==b'PK'
        assert step.status_code==200 and b'ISO-10303-21' in step.content
    finally:
        assert client.delete('/api/v1/workspace/session').status_code==204
    assert client.get(f'/api/v1/jobs/{jid}/files/cabinet.step').status_code==401


def test_revisions_are_immutable_and_changes_require_new_revision(workspace):
    client,app,h,ids,pid,rid,jid = workspace
    original = client.get(f'/api/v1/revisions/{rid}',headers=h['maker']).json()
    assert client.patch(f'/api/v1/revisions/{rid}',headers=h['maker'],json={'width_mm':900}).status_code==405
    new = client.post(f'/api/v1/projects/{pid}/revisions',headers=h['maker'],json=spec(width_mm=900).model_dump(mode='json'))
    assert new.status_code==201,new.text
    assert new.json()['revision']==2
    assert new.json()['spec_hash']!=original['spec_hash']
    assert new.json()['state']=='SPECIFICATION_PENDING'
    assert client.get(f'/api/v1/revisions/{rid}',headers=h['maker']).json()==original
    assert client.post(f'/api/v1/revisions/{new.json()["id"]}/verify',headers=h['designer']).status_code==409


def test_hashed_artifact_tampering_blocks_download_and_review(workspace):
    client,app,h,ids,pid,rid,jid = workspace
    path = app.state.settings.storage/jid/'cut_list_REVIEW.csv'
    content = path.read_bytes()
    try:
        path.write_bytes(content+b'changed')
        assert client.get(f'/api/v1/jobs/{jid}/download',headers=h['maker']).status_code==409
        assert client.post(f'/api/v1/revisions/{rid}/approvals',headers=h['designer'],
            json={'decision':'CHANGES_REQUESTED','comment':'Review cannot use modified files'}).status_code==409
    finally: path.write_bytes(content)


def test_authentication_errors_expiry_and_revocation(workspace):
    client,app,h,ids,pid,rid,jid = workspace
    assert client.post('/api/v1/auth/token',json={'username':'maker','password':'wrong'}).status_code==401
    login = client.post('/api/v1/auth/token',json={'username':'maker','password':'test-password-123'})
    assert login.status_code==200
    auth = {'Authorization':'Bearer '+login.json()['access_token']}
    assert client.get('/api/v1/auth/me',headers=auth).status_code==200
    assert client.delete('/api/v1/auth/token',headers=auth).status_code==204
    assert client.get('/api/v1/auth/me',headers=auth).status_code==401
    with app.state.db.sessions.begin() as session:
        token = issue_token(session,session.get(User,ids['maker']),-1)
    assert client.get('/api/v1/auth/me',headers={'Authorization':'Bearer '+token}).status_code==401


def test_api_input_errors_and_limits(workspace):
    client,app,h,ids,pid,rid,jid = workspace
    response = client.post('/api/v1/cabinet/generate',headers=h['maker'],json={'width_mm':-10})
    assert response.status_code==422 and response.json()['error']['code']=='INVALID_INPUT'
    assert client.post('/api/v1/cabinet/generate',headers=h['maker'],content=b'x'*(2*1024*1024+1)).status_code==413
    assert client.get('/api/v1/jobs/not-a-uuid/download',headers=h['maker']).status_code==422
    assert client.get(f'/api/v1/jobs/{uuid4()}/download',headers=h['maker']).status_code==404


def test_catalog_snapshot_cannot_be_upgraded_by_client(workspace):
    client,app,h,ids,pid,rid,jid = workspace
    profile = {'material_id':'TEST-UNAPPROVED','name':'Test catalog entry','material':'plywood','actual_thickness_mm':18}
    assert client.post('/api/v1/catalogs/materials',headers=h['maker'],json=profile).status_code==403
    assert client.post('/api/v1/catalogs/materials',headers=h['designer'],json=profile).status_code==201
    forged = {**profile,'approved':True,'evidence_reference':'FORGED ASSERTION'}
    payload = spec(material_profiles=[forged],material_assignments={'carcass':profile['material_id']}).model_dump(mode='json')
    r = client.post(f'/api/v1/projects/{pid}/revisions',headers=h['maker'],json=payload)
    assert r.status_code==201,r.text
    assert r.json()['specification']['material_profiles'][0]['approved'] is False
    assert client.post('/api/v1/catalogs/materials',headers=h['designer'],json=profile).status_code==409


def test_failed_job_retry_creates_new_job(workspace,monkeypatch):
    client,app,h,ids,pid,rid,jid = workspace
    import app.jobs as jobs
    original_builder = jobs.build_package
    def fail(*args,**kwargs): raise RuntimeError('synthetic export failure')
    monkeypatch.setattr(jobs,'build_package',fail)
    r = client.post(f'/api/v1/projects/{pid}/revisions',headers=h['maker'],json=spec().model_dump(mode='json')).json()
    failed = client.post(f'/api/v1/revisions/{r["id"]}/jobs',headers=h['maker']).json()['job_id']
    assert client.get(f'/api/v1/jobs/{failed}',headers=h['maker']).json()['status']=='FAILED'
    assert client.get(f'/api/v1/jobs/{failed}/download',headers=h['maker']).status_code==409
    monkeypatch.setattr(jobs,'build_package',original_builder)
    retry = client.post(f'/api/v1/jobs/{failed}/retry',headers=h['maker'])
    assert retry.status_code==202,retry.text
    assert retry.json()['job_id']!=failed
    assert client.get(f'/api/v1/jobs/{retry.json()["job_id"]}',headers=h['maker']).json()['status']=='SUCCEEDED'


def test_legacy_json_endpoint_and_duplicate_worker_delivery(workspace):
    from pathlib import Path
    from app.jobs import run_job
    client,app,h,ids,pid,rid,jid = workspace
    payload = json.loads(Path('examples/cabinet.json').read_text())
    response = client.post('/api/v1/cabinet/generate',headers=h['maker'],json=payload)
    assert response.status_code==202,response.text
    data = response.json()
    assert data['approved_for_manufacturing'] is False
    assert client.get(data['status_url'],headers=h['maker']).json()['status']=='SUCCEEDED'
    before = (app.state.settings.storage/data['job_id']/'manifest.json').read_bytes()
    run_job(data['job_id'],app.state.db,app.state.settings)
    assert (app.state.settings.storage/data['job_id']/'manifest.json').read_bytes()==before


def test_changes_requested_record_freezes_revision(workspace):
    client,app,h,ids,pid,rid,jid = workspace
    review = client.post(f'/api/v1/revisions/{rid}/approvals',headers=h['designer'],
        json={'decision':'CHANGES_REQUESTED','comment':'Provide engineering and verified glass system'})
    assert review.status_code==201,review.text
    assert client.get(f'/api/v1/projects/{pid}/reviews',headers=h['maker']).json()[0]['decision']=='CHANGES_REQUESTED'
    assert client.post(f'/api/v1/revisions/{rid}/jobs',headers=h['maker']).status_code==409


def test_designer_verification_binds_measurement_revision(workspace):
    client,app,h,ids,pid,rid,jid = workspace
    data = spec(measured_dimensions_confirmed=True,dimension_provenance='physically_verified',
                measurement_reference='SYNTHETIC TEST MEASUREMENTS - NOT FACTORY EVIDENCE').model_dump(mode='json')
    r = client.post(f'/api/v1/projects/{pid}/revisions',headers=h['maker'],json=data).json()
    path = f'/api/v1/revisions/{r["id"]}/verify'
    assert client.post(path,headers=h['maker']).status_code==403
    assert client.post(path,headers=h['admin']).status_code==403
    verified = client.post(path,headers=h['designer'])
    assert verified.status_code==200,verified.text
    assert verified.json()['state']=='SPECIFICATION_VERIFIED'
    with app.state.db.sessions() as session:
        stored = session.get(Revision,r['id'])
        assert stored.verified_by==ids['designer'] and stored.verified_spec_hash==stored.spec_hash


def test_review_package_cannot_be_released_by_database_state_override(workspace):
    # Simulate a faulty integration changing state. No real approval is asserted.
    client,app,h,ids,pid,rid,jid = workspace
    with app.state.db.sessions.begin() as session:
        r,job = session.get(Revision,rid),session.get(Job,jid)
        old_state,old_validation = r.state,job.validation
        r.state = 'APPROVED'
        job.validation = {'has_blockers':False,'findings':[]}
        approval = Approval(revision_id=rid,job_id=jid,designer_id=ids['designer'],decision='APPROVED',
            comment='TEST ONLY: fault injection',spec_hash=r.spec_hash,manifest_hash=job.manifest_hash)
        session.add(approval)
        session.flush()
        aid = approval.id
    try:
        response = client.post(f'/api/v1/revisions/{rid}/release',headers=h['designer'])
        assert response.status_code==409,response.text
        assert 'review packages cannot be released' in response.json()['error']['message']
    finally:
        with app.state.db.sessions.begin() as session:
            session.get(Revision,rid).state = old_state
            session.get(Job,jid).validation = old_validation
            session.delete(session.get(Approval,aid))
