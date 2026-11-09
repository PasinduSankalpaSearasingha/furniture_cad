"""Panel shaping follows the contour bounds even away from the local origin."""
import cadquery as cq
import pytest
from app.photo_schema import PhotoDesign
from app.agents.photo_agent import specification
from app.photo_cad import create_photo_furniture
from app.geometry import exact_bounds


def plan(**changes):
    part=dict(part_id='F001',label='Offset sculpted panel',geometry='outline_panel',size=[600,300,40],
        outline=[[1000,600],[1600,600],[1600,900],[1000,900]],position=[0,0,0],material='foam',color_rgb=[.5,.5,.5],
        evidence='Synthetic translated outline',**changes)
    return PhotoDesign(furniture_type='Synthetic offset panel',summary='Panel frame regression',
        values=[dict(key=k,value=v,explanation='Synthetic') for k,v in [('width_mm',600),('depth_mm',300),('height_mm',160)]],
        parts=[part],assumptions=['Synthetic, unverified'])


def shape(design):
    spec,_=specification(design,dict(known_values=[]),'Panel regression')
    return create_photo_furniture(spec).shapes['F001'].val()


def test_surface_grid_spans_actual_offset_outline_instead_of_the_origin():
    grid=dict(depths=[[8,8,8],[8,0,8],[8,8,8]])
    with pytest.raises(ValueError,match='sculpted surface does not form a panel'):shape(plan(surface_grid=grid))
    result=shape(plan(surface_grid=grid,panel_frame='BOUNDS'));b=exact_bounds(result)
    assert result.isValid() and len(result.Solids())==1
    assert [b.xmin,b.xmax,b.ymin,b.ymax]==pytest.approx([1000,1600,600,900],abs=.001)
    assert result.Volume()<600*300*40 and b.zmin==pytest.approx(-20,abs=.001)


def test_global_dish_is_centered_on_the_offset_panel_and_keeps_its_envelope():
    result=shape(plan(dish_depth=3,panel_frame='BOUNDS'));b=exact_bounds(result)
    assert result.isValid() and result.Volume()<600*300*40
    assert not result.isInside(cq.Vector(1300,750,19),1e-6)
    assert result.isInside(cq.Vector(1300,750,16),1e-6)
    assert [b.xlen,b.ylen,b.zlen]==pytest.approx([600,300,40],abs=.001)


def test_deep_cushion_crown_does_not_self_intersect_when_given_thickness():
    raw=plan(panel_frame='BOUNDS').model_dump(mode='json')
    raw['parts'][0].update(size=[550,1130,210],outline=[[1000,600],[1550,600],[1550,1730],[1000,1730]],
        surface_grid=dict(depths=[[95,80.75,80.75,80.75,95],[52.25,0,0,0,52.25],[61.75,33.25,33.25,33.25,61.75]]))
    for item in raw['values']:item['value']={'width_mm':550,'depth_mm':1130,'height_mm':210}[item['key']]
    result=shape(PhotoDesign.model_validate(raw))
    assert result.isValid() and len(result.Solids())==1 and 0<result.Volume(1e-10)<550*1130*210
