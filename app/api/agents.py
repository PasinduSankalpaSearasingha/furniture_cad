"""Authenticated AI endpoints reuse the existing project, revision and release policies."""
import json
import math
import os
from uuid import UUID
from fastapi import Depends, HTTPException, UploadFile, File, APIRouter
from fastapi.responses import FileResponse, Response
from sqlalchemy import select, func
from app.auth import current_user, require_role
from app.database import AgentWorkflow, AgentEvent, AIInvocation, ImageAsset, CatalogEntry, Revision, Job, Project, audit, utcnow, uid, commit_response
from app.ai.schemas import (WorkflowRequest, PresentationRequest, VerifiedFurnitureSpecification,
    DIMENSION_FIELDS, DOOR_DIMENSION_FIELDS, AIPriceProfile, CADGenerationRequest)
from app.ai.openai_client import AIServiceError
from app.ai.response_store import RESPONSE_PREFIX
from app.ai.images import store_image, image_json, image_path, read_image, ImageInputError
from app.agents.orchestrator import event, dispatch_workflow, workflow_supported
from app.agents.cad_agent import validate_design_specification, tool_definitions
from app.pipeline import canonical_hash
from app.workflow import transition
from app.agents.furniture_agent import EstimateRequest
from app.agents.photo_agent import PhotoRequest


def workflow_stage_label(stage):
    if stage.startswith('engineering_'):
        for prefix,label in (('engineering_candidate_','Designing and validating solid CAD parts'),
            ('engineering_repair_','Correcting the saved furniture design'),
            ('engineering_accepted_','Solid CAD candidate validated'),
            ('engineering_critic_','Checking the component plan'),('engineering_preview_','Rendering actual CAD geometry'),
            ('engineering_visual_review_','Comparing CAD views with the reference'),
            ('engineering_rejected_','Correcting CAD geometry from validation feedback')):
            if stage.startswith(prefix):return label
        return {'engineering_dimensions':'Matching drawing dimensions and estimating missing sizes',
            'engineering_final_tile_input':'Preparing the final photo and CAD comparison',
            'engineering_final_request_reserved':'Preparing the final AI check',
            'engineering_final_ai_response':'Checking the model and proposing final corrections',
            'engineering_final_tile_selected':'Rendering the corrected model for review',
            'engineering_final_correction':'Applying and validating final corrections',
            'engineering_assembly':'Planning separate furniture components',
            'engineering_exporting_parts':'Exporting closed solid CAD parts',
            'engineering_verifying_exports':'Reopening STEP solids and generating matching previews',
            'engineering_files':'Preparing solid CAD files'}.get(stage,'Engineering CAD: '+stage.removeprefix('engineering_').replace('_',' '))
    for prefix,label in (('photo_fidelity_preview','Preparing comparison views'),
        ('blender_validating_cad','Validating the proposed CAD geometry'),('blender_fitting_camera','Aligning the model and reference camera'),
        ('blender_building_parts','Building furniture parts in background Blender'),('blender_comparing_geometry','Comparing Blender, photographs and CAD'),
        ('blender_model_review','Sol is reviewing the actual Blender model'),('blender_model_correction','Sol is refining the Blender geometry'),
        ('blender_model_build','Creating native Blender geometry'),('blender_model_plan','Sol is planning and building the model'),
        ('blender_model_rejected','Sol is revising the CAD geometry from validation feedback'),
        ('reference_fit_camera','Aligning the reference camera'),('reference_fit_geometry','Fitting curves and positions'),
        ('reference_fit_cad_validation','Validating the fitted CAD'),('reference_fit_','Measuring the reference match'),
        ('photo_fidelity_review','Comparing the model with your photo'),
        ('photo_fidelity_edit','Refining the visible details'),('photo_fidelity_patch','Refining the visible details')):
        if stage.startswith(prefix): return label
    return stage.replace('_',' ').capitalize()


def register_routes(api,config,db_session,project_access,revision_access,make_revision,enqueue,submit,
                    checked_directory,approve,release,review_schema):
    router = APIRouter()
    db = api.state.db

    def workflow_access(session,workflow_id,user,lock=False):
        workflow = session.get(AgentWorkflow,str(workflow_id))
        if not workflow: raise HTTPException(404,'Workflow not found')
        project_access(session,workflow.project_id,user,lock=lock)
        if lock:
            workflow=session.get(AgentWorkflow,str(workflow_id),with_for_update=True,populate_existing=True)
            if not workflow:raise HTTPException(404,'Workflow not found')
        return workflow

    def start_workflow(data,mode,user):
        require_role(user,'admin','designer','operator')
        raw = data.model_dump(mode='json')
        if mode=='ESTIMATE':
            from app.furniture_catalog import template
            if raw.get('template_id') and not template(raw['template_id']):
                raise HTTPException(422,'Unknown furniture template')
            if not (raw.get('image_ids') or raw.get('source_workflow_id') or raw.get('template_id') or raw.get('notes','').strip()):
                raise HTTPException(422,'Choose a template, add design notes or provide a reference image')
        key = raw.pop('idempotency_key',None)
        client_request_hash = canonical_hash(raw)
        with db.sessions.begin() as session:
            p = project_access(session,data.project_id,user,lock=True)
            if key:
                existing = session.scalar(select(AgentWorkflow).where(AgentWorkflow.project_id==p.id,AgentWorkflow.idempotency_key==key))
                if existing:
                    if existing.input_data.get('client_request_hash') != client_request_hash or existing.mode!=mode:
                        raise HTTPException(409,'Idempotency key was already used for different workflow inputs')
                    return dict(workflow_id=existing.id,status=existing.state,status_url=f'/api/v1/agents/workflows/{existing.id}')
            raw['client_request_hash'] = client_request_hash
            images = list(raw.get('image_ids',[]))
            native_blender=raw.get('modeling_engine')=='blender'
            if raw.get('blender_local_only') and (mode!='MODIFY' or not raw.get('source_revision_id') or not native_blender):
                raise HTTPException(422,'Local Blender rebuilding requires an existing source plan')
            if native_blender and mode not in ('PIPELINE','MODIFY'):
                raise HTTPException(422,'Native Blender modeling is supported for design and modification workflows')
            for view in raw.get('modeling_views',[]):
                asset=session.get(ImageAsset,view['image_id'])
                if not asset or asset.project_id!=p.id or asset.kind not in ('REFERENCE','AI_CONCEPT_IMAGE','AI_CONCEPT'):
                    raise HTTPException(404,'Modeling reference not found')
                if view.get('mask_png'):
                    from app.ai.reference_mask import decode_mask
                    try:decode_mask(view['mask_png'],asset.width/asset.height,(96,96))
                    except ValueError as exc:raise HTTPException(422,str(exc))
            local_fit=raw.get('reference_fit') is not None
            if native_blender and local_fit:raise HTTPException(422,'Run local reference fitting and Blender modeling as separate operations')
            if raw.get('blender_local_only') and raw.get('prompt','').strip():raise HTTPException(422,'A local rebuild cannot include an AI design instruction')
            if local_fit:
                if mode!='MODIFY' or not raw.get('source_revision_id'):
                    raise HTTPException(422,'Local fitting requires a saved source design')
                if any(raw.get(k) for k in ('known_values','parameter_changes','features','feature_component_id',
                    'accepted_correction_id','concept_id','source_workflow_id')) or raw.get('prompt','').strip():
                    raise HTTPException(422,'Fit a saved design on its own; submit measurement or feature changes separately')
                raw['auto_render']=False
                for view in raw['reference_fit'].get('views',[]):
                    asset=session.get(ImageAsset,view['image_id'])
                    if not asset or asset.project_id!=p.id or asset.kind not in ('REFERENCE','AI_CONCEPT_IMAGE','AI_CONCEPT'):
                        raise HTTPException(404,'Fitting reference not found')
                    if view.get('mask_png'):
                        from app.ai.reference_mask import decode_mask
                        try:decode_mask(view['mask_png'],asset.width/asset.height,(96,96))
                        except ValueError as exc:raise HTTPException(422,str(exc))
            studio_mode=mode in ('CONCEPT_V2','PIPELINE','MODIFY','BLENDER','VISUAL_REVIEW')
            concept_id=raw.get('concept_id')
            if concept_id:
                concept=session.get(ImageAsset,concept_id)
                if not concept or concept.project_id!=p.id or concept.kind not in ('AI_CONCEPT_IMAGE','AI_CONCEPT'):
                    raise HTTPException(404,'Concept not found')
                images=[concept.id]
                raw['design_basis']='SELECTED_AI_CONCEPT_NOT_MEASUREMENT_EVIDENCE'
            source_revision_id=raw.get('source_revision_id')
            # Reusing a checked design makes dimension-only submissions stable.
            # Older approximate plans are refined against the reference before reuse.
            if not native_blender and mode in ('PHOTO','PIPELINE') and not source_revision_id and not raw.get('prompt','').strip() and not raw.get('notes','').strip() and not raw.get('material_hint'):
                lookup_images=images
                if not lookup_images and raw.get('source_workflow_id'):
                    source=workflow_access(session,raw['source_workflow_id'],user)
                    if source.project_id!=p.id: raise HTTPException(404,'Source workflow not found')
                    lookup_images=source.input_data.get('image_ids',[])
                def fingerprints(ids):
                    records=[session.get(ImageAsset,i) for i in ids]
                    return {im.sha256 for im in records} if all(im and im.project_id==p.id for im in records) else set()
                current=fingerprints(lookup_images)
                if current:
                    for candidate in session.scalars(select(Revision).where(Revision.project_id==p.id).order_by(Revision.number.desc()).limit(100)):
                        prior=session.get(AgentWorkflow,candidate.source_workflow_id) if candidate.source_workflow_id else None
                        fidelity=(prior.result or {}).get('reference_fidelity',{}) if prior else {}
                        if (candidate.specification.get('photo_design') and prior and
                            current==fingerprints(prior.input_data.get('image_ids',[]))):
                            source_revision_id=candidate.id
                            raw['source_revision_id']=candidate.id
                            raw['parameter_edit_only']=True
                            raw['refine_source_geometry']=not raw.get('known_values') and (
                                fidelity.get('policy_version')!=1 or fidelity.get('status')=='NEEDS_VISUAL_REVIEW')
                            break
            if source_revision_id:
                if mode not in ('PHOTO','PIPELINE','MODIFY'): raise HTTPException(422,'This workflow does not accept a source component plan')
                source_revision=revision_access(session,source_revision_id,user)
                if source_revision.project_id!=p.id or not source_revision.specification.get('photo_design'):
                    raise HTTPException(409,'Editing requires a photo-derived component plan in this project')
                if canonical_hash(source_revision.specification)!=source_revision.spec_hash:
                    raise HTTPException(409,'Source specification integrity failed')
                raw['source_plan']=source_revision.specification['photo_design']
                raw['source_specification']=source_revision.specification
                raw['source_specification_hash']=source_revision.spec_hash
                raw['source_value_provenance']=source_revision.value_provenance or {}
                prior=session.get(AgentWorkflow,source_revision.source_workflow_id) if source_revision.source_workflow_id else None
                raw['source_reference_fidelity']=(prior.result or {}).get('reference_fidelity',{}) if prior else {}
                raw['source_photo_observations']=((prior.checkpoints.get('photo_analysis') or {}).get('payload') or
                    prior.input_data.get('source_photo_analysis') or prior.input_data.get('source_photo_observations') or
                    dict(vision=(prior.result or {}).get('vision'))) if prior else {}
                if not images and prior:
                    images=(prior.result or {}).get('reference_image_ids') or prior.input_data.get('image_ids',[])
                inherited={k:v['value'] for k,v in (source_revision.value_provenance or {}).items()
                    if v.get('source') in ('USER_PROVIDED','PHYSICALLY_MEASURED','DESIGNER_VERIFIED','FACTORY_APPROVED') or v.get('verified')}
                if prior:
                    raw['refinement_iteration']=prior.input_data.get('refinement_iteration',0)
                changes=raw.get('parameter_changes',[])
                if len({v['key'] for v in changes})!=len(changes): raise HTTPException(422,'Duplicate parameter change')
                if raw.get('accepted_correction_id'):
                    review=workflow_access(session,raw.get('review_workflow_id'),user)
                    if review.project_id!=p.id or review.revision_id!=source_revision.id:
                        raise HTTPException(409,'Correction review does not match this revision')
                    proposal=next((c for c in (review.result or {}).get('visual_review',{}).get('corrections',[])
                        if c['correction_id']==raw['accepted_correction_id']),None)
                    if not proposal: raise HTTPException(404,'Correction not found')
                    iteration=max(raw.get('refinement_iteration',0),review.input_data.get('refinement_iteration',0))+1
                    if iteration>config.max_refinements: raise HTTPException(409,'Visual refinement iteration limit reached')
                    raw['refinement_iteration']=iteration
                    if raw.get('known_values') or raw.get('parameter_changes'):
                        raise HTTPException(422,'Accept a correction on its own; submit deliberate measurement edits separately')
                    if proposal['proposed_operation']=='material_only':
                        raise HTTPException(409,'Use the rendering finish controls for appearance-only corrections')
                    if proposal['proposed_operation']=='update_parameter':
                        key=proposal['parameter_key']
                        if key in inherited: raise HTTPException(409,'Visual corrections cannot change a fixed user or verified dimension')
                        if proposal['new_value'] is None or not key: raise HTTPException(409,'Correction has no applicable parameter value')
                        changes=[dict(key=key,value=proposal['new_value'])];raw['parameter_changes']=changes;raw['prompt']=''
                    else: raw['prompt']=proposal['instructions']
                    audit(session,user.id,'visual_correction_accepted',review.id,correction_id=proposal['correction_id'])
                inherited.update({v['key']:v['value'] for v in raw.get('known_values',[])})
                inherited.update({v['key']:v['value'] for v in changes})
                raw['known_values']=[dict(key=k,value=v) for k,v in inherited.items()]
            source_id = raw.get('source_workflow_id')
            if source_id:
                source = workflow_access(session,source_id,user)
                if source.project_id!=p.id or not source.result or 'vision' not in source.result:
                    raise HTTPException(409,'Source workflow must contain completed observations in the same project')
                if not images: images = source.input_data.get('image_ids',[])
                if set(images)!=set(source.input_data.get('image_ids',[])):
                    raise HTTPException(409,'Source observations refer to different images')
                cached = source.checkpoints.get('vision')
                if not cached or canonical_hash(cached['payload'])!=cached['sha256']:
                    raise HTTPException(409,'Source vision checkpoint integrity failed')
                raw['source_vision'] = cached['payload']
                analysis=source.checkpoints.get('photo_analysis')
                if analysis:
                    if canonical_hash(analysis['payload'])!=analysis['sha256']: raise HTTPException(409,'Source photo analysis integrity failed')
                    raw['source_photo_analysis']=analysis['payload']
            if native_blender:images=list(dict.fromkeys([*images,*[v['image_id'] for v in raw.get('modeling_views',[])]]))
            for image_id in images:
                record = session.get(ImageAsset,image_id)
                if not record or record.project_id!=p.id: raise HTTPException(404,'Image not found')
                if native_blender and record.kind not in ('REFERENCE','AI_CONCEPT_IMAGE','AI_CONCEPT'):
                    raise HTTPException(422,'Native reconstruction requires a photograph or selected design concept')
                if record.kind not in ('REFERENCE','CAD_PREVIEW') and not (studio_mode and record.kind in ('AI_CONCEPT_IMAGE','AI_CONCEPT','BLENDER_RENDER')):
                    raise HTTPException(422,'AI concept/presentation images cannot serve as design evidence')
            raw['image_ids'] = images
            if native_blender and not (images or raw.get('modeling_views')):
                raise HTTPException(422,'Choose a furniture reference photograph before Blender modeling')
            if raw.get('modeling_views') and not native_blender:raise HTTPException(422,'Modeling views require the Blender modeling workflow')
            if local_fit and not (images or raw['reference_fit'].get('views')):
                raise HTTPException(422,'Choose a reference photograph before fitting')
            if mode=='PIPELINE' and not (images or raw.get('prompt','').strip()):
                raise HTTPException(422,'Upload a furniture photo, select a concept or enter a design prompt')
            if mode=='MODIFY' and not source_revision_id:
                raise HTTPException(422,'An existing source revision is required')
            if mode in ('ANALYZE','COLLABORATIVE','PHOTO_ANALYZE','PHOTO') and not images:
                raise HTTPException(422,'Upload at least one reference image first')
            revision_id = raw.get('revision_id')
            if mode in ('REVIEW','RENDER','BLENDER','VISUAL_REVIEW'):
                if not revision_id: raise HTTPException(422,'A design revision is required')
                revision = revision_access(session,revision_id,user)
                if revision.project_id!=p.id: raise HTTPException(404,'Revision not found')
                job = session.get(Job,revision.active_job_id) if revision.active_job_id else None
                if not job: raise HTTPException(409,'Generate CAD before reviewing or rendering it')
                checked_directory(job,revision)
                # Bind presentation/review to the exact current CAD package.
                raw['source_job_id'],raw['source_spec_hash'] = job.id,revision.spec_hash
                raw['source_specification']=revision.specification
                raw['source_value_provenance']=revision.value_provenance or {}
                if mode=='VISUAL_REVIEW':
                    source_render=workflow_access(session,raw.get('render_workflow_id'),user)
                    render=(source_render.result or {}).get('blender')
                    if not render or source_render.revision_id!=revision.id or source_render.job_id!=job.id:
                        raise HTTPException(409,'A completed Blender render of this exact revision and CAD job is required')
                    raw['source_render']=render
                    prior=session.get(AgentWorkflow,revision.source_workflow_id) if revision.source_workflow_id else None
                    raw['refinement_iteration']=prior.input_data.get('refinement_iteration',0) if prior else 0
                    if not images and prior:
                        raw['image_ids']=(prior.result or {}).get('reference_image_ids') or prior.input_data.get('image_ids',[])
            elif revision_id:
                raise HTTPException(422,'This workflow mode does not accept a source revision')
            if mode=='RENDER' and images:
                raise HTTPException(422,'CAD presentation renders use a server-derived preview, not client reference images')
            raw['joinery_catalog'] = [e.payload for e in session.scalars(select(CatalogEntry).where(
                CatalogEntry.kind=='joinery').order_by(CatalogEntry.reference).limit(200))] if mode=='COLLABORATIVE' else []
            models=dict(config.ai_models)
            if mode=='PHOTO_ANALYZE':
                from app.ai.furniture_budget import policy
                raw['ai_budget']=policy(config)
            if native_blender:
                models.update({role:'gpt-6.1-sol' for role in ('vision','design','review','render')});models['fallback']=''
            workflow = AgentWorkflow(project_id=p.id,created_by=user.id,mode=mode,input_data=raw,
                input_hash=canonical_hash(raw),model_config=models,idempotency_key=key,
                revision_id=revision_id if mode in ('REVIEW','RENDER','BLENDER','VISUAL_REVIEW') else None,
                job_id=raw.get('source_job_id') if mode in ('REVIEW','RENDER','BLENDER','VISUAL_REVIEW') else None)
            session.add(workflow)
            session.flush()
            workflow_id = workflow.id
            event(session,workflow_id,'workflow_queued','queued',mode=mode,input_sha256=workflow.input_hash)
            audit(session,user.id,'agent_workflow_queued',workflow_id,mode=mode,project_id=p.id)
        dispatch(workflow_id)
        return dict(workflow_id=workflow_id,status='QUEUED',status_url=f'/api/v1/agents/workflows/{workflow_id}')

    def dispatch(workflow_id):
        try: dispatch_workflow(workflow_id,db,config)
        except Exception:
            with db.sessions.begin() as session:
                workflow = session.get(AgentWorkflow,workflow_id)
                workflow.state = 'FAILED'
                workflow.error = dict(code='AGENT_QUEUE_UNAVAILABLE',message='Worker queue unavailable; retry when available',retryable=True)
                event(session,workflow_id,'dispatch_failed','queued')
            raise HTTPException(503,{'message':'Worker queue unavailable','workflow_id':workflow_id})

    @router.post('/api/v1/projects/{project_id}/images',status_code=201)
    async def upload_image(project_id: UUID,file: UploadFile=File(...),user=Depends(current_user)):
        require_role(user,'admin','designer','operator')
        try:
            data = await file.read(config.max_image_bytes+1)
            with db.sessions.begin() as session:
                p = project_access(session,project_id,user,lock=True)
                record = store_image(session,config,p.id,user.id,data)
                return image_json(record)
        except ImageInputError as exc: raise HTTPException(422,str(exc))
        finally: await file.close()

    @router.get('/api/v1/images/{image_id}')
    def download_image(image_id: UUID,user=Depends(current_user),session=Depends(db_session)):
        record = session.get(ImageAsset,str(image_id))
        if not record: raise HTTPException(404,'Image not found')
        project_access(session,record.project_id,user)
        try: read_image(record,config)
        except (ImageInputError,OSError): raise HTTPException(409,'Image integrity verification failed')
        return FileResponse(image_path(record,config),media_type='image/png',filename=f'{record.kind}_{record.id}.png')

    @router.get('/api/v1/projects/{project_id}/images')
    def list_images(project_id: UUID,user=Depends(current_user),session=Depends(db_session)):
        project_access(session,project_id,user)
        return [image_json(r) for r in session.scalars(select(ImageAsset).where(ImageAsset.project_id==str(project_id)).limit(500))]

    @router.get('/api/v1/ai/configuration')
    def configuration(user=Depends(current_user)):
        return dict(enabled=config.ai_enabled,credentials_configured=bool(os.getenv('OPENAI_API_KEY')),
                    executor=config.executor,
                    background_mode=config.openai_background_mode,poll_interval_seconds=config.openai_poll_interval_seconds,
                    models=config.ai_models,account_availability='CHECKED_BY_WORKER_BEFORE_USE',
                    orchestrator='DETERMINISTIC_PYTHON',cad_tools=tool_definitions())

    @router.post('/api/v1/ai/analyze-image',status_code=202)
    def analyze_image(data: WorkflowRequest,user=Depends(current_user)): return start_workflow(data,'ANALYZE',user)

    @router.post('/api/v1/ai/generate-design',status_code=202)
    @router.post('/api/v1/agents/run-workflow',status_code=202)
    def generate_design(data: WorkflowRequest,user=Depends(current_user)): return start_workflow(data,'COLLABORATIVE',user)

    @router.get('/api/v1/furniture/templates')
    def furniture_templates(user=Depends(current_user)):
        from app.furniture_catalog import catalog
        return dict(templates=catalog(),dimension_basis='COMMON_DEFAULT_ESTIMATE',approved_for_manufacturing=False)

    @router.post('/api/v1/ai/estimate-furniture',status_code=202)
    def estimate_furniture(data: EstimateRequest,user=Depends(current_user)):
        return start_workflow(data,'ESTIMATE',user)

    @router.post('/api/v1/ai/analyze-furniture-photo',status_code=202)
    def analyze_furniture_photo(data: PhotoRequest,user=Depends(current_user)):
        return start_workflow(data,'PHOTO_ANALYZE',user)

    @router.post('/api/v1/ai/reconstruct-furniture',status_code=202)
    def reconstruct_furniture(data: PhotoRequest,user=Depends(current_user)):
        return start_workflow(data,'PHOTO',user)

    @router.post('/api/v1/ai/review-design',status_code=202)
    def review_design(data: WorkflowRequest,user=Depends(current_user)): return start_workflow(data,'REVIEW',user)

    @router.post('/api/v1/ai/generate-concepts',status_code=202)
    def generate_concepts(data: PresentationRequest,user=Depends(current_user)): return start_workflow(data,'CONCEPT',user)

    @router.post('/api/v1/ai/generate-render',status_code=202)
    def generate_render(data: PresentationRequest,user=Depends(current_user)): return start_workflow(data,'RENDER',user)

    @router.get('/api/v1/agents/workflows/{workflow_id}')
    def workflow_status(workflow_id: UUID,user=Depends(current_user),session=Depends(db_session)):
        workflow = workflow_access(session,workflow_id,user)
        design = session.get(Revision,workflow.revision_id) if workflow.revision_id else None
        invocations = session.scalars(select(AIInvocation).where(AIInvocation.workflow_id==workflow.id)).all()
        usage = dict(input_tokens=sum(i.usage.get('input_tokens',0) for i in invocations),
                     output_tokens=sum(i.usage.get('output_tokens',0) for i in invocations),
                     generated_images=sum(i.usage.get('generated_images',0) for i in invocations))
        known = [i.cost for i in invocations if i.status=='COMPLETED']
        from decimal import Decimal
        currencies = {c.get('currency') for c in known}
        cost = dict(status='PRICE_REQUIRED',total=None,currency=None)
        usage_complete = all(i.status=='COMPLETED' for i in invocations)
        if not usage_complete: cost['status']='USAGE_INCOMPLETE'
        if known and usage_complete and all(c.get('total') is not None for c in known) and len(currencies)==1:
            cost = dict(status='ESTIMATE_FROM_CONFIGURED_RATES',currency=known[0]['currency'],
                        total=str(sum(Decimal(c['total']) for c in known)))
        modeling_previews=[]
        for key,checkpoint in workflow.checkpoints.items():
            if not key.startswith('blender_model_build_'):continue
            if canonical_hash(checkpoint['payload'])!=checkpoint['sha256']:raise HTTPException(409,'Model checkpoint integrity failed')
            modeling_previews.append(checkpoint['payload']['artifact'])
        from app.sample_dimensions import workflow_dimensions
        from app.ai.furniture_budget import summary as budget_summary
        checkpoint=workflow.checkpoints.get('engineering_dimensions')
        if checkpoint and canonical_hash(checkpoint['payload'])!=checkpoint['sha256']:
            raise HTTPException(409,'Dimension checkpoint integrity failed')
        return dict(id=workflow.id,project_id=workflow.project_id,mode=workflow.mode,status=workflow.state,
                    cad_source=(workflow.input_data or {}).get('cad_source'),
                    edit_source_workflow_id=((workflow.input_data or {}).get('engineering_edit') or {}).get('source_workflow_id'),
                    reference_image_ids=(workflow.input_data or {}).get('image_ids',[]),
                    reference_views=(workflow.input_data or {}).get('reference_views',[]),
                    stage=workflow.stage,stage_label=workflow_stage_label(workflow.stage),progress=workflow.progress,attempts=workflow.attempts,
                    revision_id=workflow.revision_id,job_id=workflow.job_id,design_state=design.state if design else None,
                    result=workflow.result,error=workflow.error,
                    modeling_previews=modeling_previews,
                    dimensions=workflow_dimensions(workflow) if (workflow.input_data or {}).get('cad_source')=='ENGINEERING_CAD' else None,
                    completed_stages=[key for key in workflow.checkpoints if not key.startswith(RESPONSE_PREFIX)],
                    usage=usage,cost=cost,usage_complete=usage_complete,ai_budget=budget_summary(session,workflow),
                    approved_for_manufacturing=False)

    @router.get('/api/v1/projects/{project_id}/workflows')
    def list_workflows(project_id: UUID,user=Depends(current_user),session=Depends(db_session)):
        project_access(session,project_id,user)
        return [dict(id=w.id,project_id=w.project_id,mode=w.mode,status=w.state,
                     reference_image_ids=(w.input_data or {}).get('image_ids',[]),
                     stage=w.stage,stage_label=workflow_stage_label(w.stage),progress=w.progress,revision_id=w.revision_id,
                     job_id=w.job_id,created_at=w.created_at,result_available=w.result is not None)
                for w in session.scalars(select(AgentWorkflow).where(
                    AgentWorkflow.project_id==str(project_id)).order_by(AgentWorkflow.created_at.desc()).limit(100))]

    @router.get('/api/v1/agents/workflows/{workflow_id}/result')
    def workflow_result(workflow_id: UUID,user=Depends(current_user),session=Depends(db_session)):
        workflow = workflow_access(session,workflow_id,user)
        if workflow.result is None: raise HTTPException(409,'Workflow has not produced a result yet')
        return Response(json.dumps(workflow.result,indent=2,ensure_ascii=False),media_type='application/json',
            headers={'Content-Disposition':f'attachment; filename="workflow_{workflow.id}.json"'})

    @router.get('/api/v1/agents/workflows/{workflow_id}/events')
    def events(workflow_id: UUID,after_id: int=0,user=Depends(current_user),session=Depends(db_session)):
        workflow_access(session,workflow_id,user)
        return [dict(id=e.id,event=e.event,stage=e.stage,details=e.details,created_at=e.created_at)
                for e in session.scalars(select(AgentEvent).where(AgentEvent.workflow_id==str(workflow_id),
                    AgentEvent.id>after_id).order_by(AgentEvent.id).limit(500))]

    @router.post('/api/v1/agents/workflows/{workflow_id}/retry',status_code=202)
    def retry_workflow(workflow_id: UUID,user=Depends(current_user)):
        require_role(user,'admin','designer','operator')
        with db.sessions.begin() as session:
            workflow = workflow_access(session,workflow_id,user,True)
            if not workflow_supported(workflow):
                raise HTTPException(409,'This workflow is no longer supported. Upload the photograph to create Engineering CAD.')
            attempt_limit=10 if workflow.mode=='IMAGE_TO_CAD' else config.ai_max_attempts
            stop=workflow.error or ((workflow.result or {}).get('available_output') or {}).get('stop_reason') or {}
            retryable_spend_check=workflow.state=='BUDGET_REACHED' and stop.get('code') in ('AI_BUDGET_CHECK_UNAVAILABLE','AI_CREDIT_LIMIT')
            retryable_saved_cad=workflow.state=='REVIEW_REQUIRED' and (workflow.result or {}).get('available_output') and stop.get('code') in ('CAD_PREVIEW_UNAVAILABLE','AGENT_WORKFLOW_FAILED')
            if (workflow.state!='FAILED' and not retryable_spend_check and not retryable_saved_cad) or workflow.attempts>=attempt_limit:
                raise HTTPException(409,'Only failed workflows within the attempt budget can be retried')
            workflow.state,workflow.error,workflow.updated_at = 'QUEUED',None,utcnow()
            event(session,workflow.id,'retry_queued','queued',
                completed_stages=[key for key in workflow.checkpoints if not key.startswith(RESPONSE_PREFIX)])
        dispatch(str(workflow_id))
        return dict(workflow_id=str(workflow_id),status='QUEUED')

    @router.get('/api/v1/agents/workflows/{workflow_id}/fidelity/{filename}')
    def fidelity_preview(workflow_id: UUID,filename:str,user=Depends(current_user),session=Depends(db_session)):
        workflow=workflow_access(session,workflow_id,user)
        for key,checkpoint in workflow.checkpoints.items():
            if not key.startswith('photo_fidelity_preview_'): continue
            if canonical_hash(checkpoint['payload'])!=checkpoint['sha256']: raise HTTPException(409,'Preview checkpoint changed')
            preview=checkpoint['payload']
            if filename not in preview['files']: continue
            from app.pipeline import safe_directory,file_hash
            path=safe_directory(safe_directory(config.storage,preview['directory']),filename)
            if file_hash(path)!=preview['files'][filename]: raise HTTPException(409,'Comparison preview changed')
            return FileResponse(path,media_type='image/png',filename=filename)
        raise HTTPException(404,'Comparison preview not found')

    @router.get('/api/v1/agents/workflows/{workflow_id}/fit/{filename}')
    def fit_artifact(workflow_id:UUID,filename:str,user=Depends(current_user),session=Depends(db_session)):
        workflow=workflow_access(session,workflow_id,user)
        from app.pipeline import safe_directory,file_hash
        for key,checkpoint in workflow.checkpoints.items():
            if not key.startswith('reference_fit_'):continue
            if canonical_hash(checkpoint['payload'])!=checkpoint['sha256']:raise HTTPException(409,'Fitting checkpoint changed')
            payload=checkpoint['payload'];report=payload.get('report',payload)
            if filename not in report.get('files',{}):continue
            path=safe_directory(safe_directory(config.storage,report['directory']),filename)
            if not path.is_file() or file_hash(path)!=report['files'][filename]:raise HTTPException(409,'Fitting artifact changed')
            return FileResponse(path,media_type='image/png',filename=filename)
        raise HTTPException(404,'Fitting artifact not found')

    @router.get('/api/v1/agents/workflows/{workflow_id}/model/{artifact_id}/{filename}')
    def native_model_file(workflow_id:UUID,artifact_id:str,filename:str,user=Depends(current_user),session=Depends(db_session)):
        workflow=workflow_access(session,workflow_id,user)
        from app.pipeline import safe_directory,file_hash
        for key,checkpoint in workflow.checkpoints.items():
            if not key.startswith('blender_model_build_'):continue
            if canonical_hash(checkpoint['payload'])!=checkpoint['sha256']:raise HTTPException(409,'Model checkpoint changed')
            artifact=checkpoint['payload']['artifact']
            if artifact['directory']!=artifact_id:continue
            if filename not in artifact['files']:continue
            path=safe_directory(safe_directory(config.storage,artifact['directory']),filename)
            if not path.is_file() or file_hash(path)!=artifact['files'][filename]:raise HTTPException(409,'Native model artifact changed')
            return FileResponse(path,filename=filename,media_type='image/png' if filename.endswith('.png') else 'model/gltf-binary' if filename.endswith('.glb') else None)
        raise HTTPException(404,'Native model file not found')

    @router.post('/api/v1/designs/{design_id}/verify',status_code=201)
    def verify_design(design_id: UUID,data: VerifiedFurnitureSpecification,user=Depends(current_user),session=Depends(db_session)):
        require_role(user,'designer')
        workflow = workflow_access(session,design_id,user,True)
        if workflow.mode!='COLLABORATIVE' or workflow.state!='WAITING_FOR_DESIGNER' or workflow.revision_id:
            raise HTTPException(409,'A supported completed proposal awaiting designer verification is required')
        proposal = (workflow.result or {}).get('design') or {}
        if proposal.get('status')!='SUPPORTED_TEMPLATE' or proposal.get('template_id')!='cabinet.v1':
            raise HTTPException(409,'CUSTOM_DESIGN_REVIEW_REQUIRED')
        spec = data.specification
        if spec.display_layout is not None:
            raise HTTPException(409,'The estimated image-study template is separate from verified cabinet.v1 proposals')
        fields = list(DIMENSION_FIELDS)+(list(DOOR_DIMENSION_FIELDS) if spec.include_glazed_door else [])
        if set(fields)-spec.model_fields_set or any(getattr(spec,f) is None for f in fields):
            raise HTTPException(422,'Every critical dimension must be supplied explicitly; legacy/default values cannot be verified implicitly')
        measurements = {m.field:m for m in data.measurements}
        if len(measurements)!=len(data.measurements) or set(measurements)!=set(fields):
            raise HTTPException(422,'Supply exactly one evidence record for each required cabinet dimension')
        for field in fields:
            record = measurements[field]
            if not math.isclose(record.value_mm,getattr(spec,field),rel_tol=0,abs_tol=1e-9):
                raise HTTPException(422,f'Measurement differs from specification: {field}')
            if field in ('width_mm','depth_mm','height_mm') and record.provenance!='PHYSICALLY_MEASURED':
                raise HTTPException(422,'External dimensions require physical measurement evidence')
        spec = spec.model_copy(update=dict(measured_dimensions_confirmed=True,dimension_provenance='physically_verified',
            measurement_reference=measurements['width_mm'].evidence_reference))
        project = project_access(session,workflow.project_id,user)
        revision = make_revision(session,project,spec,user)
        revision.source_workflow_id = workflow.id
        revision.value_provenance = dict(workflow_id=workflow.id,proposal_is_advisory=True,
            values={f:dict(**measurements[f].model_dump(),verified_by=user.id,verification='DESIGNER_VERIFIED') for f in fields},
            designer_comment=data.designer_comment)
        transition(revision,'SPECIFICATION_VERIFIED',session,user.id)
        revision.verified_by,revision.verified_spec_hash = user.id,revision.spec_hash
        workflow.revision_id,workflow.state,workflow.updated_at = revision.id,'SPECIFICATION_VERIFIED',utcnow()
        event(session,workflow.id,'designer_verified','verification',revision_id=revision.id,
              specification_hash=revision.spec_hash,designer_id=user.id)
        audit(session,user.id,'ai_design_specification_verified',revision.id,workflow_id=workflow.id,spec_hash=revision.spec_hash)
        return commit_response(session,dict(design_id=workflow.id,revision_id=revision.id,revision=revision.number,
                    specification_hash=revision.spec_hash,status=workflow.state,approved_for_manufacturing=False))

    @router.post('/api/v1/designs/{design_id}/generate-cad',status_code=202)
    def generate_cad(design_id: UUID,user=Depends(current_user)):
        require_role(user,'designer','operator','admin')
        with db.sessions.begin() as session:
            workflow = workflow_access(session,design_id,user,True)
            if not workflow.revision_id or workflow.state not in ('SPECIFICATION_VERIFIED','CAD_FAILED'):
                raise HTTPException(409,'A designer-verified specification is required before CAD generation')
            revision = revision_access(session,workflow.revision_id,user,True)
            try: validate_design_specification(CADGenerationRequest(revision_id=revision.id,
                specification_hash=revision.spec_hash,template_id='cabinet.v1'),session)
            except AIServiceError as exc: raise HTTPException(409,dict(code=exc.code,message=exc.message))
            job = enqueue(session,revision,user)
            job_id = job.id
            workflow.job_id,workflow.state,workflow.progress,workflow.updated_at = job.id,'CAD_QUEUED',70,utcnow()
        submit(job_id)
        return dict(design_id=str(design_id),job_id=job_id,status_url=f'/api/v1/jobs/{job_id}')

    def design_revision(session,design_id,user):
        workflow = workflow_access(session,design_id,user,True)
        if not workflow.revision_id: raise HTTPException(409,'Verify a design specification first')
        return workflow,revision_access(session,workflow.revision_id,user,True)

    @router.post('/api/v1/designs/{design_id}/validate')
    def validate_design(design_id: UUID,user=Depends(current_user),session=Depends(db_session)):
        workflow,revision = design_revision(session,design_id,user)
        job = session.get(Job,workflow.job_id) if workflow.job_id else None
        if not job: raise HTTPException(409,'Generate CAD first')
        directory = checked_directory(job,revision)
        return json.loads((directory/'validation_report.json').read_text(encoding='utf-8'))

    @router.post('/api/v1/designs/{design_id}/approve',status_code=201)
    def approve_design(design_id: UUID,data: review_schema,user=Depends(current_user),session=Depends(db_session)):
        workflow,revision = design_revision(session,design_id,user)
        result = approve(UUID(revision.id),data,user,session)
        workflow.state,workflow.updated_at = data.decision,utcnow()
        event(session,workflow.id,'designer_reviewed','approval',decision=data.decision,revision_id=revision.id)
        return commit_response(session,result)

    @router.post('/api/v1/designs/{design_id}/release',status_code=201)
    def release_design(design_id: UUID,user=Depends(current_user),session=Depends(db_session)):
        workflow,revision = design_revision(session,design_id,user)
        return release(UUID(revision.id),user,session)

    @router.post('/api/v1/ai/pricing',status_code=201)
    def add_price(data: AIPriceProfile,user=Depends(current_user),session=Depends(db_session)):
        require_role(user,'admin')
        record = CatalogEntry(kind='ai_prices',reference=uid(),payload=data.model_dump(mode='json'),created_by=user.id)
        session.add(record)
        session.flush()
        audit(session,user.id,'ai_price_created',record.id,model_id=data.model_id)
        return commit_response(session,dict(id=record.id,model_id=data.model_id))

    @router.get('/api/v1/ai/pricing')
    def prices(user=Depends(current_user),session=Depends(db_session)):
        return [dict(id=r.id,created_at=r.created_at,profile=r.payload) for r in session.scalars(select(CatalogEntry).where(
            CatalogEntry.kind=='ai_prices').order_by(CatalogEntry.created_at.desc()).limit(500))]

    api.include_router(router)
    from .studio import register_studio_routes
    register_studio_routes(api,config,db_session,project_access,revision_access,checked_directory,start_workflow,workflow_access)
