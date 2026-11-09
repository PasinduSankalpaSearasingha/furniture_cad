"""Owner-authorized project deletion with bounded artifact cleanup and rollback."""
import logging
from pathlib import Path
import shutil
from uuid import UUID,uuid4

from fastapi import HTTPException
from sqlalchemy import delete,select
from app.database import (Project,Revision,Job,Approval,Release,ImageAsset,
    AgentWorkflow,AgentEvent,AIInvocation,audit)
from app.pipeline import safe_directory

logger=logging.getLogger('furniture.api')
ACTIVE_WORKFLOWS={'QUEUED','RUNNING','CAD_QUEUED'}


def project_artifacts(storage,jobs,workflows,images):
    """Use server-owned identifiers, never artifact paths supplied in model JSON."""
    root=Path(storage).resolve()
    job_ids={str(UUID(job.id)) for job in jobs};workflow_ids={str(UUID(w.id)) for w in workflows}
    exact=job_ids|{prefix+wid for wid in workflow_ids for prefix in ('photo_fidelity_','reference_fit_')}
    prefixes=tuple([prefix+wid+'_' for wid in workflow_ids for prefix in
        ('engineering_preview_','engineering_final_','delivery_','blender_','blender_model_','concept_')]+
        [jid+'_engineering_' for jid in job_ids]+['dimension_sheet_'+jid+'_v' for jid in job_ids])
    paths=[]
    if root.is_dir():
        for path in root.iterdir():
            if path.name in exact or prefixes and path.name.startswith(prefixes):
                checked=safe_directory(root,path.name)
                if checked!=path:raise ValueError('Artifact directory is redirected')
                paths.append(checked)
    if images:
        image_root=safe_directory(root,'images')
        if image_root!=root/'images':raise ValueError('Image directory is redirected')
        for image in images:
            path=safe_directory(image_root,str(UUID(image.id))+'.png')
            if path.exists():paths.append(path)
    return paths


class QuarantinedArtifacts:
    def __init__(self,storage,paths):
        self.root=Path(storage).resolve();self.moves=[]
        self.directory=safe_directory(self.root,'.deleting-project-'+uuid4().hex)
        self.directory.mkdir()
        try:
            for source in paths:
                source=source.resolve()
                relative=source.relative_to(self.root)
                if source==self.root:raise ValueError('Refusing to move the storage root')
                target=self.directory/relative
                if not target.resolve().is_relative_to(self.directory):raise ValueError('Unsafe deletion target')
                target.parent.mkdir(parents=True,exist_ok=True)
                source.rename(target);self.moves.append((source,target))
        except Exception:
            self.restore();raise

    def restore(self):
        for source,target in reversed(self.moves):
            if target.exists():target.rename(source)
        self.cleanup()

    def cleanup(self):
        # Verify the final absolute path before a recursive filesystem operation.
        target=self.directory.resolve()
        if target!=self.directory or target.parent!=self.root or not target.name.startswith('.deleting-project-'):
            raise ValueError('Unsafe deletion directory')
        if target.exists():shutil.rmtree(target)


def delete_project(db,config,project_id,user,project_access):
    staged=None
    try:
        with db.sessions.begin() as session:
            project=project_access(session,project_id,user,lock=True)
            if user.role!='admin' and project.owner_id!=user.id:
                raise HTTPException(403,'Only the project owner or an administrator can delete this design')
            revisions=list(session.scalars(select(Revision).where(Revision.project_id==project.id)))
            revision_ids=[r.id for r in revisions]
            jobs=list(session.scalars(select(Job).where(Job.revision_id.in_(revision_ids))))
            workflows=list(session.scalars(select(AgentWorkflow).where(AgentWorkflow.project_id==project.id)))
            workflow_ids=[w.id for w in workflows]
            if any(w.state in ACTIVE_WORKFLOWS for w in workflows) or any(j.state in ('QUEUED','RUNNING') for j in jobs):
                raise HTTPException(409,'Wait for generation to finish before deleting this design')
            images=list(session.scalars(select(ImageAsset).where(ImageAsset.project_id==project.id)))
            try:
                paths=project_artifacts(config.storage,jobs,workflows,images)
                staged=QuarantinedArtifacts(config.storage,paths)
            except (OSError,ValueError) as exc:
                raise HTTPException(409,'The design files could not be prepared for deletion; nothing was deleted') from exc
            # Delete dependent records in foreign-key order. The deletion audit
            # retains counts without retaining prompts or geometry.
            session.execute(delete(Release).where(Release.revision_id.in_(revision_ids)))
            session.execute(delete(Approval).where(Approval.revision_id.in_(revision_ids)))
            session.execute(delete(AgentEvent).where(AgentEvent.workflow_id.in_(workflow_ids)))
            session.execute(delete(AIInvocation).where(AIInvocation.workflow_id.in_(workflow_ids)))
            session.execute(delete(AgentWorkflow).where(AgentWorkflow.id.in_(workflow_ids)))
            session.execute(delete(Job).where(Job.revision_id.in_(revision_ids)))
            session.execute(delete(Revision).where(Revision.project_id==project.id))
            session.execute(delete(ImageAsset).where(ImageAsset.project_id==project.id))
            session.execute(delete(Project).where(Project.id==project.id))
            audit(session,user.id,'project_deleted',project.id,revisions=len(revisions),jobs=len(jobs),
                workflows=len(workflows),images=len(images),artifact_paths=len(paths))
    except Exception:
        if staged is not None:staged.restore()
        raise
    cleanup_pending=False
    try:staged.cleanup()
    except OSError:
        cleanup_pending=True
        logger.exception('project_deleted_cleanup_pending',extra={'project_id':str(project_id)})
    return dict(id=str(project_id),status='DELETED',cleanup_pending=cleanup_pending)
