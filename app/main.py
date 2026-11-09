import hashlib
import json
import logging
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4
from typing import Literal
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from sqlalchemy import select, update, text as sql_text
from sqlalchemy.exc import IntegrityError
from pydantic import Field
from .models import CabinetSpec, MaterialProfile, HardwareProfile, JointProfile, StrictModel
from .config import Settings
from .database import Database, User, Token, Project, Revision, Job, Approval, Release, CatalogEntry, AuditLog, audit, commit_response
from .auth import current_user, require_role, hash_password, verify_password, issue_token, bearer, local_workspace_user, require_same_origin, WORKSPACE_COOKIE
from .pipeline import canonical_hash, safe_directory, verify_package, file_hash
from .workflow import transition, ensure_approvable
from .jobs import dispatch
from .logging_config import configure_logging

logger = logging.getLogger('furniture.api')


class Login(StrictModel):
    username: str = Field(min_length=1,max_length=80)
    password: str = Field(min_length=1,max_length=256)


class CreateUser(Login):
    password: str = Field(min_length=12,max_length=256)
    role: Literal['admin','designer','operator','viewer']


class CreateProject(StrictModel):
    name: str = Field(min_length=1,max_length=120)
    reviewer_ids: list[UUID] = Field(default_factory=list,max_length=20)


class Review(StrictModel):
    decision: Literal['APPROVED','CHANGES_REQUESTED']
    comment: str = Field(min_length=1,max_length=5000)


def create_app(config: Settings | None = None):
    configure_logging()
    config = config or Settings()
    @asynccontextmanager
    async def lifespan(api):
        try: yield
        finally:
            if config._local_executor is not None:
                await run_in_threadpool(config._local_executor.shutdown)
            if config._render_executor is not None:
                await run_in_threadpool(config._render_executor.shutdown)
    api = FastAPI(title='Furniture Manufacturing Engineering Review',version='0.3.0',lifespan=lifespan)
    api.state.settings = config
    api.state.db = Database(config.database_url)
    config.storage.mkdir(parents=True,exist_ok=True)
    public_directory = Path(__file__).resolve().parent / 'static'
    api.mount('/assets', StaticFiles(directory=public_directory), name='workspace-assets')
    studio_directory=Path(__file__).resolve().parent/'studio_dist'
    api.mount('/studio-assets',StaticFiles(directory=studio_directory,check_dir=False),name='react-studio-assets')

    @api.get('/',include_in_schema=False)
    @api.get('/studio',include_in_schema=False)
    def react_studio():
        if not (studio_directory/'index.html').is_file():
            raise HTTPException(503,'Build the React frontend: cd frontend and run npm install followed by npm run build')
        return FileResponse(studio_directory/'index.html',media_type='text/html',headers={
            'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self' blob:; worker-src 'self' blob:; frame-src 'self' blob:; font-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'",
            'Referrer-Policy':'same-origin'})

    @api.middleware('http')
    async def request_limits(request: Request, call_next):
        request.state.request_id = str(uuid4())
        start = time.monotonic()
        try:
            length = int(request.headers.get('content-length','0'))
        except ValueError:
            return JSONResponse(status_code=400,content={'error':{'code':'INVALID_LENGTH','message':'Invalid Content-Length'}})
        if length < 0 or length > config.max_request_bytes:
            return JSONResponse(status_code=413,content={'error':{'code':'REQUEST_TOO_LARGE','message':'Request exceeds 2 MiB'}})
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > config.max_request_bytes:
                return JSONResponse(status_code=413,content={'error':{'code':'REQUEST_TOO_LARGE','message':'Request exceeds 2 MiB'}})
        request._body = bytes(body)
        response = await call_next(request)
        response.headers['X-Request-ID'] = request.state.request_id
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Cache-Control'] = 'no-store'
        logger.info(json.dumps(dict(event='http_request',request_id=request.state.request_id,
            method=request.method,path=request.url.path,status=response.status_code,
            duration_ms=round((time.monotonic()-start)*1000))))
        return response

    @api.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse(status_code=exc.status_code,headers=exc.headers,
            content={'error':{'code':f'HTTP_{exc.status_code}','message':exc.detail,
                              'request_id':getattr(request.state,'request_id',None)}})

    @api.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        # Validation responses intentionally omit original input, including passwords.
        details = [{'loc':list(e['loc']),'message':e['msg'],'type':e['type']} for e in exc.errors()]
        return JSONResponse(status_code=422,content={'error':{'code':'INVALID_INPUT','message':'Input validation failed','details':details}})

    @api.exception_handler(IntegrityError)
    async def conflict(request, exc):
        return JSONResponse(status_code=409,content={'error':{'code':'CONFLICT','message':'Duplicate or conflicting record'}})

    @api.exception_handler(Exception)
    async def server_error(request, exc):
        logger.exception('request_failed',extra={'request_id':getattr(request.state,'request_id',None)})
        return JSONResponse(status_code=500,content={'error':{'code':'INTERNAL_ERROR','message':'Request failed; consult server logs with the request ID',
                                    'request_id':getattr(request.state,'request_id',None)}})

    def db_session():
        with api.state.db.sessions.begin() as session: yield session

    def project_access(session, project_id, user, lock=False):
        project = session.get(Project,str(project_id),with_for_update=lock,populate_existing=lock)
        if not project or (user.role != 'admin' and user.id != project.owner_id and user.id not in project.reviewer_ids):
            raise HTTPException(404,'Project not found')
        return project

    def revision_access(session, revision_id, user, lock=False):
        revision = session.get(Revision,str(revision_id))
        if not revision: raise HTTPException(404,'Revision not found')
        project_access(session,revision.project_id,user,lock=lock)
        if lock:
            revision=session.get(Revision,str(revision_id),with_for_update=True,populate_existing=True)
            if not revision:raise HTTPException(404,'Revision not found')
        return revision

    def job_access(session, job_id, user):
        job = session.get(Job,str(job_id))
        if not job: raise HTTPException(404,'Job not found')
        revision = revision_access(session,job.revision_id,user)
        return job,revision

    def bind_catalogs(session, spec):
        # Resolve assignment references from DB into the frozen revision; client
        # assertions cannot upgrade an unapproved catalog record.
        mids = set(spec.material_assignments.values()) | {m.material_id for m in spec.material_profiles}
        profiles = []
        for mid in sorted(mids):
            entry = session.scalar(select(CatalogEntry).where(CatalogEntry.kind=='materials',CatalogEntry.reference==mid))
            if not entry: raise HTTPException(422,f'Material catalog entry required: {mid}')
            profiles.append(MaterialProfile.model_validate(entry.payload))
        hardware = []
        for supplied in spec.hardware:
            entry = session.scalar(select(CatalogEntry).where(CatalogEntry.kind=='hardware',CatalogEntry.reference==supplied.hardware_id))
            if not entry: raise HTTPException(422,f'Hardware catalog entry required: {supplied.hardware_id}')
            if supplied.model_dump(mode='json') != entry.payload: raise HTTPException(422,'Hardware snapshot differs from catalog')
            hardware.append(supplied)
        for joint in spec.joints:
            entry = session.scalar(select(CatalogEntry).where(CatalogEntry.kind=='joinery',CatalogEntry.reference==joint.profile.profile_id))
            if not entry or entry.payload != joint.profile.model_dump(mode='json'):
                raise HTTPException(422,'Joint profile must match a catalog entry')
        return spec.model_copy(update={'material_profiles':profiles,'hardware':hardware})

    def make_revision(session, project, supplied, user):
        spec = bind_catalogs(session,supplied)
        number = session.scalar(update(Project).where(Project.id==project.id).values(
            revision_counter=Project.revision_counter+1).returning(Project.revision_counter))
        spec = spec.model_copy(update={'revision':number})
        payload = spec.model_dump(mode='json')
        revision = Revision(project_id=project.id,number=number,specification=payload,
                            spec_hash=canonical_hash(payload),created_by=user.id,state='DRAFT')
        session.add(revision)
        session.flush()
        transition(revision,'SPECIFICATION_PENDING',session,user.id)
        audit(session,user.id,'revision_created',revision.id,spec_hash=revision.spec_hash,number=number)
        return revision

    def enqueue(session, revision, user):
        require_role(user,'admin','designer','operator')
        project_access(session,revision.project_id,user,lock=True)
        ai_workflow = None
        if revision.source_workflow_id:
            from .agents.cad_agent import validate_design_specification
            from .ai.schemas import CADGenerationRequest
            from .ai.openai_client import AIServiceError
            from .database import AgentWorkflow
            ai_workflow = session.get(AgentWorkflow,revision.source_workflow_id,with_for_update=True)
            try:
                if ai_workflow and ai_workflow.mode in ('ESTIMATE','PHOTO','PIPELINE','MODIFY','IMAGE_TO_CAD'):
                    from .estimated_workflow import validate_estimated_revision
                    validate_estimated_revision(revision,session)
                else:
                    validate_design_specification(CADGenerationRequest(revision_id=revision.id,
                        specification_hash=revision.spec_hash,template_id='cabinet.v1'),session)
            except AIServiceError as exc: raise HTTPException(409,dict(code=exc.code,message=exc.message))
            if (not ai_workflow or ai_workflow.revision_id!=revision.id or
                    ai_workflow.state not in ('SPECIFICATION_VERIFIED','ESTIMATED_READY','CAD_FAILED')):
                raise HTTPException(409,'AI workflow is not ready for a fresh CAD job; resume its review or create a new design revision')
        if revision.state not in ('SPECIFICATION_PENDING','SPECIFICATION_VERIFIED','VALIDATION_FAILED','DESIGNER_REVIEW'):
            raise HTTPException(409,'This revision cannot be regenerated; create a new revision for changes')
        old = session.get(Job,revision.active_job_id) if revision.active_job_id else None
        if old and old.state in ('QUEUED','RUNNING'):
            raise HTTPException(409,'A job is already queued or running for this revision')
        job = Job(revision_id=revision.id)
        session.add(job)
        session.flush()
        revision.active_job_id = job.id
        if ai_workflow:
            from .database import AgentEvent, utcnow
            ai_workflow.job_id,ai_workflow.state,ai_workflow.progress,ai_workflow.updated_at = job.id,'CAD_QUEUED',70,utcnow()
            ai_workflow.error = None
            session.add(AgentEvent(workflow_id=ai_workflow.id,event='cad_queued',stage='cad',
                details={'job_id':job.id,'revision_id':revision.id}))
        audit(session,user.id,'job_queued',job.id,revision_id=revision.id)
        return job

    def submit(job_id):
        try: dispatch(job_id,api.state.db,config)
        except Exception:
            logger.exception('job_dispatch_failed',extra={'job_id':job_id})
            with api.state.db.sessions.begin() as session:
                job = session.get(Job,job_id)
                job.state,job.stage,job.error = 'FAILED','dispatch_failed','Worker queue unavailable; retry when available'
                from .database import AgentWorkflow
                revision = session.get(Revision,job.revision_id)
                workflow = session.get(AgentWorkflow,revision.source_workflow_id) if revision.source_workflow_id else None
                if workflow and workflow.job_id==job.id:
                    workflow.state = 'CAD_FAILED'
                    workflow.error = dict(code='CAD_QUEUE_UNAVAILABLE',message=job.error,retryable=True)
            raise HTTPException(503,{'message':'Worker queue unavailable','job_id':job_id})

    def revision_json(r):
        return dict(id=r.id,project_id=r.project_id,revision=r.number,state=r.state,
                    spec_hash=r.spec_hash,active_job_id=r.active_job_id,specification=r.specification)

    def checked_directory(job, revision):
        if job.state != 'SUCCEEDED': raise HTTPException(409,'Job has no completed package')
        try:
            directory = safe_directory(config.storage,job.id)
            verify_package(directory,job,revision.spec_hash)
            return directory
        except (OSError,ValueError,KeyError): raise HTTPException(409,'Artifact integrity verification failed')

    @api.get('/health/live')
    def live(): return {'status':'ok'}

    @api.get('/api/v1/workspace/access')
    def workspace_access(request: Request):
        if not config.local_workspace: return dict(mode='authenticated',user=None)
        user = local_workspace_user(request)
        return dict(mode='local',user=dict(id=user.id,username='My workspace',role=user.role))

    @api.get('/health/ready')
    def ready():
        try:
            with api.state.db.sessions() as session:
                session.execute(select(User.id).limit(1))
            return {'database':'ok','cad_execution':config.executor}
        except Exception: raise HTTPException(503,'Database unavailable or migrations required')

    @api.post('/api/v1/auth/token')
    def login(data: Login, session=Depends(db_session)):
        user = session.scalar(select(User).where(User.username==data.username))
        # Run scrypt for nonexistent accounts as well.
        check = user.password_hash if user else api.state.dummy_password
        valid = verify_password(data.password,check)
        if not user or not user.active or not valid: raise HTTPException(401,'Invalid credentials')
        return commit_response(session,dict(access_token=issue_token(session,user,config.token_hours),token_type='bearer',expires_in=config.token_hours*3600))

    @api.post('/api/v1/workspace/session')
    def workspace_login(data: Login, request: Request, response: Response, session=Depends(db_session)):
        require_same_origin(request)
        result = login(data,session)
        response.set_cookie(WORKSPACE_COOKIE,result['access_token'],max_age=result['expires_in'],
            httponly=True,secure=request.url.scheme=='https',samesite='strict',path='/')
        return {'authenticated':True}

    @api.delete('/api/v1/workspace/session',status_code=204)
    def workspace_logout(request: Request, response: Response, session=Depends(db_session)):
        require_same_origin(request)
        raw=request.cookies.get(WORKSPACE_COOKIE)
        if raw:
            token=session.get(Token,hashlib.sha256(raw.encode()).hexdigest())
            if token:session.delete(token)
            session.commit()
        response.delete_cookie(WORKSPACE_COOKIE,path='/')

    @api.delete('/api/v1/auth/token',status_code=204)
    def logout(credentials=Depends(bearer),user=Depends(current_user),session=Depends(db_session)):
        if credentials is None: return
        token = session.get(Token,hashlib.sha256(credentials.credentials.encode()).hexdigest())
        if token: session.delete(token)
        session.commit()

    @api.get('/api/v1/auth/me')
    def me(user=Depends(current_user)): return dict(id=user.id,username=user.username,role=user.role)

    @api.post('/api/v1/users',status_code=201)
    def create_user(data: CreateUser,user=Depends(current_user),session=Depends(db_session)):
        require_role(user,'admin')
        record = User(username=data.username,password_hash=hash_password(data.password),role=data.role)
        session.add(record)
        session.flush()
        audit(session,user.id,'user_created',record.id,role=record.role)
        return commit_response(session,dict(id=record.id,username=record.username,role=record.role))

    @api.get('/api/v1/projects')
    def list_projects(user=Depends(current_user),session=Depends(db_session)):
        records = session.scalars(select(Project).order_by(Project.created_at.desc()).limit(500)).all()
        return [dict(id=p.id,name=p.name,owner_id=p.owner_id) for p in records
                if user.role=='admin' or p.owner_id==user.id or user.id in p.reviewer_ids]

    @api.post('/api/v1/projects',status_code=201)
    def create_project(data: CreateProject,user=Depends(current_user),session=Depends(db_session)):
        require_role(user,'admin','designer','operator')
        for rid in data.reviewer_ids:
            reviewer = session.get(User,str(rid))
            if not reviewer or not reviewer.active or reviewer.role not in ('designer','admin'):
                raise HTTPException(422,'Reviewers must be active designers')
        p = Project(name=data.name,owner_id=user.id,reviewer_ids=[str(v) for v in data.reviewer_ids])
        session.add(p)
        session.flush()
        audit(session,user.id,'project_created',p.id)
        return commit_response(session,dict(id=p.id,name=p.name))

    @api.post('/api/v1/projects/{project_id}/revisions',status_code=201)
    def create_revision(project_id: UUID,spec: CabinetSpec,user=Depends(current_user),session=Depends(db_session)):
        require_role(user,'admin','designer','operator')
        project = project_access(session,project_id,user,lock=True)
        return commit_response(session,revision_json(make_revision(session,project,spec,user)))

    @api.get('/api/v1/projects/{project_id}/revisions')
    def revisions(project_id: UUID,user=Depends(current_user),session=Depends(db_session)):
        project_access(session,project_id,user)
        return [revision_json(r) for r in session.scalars(select(Revision).where(Revision.project_id==str(project_id)).order_by(Revision.number))]

    @api.get('/api/v1/projects/{project_id}/reviews')
    def review_history(project_id: UUID,user=Depends(current_user),session=Depends(db_session)):
        project_access(session,project_id,user)
        records = session.scalars(select(Approval).join(Revision,Approval.revision_id==Revision.id).where(
            Revision.project_id==str(project_id)).order_by(Approval.created_at))
        return [dict(id=a.id,revision_id=a.revision_id,job_id=a.job_id,designer_id=a.designer_id,
                     decision=a.decision,comment=a.comment,spec_hash=a.spec_hash,manifest_hash=a.manifest_hash,
                     created_at=a.created_at) for a in records]

    @api.get('/api/v1/revisions/{revision_id}')
    def revision(revision_id: UUID,user=Depends(current_user),session=Depends(db_session)):
        return revision_json(revision_access(session,revision_id,user))

    @api.post('/api/v1/revisions/{revision_id}/verify')
    def verify(revision_id: UUID,user=Depends(current_user),session=Depends(db_session)):
        require_role(user,'designer')
        r = revision_access(session,revision_id,user,True)
        spec = CabinetSpec.model_validate(r.specification)
        if not (spec.measured_dimensions_confirmed and spec.dimension_provenance=='physically_verified' and spec.measurement_reference):
            raise HTTPException(409,'A new revision with measured dimensions and evidence is required')
        if r.active_job_id: raise HTTPException(409,'Verify the specification before generating CAD')
        transition(r,'SPECIFICATION_VERIFIED',session,user.id)
        r.verified_by,r.verified_spec_hash = user.id,r.spec_hash
        audit(session,user.id,'specification_verified',r.id,spec_hash=r.spec_hash)
        return commit_response(session,revision_json(r))

    @api.post('/api/v1/revisions/{revision_id}/jobs',status_code=202)
    def create_job(revision_id: UUID,user=Depends(current_user)):
        with api.state.db.sessions.begin() as session:
            job = enqueue(session,revision_access(session,revision_id,user,True),user)
            job_id = job.id
        submit(job_id)
        return dict(job_id=job_id,status='QUEUED',status_url=f'/api/v1/jobs/{job_id}')

    @api.post('/api/v1/cabinet/generate',status_code=202)
    def generate_cabinet(spec: CabinetSpec,user=Depends(current_user)):
        require_role(user,'admin','designer','operator')
        with api.state.db.sessions.begin() as session:
            p = Project(name=spec.project_name,owner_id=user.id,reviewer_ids=[])
            session.add(p)
            session.flush()
            r = make_revision(session,p,spec,user)
            job = enqueue(session,r,user)
            job_id,project_id,revision_id = job.id,p.id,r.id
        submit(job_id)
        return dict(job_id=job_id,status='QUEUED',approved_for_manufacturing=False,
            project_id=project_id,revision_id=revision_id,
            status_url=f'/api/v1/jobs/{job_id}',download_url=f'/api/v1/jobs/{job_id}/download')

    @api.get('/api/v1/jobs/{job_id}')
    def job_status(job_id: UUID,user=Depends(current_user),session=Depends(db_session)):
        job,r = job_access(session,job_id,user)
        return dict(job_id=job.id,revision_id=r.id,status=job.state,progress=job.progress,stage=job.stage,
            error=job.error,design_state=r.state,approved_for_manufacturing=False,
            download_url=f'/api/v1/jobs/{job.id}/download' if job.state=='SUCCEEDED' else None)

    @api.post('/api/v1/jobs/{job_id}/retry',status_code=202)
    def retry(job_id: UUID,user=Depends(current_user)):
        with api.state.db.sessions.begin() as session:
            job,r = job_access(session,job_id,user)
            r = session.get(Revision,r.id,with_for_update=True)
            if job.state != 'FAILED': raise HTTPException(409,'Only failed jobs can be retried')
            new = enqueue(session,r,user)
            new_id = new.id
        submit(new_id)
        return dict(job_id=new_id,status='QUEUED',retry_of=str(job_id))

    @api.get('/api/v1/jobs/{job_id}/download')
    def download(job_id: UUID,user=Depends(current_user),session=Depends(db_session)):
        job,r = job_access(session,job_id,user)
        directory = checked_directory(job,r)
        audit(session,user.id,'package_downloaded',job.id)
        return FileResponse(directory/'design_review_package.zip',media_type='application/zip',filename='design_review_package.zip')

    @api.get('/api/v1/jobs/{job_id}/files')
    def job_files(job_id: UUID,user=Depends(current_user),session=Depends(db_session)):
        from .artifacts import list_files
        job,r = job_access(session,job_id,user)
        directory = checked_directory(job,r)
        return dict(job_id=job.id,revision_id=r.id,revision=r.number,status='REVIEW',files=list_files(directory,job,r))

    @api.get('/api/v1/jobs/{job_id}/components')
    def job_components(job_id: UUID,user=Depends(current_user),session=Depends(db_session)):
        from .artifacts import list_components
        job,r = job_access(session,job_id,user)
        directory = checked_directory(job,r)
        return dict(job_id=job.id,revision=r.number,components=list_components(directory,job,r))

    @api.get('/api/v1/jobs/{job_id}/dimension-sheet')
    def dimension_sheet(job_id:UUID,user=Depends(current_user),session=Depends(db_session)):
        from .dimension_sheet_service import ensure_sheet,sheet_json
        job,revision=job_access(session,job_id,user)
        source=checked_directory(job,revision)
        try:
            _,metadata=ensure_sheet(config,job,revision,source)
            return sheet_json(job,metadata)
        except (ValueError,OSError) as exc:raise HTTPException(409,str(exc))

    @api.get('/api/v1/jobs/{job_id}/dimension-sheet/files/{filename}')
    def dimension_sheet_file(job_id:UUID,filename:str,user=Depends(current_user),session=Depends(db_session)):
        from .dimension_sheet_service import sheet_directory,verify_sheet
        job,revision=job_access(session,job_id,user)
        checked_directory(job,revision)
        directory=sheet_directory(config,job)
        if not directory.is_dir():raise HTTPException(409,'Create the dimension sheet first')
        try:metadata=verify_sheet(directory,job,revision)
        except (ValueError,OSError) as exc:raise HTTPException(409,str(exc))
        if filename not in metadata['files']:raise HTTPException(404,'Dimension sheet file not found')
        media={'.svg':'image/svg+xml','.pdf':'application/pdf','.dxf':'application/dxf','.json':'application/json','.zip':'application/zip'}
        path=safe_directory(directory,filename)
        return FileResponse(path,media_type=media[path.suffix],filename=filename,
            content_disposition_type='inline' if path.suffix in ('.svg','.pdf') else 'attachment',
            headers={'Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; sandbox"})

    def checked_artifact(session,job_id,filename,user):
        job,r = job_access(session,job_id,user)
        if job.state!='SUCCEEDED':raise HTTPException(409,'Job has no completed package')
        from .pipeline import verify_artifact
        try:return verify_artifact(safe_directory(config.storage,job.id),job,r.spec_hash,filename)
        except KeyError:raise HTTPException(404,'Artifact not found')
        except (OSError,ValueError):raise HTTPException(409,'Artifact integrity verification failed')

    @api.get('/api/v1/jobs/{job_id}/files/{filename}')
    def artifact(job_id: UUID,filename: str,inline: bool=False,user=Depends(current_user),session=Depends(db_session)):
        from .artifacts import TYPES
        path = checked_artifact(session,job_id,filename,user)
        media = TYPES.get(path.suffix.lower(),('','','application/octet-stream'))[2]
        return FileResponse(path,filename=filename,media_type=media,
                            content_disposition_type='inline' if inline else 'attachment')

    @api.get('/api/v1/jobs/{job_id}/files/{filename}/preview')
    def preview_artifact(job_id: UUID,filename: str,sheet: str|None=None,user=Depends(current_user),session=Depends(db_session)):
        from .artifacts import document_preview, dxf_svg, TYPES
        path = checked_artifact(session,job_id,filename,user)
        suffix = path.suffix.lower()
        if suffix=='.dxf':
            return Response(dxf_svg(path),media_type='image/svg+xml',headers={'Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; sandbox"})
        if suffix in ('.png','.pdf','.svg'):
            return FileResponse(path,filename=filename,media_type=TYPES[suffix][2],content_disposition_type='inline')
        try: return document_preview(path,sheet)
        except ValueError as exc: raise HTTPException(422,str(exc))

    @api.get('/api/v1/revisions/{revision_id}/{resource}')
    def revision_output(revision_id: UUID,resource: Literal['parts','cutting-list','bom','drawings','validation'],
                        user=Depends(current_user),session=Depends(db_session)):
        r = revision_access(session,revision_id,user)
        job = session.get(Job,r.active_job_id) if r.active_job_id else None
        if not job: raise HTTPException(409,'Generate a CAD job first')
        directory = checked_directory(job,r)
        files = {'parts':'parts_registry.json','cutting-list':'schedules_REVIEW.json','bom':'schedules_REVIEW.json',
                 'drawings':'drawing_manifest.json','validation':'validation_report.json'}
        data = json.loads((directory/files[resource]).read_text(encoding='utf-8'))
        return data['parts'] if resource=='cutting-list' else data['bom'] if resource=='bom' else data

    def approve(revision_id: UUID,review: Review,user=Depends(current_user),session=Depends(db_session)):
        require_role(user,'designer')
        r = revision_access(session,revision_id,user,True)
        job = session.get(Job,r.active_job_id) if r.active_job_id else None
        if not job or job.state != 'SUCCEEDED': raise HTTPException(409,'Review requires a completed job')
        checked_directory(job,r)
        if review.decision == 'APPROVED':
            ensure_approvable(r,job)
            checked_report = json.loads((safe_directory(config.storage,job.id)/'validation_report.json').read_text())
            if checked_report.get('has_blockers',True): raise HTTPException(409,'Hashed validation report contains unresolved issues')
        transition(r,review.decision,session,user.id)
        a = Approval(revision_id=r.id,job_id=job.id,designer_id=user.id,decision=review.decision,
                     comment=review.comment,spec_hash=r.spec_hash,manifest_hash=job.manifest_hash)
        session.add(a)
        session.flush()
        audit(session,user.id,'designer_reviewed',r.id,decision=review.decision,approval_id=a.id)
        return dict(id=a.id,state=r.state)

    @api.post('/api/v1/revisions/{revision_id}/approvals',status_code=201)
    def review_revision(revision_id: UUID,review: Review,user=Depends(current_user),session=Depends(db_session)):
        return commit_response(session,approve(revision_id,review,user,session))

    @api.post('/api/v1/revisions/{revision_id}/release',status_code=201)
    def release(revision_id: UUID,user=Depends(current_user),session=Depends(db_session)):
        require_role(user,'designer')
        r = revision_access(session,revision_id,user,True)
        if r.state != 'APPROVED': raise HTTPException(409,'Designer approval is required before release')
        job = session.get(Job,r.active_job_id)
        if not job or job.state != 'SUCCEEDED' or not job.validation or job.validation.get('has_blockers',True):
            raise HTTPException(409,'Validation issues prevent release')
        directory = checked_directory(job,r)
        a = session.scalar(select(Approval).where(Approval.revision_id==r.id,Approval.job_id==job.id,
                            Approval.decision=='APPROVED').order_by(Approval.created_at.desc()))
        if not a or a.spec_hash != r.spec_hash or a.manifest_hash != job.manifest_hash:
            raise HTTPException(409,'Approval does not match the current immutable revision/package')
        manifest = json.loads((directory/'manifest.json').read_text())
        # Review-only geometry/documents cannot be rebranded by an approval action.
        if manifest.get('status') != 'MANUFACTURING_APPROVED':
            raise HTTPException(409,'Manufacturing-approved shop package generation is not implemented; review packages cannot be released')
        released = config.storage/'releases'
        released.mkdir(exist_ok=True)
        record = Release(revision_id=r.id,approval_id=a.id,package_hash=job.package_hash)
        session.add(record)
        session.flush()
        target = safe_directory(released,record.id+'.zip')
        with target.open('xb') as destination, (directory/'design_review_package.zip').open('rb') as source:
            shutil.copyfileobj(source,destination)
        transition(r,'PRODUCTION_RELEASED',session,user.id)
        audit(session,user.id,'production_released',r.id,release_id=record.id,sha256=record.package_hash)
        return commit_response(session,dict(release_id=record.id,sha256=record.package_hash))

    @api.get('/api/v1/catalogs/{kind}')
    def catalog(kind: Literal['materials','hardware','joinery'],user=Depends(current_user),session=Depends(db_session)):
        return [dict(id=e.id,reference=e.reference,profile=e.payload) for e in session.scalars(select(CatalogEntry).where(CatalogEntry.kind==kind))]

    @api.post('/api/v1/catalogs/materials',status_code=201)
    def material(data: MaterialProfile,user=Depends(current_user),session=Depends(db_session)):
        return add_catalog('materials',data.material_id,data,user,session)

    @api.post('/api/v1/catalogs/hardware',status_code=201)
    def hardware(data: HardwareProfile,user=Depends(current_user),session=Depends(db_session)):
        return add_catalog('hardware',data.hardware_id,data,user,session)

    @api.post('/api/v1/catalogs/joinery',status_code=201)
    def joinery(data: JointProfile,user=Depends(current_user),session=Depends(db_session)):
        return add_catalog('joinery',data.profile_id,data,user,session)

    def add_catalog(kind,reference,data,user,session):
        require_role(user,'admin','designer')
        entry = CatalogEntry(kind=kind,reference=reference,payload=data.model_dump(mode='json'),created_by=user.id)
        session.add(entry)
        session.flush()
        audit(session,user.id,'catalog_created',entry.id,kind=kind,reference=reference)
        return commit_response(session,dict(id=entry.id,reference=reference))

    @api.get('/api/v1/audit')
    def audit_log(user=Depends(current_user),session=Depends(db_session)):
        require_role(user,'admin')
        return [dict(id=a.id,actor=a.actor_id,event=a.event,resource_id=a.resource_id,details=a.details,created_at=a.created_at)
                for a in session.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(500))]

    from .api.agents import register_routes
    register_routes(api,config,db_session,project_access,revision_access,make_revision,enqueue,submit,
                    checked_directory,approve,release,Review)
    api.state.dummy_password = hash_password('nonexistent-account-timing-placeholder')
    return api


app = create_app()
