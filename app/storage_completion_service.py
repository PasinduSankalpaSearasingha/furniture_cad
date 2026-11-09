"""New immutable revisions for local completion of a saved cabinet model."""
import copy
from app.agents.photo_agent import resolve_estimated_geometry
from app.storage_completion import POLICY,MARKER,complete_storage_plan,coverage_360,storage_design
from app.engineering_cad import geometry_checks,check_supplied_dimensions
from app.engineering_schema import EngineeringAssembly,EngineeringReview
from app.photo_schema import PhotoDesign
from app.pipeline import canonical_hash


def completion_checkpoints(source,project_name):
    if canonical_hash(source.input_data)!=source.input_hash:raise ValueError('Saved workflow inputs changed')
    for checkpoint in source.checkpoints.values():
        if canonical_hash(checkpoint['payload'])!=checkpoint['sha256']:raise ValueError('Saved workflow checkpoint changed')
    if not {'engineering_plan','engineering_dimensions'}<=source.checkpoints.keys():raise ValueError('A completed furniture component plan and dimensions are required')
    original=PhotoDesign.model_validate(source.checkpoints['engineering_plan']['payload'])
    if not storage_design(original):raise ValueError('This completion action is for closed cabinets and sideboards')
    _,_,_,build,_=resolve_estimated_geometry(original,source.input_data,project_name)
    completed,report=complete_storage_plan(original,build)
    completed,_,_,build,_=resolve_estimated_geometry(completed,source.input_data,project_name)
    checks=geometry_checks(build);checks['supplied_dimensions']=check_supplied_dimensions(build,source.input_data.get('known_values',[]))
    checks['full_360']=coverage_360(completed,build)
    if not checks.get('furniture_items_connected',checks['assembly_connected']) or not checks['full_360']['rear_closed']:
        raise ValueError('The completed cabinet must have a connected carcass and a closed rear')
    def freeze(payload):return dict(payload=payload,sha256=canonical_hash(payload))
    original_assembly=source.checkpoints.get('engineering_assembly')
    evidence_basis={part.part_id:part.evidence_basis for part in
        EngineeringAssembly.model_validate(original_assembly['payload']).parts} if original_assembly else {}
    assembly=EngineeringAssembly(furniture_type=completed.furniture_type,
        parts=[dict(part_id=p.part_id,label=p.label,description=p.evidence,visible_evidence=p.evidence,
            evidence_basis='ASSUMED_COMPLETION' if p.evidence.startswith(MARKER) else evidence_basis.get(p.part_id,'IMAGE_OBSERVED')) for p in completed.parts],
        assumptions=completed.assumptions)
    review=EngineeringReview(accepted=False,
        findings=['Original exterior geometry retained; rear and internal components added as estimated construction.'],
        limitations=['Added hidden construction has not received an AI or physical design review. Geometry and multi-angle renders are checked locally.'])
    checkpoints={key:copy.deepcopy(source.checkpoints[key]) for key in ('engineering_dimensions',) if key in source.checkpoints}
    checkpoints.update(engineering_plan=freeze(completed.model_dump(mode='json')),
        engineering_assembly=freeze(assembly.model_dump(mode='json')),
        engineering_critic_0=freeze(review.model_dump(mode='json')),
        engineering_visual_review_0=freeze(review.model_dump(mode='json')),
        engineering_completion=freeze(dict(**report,source_workflow_id=source.id,source_plan_sha256=source.checkpoints['engineering_plan']['sha256'],checks=checks)))
    return checkpoints
