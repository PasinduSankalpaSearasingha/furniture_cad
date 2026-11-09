"""Resume one existing CAD workflow once, preserving a receipt across reruns."""
import argparse
import json
from pathlib import Path
import time
from uuid import UUID
import httpx


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workflow_id',type=UUID)
    parser.add_argument('--url',default='http://127.0.0.1:8000')
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    directory=Path(args.output);directory.mkdir(parents=True,exist_ok=True)
    receipt=directory/'resume_receipt.json';workflow=str(args.workflow_id)
    saved=json.loads(receipt.read_text()) if receipt.is_file() else dict(workflow_id=workflow)
    if saved['workflow_id']!=workflow:raise SystemExit('Receipt belongs to another workflow')
    with httpx.Client(base_url=args.url,timeout=60) as client:
        def checked(response):response.raise_for_status();return response.json()
        endpoint='/api/v1/agents/workflows/'+workflow
        state=checked(client.get(endpoint))
        if state['status']=='FAILED' and not saved.get('retry_submitted'):
            # Write before submitting, so interruption cannot cause an unnoticed
            # second billed retry when this monitor is started again.
            saved['retry_submitted']=True
            receipt.write_text(json.dumps(saved,indent=2),encoding='utf-8')
            checked(client.post(endpoint+'/retry'))
        previous=None
        for _ in range(900):
            try:state=checked(client.get(endpoint))
            except httpx.TransportError:
                time.sleep(3);continue
            current=(state['status'],state['stage'],state['progress'])
            if current!=previous:
                print(json.dumps(dict(status=current[0],stage=current[1],progress=current[2],error=state.get('error'))),flush=True)
                (directory/'workflow.json').write_text(json.dumps(state,indent=2),encoding='utf-8')
                previous=current
            if state['status'] not in ('QUEUED','RUNNING'):break
            time.sleep(4)
        else:raise SystemExit('Still processing; rerun to monitor the same saved workflow')
        if state['status']!='REVIEW_REQUIRED':raise SystemExit('Workflow did not complete; no further retries were submitted')
        saved.update(project_id=state['project_id'],job_id=state['job_id'],status=state['status'],
            photo_match=state['result']['engineering_cad'].get('photo_match'),
            geometry_repairs=state['result']['engineering_cad']['workflow'].get('geometry_repairs',[]))
        receipt.write_text(json.dumps(saved,indent=2),encoding='utf-8')
        print(json.dumps(dict(project_id=saved['project_id'],job_id=saved['job_id'],
            photo_match_status=(saved.get('photo_match') or {}).get('status'),geometry_repairs=len(saved['geometry_repairs']))),flush=True)


if __name__=='__main__':main()
