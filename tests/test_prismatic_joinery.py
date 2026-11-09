"""Approved synthetic geometry fixtures; no structural qualification is implied."""
import math
import pytest
from app.cad import create_cabinet
from app.geometry import exact_bounds
from app.validation_engine import validate_build
from app.manufacturing import schedules_from_registry
from tests.test_cabinet import spec
from tests.test_engineering import joint


@pytest.mark.parametrize('kind',['dado','rabbet','mortise_tenon'])
def test_receiving_cut_and_integral_tenon_are_real_geometry_with_derived_stock(kind):
    request=joint(kind=kind,depths=(8,7.8));request['profile'].update(cross_size_mm=(20,8),clearance_mm=.1)
    build=create_cabinet(spec(joints=[request]))
    assert not build.joint_issues
    assert build.shapes['P001'].val().Volume()==pytest.approx(18*450*1800-20*8*8)
    assert build.shapes['P004'].val().Volume()==pytest.approx(664*450*18+19.8*7.8*7.8)
    assert build.registry['P004'].blank_length_mm==pytest.approx(664+7.8)
    assert not [f for f in validate_build(spec(joints=[request]),build).findings if f.severity=='ERROR']
    assert len(build.operations)==2 and build.operations[0].diameter_mm is None
    rows=schedules_from_registry(build,spec(joints=[request]))['parts']
    assert next(row for row in rows if row['part_id']=='P004')['length_mm']==pytest.approx(671.8)


def test_confirmat_clearance_and_pilot_holes_have_distinct_actual_diameters():
    request=joint(kind='mechanical');request['profile'].update(mechanical_type='confirmat',diameter_mm=7,pilot_diameter_mm=4)
    build=create_cabinet(spec(joints=[request]))
    assert not build.joint_issues
    assert [op.diameter_mm for op in build.operations]==[7,4]
    assert build.shapes['P001'].val().Volume()==pytest.approx(18*450*1800-math.pi*3.5**2*8)
    assert build.shapes['P004'].val().Volume()==pytest.approx(664*450*18-math.pi*2**2*12)


def test_unsupported_tenon_root_leaves_both_components_unchanged():
    request=joint(kind='mortise_tenon',depths=(8,7.8));request['profile'].update(cross_size_mm=(20,80),clearance_mm=.1)
    build=create_cabinet(spec(joints=[request]))
    assert not build.operations and build.joint_issues
    assert build.shapes['P001'].val().Volume()==pytest.approx(18*450*1800)
    assert build.registry['P004'].length_mm==664
