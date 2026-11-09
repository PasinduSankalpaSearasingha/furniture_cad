"""Estimated photograph studies are real CAD, never verified manufacturing input."""
import json
import cadquery as cq
import pytest
from pydantic import ValidationError
from app.models import CabinetSpec,Material
from app.cad import create_cabinet
from app.display_cad import display_component_ids
from app.geometry import exact_bounds
from app.validation_engine import validate_build
from app.pipeline import build_package,file_hash


def display_spec(**changes):
    values = dict(project_name='Estimated decorative image study',
        width_mm=650,depth_mm=430,height_mm=1900,carcass_material='solid_wood',panel_thickness_mm=18,
        back_thickness_mm=8,include_glazed_door=True,door_frame_width_mm=55,door_frame_depth_mm=24,
        glass_thickness_mm=6,glass_edge_clearance_mm=2,door_standoff_mm=3,
        dimension_provenance='estimated',display_layout={})
    values.update(changes)
    return CabinetSpec.model_validate(values)


def test_image_study_geometry_matches_envelope_and_registry():
    spec = display_spec()
    build = create_cabinet(spec)
    assert {p.part_id for p in build.registry}==display_component_ids(spec)
    assert len(build.registry)==55
    assert sum(p.material==Material.glass for p in build.registry)==4
    envelope = exact_bounds(cq.Compound.makeCompound([shape.val() for shape in build.shapes.values()]))
    assert (envelope.xlen,envelope.ylen,envelope.zlen)==pytest.approx((650,430,1900))
    for part in build.registry:
        assert build.shapes[part.part_id].val().isValid()
        assert len(build.shapes[part.part_id].val().Solids())==1
        local = exact_bounds(build.local_shapes[part.part_id].val())
        assert (local.xlen,local.ylen,local.zlen)==pytest.approx((part.length_mm,part.width_mm,part.thickness_mm))
    report = validate_build(spec,build)
    codes = {f.code for f in report.findings}
    assert not ({'UNEXPECTED_INTERSECTION','PART_DIMENSION_MISMATCH','INVALID_CAD_SOLID','COMPONENT_REGISTRY_MISMATCH'} & codes)
    assert {'IMAGE_STUDY_REVIEW_REQUIRED','MEASUREMENTS_REQUIRED','GLASS_RETENTION_GEOMETRY_REQUIRED'} <= codes
    assert report.has_blockers and report.approved_for_manufacturing is False
    build.parts.pop()
    assert 'COMPONENT_REGISTRY_MISMATCH' in {f.code for f in validate_build(spec,build).findings}


@pytest.mark.parametrize('changes',[
    {'measured_dimensions_confirmed':True},{'dimension_provenance':'physically_verified'},
    {'height_mm':1000},{'width_mm':330},{'depth_mm':220},{'include_glazed_door':False}])
def test_image_study_rejects_impossible_proportions_and_claimed_verification(changes):
    values = display_spec().model_dump(mode='json')
    values.update(changes)
    with pytest.raises(ValidationError): CabinetSpec.model_validate(values)


def test_absent_extension_does_not_change_legacy_specification_hash_input():
    from .test_cabinet import spec
    basic = spec()
    assert 'display_layout' not in basic.model_dump(mode='json')
    assert 'display_layout' not in json.loads(basic.model_dump_json())


def test_image_study_exports_reopen_and_remain_estimated(tmp_path):
    import trimesh
    import ezdxf
    from zipfile import ZipFile
    output = tmp_path/'image-study'
    result = build_package(display_spec(),output)
    reopened = cq.importers.importStep(str(output/'cabinet.step'))
    assert len(reopened.solids().vals())==55
    scene = trimesh.load(output/'cabinet.glb',force='scene')
    # OCCT emits a mesh primitive per face; stable component names group them.
    assert {name.split('_')[0] for name in scene.geometry}==display_component_ids(display_spec())
    assert scene.extents==pytest.approx((.650,1.900,.430),abs=1e-5)
    mesh = trimesh.load(output/'cabinet.stl',force='mesh')
    assert mesh.is_watertight
    assert not ezdxf.readfile(output/'front_elevation_REVIEW.dxf').audit().has_errors
    manifest = json.loads((output/'manifest.json').read_text())
    assert manifest['template_id']=='two_section_display.review.v1'
    with ZipFile(output/'design_review_package.zip') as archive:
        assert set(archive.namelist())==set(manifest['files'])|{'manifest.json'}
        for name,digest in manifest['files'].items(): assert file_hash(output/name)==digest
    report = json.loads((output/'reference_review.json').read_text())
    assert report['status']=='ESTIMATED_REVIEW_ONLY' and not report['approved_for_manufacturing']
    assert result['parts']==55 and result['validation']['has_blockers']


def test_estimated_layout_cannot_replace_a_verified_ai_template(agents_app):
    from .test_agent_workflows import begin,measured_spec
    client,app,headers,project,image = agents_app
    workflow = begin(agents_app)
    data = measured_spec(client,headers)
    data['specification'].update(display_layout={},measured_dimensions_confirmed=False,dimension_provenance='estimated')
    response = client.post(f'/api/v1/designs/{workflow}/verify',headers=headers['designer'],json=data)
    assert response.status_code==409,response.text
    assert 'image-study' in response.json()['error']['message']


from .test_agent_workflows import agents_app
