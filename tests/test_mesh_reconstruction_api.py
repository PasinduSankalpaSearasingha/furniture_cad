from io import BytesIO
from pathlib import Path
from uuid import uuid4
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from app.database import User, Project
from app.auth import hash_password, issue_token
from app.ai.images import store_image


@pytest.fixture
def api_env(tmp_path, monkeypatch):
    config = Settings(database_url=f'sqlite:///{tmp_path / "test.db"}', storage=tmp_path/'outputs', executor='local')
    app = create_app(config); app.state.db.create_schema_for_tests()
    tokens = {}
    with app.state.db.sessions.begin() as session:
        for name in ('owner', 'outsider'):
            user = User(username=name, password_hash=hash_password('test-password-123'), role='designer')
            session.add(user); session.flush()
            tokens[name] = {'Authorization': 'Bearer '+issue_token(session, user, 8)}
            if name == 'owner': owner = user.id
        project = Project(name='Dense fitting tests', owner_id=owner); session.add(project); session.flush()
        project_id = project.id
        stream = BytesIO(); Image.new('RGB', (256, 256), 'white').save(stream, format='PNG')
        image = store_image(session, config, project_id, owner, stream.getvalue()); image_id = image.id
    calls = []
    monkeypatch.setattr('app.api.neural4d.dispatch_mesh_job', lambda *args: calls.append(args))
    monkeypatch.setenv('NEURAL4D_API_KEY', 'mock-credential-no-provider-calls')
    with TestClient(app) as client:
        yield client, app, tokens, project_id, image_id, calls
    app.state.db.engine.dispose()


def test_project_ownership_idempotency_and_async_dispatch(api_env):
    client, app, tokens, project, image, calls = api_env
    body = dict(image_id=image, idempotency_key='one-submission')
    url = f'/api/projects/{project}/mesh-reconstruction'
    assert client.post(url, json=body).status_code == 401
    assert client.post(url, json=body, headers=tokens['outsider']).status_code == 404
    first = client.post(url, json=body, headers=tokens['owner'])
    assert first.status_code == 202, first.text
    second = client.post(url, json=body, headers=tokens['owner'])
    assert first.json()['job_id'] == second.json()['job_id'] and len(calls) == 1
    from app.mesh_reconstruction_jobs import ReconstructionJobs
    saved=ReconstructionJobs(app.state.settings.storage).get(first.json()['job_id'])
    assert saved['request']['optimization']['primary_fitting_method']=='global_surface'
    assert saved['request']['optimization']['multi_angle'] is False
    status = first.json()['status_url']
    assert client.get(status, headers=tokens['outsider']).status_code == 404
    assert client.get(status, headers=tokens['owner']).json()['status'] == 'QUEUED'
    assert client.post(url, json={**body, 'furniture_type':'chair'}, headers=tokens['owner']).status_code == 422
    assert client.get(status+'/files/request.json', headers=tokens['owner']).status_code == 409
    assert client.post(status+'/resume', headers=tokens['owner']).status_code == 409


def test_missing_credentials_never_dispatches(api_env, monkeypatch):
    client, app, tokens, project, image, calls = api_env
    monkeypatch.delenv('NEURAL4D_API_KEY')
    response = client.post(f'/api/projects/{project}/mesh-reconstruction',
                          json=dict(image_id=image, idempotency_key='missing-key'), headers=tokens['owner'])
    assert response.status_code == 503 and calls == []
    assert not client.get('/api/system/neural4d/status', headers=tokens['owner']).json()['credentials_configured']


def test_only_owned_reference_evidence_is_accepted(api_env):
    client, app, tokens, project, image, calls = api_env
    response = client.post(f'/api/projects/{project}/mesh-reconstruction', headers=tokens['owner'], json=dict(
        image_id=image, idempotency_key='mismatched-evidence', optimization=dict(photo_views=[dict(image_id=str(uuid4()))])))
    assert response.status_code == 422 and calls == []


def test_configured_point_counts_and_disabled_global_solver_are_preserved(api_env,monkeypatch):
    client,app,tokens,project,image,calls=api_env
    monkeypatch.setenv('GLOBAL_COARSE_POINTS','5000')
    monkeypatch.setenv('GLOBAL_3D_FITTING_ENABLED','false')
    status=client.get('/api/system/neural4d/status',headers=tokens['owner']).json()
    assert status['point_counts']['economy']==5000
    assert status['virtual_cameras_required'] is False
    response=client.post(f'/api/projects/{project}/mesh-reconstruction',headers=tokens['owner'],
        json=dict(image_id=image,idempotency_key='disabled-global',optimization=dict(enabled=True)))
    assert response.status_code==202
    from app.mesh_reconstruction_jobs import ReconstructionJobs
    saved=ReconstructionJobs(app.state.settings.storage).get(response.json()['job_id'])
    assert saved['request']['optimization']['enabled'] is False


def test_nested_artifacts_and_local_revision_preserve_provider_evidence(api_env):
    import json
    from app.pipeline import file_hash
    from app.mesh_reconstruction_jobs import ReconstructionJobs
    from tests.test_engineering_cad import table_plan
    client,app,tokens,project,image,calls=api_env
    start=client.post(f'/api/projects/{project}/mesh-reconstruction',headers=tokens['owner'],
        json=dict(image_id=image,idempotency_key='source',known_dimensions={'width_mm':700})).json()
    store=ReconstructionJobs(app.state.settings.storage);folder=store.root/start['job_id']
    (folder/'feature_specification.json').write_text(table_plan().model_dump_json(),encoding='utf-8')
    (folder/'original_neural4d.glb').write_bytes(b'provider-mesh-evidence')
    (folder/'cad').mkdir();(folder/'cad'/'assembly.step').write_text('test-artifact')
    files={p.relative_to(folder).as_posix():file_hash(p) for p in folder.rglob('*') if p.is_file()}
    (folder/'manifest.json').write_text(json.dumps(dict(files=files)),encoding='utf-8')
    store.update(start['job_id'],status='SUCCEEDED',manifest_sha256=file_hash(folder/'manifest.json'))
    url=start['status_url']
    assert client.get(url+'/files/cad/assembly.step',headers=tokens['owner']).status_code==200
    assert client.get(url+'/files/cad/assembly.step',headers=tokens['outsider']).status_code==404
    assert client.get(url+'/files/private%2Fsol_calls.sqlite',headers=tokens['owner']).status_code==404
    assert client.post(url+'/revise',headers=tokens['outsider'],json={'idempotency_key':'forbidden'}).status_code==404
    invalid=client.post(url+'/revise',headers=tokens['owner'],json={'idempotency_key':'locked','parameters':{'width_mm':720}})
    assert invalid.status_code==422
    body=dict(idempotency_key='local-revision',parameters={'width_mm':720},confirmed_dimensions={'width_mm':720},
              boundary_reviews={'F001':'accepted'},part_labels={'F001':'Reviewed table top'})
    response=client.post(url+'/revise',headers=tokens['owner'],json=body)
    assert response.status_code==202,response.text
    child=store.get(response.json()['job_id']);request=child['request']
    assert request['local_revision'] and request['part_recognition'] is False
    assert request['optimization']['enabled'] is False and request['parent_job_id']==start['job_id']
    assert (store.root/child['id']/'original_neural4d.glb').read_bytes()==b'provider-mesh-evidence'
    assert client.post(url+'/revise',headers=tokens['owner'],json=body).json()['job_id']==child['id']
    assert len(calls)==2 # only source and local worker dispatches; no provider HTTP calls in API
