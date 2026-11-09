"""Intercepted UI checks for stopped generation; no provider calls or mutations."""
import argparse
from io import BytesIO
import json
from pathlib import Path
from urllib.parse import urlsplit
from PIL import Image
from playwright.sync_api import sync_playwright,expect


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8765');args=parser.parse_args()
    output=Path('outputs/available-output-browser');output.mkdir(parents=True,exist_ok=True)
    project,image,wid,job=('00000000-0000-4000-8000-'+str(i).zfill(12) for i in range(700,704))
    pixels=BytesIO();Image.new('RGB',(120,90),(126,107,73)).save(pixels,format='PNG')
    errors=[];unexpected=[];report={}
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True)
        try:
            for scenario in ('reference-only','disconnected-cad-draft','previous-cad-retained'):
                context=browser.new_context(viewport=dict(width=1440,height=1000));page=context.new_page()
                page.on('pageerror',lambda error:errors.append(str(error)))
                proof=dict(valid_closed_solids=True,part_count=5,output_scope='WHOLE_FURNITURE')
                draft=dict(status='REPAIR_LIMIT_DRAFT',message='Automatic repairs stopped. Available CAD was saved as a draft.',
                    fully_connected=False,remaining_issues=[dict(message='Some backrest parts do not connect.')])
                engineering=dict(cad_export_agreement=proof,visual_review=dict(visual=dict(accepted=False)),
                    workflow=dict(budget_draft=draft,cost_control_stop=dict(message='Automatic repair attempts stopped.')))
                delivery=dict(download_base='/api/qa/delivery',items=[],cad_export_agreement=proof)
                available=dict(has_cad=False,message='References and available measurements are saved. No new CAD model was completed.')
                workflow=dict(id=wid,mode='IMAGE_TO_CAD',cad_source='ENGINEERING_CAD',reference_image_ids=[image],
                    project_id=project,status='REVIEW_REQUIRED' if scenario=='disconnected-cad-draft' else 'BUDGET_REACHED',error=None,
                    result=dict(engineering_cad=engineering,delivery=delivery) if scenario=='disconnected-cad-draft' else dict(available_output=available))
                scope=None if scenario=='reference-only' else dict(job_id=job,cad_workflow_id=wid,
                    geometry_source='ENGINEERING_CAD',solid_geometry_verified=True,available_output_workflow_id=wid)
                snapshot=dict(id=project,name='Synthetic stopped furniture',workflows=[workflow],revisions=[],model_scope=scope,
                    engineering_cad=engineering if scope else None,delivery=delivery if scope else None)
                def intercept(route):
                    request=route.request;path=urlsplit(request.url).path
                    if request.method!='GET':raise AssertionError('UI check attempted a mutation: '+path)
                    if path.endswith('.png'):route.fulfill(content_type='image/png',body=pixels.getvalue());return
                    if path.endswith('/furniture_complete_package.zip'):
                        route.fulfill(content_type='application/zip',headers={'Content-Disposition':'attachment; filename=draft.zip'},body=b'PK\x05\x06'+b'\0'*18);return
                    if path=='/api/v1/workspace/access':data=dict(mode='local',user=dict(id='test-owner',role='designer'))
                    elif path=='/api/projects':data=[dict(id=project,name=snapshot['name'])]
                    elif path==f'/api/projects/{project}':data=snapshot
                    elif path.endswith('/openai/status'):data=dict(enabled=True,credentials_configured=True,models=dict(vision='test',design='test',review='test'))
                    elif path.endswith('/cad/status'):data=dict(available=True)
                    elif path.endswith('/images'):data=[dict(id=image,kind='REFERENCE',download_url='/api/qa/reference.png')]
                    elif path.endswith('/workflows'):data=[workflow]
                    elif path==f'/api/v1/agents/workflows/{wid}':data=workflow
                    elif path==f'/api/v1/agents/workflows/{wid}/result':
                        route.fulfill(content_type='application/json',headers={'Content-Disposition':'attachment; filename=available.json'},body=json.dumps(workflow['result']));return
                    elif path.endswith('/components'):data=dict(components=[])
                    else:unexpected.append(path);data={}
                    route.fulfill(content_type='application/json',body=json.dumps(data))
                page.route('**/api/**',intercept)
                page.goto(args.url+'/?project='+project,wait_until='domcontentloaded')
                expect(page.get_by_text('CAD draft ready to download' if scenario=='disconnected-cad-draft' else 'Available output saved',exact=True)).to_be_visible()
                assert page.locator('.workflow-error').count()==0
                if scenario=='reference-only':
                    with page.expect_download() as download:page.get_by_role('link',name='Download available reference and measurement report').click()
                    assert download.value.suggested_filename=='available.json'
                else:
                    if scenario=='previous-cad-retained':expect(page.get_by_text('Your previous saved CAD model and files are shown below.')).to_be_visible()
                    with page.expect_download() as download:page.get_by_role('link',name='Download CAD package',exact=True).click()
                    assert download.value.suggested_filename=='draft.zip'
                page.screenshot(path=str(output/(scenario+'.png')),full_page=True)
                page.set_viewport_size(dict(width=390,height=844))
                assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
                report[scenario]=dict(download=True,error_banner=False,mobile_overflow=False)
                context.close()
        finally:browser.close()
    assert not errors and not unexpected,(errors,unexpected)
    (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))


if __name__=='__main__':main()
