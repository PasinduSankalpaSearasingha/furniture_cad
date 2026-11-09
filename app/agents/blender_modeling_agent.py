"""Sol-controlled native Blender modeling with immutable artifacts and measured edits."""
import base64
import copy
import json
import numpy as np
from app.ai.openai_client import AIServiceError,ToolResult
from app.photo_schema import PhotoDesign
from app.reference_fit_schema import FitView,ReferenceFitOptions
from app.pipeline import canonical_hash,file_hash,safe_directory
from app.ai.reference_fitting import prepare_target
from app.rendering import modeling
from . import photo_agent,photo_fidelity,reference_fit_agent


class BlenderModelingAgent:
    def __init__(self,studio):
        self.studio=studio;self.r=studio.runner;self.context=None;self.built={}
        self.references,self.fingerprints=reference_fit_agent.reference_inputs(self.r,
            ReferenceFitOptions(views=[FitView.model_validate(v) for v in self.r.inputs.get('modeling_views',[])]))

    def progress(self,stage):
        r=self.r;r.stage=stage
        with r.db.sessions.begin() as session:r.checked_workflow(session).stage=stage
        r.record('blender_modeling_progress',dict(operation=stage))

    def verify(self,artifact):
        directory=safe_directory(self.r.config.storage,artifact['directory'])
        for filename,digest in artifact['files'].items():
            path=safe_directory(directory,filename)
            if not path.is_file() or file_hash(path)!=digest:
                raise AIServiceError('BLENDER_MODEL_INTEGRITY_FAILED','A native model artifact changed')
        return directory

    def restore_context(self,artifact):
        directory=self.verify(artifact);context=[]
        for i,(view,pixels) in enumerate(self.references[:4]):
            data=artifact['report']['views'][i]
            restored=view.model_copy(update={'mask_png':base64.b64encode((directory/f'mask_{i}.png').read_bytes()).decode()})
            target=prepare_target(pixels,restored,160);target['usable']=data['mask_usable']
            target['method']=data['mask_method'];target['warnings']=data['warnings'];context.append((target,data['camera']))
        self.context=context

    def candidate(self,raw_plan,observations):
        r=self.r;raw_hash=canonical_hash(raw_plan.model_dump(mode='json'))
        self.progress('blender_validating_cad')
        if r.inputs.get('source_plan'):
            original={p['part_id']:p for p in r.inputs['source_plan']['parts']};current={p.part_id:p for p in raw_plan.parts}
            for view,_ in self.references:
                for mark in view.landmarks:
                    if mark.part_id not in current or mark.part_id not in original or current[mark.part_id].label!=original[mark.part_id]['label']:
                        raise ValueError('Preserve the identifiers and labels of components with supplied landmarks')
        try:plan,spec,provenance,build,adjustments=photo_agent.resolve_estimated_geometry(raw_plan,r.inputs,self.studio.project_name())
        except AIServiceError:raise
        except Exception as exc:raise ValueError('Independent CAD geometry validation failed: '+str(exc)[:500]) from exc
        identity=dict(version=modeling.VERSION,plan=plan.model_dump(mode='json'),references=self.fingerprints)
        digest=canonical_hash(identity)[:20]
        def execute():
            if self.context is None:
                self.progress('blender_fitting_camera');self.context=modeling.context_for(plan,build,self.references,observations,r.config)
            output=safe_directory(r.config.storage,f'blender_model_{r.id}_{digest}_{r.lease}')
            artifact=modeling.build_model(r.config,plan,build,output,self.context,self.progress)
            artifact.update(download_base=f'/api/v1/agents/workflows/{r.id}/model/{artifact["directory"]}',reference_fingerprints=self.fingerprints,
                estimate_adjustments=adjustments,raw_plan_sha256=raw_hash)
            return dict(plan=plan.model_dump(mode='json'),artifact=artifact)
        result=r.checkpoint('blender_model_build_'+digest,None,execute,43)
        artifact=result['artifact'];self.verify(artifact)
        selected=PhotoDesign.model_validate(result['plan'])
        if canonical_hash(selected.model_dump(mode='json'))!=artifact['plan_sha256'] or artifact['reference_fingerprints']!=self.fingerprints:
            raise AIServiceError('BLENDER_MODEL_INTEGRITY_FAILED','The model checkpoint belongs to different geometry or photographs')
        if self.context is None:self.restore_context(artifact)
        self.built[raw_hash]=(selected,artifact,build)
        return selected,artifact,build

    def evidence(self,artifact,payload):
        directory=self.verify(artifact);images=[]
        for filename in [f'view_{i}.png' for i in range(min(4,len(artifact['report']['views'])+1))]:
            images.append(dict(type='input_image',image_url='data:image/png;base64,'+base64.b64encode((directory/filename).read_bytes()).decode(),detail='high'))
        return ToolResult(payload,images)

    @staticmethod
    def measured(artifact):
        return [(v['metrics'],v['cad_photo_metrics']) for v in artifact['report']['views'] if v['metrics'] is not None]

    def accept(self,before,after,interior_only=False):
        a,b=self.measured(before),self.measured(after)
        if a:
            reference_fit_agent.require_improvement([v[0] for v in a],[v[0] for v in b],interior_only)
            reference_fit_agent.require_improvement([v[1] for v in a],[v[1] for v in b],True)
        # A correction may not trade a better picture for worse Blender/CAD agreement.
        previous=before['report']['cad_agreement']['sampled_max_surface_gap_mm']
        measured=after['report']['cad_agreement']['sampled_max_surface_gap_mm']
        if measured>max(self.r.config.blender_cad_tolerance_mm,previous)+.1:
            raise ValueError('The correction worsens the sampled Blender-to-CAD surface agreement')

    def run(self):
        r=self.r;source=PhotoDesign.model_validate(r.inputs['source_plan']) if r.inputs.get('source_plan') else None
        observations=r.inputs.get('source_photo_observations') or {}
        local=r.inputs.get('blender_local_only') or (source is not None and not r.inputs.get('prompt','').strip())
        if local:
            plan=photo_fidelity.parameter_edit(source,r.inputs)
            plan,artifact,_=self.candidate(plan,observations)
            return self.finish(plan,artifact,observations,[],None,local=True)
        if source is None:
            analysis=r.checkpoint('photo_analysis',photo_agent.PhotoAnalysis,
                lambda:photo_agent.analyze(r.gateway,r.images(),r.inputs.get('notes','')),25)
            observations=analysis.model_dump(mode='json')
        else:observations={**observations,'previous_plan':source.model_dump(mode='json'),'change_request':r.inputs.get('prompt','')}
        attempts=0
        def build_tool(candidate):
            nonlocal attempts
            attempts+=1
            if attempts>2:raise AIServiceError('AI_TOOL_LIMIT','The native Blender build trial budget was exhausted')
            try:
                selected,artifact,_=self.candidate(candidate,observations)
                return self.evidence(artifact,dict(valid=True,raw_plan_sha256=canonical_hash(candidate.model_dump(mode='json')),
                    resolved_plan_sha256=artifact['plan_sha256'],native_blender=artifact['report'],
                    instruction='The following images are actual Blender geometry renders. Return the exact argument plan you built; the server retains resolved overall estimates.'))
            except (AIServiceError,ValueError) as exc:
                if isinstance(exc,AIServiceError) and exc.code not in ('BLENDER_MODEL_FAILED','BLENDER_MODEL_INVALID','PHOTO_INPUT_CHANGED','PHOTO_INPUT_NOT_APPLIED'):raise
                rejected=dict(plan=candidate.model_dump(mode='json'),message=str(exc)[:600])
                r.checkpoint('blender_model_rejected_'+canonical_hash(rejected)[:20],None,lambda:rejected,43)
                self.progress('blender_model_plan')
                if attempts>=2 and not self.built:
                    raise AIServiceError('CAD_BUILD_NEEDS_REVISION',
                        'The proposed CAD geometry failed validation. The failed component feedback is saved for revision.',True) from exc
                return dict(valid=False,message=str(exc)[:600],instruction='Correct only the failed geometry and call blender_build again')
        def propose():
            nonlocal attempts
            for draft in range(2 if r.mode=='IMAGE_TO_CAD' else 1):
                attempts=0
                try:return generate_initial()
                except AIServiceError as exc:
                    if not self.built and exc.code in ('AI_TOOL_LIMIT','CAD_BUILD_NEEDS_REVISION'):
                        if r.mode=='IMAGE_TO_CAD' and draft==0:
                            r.record('blender_model_replanning',dict(saved_failure_feedback=True,maximum_initial_drafts=2));continue
                        raise AIServiceError('CAD_BUILD_NEEDS_REVISION',
                            'The proposed CAD geometry could not be built within the trial limit. Resume to revise it using the saved validation feedback.',True) from exc
                    if not self.built or exc.code not in ('AI_RESPONSE_INCOMPLETE','AI_INVALID_STRUCTURED_OUTPUT','AI_API_ERROR',
                        'AI_TIMEOUT','AI_CONNECTION_ERROR','AI_RATE_LIMIT','MODEL_UNAVAILABLE','AI_TOOL_LIMIT','CAD_BUILD_NEEDS_REVISION'):raise
                    selected,artifact,_=list(self.built.values())[-1]
                    r.record('blender_model_initial_response_incomplete',dict(error=exc.as_dict(),valid_model_retained=True))
                    return dict(plan=selected.model_dump(mode='json'),artifact=artifact,error=exc.as_dict())
        def generate_initial():
            with r.db.sessions() as session:
                failures=[]
                for key,value in r.checked_workflow(session).checkpoints.items():
                    if not key.startswith('blender_model_rejected_'):continue
                    if canonical_hash(value['payload'])!=value['sha256']:
                        raise AIServiceError('CHECKPOINT_INTEGRITY_FAILED','Rejected CAD feedback changed')
                    failures.append(value['payload'])
            plan=r.gateway.structured('design',photo_agent.DESIGN+''' Build the photographed furniture in native Blender.
You MUST use blender_build with a complete candidate plan and inspect its actual render evidence before returning.
The backend independently reconstructs CAD and measures agreement. No arbitrary Python or shell code is accepted.
Use continuous Bezier rails, variable profile_sweep sections for integrated tapering tips, rounded loft sections,
and a continuous surface_grid for visible seat sculpting when supported by the photograph. Preserve component
counts and distinctive curves; do not substitute generic furniture. Preserve every supplied dimension and
unaffected source feature. Keep machining features out of this visual reconstruction; joints remain unverified.
If failed_builds are provided, start from their latest plan and correct only failing components and any
directly related symmetric members. Preserve its other components, parameters, materials and distinctive
details. Use contact-trim errors to terminate supports at surfaces instead of leaving isolated fragments.
Return exactly the plan sent to a successful blender_build call. Never claim exact reconstruction.''',
                dict(observations=observations,known_values=r.inputs.get('known_values',[]),notes=r.inputs.get('notes',''),
                    prompt=r.inputs.get('prompt',''),failed_builds=failures[-2:]),PhotoDesign,r.images(),tools={'blender_build':dict(schema=PhotoDesign,
                    description='Create actual native Blender geometry and renders; validate independent CAD and return measured photo/3D differences',handler=build_tool)},
                require_tool='blender_build',max_output_tokens=r.config.photo_max_output_tokens,timeout_seconds=r.config.photo_timeout_seconds,
                reasoning_effort='medium')
            key=canonical_hash(plan.model_dump(mode='json'))
            if key not in self.built:raise AIServiceError('BLENDER_TOOL_PLAN_MISMATCH','The final plan differs from the native Blender geometry that was built')
            selected,artifact,_=self.built[key]
            return dict(plan=selected.model_dump(mode='json'),artifact=artifact)
        result=r.checkpoint('blender_model_plan',None,propose,45)
        plan=PhotoDesign.model_validate(result['plan']);artifact=result['artifact'];self.verify(artifact)
        if self.context is None:self.restore_context(artifact)
        if result.get('error'):return self.finish(plan,artifact,observations,[],None,error=result['error'])
        history=[];last=None;error=None
        for iteration in range(r.config.blender_modeling_passes+1):
            digest=artifact['plan_sha256'][:16]
            try:
                review=r.checkpoint(f'blender_model_review_{iteration}_{digest}',photo_fidelity.PhotoGeometryReview,
                    lambda:r.gateway.structured('review','''Compare reference furniture photos with the LAST images,
which are real native Blender renders. Separate camera/material effects from visible geometry. Identify component
IDs and actionable visible differences in silhouette, seat sculpting, rail shape, leg sweep and support layout.
Supplied dimensions are intentional overrides. Hidden geometry remains unknown. Never certify an exact copy.''',
                    dict(plan=plan.model_dump(mode='json'),known_values=r.inputs.get('known_values',[]),measurements=artifact['report'],
                        image_order='Original photos, then native Blender candidate views'),
                    photo_fidelity.PhotoGeometryReview,[*r.images(),*self.evidence(artifact,{}).images],
                    max_output_tokens=5000,reasoning_effort='medium',timeout_seconds=r.config.photo_timeout_seconds),48)
                last=review;history.append(dict(iteration=iteration,plan_sha256=digest,review=review.model_dump(mode='json'),artifact=artifact))
                if review.visible_geometry_matches or not review.differences or iteration==r.config.blender_modeling_passes:break
                candidates={};trials=0
                original_spec,_=photo_agent.specification(plan,r.inputs,self.studio.project_name())
                original_build=photo_agent.preflight(original_spec,{v['key'] for v in r.inputs.get('known_values',[])},check_estimates=False)
                def apply_tool(patch):
                    nonlocal trials
                    trials+=1
                    if trials>2:raise AIServiceError('AI_TOOL_LIMIT','The Blender correction trial budget was exhausted')
                    try:
                        expanded=photo_fidelity.expand_sparse_patch(plan,patch)
                        candidate=photo_fidelity.apply_geometry_patch(plan,expanded,review)
                        for view,_ in self.references:
                            for mark in view.landmarks:
                                old=next((p for p in plan.parts if p.part_id==mark.part_id),None)
                                new=next((p for p in candidate.parts if p.part_id==mark.part_id),None)
                                if not old or not new or old.label!=new.label:raise ValueError('A correction changed a supplied landmark identity')
                        selected,changed,candidate_build=self.candidate(candidate,observations)
                        if not photo_fidelity.geometry_changed(original_build,candidate_build,[p.part_id for p in expanded.updated_parts]):
                            raise ValueError('The requested edit did not change actual geometry')
                        interior=not expanded.added_parts and not expanded.removed_part_ids and all(
                            c.field in ('surface_grid','surface_dishes','dish_depth') for c in patch.changes)
                        self.accept(artifact,changed,interior)
                        candidates[canonical_hash(patch.model_dump(mode='json'))]=(selected,changed)
                        return self.evidence(changed,dict(valid=True,measurements=changed['report'],
                            interior_surface_accuracy_measured=False,previous_plan_retained_until_completion=True))
                    except (ValueError,AIServiceError) as exc:
                        if isinstance(exc,AIServiceError) and exc.code not in ('BLENDER_MODEL_FAILED','BLENDER_MODEL_INVALID','PHOTO_INPUT_CHANGED','PHOTO_INPUT_NOT_APPLIED'):raise
                        return dict(valid=False,message=str(exc)[:600])
                def correct():
                    patch=r.gateway.structured('design',photo_agent.DESIGN+''' Correct only the components flagged
by the visual review. Return a SparseGeometryPatch. Preserve every existing parameter value, material and
unaffected part. You MUST call blender_apply_changes with the patch; inspect the actual Blender renders and
measurement feedback. Return exactly a successful patch. Failed edits retain the previous valid model.''',
                        dict(plan=plan.model_dump(mode='json'),comparison=review.model_dump(mode='json'),known_values=r.inputs.get('known_values',[])),
                        photo_fidelity.SparseGeometryPatch,[*r.images(),*self.evidence(artifact,{}).images],
                        tools={'blender_apply_changes':dict(schema=photo_fidelity.SparseGeometryPatch,handler=apply_tool,
                            description='Apply bounded part edits, rebuild native Blender and CAD, and reject measured regressions')},
                        require_tool='blender_apply_changes',max_output_tokens=10000,reasoning_effort='medium',timeout_seconds=r.config.photo_timeout_seconds)
                    key=canonical_hash(patch.model_dump(mode='json'))
                    if key not in candidates:raise AIServiceError('BLENDER_CORRECTION_REJECTED','No successful measured Blender correction was returned')
                    selected,changed=candidates[key];return dict(plan=selected.model_dump(mode='json'),artifact=changed)
                corrected=r.checkpoint(f'blender_model_correction_{iteration}_{digest}',None,correct,50)
                plan=PhotoDesign.model_validate(corrected['plan']);artifact=corrected['artifact'];self.verify(artifact)
            except AIServiceError as exc:
                if exc.code in ('CHECKPOINT_INTEGRITY_FAILED','BLENDER_MODEL_INTEGRITY_FAILED','WORKFLOW_LEASE_LOST'):raise
                error=exc.as_dict();r.record('blender_model_refinement_incomplete',dict(error=error,valid_model_retained=True));break
        return self.finish(plan,artifact,observations,history,last,error=error)

    def finish(self,plan,artifact,observations,history,last,local=False,error=None):
        model=dict(status='LOCAL_REBUILD_REVIEW_REQUIRED' if local else 'RECONSTRUCTION_REVIEW_REQUIRED',
            ai_model=None if local else 'gpt-6.1-sol',ai_executed=not local,artifact=artifact,history=history,error=error,
            exact_reconstruction_verified=False,approved_for_manufacturing=False)
        fidelity=dict(policy_version=photo_fidelity.POLICY_VERSION,
            status='VISIBLE_MATCH_REVIEWED' if last and last.visible_geometry_matches else 'NEEDS_VISUAL_REVIEW',
            review=last.model_dump(mode='json') if last else None,estimate_adjustments=artifact.get('estimate_adjustments',{}),
            exact_reconstruction_verified=False)
        state,result=self.studio.freeze_plan(plan,observations,self.r.inputs['image_ids'],fidelity_override=fidelity)
        result['blender_modeling']=model
        return state,result
