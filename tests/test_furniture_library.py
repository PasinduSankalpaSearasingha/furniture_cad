"""Real template geometry and the explicitly unverified AI-to-CAD lifecycle."""
import json
from copy import deepcopy
import cadquery as cq
import trimesh
import pytest
from pydantic import ValidationError
from sqlalchemy import select
from app.models import CabinetSpec, FurnitureParameters
from app.furniture_catalog import catalog, default_spec
from app.furniture_cad import create_furniture
from app.geometry import exact_bounds
from app.validation_engine import validate_build
from app.pipeline import build_package, canonical_hash
from app.agents.furniture_agent import FurnitureEstimate, specification
from app.ai.openai_client import AIServiceError
from app.database import AgentWorkflow, Revision, Job
from app.estimated_workflow import validate_estimated_revision
from tests.test_agent_workflows import agents_app
from examples.ai_demo.mock_gateway import MockGateway, fixtures


@pytest.mark.parametrize('identifier',[r['id'] for r in catalog()])
def test_every_catalog_template_builds_valid_registered_review_solids(identifier):
    spec=CabinetSpec.model_validate(default_spec(identifier))
    build=create_furniture(spec)
    assert len(build.registry)==len(build.parts)==len(build.shapes)>=3
    bounds=exact_bounds(cq.Compound.makeCompound([v.val() for v in build.shapes.values()]))
    assert (bounds.xmin,bounds.ymin,bounds.zmin)==pytest.approx((0,0,0),abs=1e-5)
    assert (bounds.xlen,bounds.ylen,bounds.zlen)==pytest.approx((spec.width_mm,spec.depth_mm,spec.height_mm),abs=1e-5)
    report=validate_build(spec,build)
    assert not [f for f in report.findings if f.severity=='ERROR']
    assert report.has_blockers and not report.approved_for_manufacturing
    for part in build.registry:
        assert not part.blank_verified
        local=exact_bounds(build.local_shapes[part.part_id].val())
        assert (local.xlen,local.ylen,local.zlen)==pytest.approx((part.length_mm,part.width_mm,part.thickness_mm))


def estimate_fixture():
    row=default_spec('armchair')
    row['furniture_parameters']['leg_curve_mm']=18
    return dict(template_id='armchair',width_mm=620,depth_mm=600,height_mm=820,material='solid_wood',
        parameters=row['furniture_parameters'],summary='Synthetic curved chair test estimate',
        assumptions=['SYNTHETIC TEST: no physical dimensions'],unrepresented_features=['Hidden joints'],
        common_default_fields=['width_mm','depth_mm','height_mm','parameters.leg_curve_mm'])


def test_curves_are_real_geometry_and_exported_in_the_complete_package(tmp_path):
    spec,provenance=specification(FurnitureEstimate(**estimate_fixture()),{},'Curved chair')
    build=create_furniture(spec)
    rail=next(p for p in build.parts if p.label=='Curved back rail')
    assert any(face.geomType() not in ('PLANE',) for face in build.shapes[rail.part_id].val().Faces())
    result=build_package(spec,tmp_path/'chair')
    reopened=cq.importers.importStep(str(tmp_path/'chair/cabinet.step'))
    assert len(reopened.solids().vals())==len(build.parts)==result['parts']
    manifest=json.loads((tmp_path/'chair/manifest.json').read_text())
    assert manifest['template_id']=='armchair'
    assert {'furniture_estimates.json','cad_preview.png','cabinet.glb','cabinet.stl','drawings_REVIEW.pdf'}<=set(manifest['files'])
    assert provenance['parameters.leg_curve_mm']['source']=='AI_ESTIMATED'
    glb=trimesh.load(tmp_path/'chair/cabinet.glb',force='scene')
    assert {part.part_id for part in build.parts}<=set(glb.graph.nodes)
    assert glb.extents==pytest.approx((.620,.820,.600),abs=.001)
    stl=trimesh.load(tmp_path/'chair/cabinet.stl')
    assert stl.is_watertight
    assert stl.extents==pytest.approx((620,600,820),abs=.1)


def test_supplied_overall_dimensions_are_preserved_without_claiming_verification():
    estimate=FurnitureEstimate(**estimate_fixture())
    spec,sources=specification(estimate,{'dimensions':{'width_mm':650,'height_mm':None}},'Chair')
    assert spec.width_mm==650 and spec.height_mm==820
    assert sources['width_mm']['source']=='USER_PROVIDED'
    assert sources['height_mm']['source']=='COMMON_DEFAULT_ESTIMATE'
    assert all(v['verified'] is False for v in sources.values())
    assert not spec.measured_dimensions_confirmed and spec.dimension_provenance=='estimated'


@pytest.mark.parametrize('changes',[
    {'measured_dimensions_confirmed':True}, {'dimension_provenance':'physically_verified'},
    {'family':'table'}, {'furniture_template':'arbitrary_code'}, {'include_glazed_door':True},
    {'height_mm':300}, {'width_mm':float('nan')},
])
def test_invalid_or_claimed_verified_library_inputs_are_refused(changes):
    data=default_spec('armchair');data.update(changes)
    with pytest.raises(ValidationError): CabinetSpec.model_validate(data)


def test_mismatched_ai_template_is_refused():
    with pytest.raises(AIServiceError,match='outside'):
        specification(FurnitureEstimate(**estimate_fixture()),{'template_id':'writing_desk'},'Chair')


def start_estimate(env,**changes):
    client,app,h,p,image=env
    MockGateway.responses={**fixtures(),'FurnitureEstimate':estimate_fixture()}
    response=client.post('/api/v1/ai/estimate-furniture',headers=h['operator'],
        json=dict(project_id=p,image_ids=[image],template_id='armchair',**changes))
    assert response.status_code==202,response.text
    return client.get(response.json()['status_url'],headers=h['operator']).json()


def test_ai_estimate_automatically_builds_actual_cad_and_remains_unverified(agents_app):
    client,app,h,p,image=agents_app
    status=start_estimate(agents_app)
    assert status['status']=='REVIEW_REQUIRED',status
    assert MockGateway.calls==['vision','design','review']
    rid,jid=status['revision_id'],status['job_id']
    job=client.get(f'/api/v1/jobs/{jid}',headers=h['operator']).json()
    assert job['status']=='SUCCEEDED'
    assert status['result']['deterministic_validation']['approved_for_manufacturing'] is False
    files=client.get(f'/api/v1/jobs/{jid}/files',headers=h['operator']).json()['files']
    assert 'furniture_estimates.json' in [f['name'] for f in files]
    components=client.get(f'/api/v1/jobs/{jid}/components',headers=h['operator']).json()['components']
    assert len(components)==9
    with app.state.db.sessions() as session:
        revision=session.get(Revision,rid)
        assert revision.verified_by is None and revision.verified_spec_hash is None
        assert validate_estimated_revision(revision,session).specification.family=='chair'
    assert client.post(f'/api/v1/revisions/{rid}/verify',headers=h['designer']).status_code==409
    assert client.post(f'/api/v1/designs/{status["id"]}/generate-cad',headers=h['operator']).status_code==409


def test_estimate_idempotency_does_not_repeat_paid_calls_or_create_revisions(agents_app,monkeypatch):
    monkeypatch.setattr("app.jobs.dispatch",lambda *args:None)
    first=start_estimate(agents_app,idempotency_key='same-estimate')
    calls=list(MockGateway.calls)
    second=start_estimate(agents_app,idempotency_key='same-estimate')
    assert first['id']==second['id'] and first['job_id']==second['job_id']
    assert MockGateway.calls==calls


def test_tampered_estimate_checkpoint_cannot_authorize_regeneration(agents_app,monkeypatch):
    monkeypatch.setattr("app.jobs.dispatch",lambda *args:None)
    status=start_estimate(agents_app)
    client,app,h,p,image=agents_app
    with app.state.db.sessions.begin() as session:
        w=session.get(AgentWorkflow,status['id'])
        cp=deepcopy(w.checkpoints);cp['furniture_estimate']['payload']['width_mm']=999
        w.checkpoints=cp
        w.state='CAD_FAILED'
    response=client.post(f'/api/v1/revisions/{status["revision_id"]}/jobs',headers=h['operator'])
    assert response.status_code==409
    assert 'ESTIMATE_INTEGRITY_FAILED' in response.text


def test_catalog_and_estimate_validation_are_authenticated(agents_app):
    client,app,h,p,image=agents_app
    assert client.get('/api/v1/furniture/templates').status_code==401
    assert len(client.get('/api/v1/furniture/templates',headers=h['operator']).json()['templates'])==43
    response=client.post('/api/v1/ai/estimate-furniture',headers=h['operator'],json=dict(project_id=p,template_id='not-real'))
    assert response.status_code==422 and MockGateway.calls==[]


def test_estimation_reuses_source_observations_without_another_vision_call(agents_app,monkeypatch):
    client,app,h,p,image=agents_app
    monkeypatch.setattr('app.jobs.dispatch',lambda *args:None)
    source=client.post('/api/v1/ai/analyze-image',headers=h['operator'],json=dict(project_id=p,image_ids=[image]))
    assert source.status_code==202
    MockGateway.calls=[]
    MockGateway.responses={**fixtures(),'FurnitureEstimate':estimate_fixture()}
    response=client.post('/api/v1/ai/estimate-furniture',headers=h['operator'],json=dict(
        project_id=p,source_workflow_id=source.json()['workflow_id'],template_id='armchair'))
    status=client.get(response.json()['status_url'],headers=h['operator']).json()
    assert status['status']=='CAD_QUEUED',status
    assert MockGateway.calls==['design']
    assert 'vision' in status['completed_stages'] and status['result']['vision']
