"""API, immutable checkpoints, numerical selection and real engineering exports."""
import json
import pytest
from app.agents import orchestrator
from app.cad_optimizer import optimize_cad
from app.database import AgentWorkflow, Revision
from tests.test_agent_workflows import agents_app
from tests.test_engineering_cad import engineering_app, EngineeringGateway, table_plan, launch
from tests.test_cad_optimizer import camera, view_from_plan


def fixture_options(image):
    target = table_plan(720)
    next(v for v in target.values if v.key == 'top_thickness_mm').value = 36
    cam = camera(size=(48, 64)).model_copy(update={
        'intrinsic_matrix': [[.04, 0, 24], [0, .04, 32], [0, 0, 1]]})
    view = view_from_plan(target, cam=cam).model_copy(update={'image_id': image})
    return dict(views=[view.model_dump(mode='json')], parameter_ids=['top_thickness_mm'], max_seconds=60)


def test_optimizer_checkpoint_selected_geometry_export_and_reexport(engineering_app, monkeypatch):
    client, app, headers, project, image = engineering_app
    monkeypatch.setattr('app.cad_optimizer.optimize_cad_isolated', optimize_cad)
    state = launch(engineering_app, output_scope='WHOLE_FURNITURE', optimization=fixture_options(image))
    assert state['status'] == 'REVIEW_REQUIRED', state
    report = state['result']['engineering_cad']['workflow']['cad_optimization'][0]
    assert report['status'] == 'improved', report
    assert report['additional_llm_calls'] == 0
    assert report['final_error'] < report['initial_error']
    # Local fitting adds no AI stages to dimensions -> plan -> visual review.
    assert EngineeringGateway.calls == ['EngineeringDimensions', 'PhotoDesign', 'EngineeringReview']
    with app.state.db.sessions() as session:
        workflow = session.get(AgentWorkflow, state['id'])
        revision = session.get(Revision, state['revision_id'])
        assert 'engineering_optimization_0' in workflow.checkpoints
        dimensions = {v['key']: v['value'] for v in revision.specification['photo_design']['values']}
        assert dimensions['width_mm'] == 720
        assert abs(dimensions['top_thickness_mm'] - 36) < .5
    # Source exports use selected native geometry and contain the optimization audit.
    folder = app.state.settings.storage / state['job_id']
    assert json.loads((folder/'engineering_workflow.json').read_text())['cad_optimization'][0] == report
    import cadquery as cq
    solids = cq.importers.importStep(str(folder/'cabinet.step')).solids().vals()
    top = max(solids, key=lambda solid: solid.BoundingBox().xlen * solid.BoundingBox().ylen)
    assert abs(top.BoundingBox().zlen - 36) < .5
    source_calls = list(EngineeringGateway.calls)
    def forbidden(*a, **k):
        raise AssertionError('A frozen-plan re-export must not rerun numerical optimization')
    monkeypatch.setattr('app.cad_optimizer.optimize_cad_isolated', forbidden)
    exported = launch(engineering_app, key='optimizer-reexport', output_scope='WHOLE_FURNITURE',
        engineering_reference_workflow_id=state['id'])
    assert exported['status'] == 'REVIEW_REQUIRED', exported
    assert EngineeringGateway.calls == source_calls
    assert exported['result']['engineering_cad']['workflow']['cad_optimization'][0] == report


def test_api_rejects_foreign_image_evidence_and_wrong_pixel_resolution(engineering_app):
    client, app, headers, project, image = engineering_app
    options = fixture_options(image)
    options['views'][0]['camera']['image_size'] = [100, 100]
    response = client.post(f'/api/projects/{project}/generate-all', headers=headers['operator'],
        json=dict(image_id=image, optimization=options))
    assert response.status_code == 422
    assert 'resolution' in response.text
    from uuid import uuid4
    options['views'][0]['image_id'] = str(uuid4())
    response = client.post(f'/api/projects/{project}/generate-all', headers=headers['operator'],
        json=dict(image_id=image, optimization=options))
    assert response.status_code == 422
    assert EngineeringGateway.calls == []


def test_explicit_disabled_optimizer_keeps_original_model(engineering_app, monkeypatch):
    client, app, headers, project, image = engineering_app
    options = fixture_options(image)
    options['enabled'] = False
    state = launch(engineering_app, output_scope='WHOLE_FURNITURE', optimization=options)
    assert state['status'] == 'REVIEW_REQUIRED', state
    assert state['result']['engineering_cad']['workflow']['cad_optimization'][0]['status'] == 'disabled'
    values = {v['key']: v['value'] for v in state['result']['specification']['photo_design']['values']}
    assert values['top_thickness_mm'] == 30


def test_numeric_job_does_not_implicitly_pay_for_visual_repair(engineering_app, monkeypatch):
    client, app, headers, project, image = engineering_app
    monkeypatch.setattr('app.cad_optimizer.optimize_cad_isolated', optimize_cad)
    monkeypatch.setattr(EngineeringGateway, 'reject_visual', True)
    state = launch(engineering_app, output_scope='WHOLE_FURNITURE', optimization=fixture_options(image))
    assert state['status'] == 'REVIEW_REQUIRED', state
    assert EngineeringGateway.calls == ['EngineeringDimensions', 'PhotoDesign', 'EngineeringReview']
    assert not state['result']['engineering_cad']['photo_match']['accepted']


def test_optional_structural_fallback_has_one_reserved_call_and_no_paid_followup(engineering_app, monkeypatch):
    client, app, headers, project, image = engineering_app
    monkeypatch.setattr('app.cad_optimizer.optimize_cad_isolated', optimize_cad)
    original = EngineeringGateway.structured
    def structured(self, role, instructions, payload, schema, **kwargs):
        result = original(self, role, instructions, payload, schema, **kwargs)
        if schema.__name__ == 'EngineeringReview':
            result = result.model_copy(update={'accepted': False, 'findings': ['Missing support component requires structural repair']})
        return result
    monkeypatch.setattr(EngineeringGateway, 'structured', structured)
    options = fixture_options(image)
    options['max_structural_llm_calls'] = 1
    state = launch(engineering_app, output_scope='WHOLE_FURNITURE', optimization=options)
    assert state['status'] == 'REVIEW_REQUIRED', state
    assert EngineeringGateway.calls == ['EngineeringDimensions', 'PhotoDesign', 'EngineeringReview', 'EngineeringRepairPatch']
    audit = state['result']['engineering_cad']['workflow']['ai_structural_repair']
    assert audit['reserved_calls'] == 1 and audit['local_optimization_llm_calls'] == 0
    assert not state['result']['engineering_cad']['photo_match']['accepted']


def test_backend_mode_default_and_time_cap_are_frozen_in_workflow(engineering_app, monkeypatch):
    client, app, headers, project, image = engineering_app
    app.state.settings.cad_optimizer_mode = 'economy'
    app.state.settings.cad_optimizer_max_seconds = .5
    monkeypatch.setattr(orchestrator, 'dispatch_workflow', lambda *a, **k: None)
    options = fixture_options(image)
    response = client.post(f'/api/projects/{project}/generate-all', headers=headers['operator'],
        json=dict(image_id=image, optimization=options))
    assert response.status_code == 202, response.text
    with app.state.db.sessions() as session:
        saved = session.get(AgentWorkflow, response.json()['workflow_id']).input_data['optimization']
        assert saved['mode'] == 'economy' and saved['max_seconds'] == .5
