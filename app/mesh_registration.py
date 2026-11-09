"""Uniform scale calibration and separate rigid multi-scale ICP registration."""
from dataclasses import dataclass
import numpy as np
from scipy.spatial import cKDTree
from .mesh_analyzer import analyze_mesh


def normalize_mesh(data, known_dimensions=None, *, estimated_height_mm=750, up_axis='Y'):
    known_dimensions = known_dimensions or {}
    if not set(known_dimensions) <= {'width_mm', 'depth_mm', 'height_mm'}:
        raise ValueError('Calibration accepts overall width, depth and height only')
    mesh = data.mesh.copy()
    transform = np.eye(4)
    if up_axis == 'Y':  # GLB uses Y-up; native furniture CAD uses Z-up.
        transform[:3, :3] = [[1, 0, 0], [0, 0, -1], [0, 1, 0]]
    elif up_axis != 'Z':
        raise ValueError('Reference up-axis must be Y or Z')
    mesh.apply_transform(transform)
    spans = np.ptp(mesh.bounds, axis=0)
    if np.any(spans < 1e-8):
        raise ValueError('Reference has no three-dimensional extent')
    ratios = []
    for key, value in known_dimensions.items():
        if not np.isfinite(value) or value <= 0:
            raise ValueError('Confirmed dimensions must be finite and positive')
        axis = {'width_mm': 0, 'depth_mm': 1, 'height_mm': 2}[key]
        ratios.append(value / spans[axis])
    scale = float(np.median(ratios)) if ratios else estimated_height_mm / spans[2]
    # Distorting a generated mesh independently on each axis would conceal
    # inconsistent measurements. Keep uniform physical calibration explicit.
    conflicts = [key for key, value in known_dimensions.items()
        if abs(spans[{'width_mm': 0, 'depth_mm': 1, 'height_mm': 2}[key]]*scale-value) > max(.01, value*.02)]
    if conflicts:
        raise ValueError('Confirmed dimensions conflict with mesh proportions: ' + ', '.join(conflicts))
    shift = -np.array([mesh.bounds[:, 0].mean(), mesh.bounds[:, 1].mean(), mesh.bounds[0, 2]]) * scale
    adjustment = np.eye(4)
    adjustment[:3, :3] *= scale
    adjustment[:3, 3] = shift
    mesh.apply_transform(adjustment)
    result = analyze_mesh(mesh, data.face_components)
    report = dict(transform=(adjustment@transform).tolist(), uniform_scale=scale, units='mm',
                  calibration='confirmed dimensions' if ratios else 'estimated height',
                  confirmed_dimensions=known_dimensions, physical_accuracy_verified=False)
    return result, report


def rigid_transform(source, target):
    a, b = np.asarray(source, dtype=float), np.asarray(target, dtype=float)
    ca, cb = a.mean(0), b.mean(0)
    u, _, vt = np.linalg.svd((a-ca).T@(b-cb))
    rotation = vt.T @ u.T
    if np.linalg.det(rotation) < 0:
        vt[-1] *= -1
        rotation = vt.T @ u.T
    result = np.eye(4)
    result[:3, :3], result[:3, 3] = rotation, cb - rotation@ca
    return result


def coarse_orientation(source, target):
    """Try the 24 proper axis orientations before local ICP; no deformation."""
    from itertools import permutations, product
    source, target = np.asarray(source, dtype=float), np.asarray(target, dtype=float)
    a = source[np.linspace(0, len(source)-1, min(1500, len(source)), dtype=int)]
    b = target[np.linspace(0, len(target)-1, min(3000, len(target)), dtype=int)]
    tree = cKDTree(b)
    center_a, center_b = a.mean(0), b.mean(0)
    best = None
    for order in permutations(range(3)):
        for signs in product((-1, 1), repeat=3):
            rotation = np.eye(3)[list(order)]*np.array(signs)[:, None]
            if np.linalg.det(rotation) < 0:
                continue
            translation = center_b-rotation@center_a
            distances = tree.query(a@rotation.T+translation)[0]
            score = np.mean(np.minimum(distances, np.quantile(distances, .9))**2)
            if best is None or score < best[0]:
                transform = np.eye(4); transform[:3, :3] = rotation; transform[:3, 3] = translation
                best = score, transform
    return best[1]


def align_reference_mesh(reference, cad, *, count=25_000, budget=None, cache=None):
    """Fix one global rigid frame before allowing any CAD shape controls."""
    from .mesh_sampling import sample_surface
    from .geometry_acceleration import SurfaceIndex
    source = sample_surface(reference,count,budget=budget,cache=cache)
    target = sample_surface(cad,count,budget=budget,cache=cache)
    threads=budget.workers if budget is not None else 4
    ref_index = SurfaceIndex(reference,threads=threads)
    cad_index = SurfaceIndex(cad,threads=threads)
    def surface_error(transform):
        moved = source.points@transform[:3,:3].T+transform[:3,3]
        inverse = (target.points-transform[:3,3])@transform[:3,:3]
        return .5*(np.mean(cad_index.closest(moved)['distances'])+np.mean(ref_index.closest(inverse)['distances']))
    initial_error = surface_error(np.eye(4))
    initial = coarse_orientation(source.points,target.points)
    ids=reference.report.get('native_part_ids')
    if ids and ids==cad.report.get('native_part_ids') and len(ids)>=3:
        labels=sorted(set(source.component_ids)&set(target.component_ids))
        if len(labels)>=3:
            centers_a=np.array([source.points[source.component_ids==label].mean(0) for label in labels])
            centers_b=np.array([target.points[target.component_ids==label].mean(0) for label in labels])
            informed=rigid_transform(centers_a,centers_b)
            if surface_error(informed)<=surface_error(initial): initial=informed
    transform,report = register_points(source.points,target.points,initial=initial,iterations=20)
    final_error = surface_error(transform)
    if final_error >= initial_error:
        transform = np.eye(4)
        final_error = initial_error
    mesh = reference.mesh.copy(); mesh.apply_transform(transform)
    result = analyze_mesh(mesh,reference.face_components)
    if 'native_part_ids' in reference.report:
        result.report['native_part_ids'] = reference.report['native_part_ids']
    report.update(reference_to_cad_transform=transform.tolist(),coarse_orientation_candidates=24,
        initial_surface_mean_mm=float(initial_error),final_surface_mean_mm=float(final_error),
        shape_parameters_optimized=False,scale_optimized=False)
    return result,report


def register_points(source, target, *, initial=None, iterations=30, trim_fraction=.9):
    source, target = np.asarray(source, dtype=float), np.asarray(target, dtype=float)
    if min(len(source), len(target)) < 4 or not np.isfinite(source).all() or not np.isfinite(target).all():
        raise ValueError('Registration requires finite point sets with at least four points')
    transform = np.eye(4) if initial is None else np.array(initial, dtype=float)
    if initial is None:
        transform[:3, 3] = target.mean(0) - source.mean(0)
    tree = cKDTree(target)
    errors = []
    for fraction in (.15, .4, 1.):
        subset = source[np.linspace(0, len(source)-1, max(4, int(len(source)*fraction)), dtype=int)]
        previous = np.inf
        for _ in range(iterations):
            moved = subset@transform[:3, :3].T + transform[:3, 3]
            distance, ids = tree.query(moved)
            keep = distance <= np.quantile(distance, trim_fraction)
            if keep.sum() < 4:
                break
            delta = rigid_transform(moved[keep], target[ids[keep]])
            transform = delta@transform
            error = float(np.mean(distance[keep]**2)**.5)
            errors.append(error)
            if abs(previous-error) < 1e-7:
                break
            previous = error
    return transform, dict(method='trimmed multiscale rigid point-to-point ICP', rmse_mm=errors[-1],
                           iterations=len(errors), scale_optimized=False)
