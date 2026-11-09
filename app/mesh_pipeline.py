"""Local reconstruction orchestration; provider generation happens once upstream."""
import json
from pathlib import Path
import time
import numpy as np
from .geometry_cache import GeometryCache
from .mesh_analyzer import load_mesh
from .mesh_registration import normalize_mesh, align_reference_mesh
from .mesh_segmentation import segment_mesh
from .mesh_to_cad import reconstruct_plan, build_native, tessellate_build, export_native
from .mesh_sampling import sample_surface
from .mesh_cad_schema import DenseOptimizationOptions
from .photo_schema import PhotoDesign


def write_json(path, data):
    path = Path(path)
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def preview_glb(data, path, name='reference', errors=None):
    """GLB is Y-up metres; all fitting and report distances remain Z-up mm."""
    import trimesh
    mesh = data.mesh.copy()
    mesh.metadata={}
    if errors is not None:
        mesh.visual.vertex_colors = errors
    transform = np.array([[.001, 0, 0, 0], [0, 0, .001, 0], [0, -.001, 0, 0], [0, 0, 0, 1.]])
    mesh.apply_transform(transform)
    scene = trimesh.Scene()
    scene.add_geometry(mesh, node_name=name, geom_name=name)
    Path(path).write_bytes(scene.export(file_type='glb'))


def run_reconstruction(reference_path, output, *, initial_plan=None, known_dimensions=None,
                       furniture_type='table', up_axis='Y', estimated_height_mm=750, options=None, progress=None,
                       part_recognition=False, drawing_options=None, generate_editable_outputs=True,designer_review=None,reuse_parent=None):
    from .cad_optimizer import optimize_mesh_cad
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    reference_path = Path(reference_path)
    if reference_path.parent.resolve() != output.resolve():
        import shutil
        preserved = output/('original_reference'+reference_path.suffix.lower())
        shutil.copy2(reference_path, preserved)
        reference_path = preserved
    progress = progress or (lambda *_: None)
    known_dimensions = known_dimensions or {}
    options = DenseOptimizationOptions.model_validate(options or {})
    cache = GeometryCache(max_bytes=min(256, options.memory_mb//4)*1024**2, directory=output/'cache')
    progress('mesh_validation', 15)
    source = load_mesh(reference_path)
    reference, calibration = normalize_mesh(source, known_dimensions,
        estimated_height_mm=estimated_height_mm, up_axis=up_axis)
    components, graph = segment_mesh(reference, furniture_type)
    recognition=None
    from .ai_part_detector import local_adapter
    if __import__('os').getenv('FURNITURE_SEGMENTATION_COMMAND'):
        from .part_segmentation_validator import validate_regions
        adapter_input=output/'private'/'segmentation_reference.ply';adapter_input.parent.mkdir(exist_ok=True)
        reference.mesh.export(adapter_input)
        proposed=local_adapter(adapter_input,adapter_input.parent)
        verified=validate_regions(reference.mesh,proposed)
        write_json(output/'segmentation_regions.json',verified)
        components,graph=segment_mesh(reference,furniture_type,regions=verified['regions'])
        graph['specialized_regions']=verified
    if part_recognition and not initial_plan:
        photo=output/'original_photo.png'
        if not photo.is_file():raise ValueError('AI recognition requires the original reference photograph')
        from .ai_part_detector import recognize
        progress('ai_part_recognition',20)
        recognition=recognize(reference,graph,photo.read_bytes(),output,known_dimensions=known_dimensions,furniture_type=furniture_type)
        initial_plan=recognition.design.model_dump(mode='json')
    if initial_plan:
        plan = PhotoDesign.model_validate(initial_plan)
        construction = dict(source='sol_part_recognition' if recognition else 'existing_engineering_plan', complete=not plan.unrepresented_features,
                            unresolved_features=plan.unrepresented_features)
    else:
        plan, construction = reconstruct_plan(reference, components, furniture_type)
        construction['unresolved_face_count'] = len(graph['unresolved_faces'])
        construction['complete'] = construction['complete'] and not graph['unresolved_faces']
    # Exact supplied values are retained; the CAD envelope is independently
    # verified by build_native rather than trusting this specification field.
    for value in plan.values:
        if value.key in known_dimensions:
            value.value = known_dimensions[value.key]
    inputs = {'known_values': [dict(key=key, value=value) for key, value in known_dimensions.items()]}
    progress('initial_native_cad', 25)
    initial_build = build_native(plan, inputs)
    initial_cad = tessellate_build(initial_build)
    # Registration changes only the reference-to-CAD frame, never the CAD
    # parameters or its fixed dimensions. Keep this transform in the report.
    from .geometry_cache import ResourceBudget
    reference, registration = align_reference_mesh(reference, initial_cad, count=options.coarse_points,
        budget=ResourceBudget(options.memory_mb,workers=options.workers),cache=cache)
    write_json(output/'mesh_analysis.json', source.report)
    write_json(output/'calibration.json', calibration)
    write_json(output/'registration.json', registration)
    write_json(output/'component_graph.json', graph)
    (output/'initial_feature_specification.json').write_text(plan.model_dump_json(indent=2), encoding='utf-8')
    preview_glb(reference, output/'reference.glb')
    preview_glb(initial_cad, output/'initial_cad.glb', 'initial CAD')
    progress('global_surface_fitting', 35)
    photo_path = output/'original_photo.png'
    photo_bytes = photo_path.read_bytes() if photo_path.is_file() else None
    def checkpoint(model, build):
        import cadquery as cq
        (output/'best_valid_feature_specification.json').write_text(model.model_dump_json(indent=2), encoding='utf-8')
        # Export a native checkpoint before final high-density verification, so
        # numerical timeouts do not discard the last valid B-Rep revision.
        shape = cq.Compound.makeCompound([solid.val() for solid in build.shapes.values()])
        temporary = output/'best_valid_assembly.tmp.step'
        cq.exporters.export(shape, str(temporary))
        temporary.replace(output/'best_valid_assembly.step')
    # The calibrated reference has already been registered as a whole assembly.
    result = optimize_mesh_cad(plan, reference, options.model_copy(update={'align_reference':False}), inputs=inputs, cache=cache, photo_bytes=photo_bytes,
        publish_best=checkpoint,
        progress=lambda stage, current, total: progress(stage, 35+round(40*current/max(total, 1))))
    progress('step_export_and_reimport', 85)
    exports = export_native(result.build, result.plan, output)
    optimized = tessellate_build(result.build)
    preview_glb(optimized, output/'optimized_cad.glb', 'optimized CAD')
    # The mesh and CAD share a single transform in the overlay.
    import trimesh
    overlay = trimesh.Scene()
    for name, data, color in [('reference', result.reference, [70, 140, 225, 65]),
                              ('native CAD', optimized, [220, 160, 55, 220])]:
        mesh = data.mesh.copy()
        mesh.metadata={}
        mesh.visual.face_colors = color
        mesh.apply_transform(np.array([[.001, 0, 0, 0], [0, 0, .001, 0], [0, -.001, 0, 0], [0, 0, 0, 1.]]))
        overlay.add_geometry(mesh, node_name=name)
    (output/'overlay.glb').write_bytes(overlay.export(file_type='glb'))
    # Attach face deviation colors to a reference display mesh, while retaining
    # the high-density raw measurements in a compressed standalone artifact.
    from .geometry_acceleration import SurfaceIndex
    error = SurfaceIndex(optimized, threads=options.workers).closest(result.reference.mesh.vertices)['distances']
    scale = max(float(np.quantile(error, .95)), .01)
    fraction = np.clip(error/scale, 0, 1)
    colors = np.column_stack((255*fraction, 180*(1-fraction), 80*(1-fraction), np.full(len(error), 255))).astype(np.uint8)
    preview_glb(result.reference, output/'deviation.glb', 'deviation map', errors=colors)
    np.savez_compressed(output/'surface_deviation.npz', points_mm=result.deviation_points, deviation_mm=result.deviation_mm)
    report = result.report
    if report.get('structural_review_required'):
        construction['complete']=False
        construction['unassigned_reference_faces']=report['component_correspondence']['unassigned_faces']
    report.update(reconstruction=construction, export_validation=exports, calibration=calibration,
        registration=registration, original_mesh_preserved=True,
        error_map_95th_percentile_mm=scale, original_photo_used=bool(photo_bytes or options.photo_views),
        production_ready=False, manufacturing_review_required=True)
    if not options.photo_views and not photo_bytes:
        report['limitations'].append('Original-photo shape validation is unverified without reviewed image evidence and a reliable camera')
    write_json(output/'optimization_report.json', report)
    write_json(output/'geometry_deviation_report.json', report['final'])
    write_json(output/'parameter_revision_history.json', report['revisions'])
    if generate_editable_outputs:
        progress('editable_cad_and_six_view_drawings',92)
        from .cad_delivery import build_delivery
        delivery=build_delivery(result.plan,result.build,result.reference,optimized,output,report,
            known_dimensions=known_dimensions,recognition=recognition,drawing_options=drawing_options,designer_review=designer_review,reuse_parent=reuse_parent)
        report['delivery']=dict(status=delivery['status'],editable_cad=delivery['freecad']['status'])
        write_json(output/'optimization_report.json',report)
    # Immutable delivery manifest identifies every downloadable artifact.
    from .pipeline import file_hash
    excluded={'request.json','progress.json','manifest.json','package.zip','provider_binding.json','worker.log','failure.json'}
    files = {p.relative_to(output).as_posix():file_hash(p) for p in output.rglob('*') if p.is_file()
             and p.name not in excluded and not {'private','cache','regeneration_cache','__pycache__'}.intersection(p.relative_to(output).parts)
             and p.suffix.lower() not in ('.sqlite','.sqlite3','.fcstd1','.pyc')}
    write_json(output/'manifest.json', dict(files=files, units='mm', approved_for_manufacturing=False))
    from zipfile import ZipFile, ZIP_DEFLATED
    with ZipFile(output/'package.zip', 'w', ZIP_DEFLATED) as archive:
        for name in [*files, 'manifest.json']:
            archive.write(output/name, name)
    progress('complete', 100)
    return report
