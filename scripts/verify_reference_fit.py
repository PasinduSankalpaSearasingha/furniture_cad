"""Read-only independent audit of a completed local-fit workflow and exported STEP."""
import argparse
import json
import sys
from pathlib import Path
from zipfile import ZipFile
import cadquery as cq

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config import Settings
from app.database import Database,Revision,AgentWorkflow,Job,AIInvocation
from app.estimated_workflow import validate_estimated_revision
from app.pipeline import canonical_hash,file_hash,safe_directory,verify_package
from app.geometry import exact_bounds
from sqlalchemy import select


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workflow_id');parser.add_argument('--output',type=Path)
    args=parser.parse_args();config=Settings();db=Database(config.database_url)
    try:
        with db.sessions() as session:
            workflow=session.get(AgentWorkflow,args.workflow_id)
            assert workflow and workflow.mode=='MODIFY' and workflow.state=='REVIEW_REQUIRED'
            assert workflow.input_data.get('reference_fit') is not None
            assert not session.scalar(select(AIInvocation).where(AIInvocation.workflow_id==workflow.id).limit(1))
            revision=session.get(Revision,workflow.revision_id);job=session.get(Job,workflow.job_id)
            validate_estimated_revision(revision,session)
            source=session.get(Revision,workflow.input_data['source_revision_id'])
            assert source.spec_hash==workflow.input_data['source_specification_hash']==canonical_hash(source.specification)
            report=workflow.result['reference_fidelity']['numeric_fit']
            assert report['source_plan_sha256']==canonical_hash(source.specification['photo_design'])
            assert report['measured_plan_sha256']==canonical_hash(revision.specification['photo_design'])
            directory=safe_directory(config.storage,job.id);verify_package(directory,job,revision.spec_hash)
            with ZipFile(directory/'design_review_package.zip') as archive:assert archive.testzip() is None
            shape=cq.importers.importStep(str(directory/'cabinet.step'));solids=shape.solids().vals()
            assert len(solids)==len(revision.specification['photo_design']['parts'])
            assert all(s.isValid() and s.Volume()>0 for s in solids)
            bounds=exact_bounds(cq.Compound.makeCompound(solids));dimensions=[bounds.xlen,bounds.ylen,bounds.zlen]
            for key,actual in zip(('width_mm','depth_mm','height_mm'),dimensions):
                assert abs(actual-revision.specification[key])<=max(2,revision.specification[key]*.01)
            artifacts=safe_directory(config.storage,report['directory'])
            for filename,digest in report['files'].items():assert file_hash(safe_directory(artifacts,filename))==digest
            result=dict(workflow_id=workflow.id,revision_id=revision.id,source_revision_id=source.id,job_id=job.id,
                valid_positive_solids=len(solids),dimensions_mm=dimensions,geometry_changed=report['geometry_changed'],
                silhouette_scores=[dict(initial_camera=v['before_camera_fit']['silhouette_iou'],
                    fitted_camera=v['before_geometry_fit']['silhouette_iou'],final_geometry=v['after_geometry_fit']['silhouette_iou']) for v in report['views']],
                specification_sha256=revision.spec_hash,source_revision_unchanged=True,
                step_sha256=file_hash(directory/'cabinet.step'),package_sha256=file_hash(directory/'design_review_package.zip'),
                ai_calls=0,exact_reconstruction_verified=False,approved_for_manufacturing=False)
            if args.output:args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
            print(json.dumps(result,indent=2))
    finally:db.engine.dispose()


if __name__=='__main__':main()
