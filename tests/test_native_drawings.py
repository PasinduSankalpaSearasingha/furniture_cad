"""Curve accuracy, projection coordinates and dimensions from real B-Rep solids."""
import math
import cadquery as cq
import ezdxf
from pypdf import PdfReader
import pytest
from app.drawing_projection import Projection,write_dxf
from app.drawings import dxf_view,projected_edges,pdf_view
from reportlab.pdfgen import canvas


def test_large_round_top_retains_exact_dxf_circle_instead_of_sampled_chords(tmp_path):
    shape=cq.Workplane('XY').circle(500).extrude(20).faces('>Z').workplane().hole(16).val()
    source=tmp_path/'source.step';cq.exporters.export(shape,str(source))
    actual=cq.importers.importStep(str(source)).val()
    path=tmp_path/'top.dxf'
    record=dxf_view(path,[actual],(0,1),'Round top','TOP',1)
    doc=ezdxf.readfile(path)
    circles=[edge for edge in doc.modelspace().query('CIRCLE') if edge.dxf.layer=='CAD']
    assert sorted(edge.dxf.radius for edge in circles)==pytest.approx([8,500],abs=1e-7)
    assert not list(doc.modelspace().query('LWPOLYLINE[layer=="CAD"]'))
    assert record['curve_representation']=='NATIVE_DXF' and record['hidden_lines']
    assert doc.units==4 and not doc.audit().errors
    # Quantify the replaced plotting error on the same source geometry.
    errors=[]
    for points in projected_edges([actual],(0,1)):
        for a,b in zip(points,points[1:]):
            if math.hypot(*a)>499:
                errors.append(500-math.hypot((a[0]+b[0])/2,(a[1]+b[1])/2))
    assert max(errors)>1


@pytest.mark.parametrize('axes,expected', [((0,1),(70,30)),((0,2),(70,10)),((1,2),(30,10))])
def test_projection_keeps_axes_scale_and_native_dimension_measurements(tmp_path,axes,expected):
    from ezdxf import bbox
    shape=cq.Workplane('XY').box(70,30,10).translate((90,45,60)).val()
    path=tmp_path/'view.dxf'
    record=dxf_view(path,[shape],axes,'Asymmetric part','TEST',1)
    doc=ezdxf.readfile(path)
    extents=bbox.extents([edge for edge in doc.modelspace() if edge.dxf.layer=='CAD'])
    assert (extents.size.x,extents.size.y)==pytest.approx(expected,abs=1e-6)
    measurements=[dimension.get_measurement() for dimension in doc.modelspace().query('DIMENSION')]
    assert measurements==pytest.approx(expected,abs=1e-6)
    assert (record['width_mm'],record['height_mm'])==pytest.approx(expected)


def test_elliptical_projection_retains_ellipse_and_offset_without_mutating_source(tmp_path):
    shape=cq.Workplane('XY').ellipse(80,35).extrude(15).val()
    volume=shape.Volume()
    projection=Projection([shape],(0,1))
    path=tmp_path/'ellipses.dxf'
    doc=write_dxf(path,[(projection,0,0),(projection,250,0)])
    ellipses=[edge for edge in doc.modelspace().query('ELLIPSE') if edge.dxf.layer=='CAD']
    assert len(ellipses)==2
    assert sorted(edge.dxf.center.x for edge in ellipses)==pytest.approx([0,250],abs=1e-7)
    assert all(edge.dxf.ratio==pytest.approx(35/80) for edge in ellipses)
    assert shape.Volume()==pytest.approx(volume,rel=1e-12)


def test_curved_rail_keeps_spline_entities(tmp_path):
    path=cq.Workplane('XZ').spline([(0,0),(45,65),(70,115),(80,190)])
    shape=cq.Workplane('YZ').circle(10).sweep(path).val()
    doc=write_dxf(tmp_path/'curved.dxf',[(Projection([shape],(0,2)),0,0)])
    splines=list(doc.modelspace().query('SPLINE'))
    assert splines and all(edge.dxf.degree>=2 for edge in splines)
    assert not doc.audit().errors


def test_pdf_uses_vector_curves_and_preserves_sheet_annotations(tmp_path):
    shape=cq.Workplane('XY').circle(80).extrude(20).val()
    path=tmp_path/'round.pdf'
    pdf=canvas.Canvas(str(path),pagesize=(842,595))
    pdf_view(pdf,[shape],(0,1),(30,80,760,460),'ROUND TOP')
    pdf.save()
    document=PdfReader(path)
    assert 'ROUND TOP' in document.pages[0].extract_text()
    assert b' c' in document.pages[0].get_contents().get_data()
    assert len(document.pages)==1


def test_explicit_isometric_camera_projects_correct_envelope_without_changing_solid(tmp_path):
    solid=cq.Workplane('XY').box(70,30,10).translate((90,45,60)).val()
    volume=solid.Volume()
    projection=Projection([solid],look_from=(1,-1,1),look_up=(0,0,1))
    assert projection.bounds.size.X==pytest.approx(100/math.sqrt(2),abs=1e-6)
    assert projection.bounds.size.Y==pytest.approx(100/math.sqrt(6)+10*math.sqrt(2/3),abs=1e-6)
    assert projection.hidden.edges()
    assert projection.svg_drawing(.2).width>0
    assert solid.Volume()==pytest.approx(volume,rel=1e-12)


def test_sculpted_cross_base_hidden_projection_handles_empty_hlr_spline(tmp_path):
    from scripts.create_table_platform_sample import table_plan
    from app.models import CabinetSpec
    from app.photo_cad import create_photo_furniture
    plan,_=table_plan()
    spec=CabinetSpec(project_name='HLR regression',width_mm=2800,depth_mm=1100,height_mm=750,
        carcass_material='solid_wood',panel_thickness_mm=45,family='photo',photo_design=plan)
    build=create_photo_furniture(spec);part=build.registry['F004']
    path=tmp_path/'wing.step';cq.exporters.export(build.shapes['F004'],str(path))
    actual=cq.importers.importStep(str(path)).val()
    local=cq.Plane(origin=part.center_xyz_mm,xDir=part.length_axis,normal=part.thickness_axis).toLocalCoords(actual)
    volume=actual.Volume()
    for axes in ((0,1),(0,2),(1,2)):
        projection=Projection([local],axes)
        assert projection.svg_drawing(.2).width>0
        assert projection.visible.edges()
        assert all(edge.length>0 for edge in [*projection.visible.edges(),*projection.hidden.edges()])
    assert actual.Volume()==pytest.approx(volume,rel=1e-12)
