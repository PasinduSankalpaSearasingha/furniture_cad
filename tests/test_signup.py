"""Self-service sign-up for hosted deployments (ALLOW_SIGNUP=1)."""
import pytest
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app

ORIGIN = 'https://studio.example'
SAME = {'Origin':ORIGIN,'Sec-Fetch-Site':'same-origin'}


def make_app(tmp_path, **changes):
    app = create_app(Settings(database_url=f'sqlite:///{tmp_path / "signup.db"}', storage=tmp_path/'outputs',
                              executor='eager', ai_enabled=False, **changes))
    app.state.db.create_schema_for_tests()
    return app


def test_signup_creates_a_signed_in_designer_with_private_projects(tmp_path):
    app = make_app(tmp_path, allow_signup=True)
    with TestClient(app, base_url=ORIGIN, headers=SAME) as client:
        assert client.get('/api/v1/workspace/access').json()['signup'] is True
        assert client.post('/api/v1/workspace/signup',json={'username':'aziz','password':'longenough1'}).status_code == 201
        assert client.get('/api/v1/auth/me').json()['role'] == 'designer'
        assert client.post('/api/v1/projects',json={'name':'Desk'}).status_code == 201
        assert client.post('/api/v1/workspace/signup',json={'username':'aziz','password':'longenough1'}).status_code == 409
        assert client.post('/api/v1/workspace/signup',json={'username':'other','password':'short'}).status_code == 422
    with TestClient(app, base_url=ORIGIN, headers=SAME) as other:
        assert other.post('/api/v1/workspace/signup',json={'username':'other','password':'longenough2'}).status_code == 201
        assert other.get('/api/v1/projects').json() == []
    with TestClient(app, base_url=ORIGIN, headers=SAME) as again:
        assert again.post('/api/v1/workspace/session',json={'username':'aziz','password':'longenough1'}).status_code == 200
        assert [p['name'] for p in again.get('/api/v1/projects').json()] == ['Desk']
    app.state.db.engine.dispose()


@pytest.mark.parametrize('allow,headers',[(False,SAME),(True,{'Origin':'https://evil.example','Sec-Fetch-Site':'cross-site'})])
def test_signup_is_refused_when_off_or_cross_site(tmp_path, allow, headers):
    app = make_app(tmp_path, allow_signup=allow)
    with TestClient(app, base_url=ORIGIN, headers=headers) as client:
        assert client.post('/api/v1/workspace/signup',json={'username':'nobody','password':'longenough1'}).status_code == 403
    app.state.db.engine.dispose()
