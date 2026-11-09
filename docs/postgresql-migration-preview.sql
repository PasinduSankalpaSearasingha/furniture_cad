BEGIN;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL, 
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 7457427985fd

CREATE TABLE audit_logs (
    id VARCHAR(36) NOT NULL, 
    actor_id VARCHAR(36) NOT NULL, 
    event VARCHAR(80) NOT NULL, 
    resource_id VARCHAR(36) NOT NULL, 
    details JSON NOT NULL, 
    created_at VARCHAR(40) NOT NULL, 
    PRIMARY KEY (id)
);

CREATE TABLE users (
    id VARCHAR(36) NOT NULL, 
    username VARCHAR(80) NOT NULL, 
    password_hash TEXT NOT NULL, 
    role VARCHAR(20) NOT NULL, 
    active BOOLEAN NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (username)
);

CREATE TABLE catalog_entries (
    id VARCHAR(36) NOT NULL, 
    kind VARCHAR(20) NOT NULL, 
    reference VARCHAR(80) NOT NULL, 
    payload JSON NOT NULL, 
    created_by VARCHAR(36) NOT NULL, 
    created_at VARCHAR(40) NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(created_by) REFERENCES users (id), 
    UNIQUE (kind, reference)
);

CREATE TABLE projects (
    id VARCHAR(36) NOT NULL, 
    name VARCHAR(120) NOT NULL, 
    owner_id VARCHAR(36) NOT NULL, 
    reviewer_ids JSON NOT NULL, 
    revision_counter INTEGER NOT NULL, 
    created_at VARCHAR(40) NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(owner_id) REFERENCES users (id)
);

CREATE TABLE tokens (
    digest VARCHAR(64) NOT NULL, 
    user_id VARCHAR(36) NOT NULL, 
    expires_at VARCHAR(40) NOT NULL, 
    PRIMARY KEY (digest), 
    FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE TABLE revisions (
    id VARCHAR(36) NOT NULL, 
    project_id VARCHAR(36) NOT NULL, 
    number INTEGER NOT NULL, 
    specification JSON NOT NULL, 
    spec_hash VARCHAR(64) NOT NULL, 
    state VARCHAR(40) NOT NULL, 
    active_job_id VARCHAR(36), 
    created_by VARCHAR(36) NOT NULL, 
    created_at VARCHAR(40) NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(created_by) REFERENCES users (id), 
    FOREIGN KEY(project_id) REFERENCES projects (id), 
    UNIQUE (project_id, number)
);

CREATE TABLE jobs (
    id VARCHAR(36) NOT NULL, 
    revision_id VARCHAR(36) NOT NULL, 
    state VARCHAR(30) NOT NULL, 
    progress INTEGER NOT NULL, 
    stage VARCHAR(80) NOT NULL, 
    error TEXT, 
    package_hash VARCHAR(64), 
    manifest_hash VARCHAR(64), 
    validation JSON, 
    created_at VARCHAR(40) NOT NULL, 
    updated_at VARCHAR(40) NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(revision_id) REFERENCES revisions (id)
);

CREATE TABLE approvals (
    id VARCHAR(36) NOT NULL, 
    revision_id VARCHAR(36) NOT NULL, 
    job_id VARCHAR(36) NOT NULL, 
    designer_id VARCHAR(36) NOT NULL, 
    decision VARCHAR(30) NOT NULL, 
    comment TEXT NOT NULL, 
    spec_hash VARCHAR(64) NOT NULL, 
    manifest_hash VARCHAR(64) NOT NULL, 
    created_at VARCHAR(40) NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(designer_id) REFERENCES users (id), 
    FOREIGN KEY(job_id) REFERENCES jobs (id), 
    FOREIGN KEY(revision_id) REFERENCES revisions (id)
);

CREATE TABLE releases (
    id VARCHAR(36) NOT NULL, 
    revision_id VARCHAR(36) NOT NULL, 
    approval_id VARCHAR(36) NOT NULL, 
    package_hash VARCHAR(64) NOT NULL, 
    created_at VARCHAR(40) NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(approval_id) REFERENCES approvals (id), 
    FOREIGN KEY(revision_id) REFERENCES revisions (id), 
    UNIQUE (revision_id)
);

INSERT INTO alembic_version (version_num) VALUES ('7457427985fd') RETURNING alembic_version.version_num;

-- Running upgrade 7457427985fd -> d9a788ced8d0

ALTER TABLE revisions ADD COLUMN verified_by VARCHAR(36);

ALTER TABLE revisions ADD COLUMN verified_spec_hash VARCHAR(64);

ALTER TABLE revisions ADD CONSTRAINT fk_revisions_verified_by FOREIGN KEY(verified_by) REFERENCES users (id);

UPDATE alembic_version SET version_num='d9a788ced8d0' WHERE alembic_version.version_num = '7457427985fd';

-- Running upgrade d9a788ced8d0 -> 86f2821791ed

CREATE TABLE image_assets (
    id VARCHAR(36) NOT NULL, 
    project_id VARCHAR(36) NOT NULL, 
    created_by VARCHAR(36) NOT NULL, 
    kind VARCHAR(30) NOT NULL, 
    sha256 VARCHAR(64) NOT NULL, 
    byte_count INTEGER NOT NULL, 
    width INTEGER NOT NULL, 
    height INTEGER NOT NULL, 
    provenance JSON NOT NULL, 
    created_at VARCHAR(40) NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(created_by) REFERENCES users (id), 
    FOREIGN KEY(project_id) REFERENCES projects (id)
);

CREATE INDEX ix_image_assets_project_id ON image_assets (project_id);

CREATE TABLE agent_workflows (
    id VARCHAR(36) NOT NULL, 
    project_id VARCHAR(36) NOT NULL, 
    created_by VARCHAR(36) NOT NULL, 
    mode VARCHAR(30) NOT NULL, 
    state VARCHAR(40) NOT NULL, 
    stage VARCHAR(80) NOT NULL, 
    progress INTEGER NOT NULL, 
    input_data JSON NOT NULL, 
    input_hash VARCHAR(64) NOT NULL, 
    model_config JSON NOT NULL, 
    checkpoints JSON NOT NULL, 
    result JSON, 
    error JSON, 
    attempts INTEGER NOT NULL, 
    lease_token VARCHAR(36), 
    idempotency_key VARCHAR(80), 
    revision_id VARCHAR(36), 
    job_id VARCHAR(36), 
    created_at VARCHAR(40) NOT NULL, 
    updated_at VARCHAR(40) NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(created_by) REFERENCES users (id), 
    FOREIGN KEY(job_id) REFERENCES jobs (id), 
    FOREIGN KEY(project_id) REFERENCES projects (id), 
    FOREIGN KEY(revision_id) REFERENCES revisions (id), 
    UNIQUE (project_id, idempotency_key)
);

CREATE INDEX ix_agent_workflows_project_id ON agent_workflows (project_id);

CREATE TABLE agent_events (
    id SERIAL NOT NULL, 
    workflow_id VARCHAR(36) NOT NULL, 
    event VARCHAR(80) NOT NULL, 
    stage VARCHAR(80) NOT NULL, 
    details JSON NOT NULL, 
    created_at VARCHAR(40) NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(workflow_id) REFERENCES agent_workflows (id)
);

CREATE INDEX ix_agent_events_workflow_id ON agent_events (workflow_id);

CREATE TABLE ai_invocations (
    id VARCHAR(36) NOT NULL, 
    workflow_id VARCHAR(36) NOT NULL, 
    stage VARCHAR(80) NOT NULL, 
    model VARCHAR(100) NOT NULL, 
    request_hash VARCHAR(64) NOT NULL, 
    response_id VARCHAR(120), 
    status VARCHAR(30) NOT NULL, 
    usage JSON NOT NULL, 
    cost JSON NOT NULL, 
    error_code VARCHAR(80), 
    created_at VARCHAR(40) NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(workflow_id) REFERENCES agent_workflows (id)
);

CREATE INDEX ix_ai_invocations_workflow_id ON ai_invocations (workflow_id);

ALTER TABLE revisions ADD COLUMN source_workflow_id VARCHAR(36);

ALTER TABLE revisions ADD COLUMN value_provenance JSON;

UPDATE alembic_version SET version_num='86f2821791ed' WHERE alembic_version.version_num = 'd9a788ced8d0';

COMMIT;
