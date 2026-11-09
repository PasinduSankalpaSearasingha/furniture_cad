"""Rebuild saved rejected CAD locally, without retrying or paying for a workflow."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import cadquery as cq
from sqlalchemy import select
from app.config import Settings
from app.database import Database,AgentWorkflow,Project,AIInvocation
from app.photo_schema import PhotoDesign
from app.pipeline import canonical_hash
from app.geometry_repairs import resolve_repaired_geometry
from app.engineering_cad import geometry_checks,check_supplied_dimensions,verify_solid_step,render_preview


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--workflow',required=True)
    parser.add_argument('--output',default='outputs/local-curve-repair');args=parser.parse_args()
    config=Settings();db=Database(config.database_url);root=Path(args.output);root.mkdir(parents=True,exist_ok=True)
    try:
        with db.sessions() as session:
            source=session.get(AgentWorkflow,args.workflow)
            if not source or canonical_hash(source.input_data)!=source.input_hash:raise ValueError('Saved workflow inputs failed verification')
            candidates=[(key,cp) for key,cp in source.checkpoints.items() if key.startswith('engineering_rejected_')]
            if not candidates:raise ValueError('No saved rejected geometry is available')
            key,checkpoint=candidates[-1]
            if canonical_hash(checkpoint['payload'])!=checkpoint['sha256']:raise ValueError('Saved candidate integrity failed')
            before_invocations=list(session.scalars(select(AIInvocation.id).where(AIInvocation.workflow_id==source.id)))
            inputs=source.input_data;name=session.get(Project,source.project_id).name
        original=PhotoDesign.model_validate(checkpoint['payload']['plan'])
        print('Rebuilding saved plan locally...',flush=True)
        plan,spec,values,build,adjustments,repairs=resolve_repaired_geometry(original,inputs,name)
        from app.geometry import exact_bounds
        envelopes={}
        for pid,work in build.shapes.items():
            work.val().exportBrep(str(root/(pid+'.brep')))
            bound=exact_bounds(work.val())
            envelopes[pid]={axis+side:getattr(bound,axis+side) for axis in 'xyz' for side in ('min','max')}
        (root/'component-envelopes.json').write_text(json.dumps(envelopes,indent=2),encoding='utf-8')
        print(f'Built {len(build.shapes)} components; checking dimensions and contacts...',flush=True)
        dimension_checks=check_supplied_dimensions(build,inputs.get('known_values',[]))
        checks=geometry_checks(build);checks['supplied_dimensions']=dimension_checks
        assert checks['valid_closed_solids']
        connected=checks.get('furniture_items_connected',checks['assembly_connected'])
        print('Connection check: '+('passed' if connected else 'saved plan still contains disconnected furniture components'),flush=True)
        assert [v.model_dump(mode='json') for v in plan.values]==[v.model_dump(mode='json') for v in original.values]
        for before,after in zip(original.parts,plan.parts):
            assert before.sections==after.sections,'Local curve repair must retain every original declared profile'
        verified=[]
        for report in repairs:
            if report['kind']!='LOCAL_LOFT_REBUILD':continue
            pid=report['part_id'];solid=build.shapes[pid].val();path=root/(pid+'-repaired.step')
            print(f'Verifying {pid} STEP round-trip...',flush=True)
            cq.exporters.export(solid,str(path));_,proof=verify_solid_step(solid,path)
            verified.append(dict(part_id=pid,repair=report,step_verification=proof))
        with db.sessions() as session:
            after_invocations=list(session.scalars(select(AIInvocation.id).where(AIInvocation.workflow_id==args.workflow)))
        assert before_invocations==after_invocations
        for label,az,el in [('front',-90,0),('reference',-110,15)]:
            (root/(label+'.png')).write_bytes(render_preview(build,az,el,size=(1400,900)))
        proof=dict(workflow_id=args.workflow,source_checkpoint=key,original_error=checkpoint['payload']['message'],
            valid_closed_solids=True,all_furniture_items_connected=connected,part_count=len(build.shapes),
            all_parameters_retained=True,all_original_loft_sections_retained=True,checks=checks,
            local_loft_repairs=verified,additional_ai_requests=0,workflow_not_resubmitted=True)
        (root/'verification.json').write_text(json.dumps(proof,indent=2),encoding='utf-8')
        (root/'resolved-plan.json').write_text(json.dumps(plan.model_dump(mode='json'),indent=2),encoding='utf-8')
        print(json.dumps(dict(workflow_id=args.workflow,part_count=len(build.shapes),
            repaired_parts=[v['part_id'] for v in verified],all_parameters_retained=True,
            all_items_connected=connected,step_roundtrips_passed=True,additional_ai_requests=0)),flush=True)
    finally:db.engine.dispose()


if __name__=='__main__':main()
