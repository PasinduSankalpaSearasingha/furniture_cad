"""Keep built geometry available when the AI spending check stops a workflow."""
from app.photo_schema import PhotoDesign
from app.pipeline import canonical_hash
from app.ai.openai_client import AIServiceError

PREFIX='engineering_exportable_draft_'


def describe_draft(plan,checks,source_checkpoint=None,stop=None):
    connected=checks.get('furniture_items_connected',checks['assembly_connected'])
    issues=[]
    if not connected:
        items=checks.get('furniture_items') or [dict(label='Furniture',connected=False,contact_groups=checks['contact_groups'])]
        for item in items:
            if item['connected']:continue
            groups=item['contact_groups']
            issues.append(dict(code='ASSEMBLY_CONNECTIONS_INCOMPLETE',
                message=f'{item["label"]}: some pieces do not connect. Separate groups: '+
                    '; '.join(', '.join(group) for group in groups)+'.',
                part_ids=[pid for group in groups for pid in group]))
    repair_limit=bool(stop and stop['code']=='CAD_REPAIR_LIMIT_REACHED')
    preview_limit=bool(stop and stop['code']=='CAD_PREVIEW_UNAVAILABLE')
    message=('Automatic repair attempts stopped. Available CAD was saved as a draft.' if repair_limit else
        'The comparison preview was unavailable. Available CAD was saved as a draft for review.' if preview_limit else
        'AI corrections stopped at a credit or spending limit. Available CAD was saved as a draft.')
    return dict(status='REPAIR_LIMIT_DRAFT' if repair_limit else 'PREVIEW_UNAVAILABLE_DRAFT' if preview_limit else 'BUDGET_LIMIT_DRAFT',
        message=message,stop_reason=stop,plan_sha256=canonical_hash(plan.model_dump(mode='json')),
        source_checkpoint=source_checkpoint,available_solid_count=checks['part_count'],
        fully_connected=connected,remaining_issues=issues,approved_for_manufacturing=False)


def retain_exportable_draft(r,plan,checks):
    # Called only after the complete plan builds with valid closed solids,
    # interference checks and supplied-dimension checks. No parts are omitted.
    # Normalize numeric fields before hashing, including any values assigned
    # by local edits (e.g. integer 20 versus the schema's float 20.0).
    plan=PhotoDesign.model_validate(plan.model_dump(mode='json'))
    key=PREFIX+canonical_hash(plan.model_dump(mode='json'))[:20]
    entry=dict(plan=plan.model_dump(mode='json'),draft=describe_draft(plan,checks,key))
    r.checkpoint(key,None,lambda:entry,30)


def available_budget_draft(r):
    with r.db.sessions() as session:
        keys=[key for key in r.checked_workflow(session).checkpoints if key.startswith(PREFIX)]
    latest=None
    for key in reversed(keys):
        entry=r.checkpoint(key,None,lambda:None,30)
        plan=PhotoDesign.model_validate(entry['plan']);draft=entry['draft']
        if draft['plan_sha256']!=canonical_hash(plan.model_dump(mode='json')):
            raise AIServiceError('CHECKPOINT_INTEGRITY_FAILED','The saved draft does not match its CAD plan')
        if latest is None:latest=(plan,draft)
        # Prefer the latest connected design over a later disconnected repair.
        if draft['fully_connected']:return plan,draft
    return latest


def reference_only_output(workflow,error):
    """Return real saved evidence when no complete exportable CAD exists yet."""
    inputs=workflow.input_data or {};dimensions=None
    checkpoint=(workflow.checkpoints or {}).get('engineering_dimensions')
    if checkpoint:
        if canonical_hash(checkpoint['payload'])!=checkpoint['sha256']:
            raise AIServiceError('CHECKPOINT_INTEGRITY_FAILED','Saved dimensions changed')
        dimensions=checkpoint['payload']
    ids=inputs.get('image_ids',[])
    saved_plan=None
    for key,entry in (workflow.checkpoints or {}).items():
        if not key.startswith(PREFIX):continue
        if canonical_hash(entry['payload'])!=entry['sha256']:
            raise AIServiceError('CHECKPOINT_INTEGRITY_FAILED','Saved CAD draft changed')
        payload=entry['payload']
        if canonical_hash(payload['plan'])!=payload['draft']['plan_sha256']:
            raise AIServiceError('CHECKPOINT_INTEGRITY_FAILED','Saved CAD draft does not match its plan')
        if saved_plan is None or payload['draft']['fully_connected']:saved_plan=payload['plan']
    stopped=('Automatic CAD repair attempts stopped.' if error.code=='CAD_BUILD_NEEDS_REVISION' else
        'The CAD display preview could not be completed.' if error.code=='CAD_PREVIEW_UNAVAILABLE' else
        'Generation stopped before file exports finished.' if error.code=='AGENT_WORKFLOW_FAILED' else
        'AI generation stopped at a credit or spending limit.')
    return dict(available_output=dict(status='REFERENCE_AND_DIMENSIONS_ONLY',has_cad=False,
        has_saved_cad_plan=saved_plan is not None,cad_plan=saved_plan,
        message=stopped+(' The available CAD plan, reference photos and measurements are saved; file exports did not finish.' if saved_plan else ' Your reference photos and available measurements are saved. No new CAD model was completed.'),
        stop_reason=dict(code=error.code,message=error.message),dimensions=dimensions,
        known_values=inputs.get('known_values',[]),analysis=inputs.get('source_photo_analysis'),
        references=[dict(image_id=image_id,download_url=f'/api/v1/images/{image_id}') for image_id in ids],
        approved_for_manufacturing=False),reference_image_ids=ids,approved_for_manufacturing=False)
