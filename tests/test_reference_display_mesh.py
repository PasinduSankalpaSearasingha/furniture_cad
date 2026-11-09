"""Dense reference previews must retain continuous surfaces, not sparse faces."""
import trimesh
from app.cad_delivery import reference_display_mesh


def test_dense_preview_reduction_keeps_closed_surface_and_source_unchanged():
    source=trimesh.creation.icosphere(subdivisions=4,radius=20)
    vertices,faces=source.vertices.copy(),source.faces.copy()
    display,report=reference_display_mesh(source,face_budget=2000)
    assert len(display.faces)<=2000
    assert display.is_watertight and display.volume>0
    assert (source.vertices==vertices).all() and (source.faces==faces).all()
    assert report['method']=='continuous_surface_decimation'
    assert report['sampled_surface_deviation_mm']<=report['sampled_deviation_allowance_mm']
