"""Scoped component edits derived from a verified engineering CAD revision."""
import copy
from pydantic import Field
from app.photo_schema import PhotoModel,PhotoPart,PhotoDesign,ResolvedValue
from app.pipeline import canonical_hash


class EngineeringEditPatch(PhotoModel):
    parts: list[PhotoPart] = Field(min_length=1,max_length=60)
    values: list[ResolvedValue] = Field(default_factory=list,max_length=60)
    summary: str = Field(min_length=1,max_length=1200)
    assumptions: list[str] = Field(min_length=1,max_length=15)


STYLES={
    'round':'Give the selected parts round or oval sections while retaining their assembly contacts.',
    'tapered':'Taper the selected parts, keeping their mating ends and intended assembly contacts.',
    'curved':'Give the selected parts a smooth curved profile, preserving their mounting positions and contacts.',
    'custom':'Apply the requested shape change only to the selected components.',
}


def merge_edit(edit,patch):
    original=PhotoDesign.model_validate(edit['source_plan']);selected=set(edit['part_ids'])
    ids=[part.part_id for part in patch.parts]
    if len(ids)!=len(set(ids)) or set(ids)!=selected:
        raise ValueError('Return every selected part exactly once; other components cannot be edited')
    raw=original.model_dump(mode='json');replacements={part.part_id:part.model_dump(mode='json') for part in patch.parts}
    raw['parts']=[replacements.get(part['part_id'],part) for part in raw['parts']]
    existing={value['key']:value for value in raw['values']}
    new_keys=set()
    for value in patch.values:
        if value.key in new_keys:raise ValueError('An edit parameter was supplied twice')
        new_keys.add(value.key)
        if value.key in existing:
            if value.value!=existing[value.key]['value']:raise ValueError('Existing dimensions are retained during a shape edit; use a new parameter for the changed profile')
        else:raw['values'].append(value.model_dump(mode='json'))
    raw['summary']=(original.summary+' Requested component edit: '+patch.summary)[:2400]
    raw['assumptions']=[*patch.assumptions,*original.assumptions][:30]
    return PhotoDesign.model_validate(raw)


def verify_edit_contract(edit,candidate):
    original=PhotoDesign.model_validate(edit['source_plan'])
    if canonical_hash(edit['source_plan'])!=edit['source_plan_sha256']:raise ValueError('The source component plan changed')
    if [part.part_id for part in candidate.parts]!=[part.part_id for part in original.parts]:
        raise ValueError('Shape edits retain component identifiers and component count')
    if candidate.separate_items!=original.separate_items:raise ValueError('Shape edits retain furniture item membership and dimension scopes')
    selected=set(edit['part_ids']);before_values={value.key:value.value for value in original.values}
    after_values={value.key:value.value for value in candidate.values}
    if any(after_values.get(key)!=value for key,value in before_values.items()):raise ValueError('A shape edit changed an existing dimension')
    for before,after in zip(original.parts,candidate.parts):
        if before.part_id not in selected and before.model_dump(mode='json')!=after.model_dump(mode='json'):
            raise ValueError('An unselected component changed: '+before.part_id)


def verify_edit_solids(edit,source_build,build):
    """Check actual solids too: contact trimming can affect a later component."""
    from app.geometry import exact_bounds
    from app.engineering_cad import check_supplied_dimensions
    from app.agents.photo_agent import geometry_envelope
    selected=set(edit['part_ids']);changed=[];unchanged=[]
    for pid,source in source_build.shapes.items():
        before,after=source.val(),build.shapes[pid].val()
        tolerance=max(1e-5,before.Volume()*1e-9)
        a,b=exact_bounds(before),exact_bounds(after)
        same_bounds=all(abs(getattr(a,key)-getattr(b,key))<1e-6 for key in ('xmin','xmax','ymin','ymax','zmin','zmax'))
        same=same_bounds and abs(before.Volume()-after.Volume())<=tolerance
        if same:
            difference=before.cut(after).Volume()+after.cut(before).Volume()
            same=difference<=tolerance
        if pid not in selected:
            if not same:raise ValueError('The edit changed the solid of unselected part '+pid+'; preserve its contact surfaces')
            unchanged.append(pid)
        elif not same:changed.append(pid)
    if not changed:raise ValueError('The requested shape edit did not change any selected solid')
    dimensions=check_supplied_dimensions(build,[dict(key=key,value=value) for key,value in geometry_envelope(source_build).items()])
    return dict(source_workflow_id=edit['source_workflow_id'],changed_part_ids=changed,
        unselected_part_ids_preserved=unchanged,overall_dimensions=dimensions,all_outputs_regenerated=True)


def edit_inputs(source,part_ids,instructions,style):
    if canonical_hash(source.input_data)!=source.input_hash:raise ValueError('Saved workflow inputs changed')
    for checkpoint in source.checkpoints.values():
        if canonical_hash(checkpoint['payload'])!=checkpoint['sha256']:raise ValueError('Saved workflow checkpoint changed')
    if not {'engineering_plan','engineering_dimensions','engineering_assembly'}<=source.checkpoints.keys():
        raise ValueError('The source component plan is incomplete')
    plan=PhotoDesign.model_validate(source.checkpoints['engineering_plan']['payload'])
    if not set(part_ids)<={part.part_id for part in plan.parts}:raise ValueError('A selected component does not exist in this model')
    raw=copy.deepcopy(source.input_data)
    for key in ('engineering_reexport_of','engineering_completion_of','engineering_local_completion','engineering_manual_reconstruction','client_request_hash','engineering_edit'):
        raw.pop(key,None)
    raw['engineering_edit']=dict(source_workflow_id=source.id,source_job_id=source.job_id,
        source_plan=plan.model_dump(mode='json'),source_plan_sha256=source.checkpoints['engineering_plan']['sha256'],
        source_value_provenance=copy.deepcopy((source.result or {}).get('value_provenance',{})),
        part_ids=list(part_ids),style=style,instructions=instructions)
    if source.input_data.get('engineering_manual_reconstruction'):
        raw['engineering_edit']['source_reconstruction_origin']=source.input_data['engineering_manual_reconstruction']['origin']
    checkpoints={key:copy.deepcopy(source.checkpoints[key]) for key in ('engineering_dimensions','engineering_assembly')}
    return raw,checkpoints
