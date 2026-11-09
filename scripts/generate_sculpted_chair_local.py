"""Native sculpted-chair reconstruction from user images, with zero API calls.

The conversation's large black-seat image defines appearance. The supplied local
JPG is an alternate tan-seat view used for floor landmarks and camera estimation.
Every dimension is an editable estimate. --preview-only builds without publishing.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
from uuid import uuid4, UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
from scipy.optimize import minimize_scalar
from app.photo_schema import PhotoDesign
from app.pipeline import canonical_hash, file_hash
from app.cad_optimizer_schema import CADCamera, CADViewEvidence, CADOptimizationOptions
from app.cad_optimizer import optimize_cad_isolated
from scripts.generate_table_a_fresh_local import freeze, preview_camera


def seat_outline():
    # Eight native cubic spans: four straight rails and four rounded corners.
    w,d,r=550.,500.,35.
    k=.5522847498307936
    a,b=w/2,d/2
    points=[[-a+r,-b],[-a+r+(w-2*r)/3,-b],[a-r-(w-2*r)/3,-b],[a-r,-b],
        [a-r+k*r,-b],[a,-b+r-k*r],[a,-b+r],
        [a,-b+r+(d-2*r)/3],[a,b-r-(d-2*r)/3],[a,b-r],
        [a,b-r+k*r],[a-r+k*r,b],[a-r,b],
        [a-r-(w-2*r)/3,b],[-a+r+(w-2*r)/3,b],[-a+r,b],
        [-a+r-k*r,b],[-a,b-r+k*r],[-a,b-r],
        [-a,b-r-(d-2*r)/3],[-a,-b+r+(d-2*r)/3],[-a,-b+r],
        [-a,-b+r-k*r],[-a+r-k*r,-b],[-a+r,-b]]
    return [[f'seat_width_mm*{x/w:.12g}',f'seat_depth_mm*{y/d:.12g}'] for x,y in points]


def design():
    values=dict(width_mm=640,depth_mm=600,height_mm=840,seat_width_mm=550,seat_depth_mm=500,
        wood_top_mm=445,frame_thickness_mm=30,cushion_width_mm=525,cushion_depth_mm=470,
        cushion_thickness_mm=32,seat_height_mm=477,leg_foot_x_mm=245,leg_foot_y_mm=245,
        leg_root_x_mm=230,leg_root_y_mm=215,back_top_mm=798,back_y_mm=290)
    wood=[.43,.275,.17]
    parts=[]
    def put(label,geometry,size,**kwargs):
        parts.append(dict(part_id=f'F{len(parts)+1:03d}',label=label,geometry=geometry,size=size,
            material=kwargs.pop('material','solid_wood'),color_rgb=kwargs.pop('color_rgb',wood),
            evidence=kwargs.pop('evidence','Locally interpreted from the user chair references; dimensions and hidden construction unverified'),**kwargs))
    put('Rounded wooden seat frame and assumed concealed seat support','outline_panel',
        ['seat_width_mm','seat_depth_mm','frame_thickness_mm'],outline=seat_outline(),curve_mode='bezier',
        position=[0,0,'wood_top_mm-frame_thickness_mm/2'])
    put('Dark upholstered seat cushion from the primary image','box',
        ['cushion_width_mm','cushion_depth_mm','cushion_thickness_mm'],corner_radius=42,edge_radius=9,
        position=[0,0,'wood_top_mm+cushion_thickness_mm/2'],material='fabric',color_rgb=[.105,.10,.095],
        evidence='Dark cushion follows the primary inline image; the local alternate-view JPG has a different tan upholstery color')
    # One continuous native cubic path, with matched tangent directions at joins.
    path=[[-255,205,445],[-280,155,480],[-210,10,550],[-210,40,610],
        [-210,70,670],[-290,100,710],[-290,150,755],[-290,200,800],
        [-140,290,798],[0,290,798],[140,290,798],[290,200,800],[290,150,755],
        [290,100,710],[210,70,670],[210,40,610],[210,10,550],[280,155,480],[255,205,445]]
    for point in path:
        if point[2]==798:point[2]='back_top_mm'
        if point[1]==290:point[1]='back_y_mm'
    put('Continuous sculpted wraparound back and flowing arm supports','profile_sweep',[60,70,1],
        path=path,curve_mode='bezier',sweep_frame='WORLD_UP',sweep_sections=[
            dict(fraction=f,width=w,depth=d) for f,w,d in
            [(0,32,36),(.12,32,40),(.27,36,52),(.4,42,70),(.5,46,84),
             (.6,42,70),(.73,36,52),(.88,32,40),(1,32,36)]],
        evidence='The primary image shows a broad curved crown and S-shaped side supports, not a rectangular back or separate straight arms')
    put('Curved flared central wooden back splat','loft',[200,42,365],loft_mode='BOUNDED',sections=[
        dict(center=[0,250,430],width=88,depth=32,profile='ellipse'),
        dict(center=[0,263,520],width=72,depth=28,profile='ellipse'),
        dict(center=[0,282,640],width=78,depth=28,profile='ellipse'),
        dict(center=[0,296,722],width=120,depth=34,profile='ellipse'),
        dict(center=[0,298,775],width=200,depth=42,profile='ellipse'),
        dict(center=[0,'back_y_mm','back_top_mm+5'],width=205,depth=42,profile='ellipse')],
        evidence='Single curved splat rises from the rear seat rail and fans into the crown, leaving two large side openings')
    for sx,sy in [(-1,-1),(1,-1),(-1,1),(1,1)]:
        sections=[]
        for t,z,w,d in [(0,0,24,26),(250/440,250,45,44),(1,440,70,66)]:
            sections.append(dict(center=[f'{sx}*(leg_foot_x_mm*(1-{t})+leg_root_x_mm*{t})',
                f'{sy}*(leg_foot_y_mm*(1-{t})+leg_root_y_mm*{t})',z],width=w,depth=d,profile='ellipse'))
        put(('Front' if sy<0 else 'Rear')+(' left' if sx<0 else ' right')+' curved tapered leg',
            'loft',[80,70,440],loft_mode='BOUNDED',sections=sections,
            evidence='Rounded tapered legs and expanded upper shoulders; floor continuation uses the supplied full-chair alternate view')
    return PhotoDesign(furniture_type='Sculpted wooden dining chair with dark cushion',
        summary='Fresh locally authored native CAD interpretation of the primary black-seat image and the supplied full-chair alternate view. No OpenAI requests.',
        values=[dict(key=key,value=value,explanation='Editable local design estimate; not physically measured') for key,value in values.items()],
        parts=parts,assumptions=[
            'All dimensions are local estimates; no measured chair dimensions were supplied.',
            'The conversation inline image is the primary shape and dark-upholstery reference; the local JPG is a different view with tan upholstery.',
            'The alternate JPG supplies visible floor contacts. Camera and correspondence annotations remain approximate.',
            'Four-leg layout and approximate bilateral symmetry are interpreted; concealed seat support and joints are not established.',
            'Sweeps and lofts approximate the sculpted wood; wood grain, carving relief and exact surface blends are not reconstructed.'],
        unrepresented_features=['Wood grain and upholstery texture','Production joinery, fasteners and machining allowances','Exact carved surface blends and leather seams'])


def estimate_camera(plan,size):
    values={v.key:v.value for v in plan.values}
    w,d,h=(values[key] for key in ('seat_width_mm','seat_depth_mm','wood_top_mm'))
    world=np.array([[-w/2,-d/2,h],[-w/2,d/2,h],[w/2,d/2,h],[w/2,-d/2,h]])
    # Estimated intersections of the seat-frame rails in the 447px local JPG.
    pixels=np.array([[215.,236.],[131.,215.],[270.,181.],[362.,201.]])*np.array(size)/447.
    def solve(focal):
        k=np.array([[focal,0,size[0]/2],[0,focal,size[1]/2],[0,0,1.]])
        ok,r,t=cv2.solvePnP(world,pixels,k,None,flags=cv2.SOLVEPNP_ITERATIVE)
        if not ok:raise ValueError('Could not estimate chair reference camera')
        predicted,_=cv2.projectPoints(world,r,t,k,None)
        return float(np.mean(np.sum((predicted.reshape(-1,2)-pixels)**2,axis=1))),k,r,t
    fit=minimize_scalar(lambda f:solve(f)[0],bounds=(size[0]*.6,size[0]*12),method='bounded',options={'maxiter':60})
    error,k,r,t=solve(float(fit.x))
    if np.sqrt(error)>8:raise ValueError('Seat annotations do not support this estimated camera')
    camera=CADCamera(image_size=size,intrinsic_matrix=k.tolist(),rotation_vector=tuple(r.ravel()),
        translation_mm=tuple(t.ravel()),reliable=False,
        uncertainty='Approximate pose from manually annotated seat rails and assumed seat dimensions; not a calibrated camera')
    return camera,dict(camera=camera.model_dump(mode='json'),corner_rms_px=float(np.sqrt(error)),
        pixels=pixels.tolist(),world_points_mm=world.tolist(),source='USER_SUPPLIED_ALTERNATE_VIEW')


def local_analysis(plan):
    from app.agents.photo_agent import PhotoAnalysis
    from app.ai.schemas import FurnitureVisionResult
    fields=[('width_mm','Overall width'),('depth_mm','Overall depth'),('height_mm','Overall height'),
            ('seat_width_mm','Seat frame width'),('seat_depth_mm','Seat frame depth'),('seat_height_mm','Cushion top height')]
    values={v.key:v.value for v in plan.values}
    vision=FurnitureVisionResult(category=plan.furniture_type,
        components=[dict(component_id=p.part_id,label=p.label,kind='leg' if 'leg' in p.label.lower() else 'frame',
            possible_materials=['solid_wood'],provenance='IMAGE_OBSERVED',evidence=p.evidence) for p in plan.parts],
        proportions=['Broad back crown, narrow central splat and large open side spaces','Thin dark cushion above the wooden seat frame'],
        observations=['Local visual interpretation from the conversation and supplied JPG; no vision API request'],
        assumptions=plan.assumptions,missing_measurements=[dict(field=k,reason='No physical measurement supplied') for k,_ in fields],
        limitations=plan.unrepresented_features)
    return PhotoAnalysis(vision=vision,matching_inputs=[dict(key=k,label=label,unit='mm',estimated_value=values[k],
        explanation='Locally interpreted estimate, not a measured or API-generated value') for k,label in fields],
        summary='LOCAL VISUAL NOTES / NO API: sculpted wooden chair; black cushion follows the primary inline image.',
        reference_view=dict(azimuth_deg=-120,elevation_deg=15))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference',type=Path,default=Path(r'C:\Users\HP\Desktop\images (11).jpg'))
    parser.add_argument('--preview-only',action='store_true')
    parser.add_argument('--skip-fit',action='store_true')
    args=parser.parse_args()
    from PIL import Image
    from app.config import Settings
    from app.geometry_repairs import resolve_repaired_geometry
    from app.engineering_cad import geometry_checks,render_preview
    from app.ai.geometry_preview import render_build
    config=Settings(ai_enabled=False)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')+'-'+uuid4().hex[:6]
    output=config.storage/('sculpted-chair-local-'+stamp);output.mkdir(parents=True,exist_ok=False)
    def write(name,value):
        (output/name).write_text(json.dumps(value,indent=2,allow_nan=False),encoding='utf-8')
    raw=dict(known_values=[],dimension_sources={})
    print(json.dumps(dict(stage='building_native_chair',output=str(output),ai_requests=0)),flush=True)
    plan,spec,values,build,adjustments,repairs=resolve_repaired_geometry(design(),raw,'Sculpted chair local reference')
    checks=geometry_checks(build)
    if not checks['furniture_items_connected']:raise ValueError('The local chair must form a connected assembly: '+str(checks['contact_groups']))
    write('initial_plan.json',plan.model_dump(mode='json'));write('geometry_checks.json',checks)
    shutil.copyfile(args.reference,output/'alternate_reference.jpg')
    with Image.open(args.reference) as image:size=image.size
    camera,camera_report=estimate_camera(plan,size)
    fitted_preview=preview_camera(camera)
    write('camera_estimate.json',camera_report)
    (output/'chair_reference_view.png').write_bytes(render_build(build,size=(1100,1100),camera=fitted_preview))
    (output/'chair_primary_view.png').write_bytes(render_preview(build,-135,14,size=(1400,1100)))
    print(json.dumps(dict(stage='initial_geometry_valid',part_count=len(build.parts),corner_rms_px=camera_report['corner_rms_px'],
        estimated_dimensions_mm={key:getattr(spec,key) for key in ('width_mm','depth_mm','height_mm')})),flush=True)
    if args.preview_only:return
    reference_uuid=uuid4()
    view=CADViewEvidence(image_id=reference_uuid,camera=camera,landmarks=[dict(part_id=f'F{index:03d}',anchor='section_center',
        point_index=0,image_point=(np.array(pixel)*np.array(size)/447.).tolist(),confidence=.7,evidence_id='V004')
        for index,pixel in [(5,[222,388]),(6,[361,347]),(7,[128,336]),(8,[251,307])]])
    options=CADOptimizationOptions(views=[view],component_ids=['F005','F006','F007','F008'],
        parameter_ids=['leg_foot_x_mm','leg_foot_y_mm'],feature_components={'V004':['F005','F006','F007','F008']},
        mismatch_feature_ids=['V004'],mode='balanced',max_evaluations=24,max_seconds=150,max_displacement_mm=45,
        allow_uncertain_camera=True,max_structural_llm_calls=0,enabled=not args.skip_fit)
    print(json.dumps(dict(stage='local_foot_landmark_fit',ai_requests=0)),flush=True)
    selected=optimize_cad_isolated(plan,options,inputs=raw,name='Sculpted chair local reference')
    plan,spec,values,build,adjustments,repairs=resolve_repaired_geometry(selected.plan,raw,'Sculpted chair local reference')
    checks=geometry_checks(build)
    if not checks['furniture_items_connected']:raise ValueError('Refined chair lost a required contact')
    selected.report.update(final_engineering_validation=checks,export_plan_sha256=canonical_hash(plan.model_dump(mode='json')))
    write('optimization_report.json',selected.report);write('selected_plan.json',plan.model_dump(mode='json'))
    (output/'chair_reference_view.png').write_bytes(render_build(build,size=(1100,1100),camera=fitted_preview))
    for name,az,el in [('chair_primary_view',-135,14),('front',-90,0),('rear',90,0),('side',0,0),('top',-90,89)]:
        (output/(name+'.png')).write_bytes(render_preview(build,az,el,size=(1300,1000)))
    publish(config,output,args.reference,plan,raw,selected.report,options,camera_report,write,stamp)


def publish(config,output,reference_path,plan,raw,report,options,camera_report,write,stamp):
    from sqlalchemy import select,func
    from app.database import Database,Project,AgentWorkflow,AIInvocation,audit
    from app.ai.images import store_image
    from app.engineering_schema import EngineeringDimensions,EngineeringAssembly,EngineeringReview
    from app.engineering_upstream import provenance as upstream_provenance
    from app.photo_matching import POLICY as MATCH_POLICY
    from app.storage_completion import POLICY as CONSTRUCTION_POLICY
    from app.agents import orchestrator
    from app.delivery import verify_delivery
    db=Database(config.database_url)
    try:
        analysis=local_analysis(plan)
        with db.sessions.begin() as session:
            owner_project=session.get(Project,'50167110-b19c-496c-865f-05b69ea4f756')
            if owner_project is None:raise ValueError('Existing local workspace owner unavailable')
            project=Project(name='Sculpted wooden chair - local reference',owner_id=owner_project.owner_id,reviewer_ids=owner_project.reviewer_ids)
            session.add(project);session.flush();pid,owner=project.id,project.owner_id
            image=store_image(session,config,pid,owner,reference_path.read_bytes(),provenance=dict(
                source='USER_PROVIDED_ALTERNATE_VIEW',primary_reference='Conversation inline black-seat chair',authoritative_geometry=False))
            image_id=image.id
            analysis_input=dict(project_id=pid,image_ids=[image_id],origin='LOCAL_VISUAL_INTERPRETATION_NO_API')
            source=AgentWorkflow(project_id=pid,created_by=owner,mode='PHOTO_ANALYZE',state='SUCCEEDED',progress=100,
                stage='local_visual_notes',input_data=analysis_input,input_hash=canonical_hash(analysis_input),
                model_config=dict(vision='LOCAL_INTERPRETATION_NO_API'),checkpoints={'photo_analysis':freeze(analysis.model_dump(mode='json'))},
                result={**analysis.model_dump(mode='json'), 'origin':'LOCAL_VISUAL_INTERPRETATION_NO_API','ai_requests':0})
            session.add(source);session.flush();source_id=source.id
            audit(session,owner,'local_reference_notes_saved',source_id,ai_requests=0)
        options=options.model_copy(update={'views':[view.model_copy(update={'image_id':UUID(image_id)}) for view in options.views]})
        raw.update(project_id=pid,image_id=image_id,image_ids=[image_id],reference_views=[dict(image_id=image_id,angle='perspective')],
            cad_source='ENGINEERING_CAD',output_scope='WHOLE_FURNITURE',photo_match_policy=MATCH_POLICY,
            engineering_integration=dict(version='CADQUERY_SOLIDS_V1',upstream_commit=upstream_provenance()['commit']),
            reconstruction_policy=CONSTRUCTION_POLICY,sweep_frame_policy='WORLD_UP_V2',
            source_photo_analysis=analysis.model_dump(mode='json'),dimension_fields=[v.model_dump(mode='json') for v in analysis.matching_inputs],
            optimization=options.model_dump(mode='json'),engineering_manual_reconstruction=dict(
                source_workflow_id=source_id,origin='LOCAL_REFERENCE_RECONSTRUCTION',ai_requests=0,
                knowledge_source='ASSISTANT_LOCAL_VISUAL_INTERPRETATION',primary_reference='Conversation inline black-seat image',
                stored_alternate_view_count=1,visual_review_status='HUMAN_REVIEW_REQUIRED'),
            notes='Locally interpreted chair from user images. The inline image defines the dark cushion; the supplied JPG is an alternate tan-seat view. All dimensions are estimates. No API calls.')
        dims=EngineeringDimensions(values=plan.values,assumptions=plan.assumptions,reference_view=dict(azimuth_deg=-120,elevation_deg=15),
            appearance=dict(summary='Sculpted wood chair with continuous rim, central splat, two large openings and dark cushion',
                critical_features=[dict(feature_id=fid,label=label,visible_shape=shape,relative_layout=layout) for fid,label,shape,layout in [
                    ('V001','Continuous sculpted crown','Broad curved back rim with flowing arm supports','Above the seat and around both sides'),
                    ('V002','Central flared back splat','Narrow curved waist widening into the wooden crown','At the rear of the seat'),
                    ('V003','Dark cushion','Low rounded rectangular upholstered seat','Within the wooden seat frame'),
                    ('V004','Four tapered legs','Rounded tapered members with wider upper shoulders','Below the four seat corners')]],
                continuity=['Native curved sweeps and smooth lofts; exact carved blends remain approximate'],
                symmetry='Bilateral layout is an interpreted assumption',avoid_substitutions=['Box-shaped backrest','Straight rods in place of curved arms']))
        assembly=EngineeringAssembly(furniture_type=plan.furniture_type,assumptions=plan.assumptions,
            parts=[dict(part_id=p.part_id,label=p.label,description='Locally interpreted native CAD solid',visible_evidence=p.evidence) for p in plan.parts])
        review=EngineeringReview(accepted=False,findings=['Local image interpretation; no independent AI visual approval or exact photographic reconstruction verification.'],
            limitations=plan.assumptions,feature_checks=[dict(feature_id=f.feature_id,status='UNVERIFIABLE',component_ids=[],
                evidence='Manual interpretation and local fitting do not certify photo identity',correction='Review the CAD against both supplied images') for f in dims.appearance.critical_features])
        checkpoints={key:freeze(value.model_dump(mode='json')) for key,value in dict(engineering_dimensions=dims,engineering_assembly=assembly,
            engineering_plan=plan,engineering_critic_0=review,engineering_visual_review_0=review).items()}
        checkpoints['engineering_optimization_0']=freeze(dict(plan=plan.model_dump(mode='json'),report=report))
        with db.sessions.begin() as session:
            row=AgentWorkflow(project_id=pid,created_by=owner,mode='IMAGE_TO_CAD',input_data=raw,input_hash=canonical_hash(raw),
                model_config=dict(vision='LOCAL_INTERPRETATION_NO_API',design='NATIVE_CAD_LOCAL',review='HUMAN_REVIEW_REQUIRED',fallback=''),
                idempotency_key='sculpted-chair-local-'+stamp,checkpoints=checkpoints)
            session.add(row);session.flush();wid=row.id
            audit(session,owner,'local_reference_reconstruction_queued',wid,source_workflow_id=source_id,ai_requests=0)
        write('receipt.json',dict(status='EXPORTING',project_id=pid,workflow_id=wid,ai_requests=0))
        print(json.dumps(dict(stage='exporting_chair_revision',workflow_id=wid,project_id=pid)),flush=True)
        gateway=orchestrator.ResponsesGateway
        def forbidden(*a,**k):raise AssertionError('Local chair generation must not initialize an AI client')
        orchestrator.ResponsesGateway=forbidden
        try:orchestrator.run_workflow(wid,db,config)
        finally:orchestrator.ResponsesGateway=gateway
        with db.sessions() as session:
            row=session.get(AgentWorkflow,wid)
            if row.state!='REVIEW_REQUIRED':
                write('workflow_failure.json',dict(status=row.state,error=row.error))
                raise RuntimeError('Chair export failed; inspect workflow_failure.json')
            delivery=verify_delivery(config,row.result['delivery']);cad=config.storage/row.job_id
            for name,target in [('cabinet.step','chair_REVIEW.step'),('cabinet.glb','chair_REVIEW.glb'),('cabinet.stl','chair_REVIEW.stl'),
                    ('all_dimensions_REVIEW.pdf','chair_dimensions_REVIEW.pdf'),('all_dimensions_REVIEW.dxf','chair_dimensions_REVIEW.dxf'),
                    ('all_dimensions_REVIEW.svg','chair_dimensions_REVIEW.svg')]:
                shutil.copyfile(cad/name,output/target)
            shutil.copyfile(delivery/'furniture_complete_package.zip',output/'chair_CAD_package.zip')
            usage=session.scalar(select(func.count()).select_from(AIInvocation).where(AIInvocation.workflow_id==wid))
            if usage:raise AssertionError('Local workflow unexpectedly recorded AI calls')
            receipt=dict(status=row.state,project_id=pid,workflow_id=wid,revision_id=row.revision_id,job_id=row.job_id,
                output_directory=str(output),ai_requests=usage,all_dimensions_estimated=True,
                dimensions_mm={key:row.result['specification'][key] for key in ('width_mm','depth_mm','height_mm')},
                geometry_source='NATIVE_CADQUERY_BREP',optimization_status=report['status'],
                initial_error=report['initial_error'],final_error=report['final_error'],exact_reconstruction_verified=False,
                source_reference_sha256=file_hash(reference_path),
                files={p.name:file_hash(p) for p in sorted(output.iterdir()) if p.is_file() and p.name!='receipt.json'})
        write('receipt.json',receipt)
        print(json.dumps(receipt),flush=True)
    finally:db.engine.dispose()


if __name__=='__main__':main()
