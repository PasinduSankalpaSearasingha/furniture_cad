"""Image, CAD and Blender stages share the existing checkpoint/audit engine."""
import json
from app.ai.image_generation import FurnitureImageGenerationService
from app.ai.images import store_image, image_json
from app.ai.openai_client import AIServiceError
from app.database import Project
from app.photo_schema import PhotoDesign
from app.pipeline import canonical_hash, safe_directory, file_hash
from app.studio_schema import VisualReview
from . import photo_agent


class FurnitureDesignAgent:
    def __init__(self,runner): self.runner=runner

    def generate_concept(self):
        r=self.runner;images=r.images()
        count=r.inputs.get('count',1)
        if count>r.config.max_concepts: raise AIServiceError('CONCEPT_LIMIT','Requested images exceed the configured per-job limit')
        service=FurnitureImageGenerationService(r.gateway,r.inputs.get('quality','low'),r.inputs.get('size','1024x1024'),
            r.inputs.get('output_format','png'),r.inputs.get('backend','responses'))
        result=[]
        for index in range(count):
            def generate():
                prompt=r.inputs.get('prompt') or r.inputs.get('notes','')
                op=r.inputs.get('operation','generate')
                if op in ('edit','material','marketing') and len(images)!=1:
                    raise AIServiceError('REFERENCE_REQUIRED','This image operation requires one selected reference')
                if op=='variation': pixels=service.generate_variations(prompt+f' Direction {index+1}.',images)
                elif op=='edit': pixels=service.edit_reference_image(images[0],prompt)
                elif op=='material': pixels=service.generate_material_variations(images[0],prompt)
                elif op=='marketing': pixels=service.generate_marketing_concept(images[0],prompt)
                else: pixels=service.generate_concept(prompt+f' Design direction {index+1}.',images)
                from io import BytesIO
                from PIL import Image,UnidentifiedImageError
                try:
                    with Image.open(BytesIO(pixels)) as raster:
                        expected={'png':'PNG','jpeg':'JPEG','webp':'WEBP'}[r.inputs.get('output_format','png')]
                        if raster.format!=expected:
                            raise AIServiceError('IMAGE_FORMAT_MISMATCH','Generated image format does not match the requested output format')
                except (UnidentifiedImageError,OSError) as exc:
                    raise AIServiceError('IMAGE_GENERATION_FAILED','The provider returned invalid raster image data') from exc
                original_directory=safe_directory(r.config.storage,f'concept_{r.id}_{index}_{r.lease}')
                original_directory.mkdir(exist_ok=False)
                original_name='concept.'+r.inputs.get('output_format','png')
                original_path=safe_directory(original_directory,original_name)
                with r.db.sessions.begin() as session:
                    image=store_image(session,r.config,r.project_id,r.actor,pixels,'AI_CONCEPT_IMAGE',dict(
                        workflow_id=r.id,source='OPENAI_IMAGE_GENERATION',model=r.gateway.model_for('image'),
                        reasoning_model=r.gateway.model_for('render'),operation=op,reference_image_ids=r.inputs.get('image_ids',[]),
                        prompt=prompt,dimension_evidence=False,authoritative_geometry=False,
                        original=dict(directory=original_directory.name,filename=original_name,
                            sha256=__import__('hashlib').sha256(pixels).hexdigest()),
                        original_download_url=f'/api/studio/workflows/{r.id}/concepts/{index}/original'))
                    original_path.write_bytes(pixels)
                    return image_json(image)
            result.append(r.checkpoint(f'concept_{index}',None,generate,15+index*15))
        return 'SUCCEEDED',dict(images=result,kind='AI_CONCEPT_IMAGE',approved_for_manufacturing=False)

    def execute_design_workflow(self):
        r=self.runner
        if r.revision_id and r.job_id: return self.finalize_design()
        if r.inputs.get('modeling_engine')=='blender' and r.inputs.get('reference_fit') is None:
            from .blender_modeling_agent import BlenderModelingAgent
            return BlenderModelingAgent(self).run()
        ids=list(r.inputs.get('image_ids',[]))
        if r.inputs.get('source_plan'):
            source=PhotoDesign.model_validate(r.inputs['source_plan'])
            if r.inputs.get('reference_fit') is not None:
                from .reference_fit_agent import fit_reference
                plan,report=fit_reference(r,source,r.inputs.get('source_photo_observations') or {},
                    self.project_name(),ids,r.inputs['reference_fit'])
                return self.freeze_plan(plan,{},ids,numeric_fit=report)
            if not r.inputs.get('prompt','').strip():
                changed={v['key']:v['value'] for v in [*r.inputs.get('known_values',[]),*r.inputs.get('parameter_changes',[])]}
                if set(changed)-{v.key for v in source.values}:
                    raise AIServiceError('PARAMETER_UNKNOWN','A changed parameter does not exist in the source design')
                raw=source.model_dump(mode='json')
                for value in raw['values']:
                    if value['key'] in changed: value['value']=changed[value['key']]
                pid=r.inputs.get('feature_component_id')
                if pid:
                    part=next((p for p in raw['parts'] if p['part_id']==pid),None)
                    if part is None: raise AIServiceError('COMPONENT_UNKNOWN','The feature component does not exist')
                    part['features']=[*part.get('features',[]),*r.inputs.get('features',[])]
                plan=PhotoDesign.model_validate(raw)
                return self.freeze_plan(plan,{},ids)
            observations=dict(previous_plan=source.model_dump(mode='json'),change_request=r.inputs['prompt'])
        else:
            if not ids:
                _,concepts=self.generate_concept()
                ids=[concepts['images'][0]['id']]
            images=r.images(ids)
            analysis=r.checkpoint('photo_analysis',photo_agent.PhotoAnalysis,
                lambda:photo_agent.analyze(r.gateway,images,r.inputs.get('notes',''),r.inputs.get('reference_views',[])),25)
            observations=analysis.model_dump(mode='json')
        images=r.images(ids)
        feedback=None
        for attempt in range(2):
            def create():
                executed=[]
                trials=0
                def cad_generation(plan):
                    nonlocal trials
                    try:
                        trials+=1
                        if trials>2: raise ValueError('CAD trial limit reached')
                        spec,_=photo_agent.specification(plan,r.inputs,self.project_name())
                        build=photo_agent.preflight(spec,{v['key'] for v in r.inputs.get('known_values',[])},check_estimates=False)
                        executed.append(canonical_hash(plan.model_dump(mode='json')))
                        return dict(valid=True,component_count=len(build.parts),plan_sha256=executed[-1],
                                    generated_envelope_mm=photo_agent.geometry_envelope(build),
                                    optional_overall_estimates='Reconciled to this CAD envelope before saving; supplied values remain fixed',
                                    source='Actual CadQuery/OpenCascade solids; exports follow in the CAD worker')
                    except Exception as exc:
                        return dict(valid=False,message=str(exc)[:500])
                instructions=photo_agent.DESIGN+''' You are the furniture design orchestrator. You MUST call
cad_generation with the complete candidate plan. This executes real CadQuery geometry validation.
Use its errors to correct the plan. Return exactly the plan which the CAD tool validated successfully.
For a change request preserve all unaffected components and every fixed measurement.
The optional features list supports controlled local-mm hole, groove, pocket, fillet, chamfer, dowel,
dado, rabbet, mortise, tenon and confirmat operations. Do not invent approved construction details.
AI concept images express design intent only; unseen geometry is an assumption, never measured evidence.'''
                plan=r.gateway.structured('design',instructions,dict(observations=observations,
                    notes=r.inputs.get('notes',''),prompt=r.inputs.get('prompt',''),known_values=r.inputs.get('known_values',[]),
                    correction=feedback),PhotoDesign,images,tools={'cad_generation':dict(description=
                    'Build and validate the exact declarative furniture assembly with CadQuery/OpenCascade',
                    schema=PhotoDesign,handler=cad_generation)},require_tool='cad_generation',
                    max_output_tokens=r.config.photo_max_output_tokens,timeout_seconds=r.config.photo_timeout_seconds,
                    reasoning_effort=r.config.reasoning_effort)
                if canonical_hash(plan.model_dump(mode='json')) not in executed:
                    raise AIServiceError('CAD_TOOL_PLAN_MISMATCH','The final design differs from the plan validated by the CAD tool')
                return plan
            try:
                plan=r.checkpoint(f'studio_plan_{attempt}',PhotoDesign,create,35+attempt*10)
                return self.freeze_plan(plan,observations,ids)
            except (AIServiceError,ValueError) as exc:
                if isinstance(exc,AIServiceError) and exc.code not in ('CAD_TOOL_PLAN_MISMATCH','AI_INVALID_STRUCTURED_OUTPUT','PHOTO_INPUT_CHANGED','PHOTO_INPUT_NOT_APPLIED'):
                    raise
                if attempt: raise
                feedback=dict(issue=str(exc)[:500],instruction='Correct the invalid plan and validate it with cad_generation.')

    def project_name(self):
        r=self.runner
        with r.db.sessions() as session: return session.get(Project,r.project_id).name

    def freeze_plan(self,plan,observations,ids,numeric_fit=None,fidelity_override=None):
        r=self.runner
        from .photo_fidelity import refine,POLICY_VERSION
        if fidelity_override is not None:
            fidelity=fidelity_override
        elif numeric_fit is not None:
            fidelity=dict(policy_version=POLICY_VERSION,status='LOCAL_FIT_REVIEW_REQUIRED',numeric_fit=numeric_fit,
                exact_reconstruction_verified=False,estimate_adjustments=photo_agent.estimate_adjustments(r.inputs.get('source_value_provenance') or {}))
        elif r.inputs.get('source_plan') and not r.inputs.get('prompt','').strip() and not r.inputs.get('refine_source_geometry'):
            fidelity=dict(policy_version=POLICY_VERSION,status='PARAMETER_EDIT_PRESERVED',
                source_revision_id=r.inputs.get('source_revision_id'),exact_reconstruction_verified=False,
                estimate_adjustments=photo_agent.estimate_adjustments(r.inputs.get('source_value_provenance') or {}))
        else:
            plan,fidelity=refine(r,plan,{**(r.inputs.get('source_photo_observations') or {}),**observations},self.project_name(),r.images(ids),reference_ids=ids)
        try: plan,spec,provenance,_,adjustments=photo_agent.resolve_estimated_geometry(plan,r.inputs,self.project_name())
        except Exception as exc:
            raise AIServiceError('PHOTO_GEOMETRY_NEEDS_REVISION','CAD validation failed: '+str(exc)[:350]) from exc
        photo_agent.retain_estimate_basis(provenance,fidelity.get('estimate_adjustments',{}))
        if adjustments: r.record('photo_estimates_reconciled',dict(adjustments=adjustments))
        r.checkpoint('photo_plan',PhotoDesign,lambda:plan,55)
        return 'ESTIMATED_READY',dict(**observations,reference_image_ids=ids,
            reference_fidelity=fidelity,
            specification=spec.model_dump(mode='json'),value_provenance=provenance,
            dimension_basis='DESIGN_REVIEW_ESTIMATE',exact_reconstruction_verified=False,approved_for_manufacturing=False)

    def render(self):
        from app.rendering.blender_service import render_geometry
        r=self.runner
        revision,job,source=r.artifacts(r.revision_id or r.inputs['revision_id'],r.job_id or r.inputs.get('source_job_id'))
        def generate():
            directory=safe_directory(r.config.storage,'blender_'+r.id+'_'+r.lease)
            def progress(stage,percent):
                r.update_progress(stage,percent)
            result=render_geometry(r.config,source,directory,r.inputs.get('render_options',{}),dict(
                revision_id=revision.id,job_id=job.id,specification_hash=revision.spec_hash),progress)
            images=[]
            with r.db.sessions.begin() as session:
                for name in result['files']:
                    if name.endswith('.png'):
                        record=store_image(session,r.config,r.project_id,r.actor,(directory/name).read_bytes(),'BLENDER_RENDER',dict(
                            workflow_id=r.id,source_revision_id=revision.id,source_job_id=job.id,
                            source_spec_hash=revision.spec_hash,source_glb_sha256=result['source_glb_sha256'],
                            view=name.removeprefix('render_').removesuffix('.png'),authoritative_geometry=False,
                            source_cad_is_authoritative=True,engine='CYCLES'))
                        images.append(image_json(record))
            return dict(**result,images=images,artifact_directory=directory.name,
                download_base=f'/api/studio/workflows/{r.id}/files')
        result=r.checkpoint('blender_render',None,generate,70)
        # Resuming a stage also checks that its artifacts have not changed.
        directory=safe_directory(r.config.storage,result['artifact_directory'])
        if file_hash(directory/'render_manifest.json')!=result['manifest_sha256']:
            raise AIServiceError('RENDER_INTEGRITY_FAILED','Render manifest changed')
        for name,digest in result['files'].items():
            if file_hash(safe_directory(directory,name))!=digest:
                raise AIServiceError('RENDER_INTEGRITY_FAILED','A render artifact changed')
        return result

    def inspect_generated_render(self,render):
        r=self.runner
        ids=(r.previous_result or {}).get('reference_image_ids') or r.inputs.get('image_ids',[])
        render_ids=[im['id'] for im in render['images']]
        hero=next((im['id'] for im in render['images'] if im['provenance']['view']=='hero'),render_ids[0])
        payload=dict(source_specification=(r.previous_result or {}).get('specification') or r.inputs.get('source_specification'),
            rendering=render['report'],value_provenance=(r.previous_result or {}).get('value_provenance') or r.inputs.get('source_value_provenance'),
            instructions='First images are references; LAST image is the actual Cycles geometry render. '
                'Separate geometry discrepancies from appearance and lighting. Hidden surfaces are unknown. '
                'Suggest only explicit bounded corrections; no manufacturing certification or measured inference.')
        review=r.checkpoint('visual_review',VisualReview,lambda:r.gateway.structured('review',
            'Review a furniture reconstruction. Never change user-supplied or verified dimensions. '
            'All corrections require explicit acceptance. Use unique C001,C002... identifiers. '
            'Match numeric corrections to existing named design parameters only.',payload,VisualReview,
            r.images([*ids,hero]),reasoning_effort=r.config.reasoning_effort,max_output_tokens=6000,timeout_seconds=300),90)
        return review.model_dump(mode='json')

    def finalize_design(self):
        r=self.runner
        revision,job,directory=r.artifacts(r.revision_id,r.job_id)
        modeled=(r.previous_result or {}).get('blender_modeling')
        if modeled:
            import copy
            from app.rendering.modeling import compare_export
            modeled=copy.deepcopy(modeled);artifact=modeled['artifact']
            native_directory=safe_directory(r.config.storage,artifact['directory'])
            for filename,digest in artifact['files'].items():
                if file_hash(safe_directory(native_directory,filename))!=digest:
                    raise AIServiceError('BLENDER_MODEL_INTEGRITY_FAILED','The reconstruction changed before CAD export verification')
            modeled['cad_export_agreement']=r.checkpoint('blender_model_export_agreement',None,
                lambda:compare_export(r.config,PhotoDesign.model_validate(revision.specification['photo_design']),r.inputs,
                    self.project_name(),directory,artifact),92)
        render=self.render() if r.inputs.get('auto_render',True) else None
        review=self.inspect_generated_render(render) if render and r.inputs.get('reference_fit') is None and r.inputs.get('modeling_engine')!='blender' else None
        return 'REVIEW_REQUIRED',{**(r.previous_result or {}),'blender':render,'visual_review':review,
            **({'blender_modeling':modeled} if modeled else {}),
            'cad':dict(revision_id=revision.id,job_id=job.id),
            'deterministic_validation':json.loads((directory/'validation_report.json').read_text(encoding='utf-8')),
            'refinement_iteration':r.inputs.get('refinement_iteration',0),'approved_for_manufacturing':False}
