"""Synthetic dimensions/profiles test software behavior, not factory qualification."""
import json
import math
import pytest
import cadquery as cq
from pydantic import ValidationError
from app.models import CabinetSpec, MaterialProfile, Part
from app.cad import create_cabinet
from app.manufacturing import schedules_from_registry
from app.validation_engine import validate_build
from app.registry import PartsRegistry
from .test_cabinet import spec


@pytest.mark.parametrize('width,height,depth',[(301,451,201),(700,1800,450),(3000,3000,1500)])
def test_parameter_propagation_and_part_local_coordinates(width,height,depth):
    s = spec(width_mm=width,height_mm=height,depth_mm=depth)
    b = create_cabinet(s)
    assert b.registry['P003'].length_mm == width-36
    assert b.registry['P002'].center_xyz_mm[0] == width-9
    for p in b.registry:
        world = b.shapes[p.part_id].val().BoundingBox()
        local = b.local_shapes[p.part_id].val().BoundingBox()
        assert (world.xlen,world.ylen,world.zlen) == pytest.approx(p.size_xyz_mm)
        assert (local.xlen,local.ylen,local.zlen) == pytest.approx((p.length_mm,p.width_mm,p.thickness_mm))
        plane = cq.Plane(origin=p.center_xyz_mm,xDir=p.length_axis,normal=p.thickness_axis)
        rebuilt = b.local_shapes[p.part_id].val().transformShape(plane.rG)
        assert rebuilt.intersect(b.shapes[p.part_id].val()).Volume() == pytest.approx(b.shapes[p.part_id].val().Volume())


@pytest.mark.parametrize('change',[{'width_mm':300},{'width_mm':3001},{'height_mm':450},
    {'depth_mm':200},{'width_mm':float('nan')},{'height_mm':float('inf')},
    {'panel_thickness_mm':51},{'unknown_dimension':123},
    {'material_assignments':{'carcass':'missing'}}])
def test_invalid_inputs(change):
    with pytest.raises(ValidationError): spec(**change)


def test_blank_allowances_and_material_bom_are_not_world_bounding_boxes():
    profile = MaterialProfile(material_id='TEST-P18',name='Synthetic fixture',material='plywood',actual_thickness_mm=18,
        approved=True,evidence_reference='TEST ONLY - NOT A FACTORY APPROVAL',blank_allowance_mm=(4,2,0),
        stock_size_mm=(2440,1220),grain_direction='length',edge_treatment='test')
    s = spec(material_profiles=[profile],material_assignments={'carcass':profile.material_id})
    b = create_cabinet(s)
    p = b.registry['P001']
    assert (p.blank_length_mm,p.blank_width_mm,p.blank_thickness_mm) == (1804,452,18)
    assert p.size_xyz_mm == (18,450,1800)
    data = schedules_from_registry(b,s)
    assert {r['part_id'] for r in data['parts']} == {p.part_id for p in b.registry}
    assert sum(r['parts_count'] for r in data['bom']) == 5
    assert sum(r['blank_volume_m3'] for r in data['bom']) == pytest.approx(
        sum(p.blank_length_mm*p.blank_width_mm*p.blank_thickness_mm for p in b.registry)/1e9)
    assert data['estimate']['total'] is None
    assert all(r['pricing_status']=='PRICE_REQUIRED' for r in data['bom'])
    data['parts'].pop()
    assert 'CUT_LIST_REGISTRY_MISMATCH' in {f.code for f in validate_build(s,b,data).findings}


def joint(approved=True,origin=(18,100,9),depths=(8,12),kind='dowel'):
    return dict(joint_id='TEST-J1',part_ids=('P001','P004'),origin_xyz_mm=origin,direction_xyz=(-1,0,0),
        profile=dict(profile_id='TEST ONLY',joint_type=kind,compatible_materials=['plywood'],approved=approved,
                     evidence_reference='SYNTHETIC SOFTWARE FIXTURE',diameter_mm=6,depths_mm=depths,
                     clearance_mm=0,tolerance_mm=0.1))


def test_matching_joint_features_remove_exact_volumes():
    b = create_cabinet(spec(joints=[joint()]))
    assert not b.joint_issues
    assert len(b.operations)==2
    assert b.operations[0].origin_xyz_mm == b.operations[1].origin_xyz_mm
    assert b.shapes['P001'].val().Volume() == pytest.approx(18*450*1800-math.pi*9*8)
    assert b.shapes['P004'].val().Volume() == pytest.approx(664*450*18-math.pi*9*12)
    assert all(o.status=='PROFILE_APPROVED_GEOMETRY_ONLY' for o in b.operations)


@pytest.mark.parametrize('joint_request,code',[(joint(False),'JOINT_APPROVAL_REQUIRED'),
    (joint(origin=(19,100,9)),'JOINT_ALIGNMENT_OR_BREAKTHROUGH'),
    (joint(depths=(19,12)),'JOINT_ALIGNMENT_OR_BREAKTHROUGH'),
    (joint(kind='dado'),'JOINT_GEOMETRY_NOT_IMPLEMENTED')])
def test_joint_refusal_is_atomic(joint_request,code):
    b = create_cabinet(spec(joints=[joint_request]))
    assert not b.operations
    assert b.shapes['P001'].val().Volume()==pytest.approx(18*450*1800)
    assert code in {i['code'] for i in b.joint_issues}


def test_glass_material_and_demo_remain_blocked():
    s = spec(include_glazed_door=True,joint_type='dowel_demo',measured_dimensions_confirmed=True)
    b = create_cabinet(s)
    report = validate_build(s,b)
    codes = {f.code for f in report.findings}
    assert {'GLASS_SPECIFICATION_REQUIRED','HARDWARE_SPECIFICATION_REQUIRED','MATERIAL_SPECIFICATION_REQUIRED',
            'GLASS_RETENTION_GEOMETRY_REQUIRED','DEMO_JOINERY_NOT_APPROVED','MEASUREMENTS_REQUIRED'} <= codes
    assert report.has_blockers and not report.approved_for_manufacturing
    assert 'UNEXPECTED_INTERSECTION' not in codes


def test_detects_registry_duplicate_and_geometry_drift():
    s = spec()
    b = create_cabinet(s)
    with pytest.raises(ValueError): b.registry.add(b.parts[0])
    b.shapes['P001'] = b.shapes['P001'].translate((20,0,0))
    codes = {f.code for f in validate_build(s,b).findings}
    assert {'ASSEMBLY_ALIGNMENT_ERROR','UNEXPECTED_INTERSECTION'} <= codes
