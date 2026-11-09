"""Dense geometry correctness, resources and native exports; no paid services."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
import pytest
import trimesh
from app.mesh_analyzer import analyze_mesh, load_mesh
from app.mesh_sampling import sample_surface
from app.geometry_cache import GeometryCache, ResourceBudget, content_hash
from app.geometry_acceleration import SurfaceIndex
from app.mesh_registration import normalize_mesh, register_points
from app.multiview_geometry import generate_cameras, analyze_view, multiview_report
from app.adaptive_view_selector import select_views
from app.mesh_segmentation import segment_mesh
from app.mesh_to_cad import reconstruct_plan, build_native, tessellate_build, export_native, fit_bspline, nurbs_patch


@pytest.fixture
def box():
    return analyze_mesh(trimesh.creation.box(extents=[700, 500, 750]))


def test_glb_instances_apply_each_node_transform(tmp_path):
    scene = trimesh.Scene()
    mesh = trimesh.creation.box()
    scene.add_geometry(mesh, node_name='first', geom_name='shared')
    transform = np.eye(4); transform[0, 3] = 5
    scene.graph.update(frame_to='second', matrix=transform, geometry='shared')
    path = tmp_path/'instances.glb'; path.write_bytes(scene.export(file_type='glb'))
    data = load_mesh(path)
    assert len(data.mesh.faces) == 24
    assert np.ptp(data.mesh.bounds[:, 0]) == pytest.approx(6)
    assert len(np.unique(data.face_components)) == 2


def test_obj_loading_and_degenerate_nonfinite_rejection(tmp_path):
    path = tmp_path/'box.obj'
    trimesh.creation.box().export(path)
    assert load_mesh(path).report['watertight']
    with pytest.raises(ValueError, match='no triangle'):
        analyze_mesh(trimesh.Trimesh())
    mesh = trimesh.creation.box()
    mesh.vertices[0] = [np.nan, 0, 0]
    with pytest.raises(ValueError, match='invalid coordinates'):
        analyze_mesh(mesh)
    mesh = trimesh.creation.box()
    mesh.faces = np.vstack([mesh.faces, [0, 0, 0]])
    assert analyze_mesh(mesh).report['removed_degenerate_faces'] == [12]


@pytest.mark.parametrize('count', [25_000, 100_000, 300_000, 750_000, 1_000_000])
def test_dense_samples_are_deterministic_on_surface_and_weighted(box, count):
    # Freeze this test's allocation grant, so concurrent host RAM changes do not
    # compare two intentionally different automatic fallback densities.
    budget = ResourceBudget()
    granted = budget.sample_count(count)
    budget.sample_count = lambda requested: granted
    first = sample_surface(box, count, budget=budget)
    second = sample_surface(box, count, budget=budget)
    assert len(first.points) == granted
    assert first.fallback == (granted < count)
    assert np.array_equal(first.points, second.points)
    assert first.area_weights.sum() == pytest.approx(1)
    # RaycastingScene uses float32, including its closest-triangle arithmetic.
    tolerance = np.finfo(np.float32).eps * np.max(np.abs(box.mesh.vertices)) * 8
    assert np.max(SurfaceIndex(box).closest(first.points)['distances']) < tolerance


def test_small_components_receive_quota_without_changing_area_measure():
    big = trimesh.creation.box(extents=[100, 100, 1])
    small = trimesh.creation.box(extents=[1, 1, 10]); small.apply_translation([200, 0, 0])
    labels = np.repeat([0, 1], 12)
    data = analyze_mesh(trimesh.util.concatenate([big, small]), labels)
    samples = sample_surface(data, 25_000)
    assert (samples.component_ids == 1).sum() > .2*len(samples.points)
    small_weight = samples.area_weights[samples.component_ids == 1].sum()
    assert small_weight == pytest.approx(small.area/(big.area+small.area), rel=.1)


def test_exact_triangle_surface_is_not_nearest_vertex(box):
    query = np.array([[0, 0, 380], [0, 0, 0]])
    result = SurfaceIndex(box).closest(query)
    assert result['distances'] == pytest.approx([5, 250])
    assert result['points'][0] == pytest.approx([0, 0, 375])
    assert result['normals'][0] == pytest.approx([0, 0, 1])


def test_component_correspondence_cannot_jump_to_nearby_part():
    a = trimesh.creation.box(); b = a.copy(); b.apply_translation([4, 0, 0])
    data = analyze_mesh(trimesh.util.concatenate([a, b]), np.repeat([0, 1], 12))
    index = SurfaceIndex(data)
    assert index.closest([[0, 0, 0]], components=[1])['distances'][0] == pytest.approx(3.5)
    with pytest.raises(ValueError, match='No reference surface'):
        index.closest([[0, 0, 0]], components=[99])


def test_calibration_uses_uniform_scale_and_refuses_conflicting_measurements(box):
    normalized, report = normalize_mesh(box, {'height_mm': 750}, up_axis='Z')
    assert report['units'] == 'mm'
    assert normalized.mesh.bounds[0, 2] == pytest.approx(0)
    assert report['physical_accuracy_verified'] is False
    with pytest.raises(ValueError, match='conflict'):
        normalize_mesh(box, {'height_mm': 750, 'width_mm': 1000}, up_axis='Z')


def test_rigid_multiscale_icp_recovers_translation_and_small_rotation():
    from scipy.spatial.transform import Rotation
    rng = np.random.default_rng(1)
    points = rng.normal(size=(3000, 3))*[100, 50, 30]
    rotation = Rotation.from_euler('z', 3, degrees=True).as_matrix()
    target = points@rotation.T + [15, -8, 3]
    transform, report = register_points(points, target)
    recovered = points@transform[:3, :3].T+transform[:3, 3]
    assert np.linalg.norm(recovered-target, axis=1).mean() < .001
    assert report['scale_optimized'] is False


@pytest.mark.parametrize('count', [12, 24, 36, 48])
def test_camera_counts_share_projection_and_identical_shapes_have_zero_loss(box, count):
    cameras = generate_cameras(box.mesh.bounds, count, resolution=24)
    assert len(cameras) == count
    index = SurfaceIndex(box)
    report = multiview_report(index, index, cameras)
    assert report['silhouette_iou_loss'] == 0
    assert report['count'] == count
    assert report['covered_faces'] == 12
    for camera in cameras:
        right, up, forward = camera.basis()
        assert np.dot(right, up) == pytest.approx(0, abs=1e-10)
        assert np.linalg.norm(forward) == pytest.approx(1)
    selected, data = select_views(index, cameras)
    assert 1 <= len(selected) <= count
    assert len(data) == len(selected)


def test_sampling_memory_fallback_disk_cache_and_invalidation(box, tmp_path):
    budget = ResourceBudget(memory_mb=8)
    cache = GeometryCache(directory=tmp_path, max_bytes=2*1024**2)
    samples = sample_surface(box, 750_000, budget=budget, cache=cache)
    assert 100 < len(samples.points) < 100_000 and samples.fallback
    again = sample_surface(box, 750_000, budget=budget, cache=cache)
    assert np.array_equal(samples.points, again.points)
    old = box.identity
    box.mesh.apply_translation([1, 0, 0])
    assert old != box.identity
    changed = sample_surface(box, 750_000, budget=budget, cache=cache)
    assert not np.array_equal(samples.points, changed.points)
    assert cache.used <= cache.max_bytes


def test_readonly_parallel_queries_are_safe(box):
    index = SurfaceIndex(box, threads=1)
    query = [[0, 0, 400]]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: index.closest(query)['distances'][0], range(6)))
    assert results == pytest.approx([25]*6)


def test_segmentation_native_reconstruction_step_roundtrip(box, tmp_path):
    normalized, _ = normalize_mesh(box, up_axis='Z')
    regions, graph = segment_mesh(normalized, 'cabinet')
    plan, report = reconstruct_plan(normalized, regions, 'cabinet')
    assert report['complete'] and graph['review_required']
    build = build_native(plan)
    assert build.shapes['F001'].val().isValid()
    exported = export_native(build, plan, tmp_path)
    assert exported['assembly_reopened']
    assert b'ISO-10303-21' in (tmp_path/'assembly.step').read_bytes()


def test_curves_and_rational_nurbs_are_native_valid_geometry():
    import cadquery as cq
    curve = fit_bspline([[0, 0, 0], [0, 10, 2], [0, 20, 3], [0, 30, 0]])
    assert curve.geomType() == 'BSPLINE' and curve.isValid()
    controls = [[[x*10, y*10, 2 if x == y == 1 else 0] for y in range(3)] for x in range(3)]
    face = nurbs_patch(controls, [[1, 1, 1], [1, 2, 1], [1, 1, 1]])
    assert face.geomType() == 'BSPLINE' and face.isValid()
    solid = face.thicken(2)
    assert solid.isValid() and len(solid.Solids()) == 1
