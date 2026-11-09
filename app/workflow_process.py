"""Run native CAD outside the web interpreter while keeping the local queue bounded."""
from dataclasses import fields
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
from uuid import UUID,uuid4

logger=logging.getLogger('furniture.workflow_process')


def settings_payload(config):
    # Live executors contain locks and must never cross the process boundary.
    # API credentials remain in the inherited environment, never command arguments.
    payload={field.name:getattr(config,field.name) for field in fields(config) if field.init}
    payload['storage']=str(config.storage)
    return payload


def run_isolated_workflow(workflow_id,config):
    from app.database import Database,AgentWorkflow,utcnow,AgentEvent
    from app.ai.openai_client import AIServiceError
    from app.budget_drafts import reference_only_output
    workflow_id=str(UUID(str(workflow_id)));lease=str(uuid4())
    db=Database(config.database_url)
    try:
        with db.sessions() as session:
            row=session.get(AgentWorkflow,workflow_id)
            if not row or row.state!='QUEUED':return
            original_attempts,original_updated=row.attempts,row.updated_at
        directory=config.storage/'runtime'
        log_path=directory/f'workflow-{workflow_id}-{lease}.log'
        options=dict(creationflags=subprocess.CREATE_NO_WINDOW) if os.name=='nt' else dict(start_new_session=True)
        try:
            directory.mkdir(parents=True,exist_ok=True)
            with log_path.open('w',encoding='utf-8') as log:
                result=subprocess.run([sys.executable,'-m','app.workflow_worker',workflow_id,lease],
                    input=json.dumps(settings_payload(config)),text=True,stdout=log,stderr=subprocess.STDOUT,
                    cwd=str(Path(__file__).resolve().parents[1]),check=False,**options)
            if result.returncode==0:return
        except OSError:
            logger.exception('isolated_workflow_start_failed',extra={'workflow_id':workflow_id})
        logger.error('isolated_workflow_stopped',extra={'workflow_id':workflow_id,'worker_log':log_path.name})
        with db.sessions.begin() as session:
            row=session.get(AgentWorkflow,workflow_id,with_for_update=True)
            owned=row and ((row.state=='RUNNING' and row.lease_token==lease) or
                (row.state=='QUEUED' and row.attempts==original_attempts and row.updated_at==original_updated))
            if not owned:return
            error=AIServiceError('AGENT_WORKFLOW_FAILED',
                'The background CAD worker stopped. Available work was saved; resume the saved CAD to continue.',True)
            try:
                row.result=reference_only_output(row,error)
                row.state,row.error,row.stage='REVIEW_REQUIRED',None,'available_output_saved'
            except AIServiceError as integrity:
                row.state,row.error='FAILED',integrity.as_dict()
            row.lease_token=None;row.updated_at=utcnow()
            session.add(AgentEvent(workflow_id=workflow_id,event='isolated_worker_stopped',stage=row.stage,
                details=dict(worker_log=log_path.name,available_output=row.error is None)))
    finally:db.engine.dispose()
