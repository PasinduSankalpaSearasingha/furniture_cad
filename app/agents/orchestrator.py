"""Checkpointed collaboration with bounded calls; no LLM-controlled state transitions."""
import json
import logging
from sqlalchemy import select, update
from app.database import (AgentWorkflow, AgentEvent, AIInvocation, ImageAsset, CatalogEntry,
                          Revision, Job, utcnow, uid, audit)
from app.pipeline import canonical_hash, safe_directory, verify_package, file_hash
from app.ai.openai_client import ResponsesGateway, AIServiceError, calculate_cost
from app.ai.response_store import WorkflowResponseStore
from app.ai.schemas import FurnitureVisionResult, DesignProposal, JoineryRecommendation, QualityReviewResult, CADGenerationResult, ManufacturingPackage
from app.ai.images import image_content, store_image, image_json
from app.ai.image_generation import decode_generated_image
from . import vision_agent, design_agent, joinery_agent, review_agent, render_agent

SUPPORTED_MODES = {'ANALYZE','COLLABORATIVE','REVIEW','CONCEPT','RENDER','ESTIMATE',
    'PHOTO','PHOTO_ANALYZE','CONCEPT_V2','PIPELINE','MODIFY','BLENDER','VISUAL_REVIEW','IMAGE_TO_CAD'}


def workflow_supported(workflow):
    return workflow.mode in SUPPORTED_MODES and (workflow.mode!='IMAGE_TO_CAD' or
        (workflow.input_data or {}).get('cad_source')=='ENGINEERING_CAD')


logger = logging.getLogger('furniture.agents')


def event(session,workflow_id,event_type,stage,**details):
    session.add(AgentEvent(workflow_id=workflow_id,event=event_type,stage=stage,details=details))


class WorkflowRunner:
    def __init__(self,workflow_id,db,config):
        self.id,self.db,self.config = workflow_id,db,config
        self.lease = uid()
        self.stage = 'starting'
        self.gateway = None

    def record(self,name,data):
        with self.db.sessions.begin() as session:
            workflow = self.checked_workflow(session)
            workflow.updated_at = utcnow()
            if name=='invocation_submitting':
                # Reservation and event commit precede the HTTP submission.
                # Failed/ambiguous attempts, tool continuations and fallbacks all count.
                # Linked image analysis belongs to the same furniture generation.
                session.flush()
                scope=[self.id]
                parent=(workflow.input_data or {}).get('analysis_workflow_id')
                if parent:
                    linked=session.get(AgentWorkflow,parent)
                    if not linked or linked.project_id!=workflow.project_id:
                        raise AIServiceError('AI_GENERATION_LIMIT','Linked analysis cannot be verified')
                    scope.append(parent)
                used=list(session.scalars(select(AgentEvent.id).where(AgentEvent.workflow_id.in_(scope),
                    AgentEvent.event=='invocation_submitting')))
                # One extra request is allowed solely to repair a rejected design.
                repair=session.scalar(select(AgentEvent.id).where(AgentEvent.workflow_id.in_(scope),
                    AgentEvent.event=='structured_output_rejected').limit(1))
                if len(used)>=(3 if repair else 2):
                    raise AIServiceError('AI_GENERATION_LIMIT','The two generation request limit has been reached; local CAD and exports remain available')
            if name=='invocation_polling':
                invocation = session.get(AIInvocation,data.get('invocation_id'))
                if not invocation or invocation.workflow_id!=self.id or invocation.response_id!=data['response_id']:
                    raise AIServiceError('INVOCATION_STATE_INVALID','Polling did not match the saved response')
                state = data['status'].upper()
                if invocation.status==state:return
                invocation.status = state
            event(session,self.id,name,self.stage,**{k:v for k,v in data.items() if k not in ('usage',)})
            if name=='invocation_started':
                invocation = AIInvocation(workflow_id=self.id,stage=self.stage,model=data['model'],request_hash=data['request_hash'])
                if data.get('budget_reservation'):
                    from app.ai.furniture_budget import reserve
                    invocation.cost=reserve(session,workflow,data['budget_reservation'])
                session.add(invocation)
                session.flush()
                return invocation.id
            if name in ('invocation_submitting','invocation_submitted','invocation_completed','invocation_failed','invocation_pending'):
                invocation = session.get(AIInvocation,data.get('invocation_id'))
                if not invocation or invocation.workflow_id != self.id:
                    raise AIServiceError('INVOCATION_STATE_INVALID','Invocation trace did not match workflow')
                if name=='invocation_submitting':
                    invocation.status = 'SUBMITTING'
                elif name=='invocation_submitted':
                    invocation.response_id = data['response_id']
                    invocation.status = data['status'].upper() if data['status'] in ('queued','in_progress') else 'RECEIVED'
                elif name=='invocation_completed':
                    reservation=invocation.cost or {}
                    invocation.status = 'COMPLETED'
                    invocation.error_code = None
                    invocation.response_id,invocation.usage = data['response_id'],data['usage']
                    price = next((p for p in session.scalars(select(CatalogEntry).where(CatalogEntry.kind=='ai_prices').order_by(
                        CatalogEntry.created_at.desc())) if p.payload['model_id']==data['model']),None)
                    options = {'model':workflow.model_config.get('image'),'quality':self.inputs.get('quality'),'size':self.inputs.get('size')}
                    profile=reservation.get('rate_snapshot') or (price.payload if price else None)
                    cost=calculate_cost(data['usage'],profile,options)
                    if reservation.get('status')=='BUDGET_RESERVED':
                        raw=data['usage'].get('raw') or {};details=raw.get('input_tokens_details') or {}
                        verified=all(type(raw.get(k)) is int and raw[k]>=0 for k in ('input_tokens','output_tokens'))
                        verified=verified and type(details.get('cache_write_tokens')) is int and type(details.get('cached_tokens')) is int
                        from decimal import Decimal
                        if (not verified or cost.get('total') is None or Decimal(cost['total'])>Decimal(reservation['reserved_usd'])):
                            cost=dict(reservation,status='USAGE_UNCERTAIN',total=cost.get('total'))
                    invocation.cost = cost
                else:
                    invocation.status = 'PENDING' if name=='invocation_pending' else (
                        'UNTRACKED' if data.get('submission_uncertain') else 'FAILED')
                    invocation.error_code = data['error_code']
                    if (invocation.cost or {}).get('status')=='BUDGET_RESERVED' and invocation.status=='FAILED' and not invocation.response_id:
                        invocation.cost=dict(status='NOT_BILLED',currency='USD',total='0')

    def checked_workflow(self,session):
        workflow = session.get(AgentWorkflow,self.id,with_for_update=True)
        if workflow.state != 'RUNNING' or workflow.lease_token != self.lease:
            raise AIServiceError('WORKFLOW_LEASE_LOST','Workflow was interrupted or recovered by an administrator')
        return workflow

    def progress_value(self,key,percent):
        return percent

    def update_progress(self,key,percent):
        self.stage=key
        with self.db.sessions.begin() as session:
            workflow=self.checked_workflow(session);workflow.stage=key
            workflow.progress=max(workflow.progress,self.progress_value(key,percent))
            workflow.updated_at=utcnow()

    def checkpoint(self,key,schema,callback,percent):
        self.stage = key
        with self.db.sessions.begin() as session:
            workflow = self.checked_workflow(session)
            cached = workflow.checkpoints.get(key)
            workflow.stage,workflow.progress,workflow.updated_at = key,max(workflow.progress,self.progress_value(key,percent)),utcnow()
            if cached:
                if canonical_hash(cached['payload']) != cached['sha256']:
                    raise AIServiceError('CHECKPOINT_INTEGRITY_FAILED','Stored checkpoint failed hash verification')
                return schema.model_validate_json(json.dumps(cached['payload'])) if schema else cached['payload']
            event(session,self.id,'stage_started',key)
        value = callback()
        payload = value.model_dump(mode='json') if hasattr(value,'model_dump') else value
        with self.db.sessions.begin() as session:
            workflow = self.checked_workflow(session)
            workflow.checkpoints = {**workflow.checkpoints,key:dict(payload=payload,sha256=canonical_hash(payload))}
            workflow.updated_at = utcnow()
            event(session,self.id,'stage_completed',key,output_sha256=canonical_hash(payload))
        return value

    def images(self,ids=None):
        with self.db.sessions() as session:
            records = []
            for image_id in ids if ids is not None else self.inputs.get('image_ids',[]):
                record = session.get(ImageAsset,image_id)
                if not record or record.project_id != self.project_id:
                    raise AIServiceError('IMAGE_REFERENCE_INVALID','Reference image does not belong to this project')
                records.append(record)
            return image_content(records,self.config)

    def artifacts(self,revision_id,job_id=None):
        with self.db.sessions() as session:
            revision = session.get(Revision,revision_id)
            if not revision or revision.project_id != self.project_id:
                raise AIServiceError('DESIGN_REVISION_REQUIRED','Design revision does not belong to the workflow project')
            job = session.get(Job,job_id or revision.active_job_id) if (job_id or revision.active_job_id) else None
            if (not job or job.state!='SUCCEEDED' or job.revision_id!=revision.id or job.id!=revision.active_job_id):
                raise AIServiceError('CAD_OUTPUT_REQUIRED','A matching completed CAD package is required')
            directory = safe_directory(self.config.storage,job.id)
            verify_package(directory,job,revision.spec_hash)
            return revision,job,directory

    def review_payload(self,revision_id,job_id=None):
        revision,job,directory = self.artifacts(revision_id,job_id)
        load = lambda name: json.loads((directory/name).read_text(encoding='utf-8'))
        return dict(revision_id=revision.id,revision=revision.number,specification_hash=revision.spec_hash,
                    parts=load('parts_registry.json'),schedules=load('schedules_REVIEW.json'),
                    drawing_manifest=load('drawing_manifest.json'),deterministic_validation=load('validation_report.json'))

    def collaborative(self):
        images = self.images()
        if self.revision_id and self.job_id:
            payload = self.review_payload(self.revision_id,self.job_id)
            revision,job,directory = self.artifacts(self.revision_id,self.job_id)
            if self.mode=='PHOTO':
                import base64
                images=[*images,dict(type='input_image',image_url='data:image/png;base64,'+
                    base64.b64encode((directory/'cad_preview.png').read_bytes()).decode('ascii'),detail='high')]
                payload['visual_comparison_instructions']='Earlier images are the uploaded furniture. The LAST image is the actual generated CAD. Compare contours, curves, proportions, counts and missing visible features; report differences explicitly. No exact match has been verified.'
            cad = self.checkpoint('cad_result',CADGenerationResult,lambda:CADGenerationResult(
                revision_id=revision.id,job_id=job.id,specification_hash=revision.spec_hash,
                part_ids=[p['part_id'] for p in payload['parts']],status='GENERATED_REVIEW_ONLY',
                validation=payload['deterministic_validation']),80)
            package = self.checkpoint('manufacturing_package',ManufacturingPackage,lambda:ManufacturingPackage(
                revision_id=revision.id,job_id=job.id,package_sha256=job.package_hash,manifest_sha256=job.manifest_hash,
                status='REVIEW',approved_for_manufacturing=False,part_count=len(payload['parts'])),85)
            def completed_review():
                fidelity=(self.previous_result or {}).get('reference_fidelity') or {}
                if self.mode=='PHOTO' and fidelity.get('status') in ('VISIBLE_MATCH_REVIEWED','NEEDS_VISUAL_REVIEW','PARAMETER_EDIT_PRESERVED'):
                    from app.ai.schemas import QualityIssue
                    comparison=fidelity.get('review') or {}
                    if fidelity.get('previews',{}).get('plan_sha256') not in (None,canonical_hash(revision.specification['photo_design'])):
                        raise AIServiceError('PHOTO_PREVIEW_INTEGRITY_FAILED','The comparison belongs to a different CAD plan')
                    result=QualityReviewResult(summary=comparison.get('summary') or (
                        'Only supplied parameter values and dependent geometry changed; the saved component definitions were retained.' if fidelity['status']=='PARAMETER_EDIT_PRESERVED' else
                        'Photo reconstruction built; reference comparison is incomplete and still requires review.'),
                        issues=[QualityIssue(code='PHOTO_VISIBLE_GEOMETRY_DIFFERENCE',severity='WARNING',
                            message=d['observed_difference'],component_ids=d['component_ids']) for d in comparison.get('differences',[])],
                        revision_recommended=fidelity['status']=='NEEDS_VISUAL_REVIEW',engineering_certified=False)
                    return review_agent.retain_deterministic(result,payload)
                return review_agent.review(self.gateway,{**payload,'proposal':(self.previous_result or {}).get('design') or (self.previous_result or {}).get('estimate')},images)
            quality = self.checkpoint('post_cad_review',QualityReviewResult,completed_review,90)
            return 'REVIEW_REQUIRED',{**(self.previous_result or {}),'cad':cad.model_dump(mode='json'),
                'manufacturing_package':package.model_dump(mode='json'),'post_cad_review':quality.model_dump(mode='json'),
                'deterministic_validation':payload['deterministic_validation']}
        vision = self.checkpoint('vision',FurnitureVisionResult,
            lambda: FurnitureVisionResult.model_validate(self.inputs['source_vision']) if self.inputs.get('source_vision')
            else vision_agent.analyze(self.gateway,images,self.inputs.get('notes','')),15)
        if self.mode=='ANALYZE': return 'SUCCEEDED',dict(vision=vision.model_dump(mode='json'))
        feedback = None
        for iteration in range(self.inputs.get('max_corrections',0)+1):
            design = self.checkpoint(f'design_{iteration}',DesignProposal,
                lambda:design_agent.propose(self.gateway,vision,self.inputs.get('notes',''),feedback),30+iteration*15)
            joinery = self.checkpoint(f'joinery_{iteration}',JoineryRecommendation,
                lambda:joinery_agent.recommend(self.gateway,design,self.inputs['joinery_catalog']),40+iteration*15)
            quality = self.checkpoint(f'review_{iteration}',QualityReviewResult,
                lambda:review_agent.review(self.gateway,dict(vision=vision.model_dump(mode='json'),
                    design=design.model_dump(mode='json'),joinery=joinery.model_dump(mode='json')),images),50+iteration*15)
            if not quality.revision_recommended or design.status=='CUSTOM_DESIGN_REVIEW_REQUIRED': break
            feedback = quality
        result = dict(vision=vision.model_dump(mode='json'),design=design.model_dump(mode='json'),
                      joinery=joinery.model_dump(mode='json'),review=quality.model_dump(mode='json'),
                      automatic_corrections=iteration,requires_designer_verification=True,approved_for_manufacturing=False)
        state = 'WAITING_FOR_DESIGNER' if design.status=='SUPPORTED_TEMPLATE' else 'CUSTOM_DESIGN_REVIEW_REQUIRED'
        return state,result

    def presentation(self):
        source = None
        ids = list(self.inputs.get('image_ids',[]))
        if self.mode=='RENDER':
            def preview():
                from app.ai.cad_preview import render_step_preview
                revision,job,directory = self.artifacts(self.inputs['revision_id'],self.inputs['source_job_id'])
                if revision.spec_hash != self.inputs['source_spec_hash']:
                    raise AIServiceError('SPECIFICATION_HASH_MISMATCH','Presentation source revision changed')
                pixels = render_step_preview(directory)
                with self.db.sessions.begin() as session:
                    record = store_image(session,self.config,self.project_id,self.actor,pixels,'CAD_PREVIEW',dict(
                        source_revision_id=revision.id,source_spec_hash=revision.spec_hash,source_job_id=job.id,
                        source_cad_sha256=file_hash(directory/'cabinet.step'),source_design_state=revision.state,
                        authoritative_geometry=False,source_cad_is_authoritative=True,source='STEP_RASTERIZATION'))
                    return image_json(record)
            source = self.checkpoint('cad_preview',None,preview,25)
            # A render is based only on the frozen CAD preview, never a client-supplied replacement.
            ids = [source['id']]
            self.artifacts(source['provenance']['source_revision_id'],source['provenance']['source_job_id'])
        images = self.images(ids)
        def generate():
            pixels = decode_generated_image(render_agent.render(self.gateway,self.inputs['prompt'],images,
                                        self.inputs['quality'],self.inputs['size']))
            provenance = dict(source='OPENAI_IMAGE_GENERATION',authoritative_geometry=False,workflow_id=self.id,
                reference_image_ids=ids,source_revision_id=self.inputs.get('revision_id'),
                cad_preview=source,source_spec_hash=source['provenance']['source_spec_hash'] if source else None)
            with self.db.sessions.begin() as session:
                record = store_image(session,self.config,self.project_id,self.actor,pixels,
                    'AI_PRESENTATION' if source else 'AI_CONCEPT',provenance)
                return image_json(record)
        result = self.checkpoint('generated_image',None,generate,75)
        return 'SUCCEEDED',dict(image=result,source_cad_preview=source,authoritative_geometry=False)

    def estimated(self):
        from .furniture_agent import FurnitureEstimate, estimate, specification
        from app.database import Project
        if self.revision_id and self.job_id: return self.collaborative()
        images=self.images()
        vision=self.checkpoint('vision',FurnitureVisionResult,
            lambda: FurnitureVisionResult.model_validate(self.inputs['source_vision']) if self.inputs.get('source_vision')
            else vision_agent.analyze(self.gateway,images,self.inputs.get('notes','')),15) if images else None
        result=self.checkpoint('furniture_estimate',FurnitureEstimate,
            lambda:estimate(self.gateway,self.inputs,vision,images),40)
        with self.db.sessions() as session: name=session.get(Project,self.project_id).name
        spec,provenance=specification(result,self.inputs,name)
        payload=dict(estimate=result.model_dump(mode='json'),specification=spec.model_dump(mode='json'),
            value_provenance=provenance,approved_for_manufacturing=False,dimension_basis='ESTIMATED_REVIEW_ONLY')
        if vision: payload['vision']=vision.model_dump(mode='json')
        return 'ESTIMATED_READY',payload

    def photograph(self):
        from . import photo_agent
        from app.photo_schema import PhotoDesign
        from app.database import Project
        if self.revision_id and self.job_id: return self.collaborative()
        images=self.images()
        with self.db.sessions() as session: name=session.get(Project,self.project_id).name
        if self.inputs.get('source_plan'):
            from .photo_fidelity import parameter_edit,refine,POLICY_VERSION
            source=PhotoDesign.model_validate(self.inputs['source_plan'])
            plan=parameter_edit(source,self.inputs)
            if self.inputs.get('refine_source_geometry'):
                plan,fidelity=refine(self,plan,self.inputs.get('source_photo_observations') or {},name,images)
            else:
                fidelity=dict(policy_version=POLICY_VERSION,status='PARAMETER_EDIT_PRESERVED',
                    source_revision_id=self.inputs['source_revision_id'],exact_reconstruction_verified=False,
                    estimate_adjustments=photo_agent.estimate_adjustments(self.inputs.get('source_value_provenance') or {}))
            plan,spec,provenance,_,_=photo_agent.resolve_estimated_geometry(plan,self.inputs,name)
            photo_agent.retain_estimate_basis(provenance,fidelity.get('estimate_adjustments',{}))
            self.checkpoint('photo_plan',PhotoDesign,lambda:plan,55)
            return 'ESTIMATED_READY',dict(**(self.inputs.get('source_photo_observations') or {}),
                specification=spec.model_dump(mode='json'),value_provenance=provenance,
                reference_fidelity=fidelity,
                approved_for_manufacturing=False,dimension_basis='PHOTO_DERIVED_ESTIMATE',exact_reconstruction_verified=False)
        if self.inputs.get('source_vision'):
            vision=self.checkpoint('vision',FurnitureVisionResult,
                lambda:FurnitureVisionResult.model_validate(self.inputs['source_vision']),15)
            observations={**self.inputs.get('source_photo_analysis',{}),'vision':vision.model_dump(mode='json')}
        else:
            analysis=self.checkpoint('photo_analysis',photo_agent.PhotoAnalysis,
                lambda:photo_agent.analyze(self.gateway,images,self.inputs.get('notes',''),self.inputs.get('reference_views',[])),15)
            vision=self.checkpoint('vision',FurnitureVisionResult,lambda:analysis.vision,20)
            observations=analysis.model_dump(mode='json')
        if self.mode=='PHOTO_ANALYZE': return 'SUCCEEDED',observations
        feedback=None
        for iteration in range(2):
            plan=self.checkpoint(f'photo_plan_{iteration}',PhotoDesign,
                lambda:photo_agent.plan(self.gateway,images,observations,self.inputs,feedback),35+iteration*10)
            try:
                plan,spec,provenance,_,adjustments=photo_agent.resolve_estimated_geometry(plan,self.inputs,name)
                if adjustments: self.record('photo_estimates_reconciled',dict(adjustments=adjustments))
                break
            except Exception as exc:
                self.record('photo_plan_invalid',dict(iteration=iteration,reason=str(exc)[:400]))
                if iteration:
                    raise AIServiceError('PHOTO_GEOMETRY_NEEDS_REVISION',
                        'Photo geometry needs revision: '+str(exc)[:220]+'. Revise conflicting inputs or add another view.',True)
                feedback=dict(issue=str(exc)[:600],previous_plan=plan.model_dump(mode='json'),
                    instruction='Correct the geometry while preserving every supplied value and the photographed design.')
        from .photo_fidelity import refine
        plan,fidelity=refine(self,plan,observations,name,images,initial_provenance=provenance)
        plan,spec,provenance,_,_=photo_agent.resolve_estimated_geometry(plan,self.inputs,name)
        photo_agent.retain_estimate_basis(provenance,fidelity.get('estimate_adjustments',{}))
        self.checkpoint('photo_plan',PhotoDesign,lambda:plan,55)
        return 'ESTIMATED_READY',dict(**observations,estimate=dict(summary=plan.summary,assumptions=plan.assumptions,
            unrepresented_features=plan.unrepresented_features),specification=spec.model_dump(mode='json'),
            reference_fidelity=fidelity,
            value_provenance=provenance,approved_for_manufacturing=False,dimension_basis='PHOTO_DERIVED_ESTIMATE',
            exact_reconstruction_verified=False)

    def run(self):
        with self.db.sessions.begin() as session:
            pending=session.get(AgentWorkflow,self.id)
            if not pending:return
            attempt_limit=10 if pending.mode=='IMAGE_TO_CAD' else self.config.ai_max_attempts
            claimed = session.execute(update(AgentWorkflow).where(AgentWorkflow.id==self.id,AgentWorkflow.state=='QUEUED',
                AgentWorkflow.attempts<attempt_limit).values(state='RUNNING',lease_token=self.lease,
                    attempts=AgentWorkflow.attempts+1,updated_at=utcnow())).rowcount
            if not claimed: return
            workflow = session.get(AgentWorkflow,self.id)
            self.inputs,self.mode,self.project_id,self.actor = workflow.input_data,workflow.mode,workflow.project_id,workflow.created_by
            self.revision_id,self.job_id = workflow.revision_id,workflow.job_id
            self.previous_result = workflow.result
            models = workflow.model_config
            event(session,self.id,'workflow_started','starting',attempt=workflow.attempts)
        try:
            if canonical_hash(self.inputs) != workflow.input_hash:
                raise AIServiceError('WORKFLOW_INPUT_INTEGRITY_FAILED','Stored workflow inputs changed')
            if not workflow_supported(workflow):
                raise AIServiceError('WORKFLOW_UNSUPPORTED','This workflow is no longer supported. Upload the photograph to create Engineering CAD.')
            local_fit=self.mode=='MODIFY' and (self.inputs.get('reference_fit') is not None or
                (self.inputs.get('modeling_engine')=='blender' and self.inputs.get('source_plan') and not self.inputs.get('prompt','').strip()))
            local_engineering=self.mode=='IMAGE_TO_CAD' and self.inputs.get('cad_source')=='ENGINEERING_CAD' and (
                self.inputs.get('engineering_reexport_of') or self.inputs.get('engineering_local_completion') or
                self.inputs.get('engineering_manual_reconstruction'))
            manual=self.inputs.get('engineering_manual_reconstruction')
            if manual:
                required={'engineering_plan','engineering_dimensions','engineering_assembly','engineering_critic_0','engineering_visual_review_0'}
                with self.db.sessions() as session:source=session.get(AgentWorkflow,manual.get('source_workflow_id'))
                if (manual.get('origin')!='LOCAL_REFERENCE_RECONSTRUCTION' or not source or source.project_id!=self.project_id or
                        not required<=workflow.checkpoints.keys() or any(workflow.checkpoints[key]['payload'].get('accepted') is not False
                            for key in ('engineering_critic_0','engineering_visual_review_0'))):
                    raise AIServiceError('LOCAL_REFERENCE_CONTRACT_INVALID','Local reconstruction requires a same-project source, frozen CAD plan and explicitly unaccepted visual review')
            self.gateway = ResponsesGateway(self.config,models,self.record) if self.mode!='BLENDER' and not local_fit and not local_engineering else None
            if self.gateway is not None:self.gateway.response_store = WorkflowResponseStore(self)
            if self.gateway is not None and self.inputs.get('ai_budget'):
                from app.ai.furniture_budget import FurnitureBudget
                self.gateway.request_budget=FurnitureBudget(self)
            if self.mode=='IMAGE_TO_CAD':
                from .engineering_agent import execute
                state,result=execute(self)
            elif self.mode in ('CONCEPT_V2','PIPELINE','MODIFY','BLENDER','VISUAL_REVIEW'):
                from .studio_agent import FurnitureDesignAgent
                agent=FurnitureDesignAgent(self)
                if self.mode=='CONCEPT_V2': state,result=agent.generate_concept()
                elif self.mode=='BLENDER': state,result='SUCCEEDED',dict(blender=agent.render(),approved_for_manufacturing=False)
                elif self.mode=='VISUAL_REVIEW':
                    render=self.inputs['source_render']
                    directory=safe_directory(self.config.storage,render['artifact_directory'])
                    if file_hash(directory/'render_manifest.json')!=render['manifest_sha256']:
                        raise AIServiceError('RENDER_INTEGRITY_FAILED','Source render manifest changed')
                    for name,digest in render['files'].items():
                        if file_hash(safe_directory(directory,name))!=digest:
                            raise AIServiceError('RENDER_INTEGRITY_FAILED','Source render artifact changed')
                    state,result='SUCCEEDED',dict(visual_review=agent.inspect_generated_render(render),
                        refinement_iteration=self.inputs.get('refinement_iteration',0),approved_for_manufacturing=False)
                else: state,result=agent.execute_design_workflow()
            elif self.mode in ('CONCEPT','RENDER'): state,result = self.presentation()
            elif self.mode=='ESTIMATE': state,result = self.estimated()
            elif self.mode in ('PHOTO','PHOTO_ANALYZE'): state,result = self.photograph()
            elif self.mode=='REVIEW':
                payload = self.review_payload(self.inputs['revision_id'],self.inputs['source_job_id'])
                quality = self.checkpoint('quality_review',QualityReviewResult,
                    lambda:review_agent.review(self.gateway,payload,self.images()),75)
                state,result = 'SUCCEEDED',dict(review=quality.model_dump(mode='json'),deterministic_validation=payload['deterministic_validation'])
            else: state,result = self.collaborative()
            with self.db.sessions.begin() as session:
                workflow = self.checked_workflow(session)
                workflow.state,workflow.result,workflow.progress = state,result,60 if state=='ESTIMATED_READY' and self.mode=='IMAGE_TO_CAD' else 100
                workflow.stage,workflow.error,workflow.updated_at,workflow.lease_token = state.lower(),None,utcnow(),None
                event(session,self.id,'workflow_completed',workflow.stage,state=state)
                audit(session,self.actor,'agent_workflow_completed',self.id,state=state)
            if state=='ESTIMATED_READY':
                from app.estimated_workflow import queue_estimated_cad
                queue_estimated_cad(self.id,self.db,self.config)
        except Exception as exc:
            from app.cad_meshing import CADMeshError
            error = exc if isinstance(exc,AIServiceError) else AIServiceError('CAD_PREVIEW_UNAVAILABLE',
                'The display mesh could not be completed. Available CAD plans and reference evidence were saved.',True) if isinstance(exc,CADMeshError) else AIServiceError('AGENT_WORKFLOW_FAILED','Workflow failed; inspect server logs with the workflow ID',True)
            logger.error('agent_workflow_failed',exc_info=not isinstance(exc,AIServiceError),extra={'workflow_id':self.id,'error_code':error.code,
                                                       'provider_error':error.provider})
            with self.db.sessions.begin() as session:
                workflow = session.get(AgentWorkflow,self.id,with_for_update=True)
                if workflow.lease_token != self.lease or workflow.state!='RUNNING': return
                from app.ai.furniture_budget import STOP_CODES
                workflow.state,workflow.lease_token,workflow.updated_at = ('BUDGET_REACHED' if error.code in STOP_CODES else 'FAILED'),None,utcnow()
                workflow.error = error.as_dict()
                saved_cad=any(key.startswith('engineering_exportable_draft_') for key in workflow.checkpoints)
                recoverable=error.code in STOP_CODES|{'CAD_BUILD_NEEDS_REVISION','CAD_PREVIEW_UNAVAILABLE'} or (error.code=='AGENT_WORKFLOW_FAILED' and saved_cad)
                if recoverable and self.mode in ('IMAGE_TO_CAD','PHOTO_ANALYZE') and not (workflow.result or {}).get('engineering_cad'):
                    from app.budget_drafts import reference_only_output
                    try:
                        workflow.result=reference_only_output(workflow,error)
                        workflow.error=None
                        workflow.stage='available_output_saved'
                        if error.code in ('CAD_BUILD_NEEDS_REVISION','CAD_PREVIEW_UNAVAILABLE','AGENT_WORKFLOW_FAILED'):workflow.state='REVIEW_REQUIRED'
                    except AIServiceError as integrity:
                        workflow.state='FAILED';workflow.error=integrity.as_dict()
                event(session,self.id,'available_output_saved' if workflow.error is None else 'workflow_failed',
                    workflow.stage,**(workflow.error or dict(code=error.code)))


def run_workflow(workflow_id,db,config): WorkflowRunner(workflow_id,db,config).run()


def dispatch_workflow(workflow_id,db,config):
    with db.sessions() as session:
        workflow=session.get(AgentWorkflow,workflow_id)
        rendering=workflow.mode=='BLENDER' or (workflow.mode in ('PIPELINE','MODIFY','IMAGE_TO_CAD') and bool(workflow.job_id))
        isolated=config.local_workflow_process and workflow.mode=='IMAGE_TO_CAD' and (workflow.input_data or {}).get('cad_source')=='ENGINEERING_CAD'
    if config.executor=='eager': run_workflow(workflow_id,db,config)
    elif config.executor=='local':
        from app.local_executor import submit_local, submit_render
        if isolated:
            from app.workflow_process import run_isolated_workflow
            (submit_render if rendering else submit_local)(config,run_isolated_workflow,workflow_id,config)
        else:
            (submit_render if rendering else submit_local)(config,run_workflow,workflow_id,db,config)
    else:
        from app.jobs import agent_workflow_task
        agent_workflow_task.apply_async(args=[workflow_id],queue='renders' if rendering else 'celery',retry=True,
            retry_policy={'max_retries':2,'interval_start':0,'interval_step':0.2,'interval_max':0.5})


def notify_cad_completed(job_id,db,config):
    workflow_id = None
    with db.sessions.begin() as session:
        job = session.get(Job,job_id)
        revision = session.get(Revision,job.revision_id)
        if not revision.source_workflow_id: return
        workflow = session.get(AgentWorkflow,revision.source_workflow_id,with_for_update=True)
        if not workflow or workflow.job_id!=job.id or workflow.revision_id!=revision.id or revision.active_job_id!=job.id: return
        if workflow.state=='CAD_QUEUED':
            workflow.state,workflow.attempts,workflow_id = 'QUEUED',0,workflow.id
            event(session,workflow.id,'cad_completed','post_cad_review',job_id=job.id)
    if workflow_id: dispatch_workflow(workflow_id,db,config)
