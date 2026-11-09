"""Bounded local camera/curve fitting. Mesh proxies propose; actual CAD decides.

No model calls, arbitrary code, silhouette-derived physical measurements, or
changes to saved revisions. A foreground overlap score is not CAD accuracy.
"""
import ast
import copy
import math
import time
from dataclasses import dataclass,field
import cv2
import numpy as np
from scipy.optimize import minimize
from scipy.spatial import cKDTree
from app.photo_schema import PhotoDesign,number
from app.reference_fit_schema import ReferenceFitOptions
from .reference_mask import foreground,png_bytes

VERSION=1


class FitBudgetExceeded(RuntimeError): pass


@dataclass
class MeshPart:
    points: np.ndarray
    triangles: np.ndarray
    path_ends: dict = field(default_factory=dict)


def rotation_matrix(angles):
    x,y,z=np.radians(angles);cx,sx=math.cos(x),math.sin(x);cy,sy=math.cos(y),math.sin(y);cz,sz=math.cos(z),math.sin(z)
    return np.array([[cz,-sz,0],[sz,cz,0],[0,0,1]])@np.array([[cy,0,sy],[0,1,0],[-sy,0,cy]])@np.array([[1,0,0],[0,cx,-sx],[0,sx,cx]])


def mesh_from_build(build,plan):
    values={v.key:v.value for v in plan.values};mesh={};total=0
    for part in plan.parts:
        vertices,faces=build.shapes[part.part_id].val().copy(mesh=False).tessellate(1.0,.3)
        points=np.array([v.toTuple() for v in vertices],dtype=float);triangles=np.array(faces,dtype=np.int32)
        total+=len(triangles)
        if total>250000: raise ValueError('Reference fitting triangle budget exceeded')
        ends={}
        if part.path:
            r=rotation_matrix([number(v,values) for v in part.rotation_deg]);origin=np.array([number(v,values) for v in part.position])
            ends={key:r@np.array([number(v,values) for v in p])+origin for key,p in [('path_start',part.path[0]),('path_end',part.path[-1])]}
        mesh[part.part_id]=MeshPart(points,triangles,ends)
    return mesh


def frame_for(mesh):
    points=np.concatenate([m.points for m in mesh.values()]);low,high=points.min(0),points.max(0)
    return dict(center=((low+high)/2).tolist(),radius=float(np.linalg.norm(high-low)/2))


def project(points,camera,size):
    az,el,roll=np.radians([camera['azimuth_deg'],camera['elevation_deg'],camera['roll_deg']])
    toward=np.array([math.cos(el)*math.cos(az),math.cos(el)*math.sin(az),math.sin(el)])
    right=np.cross([0.,0.,1.],toward);right/=np.linalg.norm(right);up=np.cross(toward,right)
    local=(points-np.array(camera['center']))/camera['radius']
    denominator=1-local@toward*camera['inverse_distance']
    if np.any(denominator<.05):raise ValueError('Fitting camera intersects the model')
    xy=np.column_stack((local@right,-local@up))/denominator[:,None]
    xy=xy@np.array([[math.cos(roll),math.sin(roll)],[-math.sin(roll),math.cos(roll)]])
    width,height=size
    scale=math.exp(camera['log_scale'])*width/camera.get('viewport',[width,height])[0]
    return xy*scale+np.array([camera['pan_x']*width,camera['pan_y']*height])


def render_mask(mesh,camera,size):
    width,height=size;mask=np.zeros((height,width),np.uint8)
    for part in mesh.values():
        points=project(part.points,camera,size);triangles=points[part.triangles]
        visible=(triangles[:,:,0].max(1)>=0)&(triangles[:,:,0].min(1)<width)&(triangles[:,:,1].max(1)>=0)&(triangles[:,:,1].min(1)<height)
        for triangle in np.rint(triangles[visible]*16).astype(np.int32):
            cv2.fillConvexPoly(mask,triangle,1,shift=4)
    return mask.astype(bool)


def anchor(part,kind):
    if kind in ('path_start','path_end'):
        if kind not in part.path_ends:raise ValueError('That component has no path endpoint')
        return part.path_ends[kind]
    low,high=part.points.min(0),part.points.max(0)
    if kind=='center':return (low+high)/2
    z=high[2] if kind=='top' else low[2]
    selected=part.points[np.abs(part.points[:,2]-z)<=max(.5,(high[2]-low[2])*.02)]
    p=selected.mean(0);p[2]=z;return p


def boundary(mask):
    return mask&~cv2.erode(mask.astype(np.uint8),np.ones((3,3),np.uint8)).astype(bool)


def metrics(predicted,target,mesh=None,camera=None,landmarks=()):
    mask=target['mask'];union=np.logical_or(mask,predicted).sum()
    iou=float(np.logical_and(mask,predicted).sum()/union) if union else 0.
    a,b=boundary(predicted),target['edge'];diagonal=math.hypot(*mask.shape)
    if not a.any() or not b.any():chamfer=1.
    else:
        dt=cv2.distanceTransform((~a).astype(np.uint8),cv2.DIST_L2,3)
        chamfer=float((target['distance'][a].mean()+dt[b].mean())/(2*diagonal))
    landmark_error=None
    if landmarks and mesh is not None:
        size=(mask.shape[1],mask.shape[0]);errors=[]
        for mark in landmarks:
            p=project(anchor(mesh[mark.part_id],mark.anchor)[None],camera,size)[0]
            errors.append(np.linalg.norm(p-np.array(mark.image_point)*np.array(size))/diagonal)
        landmark_error=float(np.mean(errors))
    edge_loss=min(1,chamfer*10)
    loss=.75*(1-iou)+.25*edge_loss
    if landmark_error is not None:loss=.65*(1-iou)+.20*edge_loss+.15*min(1,landmark_error*5)
    return dict(silhouette_iou=iou,boundary_error_fraction=chamfer,landmark_error_fraction=landmark_error,loss=float(loss))


def prepare_target(image_bytes,view,resolution):
    result=foreground(image_bytes,view.mask_png,max_side=384)
    h,w=result['mask'].shape;scale=resolution/max(w,h);size=(max(32,round(w*scale)),max(32,round(h*scale)))
    mask=cv2.resize(result['mask'].astype(np.uint8),size,interpolation=cv2.INTER_NEAREST).astype(bool)
    edge=boundary(mask)
    return {**result,'mask':mask,'image':cv2.resize(result['image'],size,interpolation=cv2.INTER_AREA),
        'edge':edge,'distance':cv2.distanceTransform((~edge).astype(np.uint8),cv2.DIST_L2,3),
        'size':size,'view':view}


def initial_camera(mesh,target,frame,azimuth,elevation,inverse=.1):
    camera={**frame,'azimuth_deg':float(azimuth),'elevation_deg':float(elevation),'roll_deg':0.,
            'inverse_distance':inverse,'log_scale':0.,'pan_x':0.,'pan_y':0.,'viewport':list(target['size'])}
    points=project(np.concatenate([m.points for m in mesh.values()]),camera,target['size'])
    yy,xx=np.where(target['mask']);low,high=points.min(0),points.max(0)
    span=np.maximum(high-low,1e-6)
    scale=min((xx.max()-xx.min()+1)/span[0],(yy.max()-yy.min()+1)/span[1])
    pan=np.array([(xx.max()+xx.min())/2,(yy.max()+yy.min())/2])-(low+high)/2*scale
    camera.update(log_scale=math.log(scale),pan_x=float(pan[0]/target['size'][0]),pan_y=float(pan[1]/target['size'][1]))
    return camera


def fit_camera(mesh,target,frame,hint,options,deadline):
    view=target['view'];az=view.azimuth_deg if view.azimuth_deg is not None else hint.get('azimuth_deg',-110)
    el=view.elevation_deg if view.elevation_deg is not None else hint.get('elevation_deg',15)
    original=initial_camera(mesh,target,frame,az,el)
    original_metrics=metrics(render_mask(mesh,original,target['size']),target,mesh,original,view.landmarks)
    best=dict(camera=original,metrics=original_metrics);calls=0
    names=['azimuth_deg','elevation_deg','roll_deg','inverse_distance','log_scale','pan_x','pan_y']
    def evaluate(camera):
        nonlocal calls,best
        if calls>=options.camera_evaluations or time.monotonic()>deadline:raise FitBudgetExceeded()
        calls+=1
        try:value=metrics(render_mask(mesh,camera,target['size']),target,mesh,camera,view.landmarks)
        except ValueError:return 10.
        if value['loss']<best['metrics']['loss']:best=dict(camera=dict(camera),metrics=value)
        return value['loss']
    try:
        for da in (0,-20,20):
            for de in (0,-8,8):
                candidate=initial_camera(mesh,target,frame,az+da,max(-5,min(65,el+de)))
                evaluate(candidate)
        start=[best['camera'][k] for k in names]
        bounds=[(az-35,az+35),(-5,65),(-7,7),(0,.35),(math.log(min(target['size'])*.12),math.log(max(target['size'])*3)),(-.15,1.15),(-.15,1.15)]
        def objective(values):return evaluate({**frame,'viewport':list(target['size']),**dict(zip(names,[float(v) for v in values]))})
        minimize(objective,start,method='Powell',bounds=bounds,options={'maxfev':options.camera_evaluations-calls,'xtol':.02,'ftol':.0005})
    except FitBudgetExceeded:pass
    return dict(original_camera=original,original_metrics=original_metrics,camera=best['camera'],camera_metrics=best['metrics'],evaluations=calls)


def referenced_names(part):
    names=set()
    def walk(value):
        if isinstance(value,dict):
            for k,v in value.items():
                if k not in ('label','evidence','part_id','material','geometry','color_rgb'):walk(v)
        elif isinstance(value,list):
            for v in value:walk(v)
        elif isinstance(value,str):
            try:names.update(n.id for n in ast.walk(ast.parse(value,mode='eval')) if isinstance(n,ast.Name))
            except SyntaxError:pass
    walk(part.model_dump(mode='json'));return names


def bezier_samples(points):
    samples=[];weights=[];length=len(points)
    for start in range(0,length-1,3):
        t=np.linspace(0,1,24);basis=np.column_stack(((1-t)**3,3*(1-t)**2*t,3*(1-t)*t*t,t**3))
        samples.append(basis@points[start:start+4]);w=np.zeros((len(t),length));w[:,start:start+4]=basis;weights.append(w)
    return np.concatenate(samples),np.concatenate(weights)


def actions_for(plan,mesh,inputs,options,targets,cameras):
    known={v['key'] for v in inputs.get('known_values',[])};values={v.key:v.value for v in plan.values}
    locked=[p.part_id for p in plan.parts if referenced_names(p)&known]
    eligible=[p for p in plan.parts if p.part_id not in locked and (not options.component_ids or p.part_id in options.component_ids)]
    priority={}
    for p in eligible:
        errors=[]
        for target,camera in zip(targets,cameras):
            xy=np.rint(project(mesh[p.part_id].points,camera,target['size'])).astype(int)
            xy[:,0]=np.clip(xy[:,0],0,target['size'][0]-1);xy[:,1]=np.clip(xy[:,1],0,target['size'][1]-1)
            errors.append(float((target['distance'][xy[:,1],xy[:,0]]*~target['mask'][xy[:,1],xy[:,0]]).mean()))
        priority[p.part_id]=float(np.mean(errors))
    eligible.sort(key=lambda p:priority[p.part_id],reverse=True)
    actions=[]
    for part in eligible:
        m=mesh[part.part_id];origin=np.array([number(v,values) for v in part.position]);rotation=rotation_matrix([number(v,values) for v in part.rotation_deg])
        local=(m.points-origin)@rotation
        for axis in (0,1):
            actions.append(dict(part_id=part.part_id,kind='position',axis=axis,direction=np.eye(3)[axis],weights=np.ones(len(m.points)),amount=options.max_displacement_mm))
        if part.geometry=='loft' and len(part.sections)>2:
            zs=np.array([number(s.center[2],values) for s in part.sections]);span=zs[-1]-zs[0]
            if abs(span)>1:
                weights=np.sin(np.pi*np.clip((local[:,2]-zs[0])/span,0,1))
                for axis in (0,1):actions.append(dict(part_id=part.part_id,kind='loft_bend',axis=axis,direction=rotation[:,axis],weights=weights,amount=options.max_displacement_mm))
        if part.path and part.curve_mode=='bezier':
            points=np.array([[number(v,values) for v in row] for row in part.path]);samples,coefficients=bezier_samples(points)
            _,nearest=cKDTree(samples).query(local)
            for knot in range(3,len(points)-1,3):
                group=[knot-1,knot,knot+1];weights=coefficients[nearest][:,group].sum(1)
                for axis in (0,1,2):actions.append(dict(part_id=part.part_id,kind='path_knot',axis=axis,knot=knot,direction=rotation[:,axis],weights=weights,amount=options.max_displacement_mm))
        if len(actions)>=36:break
    return actions[:36],locked


def deform(mesh,actions,parameters):
    result={k:MeshPart(v.points.copy(),v.triangles,{n:p.copy() for n,p in v.path_ends.items()}) for k,v in mesh.items()}
    for action,value in zip(actions,parameters):
        delta=action['direction']*action['amount']*value;part=result[action['part_id']]
        part.points+=action['weights'][:,None]*delta
        if action['kind']=='position':
            for name in part.path_ends:part.path_ends[name]+=delta
    return result


def adjusted_plan(plan,actions,parameters):
    raw=plan.model_dump(mode='json');parts={p['part_id']:p for p in raw['parts']};values={v.key:v.value for v in plan.values}
    def add(value,delta):
        if abs(delta)<1e-5:return value
        if isinstance(value,(float,int)):return float(value)+float(delta)
        return f'({value})+({delta:.5f})'
    for action,value in zip(actions,parameters):
        delta=action['amount']*float(value)
        if abs(delta)<1e-5:continue
        part=parts[action['part_id']];axis=action['axis']
        if action['kind']=='position':part['position'][axis]=add(part['position'][axis],delta)
        elif action['kind']=='loft_bend':
            z0,z1=[number(s['center'][2],values) for s in (part['sections'][0],part['sections'][-1])]
            for section in part['sections'][1:-1]:
                weight=math.sin(math.pi*(number(section['center'][2],values)-z0)/(z1-z0))
                section['center'][axis]=add(section['center'][axis],delta*weight)
        else:
            for index in (action['knot']-1,action['knot'],action['knot']+1):part['path'][index][axis]=add(part['path'][index][axis],delta)
    return PhotoDesign.model_validate(raw)


def fit_plan(plan,inputs,name,view_inputs,options=None,observations=None,progress=lambda stage:None):
    """Return a NEW plan only after independent B-Rep rebuilding improves the score."""
    from app.agents import photo_agent
    options=ReferenceFitOptions.model_validate(options or {});observations=observations or {}
    started=time.monotonic();deadline=started+options.max_seconds
    targets=[prepare_target(pixels,view,options.resolution) for view,pixels in view_inputs]
    report=dict(version=VERSION,status='MASK_NEEDS_REVIEW',geometry_changed=False,
        exact_reconstruction_verified=False,physical_dimensions_verified=False,views=[],changes=[],rejections=[],warnings=[])
    if not targets or any(not t['usable'] for t in targets):
        report['warnings']=['Correct the empty, clipped or ambiguous foreground mask before fitting.']
        return plan,report,targets,[]
    for target in targets:report['warnings'].extend(target['warnings'])
    progress('reference_fit_camera')
    spec,_=photo_agent.specification(plan,inputs,name)
    build=photo_agent.preflight(spec,{v['key'] for v in inputs.get('known_values',[])},check_estimates=False)
    mesh=mesh_from_build(build,plan);frame=frame_for(mesh)
    known_parts=set(mesh)
    if set(options.component_ids)-known_parts:raise ValueError('A fitting component does not belong to this design')
    for target in targets:
        for mark in target['view'].landmarks:
            if mark.part_id not in known_parts:raise ValueError('A landmark component does not belong to this design')
            anchor(mesh[mark.part_id],mark.anchor)
    fitted=[]
    for target in targets:
        fitted.append(fit_camera(mesh,target,frame,observations.get('reference_view') or {},options,
            min(deadline, time.monotonic()+options.max_seconds*.5/len(targets))))
    cameras=[f['camera'] for f in fitted]
    baseline=[metrics(render_mask(mesh,c,t['size']),t,mesh,c,t['view'].landmarks) for t,c in zip(targets,cameras)]
    baseline_loss=float(np.mean([m['loss'] for m in baseline]));best_loss=baseline_loss
    actions,locked=actions_for(plan,mesh,inputs,options,targets,cameras)
    parameters=np.zeros(len(actions));calls=0
    progress('reference_fit_geometry')
    # Coordinate search has an explicit evaluation budget and a smoothness prior.
    # All geometry trials use the SAME fitted cameras and masks.
    for step in (1.,.5,.25):
        changed=False
        for index in range(len(actions)):
            for direction in (-1,1):
                if calls>=options.geometry_evaluations or time.monotonic()>deadline:break
                trial=parameters.copy();trial[index]=np.clip(trial[index]+direction*step,-1,1)
                if trial[index]==parameters[index]:continue
                proxy=deform(mesh,actions,trial);calls+=1
                values=[metrics(render_mask(proxy,c,t['size']),t,proxy,c,t['view'].landmarks)['loss'] for t,c in zip(targets,cameras)]
                loss=float(np.mean(values))+.001*float(np.mean(trial**2))
                if loss+1e-6<best_loss:parameters=trial;best_loss=loss;changed=True
            if calls>=options.geometry_evaluations or time.monotonic()>deadline:break
        if not changed or calls>=options.geometry_evaluations or time.monotonic()>deadline:break
    selected_mesh=mesh;selected_plan=plan;after=baseline;accepted_parameters=None
    if best_loss<baseline_loss-.001 and np.any(parameters):
        progress('reference_fit_cad_validation')
        for factor in (1.,.5,.25)[:options.cad_candidates]:
            if time.monotonic()>deadline:break
            try:
                candidate=adjusted_plan(plan,actions,parameters*factor)
                candidate,_,_,candidate_build,_=photo_agent.resolve_estimated_geometry(candidate,inputs,name)
                if locked:
                    from app.agents.photo_fidelity import geometry_changed
                    if geometry_changed(build,candidate_build,locked):
                        raise ValueError('Candidate changes a component held fixed by a supplied dimension')
                actual=mesh_from_build(candidate_build,candidate)
                measured=[metrics(render_mask(actual,c,t['size']),t,actual,c,t['view'].landmarks) for t,c in zip(targets,cameras)]
                # Reject a proxy improvement if actual CAD fails, regresses any
                # supplied view, or worsens a supplied landmark correspondence.
                improves=float(np.mean([m['loss'] for m in measured]))<baseline_loss-.001
                nonregressing=all(a['loss']<=b['loss']+.003 and
                    (b['landmark_error_fraction'] is None or a['landmark_error_fraction']<=b['landmark_error_fraction']+.002)
                    for a,b in zip(measured,baseline))
                if not (improves and nonregressing):raise ValueError('Actual CAD did not improve the fixed-camera reference comparison')
                selected_plan,selected_mesh,after,accepted_parameters=candidate,actual,measured,parameters*factor;break
            except Exception as exc:report['rejections'].append(str(exc)[:350])
    report.update(status='IMPROVED' if accepted_parameters is not None else 'NO_GEOMETRY_IMPROVEMENT',
        geometry_changed=accepted_parameters is not None,locked_parts=locked,geometry_evaluations=calls,
        elapsed_seconds=round(time.monotonic()-started,3),
        geometry_loss_before=baseline_loss,geometry_loss_after=float(np.mean([m['loss'] for m in after])))
    if accepted_parameters is not None:
        report['changes']=[dict(part_id=a['part_id'],operation=a['kind'],axis='XYZ'[a['axis']],
            displacement_mm=round(float(v*a['amount']),5),**({'knot':a['knot']} if 'knot' in a else {}))
            for a,v in zip(actions,accepted_parameters) if abs(v*a['amount'])>1e-5]
    artifacts=[]
    for index,(target,camera,data,before,measured) in enumerate(zip(targets,cameras,fitted,baseline,after)):
        report['views'].append(dict(image_id=str(target['view'].image_id),mask_method=target['method'],
            mask_reviewed=target['view'].mask_reviewed,foreground_fraction=target['foreground_fraction'],
            original_camera=data['original_camera'],camera=camera,camera_evaluations=data['evaluations'],
            before_camera_fit=data['original_metrics'],before_geometry_fit=before,after_geometry_fit=measured))
        old=render_mask(mesh,camera,target['size']);new=render_mask(selected_mesh,camera,target['size'])
        overlay=target['image'].copy();overlay[target['edge']]=[230,65,65]
        overlay[boundary(old)]=[60,110,245];overlay[boundary(new)]=[25,190,100]
        artifacts.extend([(f'mask_{index}.png',png_bytes(target['mask'].astype(np.uint8)*255)),
                          (f'overlay_{index}.png',png_bytes(overlay)),
                          (f'before_{index}.png',png_bytes(old.astype(np.uint8)*255)),
                          (f'after_{index}.png',png_bytes(new.astype(np.uint8)*255))])
    report['warnings'].extend(['Silhouette overlap measures visible projection only, not complete 3D or manufacturing accuracy.',
        'Hidden surfaces and interior sculpting cannot be determined from silhouette matching.',
        'Support contacts and joinery are unverified after fitting; inspect the assembly before manufacture.',
        'Camera estimates and all unprovided dimensions remain unverified.'])
    return selected_plan,report,targets,artifacts
