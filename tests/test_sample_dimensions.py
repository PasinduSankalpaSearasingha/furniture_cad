"""Reference matching, dimension precedence and private upload integration."""
from io import BytesIO
from zipfile import ZipFile
import json
from types import SimpleNamespace
from uuid import uuid4

from PIL import Image
import pytest

from app.ai.images import normalized_png
from app.database import AgentWorkflow
from app.agents import orchestrator
from app.agents.photo_agent import specification
from app.pipeline import canonical_hash
from app.sample_dimensions import ROOT, catalogue, match_dimensions, dimension_inputs, workflow_dimensions
from tests.test_agent_workflows import agents_app
from tests.test_engineering_cad import table_plan


REFERENCES = [(sample['id'], reference['file']) for sample in catalogue()[0] for reference in sample['references']]


@pytest.mark.parametrize('sample_id,filename', REFERENCES)
@pytest.mark.parametrize('resized', [False, True])
def test_every_sample_matches_original_and_recompressed_copy(sample_id, filename, resized):
    data = (ROOT / filename).read_bytes()
    if resized:
        with Image.open(BytesIO(data)) as image:
            image.thumbnail((480, 480))
            output = BytesIO();image.save(output, format='JPEG', quality=72)
            data = output.getvalue()
    normalized, _, _ = normalized_png(data, 1_500_000)
    result = match_dimensions(normalized)
    assert result['sample_id'] == sample_id
    assert result['source'] == 'SAMPLE_DRAWING' and result['verified'] is False


@pytest.mark.parametrize('colour', ['white', '#976a44', 'black'])
def test_unrelated_or_low_information_images_do_not_acquire_sample_sizes(colour):
    output = BytesIO();Image.new('RGB', (800, 600), colour).save(output, format='PNG')
    assert match_dimensions(output.getvalue())['status'] == 'NO_MATCH'


def test_drawing_dimensions_keep_base_sizes_and_height_variants_distinct():
    samples = {s['id']: s for s in catalogue()[0]}
    assert samples['table-b']['dimensions'] == dict(width_mm=2200, depth_mm=950, height_mm=750)
    assert samples['bed']['dimensions']['depth_mm'] == 2010
    assert {v['height_mm'] for v in samples['shoe-cabinet']['variants']} == {840, 858}


def test_user_override_and_drawing_provenance_survive_specification():
    match = match_dimensions(normalized_png((ROOT / 'table-a-1.jpg').read_bytes(), 1_500_000)[0])
    known, sources = dimension_inputs(match, [dict(key='width_mm', value=3000)])
    plan = table_plan()
    for entry in plan.values:
        entry.value = next((v['value'] for v in known if v['key'] == entry.key), entry.value)
    _, values = specification(plan, dict(known_values=known, dimension_sources=sources), 'Reference test')
    assert values['width_mm']['source'] == 'USER_PROVIDED' and values['width_mm']['value'] == 3000
    assert values['depth_mm']['source'] == 'SAMPLE_DRAWING' and values['depth_mm']['value'] == 1100
    assert values['height_mm']['drawing']['page'] == 1 and not values['height_mm']['verified']
    assert values['top_thickness_mm']['source'] == 'AI_ESTIMATED'


def test_upload_matching_constraints_status_and_authorized_drawing(agents_app, monkeypatch):
    client, app, headers, project, _ = agents_app
    monkeypatch.setenv('OPENAI_API_KEY', 'test-no-paid-requests')
    monkeypatch.setattr(orchestrator, 'dispatch_workflow', lambda *args: None)
    response = client.post(f'/api/projects/{project}/upload-image', headers=headers['operator'],
        files={'file': ('arbitrary-name.jpg', (ROOT / 'table-a-1.jpg').read_bytes(), 'image/jpeg')})
    assert response.status_code == 201, response.text
    image = response.json()
    assert image['provenance']['dimension_match']['sample_id'] == 'table-a'
    request = dict(image_id=image['id'], known_values=[dict(key='width_mm', value=2900)], idempotency_key='sample-test')
    generated = client.post(f'/api/projects/{project}/generate-all', headers=headers['operator'], json=request)
    assert generated.status_code == 202, generated.text
    state = client.get(generated.json()['status_url'], headers=headers['operator']).json()
    assert state['dimensions']['values']['width_mm'] == dict(value=2900, source='USER_PROVIDED', verified=False)
    assert state['dimensions']['values']['depth_mm']['value'] == 1100
    assert state['dimensions']['values']['depth_mm']['source'] == 'SAMPLE_DRAWING'
    with app.state.db.sessions() as session:
        workflow = session.get(AgentWorkflow, state['id'])
        assert canonical_hash(workflow.input_data) == workflow.input_hash
        assert workflow.input_data['user_known_values'] == request['known_values']
    again = client.post(f'/api/projects/{project}/generate-all', headers=headers['operator'], json=request)
    assert again.json()['workflow_id'] == state['id']
    drawing = image['provenance']['dimension_match']['drawing_url']
    assert client.get(drawing, headers=headers['operator']).content.startswith(b'%PDF')
    assert client.get(drawing).status_code == 401
    assert client.get('/api/furniture-samples/missing/drawing', headers=headers['operator']).status_code == 404
    assert client.post(f'/api/projects/{uuid4()}/upload-image', headers=headers['operator'],
        files={'file': ('test.jpg', (ROOT / 'table-a-1.jpg').read_bytes(), 'image/jpeg')}).status_code == 404


def test_unmatched_upload_retains_estimation_path(agents_app, monkeypatch):
    client, app, headers, project, image = agents_app
    monkeypatch.setenv('OPENAI_API_KEY', 'test-no-paid-requests')
    monkeypatch.setattr(orchestrator, 'dispatch_workflow', lambda *args: None)
    response = client.post(f'/api/projects/{project}/generate-all', headers=headers['operator'], json=dict(image_id=image))
    state = client.get(response.json()['status_url'], headers=headers['operator']).json()
    assert state['dimensions']['match']['status'] == 'NO_MATCH'
    assert state['dimensions']['values'] == {}


def test_intermediate_estimates_do_not_replace_fixed_drawing_sizes():
    workflow = SimpleNamespace(input_data=dict(known_values=[dict(key='width_mm', value=1350)],
        dimension_sources=dict(width_mm=dict(source='SAMPLE_DRAWING', verified=False))), result=None,
        checkpoints=dict(engineering_dimensions=dict(payload=dict(values=[dict(key='width_mm', value=1000),
            dict(key='depth_mm', value=400)]))))
    result = workflow_dimensions(workflow)
    assert result['values']['width_mm']['value'] == 1350
    assert result['values']['depth_mm']['source'] == 'AI_ESTIMATED'


def test_matched_dimensions_drive_actual_cad_and_survive_offline_reexport(agents_app, monkeypatch):
    from tests import test_engineering_cad as engineering_tests
    from tests.test_engineering_cad import EngineeringGateway
    original = engineering_tests.table_plan

    class MatchedGateway(EngineeringGateway):
        def structured(self, role, instructions, payload, schema, **options):
            known = {v['key']: v['value'] for v in payload.get('known_values', [])}
            def plan(width=700):
                candidate = original(width)
                for value in candidate.values:
                    value.value = known.get(value.key, value.value)
                return candidate
            with monkeypatch.context() as scoped:
                scoped.setattr(engineering_tests, 'table_plan', plan)
                return super().structured(role, instructions, payload, schema, **options)

    monkeypatch.setenv('OPENAI_API_KEY', 'test-no-paid-requests')
    monkeypatch.setattr(orchestrator, 'ResponsesGateway', MatchedGateway)
    client, app, headers, project, _ = agents_app
    image = client.post(f'/api/projects/{project}/upload-image', headers=headers['operator'],
        files={'file': ('table.jpg', (ROOT / 'table-a-1.jpg').read_bytes(), 'image/jpeg')}).json()
    response = client.post(f'/api/projects/{project}/generate-all', headers=headers['operator'],
        json=dict(image_id=image['id'], known_values=[dict(key='width_mm', value=2900)]))
    state = client.get(response.json()['status_url'], headers=headers['operator']).json()
    assert state['status'] == 'REVIEW_REQUIRED', state.get('error')
    proof = state['result']['engineering_cad']['cad_export_agreement']
    dimensions = proof['geometry_checks']['supplied_dimensions']
    assert {d['parameter']: d['actual_mm'] for d in dimensions} == pytest.approx(dict(width_mm=2900, depth_mm=1100, height_mm=750))
    delivery = state['result']['delivery']
    package = client.get(delivery['download_base'] + '/furniture_complete_package.zip', headers=headers['operator'])
    with ZipFile(BytesIO(package.content)) as archive:
        assert 'cad/all_dimensions_REVIEW.dxf' in archive.namelist()
        assert 'accuracy_report.json' not in archive.namelist()
    report = state['result']['engineering_cad']['workflow']
    assert report['dimension_match']['sample_id'] == 'table-a'
    assert report['value_provenance']['depth_mm']['source'] == 'SAMPLE_DRAWING'
    monkeypatch.delenv('OPENAI_API_KEY')
    def forbidden(*args, **kwargs):raise AssertionError('Offline reexport cannot call AI')
    monkeypatch.setattr(orchestrator, 'ResponsesGateway', forbidden)
    response = client.post(f'/api/projects/{project}/generate-all', headers=headers['operator'],
        json=dict(image_id=image['id'], engineering_reference_workflow_id=state['id']))
    exported = client.get(response.json()['status_url'], headers=headers['operator']).json()
    assert exported['status'] == 'REVIEW_REQUIRED', exported.get('error')
    assert exported['dimensions']['match'] == state['dimensions']['match']
    assert exported['result']['value_provenance']['depth_mm'] == state['result']['value_provenance']['depth_mm']
