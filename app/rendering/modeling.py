"""Native Blender modeling, measured photo comparison and independent CAD agreement."""
import copy
import json
import math
import os
import subprocess
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED
from io import BytesIO
import numpy as np
import cv2
from PIL import Image
from app.photo_schema import PhotoDesign,number
from app.reference_fit_schema import ReferenceFitOptions,FitView
from app.pipeline import canonical_hash,file_hash,safe_directory
from app.ai.openai_client import AIServiceError
from app.ai.reference_fitting import (MeshPart,mesh_from_build,frame_for,fit_camera,initial_camera,metrics,render_mask,boundary,prepare_target)
from app.ai.reference_fitting import rotation_matrix,anchor
from app.ai.reference_mask import png_bytes
from .blender_service import discover_blender

VERSION=2


def numeric_plan(plan):
    values={v.key:v.value for v in plan.values};raw=plan.model_dump(mode='json')
    fields={'position','rotation_deg','size','corner_radius','edge_radius','dish_depth','surface_dishes','surface_grid',
        'depths','sweep_sections','outline','path','sections','center','width','depth','radius_x','radius_y','radius_z'}
    def resolve(value):
        if isinstance(value,dict):return {k:resolve(v) if k in fields else v for k,v in value.items()}
        if isinstance(value,list):return [resolve(v) for v in value]
        if isinstance(value,str):return number(value,values)
        return value
    for part in raw['parts']:
        for key in fields&part.keys():part[key]=resolve(part[key])
    return raw


def triangle_distance(points,triangles):
    """Exact point-to-triangle distances for each tested point; no vertex-only shortcut."""
    a=triangles[:,0];ab=triangles[:,1]-a;ac=triangles[:,2]-a
    normal=np.cross(ab,ac);area=np.einsum('ij,ij->i',normal,normal);valid=area>1e-16
    a,ab,ac,normal,area=[v[valid] for v in (a,ab,ac,normal,area)]
    if not len(a):raise ValueError('Geometry contains no nondegenerate triangles')
    d00=np.einsum('ij,ij->i',ab,ab);d01=np.einsum('ij,ij->i',ab,ac);d11=np.einsum('ij,ij->i',ac,ac)
    denominator=area;result=[]
    for point in points:
        ap=point-a;dot=np.einsum('ij,ij->i',ap,normal);projection=ap-normal*(dot/area)[:,None]
        d20=np.einsum('ij,ij->i',projection,ab);d21=np.einsum('ij,ij->i',projection,ac)
        u=(d11*d20-d01*d21)/denominator;v=(d00*d21-d01*d20)/denominator
        inside=(u>=0)&(v>=0)&(u+v<=1)
        best=np.full(len(a),np.inf)
        for origin,direction in ((a,ab),(a,ac),(a+ab,ac-ab)):
            local=point-origin;t=np.clip(np.einsum('ij,ij->i',local,direction)/np.maximum(np.einsum('ij,ij->i',direction,direction),1e-20),0,1)
            delta=local-direction*t[:,None];best=np.minimum(best,np.einsum('ij,ij->i',delta,delta))
        best[inside]=dot[inside]**2/area[inside]
        result.append(math.sqrt(max(0,float(best.min()))))
    return np.array(result)


def surface_samples(mesh,count=128):
    triangles=mesh.points[mesh.triangles];area=np.linalg.norm(np.cross(triangles[:,1]-triangles[:,0],triangles[:,2]-triangles[:,0]),axis=1)
    if not area.sum():raise ValueError('Mesh has zero surface area')
    rng=np.random.default_rng(841);selected=triangles[rng.choice(len(triangles),count,p=area/area.sum())]
    uv=rng.random((count,2));uv[uv.sum(1)>1]=1-uv[uv.sum(1)>1]
    points=selected[:,0]+uv[:,:1]*(selected[:,1]-selected[:,0])+uv[:,1:]*(selected[:,2]-selected[:,0])
    # Include geometric extrema in addition to area samples.
    extrema=mesh.points[np.concatenate((mesh.points.argmin(0),mesh.points.argmax(0)))]
    return np.concatenate((points,extrema))


def indexed_triangle_distance(points,mesh):
    """Exact sampled distances with conservative triangle-sphere pruning.

    A nearest vertex gives an upper bound on nearest surface distance. Every
    triangle is enclosed by its centroid sphere, so candidates within that
    upper bound plus the largest radius include the true closest triangle.
    """
    triangles=mesh.points[mesh.triangles]
    if len(triangles)<20000:return triangle_distance(points,triangles)
    from scipy.spatial import cKDTree
    centers=triangles.mean(1);radii=np.linalg.norm(triangles-centers[:,None,:],axis=2).max(1)
    center_tree=cKDTree(centers);vertex_tree=cKDTree(mesh.points)
    upper=vertex_tree.query(points)[0];maximum=float(radii.max());result=[]
    for point,bound in zip(points,upper):
        candidates=np.asarray(center_tree.query_ball_point(point,float(bound)+maximum+1e-7),dtype=np.int64)
        if not len(candidates):raise ValueError('No nearest-surface candidate triangles')
        # The per-triangle bound further reduces large, conservative spheres.
        distance=np.linalg.norm(centers[candidates]-point,axis=1)-radii[candidates]
        candidates=candidates[distance<=float(bound)+1e-7]
        result.append(triangle_distance([point],triangles[candidates])[0])
    return np.asarray(result)


def compare_meshes(native,cad,tolerance):
    if set(native)!=set(cad):raise ValueError('Blender/CAD component identifiers differ')
    parts=[]
    for pid,a in native.items():
        b=cad[pid];distances=np.concatenate((triangle_distance(surface_samples(a),b.points[b.triangles]),
                                           triangle_distance(surface_samples(b),a.points[a.triangles])))
        bbox=max(float(np.max(np.abs(a.points.min(0)-b.points.min(0)))),float(np.max(np.abs(a.points.max(0)-b.points.max(0)))))
        parts.append(dict(part_id=pid,samples=len(distances),sampled_max_surface_gap_mm=float(distances.max()),
            p95_surface_gap_mm=float(np.percentile(distances,95)),mean_surface_gap_mm=float(distances.mean()),bbox_max_gap_mm=bbox,
            within_sampled_tolerance=bool(distances.max()<=tolerance and bbox<=tolerance)))
    return dict(status='SAMPLED_WITHIN_TOLERANCE' if all(p['within_sampled_tolerance'] for p in parts) else 'CAD_MATCH_NEEDS_REVIEW',
        tolerance_mm=tolerance,parts=parts,sampled_max_surface_gap_mm=max(p['sampled_max_surface_gap_mm'] for p in parts),
        method='Bidirectional deterministic area sampling and extrema, point-to-triangle distances in mm; component bounds checked independently',
        complete_surface_bound_verified=False,engineering_accuracy_verified=False)


def compare_export(settings,plan,inputs,name,directory,artifact=None):
    """Reopen the actual STEP, identify solids by world bounds, and measure it."""
    import cadquery as cq
    from scipy.optimize import linear_sum_assignment
    from app.geometry import exact_bounds
    from app.agents import photo_agent
    spec,_=photo_agent.specification(plan,inputs,name)
    expected=photo_agent.preflight(spec,{v['key'] for v in inputs.get('known_values',[])},check_estimates=False)
    solids=cq.importers.importStep(str(directory/'cabinet.step')).solids().vals()
    if len(solids)!=len(plan.parts) or any(not s.isValid() or s.Volume()<=0 for s in solids):
        raise AIServiceError('BLENDER_CAD_EXPORT_INVALID','The exported STEP has invalid or unexpected solids')
    def coordinates(shape):
        box=exact_bounds(shape);return np.array([getattr(box,axis+edge) for axis in 'xyz' for edge in ('min','max')])
    wanted=np.array([coordinates(expected.shapes[p.part_id].val()) for p in plan.parts]);found=np.array([coordinates(s) for s in solids])
    costs=np.max(np.abs(wanted[:,None,:]-found[None,:,:]),axis=2);rows,columns=linear_sum_assignment(costs)
    if costs[rows,columns].max()>.05:raise AIServiceError('BLENDER_CAD_EXPORT_INVALID','Exported STEP placement/bounds differ from the frozen design')
    cad={}
    for row,column in zip(rows,columns):
        points,faces=solids[column].copy(mesh=False).tessellate(1.,.3)
        cad[plan.parts[row].part_id]=MeshPart(np.array([p.toTuple() for p in points]),np.array(faces,dtype=np.int32))
    native=(load_native(safe_directory(safe_directory(settings.storage,artifact['directory']),'model_mesh.json'),plan,settings.blender_max_triangles)
        if artifact else mesh_from_build(expected,plan))
    report=compare_meshes(native,cad,settings.blender_cad_tolerance_mm)
    ambiguous=bool(any(np.count_nonzero(costs[row]<.0001)>1 for row in rows))
    if ambiguous:report['status']='CAD_COMPONENT_IDENTITY_NEEDS_REVIEW'
    report.update(source_step_sha256=file_hash(directory/'cabinet.step'),
        preview_glb_sha256=artifact['files']['reconstruction.glb'] if artifact else file_hash(directory/'cabinet.glb'),
        plan_sha256=artifact['plan_sha256'] if artifact else canonical_hash(plan.model_dump(mode='json')),
        independent_native_blender_reconstruction=bool(artifact),step_valid_solids=len(solids),component_identity_ambiguous=ambiguous,
        identity_method='Assignment against the frozen CAD parts by six world-bound coordinates; ambiguous matches flagged',
        export_reopened=True)
    report['comparison_geometry_source']='NATIVE_BLENDER_RECONSTRUCTION' if artifact else 'FROZEN_CAD_SOLIDS'
    if artifact:report['native_glb_sha256']=artifact['files']['reconstruction.glb']
    return report


def load_native(path,plan,max_triangles):
    raw=json.loads(path.read_text(encoding='utf-8'));mesh={};total=0;definitions={p.part_id:p for p in plan.parts};values={v.key:v.value for v in plan.values}
    if set(raw)!={p.part_id for p in plan.parts}:raise AIServiceError('BLENDER_MODEL_INVALID','Native component identifiers changed')
    for pid,part in raw.items():
        points=np.array(part['points'],dtype=float);triangles=np.array(part['triangles'],dtype=np.int32);total+=len(triangles)
        if (points.ndim!=2 or points.shape[1]!=3 or not np.isfinite(points).all() or np.abs(points).max()>20000 or
            triangles.ndim!=2 or triangles.shape[1]!=3 or triangles.min()<0 or triangles.max()>=len(points) or total>max_triangles):
            raise AIServiceError('BLENDER_MODEL_INVALID','Native model exceeds valid mesh bounds')
        definition=definitions[pid];ends={}
        if definition.path:
            rotation=rotation_matrix([number(v,values) for v in definition.rotation_deg]);position=np.array([number(v,values) for v in definition.position])
            ends={key:rotation@np.array([number(v,values) for v in point])+position for key,point in [('path_start',definition.path[0]),('path_end',definition.path[-1])]}
        mesh[pid]=MeshPart(points,triangles,ends)
    return mesh


def context_for(plan,build,references,observations,settings):
    import time
    targets=[prepare_target(pixels,view,160) for view,pixels in references[:4]]
    if not targets:raise AIServiceError('REFERENCE_REQUIRED','Upload a furniture photograph before Blender reconstruction')
    mesh=mesh_from_build(build,plan);frame=frame_for(mesh);context=[]
    for target in targets:
        for landmark in target['view'].landmarks:
            if landmark.part_id not in mesh:raise ValueError('A landmark refers to a missing modeling component')
            anchor(mesh[landmark.part_id],landmark.anchor)
        if target['usable']:
            fitted=fit_camera(mesh,target,frame,observations.get('reference_view') or {},ReferenceFitOptions(camera_evaluations=120),time.monotonic()+20)
            camera=fitted['camera']
        else:
            framing=copy.copy(target);w,h=target['size'];mask=np.zeros((h,w),bool);mask[round(h*.08):round(h*.92),round(w*.12):round(w*.88)]=True;framing['mask']=mask
            hint=observations.get('reference_view') or {};camera=initial_camera(mesh,framing,frame,hint.get('azimuth_deg',-110),hint.get('elevation_deg',15))
        context.append((target,camera))
    return context


def build_model(settings,plan,build,output,context,progress=lambda stage:None):
    """Blender creates geometry from the plan; it does NOT import CAD GLB here."""
    executable=discover_blender(settings.blender_executable);output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    digest=canonical_hash(plan.model_dump(mode='json'));cameras=[]
    for target,camera in context:
        w,h=target['size'];scale=512/max(w,h);cameras.append(dict(camera=camera,size=[round(w*scale),round(h*scale)]))
    opposite=copy.deepcopy(cameras[0]);opposite['camera'].update(azimuth_deg=opposite['camera']['azimuth_deg']+110,elevation_deg=20)
    cameras.append(opposite)
    configuration=dict(plan=numeric_plan(plan),plan_sha256=digest,output=str(output),cameras=cameras,
        max_triangles=min(500000,settings.blender_max_triangles),device=settings.blender_device,threads=settings.blender_threads)
    config_path=output/'model_config.json';config_path.write_text(json.dumps(configuration),encoding='utf-8')
    script=Path(__file__).resolve().parents[2]/'blender'/'model_furniture.py'
    command=[str(executable),'--background','--factory-startup','--disable-autoexec','--threads',str(settings.blender_threads),
        '--python-exit-code','1','--python',str(script),'--','--config',str(config_path)]
    progress('blender_building_parts')
    try:
        with (output/'stdout.log').open('w',encoding='utf-8') as stdout,(output/'stderr.log').open('w',encoding='utf-8') as stderr:
            result=subprocess.run(command,shell=False,stdout=stdout,stderr=stderr,timeout=settings.blender_timeout,
                env={k:v for k,v in os.environ.items() if not k.endswith('_API_KEY')},
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    except subprocess.TimeoutExpired as exc:raise AIServiceError('BLENDER_MODEL_TIMEOUT','Background modeling exceeded its time budget; reduce detail or views',True) from exc
    except OSError as exc:raise AIServiceError('BLENDER_MODEL_FAILED','Background Blender could not start',True) from exc
    if result.returncode:
        failure=output/'model_failure.json'
        reason=json.loads(failure.read_text(encoding='utf-8')).get('message','') if failure.is_file() else 'See the private modeling diagnostics'
        raise AIServiceError('BLENDER_MODEL_FAILED','Native Blender modeling failed: '+reason[:500])
    names=['reconstruction.glb','reconstruction.blend','model_mesh.json','model_report.json']+[f'view_{i}.png' for i in range(len(cameras))]
    if any(not (output/name).is_file() for name in names):raise AIServiceError('BLENDER_MODEL_INVALID','Blender did not produce every required model artifact')
    report=json.loads((output/'model_report.json').read_text(encoding='utf-8'))
    if report.get('source')!='NATIVE_BLENDER_GEOMETRY' or report.get('plan_sha256')!=digest:
        raise AIServiceError('BLENDER_MODEL_INTEGRITY_FAILED','Native geometry report does not belong to this plan')
    native=load_native(output/'model_mesh.json',plan,configuration['max_triangles']);cad=mesh_from_build(build,plan)
    progress('blender_comparing_geometry');agreement=compare_meshes(native,cad,settings.blender_cad_tolerance_mm)
    views=[]
    for i,(target,camera) in enumerate(context):
        with Image.open(output/f'view_{i}.png') as raster:
            if raster.format!='PNG' or raster.size!=tuple(cameras[i]['size']):raise AIServiceError('BLENDER_MODEL_INVALID','Invalid model render')
            alpha=np.array(raster.convert('RGBA'))[:,:,3]
        mask=cv2.resize(alpha,target['size'],interpolation=cv2.INTER_AREA)>=128
        measured=metrics(mask,target,native,camera,target['view'].landmarks) if target['usable'] else None
        cad_metrics=metrics(render_mask(cad,camera,target['size']),target,cad,camera,target['view'].landmarks) if target['usable'] else None
        overlay=target['image'].copy();overlay[target['edge']]=[230,65,65];overlay[boundary(mask)]=[25,190,100]
        name=f'comparison_{i}.png';(output/name).write_bytes(png_bytes(overlay));names.append(name)
        mask_name=f'mask_{i}.png';(output/mask_name).write_bytes(png_bytes(target['mask'].astype('uint8')*255));names.append(mask_name)
        views.append(dict(image_id=str(target['view'].image_id),camera=camera,mask_reviewed=target['view'].mask_reviewed,
            mask_method=target['method'],mask_usable=target['usable'],metrics=measured,cad_photo_metrics=cad_metrics,
            silhouette_source='ACTUAL_BLENDER_RENDER_ALPHA',warnings=target['warnings']))
    report.update(version=VERSION,cad_agreement=agreement,views=views,
        exact_reconstruction_verified=False,physical_dimensions_verified=False,approved_for_manufacturing=False)
    (output/'model_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    files={name:file_hash(output/name) for name in names}
    with ZipFile(output/'blender_model_package.zip','w',ZIP_DEFLATED) as archive:
        for name in names:archive.write(output/name,name)
    files['blender_model_package.zip']=file_hash(output/'blender_model_package.zip')
    return dict(plan_sha256=digest,directory=output.name,files=files,report=report,
        component_labels=[dict(part_id=p.part_id,label=p.label,material=p.material) for p in plan.parts])
