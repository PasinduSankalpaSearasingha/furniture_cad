"""Portable SQLAlchemy persistence; production uses PostgreSQL and Alembic."""
from datetime import datetime, timezone
from uuid import uuid4
from pathlib import Path
from sqlalchemy import (create_engine, String, Integer, Text, Boolean, ForeignKey,
                        UniqueConstraint, JSON, event)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool


def uid(): return str(uuid4())
def utcnow(): return datetime.now(timezone.utc).isoformat()


class Base(DeclarativeBase): pass


class User(Base):
    __tablename__ = 'users'
    id: Mapped[str] = mapped_column(String(36),primary_key=True,default=uid)
    username: Mapped[str] = mapped_column(String(80),unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(20))
    active: Mapped[bool] = mapped_column(Boolean,default=True)


class Token(Base):
    __tablename__ = 'tokens'
    digest: Mapped[str] = mapped_column(String(64),primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    expires_at: Mapped[str] = mapped_column(String(40))


class Project(Base):
    __tablename__ = 'projects'
    id: Mapped[str] = mapped_column(String(36),primary_key=True,default=uid)
    name: Mapped[str] = mapped_column(String(120))
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    reviewer_ids: Mapped[list] = mapped_column(JSON,default=list)
    revision_counter: Mapped[int] = mapped_column(Integer,default=0)
    created_at: Mapped[str] = mapped_column(String(40),default=utcnow)


class Revision(Base):
    __tablename__ = 'revisions'
    __table_args__ = (UniqueConstraint('project_id','number'),)
    id: Mapped[str] = mapped_column(String(36),primary_key=True,default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id'))
    number: Mapped[int] = mapped_column(Integer)
    specification: Mapped[dict] = mapped_column(JSON)
    spec_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(40),default='DRAFT')
    active_job_id: Mapped[str | None] = mapped_column(String(36),nullable=True)
    created_by: Mapped[str] = mapped_column(ForeignKey('users.id'))
    verified_by: Mapped[str | None] = mapped_column(ForeignKey('users.id'),nullable=True)
    verified_spec_hash: Mapped[str | None] = mapped_column(String(64),nullable=True)
    source_workflow_id: Mapped[str | None] = mapped_column(String(36),nullable=True)
    value_provenance: Mapped[dict | None] = mapped_column(JSON,nullable=True)
    created_at: Mapped[str] = mapped_column(String(40),default=utcnow)


class Job(Base):
    __tablename__ = 'jobs'
    id: Mapped[str] = mapped_column(String(36),primary_key=True,default=uid)
    revision_id: Mapped[str] = mapped_column(ForeignKey('revisions.id'))
    state: Mapped[str] = mapped_column(String(30),default='QUEUED')
    progress: Mapped[int] = mapped_column(Integer,default=0)
    stage: Mapped[str] = mapped_column(String(80),default='queued')
    error: Mapped[str | None] = mapped_column(Text,nullable=True)
    package_hash: Mapped[str | None] = mapped_column(String(64),nullable=True)
    manifest_hash: Mapped[str | None] = mapped_column(String(64),nullable=True)
    validation: Mapped[dict | None] = mapped_column(JSON,nullable=True)
    created_at: Mapped[str] = mapped_column(String(40),default=utcnow)
    updated_at: Mapped[str] = mapped_column(String(40),default=utcnow)


class Approval(Base):
    __tablename__ = 'approvals'
    id: Mapped[str] = mapped_column(String(36),primary_key=True,default=uid)
    revision_id: Mapped[str] = mapped_column(ForeignKey('revisions.id'))
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id'))
    designer_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    decision: Mapped[str] = mapped_column(String(30))
    comment: Mapped[str] = mapped_column(Text)
    spec_hash: Mapped[str] = mapped_column(String(64))
    manifest_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(40),default=utcnow)


class Release(Base):
    __tablename__ = 'releases'
    id: Mapped[str] = mapped_column(String(36),primary_key=True,default=uid)
    revision_id: Mapped[str] = mapped_column(ForeignKey('revisions.id'),unique=True)
    approval_id: Mapped[str] = mapped_column(ForeignKey('approvals.id'))
    package_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(40),default=utcnow)


class CatalogEntry(Base):
    __tablename__ = 'catalog_entries'
    __table_args__ = (UniqueConstraint('kind','reference'),)
    id: Mapped[str] = mapped_column(String(36),primary_key=True,default=uid)
    kind: Mapped[str] = mapped_column(String(20))
    reference: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[str] = mapped_column(String(40),default=utcnow)


class AuditLog(Base):
    __tablename__ = 'audit_logs'
    id: Mapped[str] = mapped_column(String(36),primary_key=True,default=uid)
    actor_id: Mapped[str] = mapped_column(String(36))
    event: Mapped[str] = mapped_column(String(80))
    resource_id: Mapped[str] = mapped_column(String(36))
    details: Mapped[dict] = mapped_column(JSON,default=dict)
    created_at: Mapped[str] = mapped_column(String(40),default=utcnow)


class ImageAsset(Base):
    __tablename__ = 'image_assets'
    id: Mapped[str] = mapped_column(String(36),primary_key=True,default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id'),index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey('users.id'))
    kind: Mapped[str] = mapped_column(String(30))
    sha256: Mapped[str] = mapped_column(String(64))
    byte_count: Mapped[int] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    provenance: Mapped[dict] = mapped_column(JSON,default=dict)
    created_at: Mapped[str] = mapped_column(String(40),default=utcnow)


class AgentWorkflow(Base):
    __tablename__ = 'agent_workflows'
    __table_args__ = (UniqueConstraint('project_id','idempotency_key'),)
    id: Mapped[str] = mapped_column(String(36),primary_key=True,default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id'),index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey('users.id'))
    mode: Mapped[str] = mapped_column(String(30))
    state: Mapped[str] = mapped_column(String(40),default='QUEUED')
    stage: Mapped[str] = mapped_column(String(80),default='queued')
    progress: Mapped[int] = mapped_column(Integer,default=0)
    input_data: Mapped[dict] = mapped_column(JSON)
    input_hash: Mapped[str] = mapped_column(String(64))
    model_config: Mapped[dict] = mapped_column(JSON)
    checkpoints: Mapped[dict] = mapped_column(JSON,default=dict)
    result: Mapped[dict | None] = mapped_column(JSON,nullable=True)
    error: Mapped[dict | None] = mapped_column(JSON,nullable=True)
    attempts: Mapped[int] = mapped_column(Integer,default=0)
    lease_token: Mapped[str | None] = mapped_column(String(36),nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(80),nullable=True)
    revision_id: Mapped[str | None] = mapped_column(ForeignKey('revisions.id'),nullable=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey('jobs.id'),nullable=True)
    created_at: Mapped[str] = mapped_column(String(40),default=utcnow)
    updated_at: Mapped[str] = mapped_column(String(40),default=utcnow)


class AgentEvent(Base):
    __tablename__ = 'agent_events'
    id: Mapped[int] = mapped_column(Integer,primary_key=True,autoincrement=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey('agent_workflows.id'),index=True)
    event: Mapped[str] = mapped_column(String(80))
    stage: Mapped[str] = mapped_column(String(80))
    details: Mapped[dict] = mapped_column(JSON,default=dict)
    created_at: Mapped[str] = mapped_column(String(40),default=utcnow)


class AIInvocation(Base):
    __tablename__ = 'ai_invocations'
    id: Mapped[str] = mapped_column(String(36),primary_key=True,default=uid)
    workflow_id: Mapped[str] = mapped_column(ForeignKey('agent_workflows.id'),index=True)
    stage: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_id: Mapped[str | None] = mapped_column(String(120),nullable=True)
    status: Mapped[str] = mapped_column(String(30),default='STARTED')
    usage: Mapped[dict] = mapped_column(JSON,default=dict)
    cost: Mapped[dict] = mapped_column(JSON,default=lambda:{'status':'PRICE_REQUIRED','total':None})
    error_code: Mapped[str | None] = mapped_column(String(80),nullable=True)
    created_at: Mapped[str] = mapped_column(String(40),default=utcnow)


class Database:
    def __init__(self, url):
        args = {}
        if url.startswith('sqlite'):
            args['connect_args'] = {'check_same_thread':False,'timeout':30}
            if ':memory:' in url: args['poolclass'] = StaticPool
            elif url.startswith('sqlite:///'):
                Path(url.removeprefix('sqlite:///')).resolve().parent.mkdir(parents=True,exist_ok=True)
        self.engine = create_engine(url, pool_pre_ping=True, **args)
        if url.startswith('sqlite'):
            @event.listens_for(self.engine, 'connect')
            def sqlite_settings(connection, record):
                connection.execute('PRAGMA foreign_keys=ON')
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def create_schema_for_tests(self):
        Base.metadata.create_all(self.engine)


def audit(session, actor, event, resource, **details):
    session.add(AuditLog(actor_id=actor,event=event,resource_id=resource,details=details))


def commit_response(session, payload):
    """Make writes visible before HTTP success; yield teardown runs after send."""
    session.commit()
    return payload
