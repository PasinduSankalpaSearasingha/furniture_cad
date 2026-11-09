"""Project-owned image-to-solid-CAD generation and private delivery downloads."""
import copy
import os
from typing import Literal
from uuid import UUID
from fastapi import APIRouter,Depends,HTTPException
from fastapi.responses import FileResponse
from pydantic import Field,model_validator
from sqlalchemy import select
from app.models import StrictModel
from app.photo_schema import InputValue
from app.auth import current_user,require_role
from app.database import AgentWorkflow,ImageAsset,Revision,Job,audit
from app.pipeline import canonical_hash,safe_directory,verify_package
from app.ai.openai_client import AIServiceError
from app.agents import orchestrator
from app.studio_schema import StudioRequest
from app.rendering.config import RenderOptions
from app.photo_matching import reconstruction_models,POLICY as PHOTO_MATCH_POLICY
from app.furniture_references import FurnitureReferenceView,validate_references
from app.cad_optimizer_schema import CADOptimizationOptions


class ImageToCADRequest(StrictModel):
    cad_source: Literal['ENGINEERING_CAD'] = 'ENGINEERING_CAD'
    output_scope: Literal['WHOLE_FURNITURE','PARTS'] = 'WHOLE_FURNITURE'
    engineering_reference_workflow_id: UUID | None = None
    analysis_workflow_id: UUID | None = None
    image_id: UUID
    image_ids: list[UUID] = Field(default_factory=list,max_length=6)
    reference_views: list[FurnitureReferenceView] = Field(default_factory=list,max_length=6)
    known_values: list[InputValue] = Field(default_factory=list,max_length=40)
    notes: str = Field(default='',max_length=6000)
    render_options: RenderOptions = Field(default_factory=RenderOptions)
    idempotency_key: str | None = Field(default=None,max_length=80)
    optimization: CADOptimizationOptions | None = None
    final_ai_correction: bool = False

    @model_validator(mode='after')
    def dimensions(self):
        ids=self.image_ids or [self.image_id]
        validate_references(ids,self.reference_views)
        if self.optimization and not {v.image_id for v in self.optimization.views} <= set(ids):
            raise ValueError('Optimization evidence must reference selected project photos')
        if self.engineering_reference_workflow_id and self.optimization is not None:
            raise ValueError('Local re-export retains the saved geometry; use a new generation to optimize')
        if ids[0]!=self.image_id:raise ValueError('The primary image must be the first selected furniture photo')
        if len({v.key for v in self.known_values})!=len(self.known_values):
            raise ValueError('A known input was supplied twice')
        if self.analysis_workflow_id and self.engineering_reference_workflow_id:
            raise ValueError('Choose image analysis for a new design or a saved plan for local re-export')
        for value in self.known_values:
            if value.key in ('width_mm','depth_mm','height_mm'):
                valid=10<=value.value<=10000
            elif value.key.endswith('_mm'):valid=.2<=value.value<=10000
            elif value.key.endswith('_deg'):valid=-180<=value.value<=180
            elif value.key.endswith('_count'):valid=value.value.is_integer() and 0<=value.value<=60
            else:raise ValueError('Use millimetre dimensions, degree angles or component counts')
            if not valid:raise ValueError(f'{value.key} is outside its supported dimension range')
        return self


class Complete360Request(StrictModel):
    source_workflow_id: UUID


class EditCADRequest(StrictModel):
    source_workflow_id: UUID
    part_ids: list[str] = Field(min_length=1,max_length=60)
    style: Literal['round','tapered','curved','custom'] = 'custom'
    instructions: str = Field(default='',max_length=3000)
    idempotency_key: str = Field(min_length=1,max_length=80)
    final_ai_correction: bool = False

    @model_validator(mode='after')
    def edit(self):
        if len(self.part_ids)!=len(set(self.part_ids)):raise ValueError('Select each component once')
        if self.style=='custom' and not self.instructions.strip():raise ValueError('Describe the requested component change')
        return self


def register_image_to_cad_routes(api,config,db_session,project_access,workflow_access):
    router=APIRouter();db=api.state.db

    @router.post('/api/projects/{project_id}/edit-cad',status_code=202)
    def edit_cad(project_id:UUID,data:EditCADRequest,user=Depends(current_user)):
        require_role(user,'admin','designer','operator')
        request=data.model_dump(mode='json');key=request.pop('idempotency_key')
        if not data.final_ai_correction:request.pop('final_ai_correction',None)
        digest=canonical_hash(request)
        with db.sessions.begin() as session:
            project=project_access(session,project_id,user,lock=True)
            existing=session.scalar(select(AgentWorkflow).where(AgentWorkflow.project_id==project.id,AgentWorkflow.idempotency_key==key))
            if existing:
                if existing.input_data.get('client_request_hash')!=digest or not existing.input_data.get('engineering_edit'):
                    raise HTTPException(409,'Idempotency key belongs to different inputs')
                return dict(workflow_id=existing.id,status=existing.state,status_url=f'/api/v1/agents/workflows/{existing.id}')
            source=workflow_access(session,data.source_workflow_id,user)
            if source.project_id!=project.id:raise HTTPException(404,'Furniture model not found in this project')
            if source.mode!='IMAGE_TO_CAD' or source.state!='REVIEW_REQUIRED' or source.input_data.get('cad_source')!='ENGINEERING_CAD':
                raise HTTPException(409,'Finish generating the engineering CAD package before editing its parts')
            revision=session.get(Revision,source.revision_id) if source.revision_id else None
            job=session.get(Job,source.job_id) if source.job_id else None
            if not revision or not job or job.state!='SUCCEEDED' or job.revision_id!=revision.id or revision.active_job_id!=job.id or revision.project_id!=project.id or revision.source_workflow_id!=source.id:
                raise HTTPException(409,'A matching completed CAD revision and job are required')
            try:
                if canonical_hash(revision.specification)!=revision.spec_hash:raise ValueError('Saved revision changed')
                if canonical_hash(revision.specification.get('photo_design'))!=source.checkpoints.get('engineering_plan',{}).get('sha256'):
                    raise ValueError('The component plan does not match its CAD revision')
                verify_package(safe_directory(config.storage,job.id),job,revision.spec_hash)
                from app.delivery import verify_delivery
                verify_delivery(config,(source.result or {}).get('delivery') or {})
                from app.engineering_edits import edit_inputs
                raw,checkpoints=edit_inputs(source,data.part_ids,data.instructions.strip(),data.style)
            except (ValueError,AIServiceError,KeyError,OSError) as exc:
                raise HTTPException(409,'Cannot edit this saved CAD package: '+str(exc)[:500])
            if not config.ai_enabled or not os.getenv('OPENAI_API_KEY') or any(not config.ai_models.get(role) for role in ('design','review')):
                raise HTTPException(503,'Configure backend design and review models before editing furniture')
            raw['client_request_hash']=digest
            if data.final_ai_correction and config.cad_final_correction_enabled:raw['final_ai_correction']=True
            else:raw.pop('final_ai_correction',None)
            from app.ai.furniture_budget import policy
            raw['ai_budget']=policy(config)
            workflow=AgentWorkflow(project_id=project.id,created_by=user.id,mode='IMAGE_TO_CAD',input_data=raw,
                input_hash=canonical_hash(raw),model_config=reconstruction_models(config),idempotency_key=key,checkpoints=checkpoints)
            session.add(workflow);session.flush();workflow_id=workflow.id
            audit(session,user.id,'engineering_edit_queued',workflow_id,source_workflow_id=source.id,part_ids=data.part_ids)
            orchestrator.event(session,workflow_id,'workflow_queued','queued',mode='IMAGE_TO_CAD',component_edit=True)
        try:orchestrator.dispatch_workflow(workflow_id,db,config)
        except Exception:
            with db.sessions.begin() as session:
                workflow=session.get(AgentWorkflow,workflow_id);workflow.state='FAILED'
                workflow.error=dict(code='AGENT_QUEUE_UNAVAILABLE',message='Worker queue unavailable; retry this edit',retryable=True)
            raise HTTPException(503,dict(message='Worker queue unavailable',workflow_id=workflow_id))
        return dict(workflow_id=workflow_id,status='QUEUED',status_url=f'/api/v1/agents/workflows/{workflow_id}')

    @router.post('/api/projects/{project_id}/complete-360',status_code=202)
    def complete_360(project_id:UUID,data:Complete360Request,user=Depends(current_user)):
        require_role(user,'admin','designer','operator')
        with db.sessions.begin() as session:
            project=project_access(session,project_id,user,lock=True)
            source=workflow_access(session,data.source_workflow_id,user)
            if source.project_id!=project.id:raise HTTPException(404,'Furniture model not found in this project')
            if source.state!='REVIEW_REQUIRED' or (source.input_data or {}).get('cad_source')!='ENGINEERING_CAD':
                raise HTTPException(409,'Complete a solid CAD model before adding unseen construction')
            key='complete-360:'+source.id
            existing=session.scalar(select(AgentWorkflow).where(AgentWorkflow.project_id==project.id,AgentWorkflow.idempotency_key==key))
            if existing:return dict(workflow_id=existing.id,status=existing.state,status_url=f'/api/v1/agents/workflows/{existing.id}')
            from app.storage_completion_service import completion_checkpoints
            try:checkpoints=completion_checkpoints(source,project.name)
            except ValueError as exc:raise HTTPException(422,str(exc))
            from app.storage_completion import POLICY
            raw=copy.deepcopy(source.input_data)
            raw.pop('engineering_reexport_of',None);raw.pop('client_request_hash',None)
            raw.update(engineering_completion_of=source.id,engineering_local_completion=True,reconstruction_policy=POLICY)
            workflow=AgentWorkflow(project_id=project.id,created_by=user.id,mode='IMAGE_TO_CAD',input_data=raw,
                input_hash=canonical_hash(raw),model_config=copy.deepcopy(source.model_config),idempotency_key=key,checkpoints=checkpoints)
            session.add(workflow);session.flush();workflow_id=workflow.id
            audit(session,user.id,'full_360_completion_queued',workflow_id,source_workflow_id=source.id)
            orchestrator.event(session,workflow_id,'workflow_queued','queued',mode='IMAGE_TO_CAD',local_completion=True)
        try:orchestrator.dispatch_workflow(workflow_id,db,config)
        except Exception:
            with db.sessions.begin() as session:
                workflow=session.get(AgentWorkflow,workflow_id);workflow.state='FAILED'
                workflow.error=dict(code='AGENT_QUEUE_UNAVAILABLE',message='Worker queue unavailable; retry this workflow',retryable=True)
            raise HTTPException(503,dict(message='Worker queue unavailable',workflow_id=workflow_id))
        return dict(workflow_id=workflow_id,status='QUEUED',status_url=f'/api/v1/agents/workflows/{workflow_id}')

    @router.post('/api/projects/{project_id}/generate-all',status_code=202)
    def generate_all(project_id:UUID,data:ImageToCADRequest,user=Depends(current_user)):
        require_role(user,'admin','designer','operator')
        request=data.model_dump(mode='json');key=request.pop('idempotency_key',None)
        if request.get('engineering_reference_workflow_id') is None:request.pop('engineering_reference_workflow_id',None)
        if request.get('analysis_workflow_id') is None:request.pop('analysis_workflow_id',None)
        if request.get('optimization') is None:request.pop('optimization',None)
        if not data.final_ai_correction:request.pop('final_ai_correction',None)
        digest=canonical_hash(request)
        with db.sessions.begin() as session:
            project=project_access(session,project_id,user,lock=True)
            if key:
                existing=session.scalar(select(AgentWorkflow).where(AgentWorkflow.project_id==project.id,AgentWorkflow.idempotency_key==key))
                if existing:
                    if existing.mode!='IMAGE_TO_CAD' or existing.input_data.get('client_request_hash')!=digest:
                        raise HTTPException(409,'Idempotency key belongs to different inputs')
                    return dict(workflow_id=existing.id,status=existing.state,status_url=f'/api/v1/agents/workflows/{existing.id}')
            image=session.get(ImageAsset,str(data.image_id))
            if not image or image.project_id!=project.id or image.kind not in ('REFERENCE','AI_CONCEPT_IMAGE','AI_CONCEPT'):
                raise HTTPException(404,'Furniture reference not found in this project')
            reference_ids=[str(value) for value in (data.image_ids or [data.image_id])]
            reference_views=[view.model_dump(mode='json') for view in data.reference_views]
            if data.analysis_workflow_id and not data.image_ids:
                saved_analysis=workflow_access(session,data.analysis_workflow_id,user)
                saved_ids=(saved_analysis.input_data or {}).get('image_ids',[])
                if saved_ids and saved_ids[0]==image.id:reference_ids=list(saved_ids)
            for reference_id in reference_ids:
                asset=session.get(ImageAsset,reference_id)
                if not asset or asset.project_id!=project.id or asset.kind not in ('REFERENCE','AI_CONCEPT_IMAGE','AI_CONCEPT'):
                    raise HTTPException(404,'Furniture reference not found in this project')
                if data.optimization:
                    for view in data.optimization.views:
                        if str(view.image_id)==asset.id and view.camera and tuple(view.camera.image_size)!=(asset.width,asset.height):
                            raise HTTPException(422,'Optimization pixels and camera must use the stored reference image resolution')
            try:validate_references(reference_ids,data.reference_views)
            except ValueError as exc:raise HTTPException(422,str(exc))
            raw=StudioRequest(project_id=project.id,image_ids=reference_ids,known_values=data.known_values,notes=data.notes,
                reference_views=reference_views,
                auto_render=False,render_options=data.render_options).model_dump(mode='json')
            from app.sample_dimensions import dimension_inputs
            raw['user_known_values']=list(raw['known_values'])
            raw['dimension_match']=(image.provenance or {}).get('dimension_match',{'status':'NO_MATCH'})
            raw['known_values'],raw['dimension_sources']=dimension_inputs(raw['dimension_match'],raw['known_values'])
            if data.analysis_workflow_id:
                analysis=workflow_access(session,data.analysis_workflow_id,user)
                if analysis.project_id!=project.id:raise HTTPException(404,'Image analysis not found in this project')
                if analysis.mode!='PHOTO_ANALYZE' or analysis.state!='SUCCEEDED':
                    raise HTTPException(409,'Wait for the image analysis to complete before creating CAD')
                if analysis.input_data.get('image_ids')!=reference_ids:
                    raise HTTPException(422,'The analysis belongs to a different photo set or primary image')
                saved_views=analysis.input_data.get('reference_views',[])
                if reference_views and reference_views!=saved_views:
                    raise HTTPException(422,'The photo angle labels changed; analyse the selected views again')
                if saved_views:raw['reference_views']=copy.deepcopy(saved_views)
                checkpoint=analysis.checkpoints.get('photo_analysis')
                if canonical_hash(analysis.input_data)!=analysis.input_hash or not checkpoint or canonical_hash(checkpoint['payload'])!=checkpoint['sha256']:
                    raise HTTPException(409,'Image analysis integrity verification failed')
                from app.agents.photo_agent import PhotoAnalysis
                observations=PhotoAnalysis.model_validate(checkpoint['payload'])
                fields={field.key:field for field in observations.matching_inputs}
                if any(value.key not in fields for value in data.known_values):
                    raise HTTPException(422,'Use the dimension fields identified for this furniture image')
                raw.update(analysis_workflow_id=analysis.id,source_photo_analysis=observations.model_dump(mode='json'),
                    analysis_sha256=checkpoint['sha256'],dimension_fields=[field.model_dump(mode='json') for field in fields.values()])
            engineering_stages={}
            from app.engineering_upstream import provenance
            raw['engineering_integration']=dict(version='CADQUERY_SOLIDS_V1',upstream_commit=provenance()['commit'])
            raw['output_scope']=data.output_scope
            if data.final_ai_correction and config.cad_final_correction_enabled:raw['final_ai_correction']=True
            if data.optimization is not None:
                # Freeze controls into the existing hashed workflow input.
                controls=data.optimization
                if 'mode' not in controls.model_fields_set:
                    controls=controls.model_copy(update={'mode':config.cad_optimizer_mode})
                limit=min(controls.budgets()[1],config.cad_optimizer_max_seconds)
                raw['optimization']=controls.model_copy(update={'max_seconds':limit}).model_dump(mode='json')
            raw['photo_match_policy']=PHOTO_MATCH_POLICY
            raw['sweep_frame_policy']='WORLD_UP_V2'
            if data.output_scope=='WHOLE_FURNITURE':
                from app.ai.furniture_budget import policy
                raw['ai_budget']=policy(config)
            from app.storage_completion import POLICY
            raw['reconstruction_policy']=POLICY
            if data.engineering_reference_workflow_id:
                reference=workflow_access(session,data.engineering_reference_workflow_id,user)
                if reference.project_id!=project.id:raise HTTPException(404,'Engineering plan not found in this project')
                if (reference.input_data or {}).get('cad_source')!='ENGINEERING_CAD' or reference.state not in ('REVIEW_REQUIRED','FAILED','CAD_FAILED'):
                    raise HTTPException(409,'A completed frozen engineering plan is required')
                if canonical_hash(reference.input_data)!=reference.input_hash or reference.input_data.get('engineering_integration')!=raw['engineering_integration']:
                    raise HTTPException(409,'The saved engineering input or integration version changed')
                if reference.input_data.get('image_id')!=image.id:raise HTTPException(422,'Use the same reference image as the saved plan')
                for stage,checkpoint in reference.checkpoints.items():
                    if stage in ('engineering_dimensions','engineering_assembly','engineering_plan','engineering_structural_call_0','engineering_cost_stop','engineering_budget_draft') or stage.startswith(('engineering_critic_','engineering_visual_review_','engineering_optimization_','engineering_final_')):
                        if canonical_hash(checkpoint['payload'])!=checkpoint['sha256']:raise HTTPException(409,'Engineering source checkpoint changed')
                        engineering_stages[stage]=copy.deepcopy(checkpoint)
                if not {'engineering_dimensions','engineering_assembly','engineering_plan'}<=engineering_stages.keys():
                    raise HTTPException(409,'The engineering plan has not completed')
                if not any(all(f'engineering_{kind}_{i}' in engineering_stages for kind in ('critic','visual_review')) for i in range(2)):
                    raise HTTPException(409,'The saved plan has no completed review stages')
                inherited=reference.input_data.get('known_values',[])
                inherited_values={entry['key']:entry['value'] for entry in inherited}
                if any(inherited_values.get(value.key)!=value.value for value in data.known_values):
                    raise HTTPException(422,'Local re-export retains the saved dimensions; generate a new design for changed inputs')
                raw['known_values']=inherited;raw['engineering_reexport_of']=reference.id
                raw['image_ids']=copy.deepcopy(reference.input_data.get('image_ids',[image.id]))
                raw['reference_views']=copy.deepcopy(reference.input_data.get('reference_views',[]))
                if reference.input_data.get('engineering_manual_reconstruction'):
                    raw['engineering_manual_reconstruction']=copy.deepcopy(reference.input_data['engineering_manual_reconstruction'])
                for field,default in (('dimension_match',{'status':'NO_MATCH'}),('dimension_sources',{}),('user_known_values',inherited)):
                    raw[field]=copy.deepcopy(reference.input_data.get(field,default))
                raw['reconstruction_policy']=reference.input_data.get('reconstruction_policy')
                raw['sweep_frame_policy']=reference.input_data.get('sweep_frame_policy')
                if reference.input_data.get('optimization') is not None:
                    raw['optimization']=copy.deepcopy(reference.input_data['optimization'])
                if reference.input_data.get('final_ai_correction'):
                    raw['final_ai_correction']=True
            elif not config.ai_enabled or not os.getenv('OPENAI_API_KEY'):
                raise HTTPException(503,dict(code='AI_CONFIGURATION_REQUIRED',message='Configure the backend OpenAI client for engineering CAD'))
            if not data.engineering_reference_workflow_id and any(not config.ai_models.get(role) for role in ('vision','design','review')):
                raise HTTPException(503,dict(code='MODEL_CONFIGURATION_REQUIRED',message='Configure vision, design and review models'))
            raw.update(image_id=str(data.image_id),client_request_hash=digest,cad_source='ENGINEERING_CAD')
            models=dict(reference.model_config) if data.engineering_reference_workflow_id else reconstruction_models(config)
            workflow=AgentWorkflow(project_id=project.id,created_by=user.id,mode='IMAGE_TO_CAD',input_data=raw,
                input_hash=canonical_hash(raw),model_config=models,idempotency_key=key,checkpoints=engineering_stages)
            session.add(workflow);session.flush();workflow_id=workflow.id
            orchestrator.event(session,workflow_id,'workflow_queued','queued',mode='IMAGE_TO_CAD')
            audit(session,user.id,'image_to_cad_queued',workflow_id,project_id=project.id,reference_image_id=image.id)
        try:orchestrator.dispatch_workflow(workflow_id,db,config)
        except Exception:
            with db.sessions.begin() as session:
                workflow=session.get(AgentWorkflow,workflow_id);workflow.state='FAILED'
                workflow.error=dict(code='AGENT_QUEUE_UNAVAILABLE',message='Worker queue unavailable; retry this workflow',retryable=True)
            raise HTTPException(503,dict(message='Worker queue unavailable',workflow_id=workflow_id))
        return dict(workflow_id=workflow_id,status='QUEUED',status_url=f'/api/v1/agents/workflows/{workflow_id}')

    @router.get('/api/projects/{project_id}/deliveries/{workflow_id}/files/{filename}')
    def delivery_file(project_id:UUID,workflow_id:UUID,filename:str,user=Depends(current_user),session=Depends(db_session)):
        workflow=workflow_access(session,workflow_id,user)
        if workflow.project_id!=str(project_id):raise HTTPException(404,'Delivery not found')
        checkpoint=workflow.checkpoints.get('delivery_package')
        if not checkpoint:raise HTTPException(409,'The complete package is still being generated')
        if canonical_hash(checkpoint['payload'])!=checkpoint['sha256']:raise HTTPException(409,'Delivery checkpoint changed')
        artifact=checkpoint['payload']
        if filename not in artifact['files']:raise HTTPException(404,'Delivery file not found')
        from app.delivery import verify_delivery
        try:directory=verify_delivery(config,artifact)
        except AIServiceError as exc:raise HTTPException(409,exc.as_dict())
        return FileResponse(safe_directory(directory,filename),filename=filename)

    api.include_router(router)
