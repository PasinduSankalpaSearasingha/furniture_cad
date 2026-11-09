"""Bind the studio's current engineering assembly to its verified CAD export."""
from sqlalchemy import select
from app.database import AgentWorkflow, Job, Revision


ACTIVE_WORKFLOW_STATES = {'QUEUED', 'RUNNING', 'CAD_QUEUED', 'ESTIMATED_READY'}


def project_model_scope(session, project_id, workflows=None):
    if workflows is None:
        workflows = list(session.scalars(select(AgentWorkflow).where(
            AgentWorkflow.project_id == str(project_id)).order_by(AgentWorkflow.created_at.desc())))
    engineering=next((w for w in workflows if w.mode=='IMAGE_TO_CAD' and
        (w.input_data or {}).get('cad_source')=='ENGINEERING_CAD'),None)
    if engineering is None:
        return None
    if (engineering.result or {}).get('available_output') and not (engineering.input_data or {}).get('engineering_edit'):
        # A stopped generation without CAD must not hide an earlier saved model.
        previous=next((w for w in workflows[workflows.index(engineering)+1:] if
            w.project_id==str(project_id) and w.mode=='IMAGE_TO_CAD' and (w.input_data or {}).get('cad_source')=='ENGINEERING_CAD' and (w.result or {}).get('delivery') and
            ((w.result or {}).get('engineering_cad') or {}).get('cad_export_agreement',{}).get('valid_closed_solids')),None)
        if previous:
            scope=project_model_scope(session,project_id,[previous])
            if scope and scope['solid_geometry_verified']:
                return dict(scope,available_output_workflow_id=engineering.id)
    pending_edit=None
    edit=(engineering.input_data or {}).get('engineering_edit')
    if edit and (engineering.state!='REVIEW_REQUIRED' or not (engineering.result or {}).get('delivery')):
        pending_edit=engineering.id
        source=next((workflow for workflow in workflows if workflow.id==edit['source_workflow_id']),None)
        if source is not None:engineering=source
    result=engineering.result or {}
    proof=(result.get('engineering_cad') or {}).get('cad_export_agreement') or {}
    revision=session.get(Revision,engineering.revision_id) if engineering.revision_id else None
    job=session.get(Job,engineering.job_id) if engineering.job_id else None
    valid=bool(revision and job and job.state=='SUCCEEDED' and job.revision_id==revision.id and
        revision.project_id==str(project_id) and revision.active_job_id==job.id and proof.get('valid_closed_solids'))
    return dict(source_workflow_id=engineering.id,source_glb_sha256=proof.get('assembly_glb_sha256'),
        pending_edit_workflow_id=pending_edit,
        cad_workflow_id=engineering.id if valid else None,revision_id=revision.id if valid else None,
        job_id=job.id if valid else None,geometry_source='ENGINEERING_CAD',solid_geometry_verified=valid,
        parts=dict(part_count=proof.get('part_count',0)),exact_match_verified=False,approved_for_manufacturing=False)


def scoped_workflows(workflows, scope):
    if not scope:
        return workflows
    # Keep pending work and the displayed assembly visible without substituting unrelated CAD.
    return [w for w in workflows if w.state in ACTIVE_WORKFLOW_STATES
        or w.id==scope.get('pending_edit_workflow_id')
        or w.id==scope.get('available_output_workflow_id')
        or w.id in (scope['source_workflow_id'], scope['cad_workflow_id'])
        or (scope['revision_id'] and w.revision_id == scope['revision_id'] and w.job_id == scope['job_id'])]
