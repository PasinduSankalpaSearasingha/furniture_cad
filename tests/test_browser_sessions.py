"""Browser cookies protect native downloads as well as JSON and mutation routes."""
import hashlib
from datetime import datetime,timedelta,timezone
import pytest
from app.auth import WORKSPACE_COOKIE
from app.database import Token
from tests.test_workspace import workspace_ui


def login(client,**kwargs):
    return client.post('/api/v1/workspace/session',json={'username':'owner','password':'workspace-test-password'},**kwargs)


def test_browser_session_is_private_and_revoked_on_sign_out(workspace_ui):
    client,_,project=workspace_ui
    assert client.get('/api/projects').status_code==401
    failed=client.post('/api/v1/workspace/session',json={'username':'owner','password':'incorrect'})
    assert failed.status_code==401 and WORKSPACE_COOKIE not in client.cookies
    response=login(client)
    assert response.json()=={'authenticated':True}
    cookie=response.headers['set-cookie'].lower()
    assert 'httponly' in cookie and 'samesite=strict' in cookie and 'path=/' in cookie
    assert client.get('/api/v1/auth/me').json()['username']=='owner'
    assert client.get(f'/api/projects/{project}').status_code==200
    raw=client.cookies[WORKSPACE_COOKIE]
    assert client.delete('/api/v1/workspace/session').status_code==204
    assert WORKSPACE_COOKIE not in client.cookies
    assert client.get('/api/projects').status_code==401
    with client.app.state.db.sessions() as session:
        assert session.get(Token,hashlib.sha256(raw.encode()).hexdigest()) is None
    client.cookies.set(WORKSPACE_COOKIE,raw)
    assert client.get('/api/projects').status_code==401


@pytest.mark.parametrize('headers',[
    {'Origin':'https://other.example'}, {'Sec-Fetch-Site':'cross-site'},
    {'Sec-Fetch-Site':'same-site'},
])
def test_cross_origin_browser_sessions_and_mutations_rejected(workspace_ui,headers):
    client,_,_=workspace_ui
    assert login(client,headers=headers).status_code==403
    assert login(client).status_code==200
    assert client.post('/api/projects',json={'name':'Forbidden cross-site write'},headers=headers).status_code==403
    assert client.get('/api/projects',headers=headers).status_code==403
    assert client.delete('/api/v1/workspace/session',headers=headers).status_code==403


def test_expired_cookie_and_bearer_sessions_are_rejected(workspace_ui):
    client,headers,_=workspace_ui
    assert login(client).status_code==200
    with client.app.state.db.sessions.begin() as session:
        token=session.get(Token,hashlib.sha256(client.cookies[WORKSPACE_COOKIE].encode()).hexdigest())
        token.expires_at=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
    assert client.get('/api/projects').status_code==401
    # Explicit API credentials continue to work even with a stale browser cookie.
    assert client.get('/api/projects',headers=headers['owner']).status_code==200


def test_https_browser_cookie_is_secure(workspace_ui):
    from fastapi.testclient import TestClient
    client,_,_=workspace_ui
    with TestClient(client.app,base_url='https://studio.example') as secure:
        response=login(secure,headers={'Origin':'https://studio.example'})
        assert response.status_code==200
        assert 'secure' in response.headers['set-cookie'].lower()
        assert secure.get('/api/projects').status_code==200
