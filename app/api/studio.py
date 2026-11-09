"""Project-oriented API over existing private, revision-bound CAD and AI services."""
import json
import os
from pathlib import Path
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy import select
from app.auth import current_user, require_role
from app.database import Project, Revision, Job, AgentWorkflow, ImageAsset, AuditLog, audit, commit_response
from app.ai.images import image_json, store_image, ImageInputError
from app.pipeline import safe_directory, file_hash
from app.rendering.blender_service import blender_health
from app.rendering.config import FINISHES
from app.studio_schema import StudioRequest, ConceptRequest, BlenderRequest
from app.reference_fit_schema import ReferenceFitOptions,ReferenceMaskRequest
from app.model_scope import project_model_scope, scoped_workflows


class StudioBody(StudioRequest): project_id: UUID | None = None
class ConceptBody(ConceptRequest): project_id: UUID | None = None
class BlenderBody(BlenderRequest): project_id: UUID | None = None


def register_studio_routes(api,config,db_session,project_access,revision_access,checked_directory,start,workflow_access):
    router=APIRouter();db=api.state.db

    def latest(session,project_id,user):
        project_access(session,project_id,user)
        scope=project_model_scope(session,project_id)
        if scope:
            if not scope['revision_id']: raise HTTPException(409,'No CAD export is linked to the displayed 3D model yet')
            rev=session.get(Revision,scope['revision_id'])
        else:
            rev=session.scalar(select(Revision).where(Revision.project_id==str(project_id)).order_by(Revision.number.desc()).limit(1))
        if not rev: raise HTTPException(409,'Create CAD in this project first')
        job=session.get(Job,rev.active_job_id) if rev.active_job_id else None
        return rev,job

    def revision_data(rev):
        return dict(id=rev.id,number=rev.number,status=rev.state,specification=rev.specification,
            specification_hash=rev.spec_hash,value_provenance=rev.value_provenance,job_id=rev.active_job_id)

    def completed_render(session,project_id,revision_id=None,job_id=None):
        for workflow in session.scalars(select(AgentWorkflow).where(AgentWorkflow.project_id==str(project_id),
                AgentWorkflow.state.in_(('SUCCEEDED','REVIEW_REQUIRED'))).order_by(AgentWorkflow.created_at.desc()).limit(200)):
            render=(workflow.result or {}).get('blender')
            if render and (not revision_id or workflow.revision_id==revision_id) and (not job_id or workflow.job_id==job_id):
                yield workflow,render

    def render_directory(render):
        directory=safe_directory(config.storage,render['artifact_directory'])
        if file_hash(directory/'render_manifest.json')!=render['manifest_sha256']:
            raise HTTPException(409,'Render manifest integrity failed')
        return directory

    @router.get('/api/system/blender/status')
    def blender_status(user=Depends(current_user)):
        return dict(**blender_health(config.blender_executable),finishes=FINISHES,
            render_device=config.blender_device,timeout_seconds=config.blender_timeout)

    @router.get('/api/system/cad/status')
    def cad_status(user=Depends(current_user)):
        import cadquery
        from app.cad_operations import OPERATIONS
        return dict(available=True,engine='CadQuery/OpenCascade',version=cadquery.__version__,
            engineering_geometry='B-Rep; STEP millimetres',render_geometry='GLB metres; Y up',operations=OPERATIONS)

    @router.get('/api/system/openai/status')
    def openai_status(user=Depends(current_user)):
        from app.photo_matching import reconstruction_models
        return dict(enabled=config.ai_enabled,credentials_configured=bool(os.getenv('OPENAI_API_KEY')),
            models=config.ai_models,reasoning_effort=config.reasoning_effort,
            photo_reconstruction_models=reconstruction_models(config),
            background_mode=config.openai_background_mode,poll_interval_seconds=config.openai_poll_interval_seconds,
            account_access='Checked by the worker before each model is first used; no paid probe on this endpoint')

    @router.get('/api/projects/{project_id}')
    @router.get('/api/projects/{project_id}/status')
    def project_status(project_id:UUID,user=Depends(current_user),session=Depends(db_session)):
        project=project_access(session,project_id,user)
        revisions=list(session.scalars(select(Revision).where(Revision.project_id==project.id).order_by(Revision.number.desc())))
        workflows=list(session.scalars(select(AgentWorkflow).where(AgentWorkflow.project_id==project.id).order_by(AgentWorkflow.created_at.desc())))
        scope=project_model_scope(session,project.id,workflows)
        if scope:
            scope['hidden_revision_count']=sum(r.id!=scope['revision_id'] for r in revisions)
            revisions=[r for r in revisions if r.id==scope['revision_id']]
            workflows=scoped_workflows(workflows,scope)
        selection=next((record.resource_id for record in session.scalars(select(AuditLog).where(
            AuditLog.event=='concept_selected').order_by(AuditLog.created_at.desc()).limit(1000))
            if record.details.get('project_id')==project.id),None)
        visual=next((w for w in workflows if revisions and w.revision_id==revisions[0].id and
            (w.result or {}).get('visual_review')),None)
        fidelity=next((w for w in workflows if revisions and w.revision_id==revisions[0].id and
            (w.result or {}).get('reference_fidelity')),None)
        modeling=next((w for w in workflows if revisions and w.revision_id==revisions[0].id and
            (w.result or {}).get('blender_modeling')),None)
        delivery=next((w for w in workflows if (not scope or w.id==scope['cad_workflow_id']) and (w.result or {}).get('delivery')
            and (not scope or (w.result['delivery'].get('revision_id')==scope['revision_id']
                and w.result['delivery'].get('job_id')==scope['job_id']))),None)
        return dict(id=project.id,name=project.name,revisions=[revision_data(r) for r in revisions],
            model_scope=scope,
            engineering_cad=next(((w.result or {}).get('engineering_cad') for w in workflows if
                (not scope or w.id==scope['cad_workflow_id']) and (w.result or {}).get('engineering_cad')),None),
            selected_concept_id=selection,
            latest_visual_review=visual.result['visual_review'] if visual else None,
            reference_fidelity=fidelity.result['reference_fidelity'] if fidelity else None,
            fidelity_workflow_id=fidelity.id if fidelity else None,
            blender_modeling=modeling.result['blender_modeling'] if modeling else None,
            delivery=delivery.result['delivery'] if delivery else None,
            visual_review_workflow_id=visual.id if visual else None,
            workflows=[dict(id=w.id,mode=w.mode,status=w.state,stage=w.stage,progress=w.progress,error=w.error,
                cad_source=(w.input_data or {}).get('cad_source'),
                edit_source_workflow_id=((w.input_data or {}).get('engineering_edit') or {}).get('source_workflow_id'),
                revision_id=w.revision_id,job_id=w.job_id) for w in workflows],approved_for_manufacturing=False)

    @router.post('/api/projects/{project_id}/upload-image',status_code=201)
    async def upload(project_id:UUID,file:UploadFile=File(...),user=Depends(current_user)):
        require_role(user,'admin','designer','operator')
        try:
            data=await file.read(config.max_image_bytes+1)
            with db.sessions.begin() as session:
                project_access(session,project_id,user,lock=True)
                return image_json(store_image(session,config,str(project_id),user.id,data))
        except ImageInputError as exc: raise HTTPException(422,str(exc))
        finally: await file.close()

    @router.delete('/api/projects/{project_id}')
    @router.delete('/api/v1/projects/{project_id}')
    def remove_project(project_id:UUID,user=Depends(current_user)):
        require_role(user,'admin','designer','operator')
        from app.project_deletion import delete_project
        return delete_project(db,config,project_id,user,project_access)

    @router.get('/api/furniture-samples/{sample_id}/drawing')
    def sample_drawing(sample_id:str,user=Depends(current_user)):
        from app.sample_dimensions import ROOT,catalogue
        sample=next((sample for sample in catalogue()[0] if sample['id']==sample_id),None)
        if not sample:raise HTTPException(404,'Sample drawing not found')
        drawing=sample['drawing'];path=safe_directory(ROOT,drawing['file'])
        if file_hash(path)!=drawing['sha256']:raise HTTPException(409,'Sample drawing integrity failed')
        return FileResponse(path,media_type='application/pdf',filename=drawing['filename'],content_disposition_type='inline')

    @router.post('/api/projects/{project_id}/analyze',status_code=202)
    def analyze(project_id:UUID,data:StudioBody,user=Depends(current_user)):
        return start(data.model_copy(update={'project_id':project_id}),'PHOTO_ANALYZE',user)

    @router.post('/api/projects/{project_id}/generate-concept',status_code=202)
    @router.post('/api/projects/{project_id}/edit-concept',status_code=202)
    def concept(project_id:UUID,data:ConceptBody,user=Depends(current_user)):
        return start(data.model_copy(update={'project_id':project_id}),'CONCEPT_V2',user)

    @router.get('/api/projects/{project_id}/concepts')
    def concepts(project_id:UUID,user=Depends(current_user),session=Depends(db_session)):
        project_access(session,project_id,user)
        return [image_json(im) for im in session.scalars(select(ImageAsset).where(ImageAsset.project_id==str(project_id),
            ImageAsset.kind.in_(('AI_CONCEPT_IMAGE','AI_CONCEPT'))).order_by(ImageAsset.created_at.desc()).limit(200))]

    @router.post('/api/projects/{project_id}/concepts/{image_id}/select')
    def select_concept(project_id:UUID,image_id:UUID,user=Depends(current_user),session=Depends(db_session)):
        require_role(user,'admin','designer','operator');project_access(session,project_id,user)
        record=session.get(ImageAsset,str(image_id))
        if not record or record.project_id!=str(project_id) or record.kind not in ('AI_CONCEPT','AI_CONCEPT_IMAGE'):
            raise HTTPException(404,'Concept not found')
        audit(session,user.id,'concept_selected',record.id,project_id=str(project_id),not_measurement_evidence=True)
        return commit_response(session,dict(selected_concept_id=record.id,dimension_evidence=False))

    @router.post('/api/projects/{project_id}/generate-cad',status_code=202)
    def generate(project_id:UUID,data:StudioBody,user=Depends(current_user)):
        return start(data.model_copy(update={'project_id':project_id}),'PIPELINE',user)

    @router.post('/api/projects/{project_id}/reconstruct-with-blender',status_code=202)
    def reconstruct(project_id:UUID,data:StudioBody,user=Depends(current_user)):
        updates={'project_id':project_id,'modeling_engine':'blender','blender_local_only':False}
        if data.source_revision_id and not data.prompt.strip():
            updates['prompt']='Refine the visible geometry to match the uploaded furniture photographs. Preserve supplied dimensions and all unaffected features.'
        return start(data.model_copy(update=updates),'MODIFY' if data.source_revision_id else 'PIPELINE',user)

    @router.post('/api/projects/{project_id}/rebuild-blender',status_code=202)
    def rebuild_blender(project_id:UUID,data:StudioBody,user=Depends(current_user),session=Depends(db_session)):
        rev,_=latest(session,project_id,user);session.commit()
        return start(data.model_copy(update={'project_id':project_id,'modeling_engine':'blender','blender_local_only':True,
            'source_revision_id':data.source_revision_id or UUID(rev.id),'auto_render':False}),'MODIFY',user)

    @router.patch('/api/projects/{project_id}/parameters',status_code=202)
    @router.post('/api/projects/{project_id}/modify',status_code=202)
    @router.post('/api/projects/{project_id}/operations',status_code=202)
    def parameters(project_id:UUID,data:StudioBody,user=Depends(current_user),session=Depends(db_session)):
        rev,_=latest(session,project_id,user)
        session.commit()
        return start(data.model_copy(update={'project_id':project_id,'source_revision_id':data.source_revision_id or UUID(rev.id)}),'MODIFY',user)

    @router.post('/api/projects/{project_id}/render/preview',status_code=202)
    def render_preview(project_id:UUID,data:BlenderBody,user=Depends(current_user),session=Depends(db_session)):
        rev,_=latest(session,project_id,user);session.commit()
        return start(data.model_copy(update={'project_id':project_id,'revision_id':data.revision_id or UUID(rev.id)}),'BLENDER',user)

    @router.post('/api/projects/{project_id}/fit-reference',status_code=202)
    def fit_reference(project_id:UUID,data:StudioBody,user=Depends(current_user),session=Depends(db_session)):
        rev,_=latest(session,project_id,user);session.commit()
        return start(data.model_copy(update={'project_id':project_id,'source_revision_id':data.source_revision_id or UUID(rev.id),
            'reference_fit':data.reference_fit or ReferenceFitOptions(),'auto_render':False}),'MODIFY',user)

    @router.post('/api/projects/{project_id}/reference-mask')
    def reference_mask(project_id:UUID,data:ReferenceMaskRequest,user=Depends(current_user),session=Depends(db_session)):
        require_role(user,'admin','designer','operator');project_access(session,project_id,user)
        record=session.get(ImageAsset,str(data.image_id))
        if not record or record.project_id!=str(project_id) or record.kind not in ('REFERENCE','AI_CONCEPT_IMAGE','AI_CONCEPT'):
            raise HTTPException(404,'Design reference not found')
        from app.ai.images import read_image
        from app.ai.reference_mask import foreground,png_bytes
        import base64
        try:result=foreground(read_image(record,config),strokes=data.strokes)
        except (ValueError,ImageInputError) as exc:raise HTTPException(422,str(exc))
        encode=lambda pixels:'data:image/png;base64,'+base64.b64encode(png_bytes(pixels)).decode()
        return dict(mask_png=encode(result['mask'].astype('uint8')*255),overlay_png=encode(result['overlay']),
            method=result['method'],usable=result['usable'],warnings=result['warnings'])

    @router.post('/api/projects/{project_id}/render/final',status_code=202)
    def render_final(project_id:UUID,data:BlenderBody,user=Depends(current_user),session=Depends(db_session)):
        options=data.render_options
        if options.quality in ('draft','preview'): options=options.model_copy(update={'quality':'high'})
        return render_preview(project_id,data.model_copy(update={'render_options':options}),user,session)

    @router.get('/api/projects/{project_id}/renders')
    def renders(project_id:UUID,user=Depends(current_user),session=Depends(db_session)):
        project_access(session,project_id,user)
        scope=project_model_scope(session,project_id)
        if scope and not scope['revision_id']: return []
        return [dict(workflow_id=w.id,revision_id=w.revision_id,**render) for w,render in completed_render(session,project_id,
            scope['revision_id'] if scope else None,scope['job_id'] if scope else None)]

    @router.post('/api/projects/{project_id}/refine',status_code=202)
    def refine(project_id:UUID,data:BlenderBody,user=Depends(current_user),session=Depends(db_session)):
        rev,job=latest(session,project_id,user)
        selected=data.render_workflow_id
        if not selected:
            entry=next(completed_render(session,project_id,rev.id,job.id if job else None),None)
            if not entry: raise HTTPException(409,'Render this revision with Blender before visual review')
            selected=UUID(entry[0].id)
        session.commit()
        return start(data.model_copy(update={'project_id':project_id,'revision_id':data.revision_id or UUID(rev.id),
            'render_workflow_id':selected}),'VISUAL_REVIEW',user)

    @router.post('/api/projects/{project_id}/corrections/{review_id}/{correction_id}/accept',status_code=202)
    def accept(project_id:UUID,review_id:UUID,correction_id:str,data:StudioBody,user=Depends(current_user),session=Depends(db_session)):
        review=workflow_access(session,review_id,user)
        if review.project_id!=str(project_id) or not review.revision_id: raise HTTPException(404,'Review not found')
        session.commit()
        return start(data.model_copy(update={'project_id':project_id,'source_revision_id':UUID(review.revision_id),
            'accepted_correction_id':correction_id,'review_workflow_id':review_id}),'MODIFY',user)

    @router.post('/api/projects/{project_id}/corrections/{review_id}/{correction_id}/reject')
    def reject(project_id:UUID,review_id:UUID,correction_id:str,user=Depends(current_user),session=Depends(db_session)):
        require_role(user,'admin','designer','operator');review=workflow_access(session,review_id,user)
        if review.project_id!=str(project_id) or not any(c['correction_id']==correction_id for c in (review.result or {}).get('visual_review',{}).get('corrections',[])):
            raise HTTPException(404,'Correction not found')
        audit(session,user.id,'visual_correction_rejected',review.id,correction_id=correction_id)
        return commit_response(session,dict(status='REJECTED',correction_id=correction_id))

    @router.get('/api/projects/{project_id}/components')
    @router.get('/api/projects/{project_id}/cut-list')
    @router.get('/api/projects/{project_id}/bom')
    @router.get('/api/projects/{project_id}/drawings')
    def manufacturing(project_id:UUID,user=Depends(current_user),session=Depends(db_session)):
        rev,job=latest(session,project_id,user)
        if not job: raise HTTPException(409,'Generate CAD first')
        directory=checked_directory(job,rev)
        load=lambda name:json.loads((directory/name).read_text(encoding='utf-8'))
        return dict(revision_id=rev.id,job_id=job.id,parts=load('parts_registry.json'),schedules=load('schedules_REVIEW.json'),
            drawings=load('drawing_manifest.json'),validation=load('validation_report.json'),approved_for_manufacturing=False)

    @router.get('/api/studio/workflows/{workflow_id}/files/{filename}')
    def render_file(workflow_id:UUID,filename:str,user=Depends(current_user),session=Depends(db_session)):
        workflow=workflow_access(session,workflow_id,user)
        render=(workflow.result or {}).get('blender') or (workflow.checkpoints.get('blender_render') or {}).get('payload')
        if not render: raise HTTPException(409,'Render outputs are not available')
        directory=render_directory(render)
        allowed={**render['files'],'render_manifest.json':render['manifest_sha256'],'blender_render_package.zip':render['package_sha256']}
        if filename not in allowed: raise HTTPException(404,'Render file not found')
        path=safe_directory(directory,filename)
        if file_hash(path)!=allowed[filename]: raise HTTPException(409,'Render artifact integrity failed')
        return FileResponse(path,filename=filename,media_type='image/png' if filename.endswith('.png') else None)

    @router.get('/api/studio/workflows/{workflow_id}/diagnostics')
    def diagnostics(workflow_id:UUID,user=Depends(current_user),session=Depends(db_session)):
        workflow=workflow_access(session,workflow_id,user)
        paths=sorted([*config.storage.glob('blender_'+workflow.id+'_*'),*config.storage.glob('blender_model_'+workflow.id+'_*')])
        def tail(directory,names,limit):
            path=next((directory/name for name in names if (directory/name).is_file()),None)
            return path.read_text(encoding='utf-8',errors='replace')[-limit:] if path else ''
        return dict(error=workflow.error,logs=[dict(attempt=p.name,
            stdout=tail(p,('blender_stdout.log','stdout.log'),16000),
            stderr=tail(p,('blender_stderr.log','stderr.log'),8000)) for p in paths[-3:]])

    @router.get('/api/studio/workflows/{workflow_id}/concepts/{index}/original')
    def original_concept(workflow_id:UUID,index:int,user=Depends(current_user),session=Depends(db_session)):
        workflow=workflow_access(session,workflow_id,user)
        checkpoint=workflow.checkpoints.get(f'concept_{index}')
        if not checkpoint or index not in range(3): raise HTTPException(404,'Concept not found')
        from app.pipeline import canonical_hash
        if canonical_hash(checkpoint['payload'])!=checkpoint['sha256']: raise HTTPException(409,'Concept checkpoint integrity failed')
        original=checkpoint['payload'].get('provenance',{}).get('original')
        if not original: raise HTTPException(404,'Original-format download is unavailable for this older concept')
        path=safe_directory(safe_directory(config.storage,original['directory']),original['filename'])
        if file_hash(path)!=original['sha256']: raise HTTPException(409,'Original concept integrity failed')
        return FileResponse(path,filename=original['filename'])

    @router.get('/api/projects/{project_id}/download/{kind}')
    def download(project_id:UUID,kind:str,user=Depends(current_user),session=Depends(db_session)):
        rev,job=latest(session,project_id,user)
        if not job: raise HTTPException(409,'Generate CAD first')
        directory=checked_directory(job,rev)
        if kind in ('step','glb','stl','package'):
            name={'step':'cabinet.step','glb':'cabinet.glb','stl':'cabinet.stl','package':'design_review_package.zip'}[kind]
            manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
            name=manifest.get('cad_files',{}).get(kind,name)
            return FileResponse(directory/name,filename=name)
        if kind in ('blend','renders'):
            item=next(completed_render(session,project_id,rev.id,job.id),None)
            if not item: raise HTTPException(409,'Render this CAD revision with Blender first')
            return render_file(UUID(item[0].id),'furniture_scene.blend' if kind=='blend' else 'blender_render_package.zip',user,session)
        raise HTTPException(404,'Download format not found')

    api.include_router(router)
    from .image_to_cad import register_image_to_cad_routes
    register_image_to_cad_routes(api,config,db_session,project_access,workflow_access)
    # Preserve the existing versioned API and provide the requested project API spelling.
    for route in list(api.routes):
        if getattr(route,'path','') in ('/api/v1/projects','/api/v1/projects/{project_id}/revisions'):
            api.add_api_route(route.path.replace('/api/v1/','/api/'),route.endpoint,methods=list(route.methods),
                status_code=route.status_code,response_model=route.response_model)
