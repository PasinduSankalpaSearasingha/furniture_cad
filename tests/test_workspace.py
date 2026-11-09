"""Public workspace assets and private project workflow discovery."""
import pytest
import re
from fastapi.testclient import TestClient
from app.auth import hash_password, issue_token
from app.config import Settings
from app.database import User, Project, AgentWorkflow
from app.main import create_app, Login, CreateProject
from sqlalchemy import select


@pytest.fixture
def workspace_ui(tmp_path):
    app = create_app(Settings(database_url=f'sqlite:///{tmp_path / "workspace.db"}',
                              storage=tmp_path/'outputs', executor='eager', ai_enabled=False))
    app.state.db.create_schema_for_tests()
    headers = {}
    with app.state.db.sessions.begin() as session:
        users = {}
        for name, role in [('owner','operator'),('reviewer','designer'),('outsider','viewer'),('admin','admin')]:
            user = User(username=name, role=role, password_hash=hash_password('workspace-test-password'))
            session.add(user)
            session.flush()
            users[name] = user
            headers[name] = {'Authorization': 'Bearer '+issue_token(session, user, 8)}
        project = Project(name='Private cabinet', owner_id=users['owner'].id,
                          reviewer_ids=[users['reviewer'].id])
        session.add(project)
        session.flush()
        for timestamp, mode, state in [('2026-01-01','ANALYZE','SUCCEEDED'),('2026-01-02','COLLABORATIVE','WAITING_FOR_DESIGNER')]:
            session.add(AgentWorkflow(project_id=project.id, created_by=users['owner'].id,
                mode=mode, state=state, input_data={'private_notes':'do not expose in listings'},
                input_hash='a'*64, model_config={}, created_at=timestamp))
        project_id = project.id
    with TestClient(app) as client:
        yield client, headers, project_id
    app.state.db.engine.dispose()


def test_public_workspace_and_local_assets(workspace_ui):
    client, _, _ = workspace_ui
    response = client.get('/')
    assert response.status_code == 200
    assert 'Fiske Studio' in response.text
    assert "script-src 'self'" in response.headers['Content-Security-Policy']
    assert client.get('/studio').content == response.content
    assert 'frame-src' in response.headers['Content-Security-Policy']
    assets=re.findall(r'(?:src|href)="(/studio-assets/[^\"]+)"',response.text)
    assert assets
    for path in ['/assets/mark.svg',*assets]:
        asset = client.get(path)
        assert asset.status_code == 200
        assert asset.headers['X-Content-Type-Options'] == 'nosniff'
    assert client.get('/.env').status_code == 404
    assert client.get('/assets/%2e%2e/config.py').status_code == 404
    assert client.get('/assets/%2e%2e/%2e%2e/.env').status_code == 404
    for filename in ('workspace.js','workspace.css','index.html','preview-gallery.js','model-viewer.js'):
        assert client.get('/assets/'+filename).status_code == 404


def test_workflow_listing_requires_project_access(workspace_ui):
    client, headers, project_id = workspace_ui
    path = f'/api/v1/projects/{project_id}/workflows'
    assert client.get(path).status_code == 401
    assert client.get(path, headers=headers['outsider']).status_code == 404
    for name in ('owner','reviewer','admin'):
        response = client.get(path, headers=headers[name])
        assert response.status_code == 200
        records = response.json()
        assert [record['mode'] for record in records] == ['COLLABORATIVE','ANALYZE']
        assert records[0]['status'] == 'WAITING_FOR_DESIGNER'
        assert records[0]['project_id'] == project_id
        assert 'private_notes' not in response.text
        assert not ({'input_data','model_config','result'} & set(records[0]))


def test_unknown_workflow_project_does_not_leak_records(workspace_ui):
    client, headers, _ = workspace_ui
    response = client.get('/api/v1/projects/00000000-0000-0000-0000-000000000000/workflows',
                          headers=headers['owner'])
    assert response.status_code == 404


def test_login_token_is_usable_before_dependency_teardown(workspace_ui):
    client, _, _ = workspace_ui
    app = client.app
    login = next(route.endpoint for route in app.routes
                 if getattr(route, 'path', None) == '/api/v1/auth/token' and 'POST' in route.methods)
    # Keep the write session open, as a request-scoped yield dependency does
    # until after sending its response. A second connection must see the token.
    with app.state.db.sessions.begin() as session:
        result = login(Login(username='owner', password='workspace-test-password'), session)
        response = client.get('/api/v1/auth/me', headers={'Authorization':'Bearer '+result['access_token']})
        assert response.status_code == 200
        assert response.json()['username'] == 'owner'


def test_created_project_is_visible_before_dependency_teardown(workspace_ui):
    client, headers, _ = workspace_ui
    app = client.app
    create = next(route.endpoint for route in app.routes
                  if getattr(route, 'path', None) == '/api/v1/projects' and 'POST' in route.methods)
    with app.state.db.sessions.begin() as session:
        owner = session.scalar(select(User).where(User.username == 'owner'))
        result = create(CreateProject(name='Immediately visible'), owner, session)
        projects = client.get('/api/v1/projects', headers=headers['owner']).json()
        assert any(project['id'] == result['id'] for project in projects)
