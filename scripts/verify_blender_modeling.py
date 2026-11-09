"""Read-only audit of frozen native Blender, CAD exports, hashes and AI usage."""
import argparse
import json
import sys
from pathlib import Path
from zipfile import ZipFile
import cadquery as cq
from sqlalchemy import select

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config import Settings
from app.database import Database,AgentWorkflow,Revision,Job,AIInvocation
from app.estimated_workflow import validate_estimated_revision
from app.pipeline import canonical_hash,file_hash,safe_directory,verify_package
from app.geometry import exact_bounds


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workflow_id');parser.add_argument('--output',type=Path)
    args=parser.parse_args();config=Settings();db=Database(config.database_url)
    try:
        with db.sessions() as session:
            workflow=session.get(AgentWorkflow,args.workflow_id)
            assert workflow and workflow.state=='REVIEW_REQUIRED'
            assert workflow.input_data.get('modeling_engine')=='blender'
            revision=session.get(Revision,workflow.revision_id);job=session.get(Job,workflow.job_id)
            validate_estimated_revision(revision,session)
            plan=revision.specification['photo_design'];model=workflow.result['blender_modeling']
            artifact=model['artifact'];report=artifact['report'];agreement=model['cad_export_agreement']
            assert artifact['plan_sha256']==canonical_hash(plan)==agreement['plan_sha256']
            assert report['source']=='NATIVE_BLENDER_GEOMETRY'
            assert all(p['closed'] and p['volume_mm3']>0 for p in report['components'])
            native=safe_directory(config.storage,artifact['directory'])
            for filename,digest in artifact['files'].items():
                assert file_hash(safe_directory(native,filename))==digest,filename
            with ZipFile(native/'blender_model_package.zip') as archive:
                assert archive.testzip() is None
                assert set(archive.namelist())==set(artifact['files'])-{'blender_model_package.zip'}
                import hashlib
                for name in archive.namelist():
                    assert hashlib.sha256(archive.read(name)).hexdigest()==artifact['files'][name]
            directory=safe_directory(config.storage,job.id);verify_package(directory,job,revision.spec_hash)
            with ZipFile(directory/'design_review_package.zip') as archive:assert archive.testzip() is None
            solids=cq.importers.importStep(str(directory/'cabinet.step')).solids().vals()
            assert len(solids)==len(plan['parts'])==report['component_count']==agreement['step_valid_solids']
            assert all(s.isValid() and s.Volume()>0 for s in solids)
            assert agreement['export_reopened'] and agreement['source_step_sha256']==file_hash(directory/'cabinet.step')
            assert agreement['native_glb_sha256']==artifact['files']['reconstruction.glb']
            assert not agreement['complete_surface_bound_verified'] and not model['approved_for_manufacturing']
            bounds=exact_bounds(cq.Compound.makeCompound(solids));dimensions=[bounds.xlen,bounds.ylen,bounds.zlen]
            for key,actual in zip(('width_mm','depth_mm','height_mm'),dimensions):
                assert abs(actual-revision.specification[key])<=max(2,revision.specification[key]*.01)
            source_id=workflow.input_data.get('source_revision_id')
            if source_id:
                source=session.get(Revision,source_id)
                assert source.spec_hash==canonical_hash(source.specification)==workflow.input_data['source_specification_hash']
            invocations=list(session.scalars(select(AIInvocation).where(AIInvocation.workflow_id==workflow.id)))
            if not model['ai_executed']:assert not invocations
            result=dict(workflow_id=workflow.id,revision_id=revision.id,source_revision_id=source_id,job_id=job.id,
                valid_positive_solids=len(solids),dimensions_mm=dimensions,specification_sha256=revision.spec_hash,
                plan_sha256=artifact['plan_sha256'],source_revision_unchanged=True if source_id else None,
                step_sha256=agreement['source_step_sha256'],native_glb_sha256=agreement['native_glb_sha256'],
                native_package_sha256=artifact['files']['blender_model_package.zip'],
                native_triangles=report['triangles'],device=report['device'],
                maximum_camera_projection_error_pixels=report['maximum_camera_projection_error_pixels'],
                photo_views=[dict(metrics=v['metrics'],mask_reviewed=v['mask_reviewed'],mask_usable=v['mask_usable']) for v in report['views']],
                cad_agreement_status=agreement['status'],sampled_max_surface_gap_mm=agreement['sampled_max_surface_gap_mm'],
                ai_executed=model['ai_executed'],ai_invocations=[dict(model=i.model,status=i.status,stage=i.stage,usage=i.usage) for i in invocations],
                exact_reconstruction_verified=False,approved_for_manufacturing=False)
            if args.output:args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
            print(json.dumps(result,indent=2))
    finally:db.engine.dispose()


if __name__=='__main__':main()
