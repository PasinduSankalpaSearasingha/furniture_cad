import json
from copy import deepcopy
from pathlib import Path
import cadquery as cq
import pytest
from pydantic import ValidationError
from openai.lib._pydantic import to_strict_json_schema
from app.photo_schema import number,PhotoDesign
from app.photo_schema import PhotoPart
from app.agents.photo_agent import specification,preflight
from app.photo_cad import create_photo_furniture
from app.pipeline import build_package
from app.geometry import exact_bounds
from app.validation_engine import validate_build
from app.database import Revision,AgentWorkflow
from app.estimated_workflow import validate_estimated_revision
from app.ai.openai_client import AIServiceError
from tests.test_agent_workflows import agents_app
from tests.photo_fixtures import photo_plan_fixture,analysis_fixture
from examples.ai_demo.mock_gateway import MockGateway,fixtures


@pytest.mark.parametrize('value',["__import__('os').getcwd()",'x.__class__','x[0]','2**999999','max(x,5)',"open('private')",'[x for x in y]','unknown+1','1/0'])
def test_geometry_expressions_cannot_execute_code(value):
    with pytest.raises((ValueError,SyntaxError,ZeroDivisionError)): number(value,{'x':5})


def test_dimension_ratios_allow_bounded_intermediate_products_but_limit_final_coordinates():
    assert number('seat_width_mm*255/530',{'seat_width_mm':530})==255
    with pytest.raises(ValueError,match='exceeds limits'): number('530*255',{})
    with pytest.raises(ValueError,match='intermediate'): number('100000000*100000000/100000000',{})


def test_geometry_uses_supplied_values_and_missing_inputs_remain_estimated():
    plan=photo_plan_fixture(width_mm=700)
    spec,provenance=specification(plan,{'known_values':[{'key':'width_mm','value':700}]},'Photo chair')
    assert spec.width_mm==700 and spec.furniture_template is None
    assert spec.family=='photo' and provenance['width_mm']['source']=='USER_PROVIDED'
    assert provenance['height_mm']['source']=='AI_ESTIMATED'
    build=preflight(spec,{'width_mm'})
    bounds=exact_bounds(cq.Compound.makeCompound([s.val() for s in build.shapes.values()]))
    assert bounds.xlen==pytest.approx(700,abs=.2)
    assert not [f for f in validate_build(spec,build).findings if f.severity=='ERROR']


def test_no_supplied_values_uses_estimates_without_catalog_selection():
    spec,provenance=specification(photo_plan_fixture(),{},'Photo only')
    assert all(v['source']=='AI_ESTIMATED' and v['verified'] is False for v in provenance.values())
    assert 'furniture_template' not in spec.model_dump(mode='json')


def test_changed_or_ignored_inputs_are_rejected():
    with pytest.raises(AIServiceError,match='changed'):
        specification(photo_plan_fixture(),{'known_values':[{'key':'width_mm','value':900}]},'Bad')
    plan=photo_plan_fixture()
    data=plan.model_dump(mode='json');data['values'].append(dict(key='unused_mm',value=30,explanation='Test'))
    data['parts'][0]['evidence']='unused_mm'
    with pytest.raises(AIServiceError,match='drive'):
        specification(PhotoDesign(**data),{'known_values':[{'key':'unused_mm','value':30}]},'Ignored')


def test_photo_schema_uses_supported_array_schema_and_bounded_geometry():
    schema=json.dumps(to_strict_json_schema(PhotoDesign))
    assert 'prefixItems' not in schema and 'additionalProperties' in schema
    data=photo_plan_fixture().model_dump(mode='json')
    data['parts'][0]['size'][0]='__import__("os")'
    with pytest.raises(ValidationError): PhotoDesign(**data)


def setup_photo_mock():
    MockGateway.responses={**fixtures(),'PhotoAnalysis':analysis_fixture(),'PhotoDesign':photo_plan_fixture(width_mm=700).model_dump(mode='json')}


def test_photo_analysis_provides_optional_matching_inputs_and_no_cad(agents_app):
    client,app,h,p,image=agents_app;setup_photo_mock()
    response=client.post('/api/v1/ai/analyze-furniture-photo',headers=h['operator'],json=dict(project_id=p,image_ids=[image]))
    assert response.status_code==202
    status=client.get(response.json()['status_url'],headers=h['operator']).json()
    assert status['status']=='SUCCEEDED' and status['revision_id'] is None
    assert {'seat_height_mm','width_mm'}<={v['key'] for v in status['result']['matching_inputs']}
    assert MockGateway.calls==['vision']


def test_image_plan_generates_real_cad_without_any_template_or_default_picker(agents_app):
    client,app,h,p,image=agents_app;setup_photo_mock()
    analyzed=client.post('/api/v1/ai/analyze-furniture-photo',headers=h['operator'],json=dict(project_id=p,image_ids=[image])).json()
    response=client.post('/api/v1/ai/reconstruct-furniture',headers=h['operator'],json=dict(
        project_id=p,source_workflow_id=analyzed['workflow_id'],known_values=[dict(key='width_mm',value=700)]))
    assert response.status_code==202
    status=client.get(response.json()['status_url'],headers=h['operator']).json()
    assert status['status']=='REVIEW_REQUIRED',status
    assert MockGateway.calls==['vision','design','review']
    assert status['result']['exact_reconstruction_verified'] is False
    spec=status['result']['specification']
    assert spec['family']=='photo' and 'furniture_template' not in spec
    with app.state.db.sessions() as session:
        revision=session.get(Revision,status['revision_id'])
        context=validate_estimated_revision(revision,session)
        assert context.specification.width_mm==700 and not revision.verified_by
    files=client.get(f'/api/v1/jobs/{status["job_id"]}/files',headers=h['operator']).json()['files']
    assert 'photo_reconstruction.json' in [f['name'] for f in files]
    output=app.state.settings.storage/status['job_id']
    assert len(cq.importers.importStep(str(output/'cabinet.step')).solids().vals())==7


def test_reconstruction_requires_private_images_and_refuses_template_field(agents_app):
    client,app,h,p,image=agents_app
    assert client.post('/api/v1/ai/reconstruct-furniture',headers=h['operator'],json=dict(project_id=p)).status_code==422
    assert client.post('/api/v1/ai/reconstruct-furniture',headers=h['operator'],json=dict(project_id=p,image_ids=[image],template_id='armchair')).status_code==422
    assert client.post('/api/v1/ai/reconstruct-furniture',json=dict(project_id=p,image_ids=[image])).status_code==401


def test_photo_ui_has_no_template_selection():
    text=Path('frontend/src/main.jsx').read_text(encoding='utf-8')
    assert 'Furniture templates' not in text and 'reference-template' not in text and 'templateOptions' not in text
    assert 'Upload furniture photo' in text and '/generate-all' in text and '/edit-cad' in text


def test_silent_stl_kernel_failure_preserves_component_mesh_export(tmp_path,monkeypatch):
    from app.cad import export_cad
    spec,_=specification(photo_plan_fixture(),{},'Export regression')
    build=create_photo_furniture(spec)
    original=cq.exporters.export
    def no_stl(shape,filename,*args,**kwargs):
        if str(filename).endswith('.stl'): return None
        return original(shape,filename,*args,**kwargs)
    monkeypatch.setattr(cq.exporters,'export',no_stl)
    export_cad(build,tmp_path)
    report=json.loads((tmp_path/'mesh_export_review.json').read_text())
    assert report['stl_mode']=='COMPONENT_SHELLS' and not report['printable_union_verified']
    import trimesh
    mesh=trimesh.load(tmp_path/'cabinet.stl')
    assert len(mesh.faces)>0 and len(cq.importers.importStep(str(tmp_path/'cabinet.step')).solids().vals())==7


@pytest.mark.parametrize('geometry',['box','cylinder','ellipsoid','outline_panel','loft','round_sweep','ellipse_sweep','rect_sweep'])
def test_photo_shape_primitives_are_valid_and_have_matching_local_registry(geometry):
    data=dict(part_id='F001',label='Synthetic shape',evidence='Synthetic geometry test',geometry=geometry,
        material='foam' if geometry=='ellipsoid' else 'solid_wood',color_rgb=[.5,.4,.3],
        size=[30,20,100],position=[200,200,100],rotation_deg=[10,15,20])
    if geometry=='outline_panel': data.update(outline=[[-15,-10],[15,-10],[15,10],[-15,10]])
    if geometry=='loft': data.update(sections=[dict(center=[0,0,0],width=20,depth=15,profile='ellipse'),dict(center=[10,5,100],width=30,depth=20,profile='ellipse')])
    if geometry.endswith('sweep'): data.update(path=[[0,0,0],[20,0,30],[30,30,60],[0,50,100]],curve_mode='bezier')
    plan=photo_plan_fixture();plan.parts=[PhotoPart(**data)]
    spec,_=specification(plan,{},'Primitive test')
    build=create_photo_furniture(spec)
    solid=build.shapes['F001'].val();part=build.parts[0]
    assert solid.isValid() and solid.Volume()>0 and len(solid.Solids())==1
    local=exact_bounds(build.local_shapes['F001'].val())
    assert (local.xlen,local.ylen,local.zlen)==pytest.approx((part.length_mm,part.width_mm,part.thickness_mm))
