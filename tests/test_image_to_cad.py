"""Removed provider routes and inputs cannot submit paid requests."""
from uuid import uuid4
import pytest
from app.agents import orchestrator
from app.database import AgentWorkflow, Project
from app.pipeline import canonical_hash
from tests.test_agent_workflows import agents_app


def test_removed_provider_routes_are_unavailable(agents_app):
    client, app, headers, project, image = agents_app
    paths = {getattr(route, 'path', '') for route in app.routes}
    assert not any('generate-neural4d' in path.lower() for path in paths)
    assert client.get('/api/system/neural4d/status', headers=headers['operator']).status_code == 404
    assert client.post(f'/api/projects/{project}/generate-neural4d',
        headers=headers['operator'], json={'image_id': image}).status_code == 404


@pytest.mark.parametrize('fields', [
    {'cad_source': 'NEURAL4D_MESH'},
    {'mesh_reference_workflow_id': str(uuid4())},
    {'texture': True},
])
def test_generation_rejects_removed_provider_inputs(agents_app, monkeypatch, fields):
    client, app, headers, project, image = agents_app
    def forbidden(*args, **kwargs):
        raise AssertionError('Invalid requests must not dispatch a workflow')
    monkeypatch.setattr(orchestrator, 'dispatch_workflow', forbidden)
    response = client.post(f'/api/projects/{project}/generate-all',
        headers=headers['operator'], json={'image_id': image, **fields})
    assert response.status_code == 422


@pytest.mark.parametrize('mode, inputs', [
    ('NEURAL4D', {}),
    ('IMAGE_TO_CAD', {'cad_source': 'NEURAL4D_MESH'}),
])
def test_retired_workflows_cannot_retry_or_initialize_a_gateway(agents_app, monkeypatch, mode, inputs):
    client, app, headers, project, image = agents_app
    with app.state.db.sessions.begin() as session:
        owner = session.get(Project, project).owner_id
        workflow = AgentWorkflow(project_id=project, created_by=owner, mode=mode,
            state='FAILED', input_data=inputs, input_hash=canonical_hash(inputs), model_config={},
            result={'historical_artifacts': 'retained'})
        session.add(workflow)
        session.flush()
        workflow_id = workflow.id
    def forbidden(*args, **kwargs):
        raise AssertionError('Retired workflows must not initialize an AI gateway')
    monkeypatch.setattr(orchestrator, 'ResponsesGateway', forbidden)
    response = client.post(f'/api/v1/agents/workflows/{workflow_id}/retry', headers=headers['operator'])
    assert response.status_code == 409
    with app.state.db.sessions.begin() as session:
        session.get(AgentWorkflow, workflow_id).state = 'QUEUED'
    orchestrator.run_workflow(workflow_id, app.state.db, app.state.settings)
    with app.state.db.sessions() as session:
        workflow = session.get(AgentWorkflow, workflow_id)
        assert workflow.state == 'FAILED'
        assert workflow.error['code'] == 'WORKFLOW_UNSUPPORTED'
        assert workflow.result == {'historical_artifacts': 'retained'}
