"""Run/resume one paid AI review workflow in the trusted local workspace."""
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
    output=args.output.resolve()
    output.mkdir(parents=True,exist_ok=True)
    with httpx.Client(base_url=args.base_url,timeout=45) as client:
        def request(method,path,**kwargs):
            response=client.request(method,path,**kwargs)
            response.raise_for_status()
            return response
        if request('GET','/api/v1/workspace/access').json()['mode']!='local':
            raise RuntimeError('This helper requires the trusted local workspace')
        if args.workflow_id: wid=args.workflow_id
        else:
            body=dict(project_id=args.project_id,notes=args.notes,template_id=args.template,
                source_workflow_id=args.source_workflow,image_ids=args.image_id or [],
                idempotency_key=args.idempotency_key or str(uuid4()))
            response=request('POST','/api/v1/ai/estimate-furniture',json=body).json()
            wid=response['workflow_id']
        (output/'workflow_id.txt').write_text(wid,encoding='utf-8')
        print(json.dumps(dict(workflow_id=wid,project_url=args.base_url+'/#project/'+args.project_id)),flush=True)
        deadline=time.monotonic()+600
        previous=None
        while time.monotonic()<deadline:
            status=request('GET',f'/api/v1/agents/workflows/{wid}').json()
            current=(status['status'],status['stage'],status['progress'])
            if current!=previous:
                print(json.dumps(dict(status=current[0],stage=current[1],progress=current[2],job_id=status['job_id'])),flush=True)
                previous=current
            if status['status'] in ('REVIEW_REQUIRED','FAILED','CAD_FAILED'): break
            time.sleep(3)
        else: raise TimeoutError('Workflow is still running. Resume using --workflow-id; do not submit a duplicate.')
        (output/'ai_workflow.json').write_text(json.dumps(status,indent=2),encoding='utf-8')
        if not status['job_id']: raise RuntimeError(json.dumps(status.get('error')))
        job=request('GET',f'/api/v1/jobs/{status["job_id"]}').json()
        if job['status']!='SUCCEEDED': raise RuntimeError(json.dumps(job))
        package=request('GET',job['download_url']).content
        with ZipFile(BytesIO(package)) as archive:
            if archive.testzip(): raise RuntimeError('Review package CRC check failed')
            manifest=json.loads(archive.read('manifest.json'))
            if set(archive.namelist())!=set(manifest['files'])|{'manifest.json'}: raise RuntimeError('Invalid manifest contents')
            for name in archive.namelist():
                if Path(name).name!=name or '\\' in name or ':' in name: raise RuntimeError('Unsafe artifact path')
                data=archive.read(name)
                if name in manifest['files'] and hashlib.sha256(data).hexdigest()!=manifest['files'][name]:
                    raise RuntimeError('Artifact integrity check failed')
                (output/name).write_bytes(data)
        (output/'design_review_package.zip').write_bytes(package)
        summary=dict(project_id=args.project_id,workflow_id=wid,job_id=status['job_id'],revision_id=status['revision_id'],
            project_url=args.base_url+'/#project/'+args.project_id,
            template_id=manifest.get('template_id'),workflow_status=status['status'],cad_status=job['status'],
            package_sha256=hashlib.sha256(package).hexdigest(),output=str(output),approved_for_manufacturing=False)
        (output/'generation_summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
        print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-id',required=True)
    parser.add_argument('--source-workflow')
    parser.add_argument('--image-id',action='append')
    parser.add_argument('--template')
    parser.add_argument('--notes',default='Use common estimated dimensions and predict visible curves and other design details for a review CAD model.')
    parser.add_argument('--workflow-id',help='Resume polling without another paid generation')
    parser.add_argument('--idempotency-key')
    parser.add_argument('--base-url',default='http://127.0.0.1:8000')
    parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args())
