"""Password-free local design work without exposing the shared API."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from app.auth import LOCAL_WORKSPACE_USER_ID, hash_password
from app.config import Settings
from app.database import User, Project
from app.main import create_app


@pytest.fixture
def local_app(tmp_path):
    app = create_app(Settings(database_url=f'sqlite:///{tmp_path / "local.db"}',
        storage=tmp_path/'outputs', executor='eager', ai_enabled=False, local_workspace=True))
    app.state.db.create_schema_for_tests()
    yield app
    app.state.db.engine.dispose()


def local_client(app, **changes):
    return TestClient(app, base_url=changes.pop('base_url','http://127.0.0.1:8000'),
                      client=changes.pop('client',('127.0.0.1',50000)), **changes)


def test_local_workflow_does_not_request_a_password(local_app):
    with local_client(local_app) as client:
        access = client.get('/api/v1/workspace/access')
        assert access.status_code == 200
        assert access.json()['mode'] == 'local'
        assert set(access.json()['user']) == {'id','username','role'}
        assert access.json()['user']['role'] == 'designer'
        created = client.post('/api/v1/projects',json={'name':'No login required'})
        assert created.status_code == 201
        project_id = created.json()['id']
        revision = client.post(f'/api/v1/projects/{project_id}/revisions',json={
            'width_mm':700,'height_mm':1800,'depth_mm':450,
            'panel_thickness_mm':18,'carcass_material':'plywood'})
        assert revision.status_code == 201
        assert client.get(f'/api/v1/projects/{project_id}/revisions').json()[0]['id'] == revision.json()['id']
        material = client.post('/api/v1/catalogs/materials',json={
            'material_id':'LOCAL-PLY','name':'Local plywood','material':'plywood','actual_thickness_mm':18})
        assert material.status_code == 201
        assert client.get('/api/v1/auth/me').json()['id'] == LOCAL_WORKSPACE_USER_ID
        assert client.post('/api/v1/auth/token',json={'username':'local-workspace','password':'anything'}).status_code == 401
        assert client.post('/api/v1/users',json={'username':'extra-admin','password':'test-password-123','role':'admin'}).status_code == 403
        page = client.get('/')
        assert page.status_code==200 and 'Photo to CAD' in page.text
        assert client.get('/assets/workspace.js').status_code==404


def test_local_identity_and_projects_survive_reopening(local_app):
    with local_client(local_app) as client:
        first = client.get('/api/v1/workspace/access').json()['user']['id']
        project = client.post('/api/v1/projects',json={'name':'Persistent local project'}).json()
    with local_client(local_app) as client:
        assert client.get('/api/v1/workspace/access').json()['user']['id'] == first
        assert client.get('/api/v1/projects').json()[0]['id'] == project['id']
    with local_app.state.db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(User)) == 1


def test_local_identity_does_not_take_over_other_projects(local_app):
    with local_app.state.db.sessions.begin() as session:
        other = User(username='existing-owner',password_hash=hash_password('test-password-123'),role='designer')
        session.add(other)
        session.flush()
        project = Project(name='Other private project',owner_id=other.id,reviewer_ids=[])
        session.add(project)
        session.flush()
        project_id = project.id
    with local_client(local_app) as client:
        assert client.get('/api/v1/projects').json() == []
        assert client.get(f'/api/v1/projects/{project_id}/revisions').status_code == 404


@pytest.mark.parametrize('changes',[
    {'client':('192.0.2.10',50000)},
    {'base_url':'http://unexpected.example'},
    {'headers':{'Origin':'https://unexpected.example'}},
    {'headers':{'Sec-Fetch-Site':'cross-site'}},
    {'headers':{'Origin':'http://127.0.0.1:9000'}},
])
def test_local_access_rejects_remote_and_cross_origin_requests(local_app,changes):
    with local_client(local_app,**changes) as client:
        assert client.get('/api/v1/workspace/access').status_code == 403
        assert client.get('/api/v1/projects').status_code == 403
        assert client.post('/api/v1/projects',json={'name':'Forbidden'}).status_code == 403
    with local_app.state.db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(User)) == 0


def test_local_mode_is_optional_and_accepts_same_origin(local_app):
    with local_client(local_app, headers={'Origin':'http://127.0.0.1:8000','Sec-Fetch-Site':'same-origin'}) as client:
        assert client.get('/api/v1/workspace/access').status_code == 200
    local_app.state.settings.local_workspace = False
    with local_client(local_app) as client:
        assert client.get('/api/v1/workspace/access').json() == {'mode':'authenticated','user':None,'signup':False}
        assert client.get('/api/v1/projects').status_code == 401


def test_public_workspace_opens_without_login_behind_a_proxy(local_app):
    local_app.state.settings.public_workspace = True
    remote = dict(base_url='https://fraugment.com', client=('10.0.1.5',50000))
    with local_client(local_app, headers={'Origin':'https://fraugment.com','Sec-Fetch-Site':'same-origin'}, **remote) as client:
        assert client.get('/api/v1/workspace/access').json()['mode'] == 'local'
        assert client.post('/api/v1/projects',json={'name':'Hosted, no login'}).status_code == 201
    with local_client(local_app, headers={'Origin':'https://evil.example','Sec-Fetch-Site':'cross-site'}, **remote) as client:
        assert client.post('/api/v1/projects',json={'name':'Cross-site'}).status_code == 403
