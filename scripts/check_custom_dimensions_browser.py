"""Exercise analysis-first custom dimensions with synthetic responses and no paid calls."""
import argparse
import json
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
from PIL import Image
from playwright.sync_api import sync_playwright,expect


def check_live(url,project):
    output=Path('outputs/custom-dimensions-live');output.mkdir(parents=True,exist_ok=True)
    errors=[];mutations=[]
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True)
        try:
            page=browser.new_page(viewport=dict(width=1440,height=1080))
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.on('request',lambda request:mutations.append(request.url) if request.method not in ('GET','HEAD','OPTIONS') else None)
            state=page.request.get(url+'/api/projects/'+project).json()
            original_job=state['model_scope']['job_id']
            history=page.request.get(url+'/api/v1/projects/'+project+'/workflows').json()
            source=next(workflow for workflow in history if workflow['id']==state['model_scope']['cad_workflow_id'])
            analysis=next(workflow for workflow in history if workflow['mode']=='PHOTO_ANALYZE' and workflow['reference_image_ids']==source['reference_image_ids'])
            data=page.request.get(url+'/api/v1/agents/workflows/'+analysis['id']).json()
            assert data['status']=='SUCCEEDED',data.get('error')
            fields=data['result']['matching_inputs'];assert len(fields)>3
            page.goto(url+'/studio?project='+project+'&tab=reference',wait_until='domcontentloaded')
            inputs=page.locator('.furniture-dimensions input[type=number]')
            expect(inputs).to_have_count(len(fields),timeout=30000)
            for item in inputs.all():assert item.input_value()!=''
            create=page.get_by_role('button',name='Generate a new version',exact=True)
            expect(create).to_be_enabled()
            page.screenshot(path=str(output/'live-dimensions-desktop.png'),full_page=True)
            page.set_viewport_size(dict(width=390,height=844))
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
            page.screenshot(path=str(output/'live-dimensions-mobile.png'),full_page=True)
            after=page.request.get(url+'/api/projects/'+project).json()
            assert after['model_scope']['job_id']==original_job
            assert not errors and not mutations,(errors,mutations)
            report=dict(project_id=project,analysis_workflow_id=data['id'],detected_item=data['result']['vision']['category'],
                dimension_fields=fields,estimated_fields_prefilled=True,cad_creation_enabled=True,existing_cad_job_unchanged=original_job,
                mobile_overflow=False,javascript_errors=errors,mutation_requests=mutations)
            (output/'browser-audit.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            print(json.dumps(dict(project_id=project,dimension_field_count=len(fields),cad_creation_enabled=True,
                existing_cad_unchanged=True,mobile_overflow=False,javascript_errors=errors),indent=2),flush=True)
        finally:browser.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--url',default='http://127.0.0.1:8000')
    parser.add_argument('--project',help='Read-only checks against an already analysed live project')
    args=parser.parse_args()
    if args.project:return check_live(args.url,args.project)
    output=Path('outputs/custom-dimensions-browser');output.mkdir(parents=True,exist_ok=True)
    project='00000000-0000-4000-8000-000000000201';image='00000000-0000-4000-8000-000000000202'
    analysis_id='00000000-0000-4000-8000-000000000203';cad_id='00000000-0000-4000-8000-000000000204'
    pixels=BytesIO();Image.new('RGB',(80,60),(110,85,50)).save(pixels,format='PNG');png=pixels.getvalue()
    fields=[dict(key=key,label=label,unit=unit,estimated_value=value,explanation='Synthetic optional suggestion')
        for key,label,unit,value in [('width_mm','Overall width','mm',1200),('depth_mm','Overall depth','mm',600),
            ('height_mm','Overall height','mm',750),('top_thickness_mm','Top thickness','mm',30),
            ('leg_width_mm','Leg width','mm',40),('leg_count','Leg count','count',4),('leg_angle_deg','Leg angle','degrees',8)]]
    analysis=dict(id=analysis_id,mode='PHOTO_ANALYZE',project_id=project,reference_image_ids=[image],status='SUCCEEDED',
        progress=100,stage='succeeded',result=dict(vision=dict(category='Wooden table'),matching_inputs=fields,summary='A table with a top and four legs'))
    state=dict(uploaded=False,analysed=False,requests=[]);errors=[];unexpected=[]
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True)
        try:
            page=browser.new_page(viewport=dict(width=1440,height=1080))
            page.on('pageerror',lambda error:errors.append(str(error)))
            def intercept(route):
                request=route.request;path=urlsplit(request.url).path
                if path.endswith('.png'):route.fulfill(content_type='image/png',body=png);return
                if path=='/api/v1/workspace/access':data=dict(mode='local',user=dict(id='local',username='My workspace',role='designer'))
                elif path=='/api/projects':data=[dict(id=project,name='Synthetic custom dimensions')]
                elif path==f'/api/projects/{project}':data=dict(id=project,name='Synthetic custom dimensions',workflows=[],revisions=[])
                elif path.endswith('/images'):data=[dict(id=image,kind='REFERENCE',download_url='/api/qa/source.png')] if state['uploaded'] else []
                elif path.endswith('/workflows'):data=[analysis] if state['analysed'] else []
                elif path.endswith('/upload-image'):
                    state['uploaded']=True;data=dict(id=image,kind='REFERENCE',download_url='/api/qa/source.png')
                elif path==f'/api/projects/{project}/analyze':
                    state['analysed']=True;data=dict(workflow_id=analysis_id)
                elif path==f'/api/v1/agents/workflows/{analysis_id}':data=analysis
                elif path==f'/api/projects/{project}/generate-all':
                    state['requests'].append(request.post_data_json);data=dict(workflow_id=cad_id)
                elif path.endswith('/openai/status'):data=dict(enabled=True,credentials_configured=True)
                elif path.endswith('/cad/status'):data=dict(available=True)
                else:
                    unexpected.append(request.method+' '+path);data={}
                route.fulfill(content_type='application/json',body=json.dumps(data))
            page.route('**/api/**',intercept)
            page.goto(args.url+'/studio?project='+project+'&tab=reference',wait_until='domcontentloaded')
            expect(page.get_by_role('heading',name='Upload a furniture photo')).to_be_visible()
            assert page.locator('input[type=number]').count()==0
            page.locator('input[type=file]').set_input_files(dict(name='table.png',mimeType='image/png',buffer=png))
            expect(page.get_by_label('Top thickness',exact=True)).to_be_visible(timeout=20000)
            expect(page.get_by_label('Leg width',exact=True)).to_be_visible()
            assert not state['requests'],'Uploading or analysing must not start CAD'
            assert page.locator('input[type=number]').count()==7
            for field in fields:expect(page.get_by_label(field['label'],exact=True)).to_have_value(str(field['estimated_value']))
            expect(page.get_by_label('Top thickness',exact=True)).to_have_attribute('placeholder','30')
            page.get_by_label('Top thickness',exact=True).fill('42')
            page.get_by_label('Leg count',exact=True).fill('')
            page.get_by_label('Leg count',exact=True).fill('4')
            page.get_by_label('Leg angle',exact=True).fill('-5')
            page.screenshot(path=str(output/'custom-part-dimensions.png'),full_page=True)
            page.get_by_role('button',name='Generate CAD files',exact=True).click()
            expect(page.get_by_role('heading',name='Model & edit',exact=True)).to_be_visible()
            assert len(state['requests'])==1
            custom=state['requests'][0]
            assert custom['analysis_workflow_id']==analysis_id
            assert custom['known_values']==[dict(key='top_thickness_mm',value=42),dict(key='leg_count',value=4),dict(key='leg_angle_deg',value=-5)]
            page.goto(args.url+'/studio?project='+project+'&tab=reference',wait_until='domcontentloaded')
            expect(page.get_by_label('Top thickness',exact=True)).to_be_visible(timeout=20000)
            page.set_viewport_size(dict(width=390,height=844))
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
            page.screenshot(path=str(output/'mobile.png'),full_page=True)
            page.get_by_role('button',name='Generate CAD files',exact=True).click()
            expect(page.get_by_role('heading',name='Model & edit',exact=True)).to_be_visible()
            assert len(state['requests'])==2 and state['requests'][1]['known_values']==[]
            assert not errors and not unexpected,(errors,unexpected)
            report=dict(image_analysis_before_cad=True,item_specific_fields=7,estimated_fields_prefilled=True,
                custom_part_angle_and_count_forwarded=True,untouched_estimates_omitted_from_user_measurements=True,
                analysis_restored_after_reload=True,mobile_overflow=False,javascript_errors=errors,paid_requests=0)
            (output/'audit.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            print(json.dumps(report,indent=2),flush=True)
        finally:browser.close()


if __name__=='__main__':main()
