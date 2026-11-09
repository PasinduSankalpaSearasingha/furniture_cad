"""Package native editable models, six-side drawings and review evidence."""
import csv
import json
from pathlib import Path
import shutil
import numpy as np


def assembly_source(project_root):
    return '''# Authoritative, editable native furniture CAD. Run with the project .venv.
import json, os, sys
from pathlib import Path
project=Path(os.environ.get('FURNITURE_CAD_PROJECT',PROJECT_ROOT))
sys.path.insert(0,str(project))
from app.photo_schema import PhotoDesign
from app.mesh_to_cad import build_native
folder=Path(__file__).resolve().parent
plan=PhotoDesign.model_validate_json((folder/'feature_specification.json').read_text(encoding='utf-8'))
parameters=json.loads((folder/'cad_parameters.json').read_text(encoding='utf-8'))
values={v['key']:v['value'] for v in parameters['values']}
for value in plan.values:
    if value.key in values:value.value=values[value.key]
plan=PhotoDesign.model_validate(plan.model_dump())
build=build_native(plan)
build.assembly.save(str(folder/'edited_assembly.step'))
''' .replace('PROJECT_ROOT',repr(str(project_root)))


def write(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,indent=2,allow_nan=False),encoding='utf-8')


def reference_display_mesh(mesh,face_budget=50000):
    """Reduce a continuous display surface; never delete a sampled set of faces."""
    import trimesh
    from .cad_meshing import simplify_triangles,CADMeshError
    if len(mesh.faces)<=face_budget:return mesh,dict(method='complete_source_surface',source_faces=len(mesh.faces))
    allowance=max(.25,float(np.ptp(mesh.vertices,axis=0).max())/1500)
    details={}
    try:
        points,faces=simplify_triangles(mesh.vertices,mesh.faces,face_budget,allowance,details)
        display=trimesh.Trimesh(vertices=points,faces=faces,process=False)
        return display,dict(method='continuous_surface_decimation',source_faces=len(mesh.faces),**details)
    except CADMeshError:
        # Keeping the full surface is preferable to a misleading perforated view.
        return mesh,dict(method='complete_source_surface_fallback',source_faces=len(mesh.faces))


def build_delivery(plan,build,reference,native,output,report,*,known_dimensions=None,recognition=None,drawing_options=None,designer_review=None,reuse_parent=None):
    import cadquery as cq
    import trimesh
    from .mesh_pipeline import preview_glb
    from .mesh_analyzer import analyze_mesh
    from .cad_meshing import absolute_triangles
    from .furniture_part_graph import part_registry
    from .freecad_project_exporter import export_freecad
    from .engineering_drawing_generator import generate_drawings,make_reports
    from .drawing_validator import validate_drawings
    from .ai_call_ledger import CallLedger
    from .engineering_cad import geometry_checks
    from .engineering_cad import verify_solid_step
    output=Path(output);cad=output/'cad';cad.mkdir(exist_ok=True)
    registry=part_registry(build,plan,reference,native,recognition=recognition)
    report['native_geometry_checks']=geometry_checks(build)
    write(output/'reports'/'native_geometry_validation.json',report['native_geometry_checks'])
    designer_review=designer_review or {}
    assignments=designer_review.get('face_assignments',{})
    face_map=np.asarray(reference.mesh.metadata.get('_cad_source_face_ids',np.arange(len(reference.mesh.faces))))
    vertex_pairs=np.asarray(reference.mesh.metadata.get('_cad_source_vertex_pairs',np.column_stack((np.arange(len(reference.mesh.vertices)),np.arange(len(reference.mesh.vertices))))))
    if assignments:
        from .part_segmentation_validator import validate_regions
        working={}
        for pid,ids in assignments.items():
            indexes=np.searchsorted(face_map,ids)
            if np.any(indexes>=len(face_map)) or not np.array_equal(face_map[indexes],ids):
                raise ValueError('A reviewed face ID is absent or was removed as degenerate')
            working[pid]=indexes.tolist()
        validate_regions(reference.mesh,dict(regions=[dict(face_ids=ids) for ids in working.values()]))
        claimed=set(i for ids in working.values() for i in ids)
        for p in registry['parts']:
            p['working_face_ids']=working.get(p['part_id'],[i for i in p['working_face_ids'] if i not in claimed])
            p['face_ids']=face_map[p['working_face_ids']].tolist()
            vertices=np.unique(reference.mesh.faces[p['working_face_ids']])
            p['vertex_ids']=np.unique(vertex_pairs[np.isin(vertex_pairs[:,0],vertices),1]).tolist()
        used={i for p in registry['parts'] for i in p['working_face_ids']}
        registry['unassigned_working_face_ids']=sorted(set(range(len(reference.mesh.faces)))-used)
        registry['unassigned_face_ids']=face_map[registry['unassigned_working_face_ids']].tolist()
    for p in registry['parts']:
        review=designer_review.get('boundary_reviews',{}).get(p['part_id'],'pending')
        p['review_status']=review
        if review=='accepted':p['boundary_status']='designer_confirmed'
    registry['designer_review']=designer_review
    write(output/'part_registry.json',registry)
    write(cad/'cad_parameters.json',dict(values=[v.model_dump() for v in plan.values],locked=list(known_dimensions or {}),
        source='feature_specification.json',units='mm',status='draft'))
    shutil.copy2(output/'feature_specification.json',cad/'feature_specification.json')
    shutil.copy2(output/'assembly.step',cad/'assembly.step')
    (cad/'assembly.py').write_text(assembly_source(Path(__file__).resolve().parent.parent),encoding='utf-8')
    transform=np.array([[.001,0,0,0],[0,0,.001,0],[0,-.001,0,0],[0,0,0,1.]])
    scene=trimesh.Scene()
    for part in registry['parts']:
        pid=part['part_id'];directory=cad/'parts'/pid;directory.mkdir(parents=True,exist_ok=True)
        # STEP uses the same stable local stock frame as the part drawing.
        cq.exporters.export(build.local_shapes[pid],str(directory/'part.step'))
        _,proof=verify_solid_step(build.local_shapes[pid].val(),directory/'part.step')
        part['local_step_validation']=proof
        write(directory/'parameters.json',dict(part=next(p.model_dump(mode='json') for p in plan.parts if p.part_id==pid),
            values=[v.model_dump() for v in plan.values],local_to_world=part['local_to_world'],world_to_local=part['world_to_local']))
        (directory/'part.py').write_text("# Edit parameters.json, then run with the project .venv.\n"+
            "import runpy,json\nfrom pathlib import Path\nfolder=Path(__file__).resolve().parent\n"+
            "context=runpy.run_path(str(folder.parents[1]/'assembly.py'))\n"+
            "from app.photo_schema import PhotoPart,PhotoDesign\nfrom app.mesh_to_cad import build_native\nimport cadquery as cq\n"+
            "data=json.loads((folder/'parameters.json').read_text(encoding='utf-8'));plan=context['plan']\n"+
            "replacement=PhotoPart.model_validate(data['part'])\n"+
            "for index,part in enumerate(plan.parts):\n    if part.part_id==replacement.part_id:plan.parts[index]=replacement\n"+
            "values={v['key']:v['value'] for v in data['values']}\n"+
            "for value in plan.values:\n    if value.key in values:value.value=values[value.key]\n"+
            "plan=PhotoDesign.model_validate(plan.model_dump());build=build_native(plan)\n"+
            f"cq.exporters.export(build.local_shapes[{pid!r}],str(folder/'edited_part.step'))\n",encoding='utf-8')
        vertices,faces=absolute_triangles(build.local_shapes[pid].val(),.25,max_triangles=250000)
        preview_glb(analyze_mesh(trimesh.Trimesh(vertices=vertices,faces=faces,process=False)),directory/'part.glb',pid)
        vertices,faces=absolute_triangles(build.shapes[pid].val(),.25,max_triangles=250000)
        mesh=trimesh.Trimesh(vertices=vertices,faces=faces,process=False);mesh.apply_transform(transform)
        mesh.visual.face_colors=[140,100,60,255];scene.add_geometry(mesh,node_name=pid,geom_name=pid)
    (cad/'assembly.glb').write_bytes(scene.export(file_type='glb'))
    regions=trimesh.Scene()
    for index,part in enumerate(registry['parts']):
        if not part['working_face_ids']:continue
        mesh=reference.mesh.submesh([part['working_face_ids']],append=True,repair=False);mesh.metadata={}
        mesh.visual.face_colors=[(index*97+60)%220+20,(index*53+80)%220+20,(index*71+20)%220+20,255]
        mesh.apply_transform(transform);regions.add_geometry(mesh,node_name=part['part_id'],geom_name=part['part_id'])
    if registry['unassigned_working_face_ids']:
        mesh=reference.mesh.submesh([registry['unassigned_working_face_ids']],append=True,repair=False);mesh.metadata={}
        mesh.visual.face_colors=[230,50,50,255];mesh.apply_transform(transform)
        regions.add_geometry(mesh,node_name='UNASSIGNED',geom_name='UNASSIGNED')
    (output/'reference').mkdir(exist_ok=True)
    np.savez_compressed(output/'reference'/'mesh_index_map.npz',working_to_source_face=face_map,
        working_to_source_vertex_pairs=vertex_pairs,source_face_count=reference.mesh.metadata.get('_cad_source_face_count',len(reference.mesh.faces)),
        source_vertex_count=reference.mesh.metadata.get('_cad_source_vertex_count',len(reference.mesh.vertices)))
    (output/'reference'/'part_regions.glb').write_bytes(regions.export(file_type='glb'))
    editable=export_freecad(plan,cad,locked=known_dimensions or {})
    if editable['status']=='validated':
        for pid,check in editable['parts'].items():
            volume=build.shapes[pid].val().Volume()
            if abs(check['volume_mm3']-volume)>max(.01,volume*1e-6):raise ValueError('FreeCAD and authoritative CAD differ: '+pid)
    report['editable_cad']=editable
    write(output/'reports'/'cad_editability.json',editable)
    from .cad_projection_cache import ProjectionCache
    projection_cache=ProjectionCache(output,reuse_parent)
    drawings=generate_drawings(build,plan,output/'drawings',known_dimensions=known_dimensions,
                              projection_cache=projection_cache,**(drawing_options or {}))
    report['projection_cache']=dict(reused=projection_cache.reused,regenerated_title_blocks=True,
        scope='Only unchanged exact B-Rep projections are reused; global validation and assembled outputs are regenerated')
    validation=validate_drawings(output/'drawings',drawings);write(output/'reports'/'drawing_validation.json',validation)
    report['drawing_validation']=validation
    reference_dir=output/'reference';reference_dir.mkdir(exist_ok=True)
    for name in ('original_reference.glb','original_reference.obj','original_photo.png',
                 'reference.glb','overlay.glb','deviation.glb','surface_deviation.npz'):
        if (output/name).is_file():shutil.copy2(output/name,reference_dir/name)
    from .ai.geometry_preview import render_build
    from .geometry_acceleration import SurfaceIndex
    (reference_dir/'cad_evidence.png').write_bytes(render_build(build,size=(620,600)))
    mesh=reference.mesh
    display,reduction=reference_display_mesh(mesh);faces=display.faces
    normals=np.asarray(display.vertex_normals);points=np.asarray(display.vertices)
    reference_display=[(points,faces,normals,np.array([.25,.55,.85]))]
    (reference_dir/'mesh_evidence.png').write_bytes(render_build(None,size=(620,600),meshes=reference_display,label='REFERENCE MESH / ESTIMATED'))
    deviations=SurfaceIndex(native).closest(points)['distances']
    maximum=max(float(report.get('error_map_95th_percentile_mm') or np.quantile(deviations,.95)),.01)
    values=np.clip(deviations[faces].mean(1)/maximum,0,1);bands=np.minimum((values*8).astype(int),7)
    colored=[(points,faces[bands==i],normals,np.array([(i+.5)/8,1-(i+.5)/8,.15])) for i in range(8) if np.any(bands==i)]
    (reference_dir/'deviation_evidence.png').write_bytes(render_build(None,size=(620,600),meshes=colored,label=f'DEVIATION DISPLAY / RED >= {maximum:.2f} mm'))
    report['evidence_display']=dict(display_faces=len(faces),reference_faces=len(mesh.faces),surface_crop=False,
        downsampled_for_pdf_only=len(faces)<len(mesh.faces),deviation_red_mm=maximum)
    report['evidence_display']['surface_reduction']=reduction
    reports=make_reports(build,output,drawings,report,registry)
    with (output/'manufacturing'/'bom.csv').open('w',newline='',encoding='utf-8') as handle:
        writer=csv.writer(handle);writer.writerow(['part_id','label','quantity','material_status','geometry','review_status'])
        for p in registry['parts']:writer.writerow([p['part_id'],p['label'],1,'unconfirmed',p['geometry'],p['review_status']])
    with (output/'manufacturing'/'cutting_envelopes_REVIEW.csv').open('w',newline='',encoding='utf-8') as handle:
        writer=csv.writer(handle);writer.writerow(['part_id','length_mm','width_mm','thickness_mm','status'])
        for p in registry['parts']:writer.writerow([p['part_id'],*p['local_envelope_mm'],'enclosing envelope, not verified stock'])
    if editable['status']!='validated':
        for p in registry['parts']:p['files'].pop('freecad',None)
    notes='''DRAFT - MANUFACTURING REVIEW REQUIRED

assembly.step and per-part STEP contain native B-Rep geometry, not mesh facets.
Part STEP/drawings use the local stock frame. local_to_world matrices restore the assembly frame.
Plain untrimmed axis-aligned boxes use native Sketcher + PartDesign Pad histories.
Complex curves and contact-trimmed parts use packaged FeaturePython objects.
Edit shared Parameters in FreeCAD, or ConstructionJSON on complex parts for curves/profiles/control points.
The proxy rebuilds the same PhotoDesign using the installed project's .venv.
Keep the source project and .venv available; set Parameters.ProjectRoot/PythonExecutable after moving them.
Place cad/furniture_cad_proxy.py on FreeCAD's Python path before opening assembly.FCStd.
Alternatively run cad/open_project.FCMacro. Recompute must finish without LastError before export.
Confirmed dimensions are read-only in the property editor; unlocking requires deliberate designer action.
No model weights are bundled. Local segmentation code AND weights require a license review.
Drawing curves use exact native B-Rep HLR. Dimensions are measured envelopes, not cutting instructions.
Unseen geometry, materials, joints, loads, tolerances, grain and stock allowances remain unverified.
Six-side drawing PDFs/SVGs/DXFs use the stated first/third angle convention; DXF model space is 1:1 mm.
'''
    (output/'README_EDITABLE_CAD.txt').write_text(notes,encoding='utf-8')
    write(output/'part_registry.json',registry)
    states=['draft','geometry_validated']
    dimensions_verified={'width_mm','depth_mm','height_mm'}<=set(known_dimensions or {})
    if dimensions_verified:states.append('dimension_verified')
    states.append('manufacturing_review')
    delivery=dict(status='manufacturing_review',revision=(drawing_options or {}).get('revision','R1'),approved_for_manufacturing=False,parts=registry['parts'],
        parameters=[dict(**v.model_dump(),locked=v.key in (known_dimensions or {})) for v in plan.values],
        freecad=editable,drawings=drawings,files=dict(assembly_step='cad/assembly.step',assembly_preview='cad/assembly.glb',
        source='cad/assembly.py',parameters='cad/cad_parameters.json',**reports),ai_usage=CallLedger(output/'private'/'sol_calls.sqlite').summary(),
        review_required=True,status_history=states,review_state=dict(geometry='geometry_validated',
            dimensions='dimension_verified' if dimensions_verified else 'incomplete',
            dimension_verification_basis='Supplied dimensions compared with native CAD; no physical inspection',
            boundaries={key:sum(p['review_status']==key for p in registry['parts']) for key in ('pending','accepted','rejected')},
            manufacturing='review_required'),
        outstanding=['Part boundaries','Physical dimensions','Materials','Joinery','Tolerances','Load and stability checks'])
    if editable['status']=='validated':delivery['files']['assembly_freecad']='cad/assembly.FCStd'
    write(output/'cad_delivery.json',delivery)
    return delivery
