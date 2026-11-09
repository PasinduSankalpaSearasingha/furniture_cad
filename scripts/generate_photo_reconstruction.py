"""Submit/resume a photo-specific CAD reconstruction using the running local API."""
import argparse
import hashlib
import json
import time
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile
import httpx


def run(args):
    directory=args.output.resolve();directory.mkdir(parents=True,exist_ok=True)
    known=[]
    for item in args.known:
        key,value=item.split('=',1);known.append(dict(key=key,value=float(value)))
    with httpx.Client(base_url=args.base_url,timeout=40) as client:
        def request(method,path,**kw):
            response=client.request(method,path,**kw);response.raise_for_status();return response
        if request('GET','/api/v1/workspace/access').json()['mode']!='local':
            raise RuntimeError('The helper requires the trusted local workspace')
        if args.workflow_id: wid=args.workflow_id
        else:
            result=request('POST','/api/v1/ai/reconstruct-furniture',json=dict(project_id=args.project_id,
                source_workflow_id=args.source_workflow,image_ids=args.image_id or [],known_values=known,
                notes=args.notes,idempotency_key=args.idempotency_key or str(uuid4()))).json()
            wid=result['workflow_id']
        (directory/'workflow_id.txt').write_text(wid,encoding='utf-8')
        print(json.dumps(dict(workflow_id=wid,project_id=args.project_id)),flush=True)
        previous=None;deadline=time.monotonic()+900
        while time.monotonic()<deadline:
            status=request('GET',f'/api/v1/agents/workflows/{wid}').json()
            current=(status['status'],status['stage'],status['progress'])
            if current!=previous: print(json.dumps(dict(status=current[0],stage=current[1],progress=current[2],job_id=status['job_id'])),flush=True);previous=current
            if status['status'] in ('REVIEW_REQUIRED','FAILED','CAD_FAILED'): break
            time.sleep(3)
        else: raise TimeoutError('Workflow is still running; use --workflow-id to resume polling without another paid request')
        (directory/'ai_workflow.json').write_text(json.dumps(status,indent=2),encoding='utf-8')
        if not status['job_id']: raise RuntimeError(json.dumps(status.get('error')))
        job=request('GET',f'/api/v1/jobs/{status["job_id"]}').json()
        if job['status']!='SUCCEEDED': raise RuntimeError(json.dumps(job))
        package=request('GET',job['download_url']).content
        with ZipFile(BytesIO(package)) as archive:
            if archive.testzip(): raise RuntimeError('Archive CRC check failed')
            manifest=json.loads(archive.read('manifest.json'))
            if set(archive.namelist())!=set(manifest['files'])|{'manifest.json'}: raise RuntimeError('Manifest entries differ')
            for name in archive.namelist():
                if Path(name).name!=name or ':' in name or '\\' in name: raise RuntimeError('Unsafe artifact path')
                data=archive.read(name)
                if name in manifest['files'] and hashlib.sha256(data).hexdigest()!=manifest['files'][name]: raise RuntimeError('Artifact hash check failed')
                (directory/name).write_bytes(data)
        (directory/'design_review_package.zip').write_bytes(package)
        summary=dict(project_id=args.project_id,workflow_id=wid,revision_id=status['revision_id'],job_id=status['job_id'],
            project_url=args.base_url+'/#project/'+args.project_id,cad_status=job['status'],
            package_sha256=hashlib.sha256(package).hexdigest(),exact_reconstruction_verified=False)
        (directory/'generation_summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
        print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-id',required=True)
    parser.add_argument('--source-workflow')
    parser.add_argument('--image-id',action='append')
    parser.add_argument('--known',action='append',default=[],help='Optional fixed input, e.g. width_mm=700')
    parser.add_argument('--workflow-id')
    parser.add_argument('--idempotency-key')
    parser.add_argument('--base-url',default='http://127.0.0.1:8000')
    parser.add_argument('--notes',default='Follow the uploaded furniture as closely as possible, including every visible support, apron, outline, curved rail, seat profile and leg shape. Estimate dimensions when absent; list remaining differences honestly.')
    parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args())
