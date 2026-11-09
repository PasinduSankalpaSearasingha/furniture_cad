import argparse
import getpass
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from sqlalchemy import select
from .config import Settings
from .database import Database, User, Job, AgentWorkflow, AgentEvent, audit
from .auth import hash_password
from .models import CabinetSpec
from .pipeline import build_package, safe_directory


def main():
    parser = argparse.ArgumentParser(description='Cabinet engineering review administration')
    sub = parser.add_subparsers(dest='command',required=True)
    admin = sub.add_parser('create-admin')
    admin.add_argument('--username',required=True)
    example = sub.add_parser('build-example')
    example.add_argument('--spec',type=Path,default=Path('examples/cabinet.json'))
    example.add_argument('--output',type=Path,required=True)
    cleanup = sub.add_parser('cleanup-failed')
    cleanup.add_argument('--older-than-days',type=int,default=7)
    recover = sub.add_parser('recover-jobs')
    recover.add_argument('--stale-hours',type=int,default=24)
    recover_agents = sub.add_parser('recover-agent-workflows')
    recover_agents.add_argument('--stale-hours',type=int,default=24)
    args = parser.parse_args()
    config = Settings()
    if args.command == 'build-example':
        spec = CabinetSpec.model_validate_json(args.spec.read_text(encoding='utf-8'))
        result = build_package(spec,args.output.resolve())
        print(json.dumps(dict(output=str(args.output.resolve()),parts=result['parts'],
            validation_status=result['validation']['status'],findings=len(result['validation']['findings']),
            package_hash=result['package_hash'],manifest_hash=result['manifest_hash']),indent=2))
        return
    db = Database(config.database_url)
    with db.sessions.begin() as session:
        if args.command == 'create-admin':
            password = getpass.getpass('Admin password (12+ characters): ')
            if len(password) < 12: raise SystemExit('Password must contain at least 12 characters')
            if password != getpass.getpass('Confirm password: '): raise SystemExit('Passwords differ')
            user = User(username=args.username,role='admin',password_hash=hash_password(password))
            session.add(user)
            session.flush()
            audit(session,user.id,'admin_bootstrapped',user.id)
            print(f'Created admin {user.username}')
        elif args.command == 'recover-jobs':
            if args.stale_hours < 1: raise SystemExit('Stale threshold must be at least one hour')
            cutoff = (datetime.now(timezone.utc)-timedelta(hours=args.stale_hours)).isoformat()
            for job in session.scalars(select(Job).where(Job.state.in_(['QUEUED','RUNNING']),Job.updated_at<cutoff).with_for_update()):
                job.state,job.stage = 'FAILED','recovered_stale'
                job.error = 'Stale job recovered by administrator; retry creates a new job'
                audit(session,'cli','stale_job_recovered',job.id)
                print(f'Recovered stale job {job.id}')
        elif args.command == 'recover-agent-workflows':
            if args.stale_hours < 1: raise SystemExit('Stale threshold must be at least one hour')
            cutoff = (datetime.now(timezone.utc)-timedelta(hours=args.stale_hours)).isoformat()
            rows = session.scalars(select(AgentWorkflow).where(AgentWorkflow.state.in_(['QUEUED','RUNNING','CAD_QUEUED']),
                AgentWorkflow.updated_at<cutoff).with_for_update())
            for workflow in rows:
                job = session.get(Job,workflow.job_id) if workflow.job_id else None
                if workflow.state=='CAD_QUEUED' and job and job.state not in ('SUCCEEDED','FAILED'): continue
                workflow.state = 'CAD_FAILED' if job and job.state=='FAILED' else 'FAILED'
                workflow.lease_token,workflow.updated_at = None,datetime.now(timezone.utc).isoformat()
                workflow.error = dict(code='STALE_WORKFLOW_RECOVERED',message='Administrator recovered an interrupted workflow; resume explicitly',retryable=True)
                if job and job.state=='SUCCEEDED': workflow.attempts=0
                session.add(AgentEvent(workflow_id=workflow.id,event='stale_workflow_recovered',stage=workflow.stage,details={}))
                audit(session,'cli','agent_workflow_recovered',workflow.id)
                print(f'Recovered workflow {workflow.id}')
        elif args.command == 'cleanup-failed':
            if args.older_than_days < 1: raise SystemExit('Retention must be at least one day')
            cutoff = (datetime.now(timezone.utc)-timedelta(days=args.older_than_days)).isoformat()
            for job in session.scalars(select(Job).where(Job.state=='FAILED',Job.updated_at<cutoff)):
                target = safe_directory(config.storage,job.id)
                if target.exists():
                    # safe_directory verifies the resolved absolute path is a direct
                    # child of the configured output root and rejects symlinks.
                    shutil.rmtree(target)
                    audit(session,'cli','failed_artifacts_cleaned',job.id)
                    print(f'Cleaned failed job {job.id}')
    db.engine.dispose()


if __name__ == '__main__': main()
