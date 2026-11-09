import logging
from celery import Celery
from sqlalchemy import update
from .config import Settings
from .database import Database, Job, Revision, utcnow, audit
from .models import CabinetSpec
from .pipeline import build_package, canonical_hash, safe_directory
from .workflow import transition
from .logging_config import configure_logging

configure_logging()
logger = logging.getLogger('furniture.jobs')
settings = Settings()
celery_app = Celery('furniture',broker=settings.broker_url)
celery_app.conf.update(task_serializer='json',accept_content=['json'],result_serializer='json',
                       task_acks_late=True,worker_prefetch_multiplier=1,
                       broker_connection_retry_on_startup=True,task_ignore_result=True,
                       task_soft_time_limit=1500,task_time_limit=1800)


def run_job(job_id, db, config):
    # Compare-and-set claim prevents a redelivered task from overwriting artifacts.
    with db.sessions.begin() as session:
        claimed = session.execute(update(Job).where(Job.id==job_id,Job.state=='QUEUED').values(
            state='RUNNING',stage='starting',updated_at=utcnow())).rowcount
        if not claimed: return
        job = session.get(Job,job_id)
        revision = session.get(Revision,job.revision_id)
        spec = revision.specification
        expected_hash = revision.spec_hash
        revision_id = revision.id
        source_workflow_id = revision.source_workflow_id
    def progress(stage, percent):
        with db.sessions.begin() as session:
            job = session.get(Job,job_id)
            job.stage,job.progress,job.updated_at = stage,percent,utcnow()
            if source_workflow_id:
                from .database import AgentWorkflow
                workflow=session.get(AgentWorkflow,source_workflow_id)
                if workflow and workflow.mode=='IMAGE_TO_CAD' and workflow.state=='CAD_QUEUED' and workflow.job_id==job_id:
                    workflow.stage,workflow.progress,workflow.updated_at=stage,65+round(percent*.15),utcnow()
    try:
        if canonical_hash(spec) != expected_hash: raise ValueError('Stored specification integrity failure')
        context = None
        if source_workflow_id:
            from .agents.cad_agent import validate_design_specification
            from .ai.schemas import CADGenerationRequest
            with db.sessions() as session:
                from .database import AgentWorkflow
                workflow=session.get(AgentWorkflow,source_workflow_id)
                if workflow and workflow.mode in ('ESTIMATE','PHOTO','PIPELINE','MODIFY','IMAGE_TO_CAD'):
                    from .estimated_workflow import validate_estimated_revision
                    context=validate_estimated_revision(session.get(Revision,revision_id),session)
                else:
                    context = validate_design_specification(CADGenerationRequest(revision_id=revision_id,
                        specification_hash=expected_hash,template_id='cabinet.v1'),session)
        if context:
            result = build_package(CabinetSpec.model_validate(spec),safe_directory(config.storage,job_id),progress,verified_context=context)
        else:
            result = build_package(CabinetSpec.model_validate(spec),safe_directory(config.storage,job_id),progress)
        with db.sessions.begin() as session:
            job = session.get(Job,job_id)
            if job.state != 'RUNNING': return
            revision = session.get(Revision,revision_id,with_for_update=True)
            job.validation = result['validation']
            job.package_hash,job.manifest_hash = result['package_hash'],result['manifest_hash']
            job.state,job.stage,job.progress,job.updated_at = 'SUCCEEDED','complete',100,utcnow()
            if revision.active_job_id == job_id:
                transition(revision,'CAD_GENERATED',session,'worker')
                transition(revision,'VALIDATION_PENDING',session,'worker')
                transition(revision,result['validation']['status'],session,'worker')
            audit(session,'worker','job_completed',job_id,revision_id=revision_id)
        if source_workflow_id:
            try:
                from .agents.orchestrator import notify_cad_completed
                notify_cad_completed(job_id,db,config)
            except Exception:
                logger.exception('post_cad_agent_dispatch_failed',extra={'job_id':job_id})
                from .database import AgentWorkflow
                with db.sessions.begin() as session:
                    workflow = session.get(AgentWorkflow,source_workflow_id)
                    workflow.state = 'FAILED'
                    workflow.error = dict(code='POST_CAD_REVIEW_DISPATCH_FAILED',message='CAD succeeded; retry the workflow to resume AI review',retryable=True)
    except Exception:
        logger.exception('cad_job_failed',extra={'job_id':job_id})
        with db.sessions.begin() as session:
            job = session.get(Job,job_id)
            job.state,job.stage,job.updated_at = 'FAILED','failed',utcnow()
            job.error = 'CAD/export job failed. Consult the server log using the job ID; retry creates a new job.'
            audit(session,'worker','job_failed',job_id,revision_id=revision_id)
            if source_workflow_id:
                from .database import AgentWorkflow, AgentEvent
                workflow = session.get(AgentWorkflow,source_workflow_id,with_for_update=True)
                if workflow and workflow.job_id==job_id:
                    workflow.state = 'CAD_FAILED'
                    workflow.error = dict(code='CAD_GENERATION_FAILED',message=job.error,retryable=True)
                    session.add(AgentEvent(workflow_id=workflow.id,event='cad_failed',stage='cad',details={'job_id':job_id}))


@celery_app.task(name='furniture.generate')
def generate_task(job_id):
    config = Settings()
    db = Database(config.database_url)
    try: run_job(job_id,db,config)
    finally: db.engine.dispose()


@celery_app.task(name='furniture.agent_workflow')
def agent_workflow_task(workflow_id):
    from .agents.orchestrator import run_workflow
    config = Settings()
    db = Database(config.database_url)
    try: run_workflow(workflow_id,db,config)
    finally: db.engine.dispose()


def dispatch(job_id, db, config):
    if config.executor == 'eager':
        run_job(job_id,db,config)
    elif config.executor == 'local':
        from .local_executor import submit_local
        submit_local(config,run_job,job_id,db,config)
    else:
        generate_task.apply_async(args=[job_id],retry=True,
                                  retry_policy={'max_retries':2,'interval_start':0,'interval_step':0.2,'interval_max':0.5})


