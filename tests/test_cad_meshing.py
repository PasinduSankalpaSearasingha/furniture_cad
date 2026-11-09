"""Mesh failure regression: exact CAD is independent of bounded display meshes."""
from io import BytesIO
from types import SimpleNamespace
import numpy as np
import cadquery as cq
from PIL import Image
import pytest
from app.cad_meshing import absolute_triangles,absolute_mesh_bounds,simplify_triangles,CADTriangleBudgetError
from app.ai.geometry_preview import tessellation,render_build


def test_absolute_mesh_does_not_invoke_relative_cadquery_remesher(monkeypatch):
    shape=cq.Workplane('XY').box(30,20,10).edges('|Z').fillet(3).val()
    before=shape.Volume()
    def forbidden(*a,**k):raise AssertionError('Relative remeshing must not run')
    monkeypatch.setattr(cq.Shape,'tessellate',forbidden)
    points,triangles,normals=tessellation(shape,.1)
    assert len(triangles)>12 and np.isfinite(points).all() and np.isfinite(normals).all()
    assert shape.isValid() and shape.Volume()==pytest.approx(before,rel=1e-12)
    # Outward winding must survive OCCT face transforms and reversed faces.
    volume=np.einsum('ij,ij->i',points[triangles[:,0]],np.cross(points[triangles[:,1]],points[triangles[:,2]])).sum()/6
    assert volume>0 and volume==pytest.approx(before,rel=.01)


def test_dense_display_mesh_reduction_preserves_closed_topology_and_source_cad():
    shape=cq.Workplane('XY').sphere(25).val();before=shape.Volume()
    points,triangles=absolute_triangles(shape,.02,.08,max_triangles=500000)
    assert len(triangles)>1000
    reduced,faces=simplify_triangles(points,triangles,1000,.15)
    assert 0<len(faces)<=1000 and np.isfinite(reduced).all()
    # Coincident face points are welded for reduction; the CAD remains exact.
    assert shape.isValid() and shape.Volume()==pytest.approx(before,rel=1e-12)
    volume=np.einsum('ij,ij->i',reduced[faces[:,0]],np.cross(reduced[faces[:,1]],reduced[faces[:,2]])).sum()/6
    assert volume==pytest.approx(before,rel=.04)


def test_triangle_budget_has_specific_exception_and_does_not_drop_parts():
    shape=cq.Workplane('XY').box(10,10,10).val()
    with pytest.raises(CADTriangleBudgetError):absolute_triangles(shape,.1,max_triangles=1)
    assert shape.isValid() and shape.Volume()==pytest.approx(1000)


def test_envelope_uses_streamed_vertices_without_triangle_arrays(monkeypatch):
    from app import engineering_cad
    shape=cq.Workplane('XY').box(30,20,10).translate((5,8,12)).val()
    def forbidden(*a,**k):raise AssertionError('Envelope measurement does not need triangle arrays')
    monkeypatch.setattr(engineering_cad,'absolute_triangles',forbidden)
    bounds=engineering_cad.cad_bounds(shape)
    assert (bounds.xmin,bounds.xmax)==pytest.approx((-10,20))
    assert (bounds.ymin,bounds.ymax)==pytest.approx((-2,18))
    assert (bounds.zmin,bounds.zmax)==pytest.approx((7,17))
    with pytest.raises(CADTriangleBudgetError):absolute_mesh_bounds(shape,.1,max_triangles=1)


def test_prepared_mesh_is_reused_across_camera_views(monkeypatch):
    from app.ai import geometry_preview
    shape=cq.Workplane('XY').box(10,10,10)
    build=SimpleNamespace(registry=[SimpleNamespace(part_id='F001',label='Test cube',color_rgb=[.5,.3,.2])],shapes={'F001':shape})
    meshes=geometry_preview.preview_meshes(build)
    def forbidden(*a,**k):raise AssertionError('Views must reuse the prepared native mesh')
    monkeypatch.setattr(geometry_preview,'tessellation',forbidden)
    for azimuth in (-90,90):
        pixels=render_build(build,azimuth=azimuth,size=(200,200),meshes=meshes)
        with Image.open(BytesIO(pixels)) as image:assert image.size==(200,200)
