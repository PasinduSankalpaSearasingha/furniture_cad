"""Two-speed surface fitting over existing native CAD parameters. No network I/O."""
from dataclasses import dataclass
import time
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation
from scipy.interpolate import RectBivariateSpline
from .cad_parameters import CADParameterRegistry
from .geometry_acceleration import SurfaceIndex, deviation_statistics
from .geometry_cache import GeometryCache, ResourceBudget
from .mesh_analyzer import analyze_mesh
from .mesh_sampling import STAGES, sample_surface
from .mesh_to_cad import build_native, tessellate_build
from .mesh_cad_schema import DenseOptimizationOptions
from .photo_schema import PhotoDesign, number


@dataclass
class DenseOptimizationResult:
    plan: PhotoDesign
    build: object
    report: dict
    reference: object
    deviation_points: np.ndarray
    deviation_mm: np.ndarray


class FitBudgetStop(Exception):
    pass


from .cad_geometry_proxy import ComponentProxy, resolved_frame


def assign_reference_components(reference, cad, threads=4):
    closest = SurfaceIndex(cad, threads=threads).closest(reference.mesh.triangles_center)
    labels = cad.face_components[closest['face_ids']]
    result = analyze_mesh(reference.mesh, labels)
    return result


def sensitive_parameters(registry, plan, cad_samples, maximum, base_updates=None):
    """Bounded mathematical probes avoid spending solves on inert controls."""
    from dataclasses import replace
    ids = np.linspace(0, len(cad_samples.points)-1, min(512, len(cad_samples.points)), dtype=int)
    subset = replace(cad_samples, **{name: getattr(cad_samples, name)[ids] for name in
        ('points', 'normals', 'face_ids', 'component_ids', 'area_weights')})
    proxy = ComponentProxy(plan, subset)
    initial, _ = proxy.evaluate(plan)
    measured = []
    base_updates=base_updates or {}
    phases = ('global', 'position', 'component', 'curve')
    candidates = registry.candidates()
    probe_candidates = [p for phase in phases for p in [item for item in candidates if item.phase == phase][:24]]
    for parameter in probe_candidates:
        current=base_updates.get(parameter.parameter_id,parameter.current_value)
        step=max(.01,(parameter.maximum-parameter.minimum)*.03)
        for direction in (1,-1):
            value=float(np.clip(current+direction*step,parameter.minimum,parameter.maximum))
            if value==current: continue
            try:
                changed=registry.apply({**base_updates,parameter.parameter_id:value})
                points,_=proxy.evaluate(changed)
                sensitivity=float(np.sqrt(np.mean(np.sum((points-initial)**2,axis=1)))/abs(value-current))
                if sensitivity>1e-6: measured.append((parameter,sensitivity))
                break
            except ValueError:
                continue
    phase = {'global':0, 'position':1, 'component':2, 'curve':3}
    measured.sort(key=lambda item: (phase[item[0].phase], -item[1]))
    pools = {name: [p for p, _ in measured if p.phase == name] for name in phases}
    selected = []
    while len(selected) < maximum and any(pools.values()):
        for name in phases:
            if pools[name] and len(selected) < maximum:
                selected.append(pools[name].pop(0))
    return selected, {p.parameter_id: sensitivity for p, sensitivity in measured}


def bidirectional_report(reference_index, cad, reference_samples, count, budget, cache, component_aware):
    cad_samples = sample_surface(cad, count, budget=budget, cache=cache)
    cad_index = SurfaceIndex(cad, threads=budget.workers)
    forward = reference_index.closest(cad_samples.points, cad_samples.component_ids if component_aware else None)
    reverse = cad_index.closest(reference_samples.points, reference_samples.component_ids if component_aware else None)
    a, b = forward['distances'], reverse['distances']
    normal_error = 1 - np.clip(np.einsum('ij,ij->i', cad_samples.normals, forward['normals']), -1, 1)
    per_component = {}
    for component in np.unique(reference_samples.component_ids):
        selected = reference_samples.component_ids == component
        per_component[str(int(component))] = deviation_statistics(b[selected], reference_samples.area_weights[selected])
    report = dict(cad_to_reference=deviation_statistics(a, cad_samples.area_weights),
        reference_to_cad=deviation_statistics(b, reference_samples.area_weights), components=per_component,
        symmetric_mean_mm=float((np.sum(a*cad_samples.area_weights)+np.sum(b*reference_samples.area_weights))*.5),
        normal_loss=float(np.sum(normal_error*cad_samples.area_weights)),
        distance_method='Open3D closest triangle surface on temporary CAD tessellation',
        cad_tessellation_tolerance_mm=.25, reference_points=len(b), cad_points=len(a))
    return report, reverse


def photo_error(plan, build, views):
    """Reviewed image evidence uses the existing native projection engine."""
    from .cad_projection import NativeProjection, project_points, landmark_point, curve_points
    from .cad_optimizer import boundary_distances
    errors = []
    evidence = []
    for view in views:
        if view.camera is None or not view.camera.reliable:
            evidence.append(dict(image_id=str(view.image_id), status='unverified', reason='A reliable photo camera is required'))
            continue
        diagonal = np.hypot(*view.camera.image_size)
        local = []
        for landmark in view.landmarks:
            if landmark.visible:
                projected = project_points([landmark_point(landmark, plan, build)], view.camera)[0]
                local.append(float(np.linalg.norm(projected-landmark.image_point)/diagonal)*landmark.confidence)
        for curve in view.curves:
            if curve.visible:
                projected = project_points(curve_points(curve, plan, build), view.camera)
                local.extend((boundary_distances(projected, curve.image_points)/diagonal*curve.confidence).tolist())
        if view.silhouette and view.silhouette_reviewed:
            # The established projection API supplies exact B-Rep HLR contours.
            projection = NativeProjection(build, view.camera)
            predicted = projection.silhouette()
            local.extend((boundary_distances(predicted, view.silhouette)/diagonal).tolist())
        if local:
            errors.extend(local)
            evidence.append(dict(image_id=str(view.image_id), status='measured', error=float(np.mean(local))))
    return (float(np.mean(errors)) if errors else None), evidence


def optimize_dense_cad(plan, reference, options=None, *, inputs=None, cache=None, progress=None, photo_bytes=None, publish_best=None):
    """Backward-compatible entry point for the camera-free global solver."""
    from .global_cad_optimizer import optimize_global_cad
    return optimize_global_cad(plan, reference, options, inputs=inputs, cache=cache,
        progress=progress, photo_bytes=photo_bytes, publish_best=publish_best)
