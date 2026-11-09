"""Explicit live API demonstration; incurs OpenAI usage. Never resubmits a saved job."""
import argparse
import json
from pathlib import Path
import time
import httpx


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--base-url',default='http://127.0.0.1:8000')
    parser.add_argument('--output',default='outputs/integrated-studio-demo');args=parser.parse_args()
    output=Path(args.output);output.mkdir(parents=True,exist_ok=True);state_path=output/'demo_state.json'
    state=json.loads(state_path.read_text()) if state_path.exists() else {}
    save=lambda:state_path.write_text(json.dumps(state,indent=2),encoding='utf-8')
    with httpx.Client(base_url=args.base_url,timeout=60) as client:
        def request(path,data):
            result=client.post(path,json=data);result.raise_for_status();return result.json()
        def wait(wid):
            last=None
            for _ in range(400):
                response=client.get('/api/v1/agents/workflows/'+wid);response.raise_for_status();workflow=response.json()
                status=workflow['status']+':'+workflow['stage']
                if status!=last: print(wid,status,workflow['progress'],flush=True);last=status
                if workflow['status'] not in ('QUEUED','RUNNING','CAD_QUEUED','ESTIMATED_READY'):
                    if workflow['status'] not in ('SUCCEEDED','REVIEW_REQUIRED'):
                        save();raise RuntimeError(json.dumps(workflow['error']))
                    return workflow
                time.sleep(3)
            raise TimeoutError('Live workflow remains in progress; resume this script with the same output directory')
        if not state.get('project_id'):
            state['project_id']=request('/api/projects',{'name':'Astra + Sunburst + Blender · Oak desk'})['id'];save()
        project=state['project_id']
        if not state.get('concept_workflow_id'):
            concept=request(f'/api/projects/{project}/generate-concept',dict(
                prompt='Create a modern solid oak writing desk: four straight square legs, one rectangular top with gently rounded vertical corners, simple straight side aprons; width 1200 mm, depth 600 mm, height 750 mm. No drawers, no chair, no accessories. Isolated three-quarter studio product view.',
                quality='low',count=1,idempotency_key='integrated-demo-concept-v1'))
            state['concept_workflow_id']=concept['workflow_id'];save()
        concept=wait(state['concept_workflow_id'])
        (output/'concept_workflow.json').write_text(json.dumps(concept,indent=2),encoding='utf-8')
        image=concept['result']['images'][0];state['concept_id']=image['id'];save()
        if not state.get('cad_workflow_id'):
            request(f'/api/projects/{project}/concepts/{image["id"]}/select',{})
            cad=request(f'/api/projects/{project}/generate-cad',dict(concept_id=image['id'],
                known_values=[dict(key='width_mm',value=1200),dict(key='depth_mm',value=600),dict(key='height_mm',value=750)],
                notes='Use a simple economical part plan closely matching the selected concept. Preserve all supplied dimensions exactly.',
                render_options=dict(quality='draft',views=['front','side','top','isometric','hero'],finish='oak'),
                idempotency_key='integrated-demo-cad-v1'))
            state['cad_workflow_id']=cad['workflow_id'];save()
        cad=wait(state['cad_workflow_id']);state.update(revision_id=cad['revision_id'],job_id=cad['job_id']);save()
        (output/'cad_workflow.json').write_text(json.dumps(cad,indent=2),encoding='utf-8')
        for name,path in [('concept.png',image['download_url']),('cad_review_package.zip',f'/api/v1/jobs/{cad["job_id"]}/download'),
                          ('blender_render_package.zip',cad['result']['blender']['download_base']+'/blender_render_package.zip')]:
            result=client.get(path);result.raise_for_status();(output/name).write_bytes(result.content)
        print(json.dumps(dict(project_id=project,revision_id=cad['revision_id'],job_id=cad['job_id'],status=cad['status'],
            render=cad['result']['blender']['report'],usage=cad['usage'],cost=cad['cost']),indent=2),flush=True)


if __name__=='__main__': main()
