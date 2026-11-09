"""Stable section axes and bounded interpolation preserve dimension targets."""
import json
from pathlib import Path
import pytest
from app.photo_schema import PhotoDesign
from app.agents.photo_agent import specification
from app.photo_cad import create_photo_furniture
from app.geometry import exact_bounds


def solid(raw):
    spec,_=specification(PhotoDesign.model_validate(raw),dict(known_values=[]),'Frame regression')
    return create_photo_furniture(spec)


def sofa():
    return json.loads((Path(__file__).parent/'fixtures/sofa_with_ottoman.json').read_text())


def test_world_up_planar_oval_sweep_retains_its_vertical_section_height():
    raw=sofa();part=raw['parts'][7];part['part_id']='F001';part['sweep_frame']='WORLD_UP';raw['parts']=[part]
    shape=solid(raw).shapes['F001'].val();bounds=exact_bounds(shape)
    assert shape.isValid() and bounds.zmin==pytest.approx(640,abs=1e-5) and bounds.zmax==pytest.approx(880,abs=1e-5)
    assert bounds.zlen==pytest.approx(240,abs=1e-5)


def test_bounded_loft_retains_sections_without_interpolation_overshoot():
    raw=sofa();part=raw['parts'][9];part['part_id']='F001';part['loft_mode']='BOUNDED';raw['parts']=[part]
    actual=solid(raw);shape=actual.shapes['F001'].val();bounds=exact_bounds(shape)
    assert shape.isValid() and len(shape.Solids())==1
    assert bounds.xmin>=-1e-5 and bounds.xmax<=230+1e-5
    assert bounds.ymin>=-1e-5 and bounds.ymax<=680+1e-5
    assert actual.construction_adjustments[0]['section_dimensions_changed'] is False


def test_legacy_saved_frames_and_lofts_keep_their_original_interpretation():
    raw=sofa();part=raw['parts'][7];part['part_id']='F001';raw['parts']=[part]
    assert 'sweep_frame' not in PhotoDesign.model_validate(raw).model_dump(mode='json')['parts'][0]
    bounds=exact_bounds(solid(raw).shapes['F001'].val())
    assert bounds.zlen==pytest.approx(183.320792,abs=.01)


def test_kernel_failures_report_the_component_instead_of_an_unhandled_exception(monkeypatch):
    import cadquery as cq
    from OCP.StdFail import StdFail_NotDone
    def failure(*args,**kwargs):raise StdFail_NotDone('Synthetic kernel failure')
    monkeypatch.setattr(cq.Workplane,'fillet',failure)
    raw=sofa();part=raw['parts'][7];part['part_id']='F001';part['rounded_ends']=True;raw['parts']=[part]
    with pytest.raises(ValueError,match='F001.*CAD kernel could not build'):
        solid(raw)


def test_envelope_measurement_coarsens_within_a_bounded_mesh_budget(monkeypatch):
    import cadquery as cq
    from app import engineering_cad
    target=cq.Workplane('XY').box(100,80,20).val();original=target.tessellate(.2);calls=[]
    monkeypatch.setattr(engineering_cad,'mesh_absolute',lambda *a,**k:0)
    def tessellate(self,tolerance,angular):
        calls.append(tolerance)
        return (original[0],range(1_000_001)) if tolerance==.02 else original
    monkeypatch.setattr(cq.Solid,'tessellate',tessellate)
    bounds=engineering_cad.cad_bounds(target)
    assert calls==[.02,.05] and bounds.requested_tolerance_mm==.05
    assert [bounds.xlen,bounds.ylen,bounds.zlen]==pytest.approx([100,80,20])
