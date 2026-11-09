"""Native circular radius measurements agree across the real vector exports."""
from dataclasses import replace
import json
import math
import cadquery as cq
import ezdxf
from pypdf import PdfReader
import pytest
from app.agents.photo_agent import resolve_estimated_geometry
from app.photo_schema import PhotoDesign
from app.furniture_sheet import create_furniture_sheet
from app.curve_dimensions import curve_inventory
from app.dimension_sheet import STEM
from tests.test_engineering_cad import table_plan


def test_circle_radius_is_native_editable_and_true_size_in_all_outputs(tmp_path):
    raw=table_plan(720).model_dump(mode='json')
    raw['values'].append(dict(key='top_corner_radius_mm',value=20,explanation='Synthetic requested radius'))
    raw['parts'][0]['corner_radius']='top_corner_radius_mm'
    _,spec,provenance,build,_=resolve_estimated_geometry(PhotoDesign.model_validate(raw),
        dict(known_values=[dict(key='width_mm',value=720),dict(key='top_corner_radius_mm',value=20)]),'Radius test')
    shapes={}
    for pid,shape in build.shapes.items():
        path=tmp_path/(pid+'.step');cq.exporters.export(shape,str(path));shapes[pid]=cq.importers.importStep(str(path))
    sheet=create_furniture_sheet(spec,replace(build,shapes=shapes),tmp_path,provenance=provenance)
    assert any(v['radius_mm']==20 for v in sheet['curves']['circular_radii'])
    assert sheet['radius_dimensions'] and all(v['value_mm']==pytest.approx(20) for v in sheet['radius_dimensions'])
    assert 'R20' in PdfReader(tmp_path/(STEM+'.pdf')).pages[0].extract_text()
    assert 'R20' in (tmp_path/(STEM+'.svg')).read_text()
    dxf=ezdxf.readfile(tmp_path/(STEM+'.dxf'));assert not dxf.audit().errors
    radial=[d for d in dxf.modelspace().query('DIMENSION') if d.dxf.dimtype&7==4]
    assert len(radial)==len(sheet['radius_dimensions'])
    for dimension in radial:
        assert dimension.get_measurement()==pytest.approx(20,abs=1e-6)
        assert dimension.dxf.defpoint.distance(dimension.dxf.defpoint4)==pytest.approx(20,abs=1e-6)
    assert json.loads((tmp_path/(STEM+'.json')).read_text())['curves']['physical_radii_verified'] is False


def test_ellipse_and_freeform_curves_are_not_given_a_false_constant_radius():
    ellipse=cq.Workplane('XY').ellipse(80,40).extrude(10)
    curves=curve_inventory({'shape':ellipse})
    assert not curves['circular_radii']
    assert curves['variable_curves'] and all(not v['constant_radius'] for v in curves['variable_curves'])
    ranges=curves['variable_curves'][0]
    assert ranges['sampled_local_radius_min_mm']>=20-1e-5
    assert ranges['sampled_local_radius_max_mm']<=160+1e-5
    spline=cq.Workplane('XY').spline([(0,0),(20,10),(50,-8),(80,25)]).val()
    assert curve_inventory({'spline':spline})['variable_curves']
