"""Export a stopped workflow's saved CAD as a new draft, with no AI requests."""
import argparse
import copy
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workflow',required=True)
    parser.add_argument('--patch',type=Path,help='Optional locally reviewed EngineeringRepairPatch')
    args=parser.parse_args()
    from sqlalchemy import select,func
    from app.config import Settings
    from app.database import Database,AgentWorkflow,Project,AIInvocation,audit
    from app.pipeline import canonical_hash
    from app.photo_schema import PhotoDesign
    from app.engineering_schema import EngineeringReview,EngineeringAssembly
    from app.engineering_repairs import EngineeringRepairPatch,merge_repair
    from app.geometry_repairs import resolve_repaired_geometry
    from app.engineering_cad import geometry_checks,check_supplied_dimensions
    from app.agents import orchestrator
    config=Settings(ai_enabled=False)
    db=Database(config.database_url)
    freeze=lambda payload:dict(payload=payload,sha256=canonical_hash(payload))
    try:
        with db.sessions() as session:
            source=session.get(AgentWorkflow,args.workflow)
            if not source or source.mode!='IMAGE_TO_CAD' or source.state not in ('FAILED','BUDGET_REACHED','REVIEW_REQUIRED'):
                raise ValueError('Select a stopped Engineering CAD workflow')
            if source.input_hash!=canonical_hash(source.input_data):raise ValueError('Source inputs changed')
            for checkpoint in source.checkpoints.values():
                if checkpoint['sha256']!=canonical_hash(checkpoint['payload']):raise ValueError('Source checkpoint changed')
            candidates=[entry['payload']['plan'] for key,entry in source.checkpoints.items() if key.startswith('engineering_rejected_')]
            drafts=[entry['payload'] for key,entry in source.checkpoints.items() if key.startswith('engineering_exportable_draft_')]
            best=next((entry for entry in reversed(drafts) if entry['draft']['fully_connected']),drafts[-1] if drafts else None)
            saved=source.checkpoints.get('engineering_plan')
            raw_plan=saved['payload'] if saved else best['plan'] if best else candidates[-1] if candidates else None
            if raw_plan is None:raise ValueError('No saved CAD plan exists; reference and measurement output remains available')
            plan=PhotoDesign.model_validate(raw_plan)
            raw=copy.deepcopy(source.input_data);project_id=source.project_id;owner=source.created_by
            name=session.get(Project,project_id).name
            dimensions=copy.deepcopy(source.checkpoints['engineering_dimensions'])
        if args.patch:
            patch=EngineeringRepairPatch.model_validate_json(args.patch.read_text(encoding='utf-8'))
            plan=merge_repair(plan.model_dump(mode='json'),patch,raw.get('known_values',[]))
        key='available-recovery-'+canonical_hash(dict(source=args.workflow,plan=plan.model_dump(mode='json')))[:36]
        with db.sessions() as session:
            existing=session.scalar(select(AgentWorkflow).where(AgentWorkflow.project_id==project_id,AgentWorkflow.idempotency_key==key))
            if existing:
                print(json.dumps(dict(workflow_id=existing.id,status=existing.state,job_id=existing.job_id,reused=True)),flush=True)
                return
        print(json.dumps(dict(stage='validating_saved_cad',api_requests=0)),flush=True)
        plan,_,_,build,_,_=resolve_repaired_geometry(plan,raw,name)
        checks=geometry_checks(build);check_supplied_dimensions(build,raw.get('known_values',[]))
        for field in ('engineering_edit','engineering_reexport_of','engineering_local_completion','client_request_hash','ai_budget'):
            raw.pop(field,None)
        raw.update(engineering_manual_reconstruction=dict(source_workflow_id=args.workflow,
            origin='LOCAL_REFERENCE_RECONSTRUCTION',knowledge_source='SAVED_CAD_DRAFT_RECOVERY',
            ai_requests=0,visual_review_status='HUMAN_REVIEW_REQUIRED'),final_ai_correction=False)
        review=EngineeringReview(accepted=False,findings=['Saved CAD recovered locally. No final AI visual review was performed.'],
            limitations=['Review the reference match and unresolved assembly issues.'])
        assembly=EngineeringAssembly(furniture_type=plan.furniture_type,assumptions=plan.assumptions,
            parts=[dict(part_id=p.part_id,label=p.label,description='Recovered saved CAD solid',visible_evidence=p.evidence) for p in plan.parts])
        checkpoints={name:freeze(value.model_dump(mode='json')) for name,value in dict(engineering_plan=plan,
            engineering_assembly=assembly,engineering_critic_0=review,engineering_visual_review_0=review).items()}
        checkpoints['engineering_dimensions']=dimensions
        checkpoints['engineering_cost_stop']=freeze(dict(code='CAD_REPAIR_LIMIT_REACHED',
            message='Saved CAD was recovered locally after automatic repairs stopped. No additional AI requests were made.'))
        with db.sessions.begin() as session:
            workflow=AgentWorkflow(project_id=project_id,created_by=owner,mode='IMAGE_TO_CAD',input_data=raw,
                input_hash=canonical_hash(raw),model_config=dict(vision='LOCAL_RECOVERY',design='LOCAL_RECOVERY',review='HUMAN_REVIEW_REQUIRED'),
                idempotency_key=key,checkpoints=checkpoints)
            session.add(workflow);session.flush();workflow_id=workflow.id
            audit(session,owner,'available_cad_recovery_queued',workflow_id,source_workflow_id=args.workflow,ai_requests=0)
        def forbidden(*a,**k):raise AssertionError('Local recovery must not initialize an AI client')
        orchestrator.ResponsesGateway=forbidden
        print(json.dumps(dict(stage='exporting_available_cad',workflow_id=workflow_id,api_requests=0)),flush=True)
        orchestrator.run_workflow(workflow_id,db,config)
        with db.sessions() as session:
            workflow=session.get(AgentWorkflow,workflow_id)
            calls=session.scalar(select(func.count()).select_from(AIInvocation).where(AIInvocation.workflow_id==workflow_id))
            if calls or workflow.state!='REVIEW_REQUIRED' or not (workflow.result or {}).get('delivery'):
                raise RuntimeError('Recovery export did not complete: '+json.dumps(workflow.error))
            print(json.dumps(dict(status=workflow.state,workflow_id=workflow_id,project_id=project_id,job_id=workflow.job_id,
                revision_id=workflow.revision_id,api_requests=calls,connected=checks['furniture_items_connected'],
                package=str(config.storage/workflow.result['delivery']['directory']/'furniture_complete_package.zip'))),flush=True)
    finally:db.engine.dispose()


if __name__=='__main__':main()
