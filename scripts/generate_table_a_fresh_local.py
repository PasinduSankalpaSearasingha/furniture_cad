"""Fresh Table A CAD from an explicitly local visual interpretation, with no API.

Uses saved design targets, manually annotated image evidence, local native CAD
optimization, and the normal immutable engineering export workflow. It does not
change the configured AI provider or claim an AI/engineering approval.
"""
import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2
import numpy as np
from scipy.optimize import minimize_scalar
from app.cad_optimizer_schema import CADCamera, CADOptimizationOptions, CADViewEvidence
from app.cad_optimizer import optimize_cad_isolated, NativeCADBuilder
from app.cad_projection import project_points
from app.photo_schema import PhotoDesign
from app.pipeline import canonical_hash, file_hash

SOURCE = 'd1e5808b-a276-4dc7-8553-16bec4993a5b'
REFERENCE = Path(r'C:\Users\HP\Desktop\Profile\NEW\Fiske\Table\Table A\Referance Image - 4.JPG')


def interpret(source):
    """Author native shapes from this reference; retain prior overall targets."""
    original = PhotoDesign.model_validate(source.checkpoints['engineering_plan']['payload'])
    raw = original.model_dump(mode='json')
    values = {v['key']: v for v in raw['values']}
    values['foot_section_corner_radius_mm']['value'] = 20.
    for part in raw['parts']:
        part['color_rgb'] = [.66, .63, .45] if part['part_id']=='F001' else [.18, .17, .10]
    raw['summary'] = ('Fresh local interpretation of the single supplied Table A photo: rounded thin light-wood '
        'top, continuous dark concave web and four broad, splayed tapered feet. No OpenAI request or visual approval.')
    raw['assumptions'] = [
        '2800 x 1100 x 750 mm are retained design targets from the existing Table A project, not measurements established by this photograph.',
        'The assistant visually interpreted the supplied reference locally because the user reported the backend API key unavailable.',
        'Three foot ends are annotated; the partly hidden fourth foot is completed by an explicitly assumed bilateral layout.',
        'Camera pose and focal length are estimated from manually annotated tabletop corners and the retained dimensions; no lens calibration exists.',
        'Base cross-sections, curvature, edge radii, material and hidden joinery remain editable design estimates.',
    ]
    raw['unrepresented_features'] = ['Wood grain and subtle surface relief', 'Measured joints, concealed fixings and structural qualification']
    return PhotoDesign.model_validate(raw)


def estimate_camera(plan, size):
    values = {v.key:v.value for v in plan.values}
    w,d,h = (values[key] for key in ('width_mm','depth_mm','height_mm'))
    # Intersections of straight tabletop edges, rather than pretending rounded
    # corners expose a physical point. Annotation uncertainty is explicit.
    pixels = np.array([[687.,469.],[170.,315.],[1320.,122.],[1781.,174.]])
    world = np.array([[-w/2,-d/2,h],[-w/2,d/2,h],[w/2,d/2,h],[w/2,-d/2,h]])
    def solve(focal):
        intrinsics = np.array([[focal,0,size[0]/2],[0,focal,size[1]/2],[0,0,1.]])
        ok, rotation, translation = cv2.solvePnP(world,pixels,intrinsics,None,flags=cv2.SOLVEPNP_ITERATIVE)
        if not ok:raise ValueError('The planar tabletop pose could not be estimated')
        projected,_ = cv2.projectPoints(world,rotation,translation,intrinsics,None)
        error=float(np.mean(np.sum((projected.reshape(-1,2)-pixels)**2,axis=1)))
        return error,intrinsics,rotation,translation
    fit = minimize_scalar(lambda focal:solve(focal)[0],bounds=(1000.,12000.),method='bounded',options={'maxiter':60})
    error,intrinsics,rotation,translation = solve(float(fit.x))
    if np.sqrt(error)>10:
        raise ValueError('The estimated camera does not fit the annotated tabletop; do not generate or publish this pose')
    camera = CADCamera(image_size=size,intrinsic_matrix=intrinsics.tolist(),
        rotation_vector=tuple(rotation.ravel()),translation_mm=tuple(translation.ravel()),reliable=False,
        uncertainty='Estimated from four manually annotated, rounded tabletop edge intersections and saved target sizes; not a calibrated camera')
    return camera,dict(method='LOCAL_PLANAR_PNP_WITH_FOCAL_SEARCH',focal_pixels=float(fit.x),
        tabletop_corner_rms_px=float(np.sqrt(error)),image_points=pixels.tolist(),world_points_mm=world.tolist(),
        reliability='APPROXIMATE',camera=camera.model_dump(mode='json'))


def preview_camera(camera):
    """Represent the same pinhole camera for the existing CAD raster preview."""
    rotation,_ = cv2.Rodrigues(np.asarray(camera.rotation_vector))
    toward = -rotation[2]
    eye = -rotation.T@np.asarray(camera.translation_mm)
    distance=float(np.linalg.norm(eye-np.array([0.,0.,375.])))
    radius=1600.
    right=np.cross([0.,0.,1.],toward);right/=np.linalg.norm(right)
    down=-np.cross(toward,right)
    roll=float(np.arctan2(-rotation[0]@down,rotation[0]@right))
    return dict(azimuth_deg=float(np.rad2deg(np.arctan2(toward[1],toward[0]))),
        elevation_deg=float(np.rad2deg(np.arcsin(toward[2]))),roll_deg=float(np.rad2deg(roll)),
        center=(eye-toward*distance).tolist(),radius=radius,inverse_distance=radius/distance,
        log_scale=float(np.log(camera.intrinsic_matrix[0][0]*radius/distance)),
        pan_x=camera.intrinsic_matrix[0][2]/camera.image_size[0],
        pan_y=camera.intrinsic_matrix[1][2]/camera.image_size[1],viewport=list(camera.image_size))


def freeze(value):
    return dict(payload=value,sha256=canonical_hash(value))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference',type=Path,default=REFERENCE)
    parser.add_argument('--source-workflow',default=SOURCE)
    parser.add_argument('--max-seconds',type=float,default=180.)
    args=parser.parse_args()
    from PIL import Image
    from sqlalchemy import func,select
    from app.config import Settings
    from app.database import Database,AgentWorkflow,Project,AIInvocation,audit
    from app.ai.images import store_image
    from app.engineering_schema import EngineeringDimensions,EngineeringAssembly,EngineeringReview
    from app.geometry_repairs import resolve_repaired_geometry
    from app.engineering_cad import geometry_checks,check_supplied_dimensions,render_preview
    from app.ai.geometry_preview import render_build
    from app.agents import orchestrator
    from app.delivery import verify_delivery

    config=Settings(ai_enabled=False)
    db=Database(config.database_url)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')+'-'+uuid4().hex[:6]
    output=config.storage/('table-a-fresh-local-'+stamp)
    output.mkdir(parents=True,exist_ok=False)
    receipt=dict(output_directory=str(output),ai_requests=0,status='STARTED')
    def write(name,value):
        (output/name).write_text(json.dumps(value,indent=2,allow_nan=False),encoding='utf-8')
    try:
        with db.sessions.begin() as session:
            source=session.get(AgentWorkflow,args.source_workflow)
            if source is None or source.input_hash!=canonical_hash(source.input_data):
                raise ValueError('A valid saved Table A source workflow is required')
            project=session.get(Project,source.project_id)
            if project is None:raise ValueError('The source project is unavailable')
            for checkpoint in source.checkpoints.values():
                if checkpoint['sha256']!=canonical_hash(checkpoint['payload']):raise ValueError('Source checkpoint integrity failed')
            plan=interpret(source)
            raw=copy.deepcopy(source.input_data)
            reference=store_image(session,config,project.id,project.owner_id,args.reference.read_bytes(),
                provenance=dict(source='USER_PROVIDED',purpose='Fresh local reconstruction from the current reference',authoritative_geometry=False))
            reference_id,project_id,owner=reference.id,project.id,project.owner_id
            invocation_count=session.scalar(select(func.count()).select_from(AIInvocation))
        shutil.copyfile(args.reference,output/'reference_original.jpg')
        for key in ('engineering_reexport_of','engineering_edit','engineering_local_completion','analysis_workflow_id',
                    'analysis_sha256','dimension_fields','client_request_hash','source_photo_analysis','optimization'):
            raw.pop(key,None)
        raw.update(image_id=reference_id,image_ids=[reference_id],reference_views=[dict(image_id=reference_id,angle='perspective')],
            output_scope='WHOLE_FURNITURE',engineering_manual_reconstruction=dict(
                source_workflow_id=args.source_workflow,origin='LOCAL_REFERENCE_RECONSTRUCTION',
                knowledge_source='ASSISTANT_VISUAL_INTERPRETATION_OF_USER_REFERENCE',stored_original_image_count=1,
                ai_requests=0,visual_review_status='HUMAN_REVIEW_REQUIRED'),
            notes='Fresh local native CAD output for the current Table A reference while the API key is unavailable. No paid AI calls.')
        with Image.open(args.reference) as image:size=image.size
        camera,camera_report=estimate_camera(plan,size)
        projection_camera=preview_camera(camera)
        write('camera_estimate.json',camera_report)
        print(json.dumps(dict(stage='camera_estimated',corner_rms_px=camera_report['tabletop_corner_rms_px'],
            azimuth_deg=projection_camera['azimuth_deg'],elevation_deg=projection_camera['elevation_deg'])),flush=True)
        # Foot centers are interpreted floor-contact landmarks, not claimed
        # factory measurements. Occluded rear-right geometry is not penalized.
        view=CADViewEvidence(image_id=reference_id,camera=camera,landmarks=[
            dict(part_id='F003',anchor='section_center',point_index=0,image_point=[828,917],confidence=.8,evidence_id='V003'),
            dict(part_id='F004',anchor='section_center',point_index=0,image_point=[1552,561],confidence=.7,evidence_id='V003'),
            dict(part_id='F005',anchor='section_center',point_index=0,image_point=[503,768],confidence=.7,evidence_id='V003')])
        options=CADOptimizationOptions(views=[view],mode='balanced',max_evaluations=40,max_seconds=args.max_seconds,
            component_ids=['F003','F004','F005','F006'],parameter_ids=['foot_center_x_mm','foot_center_y_mm'],
            feature_components={'V003':['F003','F004','F005','F006']},mismatch_feature_ids=['V003'],
            allow_uncertain_camera=True,max_displacement_mm=100,max_relative_change=.25,max_structural_llm_calls=0)
        write('initial_plan.json',plan.model_dump(mode='json'))
        initial_build,_=NativeCADBuilder(raw,'Table A fresh local')(plan)
        preview_size=(1280,720)
        (output/'initial_camera_view.png').write_bytes(render_build(initial_build,size=preview_size,camera=projection_camera))
        print(json.dumps(dict(stage='local_native_optimization',max_evaluations=40,max_seconds=args.max_seconds,ai_requests=0)),flush=True)
        selected=optimize_cad_isolated(plan,options,inputs=raw,name='Table A fresh local reference reconstruction')
        resolved,spec,provenance,build,adjustments,repairs=resolve_repaired_geometry(selected.plan,raw,'Table A fresh local')
        checks=geometry_checks(build);checks['supplied_dimensions']=check_supplied_dimensions(build,raw['known_values'])
        if not checks['furniture_items_connected']:raise ValueError('The selected model must be connected')
        selected.report.update(final_engineering_validation=checks,export_plan_sha256=canonical_hash(resolved.model_dump(mode='json')),
            fixed_dimensions_preserved=True,geometry_valid=True)
        write('optimization_report.json',selected.report)
        write('selected_plan.json',resolved.model_dump(mode='json'))
        write('geometry_checks.json',checks)
        (output/'photo_aligned_cad.png').write_bytes(render_build(build,size=preview_size,camera=projection_camera))
        for name,az,el in [('front',-90,0),('side',0,0),('top',-90,89),('isometric',-60,20)]:
            (output/(name+'.png')).write_bytes(render_preview(build,az,el,size=(1300,900)))
        dims=EngineeringDimensions(values=resolved.values,assumptions=resolved.assumptions,
            reference_view=dict(azimuth_deg=projection_camera['azimuth_deg'],elevation_deg=projection_camera['elevation_deg']),
            appearance=dict(summary='Single-photo local interpretation of the supplied Table A',
                critical_features=[dict(feature_id='V001',label='Thin rounded tabletop',visible_shape='Pale wooden rounded rectangular slab',relative_layout='Overhangs the dark base'),
                    dict(feature_id='V002',label='Sculpted concave web',visible_shape='Broad dark web with concave upper and lower contours',relative_layout='Between elevated shoulders'),
                    dict(feature_id='V003',label='Splayed tapered feet',visible_shape='Broad blades flare to three visible floor contacts; a fourth is partly occluded',relative_layout='Front and rear feet beneath the web')],
                continuity=['Native curved web and lofted feet; exact blending remains unverified'],
                symmetry='Bilateral completion is assumed, not measured',avoid_substitutions=['Thin stock rods instead of broad sculpted supports']))
        assembly=EngineeringAssembly(furniture_type=resolved.furniture_type,assumptions=resolved.assumptions,
            parts=[dict(part_id=p.part_id,label=p.label,description='Locally interpreted native CAD solid',visible_evidence=p.evidence) for p in resolved.parts])
        review=EngineeringReview(accepted=False,findings=['Fresh local reference interpretation; no AI visual review or exact photo identity verification was performed.'],
            limitations=resolved.assumptions,feature_checks=[dict(feature_id=f.feature_id,status='UNVERIFIABLE',
                component_ids=['F001'] if f.feature_id=='V001' else ['F002'] if f.feature_id=='V002' else ['F003','F004','F005','F006'],
                evidence='Local image interpretation and numerical fitting are not an independent visual approval.',
                correction='Compare the actual CAD views with the supplied photograph.') for f in dims.appearance.critical_features])
        raw.update(optimization=options.model_dump(mode='json'),source_photo_analysis=dict(reference_view=dims.reference_view.model_dump(mode='json')))
        checkpoints={key:freeze(value.model_dump(mode='json')) for key,value in dict(engineering_dimensions=dims,
            engineering_assembly=assembly,engineering_plan=resolved,engineering_critic_0=review,engineering_visual_review_0=review).items()}
        checkpoints['engineering_optimization_0']=freeze(dict(plan=resolved.model_dump(mode='json'),report=selected.report))
        checkpoints['engineering_initial_local_plan']=freeze(plan.model_dump(mode='json'))
        with db.sessions.begin() as session:
            row=AgentWorkflow(project_id=project_id,created_by=owner,mode='IMAGE_TO_CAD',input_data=raw,
                input_hash=canonical_hash(raw),model_config=dict(vision='LOCAL_INTERPRETATION_NO_API',design='NATIVE_CAD_LOCAL',review='HUMAN_REVIEW_REQUIRED',fallback=''),
                idempotency_key='table-a-fresh-local-'+stamp,checkpoints=checkpoints)
            session.add(row);session.flush();wid=row.id
            audit(session,owner,'local_reference_reconstruction_queued',wid,source_workflow_id=args.source_workflow,ai_requests=0)
        receipt.update(workflow_id=wid,project_id=project_id,status='EXPORTING')
        write('receipt.json',receipt)
        print(json.dumps(dict(stage='exporting_fresh_revision',workflow_id=wid,optimization_status=selected.report['status'],
            initial_error=selected.report['initial_error'],final_error=selected.report['final_error'])),flush=True)
        gateway=orchestrator.ResponsesGateway
        def forbidden(*a,**k):raise AssertionError('This fresh local reconstruction must not initialize an AI client')
        orchestrator.ResponsesGateway=forbidden
        try:orchestrator.run_workflow(wid,db,config)
        finally:orchestrator.ResponsesGateway=gateway
        with db.sessions() as session:
            row=session.get(AgentWorkflow,wid)
            if row.state!='REVIEW_REQUIRED':
                write('workflow_failure.json',dict(status=row.state,error=row.error))
                raise RuntimeError('The local CAD export failed; inspect workflow_failure.json')
            delivery=verify_delivery(config,row.result['delivery'])
            cad=config.storage/row.job_id
            for source_name,target_name in [('cabinet.step','table_a_fresh_REVIEW.step'),('cabinet.glb','table_a_fresh_REVIEW.glb'),
                    ('cabinet.stl','table_a_fresh_REVIEW.stl'),('all_dimensions_REVIEW.pdf','table_a_dimensions_REVIEW.pdf'),
                    ('all_dimensions_REVIEW.dxf','table_a_dimensions_REVIEW.dxf'),('all_dimensions_REVIEW.svg','table_a_dimensions_REVIEW.svg')]:
                shutil.copyfile(cad/source_name,output/target_name)
            shutil.copyfile(delivery/'furniture_complete_package.zip',output/'table_a_fresh_CAD_package.zip')
            actual_invocations=session.scalar(select(func.count()).select_from(AIInvocation).where(AIInvocation.workflow_id==wid))
            if actual_invocations:raise AssertionError('The offline workflow unexpectedly recorded AI usage')
            receipt.update(status=row.state,job_id=row.job_id,revision_id=row.revision_id,ai_requests=actual_invocations,
                optimization_status=selected.report['status'],initial_error=selected.report['initial_error'],final_error=selected.report['final_error'],
                dimensions_mm={key:spec.model_dump()[key] for key in ('width_mm','depth_mm','height_mm')},
                source_reference_sha256=file_hash(args.reference),geometry_source='NATIVE_CADQUERY_BREP',
                physical_dimensions_verified=False,exact_reconstruction_verified=False,
                files={p.name:file_hash(p) for p in sorted(output.iterdir()) if p.is_file() and p.name!='receipt.json'})
        write('receipt.json',receipt)
        (output/'README.txt').write_text('Fresh Table A native CAD output\n\n'
            'Created locally from the supplied image without OpenAI API calls.\n'
            'Targets: 2800 x 1100 x 750 mm, retained from the existing project.\n'
            'Camera, base details and hidden construction are estimates.\n'
            'Numerical fitting concerns annotated visible foot positions only.\n'
            'Inspect STEP and drawings before use; exact photo matching and manufacturing suitability are unverified.\n',encoding='utf-8')
        print(json.dumps(receipt),flush=True)
    finally:
        db.engine.dispose()


if __name__=='__main__':main()
