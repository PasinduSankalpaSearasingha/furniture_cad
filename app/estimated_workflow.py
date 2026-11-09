"""Explicit review-only path; never grants measurement or manufacturing approval."""
from dataclasses import dataclass
from sqlalchemy import update
from .database import AgentWorkflow, Project, Revision, Job, audit, utcnow
from .models import CabinetSpec
from .pipeline import canonical_hash
from .workflow import transition
from .ai.openai_client import AIServiceError


@dataclass(frozen=True)
class EstimatedCADContext:
    revision_id: str
    specification_hash: str
    specification: CabinetSpec


def validate_estimated_revision(revision,session):
    from .agents.furniture_agent import FurnitureEstimate, specification
    workflow=session.get(AgentWorkflow,revision.source_workflow_id)
    if (not workflow or workflow.mode not in ('ESTIMATE','PHOTO','PIPELINE','MODIFY','IMAGE_TO_CAD') or workflow.revision_id!=revision.id or
            workflow.project_id!=revision.project_id or canonical_hash(workflow.input_data)!=workflow.input_hash):
        raise AIServiceError('ESTIMATE_INTEGRITY_FAILED','Estimated CAD must refer to its exact source workflow')
    checkpoint=workflow.checkpoints.get('photo_plan' if workflow.mode in ('PHOTO','PIPELINE','MODIFY','IMAGE_TO_CAD') else 'furniture_estimate')
    if not checkpoint or canonical_hash(checkpoint['payload'])!=checkpoint['sha256']:
        raise AIServiceError('ESTIMATE_INTEGRITY_FAILED','Furniture estimate checkpoint changed')
    spec=CabinetSpec.model_validate(revision.specification)
    if workflow.mode in ('PHOTO','PIPELINE','MODIFY','IMAGE_TO_CAD'):
        from .photo_schema import PhotoDesign
        from .agents.photo_agent import specification as photo_specification
        result=PhotoDesign.model_validate(checkpoint['payload'])
        expected,_=photo_specification(result,workflow.input_data,spec.project_name)
    else:
        result=FurnitureEstimate.model_validate(checkpoint['payload'])
        expected,_=specification(result,workflow.input_data,spec.project_name)
    expected=expected.model_copy(update={'revision':revision.number})
    if (canonical_hash(expected.model_dump(mode='json'))!=revision.spec_hash or
            canonical_hash(revision.specification)!=revision.spec_hash or revision.verified_by or
            revision.verified_spec_hash or spec.measured_dimensions_confirmed or spec.dimension_provenance!='estimated'):
        raise AIServiceError('ESTIMATE_INTEGRITY_FAILED','Estimated revision changed or incorrectly claims verification')
    return EstimatedCADContext(revision.id,revision.spec_hash,spec)


def queue_estimated_cad(workflow_id,db,config):
    from .jobs import dispatch
    try:
        with db.sessions.begin() as session:
            workflow=session.get(AgentWorkflow,workflow_id,with_for_update=True)
            if workflow.mode not in ('ESTIMATE','PHOTO','PIPELINE','MODIFY','IMAGE_TO_CAD') or workflow.state!='ESTIMATED_READY': return
            if workflow.job_id: return
            project=session.get(Project,workflow.project_id,with_for_update=True)
            number=session.scalar(update(Project).where(Project.id==project.id).values(
                revision_counter=Project.revision_counter+1).returning(Project.revision_counter))
            spec=CabinetSpec.model_validate(workflow.result['specification']).model_copy(update={'revision':number})
            payload=spec.model_dump(mode='json')
            revision=Revision(project_id=project.id,number=number,specification=payload,spec_hash=canonical_hash(payload),
                created_by=workflow.created_by,state='DRAFT',source_workflow_id=workflow.id,
                value_provenance=workflow.result['value_provenance'])
            session.add(revision);session.flush()
            transition(revision,'SPECIFICATION_PENDING',session,workflow.created_by)
            workflow.revision_id=revision.id
            validate_estimated_revision(revision,session)
            job=Job(revision_id=revision.id)
            session.add(job);session.flush()
            revision.active_job_id=job.id
            workflow.job_id,workflow.state,workflow.stage,workflow.progress=job.id,'CAD_QUEUED','building_estimated_cad',65
            workflow.updated_at=utcnow()
            audit(session,workflow.created_by,'estimated_review_cad_queued',job.id,workflow_id=workflow.id,revision_id=revision.id)
            job_id=job.id
        dispatch(job_id,db,config)
    except Exception:
        with db.sessions.begin() as session:
            workflow=session.get(AgentWorkflow,workflow_id,with_for_update=True)
            workflow.state='CAD_FAILED'
            workflow.error=dict(code='ESTIMATED_CAD_QUEUE_FAILED',message='Estimated CAD could not be queued; retry the CAD job.',retryable=True)
            if workflow.job_id:
                job=session.get(Job,workflow.job_id)
                if job and job.state=='QUEUED': job.state,job.error='FAILED','CAD worker dispatch failed'
        raise
