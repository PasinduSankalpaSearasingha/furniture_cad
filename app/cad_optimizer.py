"""CPU-only native CAD optimization. This module has no AI gateway or network client."""
from dataclasses import dataclass
import hashlib
import json
import time
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial import cKDTree
from app.cad_optimizer_schema import CADOptimizationOptions
from app.cad_parameters import CADParameterRegistry
from app.cad_projection import project_points, landmark_point, curve_points, NativeProjection
from app.photo_schema import PhotoDesign

VERSION = 'NATIVE_BREP_OPTIMIZER_V1'


def optimize_mesh_cad(plan, reference, options=None, **kwargs):
    """Camera-free global surface fitting; photograph protection stays optional."""
    from .dense_cad_optimizer import optimize_dense_cad
    return optimize_dense_cad(plan, reference, options, **kwargs)


def digest(plan):
    return hashlib.sha256(json.dumps(plan.model_dump(mode='json'), sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class OptimizationStop(Exception):
    pass


@dataclass
class OptimizationResult:
    plan: PhotoDesign
    report: dict


def empty_report(plan, status, reason):
    return dict(version=VERSION, status=status, reason=reason, initial_error=None, final_error=None,
        error_unit='image_diagonal_normalized_pixel_distance', evaluations=0, iterations=0,
        additional_llm_calls=0, geometry_valid=None, fixed_dimensions_preserved=True,
        processing_seconds=0., original_plan_sha256=digest(plan), selected_plan_sha256=digest(plan),
        revisions=[], attempts=[], camera_updates=[], remaining_mismatches=[], warnings=[],
        compute=dict(device='CPU', projection='OpenCascade B-Rep HLR and native ray intersections', ai_requests=0))


class NativeCADBuilder:
    """Reuse the existing builder, native solids, and complete engineering checks."""
    def __init__(self, inputs, name):
        self.inputs, self.name, self.cache = inputs, name, {}

    def __call__(self, plan):
        from app.agents.photo_agent import specification
        from app.photo_cad import create_photo_furniture
        from app.engineering_cad import geometry_checks, check_supplied_dimensions
        from OCP.BRepCheck import BRepCheck_Analyzer
        spec, _ = specification(plan, self.inputs, self.name)
        build = create_photo_furniture(spec, geometry_cache=self.cache)
        for shape in build.shapes.values():
            if not BRepCheck_Analyzer(shape.val().wrapped).IsValid():
                raise ValueError('Invalid B-Rep topology')
        checks = geometry_checks(build)
        checks['supplied_dimensions'] = check_supplied_dimensions(build, self.inputs.get('known_values', []))
        if not checks.get('furniture_items_connected', checks['assembly_connected']):
            raise ValueError('Lost required furniture component connectivity')
        return build, checks


def boundary_distances(predicted, observed, *, symmetric=True, observed_tree=None):
    """Bidirectional nearest-boundary distances in pixel units, with fixed size."""
    predicted, observed = np.asarray(predicted), np.asarray(observed)
    if len(predicted) < 2 or len(observed) < 2:
        raise ValueError('Insufficient numerical curve/boundary evidence')
    forward = cKDTree(predicted).query(observed)[0]
    if not symmetric:
        return forward
    reverse = (observed_tree or cKDTree(observed)).query(predicted)[0]
    # A fixed number of quantiles keeps residual dimensions stable as HLR splits edges.
    return np.concatenate([forward, np.quantile(reverse, np.linspace(0, 1, 16))])


def check_unselected_solids(baseline, candidate, selected):
    """Contact trimming must not silently remodel an unselected component."""
    from app.geometry import exact_bounds
    preserved = []
    for pid, original in baseline.shapes.items():
        if pid in selected:
            continue
        before, after = original.val(), candidate.shapes[pid].val()
        if original is candidate.shapes[pid]:
            preserved.append(pid)
            continue
        a, b = exact_bounds(before), exact_bounds(after)
        tolerance = max(1e-5, before.Volume() * 1e-9)
        same = all(abs(getattr(a, key) - getattr(b, key)) < 1e-6
            for key in ('xmin', 'xmax', 'ymin', 'ymax', 'zmin', 'zmax'))
        same &= abs(before.Volume() - after.Volume()) <= tolerance
        if same:
            same = before.cut(after).Volume() + after.cut(before).Volume() <= tolerance
        if not same:
            raise ValueError('Contact trimming changed an unselected native solid')
        preserved.append(pid)
    return preserved


def align_camera(view):
    """Calibrate only from fixed world correspondences, never movable CAD controls."""
    import cv2
    camera = view.camera
    marks = [m for m in view.landmarks if m.visible and m.fixed_world_point is not None]
    if camera is None or camera.projection != 'perspective' or len(marks) < 6:
        return view, 'Camera alignment needs six fixed 3D-to-2D correspondences'
    world = np.array([m.fixed_world_point for m in marks], dtype=float)
    if np.linalg.matrix_rank(world - world.mean(0)) < 3:
        return view, 'Camera alignment points do not constrain a nonplanar pose'
    pixels = np.array([m.image_point for m in marks], dtype=float)
    ok, rotation, translation = cv2.solvePnP(world, pixels, np.array(camera.intrinsic_matrix, dtype=float),
        np.array(camera.distortion, dtype=float) if camera.distortion else None, flags=cv2.SOLVEPNP_SQPNP)
    if not ok:
        return view, 'Camera calibration failed'
    updated = camera.model_copy(update=dict(rotation_vector=tuple(rotation.ravel()), translation_mm=tuple(translation.ravel())))
    error = np.sqrt(np.mean(np.sum((project_points(world, updated) - pixels) ** 2, axis=1)))
    reliable = error / np.hypot(*camera.image_size) < .003
    updated = updated.model_copy(update=dict(reliable=reliable, uncertainty=f'Fixed-landmark reprojection RMS {error:.4f} px'))
    return view.model_copy(update={'camera': updated}), updated.uncertainty


def optimize_cad(plan, options=None, *, inputs=None, name='Local CAD optimization', builder=None, progress=None, publish_best=None):
    """Cooperative in-process core. Use optimize_cad_isolated for hard wall limits.

    Every objective invocation (including finite differences) spends one budget
    evaluation. Only fully valid, strictly improving native models are retained.
    """
    started = time.monotonic()
    source = PhotoDesign.model_validate(plan)
    options = CADOptimizationOptions.model_validate(options or {})
    inputs = inputs or {}
    report = empty_report(source, 'skipped', 'No usable quantitative reference evidence')
    report['remaining_mismatches'] = list(options.mismatch_feature_ids)
    if not options.enabled:
        report.update(status='disabled', reason='CAD optimization is disabled')
        return OptimizationResult(source, report)
    max_evaluations, seconds, max_parameters = options.budgets()
    deadline = started + seconds
    evaluations, iterations, last_improvement = 0, 0, 0
    stop_reason = 'completed'
    def check_budget():
        if time.monotonic() >= deadline:
            raise OptimizationStop('wall_time_budget')
    views = []
    for view in options.views:
        if options.align_cameras:
            view, message = align_camera(view)
            report['camera_updates'].append(dict(image_id=str(view.image_id), message=message,
                camera=view.camera.model_dump(mode='json') if view.camera else None))
        if view.camera is None:
            report['warnings'].append(f'{view.image_id}: no calibrated camera; view skipped')
            continue
        if not view.camera.reliable:
            report['warnings'].append(f'{view.image_id}: {view.camera.uncertainty}')
            if not options.allow_uncertain_camera:
                continue
        usable = (any(m.visible for m in view.landmarks) or any(c.visible for c in view.curves) or
                  bool(view.silhouette and view.silhouette_reviewed))
        if usable:
            views.append(view)
    if not views:
        return OptimizationResult(source, report)
    if not options.component_ids and not options.mismatch_feature_ids and not any(
            view.silhouette and view.silhouette_reviewed for view in views):
        observed = {feature.part_id for view in views for feature in [*view.landmarks, *view.curves] if feature.visible}
        options = options.model_copy(update={'component_ids': sorted(observed)})
    try:
        registry = CADParameterRegistry(source, inputs.get('known_values', []), options)
    except ValueError as exc:
        report.update(status='rejected', reason=str(exc))
        return OptimizationResult(source, report)
    report['parameters'] = [p.report() for p in registry.parameters.values()]
    available = {p.part_id for p in source.parts}
    if any(m.part_id not in available for v in views for m in [*v.landmarks, *v.curves]):
        report.update(status='rejected', reason='Numerical evidence refers to an unknown CAD component')
        return OptimizationResult(source, report)
    build_geometry = builder or NativeCADBuilder(inputs, name)
    try:
        baseline, baseline_checks = build_geometry(source)
        report['geometry_valid'] = True
    except Exception as exc:
        report.update(status='invalid_initial_geometry', reason=f'Initial CAD validation failed ({type(exc).__name__})')
        return OptimizationResult(source, report)
    required_contacts = {tuple(sorted((p['a'], p['b']))) for p in baseline_checks.get('contacts', [])}
    required_contacts.update(tuple(sorted(pair)) for pair in options.required_contacts)
    active = registry.candidates()[:max_parameters]
    # Resolve numerical evidence once; qualitative feature IDs only choose controls.
    prepared = [(v, np.hypot(*v.camera.image_size)) for v in views]
    target_trees = {}
    for view in views:
        for index, curve in enumerate(view.curves):
            target_trees[(str(view.image_id), index)] = cKDTree(curve.image_points)
        if view.silhouette:
            target_trees[(str(view.image_id), 'silhouette')] = cKDTree(view.silhouette)
    best_plan, best_checks, best_updates = source, baseline_checks, {}
    best_error, best_total = np.inf, np.inf
    residual_size = None
    result_cache = {}
    def evaluate(updates):
        nonlocal evaluations, last_improvement, best_plan, best_checks, best_updates
        nonlocal best_error, best_total, residual_size
        check_budget()
        if evaluations >= max_evaluations:
            raise OptimizationStop('evaluation_budget')
        evaluations += 1
        if progress:
            progress(evaluations, max_evaluations)
        candidate_hash = None
        try:
            candidate = registry.apply(updates)
            candidate_hash = digest(candidate)
            cached = result_cache.get(candidate_hash)
            if cached is not None:
                return cached
            build, checks = (baseline, baseline_checks) if candidate_hash == digest(source) else build_geometry(candidate)
            check_budget()
            contacts = {tuple(sorted((p['a'], p['b']))) for p in checks.get('contacts', [])}
            if not required_contacts <= contacts:
                raise ValueError('Candidate lost a required component contact')
            checks = dict(checks, unselected_parts_preserved=check_unselected_solids(baseline, build, registry.selected))
            for relation in options.relations:
                # Registry derives the left side, with bounds and locks enforced.
                if relation.left not in registry.parameters:
                    raise ValueError('Unresolved geometric relation')
            residuals, measured = [], []
            terms = dict(landmark=[], silhouette=[], curve=[])
            for view, diagonal in prepared:
                check_budget()
                visibility = None
                for mark in view.landmarks:
                    if not mark.visible:
                        continue
                    point = mark.fixed_world_point if mark.fixed_world_point is not None else landmark_point(mark, candidate, build)
                    delta = (project_points([point], view.camera)[0] - mark.image_point) / diagonal
                    weight = np.sqrt(options.weights.landmark * view.confidence * mark.confidence)
                    terms['landmark'].extend((delta * weight).tolist())
                for index, curve in enumerate(view.curves):
                    if not curve.visible:
                        continue
                    points = curve_points(curve, candidate, build)
                    if curve.source == 'edge':
                        visibility = visibility or NativeProjection(build, view.camera)
                        points = points[visibility.visible(points)]
                    distances = boundary_distances(project_points(points, view.camera), curve.image_points,
                        observed_tree=target_trees[(str(view.image_id), index)]) / diagonal
                    terms['curve'].extend((distances * np.sqrt(options.weights.curve * view.confidence * curve.confidence)).tolist())
                if view.silhouette and view.silhouette_reviewed:
                    visibility = visibility or NativeProjection(build, view.camera)
                    projected = visibility.silhouette(check_budget)
                    distances = boundary_distances(projected, view.silhouette, symmetric=view.silhouette_complete,
                        observed_tree=target_trees[(str(view.image_id), 'silhouette')]) / diagonal
                    terms['silhouette'].extend((distances * np.sqrt(options.weights.silhouette *
                        view.confidence * view.silhouette_confidence)).tolist())
            for values in terms.values():
                if values:
                    # Equal per-category mean-square weight; image resolution cannot dominate.
                    normalized = np.asarray(values) / np.sqrt(len(values))
                    measured.extend(normalized)
                    residuals.extend(normalized)
            error = float(np.linalg.norm(measured))
            for parameter in active:
                delta = updates.get(parameter.parameter_id, parameter.current_value) - parameter.current_value
                residuals.append(np.sqrt(options.weights.regularization) * delta /
                    max(parameter.maximum - parameter.minimum, 1e-6))
            residuals.append(0. * options.weights.constraint)  # Hard-invalid candidates never enter the feasible set.
            residuals = np.asarray(residuals)
            residual_size = len(residuals)
            total = float(np.dot(residuals, residuals))
            if report['initial_error'] is None:
                report['initial_error'] = error
            if total < best_total and error <= best_error:
                improvement = best_error - error
                if np.isfinite(best_error) and improvement >= options.minimum_improvement:
                    changes = parameter_changes(registry, candidate)
                    report['revisions'].append(dict(revision_id=candidate_hash, previous_revision_id=digest(best_plan),
                        modified_parameters=changes, initial_error=best_error, final_error=error,
                        validation=checks, processing_seconds=time.monotonic() - started,
                        evaluations=evaluations, additional_llm_calls=0, accepted=True,
                        reason='Lower measured geometric error and valid native CAD'))
                    last_improvement = evaluations
                best_plan, best_checks, best_updates = candidate, checks, dict(updates)
                best_error, best_total = error, total
                if publish_best and report['revisions']:
                    snapshot = dict(report, status='improved', final_error=error, evaluations=evaluations,
                        validation=checks, selected_plan_sha256=candidate_hash, geometry_valid=True,
                        modified_parameters=parameter_changes(registry, candidate))
                    publish_best(candidate, snapshot)
            report['attempts'].append(dict(evaluation=evaluations, plan_sha256=candidate_hash, valid=True, error=error))
            result_cache[candidate_hash] = residuals
            if len(result_cache) > 16:
                result_cache.pop(next(iter(result_cache)))
            return residuals
        except OptimizationStop:
            raise
        except Exception as exc:
            # Exceptions may contain kernel detail, never echo arbitrary inputs or secrets.
            report['attempts'].append(dict(evaluation=evaluations, plan_sha256=candidate_hash,
                valid=False, reason=f'Native CAD/evidence validation rejected candidate ({type(exc).__name__})'))
            if residual_size is None:
                raise ValueError('Initial evidence could not be projected against the native CAD') from exc
            return np.full(residual_size, 100. * max(1., options.weights.constraint))
    try:
        initial = evaluate({})
        if np.linalg.norm(initial) <= options.target_error:
            stop_reason = 'target_error_reached'
        elif not active:
            stop_reason = 'no_unlocked_parameters'
        else:
            for phase in ('global', 'component', 'position', 'curve'):
                controls = [p for p in active if p.phase == phase]
                if not controls:
                    continue
                check_budget()
                fixed = dict(best_updates)
                def objective(x):
                    updates = dict(fixed)
                    for parameter, unit_value in zip(controls, x):
                        updates[parameter.parameter_id] = parameter.current_value + unit_value * (parameter.maximum - parameter.minimum)
                    result = evaluate(updates)
                    if best_error <= options.target_error:
                        raise OptimizationStop('target_error_reached')
                    if evaluations - last_improvement >= options.stagnation_evaluations:
                        raise OptimizationStop('stagnation')
                    return result
                lower = [(p.minimum - p.current_value) / (p.maximum - p.minimum) for p in controls]
                upper = [(p.maximum - p.current_value) / (p.maximum - p.minimum) for p in controls]
                remaining_phases = sum(any(p.phase == q for p in active) for q in
                    ('global', 'component', 'position', 'curve')[('global', 'component', 'position', 'curve').index(phase):])
                allowance = max(2, (max_evaluations - evaluations) // max(1, remaining_phases))
                start_phase = evaluations
                phase_iterations = 0
                def limited_objective(x):
                    if evaluations - start_phase >= allowance:
                        raise OptimizationStop('phase_budget')
                    return objective(x)
                def callback(intermediate_result):
                    nonlocal iterations, phase_iterations
                    iterations += 1
                    phase_iterations += 1
                    if iterations >= options.max_iterations:
                        raise OptimizationStop('iteration_budget')
                try:
                    least_squares(limited_objective, np.zeros(len(controls)), bounds=(lower, upper),
                        loss='soft_l1', f_scale=.01, diff_step=.002, max_nfev=allowance,
                        ftol=1e-7, xtol=1e-6, gtol=1e-9, callback=callback)
                except OptimizationStop as exc:
                    if str(exc) != 'phase_budget':
                        raise
    except OptimizationStop as exc:
        stop_reason = str(exc)
    except Exception as exc:
        stop_reason = f'optimization_failure ({type(exc).__name__})'
        report['warnings'].append('Optimization stopped safely; the best fully validated CAD was retained')
    improved = (report['initial_error'] is not None and np.isfinite(best_error) and
                report['initial_error'] - best_error >= options.minimum_improvement and digest(best_plan) != digest(source))
    if not improved:
        best_plan, best_checks = source, baseline_checks
        best_error = report['initial_error']
    # Independent final full rebuild/validation before publishing the winner.
    if improved:
        try:
            check_budget()
            _, best_checks = build_geometry(best_plan)
            check_budget()
        except OptimizationStop:
            report['warnings'].append('Time limit reached; the best already validated candidate is retained for final pipeline checks')
        except Exception:
            report['warnings'].append('Final validation failed or exceeded time; reverted to the original CAD')
            best_plan, best_checks, best_error, improved = source, baseline_checks, report['initial_error'], False
    report.update(status='improved' if improved else 'unchanged', reason=stop_reason,
        final_error=best_error, evaluations=evaluations, iterations=iterations,
        geometry_valid=True, fixed_dimensions_preserved=True, processing_seconds=time.monotonic() - started,
        selected_plan_sha256=digest(best_plan), validation=best_checks,
        modified_parameters=parameter_changes(registry, best_plan),
        # A numerical improvement never clears an unreviewed qualitative finding.
        remaining_mismatches=options.mismatch_feature_ids,
        remaining_error=best_error, visual_match_verified=False)
    return OptimizationResult(best_plan, report)


def parameter_changes(registry, candidate):
    raw = candidate.model_dump(mode='json')
    values = {v.key: v.value for v in candidate.values}
    result = []
    for pid, parameter in registry.parameters.items():
        if parameter.path[0] == 'values':
            value = values[pid]
        else:
            value = raw
            for key in parameter.path:
                value = value[key]
            from app.photo_schema import number
            value = number(value, values)
        if value != parameter.current_value:
            result.append(dict(parameter_id=pid, component_ids=parameter.component_ids,
                previous_value=parameter.current_value, updated_value=value, unit=parameter.unit,
                evidence_ids=parameter.evidence_ids))
    return result


def optimize_cad_isolated(plan, options=None, *, inputs=None, name='Local CAD optimization'):
    """Worker isolation enforces wall limits even inside a stalled native operation."""
    import os
    from pathlib import Path
    import subprocess
    import sys
    from tempfile import TemporaryDirectory
    options = CADOptimizationOptions.model_validate(options or {})
    plan = PhotoDesign.model_validate(plan)
    if not options.enabled or not options.views:
        return optimize_cad(plan, options, inputs=inputs, name=name)
    started = time.monotonic()
    seconds = options.budgets()[1]
    report = empty_report(plan, 'unchanged', 'wall_time_budget')
    process = None
    best_plan = plan
    try:
        with TemporaryDirectory(prefix='fiske-native-optimization-') as directory:
            path = Path(directory)
            request, events = path/'request.json', path/'events.jsonl'
            request.write_text(json.dumps(dict(plan=plan.model_dump(mode='json'),
                options=options.model_dump(mode='json'), inputs=inputs or {}, name=name), allow_nan=False), encoding='utf-8')
            events.touch()
            # An ordinary child executable also works inside Celery's daemonic
            # workers, where multiprocessing.Process cannot start children.
            process = subprocess.Popen([sys.executable, '-m', 'app.cad_optimizer_worker', str(request), str(events)],
                cwd=str(Path(__file__).resolve().parent.parent), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env={**os.environ, 'OMP_NUM_THREADS':'1', 'OPENBLAS_NUM_THREADS':'1', 'MKL_NUM_THREADS':'1'},
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            try:
                with events.open(encoding='utf-8') as stream:
                    pending = ''
                    while time.monotonic() - started < seconds:
                        pending += stream.read()
                        lines = pending.split('\n')
                        pending = lines.pop()
                        for line in lines:
                            message = json.loads(line)
                            if message['kind'] == 'progress':
                                report['evaluations'] = message['evaluations']
                            elif message['kind'] == 'best':
                                best_plan = PhotoDesign.model_validate(message['plan'])
                                report = message['report']
                                report['reason'] = 'wall_time_budget'
                            elif message['kind'] == 'result':
                                result = OptimizationResult(PhotoDesign.model_validate(message['plan']), message['report'])
                                result.report['processing_seconds'] = time.monotonic() - started
                                return result
                            else:
                                report['reason'] = 'isolated_worker_failed (' + message['error'] + ')'
                                return OptimizationResult(best_plan, report)
                        if process.poll() is not None:
                            # Drain the writer's final flushed event before deciding
                            # that a worker exited without a result.
                            pending += stream.read()
                            if '\n' in pending:
                                continue
                            report['reason'] = 'isolated_worker_exited'
                            break
                        time.sleep(min(.03, max(0., seconds - (time.monotonic() - started))))
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=1)
    except Exception as exc:
        report['reason'] = f'isolated_worker_failed ({type(exc).__name__})'
    report['processing_seconds'] = time.monotonic() - started
    report['warnings'].append('Worker stopped; best completed CAD candidate retained for normal engineering validation')
    return OptimizationResult(best_plan, report)
