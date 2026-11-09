import hashlib
import hmac
import secrets
import ipaddress
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from .database import User, Token

bearer = HTTPBearer(auto_error=False)
LOCAL_WORKSPACE_USER_ID = '00000000-0000-4000-8000-000000000001'
WORKSPACE_COOKIE = 'fiske_session'


def require_same_origin(request: Request):
    origin=request.headers.get('origin')
    if (request.headers.get('sec-fetch-site','none') not in ('none','same-origin') or
            (origin and origin.rstrip('/')!=str(request.base_url).rstrip('/'))):
        raise HTTPException(403,'Use the workspace from the same origin')


def local_workspace_user(request: Request):
    """Resolve the single local designer without accepting remote or cross-site access."""
    try:
        peer = ipaddress.ip_address(request.client.host if request.client else '')
        local_peer = peer.is_loopback or bool(getattr(peer,'ipv4_mapped',None) and peer.ipv4_mapped.is_loopback)
    except ValueError:
        local_peer = False
    origin = request.headers.get('origin')
    if (not local_peer or request.url.hostname not in ('127.0.0.1','localhost','::1') or
        request.headers.get('sec-fetch-site','none') not in ('none','same-origin') or
        (origin and origin.rstrip('/') != str(request.base_url).rstrip('/'))):
        raise HTTPException(403,'Local workspace requires a direct same-origin loopback connection')
    db = request.app.state.db
    try:
        with db.sessions.begin() as session:
            user = session.get(User,LOCAL_WORKSPACE_USER_ID)
            if user is None:
                user = User(id=LOCAL_WORKSPACE_USER_ID,username='local-workspace',password_hash='!',role='designer')
                session.add(user)
                session.flush()
    except IntegrityError:
        # Concurrent first requests may attempt to create the same identity.
        with db.sessions() as session:
            user = session.get(User,LOCAL_WORKSPACE_USER_ID)
    if not user or not user.active or user.role!='designer' or user.password_hash!='!':
        raise HTTPException(409,'Local workspace identity is unavailable')
    return user


def hash_password(password: str):
    salt = secrets.token_hex(16)
    key = hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()
    return f'scrypt${salt}${key}'


def verify_password(password: str, stored: str):
    try:
        _,salt,expected = stored.split('$')
        actual = hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()
        return hmac.compare_digest(actual,expected)
    except (ValueError,TypeError): return False


def issue_token(session, user, hours):
    raw = secrets.token_urlsafe(32)
    session.add(Token(digest=hashlib.sha256(raw.encode()).hexdigest(),user_id=user.id,
                      expires_at=(datetime.now(timezone.utc)+timedelta(hours=hours)).isoformat()))
    return raw


def current_user(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    raw=credentials.credentials if credentials else request.cookies.get(WORKSPACE_COOKIE)
    if not credentials and raw:require_same_origin(request)
    if not raw:
        if request.app.state.settings.local_workspace: return local_workspace_user(request)
        raise HTTPException(401,'Authentication required',headers={'WWW-Authenticate':'Bearer'})
    digest = hashlib.sha256(raw.encode()).hexdigest()
    with request.app.state.db.sessions() as session:
        token = session.get(Token,digest)
        if token is None or datetime.fromisoformat(token.expires_at) <= datetime.now(timezone.utc):
            raise HTTPException(401,'Invalid or expired token',headers={'WWW-Authenticate':'Bearer'})
        user = session.get(User,token.user_id)
        if not user or not user.active: raise HTTPException(401,'Inactive account')
        return user


def require_role(user, *roles):
    if user.role not in roles: raise HTTPException(403,'Role does not permit this action')
