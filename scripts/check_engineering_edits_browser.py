"""Synthetic edit lifecycle with real CAD meshes, without paid requests or project changes."""
import argparse
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright,expect
from app.agents.photo_agent import resolve_estimated_geometry
from app.engineering_edits import merge_edit
from app.engineering_cad import render_preview
from app.review_mesh import export_glb
from tests.test_engineering_edits import chair_plan,patch_for


def check_live(url,project):
    output=Path('outputs/engineering-edit-live');output.mkdir(parents=True,exist_ok=True)
    errors=[];mutations=[]
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True,
            args=['--use-angle=swiftshader','--enable-unsafe-swiftshader'])
        try:
            page=browser.new_page(viewport=dict(width=1440,height=1080))
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.on('request',lambda request:mutations.append(request.url) if request.method not in ('GET','HEAD','OPTIONS') else None)
            before=page.request.get(url+'/api/projects/'+project).json();job=before['model_scope']['job_id']
            schema=page.request.get(url+'/openapi.json').json()
            assert '/api/projects/{project_id}/edit-cad' in schema['paths']
            page.goto(url+'/studio?project='+project+'&tab=model',wait_until='domcontentloaded')
            viewer=page.locator('.viewer-layout').first;editor=page.get_by_role('region',name='Edit generated furniture')
            expect(viewer).to_have_attribute('data-model-ready','true',timeout=120000)
            expect(editor.locator('input[type=checkbox]')).to_have_count(before['model_scope']['parts']['part_count'])
            expect(editor.get_by_role('button',name='Apply change & rebuild all files')).to_be_disabled()
            editor.locator('input[type=checkbox]').first.check()
            editor.get_by_label('Shape',exact=True).select_option('round')
            expect(editor.get_by_role('button',name='Apply change & rebuild all files')).to_be_enabled()
            editor.get_by_role('button',name='Clear selection',exact=True).click()
            expect(editor.get_by_role('button',name='Apply change & rebuild all files')).to_be_disabled()
            editor.screenshot(path=str(output/'editor-desktop.png'))
            page.set_viewport_size(dict(width=390,height=844));assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
            editor.screenshot(path=str(output/'editor-mobile.png'))
            after=page.request.get(url+'/api/projects/'+project).json()
            assert after['model_scope']['job_id']==job and not mutations and not errors,(mutations,errors)
            report=dict(project_id=project,edit_endpoint_available=True,editor_available=True,component_selection=True,
                saved_job_unchanged=job,mobile_overflow=False,javascript_errors=errors,mutation_requests=mutations,paid_requests=0)
            (output/'audit.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report,indent=2),flush=True)
        finally:browser.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--url',default='http://127.0.0.1:8000')
    parser.add_argument('--project',help='Read-only verification against an existing project')
    args=parser.parse_args()
    if args.project:return check_live(args.url,args.project)
    output=Path('outputs/engineering-edit-browser');output.mkdir(parents=True,exist_ok=True)
    plan=chair_plan();_,_,_,before,_=resolve_estimated_geometry(plan,dict(known_values=[]),'Synthetic chair')
    edit=dict(source_plan=plan.model_dump(mode='json'),part_ids=['F002','F003','F004','F005'])
    changed=merge_edit(edit,patch_for(edit,'tapered'));_,_,_,after,_=resolve_estimated_geometry(changed,dict(known_values=[]),'Edited chair')
    for name,build in [('before',before),('after',after)]:
        export_glb(build,output/(name+'.glb'),.1);(output/(name+'.png')).write_bytes(render_preview(build,-110,18))
    project='00000000-0000-4000-8000-000000000301';source='00000000-0000-4000-8000-000000000302'
    edited='00000000-0000-4000-8000-000000000303';old_job='00000000-0000-4000-8000-000000000304'
    new_job='00000000-0000-4000-8000-000000000305';image='00000000-0000-4000-8000-000000000306'
    state=dict(started=False,complete=False,failed=False,requests=[],retries=0);errors=[];unexpected=[]
    proof=dict(valid_closed_solids=True,output_scope="WHOLE_FURNITURE",part_count=6,geometry_checks=dict(full_360=dict(applicable=False)))
    def delivery(job):return dict(job_id=job,download_base='/api/qa/'+job,items=[],cad_export_agreement=proof)
    def result(job):return dict(specification=plan.model_dump(mode='json'),reference_image_ids=[image],
        engineering_cad=dict(cad_export_agreement=proof,visual_review=dict(critic=dict(accepted=True),visual=dict(accepted=True))),delivery=delivery(job))
    def source_workflow():return dict(id=source,project_id=project,mode='IMAGE_TO_CAD',cad_source='ENGINEERING_CAD',
        status='REVIEW_REQUIRED',stage='review_required',progress=100,job_id=old_job,reference_image_ids=[image],result=result(old_job))
    def edited_workflow():return dict(id=edited,project_id=project,mode='IMAGE_TO_CAD',cad_source='ENGINEERING_CAD',edit_source_workflow_id=source,
        status='REVIEW_REQUIRED' if state['complete'] else 'FAILED' if state['failed'] else 'RUNNING',
        stage='review_required' if state['complete'] else 'delivery_package',progress=100 if state['complete'] else 96,
        job_id=new_job,reference_image_ids=[image],result=result(new_job) if state['complete'] else dict(engineering_cad=result(new_job)['engineering_cad']),
        error=dict(message='Synthetic interrupted packaging',retryable=True) if state['failed'] else None)
    def snapshot():
        job=new_job if state['complete'] else old_job;current=edited if state['complete'] else source
        return dict(id=project,name='Synthetic editable chair',revisions=[],engineering_cad=result(job)['engineering_cad'],delivery=delivery(job),
            model_scope=dict(cad_workflow_id=current,source_workflow_id=current,job_id=job,geometry_source='ENGINEERING_CAD',solid_geometry_verified=True),
            workflows=([edited_workflow()] if state['started'] else [])+([] if state['complete'] else [source_workflow()]))
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True,
            args=['--use-angle=swiftshader','--enable-unsafe-swiftshader'])
        try:
            page=browser.new_page(viewport=dict(width=1440,height=1080));page.on('pageerror',lambda error:errors.append(str(error)))
            def intercept(route):
                request=route.request;path=urlsplit(request.url).path
                if path.endswith('.glb'):
                    route.fulfill(content_type='model/gltf-binary',body=(output/('after.glb' if new_job in path else 'before.glb')).read_bytes());return
                if path.endswith('.png'):
                    route.fulfill(content_type='image/png',body=(output/('after.png' if new_job in path else 'before.png')).read_bytes());return
                if path=='/api/v1/workspace/access':data=dict(mode='local',user=dict(id='local',username='My workspace',role='designer'))
                elif path=='/api/projects':data=[dict(id=project,name='Synthetic editable chair')]
                elif path==f'/api/projects/{project}':data=snapshot()
                elif path.endswith('/images'):data=[dict(id=image,kind='REFERENCE',download_url='/api/qa/reference.png')]
                elif path.endswith('/workflows'):data=snapshot()['workflows']
                elif path==f'/api/v1/agents/workflows/{source}':data=source_workflow()
                elif path==f'/api/v1/agents/workflows/{edited}':data=edited_workflow()
                elif path==f'/api/v1/agents/workflows/{edited}/retry':state['failed']=False;state['retries']+=1;data=dict(workflow_id=edited)
                elif path==f'/api/projects/{project}/edit-cad':
                    state['started']=True;state['requests'].append(request.post_data_json);data=dict(workflow_id=edited)
                elif path.endswith('/components'):
                    job=new_job if new_job in path else old_job
                    data=dict(components=[dict(part_id=part.part_id,label=part.label,material=part.material,geometry_source='ENGINEERING_CAD',
                        length_mm=40,width_mm=40,thickness_mm=420,model_url=f'/api/v1/jobs/{job}/files/part_{part.part_id}_REVIEW.glb',
                        thumbnail_url=f'/api/v1/jobs/{job}/files/part_{part.part_id}_preview.png',files=dict(step=f'part_{part.part_id}_REVIEW.step',pdf=f'part_{part.part_id}_REVIEW.pdf')) for part in (changed.parts if job==new_job else plan.parts)])
                elif path.endswith('/dimension-sheet'):
                    job=new_job if new_job in path else old_job
                    data=dict(files={kind:f'/api/v1/jobs/{job}/drawing.{kind}' for kind in ('pdf','dxf','zip')},preview_url=f'/api/v1/jobs/{job}/sheet.png',sheet=dict(parameters=[],parts=[]))
                elif path.endswith('/cad/status'):data=dict(available=True)
                elif path.endswith('/openai/status'):data=dict(enabled=True,credentials_configured=True)
                else:unexpected.append(request.method+' '+path);data={}
                route.fulfill(content_type='application/json',body=json.dumps(data))
            page.route('**/api/**',intercept)
            page.goto(args.url+'/studio?project='+project+'&tab=model',wait_until='domcontentloaded')
            viewer=page.locator('.viewer-layout').first;editor=page.get_by_role('region',name='Edit generated furniture')
            expect(viewer).to_have_attribute('data-model-ready','true',timeout=90000)
            expect(editor).to_be_visible()
            expect(editor.get_by_role('button',name='Apply change & rebuild all files')).to_be_disabled()
            editor.get_by_role('button',name='Select all legs (4)',exact=True).click()
            assert editor.locator('input[type=checkbox]:checked').count()==4
            editor.get_by_label('Shape',exact=True).select_option('tapered')
            editor.get_by_label('Change details (optional)',exact=True).fill('Taper the legs and retain the mounting positions.')
            editor.screenshot(path=str(output/'edit-form.png'))
            editor.get_by_role('button',name='Apply change & rebuild all files').click()
            expect(editor.get_by_text('Rebuilding the edited model and every file.',exact=False)).to_be_visible()
            assert len(state['requests'])==1 and state['requests'][0]['part_ids']==edit['part_ids']
            assert state['requests'][0]['source_workflow_id']==source and state['requests'][0]['style']=='tapered'
            expect(viewer).to_have_attribute('data-model-url',f'/api/v1/jobs/{old_job}/files/cabinet.glb')
            expect(page.get_by_role('link',name='Download CAD package (.zip)',exact=False)).to_have_attribute('href',f'/api/qa/{old_job}/furniture_complete_package.zip')
            state['failed']=True
            retry=page.get_by_role('button',name='Resume saved workflow',exact=True);expect(retry).to_be_visible(timeout=15000)
            expect(viewer).to_have_attribute('data-model-url',f'/api/v1/jobs/{old_job}/files/cabinet.glb')
            retry.click();expect(editor.get_by_text('Rebuilding the edited model and every file.',exact=False)).to_be_visible(timeout=15000)
            state['complete']=True
            expect(viewer).to_have_attribute('data-model-url',f'/api/v1/jobs/{new_job}/files/cabinet.glb',timeout=15000)
            expect(viewer).to_have_attribute('data-model-ready','true',timeout=90000)
            expect(page.get_by_role('link',name='Download CAD package (.zip)',exact=False)).to_have_attribute('href',f'/api/qa/{new_job}/furniture_complete_package.zip')
            viewer.screenshot(path=str(output/'edited-model.png'))
            page.set_viewport_size(dict(width=390,height=844));assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
            editor.scroll_into_view_if_needed();editor.screenshot(path=str(output/'mobile-edit-form.png'))
            page.get_by_role('button',name='CAD files',exact=True).click()
            expect(page.get_by_role('link',name='Open / print PDF')).to_have_attribute('href',f'/api/v1/jobs/{new_job}/drawing.pdf')
            assert page.locator('.part-exports').count()==0
            assert not errors and not unexpected,(errors,unexpected)
            assert state['retries']==1
            report=dict(component_selection=True,all_legs_selected=True,scoped_request=True,saved_model_during_edit=True,
                saved_model_after_failed_package=True,retry=True,viewer_and_downloads_switched_together=True,
                whole_furniture_drawing_updated=True,mobile_overflow=False,javascript_errors=errors,
                api_responses='SYNTHETIC',geometry='REAL_CAD_FIXTURE',paid_requests=0)
            (output/'audit.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report,indent=2),flush=True)
        finally:browser.close()


if __name__=='__main__':main()
