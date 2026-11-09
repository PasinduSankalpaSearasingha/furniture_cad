"""Unambiguous model slips are repaired locally; genuinely invalid parts are still rejected."""
import pytest
from pydantic import ValidationError
from app.photo_schema import PhotoDesign


def design(*parts):
    values=[dict(key=k,value=v,explanation='x') for k,v in (('width_mm',3000),('depth_mm',1450),('height_mm',880))]
    base=dict(part_id='F001',label='l',evidence='e',material='fabric',color_rgb=[.5,.5,.5])
    return dict(furniture_type='sofa',summary='s',values=values,assumptions=['a'],
        parts=[{**base,**part,'part_id':f'F{i+1:03d}'} for i,part in enumerate(parts)])


def section(fraction):
    return dict(fraction=fraction,width=50,depth=50)


def test_unused_size_slots_and_rounded_rect_sweeps_are_repaired():
    path=[[0,0,0],[100,0,0]]
    plan=PhotoDesign.model_validate(design(
        dict(geometry='loft',size=[0,0,0],sections=[dict(center=[0,0,0],width=50,depth=50),dict(center=[0,0,100],width=50,depth=50)]),
        dict(geometry='rect_sweep',size=[20,30,0],path=path,rounded_ends=True)))
    assert plan.parts[0].size==[1,1,1] and plan.parts[1].size==[20,30,1] and not plan.parts[1].rounded_ends
    assert 'Local corrections' in plan.assumptions[-1]


def test_ellipse_sweep_with_sections_becomes_a_profile_sweep():
    plan=PhotoDesign.model_validate(design(dict(geometry='ellipse_sweep',size=[20,30,0],rounded_ends=True,
        path=[[0,0,0],[100,0,0]],sweep_sections=[section(0),section(1)])))
    assert plan.parts[0].geometry=='profile_sweep' and not plan.parts[0].rounded_ends


@pytest.mark.parametrize('size',[[-1,1,1],[0,10,10],[20000,10,10]])
def test_invalid_used_dimensions_are_still_rejected(size):
    with pytest.raises(ValidationError,match='Part size'):
        PhotoDesign.model_validate(design(dict(geometry='box',size=size)))
