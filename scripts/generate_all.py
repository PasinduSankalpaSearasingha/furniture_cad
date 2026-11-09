"""Start/resume a complete local delivery using an existing image and optional mesh."""
import argparse
import json
import time
from pathlib import Path
import httpx


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',required=True);parser.add_argument('--image',required=True)
    parser.add_argument('--mesh-workflow');parser.add_argument('--idempotency-key',required=True)
    parser.add_argument('--url',default='http://127.0.0.1:8000');parser.add_argument('--wait',action='store_true')
    parser.add_argument('--output',default='outputs/image-to-cad-live')
    args=parser.parse_args();directory=Path(args.output);directory.mkdir(exist_ok=True,parents=True)
    body=dict(image_id=args.image,idempotency_key=args.idempotency_key)
    if args.mesh_workflow:body['mesh_reference_workflow_id']=args.mesh_workflow
    with httpx.Client(base_url=args.url,timeout=120,limits=httpx.Limits(keepalive_expiry=2)) as client:
        response=client.post(f'/api/projects/{args.project}/generate-all',json=body);response.raise_for_status()
        receipt=response.json();(directory/'receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
        print(json.dumps(receipt),flush=True)
        if not args.wait:return
        previous=None;deadline=time.monotonic()+3600
        while time.monotonic()<deadline:
            try:response=client.get(receipt['status_url']);response.raise_for_status();workflow=response.json()
            except httpx.TransportError:
                # Status reads can be retried without submitting another paid job.
                time.sleep(2);continue
            (directory/'workflow.json').write_text(json.dumps(workflow,indent=2),encoding='utf-8')
            current=(workflow['status'],workflow['stage'],workflow['progress'])
            if current!=previous:print(json.dumps(dict(status=current[0],stage=current[1],progress=current[2],error=workflow.get('error'))),flush=True);previous=current
            if workflow['status'] not in ('QUEUED','RUNNING','ESTIMATED_READY','CAD_QUEUED'):
                if workflow['status']!='REVIEW_REQUIRED' or not workflow.get('result',{}).get('delivery'):raise SystemExit(1)
                print('Complete delivery: '+workflow['result']['delivery']['download_base']+'/furniture_complete_package.zip',flush=True);return
            time.sleep(5)
        raise SystemExit('Workflow is still running; continue checking its status URL without resubmitting a generation.')


if __name__=='__main__':main()
