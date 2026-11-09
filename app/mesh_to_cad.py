"""Reuse the existing feature specification and native photo CAD builder."""
import json
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation
from .photo_schema import PhotoDesign


def reconstruct_plan(data, components, furniture_type='table'):
    span = np.ptp(data.mesh.bounds, axis=0)
    parts, unresolved = [], []
    for component in components:
        if component.geometry != 'box':
            unresolved.append(component.component_id)
            continue
        # Bind the existing dimension contract so measured values genuinely
        # drive geometry and the existing measurement-lock machinery applies.
        keys = ('width_mm', 'depth_mm', 'height_mm')
        position = [f'({v:.14g})*{key}/({dimension:.14g})' for v, key, dimension in zip(component.center, keys, span)]
        # OBB local axes need not match the furniture axes. A dimension factor
        # of one preserves the fitted primitive while retaining the dependency.
        size = [f'({v:.14g})*{key}/({dimension:.14g})' for v, key, dimension in zip(component.size, keys, span)]
        parts.append(dict(part_id=f'F{len(parts)+1:03d}', label=component.label,
            evidence='Estimated oriented primitive from reference mesh; review component segmentation',
            geometry='box', material='solid_wood', color_rgb=[.58, .39, .22],
            position=position, size=size,
            rotation_deg=Rotation.from_matrix(component.rotation).as_euler('xyz', degrees=True).tolist()))
    if not parts:
        raise ValueError('No reliable native components reconstructed; supply a reviewed engineering plan')
    plan = PhotoDesign(furniture_type=furniture_type, summary='Local mesh-derived editable component hypotheses',
        values=[dict(key=k, value=float(v), explanation='Reference mesh envelope; physical size unverified')
            for k, v in zip(('width_mm', 'depth_mm', 'height_mm'), span)], parts=parts,
        assumptions=['Geometry follows an AI-generated estimate; joints and material need engineering review'],
        unrepresented_features=[f'Unresolved mesh region {i}' for i in unresolved])
    return plan, dict(reconstructed_components=len(parts), unresolved_components=unresolved,
                      complete=not unresolved, native_geometry=True)


def build_native(plan, inputs=None, name='Mesh-derived furniture', *, geometry_cache=None):
    from .agents.photo_agent import specification
    from .photo_cad import create_photo_furniture
    from .engineering_cad import check_solid, check_supplied_dimensions
    inputs = inputs or {}
    spec, _ = specification(plan, inputs, name)
    build = create_photo_furniture(spec, geometry_cache=geometry_cache)
    for shape in build.shapes.values():
        check_solid(shape.val())
    check_supplied_dimensions(build, inputs.get('known_values', []))
    return build


def tessellate_build(build, tolerance=.25):
    import trimesh
    from .cad_meshing import absolute_triangles
    from .mesh_analyzer import analyze_mesh
    meshes, labels = [], []
    for index, (pid, shape) in enumerate(build.shapes.items()):
        vertices, faces = absolute_triangles(shape.val(), tolerance, max_triangles=250_000)
        meshes.append(trimesh.Trimesh(vertices=vertices, faces=faces, process=False))
        labels.append(np.full(len(faces), index, dtype=np.int32))
    result = analyze_mesh(trimesh.util.concatenate(meshes), np.concatenate(labels))
    result.report['native_part_ids'] = list(build.shapes)
    return result


def fit_bspline(points, tolerance=.1):
    """Native interpolating B-Spline edge, with independent deviation evidence."""
    import cadquery as cq
    points = np.asarray(points, dtype=float)
    if points.ndim != 2 or points.shape[1] != 3 or len(points) < 4 or not np.isfinite(points).all():
        raise ValueError('A B-Spline requires at least four finite 3D points')
    return cq.Edge.makeSpline([cq.Vector(*p) for p in points], tol=tolerance)


def nurbs_patch(control_points, weights=None):
    """Native rational Bezier patch (a clamped NURBS special case)."""
    import cadquery as cq
    from OCP.Geom import Geom_BSplineSurface
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.TColgp import TColgp_Array2OfPnt
    from OCP.TColStd import TColStd_Array2OfReal, TColStd_Array1OfReal, TColStd_Array1OfInteger
    from OCP.gp import gp_Pnt
    points = np.asarray(control_points, dtype=float)
    if points.ndim != 3 or points.shape[2] != 3 or not np.isfinite(points).all():
        raise ValueError('NURBS controls must be a finite rectangular 3D grid')
    nu, nv = points.shape[:2]
    if not (2 <= nu <= 8 and 2 <= nv <= 8):
        raise ValueError('Use a 2..8 by 2..8 control grid')
    weights = np.ones((nu, nv)) if weights is None else np.asarray(weights)
    if weights.shape != (nu, nv) or not np.isfinite(weights).all() or (weights <= 0).any():
        raise ValueError('NURBS weights must be finite and positive')
    poles, rational = TColgp_Array2OfPnt(1, nu, 1, nv), TColStd_Array2OfReal(1, nu, 1, nv)
    for i in range(nu):
        for j in range(nv):
            poles.SetValue(i+1, j+1, gp_Pnt(*points[i, j]))
            rational.SetValue(i+1, j+1, float(weights[i, j]))
    knots = TColStd_Array1OfReal(1, 2)
    knots.SetValue(1, 0.)
    knots.SetValue(2, 1.)
    um, vm = TColStd_Array1OfInteger(1, 2), TColStd_Array1OfInteger(1, 2)
    for i in (1, 2):
        um.SetValue(i, nu)
        vm.SetValue(i, nv)
    surface = Geom_BSplineSurface(poles, rational, knots, knots, um, vm, nu-1, nv-1, False, False)
    face = cq.Face(BRepBuilderAPI_MakeFace(surface, 1e-6).Face())
    if not face.isValid():
        raise ValueError('Invalid rational surface patch')
    return face


def export_native(build, plan, output):
    import cadquery as cq
    from .engineering_cad import verify_solid_step
    from .pipeline import file_hash
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    checks = {}
    for pid, shape in build.shapes.items():
        path = output / f'{pid}.step'
        cq.exporters.export(shape, str(path))
        _, checks[pid] = verify_solid_step(shape.val(), path)
    assembly_path = output / 'assembly.step'
    build.assembly.save(str(assembly_path))
    reopened = cq.importers.importStep(str(assembly_path)).val()
    if not reopened.isValid() or len(reopened.Solids()) != len(build.shapes):
        raise ValueError('Reopened assembly STEP lost valid solids')
    expected_volume = sum(shape.val().Volume(1e-10) for shape in build.shapes.values())
    if abs(reopened.Volume(1e-10)-expected_volume) > max(.01, expected_volume*1e-6):
        raise ValueError('Assembly STEP volume changed on reimport')
    (output / 'feature_specification.json').write_text(plan.model_dump_json(indent=2), encoding='utf-8')
    source = """# Editable native CAD; run from the project root using .venv.
import json
from pathlib import Path
from app.photo_schema import PhotoDesign
from app.mesh_to_cad import build_native
plan = PhotoDesign.model_validate_json(Path(__file__).with_name('feature_specification.json').read_text(encoding='utf-8'))
build = build_native(plan)
build.assembly.save(str(Path(__file__).with_name('edited_assembly.step')))
"""
    (output / 'editable_cad.py').write_text(source, encoding='utf-8')
    return dict(assembly_reopened=True, assembly_solid_count=len(reopened.Solids()),
                assembly_sha256=file_hash(assembly_path), components=checks, approved_for_manufacturing=False)
