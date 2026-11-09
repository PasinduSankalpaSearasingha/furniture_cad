from app.models import CabinetSpec
from app.cad import create_cabinet
from app.drawings import export_front_elevation
from app.manufacturing import export_manufacturing
import ezdxf


def spec(**changes):
    d=dict(width_mm=700,depth_mm=450,height_mm=1800,
           carcass_material='plywood',panel_thickness_mm=18,back_thickness_mm=6)
    d.update(changes)
    return CabinetSpec(**d)


def test_cutlist_is_from_part_registry():
    b=create_cabinet(spec())
    assert len(b.parts)==5
    assert next(p for p in b.parts if p.part_id=='P003').blank_length_mm==664
    assert all(sh.val().isValid() for sh in b.shapes.values())


def test_glass_and_dowel_holes(tmp_path):
    s=spec(include_glazed_door=True,joint_type='dowel_demo')
    b=create_cabinet(s)
    assert len(b.parts)==10
    assert len(b.operations)==12
    assert all(sh.val().isValid() for sh in b.shapes.values())
    export_front_elevation(s,tmp_path)
    export_manufacturing(s,b,tmp_path)
    assert (tmp_path/'cut_list_REVIEW.csv').exists()
    assert (tmp_path/'glass_schedule_REVIEW.csv').exists()
    assert (tmp_path/'review_report.json').exists()
    assert len(ezdxf.readfile(tmp_path/'front_elevation_REVIEW.dxf').modelspace()) > 0
