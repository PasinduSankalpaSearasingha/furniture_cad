"""Test engineering CAD in a new project; never mutates the existing chair11."""
import argparse
import hashlib
import json
import time
from pathlib import Path
import httpx

PROTECTED_PROJECT='c5429a31-1e0f-438f-9c6c-23018da0310a'


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--image',required=True)
    parser.add_argument('--name',default='Engineering CAD Test 01');parser.add_argument('--wait',action='store_true')
    parser.add_argument('--url',default='http://127.0.0.1:8000');parser.add_argument('--output',default='outputs/engineering-live')
    args=parser.parse_args();root=Path(args.output);root.mkdir(parents=True,exist_ok=True)
    if args.name.lower()=='chair11':raise SystemExit('Use a new test project name')
    source=Path(args.image);digest=hashlib.sha256(source.read_bytes()).hexdigest();receipt=root/'test-project.json'
    with httpx.Client(base_url=args.url,timeout=120) as client:
        if receipt.is_file():
            saved=json.loads(receipt.read_text());assert saved['name']==args.name and saved['source_sha256']==digest
        else:
            project=client.post('/api/projects',json={'name':args.name});project.raise_for_status();pid=project.json()['id']
            assert pid!=PROTECTED_PROJECT
            image=client.post(f'/api/projects/{pid}/upload-image',files={'file':(source.name,source.read_bytes(),'image/webp')})
            image.raise_for_status();saved=dict(project_id=pid,image_id=image.json()['id'],name=args.name,source_sha256=digest)
            receipt.write_text(json.dumps(saved,indent=2),encoding='utf-8')
        assert saved['project_id']!=PROTECTED_PROJECT
        response=client.post(f'/api/projects/{saved["project_id"]}/generate-all',json=dict(image_id=saved['image_id'],
            cad_source='ENGINEERING_CAD',known_values=[],idempotency_key='engineering-repository-test-01',
            notes='Preserve the reference chair: four curved legs, sculpted wooden seat, continuous curved arm/back rail and visible uprights. All unsupplied dimensions are estimates. Model separate closed solid parts; hidden joinery remains unverified.'))
        response.raise_for_status();queued=response.json();(root/'receipt.json').write_text(json.dumps(queued,indent=2));print(json.dumps(dict(**saved,**queued)),flush=True)
        if not args.wait:return
        previous=None
        for _ in range(720):
            try:response=client.get(queued['status_url']);response.raise_for_status();state=response.json()
            except httpx.TransportError:
                time.sleep(2);continue  # Status reads never resubmit a generation.
            (root/'workflow.json').write_text(json.dumps(state,indent=2),encoding='utf-8')
            status=(state['status'],state['stage'],state['progress'])
            if status!=previous:print(json.dumps(dict(status=status[0],stage=status[1],progress=status[2],error=state.get('error'))),flush=True);previous=status
            if status[0] not in ('QUEUED','RUNNING','CAD_QUEUED','ESTIMATED_READY'):
                if status[0]!='REVIEW_REQUIRED':raise SystemExit(1)
                print('Open: '+args.url+'/studio?project='+saved['project_id']+'&tab=manufacturing',flush=True);return
            time.sleep(5)
        raise SystemExit('Still running; keep polling the saved workflow instead of submitting a new task')


if __name__=='__main__':main()
