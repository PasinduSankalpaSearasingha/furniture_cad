import json
from pathlib import Path
import trimesh
from app.mesh_pipeline import run_reconstruction


def test_complete_local_reconstruction_preserves_mesh_and_exports(tmp_path):
    original = tmp_path/'original.obj'
    mesh = trimesh.creation.box(extents=[700, 500, 750]); mesh.apply_translation([0, 0, 375]); mesh.export(original)
    output = tmp_path/'result'
    report = run_reconstruction(original, output, furniture_type='cabinet', up_axis='Z',
        known_dimensions={'width_mm':700,'depth_mm':500,'height_mm':750},
        options=dict(enabled=False, verify_points=1000, final_views=12, camera_resolution=24))
    assert report['export_validation']['assembly_reopened']
    assert report['production_ready'] is False
    assert report['primary_fitting_method']=='global_surface'
    assert report['virtual_camera_evaluations']==0 and report['multiview'] is None
    assert (output/'original_reference.obj').read_bytes() == original.read_bytes()
    for name in ('overlay.glb','deviation.glb','optimized_cad.glb','assembly.step','package.zip',
                 'feature_specification.json','geometry_deviation_report.json','parameter_revision_history.json',
                 'best_valid_assembly.step'):
        assert (output/name).is_file(), name
    assert report['original_photo_used'] is False
    assert 'unverified' in report['photo_verification_status']
    manifest = json.loads((output/'manifest.json').read_text())
    assert 'assembly.step' in manifest['files']
