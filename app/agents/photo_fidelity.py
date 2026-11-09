"""Bounded reference comparisons before a photo plan becomes an immutable revision."""
import base64
import math
import json
import ast
from typing import Literal,Annotated
from pydantic import Field, model_validator
from app.photo_schema import PhotoModel, PhotoPart, PhotoDesign, ResolvedValue,Expression,Point,Point2,LoftSection,SurfaceDish,SurfaceGrid,SweepSection,number
from app.pipeline import canonical_hash, safe_directory, file_hash
from app.database import AgentWorkflow
from app.ai.openai_client import AIServiceError
from app.ai.geometry_preview import render_build
from . import photo_agent


POLICY_VERSION=1


class GeometryDifference(PhotoModel):
    component_ids: list[str] = Field(max_length=60)
    severity: Literal['MINOR','MAJOR']
    operation: Literal['reshape','add','remove'] = 'reshape'
    observed_difference: str = Field(min_length=1,max_length=1000)
    correction: str = Field(min_length=1,max_length=1500)


class PhotoGeometryReview(PhotoModel):
    summary: str = Field(min_length=1,max_length=2400)
    visible_geometry_matches: bool
    differences: list[GeometryDifference] = Field(max_length=20)
    preserved_features: list[str] = Field(max_length=30)
    unknown_geometry: list[str] = Field(max_length=30)
    exact_reconstruction_verified: Literal[False] = False

    @model_validator(mode='after')
    def coherent(self):
        if self.visible_geometry_matches and any(d.severity=='MAJOR' for d in self.differences):
            raise ValueError('A major visible difference cannot be marked as a visible match')
        return self


class PhotoGeometryPatch(PhotoModel):
    summary: str = Field(min_length=1,max_length=1600)
    updated_parts: list[PhotoPart] = Field(max_length=60)
    added_parts: list[PhotoPart] = Field(max_length=12)
    removed_part_ids: list[str] = Field(max_length=12)
    added_values: list[ResolvedValue] = Field(max_length=24)


class PartFieldChange(PhotoModel):
    part_id: str = Field(pattern=r'^F[0-9]{3}$')
    field: Literal['geometry','position','rotation_deg','size','corner_radius','edge_radius','rounded_ends',
        'dish_depth','surface_dishes','surface_grid','sweep_sections','outline','path','curve_mode','sections','center','width','depth','profile']
    section_index: int | None = Field(default=None,ge=0,le=5)
    value: (Expression | bool | Annotated[list[Expression],Field(max_length=3)] |
        Annotated[list[Point],Field(max_length=37)] | Annotated[list[Point2],Field(max_length=37)] |
        Annotated[list[LoftSection],Field(max_length=6)] | Annotated[list[SurfaceDish],Field(max_length=8)] |
        SurfaceGrid | Annotated[list[SweepSection],Field(max_length=12)])


class SparseGeometryPatch(PhotoModel):
    summary: str = Field(min_length=1,max_length=1600)
    changes: list[PartFieldChange] = Field(max_length=120)
    added_parts: list[PhotoPart] = Field(max_length=12)
    removed_part_ids: list[str] = Field(max_length=12)
    added_values: list[ResolvedValue] = Field(max_length=24)


def expand_sparse_patch(plan,patch):
    existing={p.part_id:p.model_dump(mode='json') for p in plan.parts};updated={};seen=set()
    for change in patch.changes:
        if change.part_id not in existing: raise ValueError('A field edit refers to a missing component')
        target=(change.part_id,change.section_index,change.field)
        if target in seen: raise ValueError('The same component field was edited twice')
        seen.add(target)
        if change.part_id not in updated:
            import copy
            updated[change.part_id]=copy.deepcopy(existing[change.part_id])
        part=updated[change.part_id]
        if change.section_index is not None:
            if change.field not in ('center','width','depth','profile','corner_radius') or change.section_index>=len(part['sections']):
                raise ValueError('Invalid loft section field edit')
            destination=part['sections'][change.section_index]
        else:
            if change.field in ('center','width','depth','profile'): raise ValueError('Section fields require a section index')
            destination=part
        destination[change.field]=change.model_dump(mode='json')['value']
    additions=list(patch.added_values);values={v.key:v.value for v in [*plan.values,*additions]};aliases={}
    # Missing simple aliases may retain an existing corresponding scalar. This
    # never invents a new dimension or changes a supplied parameter value.
    def corresponding(new,old):
        if isinstance(new,dict) and isinstance(old,dict):
            for key in new.keys()&old.keys():
                if key in ('position','rotation_deg','size','corner_radius','edge_radius','dish_depth','outline','path','sections',
                           'center','width','depth','depths','radius_x','radius_y','radius_z','surface_dishes','surface_grid','sweep_sections'):
                    corresponding(new[key],old[key])
        elif isinstance(new,list) and isinstance(old,list):
            for a,b in zip(new,old): corresponding(a,b)
        elif isinstance(new,str) and new.isidentifier() and new not in values:
            try: aliases.setdefault(new,[]).append(number(old,{v.key:v.value for v in plan.values}))
            except (ValueError,SyntaxError,TypeError): pass
    for pid,raw in updated.items(): corresponding(raw,existing[pid])
    for key,candidates in aliases.items():
        if all(math.isclose(candidates[0],v,abs_tol=1e-6) for v in candidates):
            additions.append(ResolvedValue(key=key,value=candidates[0],explanation='Unprovided alias retained from the corresponding existing geometry; not a new measurement'))
    return PhotoGeometryPatch(summary=patch.summary,updated_parts=[PhotoPart.model_validate(p) for p in updated.values()],
        added_parts=patch.added_parts,removed_part_ids=patch.removed_part_ids,added_values=additions)


def apply_geometry_patch(plan,patch,review):
    """The provider can change only the parts identified by the comparison."""
    existing={p.part_id:p for p in plan.parts}
    allowed={pid for d in review.differences if d.operation=='reshape' for pid in d.component_ids}
    removable={pid for d in review.differences if d.operation=='remove' for pid in d.component_ids}
    updates={p.part_id:p for p in patch.updated_parts}
    if len(updates)!=len(patch.updated_parts) or not updates.keys()<=allowed or not updates.keys()<=existing.keys():
        raise ValueError('Visual correction attempted to change an unflagged or missing component')
    if any(p.material!=existing[pid].material or p.color_rgb!=existing[pid].color_rgb for pid,p in updates.items()):
        raise ValueError('A geometry correction cannot change a component material or color')
    if not set(patch.removed_part_ids)<=removable or set(patch.removed_part_ids)&updates.keys():
        raise ValueError('Visual correction attempted an unapproved component removal')
    if patch.added_parts and not any(d.operation=='add' for d in review.differences):
        raise ValueError('Visual correction attempted to invent an unrequested component')
    keys={v.key for v in plan.values}
    if any(v.key in keys for v in patch.added_values) or len({v.key for v in patch.added_values})!=len(patch.added_values):
        raise ValueError('Visual correction cannot change an existing parameter value')
    parts=[updates.get(p.part_id,p) for p in plan.parts if p.part_id not in patch.removed_part_ids]
    parts.extend(patch.added_parts)
    raw=plan.model_dump(mode='json')
    raw['parts']=[{**p.model_dump(mode='json'),'part_id':f'F{i+1:03d}'} for i,p in enumerate(parts)]
    raw['values']+= [v.model_dump(mode='json') for v in patch.added_values]
    return PhotoDesign.model_validate(raw)


def parameter_edit(plan,inputs):
    """Keep the exact geometry expressions, profiles, colors and component list."""
    values={v.key for v in plan.values}
    changes={v['key']:v['value'] for v in inputs.get('known_values',[])}
    if changes.keys()-values:
        raise AIServiceError('PHOTO_PARAMETER_UNKNOWN','This saved design does not define that parameter; add a named geometry parameter before editing it')
    raw=plan.model_dump(mode='json')
    for value in raw['values']:
        if value['key'] in changes: value['value']=changes[value['key']]
    return PhotoDesign.model_validate(raw)


def geometry_changed(before,after,part_ids):
    from app.geometry import exact_bounds
    if set(before.shapes)!=set(after.shapes): return True
    for pid in part_ids:
        a,b=before.shapes[pid].val(),after.shapes[pid].val()
        ba,bb=exact_bounds(a),exact_bounds(b)
        if (not math.isclose(a.Volume(),b.Volume(),rel_tol=1e-8,abs_tol=1e-5) or
            any(abs(getattr(ba,axis+edge)-getattr(bb,axis+edge))>1e-5 for axis in 'xyz' for edge in ('min','max'))):
            return True
        if a.cut(b).Volume()+b.cut(a).Volume()>1e-4: return True
    return False


def validate_correction(plan,patch,review,inputs,name,original_build):
    """Keep valid independent corrections when another proposed component is invalid."""
    deferred=[];parts=[];source={p.part_id:p for p in plan.parts};known={v['key'] for v in inputs.get('known_values',[])}
    for part in patch.updated_parts:
        original=source.get(part.part_id)
        names={n.id for n in ast.walk(ast.parse(str(part.edge_radius),mode='eval')) if isinstance(n,ast.Name)}
        if (original and part.edge_radius!=original.edge_radius and not names&known and
            (part.surface_grid or part.surface_dishes or part.rounded_ends or (part.geometry=='outline_panel' and len(part.outline)>16))):
            # An all-edge fillet on these compound surfaces is not a qualified
            # operation. Retain the previous rounding and keep the actual curves/cuts.
            part=part.model_copy(update={'edge_radius':original.edge_radius})
            deferred.append(dict(part_id=part.part_id,field='edge_radius',reason='Previous edge rounding retained; compound-surface fillet requires a dedicated profile'))
        parts.append(part)
    patch=patch.model_copy(update={'updated_parts':parts})
    def prune_unused_estimates(candidate):
        references=set();original_keys={v.key for v in plan.values}
        def collect(value):
            if isinstance(value,dict):
                for v in value.values(): collect(v)
            elif isinstance(value,list):
                for v in value: collect(v)
            elif isinstance(value,str):
                try: references.update(n.id for n in ast.walk(ast.parse(value,mode='eval')) if isinstance(n,ast.Name))
                except SyntaxError: pass
        for part in candidate.parts:
            data=part.model_dump(mode='json')
            collect({k:v for k,v in data.items() if k not in ('part_id','label','evidence','geometry','material','color_rgb')})
        return candidate.model_copy(update={'values':[v for v in candidate.values
            if v.key in original_keys or v.key in references or v.key.endswith('_count')]})
    def validate(base,proposal,before):
        candidate=apply_geometry_patch(base,proposal,review)
        spec,_=photo_agent.specification(candidate,inputs,name)
        build=photo_agent.preflight(spec,{v['key'] for v in inputs.get('known_values',[])},check_estimates=False)
        if not geometry_changed(before,build,[p.part_id for p in proposal.updated_parts]):
            raise ValueError('The edit did not change actual CAD geometry; change a field used by that component')
        return candidate,build
    try:
        candidate,_=validate(plan,patch,original_build)
        return prune_unused_estimates(candidate),deferred
    except Exception as original_error:
        if patch.added_parts or patch.removed_part_ids or len(patch.updated_parts)<2 or len(patch.updated_parts)>12: raise
        working,before=plan,original_build;errors=list(deferred);accepted=0
        for part in patch.updated_parts:
            present={v.key:v.value for v in working.values}
            additions=[v for v in patch.added_values if v.key not in present]
            if any(v.key in present and v.value!=present[v.key] for v in patch.added_values): raise original_error
            individual=PhotoGeometryPatch(summary=patch.summary,updated_parts=[part],added_parts=[],
                removed_part_ids=[],added_values=additions)
            try:
                working,before=validate(working,individual,before);accepted+=1
            except Exception as exc: errors.append(dict(part_id=part.part_id,reason=str(exc)[:400]))
        if not accepted: raise original_error
        return prune_unused_estimates(working),errors


def refine(runner,plan,observations,name,images=None,initial_provenance=None,reference_ids=None):
    r=runner;images=images if images is not None else r.images()
    passes=r.config.photo_refinement_passes
    estimates=photo_agent.estimate_adjustments(initial_provenance or r.inputs.get('source_value_provenance') or {})
    def keep_adjustments(adjustments):
        for key,change in adjustments.items():
            estimates[key]=dict(estimated_value=estimates.get(key,change)['estimated_value'],resolved_value=change['resolved_value'])
    numeric=None;context=[]
    from . import reference_fit_agent as fitting
    if getattr(r.config,'photo_numeric_fit_enabled',False):
        try:
            plan,numeric=fitting.fit_reference(r,plan,observations,name,image_ids=reference_ids)
            context=fitting.load_context(r,numeric)
        except (ValueError,AIServiceError) as exc:
            r.record('reference_fitting_incomplete',dict(reason=str(exc)[:400],valid_geometry_retained=True))
            numeric=dict(status='FIT_INCOMPLETE',warnings=[str(exc)[:400]],geometry_changed=False)
    if not passes:
        return plan,dict(policy_version=POLICY_VERSION,status='LOCAL_FIT_REVIEW_REQUIRED' if numeric else 'NOT_CHECKED',
            numeric_fit=numeric,estimate_adjustments=estimates,exact_reconstruction_verified=False)
    view=observations.get('reference_view') or {}
    azimuth=view.get('azimuth_deg',-110);elevation=view.get('elevation_deg',12)
    history=[];last=None;previews=None;rejected=None;comparison_error=None
    def optional_checkpoint(key,schema,callback,percent):
        nonlocal comparison_error
        try: return r.checkpoint(key,schema,callback,percent)
        except AIServiceError as exc:
            if exc.code not in {'AI_API_ERROR','AI_RESPONSE_INCOMPLETE','AI_INVALID_STRUCTURED_OUTPUT','AI_TIMEOUT',
                'AI_CONNECTION_ERROR','AI_RATE_LIMIT','AI_REFUSAL','AI_BAD_REQUEST','AI_UNPROCESSABLE_REQUEST','MODEL_UNAVAILABLE'}: raise
            comparison_error=exc.as_dict()
            r.record('photo_comparison_incomplete',dict(error=comparison_error,valid_geometry_retained=True))
            return None
    for iteration in range(passes+1):
        plan,spec,_,build,adjustments=photo_agent.resolve_estimated_geometry(plan,r.inputs,name)
        keep_adjustments(adjustments)
        plan_hash=canonical_hash(plan.model_dump(mode='json'))
        camera=context[0][1] if context else None
        frame_hash=canonical_hash(dict(plan=plan_hash,azimuth=azimuth,elevation=elevation,camera=camera,renderer=3))[:12]
        legacy_preview=f'photo_fidelity_preview_{iteration}'
        with r.db.sessions() as session:
            cached=(session.get(AgentWorkflow,r.id).checkpoints or {}).get(legacy_preview)
        compatible=bool(not camera and cached and cached['payload'].get('plan_sha256')==plan_hash and
            cached['payload'].get('azimuth_deg')==azimuth and cached['payload'].get('elevation_deg')==elevation)
        suffix='' if compatible else '_'+frame_hash
        def preview():
            directory=safe_directory(r.config.storage,'photo_fidelity_'+r.id)
            directory.mkdir(exist_ok=True)
            files={}
            for view_name,az,el in (('reference_view',azimuth,elevation),('opposite_view',azimuth+110,20)):
                filename=f'candidate_{iteration}_{frame_hash}_{view_name}.png'
                path=safe_directory(directory,filename)
                fitted_camera=camera if view_name=='reference_view' else None
                view_size=tuple(int(v*4) for v in context[0][0]['size']) if fitted_camera else (620,700)
                path.write_bytes(render_build(build,az,el,size=view_size,camera=fitted_camera))
                files[filename]=file_hash(path)
            return dict(directory=directory.name,files=files,plan_sha256=plan_hash,
                azimuth_deg=azimuth,elevation_deg=elevation)
        previews=r.checkpoint(legacy_preview+suffix,None,preview,47)
        if previews['plan_sha256']!=plan_hash: raise AIServiceError('PHOTO_PREVIEW_INTEGRITY_FAILED','Comparison preview belongs to another geometry plan')
        directory=safe_directory(r.config.storage,previews['directory'])
        comparison=[]
        for filename,digest in previews['files'].items():
            path=safe_directory(directory,filename)
            if file_hash(path)!=digest: raise AIServiceError('PHOTO_PREVIEW_INTEGRITY_FAILED','Geometry comparison preview changed')
            comparison.append(dict(type='input_image',image_url='data:image/png;base64,'+base64.b64encode(path.read_bytes()).decode(),detail='high'))
        last=optional_checkpoint(f'photo_fidelity_review_{iteration}'+suffix,PhotoGeometryReview,lambda:r.gateway.structured('review',
            '''Compare the uploaded furniture reference(s) with the LAST TWO images, which show actual CAD
triangles with correct depth occlusion. First CAD view approximates the reference camera; second exposes depth.
Judge visible design fidelity: silhouette, seat shape, curves, rail cross-section and end shaping,
component counts, support positions, leg taper/sweep and softened edges. Do not accept a generic similar chair.
Separate camera/perspective or plain material differences from actual geometry errors. Do not infer exact
physical measurements or hidden surfaces. User-entered dimensions are intentional overrides: preserve them
and evaluate other features against the reference. Identify MAJOR differences that can be corrected from
visible evidence. Assign existing component_ids accurately. Use add/remove only for visibly missing/extra
parts. Unknown hidden geometry is uncertainty, not an instruction to invent it. Never claim an exact copy.''',
            dict(plan=plan.model_dump(mode='json'),known_values=r.inputs.get('known_values',[]),
                 reference_view=dict(azimuth_deg=azimuth,elevation_deg=elevation)),PhotoGeometryReview,
            images=[*images,*comparison],max_output_tokens=10000,timeout_seconds=r.config.photo_timeout_seconds,
            reasoning_effort='medium'),49)
        if last is None: break
        history.append(dict(iteration=iteration,plan_sha256=plan_hash,review=last.model_dump(mode='json'),previews=previews))
        if last.visible_geometry_matches or not last.differences or iteration==passes: break
        patch=optional_checkpoint(f'photo_fidelity_edit_{iteration}'+suffix,SparseGeometryPatch,lambda:r.gateway.structured('design',
            photo_agent.DESIGN+''' Correct only the components identified in the supplied comparison.
Return a SparseGeometryPatch containing only changed fields for flagged reshape IDs. Do not repeat complete
component definitions. For a loft corner radius, use field=corner_radius and its zero-based section_index;
for whole-part fields use section_index=null. Path/outline/sections fields replace their corresponding list.
Other component definitions remain unchanged. Keep every existing parameter value, including estimates.
Change local curves, profiles, sections and coordinate expressions to match the visible photograph. You may
add new named parameters but never redefine an existing one. Only add/remove components if explicitly
requested by the comparison. Preserve colors/materials and all other design features. Do not simplify
unaffected components. Rounded rail ends and rounded rectangular loft sections are real CAD features.''',
            dict(previous_plan=plan.model_dump(mode='json'),comparison=last.model_dump(mode='json'),
                 previous_correction_failure=rejected,
                 known_values=r.inputs.get('known_values',[])),SparseGeometryPatch,images=[*images,*comparison],
            max_output_tokens=r.config.photo_max_output_tokens,timeout_seconds=r.config.photo_timeout_seconds,
            reasoning_effort='medium'),50)
        if patch is None: break
        try:
            expanded=expand_sparse_patch(plan,patch) if isinstance(patch,SparseGeometryPatch) else patch
            candidate,component_errors=validate_correction(plan,expanded,last,r.inputs,name,build)
            candidate,_,_,candidate_build,adjustments=photo_agent.resolve_estimated_geometry(candidate,r.inputs,name)
            if canonical_hash(candidate.model_dump(mode='json'))==plan_hash: break
            if not geometry_changed(build,candidate_build,[p.part_id for p in expanded.updated_parts]):
                raise ValueError('The edit did not change actual CAD geometry; change a field used by that component')
            if context:
                original_parts={p.part_id:p.model_dump(mode='json') for p in plan.parts}
                interior_only=not expanded.added_parts and not expanded.removed_part_ids and all(
                    {k for k in set(original_parts[p.part_id])|set(p.model_dump(mode='json'))
                        if original_parts[p.part_id].get(k)!=p.model_dump(mode='json').get(k)}<=
                    {'surface_grid','surface_dishes','dish_depth'} for p in expanded.updated_parts)
                fitting.require_improvement(fitting.measure(build,plan,context),fitting.measure(candidate_build,candidate,context),interior_only)
                if interior_only:r.record('interior_surface_review_required',dict(
                    reason='Surface sculpting retained after CAD validation and projection nonregression; silhouette does not measure interior accuracy'))
            plan=candidate
            keep_adjustments(adjustments)
            rejected=json.dumps(component_errors) if component_errors else None
            r.record('photo_visual_correction_applied',dict(iteration=iteration,
                component_ids=[p.part_id for p in expanded.updated_parts if p.part_id not in {e['part_id'] for e in component_errors if 'field' not in e}],
                rejected_components=component_errors))
        except Exception as exc:
            rejected=str(exc)[:500]
            r.record('photo_visual_correction_rejected',dict(iteration=iteration,reason=rejected))
            if iteration>=passes-1: break
    if context:
        plan,_,_,final_build,_=photo_agent.resolve_estimated_geometry(plan,r.inputs,name)
        numeric=fitting.final_measurement(r,plan,final_build,numeric,context)
    return plan,dict(policy_version=POLICY_VERSION,numeric_fit=numeric,
        status='VISIBLE_MATCH_REVIEWED' if last and last.visible_geometry_matches else 'NEEDS_VISUAL_REVIEW',
        iterations=max(0,len(history)-1),review=last.model_dump(mode='json') if last else None,history=history,error=comparison_error,
        correction_rejected=rejected,previews=previews,estimate_adjustments=estimates,exact_reconstruction_verified=False)
