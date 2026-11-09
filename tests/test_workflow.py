from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from app.database import Database, Revision, User
from app.auth import hash_password
from app.workflow import ensure_approvable, transition
from app.validation_engine import validate_build
from app.cad import create_cabinet
from app.manufacturing import schedules_from_registry
from .test_cabinet import spec


def test_approval_binds_verification_and_fails_closed_on_report():
    # A synthetic policy fixture tests the gate; it is not a releasable design.
    r = SimpleNamespace(state='DESIGNER_REVIEW',spec_hash='abc',verified_by='designer',verified_spec_hash='abc')
    job = SimpleNamespace(state='SUCCEEDED',validation={'has_blockers':False,'findings':[]})
    ensure_approvable(r,job)
    for validation in (None,{}, {'has_blockers':True},
        {'has_blockers':False,'findings':[{'severity':'ERROR'}]}):
        job.validation = validation
        with pytest.raises(HTTPException): ensure_approvable(r,job)
    job.validation = {'has_blockers':False,'findings':[]}
    r.verified_spec_hash = 'old revision'
    with pytest.raises(HTTPException): ensure_approvable(r,job)


def test_transition_cannot_skip_validation_or_reopen_release():
    db = Database('sqlite:///:memory:')
    db.create_schema_for_tests()
    with db.sessions.begin() as session:
        r = SimpleNamespace(id='test',state='DRAFT')
        with pytest.raises(HTTPException): transition(r,'APPROVED',session,'test')
        transition(r,'SPECIFICATION_PENDING',session,'test')
        transition(r,'CAD_GENERATED',session,'worker')
        transition(r,'VALIDATION_PENDING',session,'worker')
        transition(r,'VALIDATION_FAILED',session,'worker')
        with pytest.raises(HTTPException): transition(r,'APPROVED',session,'test')
        r.state = 'PRODUCTION_RELEASED'
        with pytest.raises(HTTPException): transition(r,'DRAFT',session,'test')
    db.engine.dispose()


def test_missing_geometry_and_wrong_document_dimensions_are_findings():
    s = spec()
    b = create_cabinet(s)
    data = schedules_from_registry(b,s)
    data['parts'][0]['length_mm'] += 5
    del b.shapes['P005']
    codes = {f.code for f in validate_build(s,b,data).findings}
    assert {'COMPONENT_REGISTRY_MISMATCH','CUT_LIST_DIMENSION_MISMATCH'} <= codes


def test_migrations_upgrade_metadata_and_downgrade(tmp_path,monkeypatch):
    url = f'sqlite:///{tmp_path / "migration.db"}'
    monkeypatch.setenv('DATABASE_URL',url)
    config = Config('alembic.ini')
    command.upgrade(config,'head')
    command.check(config)
    db = Database(url)
    assert {'users','projects','revisions','jobs','approvals','releases','catalog_entries','audit_logs'} <= set(inspect(db.engine).get_table_names())
    assert 'verified_by' in {c['name'] for c in inspect(db.engine).get_columns('revisions')}
    db.engine.dispose()
    command.downgrade(config,'base')
    db = Database(url)
    assert 'users' not in inspect(db.engine).get_table_names()
    db.engine.dispose()
