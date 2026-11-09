"""Native CAD recovery tests. Synthetic image coordinates are not real accuracy evidence."""
from copy import deepcopy
from pathlib import Path
from uuid import uuid4
import cv2
import numpy as np
import pytest
from app.cad_optimizer_schema import CADCamera, CADOptimizationOptions, CADViewEvidence
from app.cad_parameters import CADParameterRegistry
from app.cad_projection import project_points, landmark_point, curve_points, NativeProjection
from app.cad_optimizer import optimize_cad, optimize_cad_isolated, NativeCADBuilder, boundary_distances, digest
from app.photo_schema import PhotoDesign
from tests.test_engineering_cad import table_plan


def camera(perspective=False, size=(1000, 1000)):
    # CAD X right, Z up; camera Y down and looks toward CAD +Y.
    rotation = cv2.Rodrigues(np.array([[1., 0, 0], [0, 0, -1.], [0, 1., 0]]))[0].ravel()
    return CADCamera(image_size=size, projection='perspective' if perspective else 'orthographic',
        intrinsic_matrix=[[900 if perspective else .8, 0, size[0]/2],
                          [0, 900 if perspective else .8, size[1]/2], [0, 0, 1]],
        rotation_vector=tuple(rotation), translation_mm=(0, 375, 1500),
        reliable=True, uncertainty='Known synthetic test camera')


def view_from_plan(target, marks=None, cam=None):
    cam = cam or camera()
    build, _ = NativeCADBuilder({}, 'Synthetic target')(target)
    marks = marks or [dict(part_id=p.part_id, anchor='center') for p in target.parts]
    from app.cad_optimizer_schema import CADLandmark
    observations = []
    for mark in marks:
        probe = CADLandmark(**mark, image_point=(0, 0))
        pixel = project_points([landmark_point(probe, target, build)], cam)[0]
        observations.append(dict(**mark, image_point=pixel.tolist(), evidence_id='V001'))
    return CADViewEvidence(image_id=uuid4(), camera=cam, landmarks=observations)


def options(view, **changes):
    return CADOptimizationOptions(views=[view], max_seconds=60, max_evaluations=40, **changes)


def test_camera_projection_matches_opencv_and_orthographic_coordinates():
    cam = camera(True)
    points = np.array([[0., 0, 375], [100, 100, 400]])
    expected, _ = cv2.projectPoints(points, np.array(cam.rotation_vector), np.array(cam.translation_mm, dtype=float),
        np.array(cam.intrinsic_matrix), None)
    assert np.allclose(project_points(points, cam), expected.reshape(-1, 2))
    assert np.allclose(project_points([[0, 0, 375], [100, 0, 375]], camera()), [[500, 500], [580, 500]])


def test_camera_rejects_behind_eye_and_invalid_intrinsics():
    cam = camera(True).model_copy(update={'translation_mm': (0, 0, -2000)})
    with pytest.raises(ValueError, match='behind'):
        project_points([[0, 0, 0]], cam)
    with pytest.raises(ValueError):
        CADCamera(image_size=(100, 100), intrinsic_matrix=[[0, 0, 0]] * 3)


def test_registry_locks_confirmed_values_and_enforces_bounds_and_component_subset():
    plan = table_plan()
    opts = CADOptimizationOptions(component_ids=['F002'], feature_components={'V001': ['F002']}, mismatch_feature_ids=['V001'])
    registry = CADParameterRegistry(plan, [dict(key='width_mm', value=700)], opts)
    assert registry.parameters['width_mm'].user_confirmed
    assert not registry.parameters['width_mm'].optimize
    assert not registry.parameters['leg_width_mm'].optimize  # shared by unselected legs
    with pytest.raises(ValueError, match='locked'):
        registry.apply({'width_mm': 701})
    parameter = registry.parameters['F002.size.0'] if 'F002.size.0' in registry.parameters else registry.parameters['F002.rotation_deg.0']
    with pytest.raises(ValueError, match='bounds'):
        registry.apply({parameter.parameter_id: parameter.maximum + 1})
    assert registry.apply({}) == plan


def test_error_calculation_is_bidirectional_and_exact_for_corresponding_curves():
    points = np.array([[0, 0], [10, 0], [20, 0]])
    assert np.allclose(boundary_distances(points, points), 0)
    assert np.allclose(boundary_distances(points, points + [0, 3]), 3)


def test_recovers_incorrect_native_dimension_and_preserves_known_width():
    source, target = table_plan(), table_plan()
    next(v for v in target.values if v.key == 'top_thickness_mm').value = 36
    view = view_from_plan(target)
    result = optimize_cad(source, options(view, parameter_ids=['top_thickness_mm']),
        inputs={'known_values': [dict(key='width_mm', value=700)]})
    assert result.report['status'] == 'improved', result.report
    assert result.report['final_error'] < result.report['initial_error'] * .15
    assert next(v.value for v in result.plan.values if v.key == 'width_mm') == 700
    assert next(v.value for v in source.values if v.key == 'top_thickness_mm') == 30
    assert result.report['additional_llm_calls'] == 0
    assert result.report['revisions'] and result.report['fixed_dimensions_preserved']


def test_recovers_leg_position_without_moving_unrelated_parts():
    source = table_plan()
    source.parts[1].position[0] = -260.
    target = source.model_copy(deep=True)
    target.parts[1].position[0] = -282.
    view = view_from_plan(target, [dict(part_id='F002', anchor='center')])
    result = optimize_cad(source, options(view, parameter_ids=['F002.position.0'], component_ids=['F002'],
        feature_components={'V001': ['F002']}, mismatch_feature_ids=['V001']))
    assert result.report['status'] == 'improved', result.report
    assert abs(result.plan.parts[1].position[0] + 282) < .5
    assert all(a == b for i, (a, b) in enumerate(zip(source.parts, result.plan.parts)) if i != 1)


def test_refines_expression_based_leg_placement_without_erasing_measured_dependencies():
    from app.photo_schema import number
    source = table_plan()
    target = source.model_copy(deep=True)
    target.parts[1].position[0] = '-width_mm/2+80'
    view = view_from_plan(target, [dict(part_id='F002', anchor='center')])
    result = optimize_cad(source, options(view, parameter_ids=['F002.position.0']),
        inputs={'known_values': [dict(key='width_mm', value=700)]})
    assert result.report['status'] == 'improved', result.report
    expression = result.plan.parts[1].position[0]
    assert isinstance(expression, str) and 'width_mm' in expression
    assert abs(number(expression, {v.key: v.value for v in result.plan.values}) + 270) < .5


def curved_plan():
    return PhotoDesign(furniture_type='synthetic curved arm', summary='Native cubic sweep fixture',
        values=[dict(key=k, value=v, explanation='Synthetic value') for k, v in
                [('width_mm', 420), ('depth_mm', 100), ('height_mm', 250)]],
        parts=[dict(part_id='F001', label='Curved arm', evidence='Synthetic cubic curve',
            geometry='round_sweep', material='solid_wood', color_rgb=[.5, .3, .2], size=[20, 20, 20],
            curve_mode='bezier', path=[[-180, 0, 100], [-90, 0, 180], [90, 0, 180], [180, 0, 100]])],
        assumptions=['Synthetic fixture'])


def test_refines_native_bezier_curve_without_mesh_reconstruction():
    from app.cad_optimizer_schema import CADCurveObservation
    source = curved_plan()
    target = source.model_copy(deep=True)
    target.parts[0].path[1][2] = 204.
    cam = camera()
    build, _ = NativeCADBuilder({}, 'Curve target')(target)
    observation = CADCurveObservation(part_id='F001', image_points=[(0, 0)] * 3)
    pixels = project_points(curve_points(observation, target, build), cam)
    view = CADViewEvidence(image_id=uuid4(), camera=cam, curves=[dict(part_id='F001', source='path', image_points=pixels.tolist())])
    result = optimize_cad(source, options(view, parameter_ids=['F001.path.1.2']))
    assert result.report['status'] == 'improved', result.report
    assert result.report['final_error'] < result.report['initial_error'] * .3
    final, _ = NativeCADBuilder({}, 'Refined arm')(result.plan)
    assert final.shapes['F001'].val().isValid()
    assert any('BSPLINE' == edge.geomType() for edge in final.shapes['F001'].val().Edges())


def test_invalid_candidates_roll_back_without_discarding_valid_original():
    source, target = table_plan(), table_plan()
    next(v for v in target.values if v.key == 'top_thickness_mm').value = 36
    native = NativeCADBuilder({}, 'Reject candidates')
    def reject_changes(plan):
        if digest(plan) != digest(source):
            raise ValueError('Intentionally invalid geometry')
        return native(plan)
    result = optimize_cad(source, options(view_from_plan(target), parameter_ids=['top_thickness_mm']), builder=reject_changes)
    assert result.plan == source
    assert result.report['geometry_valid']
    assert any(not attempt['valid'] for attempt in result.report['attempts'])


def test_budget_includes_finite_difference_evaluations():
    source, target = table_plan(), table_plan(760)
    opts = options(view_from_plan(target), parameter_ids=['width_mm']).model_copy(update={'max_evaluations': 3})
    result = optimize_cad(source, opts)
    assert result.report['evaluations'] <= 3
    assert result.report['geometry_valid']


def test_exact_initial_match_stops_early_and_makes_no_ai_calls(monkeypatch):
    from app.ai.openai_client import ResponsesGateway
    def forbidden(*args, **kwargs):
        raise AssertionError('No AI call is permitted inside numerical optimization')
    monkeypatch.setattr(ResponsesGateway, 'structured', forbidden)
    source = table_plan()
    result = optimize_cad(source, options(view_from_plan(source), parameter_ids=['width_mm']))
    assert result.report['reason'] == 'target_error_reached'
    assert result.report['evaluations'] == 1
    assert result.report['additional_llm_calls'] == 0


def test_multiview_resolution_and_invisible_observations():
    source = table_plan()
    first = view_from_plan(source)
    second = view_from_plan(source, cam=camera(size=(2000, 2000)).model_copy(update={
        'intrinsic_matrix': [[1.6, 0, 1000], [0, 1.6, 1000], [0, 0, 1]]}))
    first.landmarks.append(first.landmarks[0].model_copy(update={'image_point': (0, 0), 'visible': False}))
    result = optimize_cad(source, CADOptimizationOptions(views=[first, second], parameter_ids=['width_mm']))
    assert result.report['initial_error'] < 1e-10


@pytest.mark.parametrize('perspective', [False, True])
def test_native_hlr_boundary_and_rays_agree_with_landmark_camera(perspective):
    source = table_plan()
    source.parts = source.parts[:1]  # single rectangular top, no occluding legs
    build, _ = NativeCADBuilder({}, 'Projection fixture')(source)
    projection = NativeProjection(build, camera(perspective))
    boundary = projection.silhouette()
    assert len(boundary) > 12
    # The box has no Y rotation; extreme X lies on a directly projected native vertex.
    vertices = [v.toTuple() for v in build.shapes['F001'].val().Vertices()]
    pixels = project_points(vertices, camera(perspective))
    assert abs(boundary[:, 0].min() - pixels[:, 0].min()) < 1
    assert abs(boundary[:, 0].max() - pixels[:, 0].max()) < 1
    assert np.isfinite(projection.hit_distance(pixels.mean(0)))
    assert not np.isfinite(projection.hit_distance((0, 0)))


def test_isolated_worker_has_a_hard_time_limit():
    import time
    source = table_plan()
    opts = options(view_from_plan(source)).model_copy(update={'max_seconds': .05})
    started = time.monotonic()
    result = optimize_cad_isolated(source, opts)
    assert time.monotonic() - started < 3
    assert result.plan == source
    assert result.report['reason'] == 'wall_time_budget'


def test_optimized_step_remains_compatible_with_existing_export_verifier(tmp_path):
    import cadquery as cq
    from app.engineering_cad import verify_solid_step
    source, target = table_plan(), table_plan(750)
    result = optimize_cad(source, options(view_from_plan(target), parameter_ids=['width_mm']))
    build, _ = NativeCADBuilder({}, 'STEP compatibility')(result.plan)
    for pid, workplane in build.shapes.items():
        path = tmp_path / (pid + '.step')
        cq.exporters.export(workplane, str(path))
        _, proof = verify_solid_step(workplane.val(), path)
        assert proof['valid_closed_solid']


def test_no_quantitative_evidence_and_disabled_mode_leave_geometry_unchanged():
    source = table_plan()
    assert optimize_cad(source).report['status'] == 'skipped'
    assert optimize_cad(source, {'enabled': False}).report['status'] == 'disabled'


def test_qualitative_mismatch_is_only_a_component_selector():
    result = optimize_cad(table_plan(), dict(feature_components={'V001': ['F002']}, mismatch_feature_ids=['V001']))
    assert result.report['status'] == 'skipped'
    assert result.report['initial_error'] is None


def test_cached_builder_reuses_unchanged_native_parts():
    builder = NativeCADBuilder({}, 'Cached table')
    source = table_plan()
    before, _ = builder(source)
    candidate = source.model_copy(deep=True)
    candidate.parts[-1].position[0] = 270.
    after, _ = builder(candidate)
    assert before.shapes['F001'] is after.shapes['F001']
    assert before.shapes['F005'] is not after.shapes['F005']


def test_native_parameter_relations_preserve_mirrored_placement():
    source = table_plan()
    source.parts[1].position[0] = -260.
    source.parts[3].position[0] = 260.
    opts = CADOptimizationOptions(component_ids=['F002', 'F004'],
        relations=[dict(left='F002.position.0', right='F004.position.0', factor=-1)])
    registry = CADParameterRegistry(source, [], opts)
    candidate = registry.apply({'F004.position.0': 275})
    assert candidate.parts[1].position[0] == -275
    assert candidate.parts[3].position[0] == 275


def test_native_bezier_chain_retains_existing_c1_continuity():
    source = curved_plan()
    source.parts[0].path = [[float(x), 0., 100.] for x in (-180, -120, -60, 0, 60, 120, 180)]
    registry = CADParameterRegistry(source, [], CADOptimizationOptions())
    candidate = registry.apply({'F001.path.2.2': 110})
    points = np.array(candidate.parts[0].path)
    assert np.allclose(points[3] - points[2], points[4] - points[3])


def test_camera_alignment_uses_fixed_world_correspondences_before_geometry():
    from app.cad_optimizer import align_camera
    from app.cad_optimizer_schema import CADLandmark
    cam = camera(True)
    world = np.array([[-100., -100, 0], [100, -100, 0], [-100, 100, 0],
                      [100, 100, 0], [-100, -100, 600], [100, 100, 700]])
    pixels = project_points(world, cam)
    uncertain = cam.model_copy(update={'translation_mm': (20, 350, 1600), 'reliable': False})
    view = CADViewEvidence(image_id=uuid4(), camera=uncertain, landmarks=[dict(part_id='F001',
        image_point=pixel.tolist(), fixed_world_point=point.tolist()) for point, pixel in zip(world, pixels)])
    selected, _ = align_camera(view)
    assert selected.camera.reliable
    assert np.max(np.abs(project_points(world, selected.camera) - pixels)) < .01


def test_iteration_limit_and_stagnation_are_bounded():
    target = table_plan(760)
    opts = options(view_from_plan(target), parameter_ids=['width_mm'], max_iterations=1)
    result = optimize_cad(table_plan(), opts)
    assert result.report['iterations'] <= 1
    assert result.report['evaluations'] <= 40


def test_contact_trimming_cannot_change_an_unselected_solid():
    from app.cad_optimizer import check_unselected_solids
    from types import SimpleNamespace
    import cadquery as cq
    original = cq.Workplane('XY').box(20, 20, 20)
    changed = cq.Workplane('XY').box(19, 20, 20)
    with pytest.raises(ValueError, match='unselected'):
        check_unselected_solids(SimpleNamespace(shapes={'F001': original}),
            SimpleNamespace(shapes={'F001': changed}), set())


def test_isolated_worker_returns_a_native_improvement():
    source, target = table_plan(), table_plan(750)
    result = optimize_cad_isolated(source, options(view_from_plan(target), parameter_ids=['width_mm']))
    assert result.report['status'] == 'improved', result.report
    assert result.report['final_error'] < result.report['initial_error']


def test_loft_section_landmark_resolves_parameters_and_component_transform():
    from app.cad_optimizer_schema import CADLandmark
    raw = curved_plan().model_dump(mode='json')
    part = raw['parts'][0]
    part.update(geometry='loft', path=[], position=[10, 20, 30], rotation_deg=[0, 0, 90],
        sections=[dict(center=['width_mm/4', 0, 0], width=20, depth=20),
                  dict(center=[0, 0, 'height_mm'], width=30, depth=30)])
    plan = PhotoDesign.model_validate(raw)
    mark = CADLandmark(part_id='F001', anchor='section_center', point_index=0, image_point=(0, 0))
    assert np.allclose(landmark_point(mark, plan, None), [10, 125, 30])


def test_table_reference_estimated_camera_and_existing_preview_projection_agree():
    from scripts.generate_table_a_fresh_local import estimate_camera, preview_camera
    from scripts.recreate_table_a_local import design
    from app.ai.reference_fitting import project
    cam, report = estimate_camera(design(), (1920, 1080))
    assert report['tabletop_corner_rms_px'] < 10
    assert not cam.reliable
    points = np.array([[-1400., -550, 750], [1400, 550, 750], [-1010, -358, 0], [1010, -358, 0]])
    assert np.allclose(project(points, preview_camera(cam), (1920, 1080)), project_points(points, cam), atol=1e-6)


def test_existing_real_photograph_requires_reliable_camera_before_geometry_fitting():
    # This is an actual catalog photo, not a CAD-derived synthetic image. No
    # calibrated pose is supplied, so geometry must not be optimized against it.
    from PIL import Image
    path = Path('tests/fixtures/cad_optimizer_real_photo.jpg')
    assert path.exists()
    with Image.open(path) as photo:
        size = photo.size
    view = CADViewEvidence(image_id=uuid4(), camera=CADCamera(image_size=size,
        intrinsic_matrix=[[900, 0, size[0]/2], [0, 900, size[1]/2], [0, 0, 1]],
        reliable=False, uncertainty='Actual photograph has no calibrated pose'),
        landmarks=[dict(part_id='F001', image_point=(88, 347))])
    result = optimize_cad(table_plan(), options(view))
    assert result.report['status'] == 'skipped'
    assert any('no calibrated pose' in message for message in result.report['warnings'])


def test_real_photo_manual_facade_landmarks_reduce_2d_error_with_explicit_camera_assumption():
    import json
    fixture = json.loads(Path('tests/fixtures/cad_optimizer_real_photo.json').read_text())
    source = PhotoDesign(furniture_type='catalog facade study', summary=fixture['limitations'],
        values=[dict(key=k, value=v, explanation='Assumed image-study dimension') for k, v in
                [('width_mm', 1400), ('depth_mm', 100), ('height_mm', 355)]],
        parts=[dict(part_id='F001', label='Front facade envelope', evidence='Manually annotated catalog facade',
            geometry='box', material='solid_wood', color_rgb=[.5, .3, .2],
            size=['width_mm', 'depth_mm', 'height_mm'], position=[0, 0, 'height_mm/2'])],
        assumptions=[fixture['limitations']])
    cam = camera(size=(800, 600)).model_copy(update={
        'intrinsic_matrix': [[.4, 0, 401.5], [0, .4, 300], [0, 0, 1]],
        'translation_mm': (0, 472.5, 1500), 'reliable': False,
        'uncertainty': 'Approximate frontal camera and assumed scale; not calibrated'})
    corners = [(-.5, -.5, .5), (.5, -.5, .5), (.5, -.5, -.5), (-.5, -.5, -.5)]
    view = CADViewEvidence(image_id=uuid4(), camera=cam, landmarks=[dict(part_id='F001',
        anchor='size_corner', local_point=corner, image_point=pixel) for corner, pixel in
        zip(corners, fixture['front_facade_pixels'])])
    result = optimize_cad(source, options(view, parameter_ids=['width_mm'], allow_uncertain_camera=True),
        inputs={'known_values': [dict(key='height_mm', value=355)]})
    assert result.report['status'] == 'improved', result.report
    assert result.report['final_error'] < result.report['initial_error'] * .1
    assert result.report['warnings'] and not result.report['visual_match_verified']
