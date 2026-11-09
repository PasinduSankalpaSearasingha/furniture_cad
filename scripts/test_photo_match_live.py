"""One resumable live photo reconstruction in a separate project, with saved receipts."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import httpx


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',required=True)
    parser.add_argument('--name',default='Astra photo reconstruction')
    parser.add_argument('--url',default='http://127.0.0.1:8000')
    parser.add_argument('--output',default='outputs/astra-photo-match-live')
    args=parser.parse_args();output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    image=Path(args.image);receipt=output/'receipt.json';digest=hashlib.sha256(image.read_bytes()).hexdigest()
    with httpx.Client(base_url=args.url,timeout=60) as client:
        def checked(response):response.raise_for_status();return response.json()
        if receipt.is_file():
            saved=json.loads(receipt.read_text());assert saved['source_sha256']==digest
        else:
            project=checked(client.post('/api/projects',json={'name':args.name}))
            stored=checked(client.post(f'/api/projects/{project["id"]}/upload-image',
                files={'file':(image.name,image.read_bytes(),'image/png')}))
            saved=dict(project_id=project['id'],image_id=stored['id'],source_sha256=digest)
            receipt.write_text(json.dumps(saved,indent=2),encoding='utf-8')
        if not saved.get('analysis_workflow_id'):
            result=checked(client.post(f'/api/projects/{saved["project_id"]}/analyze',json=dict(
                image_ids=[saved['image_id']],idempotency_key='photo-match-analysis-01')))
            saved['analysis_workflow_id']=result['workflow_id'];receipt.write_text(json.dumps(saved,indent=2),encoding='utf-8')
        def wait(workflow_id):
            previous=None
            for _ in range(900):
                try:state=checked(client.get('/api/v1/agents/workflows/'+workflow_id))
                except httpx.TransportError:time.sleep(3);continue
                status=(state['status'],state['stage'],state['progress'])
                if status!=previous:
                    print(json.dumps(dict(workflow_id=workflow_id,status=status[0],stage=status[1],progress=status[2],error=state.get('error'))),flush=True)
                    previous=status
                (output/(state['mode'].lower()+'.json')).write_text(json.dumps(state,indent=2),encoding='utf-8')
                if state['status'] not in ('QUEUED','RUNNING','CAD_QUEUED','ESTIMATED_READY'):return state
                time.sleep(4)
            raise SystemExit('Still processing the saved workflow; rerun this script to poll it, without resubmitting.')
        analysis=wait(saved['analysis_workflow_id'])
        if analysis['status']!='SUCCEEDED':raise SystemExit('Photo analysis failed; inspect the saved workflow before retrying.')
        if not saved.get('cad_workflow_id'):
            result=checked(client.post(f'/api/projects/{saved["project_id"]}/generate-all',json=dict(
                image_id=saved['image_id'],analysis_workflow_id=saved['analysis_workflow_id'],known_values=[],
                notes='Reconstruct the visible furniture in the uploaded photo. Preserve its characteristic silhouette, broad support faces, curves, splay and continuous junctions. Do not substitute a generic design. Dimensions are estimated separately; no manual drawing is a shape reference.',
                idempotency_key='photo-match-cad-01')))
            saved['cad_workflow_id']=result['workflow_id'];receipt.write_text(json.dumps(saved,indent=2),encoding='utf-8')
        cad=wait(saved['cad_workflow_id'])
        if cad['status']!='REVIEW_REQUIRED':raise SystemExit('CAD generation did not complete; inspect the saved workflow before retrying.')
        saved['job_id']=cad['job_id'];saved['url']=args.url+'/studio?project='+saved['project_id']+'&tab=model'
        saved['photo_match']=cad['result']['engineering_cad'].get('photo_match')
        receipt.write_text(json.dumps(saved,indent=2),encoding='utf-8')
        for name in ('cad_preview.png','cabinet.glb','all_dimensions_REVIEW.svg'):
            response=client.get(f'/api/v1/jobs/{cad["job_id"]}/files/{name}');response.raise_for_status()
            (output/name).write_bytes(response.content)
        print(json.dumps(saved,indent=2),flush=True)


if __name__=='__main__':main()
