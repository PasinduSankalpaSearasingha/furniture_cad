"""Pinned upstream agent stages adapted to strict declarative CadQuery solids."""
import base64
import copy
import json
from sqlalchemy import update
from app.ai.openai_client import AIServiceError
from app.database import Project,Revision,Job,audit,utcnow
from app.engineering_schema import EngineeringDimensions,EngineeringAssembly,EngineeringReview,EngineeringRecognition
from app.engineering_upstream import prompt,RULES,provenance
from app.engineering_cad import build_engineering_package,geometry_checks,check_supplied_dimensions,SOURCE
from app.photo_schema import PhotoDesign
from app.pipeline import safe_directory,canonical_hash,file_hash,verify_package
from . import photo_agent
from app.storage_completion import POLICY,MARKER,complete_storage_plan,coverage_360
from app.photo_matching import (POLICY as PHOTO_MATCH_POLICY,reference_camera,reference_render_size,
    checked_visual_review,photo_match)
from app.geometry_repairs import resolve_repaired_geometry
from app.ai.furniture_budget import STOP_CODES
from app.engineering_repairs import EngineeringRepairPatch,merge_repair
from app.furniture_references import MULTIVIEW
from app.budget_drafts import retain_exportable_draft,available_budget_draft,describe_draft


def execute(r):
    if r.inputs.get('engineering_integration')!={'version':'CADQUERY_SOLIDS_V1','upstream_commit':provenance()['commit']}:
        raise AIServiceError('ENGINEERING_VERSION_MISMATCH','The frozen engineering integration version is unavailable')
    if r.revision_id and r.job_id and (r.previous_result or {}).get('engineering_cad'):
        # A package-stage retry must not rebuild completed geometry or rerun AI.
        r.artifacts(r.revision_id,r.job_id)
        from app.delivery import build_delivery,verify_delivery
        result=dict(r.previous_result)
        delivery=r.checkpoint('delivery_package',None,lambda:build_delivery(r,result),97)
        verify_delivery(r.config,delivery);result['delivery']=delivery
        return 'REVIEW_REQUIRED',result
    images=r.images();known=r.inputs.get('known_values',[])
    whole=r.inputs.get('output_scope')=='WHOLE_FURNITURE'
    dimension_context=dict(dimension_match=r.inputs.get('dimension_match'),dimension_sources=r.inputs.get('dimension_sources',{}))
    if not images:raise AIServiceError('IMAGE_REFERENCE_INVALID','Upload a furniture reference first')
    with r.db.sessions() as session:name=session.get(Project,r.project_id).name
    edit=r.inputs.get('engineering_edit')
    source_build=None
    if edit:
        from app.engineering_edits import verify_edit_contract
        source_plan=PhotoDesign.model_validate(edit['source_plan']);verify_edit_contract(edit,source_plan)
        _,_,_,source_build,_=photo_agent.resolve_estimated_geometry(source_plan,r.inputs,name)
    edit_request={key:edit[key] for key in ('source_workflow_id','source_job_id','source_plan_sha256','part_ids','style','instructions')} if edit else None
    structural_budget=dict(active=False,used=0,maximum=0)
    combined=None
    with r.db.sessions() as session:
        saved_recognition=dict(r.checked_workflow(session).checkpoints)
    if 'engineering_combined_recognition' in saved_recognition and not any(k.startswith('engineering_rejected_') for k in saved_recognition):
        combined=r.checkpoint('engineering_combined_recognition',EngineeringRecognition,lambda:None,8)
    def call(role,instructions,payload,schema,pictures=images,**options):
        nonlocal combined
        if schema is EngineeringDimensions and not edit and not r.inputs.get('engineering_reexport_of'):
            combined=r.checkpoint('engineering_combined_recognition',EngineeringRecognition,lambda:r.gateway.structured(
                'design',instructions+RULES+' In the SAME response return EngineeringRecognition: dimensions AND a full '
                'PhotoDesign for the actual furniture. Decompose into consecutive F001 native components, '
                'bind dimensions and relative placements to named values, preserve visible curves with sweeps, lofts or NURBS. '
                'No generic substitute, fabricated hidden joints or manufacturing approval. Keep every supplied dimension. '
                'This combined recognition replaces separate dimension/decomposition/design requests. '
                'For one photographed furniture item, leave separate_items empty; only use separate_items '
                'for additional distinct furniture items, each with its own non-overall dimension keys. '
                'Every string geometry expression must reference only keys declared in the response values '
                'list; declare a key before using it and never invent names.',payload,EngineeringRecognition,
                images=pictures,max_output_tokens=r.config.photo_max_output_tokens,timeout_seconds=r.config.photo_timeout_seconds,
                reasoning_effort=r.config.photo_reasoning_effort),8)
            return combined.dimensions
        if combined and schema is EngineeringAssembly:
            return EngineeringAssembly(furniture_type=combined.design.furniture_type,assumptions=combined.design.assumptions,
                parts=[dict(part_id=p.part_id,label=p.label,description='Native component hypothesis',visible_evidence=p.evidence)
                    for p in combined.design.parts])
        if combined and schema is PhotoDesign and not payload.get('feedback'):
            candidate=combined.design
            # The same real local CAD validation runs without another paid request.
            result=options['tools']['cad_generation']['handler'](candidate)
            combined=None
            if not result.get('valid'):raise AIServiceError('AI_CAD_CANDIDATE_INVALID',str(result.get('message','Invalid candidate')),True)
            return candidate
        if schema is EngineeringReview and pictures is images and not payload.get('final_correction'):
            return EngineeringReview(accepted=True,findings=[],limitations=[
                'Native geometry and dimension checks only; photo review is performed separately within the two-request limit.'])
        if structural_budget['active']:
            if schema is EngineeringReview:
                return unreviewed_budget_draft('The optional structural repair used its configured additional AI call; review the changed CAD manually.')
            if structural_budget['used']>=structural_budget['maximum']:
                raise AIServiceError('AI_TOOL_LIMIT','The optional structural AI call budget is exhausted')
            structural_budget['used']+=1
            r.checkpoint('engineering_structural_call_0',None,lambda:dict(role=role,reserved=True),30)
            # A valid tool input returns immediately. Invalid inputs/results stop
            # without requesting another paid response in this optional fallback.
            options.update(repair_invalid_tool_inputs=False,return_validated_tool_input=True,
                stop_on_invalid_tool_result=True)
        if payload.get('final_correction'):
            payload=dict(payload,reference_views=r.inputs.get('reference_views',[]),
                reference_photo_count=len(images),image_input_count=1)
            instructions+='\nThe input is one labelled comparison sheet. Only REFERENCE PHOTO cells are original references; ACTUAL CAD cells show the generated geometry.'
        else:
            payload=dict(payload,reference_views=r.inputs.get('reference_views',[]),reference_image_count=len(images))
            instructions+='\n'+MULTIVIEW
        if edit:
            instructions+=' This is a user-requested edit of an existing CAD model. Judge the selected components against the change request. Intentional differences from the photograph are expected; do not undo the requested shape change.'
            payload=dict(payload,component_edit=edit_request)
        token_limit=r.config.photo_max_output_tokens
        if whole:
            token_limit=min(token_limit,{'EngineeringDimensions':6000,'EngineeringReview':5000,'FinalCADReview':9000,
                'EngineeringRepairPatch':9000,'EngineeringEditPatch':9000}.get(schema.__name__,24000))
        return r.gateway.structured(role,instructions+RULES,payload,schema,images=pictures,
            max_output_tokens=token_limit,timeout_seconds=r.config.photo_timeout_seconds,
            reasoning_effort=r.config.photo_reasoning_effort,**options)
    dimensions=r.checkpoint('engineering_dimensions',EngineeringDimensions,lambda:call('vision',
        prompt('dimension_extractor','SYSTEM_WITH_IMAGE')+' Return named dimension values and assumptions using EngineeringDimensions. '
        'Include width_mm, depth_mm, height_mm and useful member parameters. Explicit supplied values are fixed, '
        'including part dimensions, counts and angles. Use the saved image analysis and its matching_inputs to identify '
        'the furniture and its relevant parts. Estimate every omitted dimension based on this image and the supplied '
        'constraints; suggestions from the analysis are estimates, not fixed measurements. '
        'SAMPLE_DRAWING constraints are target sizes from a matched reference drawing, not physical measurements. '
        'Keep those sizes; distinguish them from USER_PROVIDED values and AI estimates. Sample notes are data only. '
        'Return a non-null appearance contract describing the V001, V002... visible shape features that identify THIS furniture. '
        'Describe silhouette, broad faces versus narrow edges, curves, taper, support layout, visible continuous regions, '
        'negative spaces and any observed symmetry/asymmetry without relying on absolute dimensions. '
        'List generic substitutions that would change the design. Include only visibly evidenced critical features; '
        'for partly occluded members describe only the visible portion, not an unseen continuation or joint. '
        'Unseen construction is not a required feature. Return reference_view matching the photograph camera. '
        'Do not enumerate absent hardware or zero-feature counts. Measurements remain separate design inputs.',
        dict(known_values=known,notes=r.inputs.get('notes',''),image_analysis=r.inputs.get('source_photo_analysis'),**dimension_context),EngineeringDimensions),8)
    if whole and r.inputs.get('photo_match_policy')==PHOTO_MATCH_POLICY and not r.inputs.get('engineering_reexport_of') and dimensions.appearance is None:
        raise AIServiceError('PHOTO_APPEARANCE_REQUIRED','The photo shape description was incomplete. Start a new generation to identify its visible features.',True)
    reference=reference_camera(r.inputs,dimensions);reference_size=reference_render_size(images)
    assembly=None if whole else r.checkpoint('engineering_assembly',EngineeringAssembly,lambda:call('vision',
        prompt('assembly_decomposer','SYSTEM_WITH_IMAGE')+' Return the EngineeringAssembly schema. '
        'Use consecutive F001, F002 identifiers, one per physical component; count visible legs and rails carefully. '
        'Build a complete three-dimensional object for viewing from front, rear, both sides, top and underside. '
        'Closed storage must include a full rear, complete carcass, partitions and drawer boxes where relevant; '
        'do not stop at a front facade and perimeter. Use evidence_basis ASSUMED_COMPLETION for unseen proposed parts. '
        'Preserve intentional open furniture and label all hidden construction as unverified. '
        'Describe member profiles, curves, proportions and placement. Hidden joints remain assumptions.',
        dict(dimensions=dimensions.model_dump(mode='json'),known_values=known,notes=r.inputs.get('notes',''),
            image_analysis=r.inputs.get('source_photo_analysis'),**dimension_context),
        EngineeringAssembly),18)
    final_plan=None;final_reviews=None;feedback=None;rejections=[];accepted_builds={};cost_stop=None;budget_draft=None
    with r.db.sessions() as session:saved_stages=dict(r.checked_workflow(session).checkpoints)
    final_correction_report=(r.checkpoint('engineering_final_correction',None,lambda:None,30)['report']
        if 'engineering_final_correction' in saved_stages else None)
    final_correction_enabled=bool(r.inputs.get('final_ai_correction') and r.config.cad_final_correction_enabled and r.gateway is not None)
    from app.cad_optimizer_schema import CADOptimizationOptions
    optimizer_options=CADOptimizationOptions.model_validate(r.inputs.get('optimization') or dict(
        mode=r.config.cad_optimizer_mode,max_seconds=r.config.cad_optimizer_max_seconds))
    if not r.config.cad_optimizer_enabled or edit:
        optimizer_options=optimizer_options.model_copy(update={'enabled':False})
    structural_budget.update(used=int('engineering_structural_call_0' in saved_stages),
        maximum=optimizer_options.max_structural_llm_calls)
    optimization_reports=[r.checkpoint(key,None,lambda:None,30)['report'] for key in saved_stages
        if key.startswith('engineering_optimization_')]
    if 'engineering_cost_stop' in saved_stages:
        cost_stop=r.checkpoint('engineering_cost_stop',None,lambda:None,30)
    if 'engineering_budget_draft' in saved_stages:
        budget_draft=r.checkpoint('engineering_budget_draft',None,lambda:None,30)
    saved_rejection=next((key for key in reversed(saved_stages) if key.startswith('engineering_rejected_')),None)
    frozen_plan=saved_stages.get('engineering_plan')
    if frozen_plan:
        final_plan=r.checkpoint('engineering_plan',PhotoDesign,lambda:None,70)
        selected=max(i for i in range(2) if f'engineering_visual_review_{i}' in saved_stages and f'engineering_critic_{i}' in saved_stages)
        final_reviews=dict(critic=r.checkpoint(f'engineering_critic_{selected}',EngineeringReview,lambda:None,70).model_dump(mode='json'),
            visual=r.checkpoint(f'engineering_visual_review_{selected}',EngineeringReview,lambda:None,70).model_dump(mode='json'))
    def resolve(candidate):
        digest=canonical_hash(candidate.model_dump(mode='json'))
        cached=accepted_builds.get(digest)
        if cached:return cached
        if edit:
            from app.engineering_edits import verify_edit_contract
            verify_edit_contract(edit,candidate)
        missing={field['key'] for field in r.inputs.get('dimension_fields',[])}-{value.key for value in candidate.values}
        if missing:raise ValueError('Include estimates for all identified dimension fields: '+', '.join(sorted(missing)))
        resolved,spec,values,build,adjustments,repairs=resolve_repaired_geometry(candidate,r.inputs,name)
        if r.inputs.get('reconstruction_policy')==POLICY:
            completed,completion=complete_storage_plan(resolved,build)
            if completion.get('added_parts') and canonical_hash(completed.model_dump(mode='json'))!=canonical_hash(resolved.model_dump(mode='json')):
                resolved,spec,values,build,adjustments=photo_agent.resolve_estimated_geometry(completed,r.inputs,name)
                build.warnings.extend(repair['description'] for repair in repairs)
        repair_key='engineering_geometry_repairs_'+canonical_hash(resolved.model_dump(mode='json'))[:20]
        if repairs or repair_key in saved_stages:
            repairs=r.checkpoint(repair_key,None,lambda:repairs,30)
        checks=geometry_checks(build);checks['supplied_dimensions']=check_supplied_dimensions(build,known)
        checks['geometry_repairs']=repairs
        checks['full_360']=coverage_360(resolved,build)
        if edit:
            from app.engineering_edits import verify_edit_solids
            verify_edit_contract(edit,resolved)
            checks['component_edit']=verify_edit_solids(edit,source_build,build)
            for key,source in edit.get('source_value_provenance',{}).items():
                if key in values and values[key]['value']==source['value']:values[key]=copy.deepcopy(source)
        for key,source in values.items():
            if key.startswith('completion_') and source['source']=='AI_ESTIMATED':
                source.update(source='ASSUMED_COMPLETION',basis='LOCAL_CONSTRUCTION_ESTIMATE')
        if r.inputs.get('reconstruction_policy')==POLICY and checks['full_360'].get('applicable') and not checks['full_360']['rear_closed']:
            raise ValueError('Closed storage has an incomplete rear surface. Build the complete cabinet, not only its front facade.')
        cached=(resolved,spec,values,build,adjustments,checks)
        accepted_builds[digest]=cached;accepted_builds[canonical_hash(resolved.model_dump(mode='json'))]=cached
        retain_exportable_draft(r,resolved,checks)
        return cached
    def unreviewed_budget_draft(message):
        return EngineeringReview(accepted=False,findings=[message],
            limitations=['AI visual review was not completed. Inspect this CAD draft against the reference.'],
            feature_checks=[dict(feature_id=f.feature_id,status='UNVERIFIABLE',component_ids=[],
                evidence='Visual review was not performed because further AI corrections stopped.',
                correction='Review this visible feature manually.')
                for f in dimensions.appearance.critical_features] if dimensions.appearance else [])
    def retain_available_output(exc,attempt):
        nonlocal final_plan,final_reviews,cost_stop,budget_draft
        available=available_budget_draft(r)
        if available is None:return False
        final_plan,previous=available
        _,_,_,_,_,draft_checks=resolve(final_plan)
        cost_stop=r.checkpoint('engineering_cost_stop',None,
            lambda:dict(code=exc.code,message=exc.message),30)
        budget_draft=describe_draft(final_plan,draft_checks,previous['source_checkpoint'],cost_stop)
        critic=r.checkpoint(f'engineering_critic_{attempt}',EngineeringReview,lambda:EngineeringReview(
            accepted=budget_draft['fully_connected'],
            findings=[issue['message'] for issue in budget_draft['remaining_issues']],
            limitations=['Local solid and dimension checks only; unresolved assembly issues remain in this draft.']),70)
        visual=r.checkpoint(f'engineering_visual_review_{attempt}',EngineeringReview,
            lambda:unreviewed_budget_draft(exc.message),70)
        final_reviews=dict(critic=critic.model_dump(mode='json'),visual=visual.model_dump(mode='json'))
        r.record('ai_budget_draft_retained',cost_stop)
        return True
    # Resume the latest accepted tool plan, including a second-attempt plan.
    # A later preview failure must not restart earlier paid geometry repairs.
    resume_attempt=max((i for i in range(2) if f'engineering_candidate_{i}' in saved_stages or
        f'engineering_accepted_{i}' in saved_stages),default=0)
    for attempt in (() if frozen_plan else range(resume_attempt,2)):
        def propose():
            with r.db.sessions() as session:accepted=r.checked_workflow(session).checkpoints.get(f'engineering_accepted_{attempt}')
            if accepted:return r.checkpoint(f'engineering_accepted_{attempt}',PhotoDesign,lambda:None,30+attempt*20)
            executed=[];trial_count=0
            repair_source=final_plan if whole and attempt else None
            def build_tool(candidate):
                nonlocal trial_count
                trial_count+=1
                if trial_count>2:raise AIServiceError('AI_TOOL_LIMIT','CAD candidate trial budget exhausted')
                try:
                    contract=[p.part_id for p in assembly.parts] if assembly else []
                    if assembly and ([p.part_id for p in candidate.parts if p.part_id in contract]!=contract or any(
                            not p.evidence.startswith(MARKER) for p in candidate.parts if p.part_id not in contract)):
                        raise ValueError('Part identifiers/count must match the frozen assembly decomposition')
                    resolved,spec,values,build,adjustments,checks=resolve(candidate)
                    if len(build.parts)>1 and not checks.get('furniture_items_connected',checks['assembly_connected']):
                        raise ValueError('Disconnected assembly groups: '+str(checks['contact_groups'])+'. Bring intended contacts together without losing separate solids.')
                    digest=canonical_hash(candidate.model_dump(mode='json'));executed.append(digest)
                    r.checkpoint(f'engineering_accepted_{attempt}',PhotoDesign,lambda:candidate,30+attempt*20)
                    return dict(valid=True,plan_sha256=digest,solid_count=len(build.parts),checks=checks,
                        optional_estimates=adjustments,source='Actual closed CadQuery/OpenCascade solids')
                except (ValueError,AIServiceError) as exc:
                    rejected=dict(plan=candidate.model_dump(mode='json'),message=str(exc)[:1300]);rejections.append(rejected)
                    r.checkpoint('engineering_rejected_'+canonical_hash(rejected)[:20],None,lambda:rejected,30+attempt*20)
                    if trial_count>=2:raise AIServiceError('AI_TOOL_LIMIT','CAD candidate trial budget exhausted: '+str(exc)[:1300],True) from exc
                    return dict(valid=False,message=str(exc)[:1300],instruction='Correct the geometry and call cad_generation again; preserve all supplied dimensions.')
            # Retry the latest saved candidate locally after a validator/kernel
            # fix before paying for another plan. Checkpoint hashes, all solid
            # checks and the normal visual review still apply.
            if attempt==0 and saved_rejection and not edit:
                previous=r.checkpoint(saved_rejection,None,lambda:None,30)
                recovered=PhotoDesign.model_validate(previous['plan'])
                checked=build_tool(recovered)
                if checked['valid']:
                    r.record('saved_geometry_revalidated',dict(source_checkpoint=saved_rejection,
                        plan_sha256=checked['plan_sha256'],additional_planning_requests=0))
                    return recovered
                trial_count=0
                if whole:repair_source=recovered
            instructions=photo_agent.DESIGN+'\n'+prompt('planner')+'\n'+prompt('coder').split('CADQUERY PATTERNS:')[0]+'''
Return PhotoDesign, not Python code. Existing registered cad_generation replaces upstream execute_cadquery.
You MUST execute cad_generation with the complete candidate. Return EXACTLY the input plan it accepted.
Reuse named parameters in all dimensions and positions; include every supplied parameter unchanged.
Retain all matching_inputs identified in the saved image analysis as named values; estimate any left blank.
The assembly decomposition is a fixed part contract. Do not fuse parts, omit parts or simplify curves.
Model the object completely for 360 degree inspection, including its rear, sides and underside.
For cabinets, include back panels and plausible carcass/interior construction as explicitly unverified assumptions.
The deterministic completion stage may add ASSUMED_360_COMPLETION parts; retain them if present in repair feedback.
For chair legs/back supports prefer loft sections for taper/curvature; a curved rail needs a smooth sweep.
Keep a single valid solid per part after contact trimming. Missing measurements remain estimates.'''
            if whole:
                instructions=photo_agent.DESIGN+'''
Create one whole-furniture design for an assembled STEP model and one general arrangement sheet.
Declare genuinely detached companion furniture (such as an ottoman beside a sofa) in separate_items,
with its own part_ids and named width/depth/height parameters. The unassigned primary furniture uses
width_mm, depth_mm and height_mm; these dimensions exclude a detached companion and its placement gap.
Every item must be internally connected, but separate furniture items must not be forced into contact.
Use sweep_frame=WORLD_UP for oval/rectangular rails and cushion sweeps so section width stays horizontal
and section depth stays vertical. Use loft_mode=BOUNDED and provide sections at visible extrema;
the constructor limits small interpolation overshoot to those section bounds instead of changing dimensions.
Use panel_frame=BOUNDS for panel surface_grid or dish_depth: shaping covers the actual local panel bounds,
including outlines whose coordinates are offset from the local origin. Keep their contour and placement.
Use named closed solids only as needed to preserve the visible shape, curves, counts and materials.
Choose the geometry directly; there is no separate component-decomposition contract.
Do not invent detailed manufacturing breakdowns, fasteners, joints, machining allowances or cut lists.
Retain the exterior on all sides, including a rear closure for closed storage. Hidden details are assumptions.
Preserve supplied dimensions and all identified dimension fields; estimate missing measurements explicitly.
You MUST call cad_generation with the complete PhotoDesign and return exactly the accepted tool input.
The local tool checks solid validity, contacts, collisions and supplied dimensions.
The appearance contract is the shape target: reproduce those visible features before adding construction details.
Use shared parameters and mirrored/repeated geometry ONLY where the photo shows matching members.
Keep asymmetry where it is visible. Preserve broad blade/panel faces and their orientation; do not replace them
with edge-on strips, rods or a generic frame. Preserve continuous sculptural junctions using coherent
outlines/loft profiles; arbitrary rails and abrupt contacts are not acceptable substitutes.
Model visual regions rather than inventing a manufacturing breakdown. Use the original photo to choose
section axes, curve control points, support splay and transitions. Correct overall dimensions do not
establish that the furniture's shape matches. Preserve user dimensions without redesigning its appearance.
Do not force a continuous sculptural region into separate physical parts. Choose coherent closed geometry
that preserves its flowing visible surface; internal component boundaries are not a photo-match requirement.
'''
            if attempt:instructions+='\n'+prompt('repair_specialist')+' Return only the corrected declarative PhotoDesign; no Python.'
            if edit:
                from app.engineering_edits import EngineeringEditPatch,merge_edit,STYLES
                def edit_tool(patch):
                    try:return build_tool(merge_edit(edit,patch))
                    except ValueError as exc:return dict(valid=False,message=str(exc),instruction='Correct the selected component patch and call cad_generation again')
                patch=call('design','''Edit only the selected physical components of the frozen source CAD plan.
Return EngineeringEditPatch. Include each selected part exactly once with its original part_id.
Keep all existing parameter values, other components and overall dimensions. Use new parameters or
bounded arithmetic for a changed profile. Keep attachment surfaces and make one valid solid per part.
For round legs use cylinder/ellipse sections; for tapered or curved legs use lofts or swept profiles.
Section centers are local coordinates; preserve the part placement and intended assembly contacts.
Values contains only new profile parameters, or unchanged existing values. Do not add/remove parts.
You MUST call cad_generation with the patch and return exactly the patch it accepts. The actual
CAD tool checks the requested change, unchanged solids, contacts, collisions and overall envelope.
The photograph is reference context; the requested change supersedes its selected component shapes.
'''+STYLES[edit['style']],dict(source_plan=edit['source_plan'],selected_part_ids=edit['part_ids'],
                    change_request=edit['instructions'],feedback=feedback),EngineeringEditPatch,
                    tools={'cad_generation':dict(description='Apply a selected-component patch and validate the entire CAD assembly',
                        schema=EngineeringEditPatch,handler=edit_tool)},require_tool='cad_generation',repair_invalid_tool_inputs=True,return_validated_tool_input=True)
                plan=merge_edit(edit,patch)
            elif whole:
                # Ask for a complete declaration once. Subsequent tool failures
                # and visual corrections return only replacements/parameters.
                # A saved candidate is also repaired without another full plan.
                if repair_source is None and rejections:
                    repair_source=PhotoDesign.model_validate(rejections[-1]['plan'])
                while trial_count<2:
                    try:
                        if repair_source is None:
                            plan=call('design',instructions,dict(dimensions=dimensions.model_dump(mode='json'),
                                known_values=known,notes=r.inputs.get('notes',''),image_analysis=r.inputs.get('source_photo_analysis'),
                                appearance=dimensions.appearance.model_dump(mode='json') if dimensions.appearance else None,
                                **dimension_context),PhotoDesign,tools={'cad_generation':dict(description='Build and validate closed furniture solids, dimensions and contacts',
                                    schema=PhotoDesign,handler=build_tool)},require_tool='cad_generation',
                                repair_invalid_tool_inputs=True,return_validated_tool_input=True,stop_on_invalid_tool_result=True,
                                compact_tool_output=bool(r.inputs.get('ai_budget')))
                        else:
                            source=repair_source.model_dump(mode='json')
                            def repair_tool(patch):
                                nonlocal trial_count
                                try:candidate=merge_repair(source,patch,known)
                                except ValueError as exc:
                                    trial_count+=1
                                    return dict(valid=False,message=str(exc))
                                return build_tool(candidate)
                            r.update_progress(f'engineering_repair_{attempt}_{trial_count}',30+attempt*20)
                            patch=call('design','''
Repair this photographed whole-furniture design using a small declarative correction.
Return EngineeringRepairPatch, containing ONLY changed parts and changed/new estimated values.
All omitted components and values are retained locally. Keep all supplied dimensions unchanged.
Use the current part IDs. A new part must continue consecutive Fxxx IDs; removal cannot leave gaps.
Keep separate_items null to retain furniture membership; change it only to correct explicit item scope.
Do not reproduce the entire source plan, repeat unchanged parameters, or add manufacturing detail.
Fix the specific validation/visible-feature findings while retaining the photographed appearance.
Keep coherent smooth outlines/lofts, broad faces, curves and the observed member layout.
Use WORLD_UP sweep frames, BOUNDED lofts and BOUNDS panel surface frames for new geometry.
For loft rounding prefer rounded sections; do not apply a blanket all-edge fillet.
You MUST call cad_generation with the patch. Its handler merges and validates the entire actual CAD model.
''',dict(source_plan=source,known_values=known,geometry_error=rejections[-1]['message'] if rejections else None,
                                visual_findings=final_reviews,appearance=dimensions.appearance.model_dump(mode='json') if dimensions.appearance else None),
                                EngineeringRepairPatch,tools={'cad_generation':dict(description='Merge a small correction into saved furniture and validate the complete CAD assembly',
                                    schema=EngineeringRepairPatch,handler=repair_tool)},require_tool='cad_generation',
                                repair_invalid_tool_inputs=True,return_validated_tool_input=True,stop_on_invalid_tool_result=True,
                                compact_tool_output=bool(r.inputs.get('ai_budget')))
                            plan=merge_repair(source,patch,known)
                            r.record('cad_patch_applied',dict(replaced_part_ids=[p.part_id for p in patch.parts],
                                changed_value_keys=[v.key for v in patch.values],source_plan_sha256=canonical_hash(source)))
                        break
                    except AIServiceError as exc:
                        if exc.code!='AI_CAD_CANDIDATE_INVALID' or trial_count>=2:raise
                        if rejections:repair_source=PhotoDesign.model_validate(rejections[-1]['plan'])
                        else:raise
                else:raise AIServiceError('AI_TOOL_LIMIT','CAD candidate trial budget exhausted',True)
            else:
                plan=call('design',instructions,dict(dimensions=dimensions.model_dump(mode='json'),
                    assembly=assembly.model_dump(mode='json') if assembly else None,known_values=known,notes=r.inputs.get('notes',''),
                    image_analysis=r.inputs.get('source_photo_analysis'),appearance=dimensions.appearance.model_dump(mode='json') if dimensions.appearance else None,
                    feedback=feedback,**dimension_context),
                    PhotoDesign,tools={'cad_generation':dict(description='Build and validate separate closed CAD solids, dimensions and assembly contacts',
                        schema=PhotoDesign,handler=build_tool)},require_tool='cad_generation',repair_invalid_tool_inputs=True,return_validated_tool_input=True)
            if canonical_hash(plan.model_dump(mode='json')) not in executed:
                raise AIServiceError('CAD_TOOL_PLAN_MISMATCH','The returned plan was not validated by the CAD tool',True)
            return plan
        try:plan=r.checkpoint(f'engineering_candidate_{attempt}',PhotoDesign,propose,30+attempt*20)
        except AIServiceError as exc:
            if exc.code in STOP_CODES:
                if not retain_available_output(exc,attempt):raise
                break
            if exc.code not in ('AI_CAD_CANDIDATE_INVALID','AI_TOOL_INPUT_INVALID','AI_TOOL_LIMIT','AI_INVALID_STRUCTURED_OUTPUT','CAD_TOOL_PLAN_MISMATCH'):raise
            feedback=dict(error=exc.code,rejected_candidates=rejections[-2:],instruction='Use the repair specialist to correct the failed component plan.')
            if attempt:
                detail=' Last geometry error: '+rejections[-1]['message'] if rejections else ''
                stop=AIServiceError('CAD_REPAIR_LIMIT_REACHED',
                    'Automatic repair attempts stopped. Available CAD was retained for review.')
                if retain_available_output(stop,attempt):break
                if final_plan is not None:
                    r.record('visual_repair_geometry_rejected',dict(error=exc.code,message=detail,
                        retained_plan_sha256=canonical_hash(final_plan.model_dump(mode='json')),
                        status='RETAINED_VALIDATED_DRAFT'))
                    break
                raise AIServiceError('CAD_BUILD_NEEDS_REVISION',
                    'No candidate passed solid/parameter validation within the bounded repair budget.'+detail,True) from exc
            continue
        resolved,spec,values,build,adjustments,checks=resolve(plan)
        if not attempt or f'engineering_optimization_{attempt}' in saved_stages:
            # Numerical fitting precedes review, so previews and exports describe
            # the selected geometry. Frozen/re-exported plans never rerun fitting.
            def optimize_locally():
                from app.cad_optimizer import optimize_cad_isolated
                selected=optimize_cad_isolated(resolved,optimizer_options,inputs=r.inputs,name=name)
                try:
                    checked=resolve(selected.plan)
                except (ValueError,AIServiceError):
                    checked=resolve(resolved)
                    selected.report=dict(selected.report,status='rejected',
                        reason='Final engineering validation rejected optimized CAD')
                final=checked[0].model_dump(mode='json')
                selected.report.update(export_plan_sha256=canonical_hash(final),
                    geometry_valid=True,fixed_dimensions_preserved=True,
                    final_engineering_validation=checked[-1])
                return dict(plan=final,report=selected.report)
            optimized=r.checkpoint(f'engineering_optimization_{attempt}',None,optimize_locally,35)
            if not any(item==optimized['report'] for item in optimization_reports):
                optimization_reports.append(optimized['report'])
            candidate=PhotoDesign.model_validate(optimized['plan'])
            try:
                resolved,spec,values,build,adjustments,checks=resolve(candidate)
            except (ValueError,AIServiceError):
                optimized['report']=dict(optimized['report'],status='rejected',reason='Final engineering validation rejected optimized CAD')
                optimization_reports[-1]=optimized['report']
                resolved,spec,values,build,adjustments,checks=resolve(plan)
        if final_correction_enabled:
            from .final_correction import execute as correct_final
            selected_plan,visual,final_correction_report=correct_final(r,resolved,build,checks,dimensions,
                images,reference,reference_size,resolve,call)
            resolved,spec,values,build,adjustments,checks=resolve(selected_plan)
            if final_correction_report.get('error_code') in STOP_CODES|{'CAD_PREVIEW_UNAVAILABLE'}:
                cost_stop=r.checkpoint('engineering_cost_stop',None,lambda:dict(
                    code=final_correction_report['error_code'],message='The comparison preview was unavailable. Available CAD was saved.' if final_correction_report['error_code']=='CAD_PREVIEW_UNAVAILABLE' else 'Final AI review stopped at a credit or spending limit. Available CAD was saved.'),65)
            critic=r.checkpoint(f'engineering_critic_{attempt}',EngineeringReview,lambda:EngineeringReview(
                accepted=True,findings=[],limitations=['Local solid, contact and dimension validation; final AI review is combined with one correction response.']),67)
            visual=r.checkpoint(f'engineering_visual_review_{attempt}',EngineeringReview,lambda:visual,68)
            final_plan=resolved;final_reviews=dict(critic=critic.model_dump(mode='json'),visual=visual.model_dump(mode='json'))
            break
        def review_contract():
            nonlocal cost_stop
            if whole:return EngineeringReview(accepted=True,findings=[],
                limitations=['Local geometry and dimension checks only; no separate AI code-critic call.'])
            try:return call('review',prompt('code_critic')+' Review the declarative geometry contract rather than Python source. '
                'Compare actual parameters/parts/features with the decomposition and user values. Return EngineeringReview.',
                dict(plan=resolved.model_dump(mode='json'),assembly=assembly.model_dump(mode='json'),known_values=known,
                    solid_checks=checks),EngineeringReview)
            except AIServiceError as exc:
                if exc.code not in STOP_CODES:raise
                cost_stop=dict(code=exc.code,message=exc.message)
                cost_stop=r.checkpoint('engineering_cost_stop',None,lambda:cost_stop,30)
                r.record('ai_budget_draft_retained',cost_stop)
                return EngineeringReview(accepted=False,findings=[exc.message],
                    limitations=['AI contract review stopped at the spending check; local geometry checks passed.'])
        critic=r.checkpoint(f'engineering_critic_{attempt}',EngineeringReview,review_contract,38+attempt*20)
        def preview():
            from app.engineering_cad import render_preview
            from app.ai.geometry_preview import preview_meshes
            directory=safe_directory(r.config.storage,f'engineering_preview_{r.id}_{attempt}_{r.lease}');directory.mkdir(exist_ok=False)
            files={};meshes=preview_meshes(build)
            for view,az,el in [('reference',reference['azimuth_deg'],reference['elevation_deg']),
                    ('front',-90,0),('rear',90,0),('left',180,0),('right',0,0),('top',-90,89),('bottom',-90,-89)]:
                filename=view+'.png';path=directory/filename
                path.write_bytes(render_preview(build,az,el,size=reference_size if view=='reference' else (620,700),meshes=meshes))
                files[filename]=file_hash(path)
            return dict(directory=directory.name,files=files,view_order=list(files),
                plan_sha256=canonical_hash(resolved.model_dump(mode='json')),reference_view=reference,reference_size=list(reference_size))
        views=r.checkpoint(f'engineering_preview_{attempt}',None,preview,42+attempt*20)
        directory=safe_directory(r.config.storage,views['directory']);pictures=list(images)
        if views['plan_sha256']!=canonical_hash(resolved.model_dump(mode='json')):raise ValueError('Preview plan changed')
        view_order=views.get('view_order',list(views['files']))
        if len(view_order)!=7 or set(view_order)!=set(views['files']):raise ValueError('CAD review view inventory changed')
        for filename in view_order:
            digest=views['files'][filename]
            path=safe_directory(directory,filename)
            if file_hash(path)!=digest:raise ValueError('Engineering preview changed')
            pictures.append(dict(type='input_image',image_url='data:image/png;base64,'+base64.b64encode(path.read_bytes()).decode(),detail='high'))
        if len(pictures)!=len(images)+7:raise ValueError('All seven actual CAD views are required for visual review')
        def review_visible_shape():
            review=call('review',
            prompt('reviewer')+' Return EngineeringReview. First images are references; last seven show actual CAD from all angles. '
            'The first CAD image approximates the uploaded photo camera; the other six expose construction. '
            'Check rear, both sides and underside completeness as well as the front. Reject a cabinet facade with an unintended open back. '
            'Evaluate silhouette, negative spaces, support placement/splay, section shape, broad face orientation and continuous curves. '
            'A similar furniture category or correct overall envelope is insufficient. Distinguish camera effects from actual geometry errors. '
            'Return one feature_check for EVERY critical Vxxx feature, with existing affected component IDs and actionable shape corrections. '
            'Reject any visible MISMATCH or UNVERIFIABLE critical feature. Do not accept thin strips in place of broad supports or abrupt '
            'junctions in place of continuous sculptural regions. Local solid_checks are supplied separately; photo identity is your task. '
            'For partly occluded members, evaluate their visible projection only. Record unknown hidden continuations and joints as '
            'limitations; hidden uncertainty alone is not a failed visible feature. Do not require separate solids for a continuous visible region. '
            'Inferred hidden construction remains unverified. Acceptance is a visual opinion, never exact reconstruction or physical verification.',
            dict(plan=resolved.model_dump(mode='json'),known_values=known,critic=critic.model_dump(mode='json'),solid_checks=checks,
                appearance=dimensions.appearance.model_dump(mode='json') if dimensions.appearance else None,
                reference_view=reference,view_inventory=view_order),EngineeringReview,pictures)
            return checked_visual_review(review,dimensions.appearance,{part.part_id for part in resolved.parts})
        def review_or_retain_draft():
            nonlocal cost_stop
            if cost_stop:return unreviewed_budget_draft(cost_stop['message'])
            try:return review_visible_shape()
            except AIServiceError as exc:
                if exc.code not in STOP_CODES:raise
                cost_stop=dict(code=exc.code,message=exc.message)
                cost_stop=r.checkpoint('engineering_cost_stop',None,lambda:cost_stop,30)
                r.record('ai_budget_draft_retained',cost_stop)
                return unreviewed_budget_draft(exc.message)
        visual=r.checkpoint(f'engineering_visual_review_{attempt}',EngineeringReview,review_or_retain_draft,46+attempt*20)
        final_plan=resolved;final_reviews=dict(critic=critic.model_dump(mode='json'),visual=visual.model_dump(mode='json'))
        if cost_stop:break
        if critic.accepted and visual.accepted:break
        if optimizer_options.enabled and optimizer_options.views:
            # A numerical job grants no implicit paid repair request. Preserve
            # the legacy bounded repair when no quantitative fitting is requested.
            structural=any(word in ' '.join(visual.findings+critic.findings).lower()
                for word in ('missing','extra component','wrong number','topology','relationship','construction method'))
            if not optimizer_options.max_structural_llm_calls or not structural:break
            structural_budget['active']=True
        feedback=dict(previous_plan=resolved.model_dump(mode='json'),**final_reviews)
    plan=r.checkpoint('engineering_plan',PhotoDesign,lambda:final_plan,70)
    resolved,spec,values,build,adjustments,checks=resolve(plan)
    if cost_stop:
        budget_draft=r.checkpoint('engineering_budget_draft',None,
            lambda:budget_draft or describe_draft(resolved,checks,'engineering_plan',cost_stop),70)
    if whole:
        # Preserve internal identifiers for edits and saved-plan re-exports without
        # paying for a second description of the already accepted model.
        assembly=r.checkpoint('engineering_assembly',EngineeringAssembly,lambda:EngineeringAssembly(
            furniture_type=resolved.furniture_type,assumptions=resolved.assumptions,
            parts=[dict(part_id=p.part_id,label=p.label,description='Internal solid of the whole-furniture model',
                visible_evidence=p.evidence,evidence_basis='ASSUMED_COMPLETION' if p.evidence.startswith(MARKER) else 'IMAGE_OBSERVED')
                for p in resolved.parts]),70)
    evidence=dict(upstream={k:v for k,v in provenance().items() if k!='files'},
        output_scope='WHOLE_FURNITURE' if whole else 'PARTS',
        review_policy='LOCAL_GEOMETRY_AND_AI_VISUAL' if whole else 'AI_CRITIC_AND_VISUAL',
        reexport_of=r.inputs.get('engineering_reexport_of'),
        completion_of=r.inputs.get('engineering_completion_of'),full_360=checks.get('full_360'),
        component_edit=edit_request,
        manual_reconstruction=r.inputs.get('engineering_manual_reconstruction'),
        furniture_items=getattr(build,'furniture_items',[]),
        geometry_repairs=checks.get('geometry_repairs',[]),
        cad_optimization=optimization_reports,
        final_ai_correction=final_correction_report,
        ai_structural_repair=dict(additional_call_allowance=structural_budget['maximum'],
            reserved_calls=structural_budget['used'],local_optimization_llm_calls=0),
        cost_control_stop=cost_stop,
        budget_draft=budget_draft,
        appearance=dimensions.appearance.model_dump(mode='json') if dimensions.appearance else None,
        reference_view=reference,reference_render_size=list(reference_size),photo_match=photo_match(final_reviews),
        dimensions=dimensions.model_dump(mode='json'),assembly=assembly.model_dump(mode='json'),reviews=final_reviews,
        analysis_workflow_id=r.inputs.get('analysis_workflow_id'),analysis_sha256=r.inputs.get('analysis_sha256'),
        **dimension_context,
        adaptations=['Strict declarative geometry replaces generated Python execution','Real closed-solid and contact validation',
            'Missing renders fail review','STEP is reopened before preview generation','No automatic manufacturing approval'],
        reference_image_ids=r.inputs['image_ids'],value_provenance=values,exact_reconstruction_verified=False)
    evidence['reference_views']=r.inputs.get('reference_views',[])
    def create_job():
        with r.db.sessions.begin() as session:
            workflow=r.checked_workflow(session)
            if workflow.revision_id and workflow.job_id:return dict(revision_id=workflow.revision_id,job_id=workflow.job_id)
            project=session.get(Project,r.project_id,with_for_update=True)
            number=session.scalar(update(Project).where(Project.id==project.id).values(revision_counter=Project.revision_counter+1).returning(Project.revision_counter))
            frozen=spec.model_copy(update={'revision':number}).model_dump(mode='json')
            revision=Revision(project_id=project.id,number=number,specification=frozen,spec_hash=canonical_hash(frozen),
                created_by=r.actor,source_workflow_id=r.id,value_provenance=values,state='SPECIFICATION_PENDING')
            session.add(revision);session.flush();job=Job(revision_id=revision.id,state='RUNNING',stage='engineering_cad_export')
            session.add(job);session.flush();revision.active_job_id=job.id
            workflow.revision_id=revision.id;workflow.job_id=job.id
            audit(session,r.actor,'engineering_cad_queued',job.id,project_id=r.project_id,upstream_commit=provenance()['commit'])
            return dict(revision_id=revision.id,job_id=job.id)
    bound=r.checkpoint('engineering_job',None,create_job,73);r.revision_id=bound['revision_id'];r.job_id=bound['job_id']
    with r.db.sessions() as session:
        from app.models import CabinetSpec
        revision=session.get(Revision,r.revision_id);spec=CabinetSpec.model_validate(revision.specification)
    def export():
        output=safe_directory(r.config.storage,r.job_id)
        if output.exists():
            from types import SimpleNamespace
            hashes=dict(package_hash=file_hash(output/'design_review_package.zip'),manifest_hash=file_hash(output/'manifest.json'))
            verify_package(output,SimpleNamespace(**hashes),canonical_hash(spec.model_dump(mode='json')))
            return dict(**hashes,agreement=json.loads((output/'geometry_verification.json').read_text()),validation=json.loads((output/'validation_report.json').read_text()))
        temporary=safe_directory(r.config.storage,r.job_id+'_engineering_'+r.lease)
        result=build_engineering_package(r.config,spec,temporary,evidence,r.update_progress,build,checks);temporary.rename(output);return result
    try:exported=r.checkpoint('engineering_files',None,export,75)
    except Exception as exc:
        with r.db.sessions.begin() as session:session.get(Job,r.job_id).state='FAILED'
        from app.cad_meshing import CADMeshError
        if isinstance(exc,CADMeshError):raise
        if isinstance(exc,ValueError):raise AIServiceError('CAD_EXPORT_NEEDS_REVISION',str(exc)[:1100],True) from exc
        raise
    with r.db.sessions.begin() as session:
        job=session.get(Job,r.job_id);revision=session.get(Revision,r.revision_id)
        job.state='SUCCEEDED';job.progress=100;job.stage='complete';job.updated_at=utcnow()
        job.package_hash=exported['package_hash'];job.manifest_hash=exported['manifest_hash'];job.validation=exported['validation']
        revision.state='VALIDATION_FAILED'
    result=dict(engineering_cad=dict(geometry_source=SOURCE,status=budget_draft['status'] if budget_draft else 'SOLID_GEOMETRY_CHECKED',
        cad_export_agreement=exported['agreement'],workflow=evidence,visual_review=final_reviews,photo_match=photo_match(final_reviews)),
        cad=dict(revision_id=r.revision_id,job_id=r.job_id),specification=spec.model_dump(mode='json'),
        value_provenance=values,deterministic_validation=exported['validation'],reference_image_ids=r.inputs['image_ids'],
        approved_for_manufacturing=False,exact_reconstruction_verified=False)
    r.previous_result=result
    with r.db.sessions.begin() as session:r.checked_workflow(session).result=result
    from app.delivery import build_delivery,verify_delivery
    delivery=r.checkpoint('delivery_package',None,lambda:build_delivery(r,result),97);verify_delivery(r.config,delivery)
    result['delivery']=delivery;return 'REVIEW_REQUIRED',result
