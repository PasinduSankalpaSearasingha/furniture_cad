"""Read-only native reconstruction QA, optionally rebuilding a saved plan with zero AI calls."""
import argparse
import json
import time
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright,expect,Error


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--project',required=True)
    parser.add_argument('--url',default='http://127.0.0.1:8000/studio');parser.add_argument('--rebuild',action='store_true')
    parser.add_argument('--workflow');args=parser.parse_args()
    output=Path('outputs/sol-blender-browser');output.mkdir(parents=True,exist_ok=True);errors=[]
    expect.set_options(timeout=60000)
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True,
            args=['--use-angle=swiftshader','--enable-unsafe-swiftshader'])
        try:
            origin=urlsplit(args.url);page=browser.new_page(base_url=f'{origin.scheme}://{origin.netloc}',viewport={'width':1440,'height':1080})
            page.set_default_navigation_timeout(120000);page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(args.url,wait_until='domcontentloaded');page.locator('#project').select_option(args.project)
            page.get_by_role('button',name='3D workspace',exact=True).click()
            workflow_id=args.workflow
            if args.rebuild:
                with page.expect_response(lambda r:'/rebuild-blender' in r.url and r.request.method=='POST') as response:
                    page.get_by_role('button',name='Rebuild saved plan in Blender',exact=True).click()
                body=response.value.json();assert response.value.status==202,body;workflow_id=body['workflow_id']
                (output/'receipt.json').write_text(json.dumps(body,indent=2));print('Native rebuild queued: '+workflow_id,flush=True)
            if workflow_id:
                deadline=time.monotonic()+600
                while True:
                    try:
                        workflow=page.request.get('/api/v1/agents/workflows/'+workflow_id,timeout=45000).json()
                    except Error:
                        if time.monotonic()>=deadline:raise
                        page.wait_for_timeout(1500);continue
                    if workflow['status'] in ('REVIEW_REQUIRED','FAILED','SUCCEEDED'):break
                    assert time.monotonic()<deadline,workflow['stage'];page.wait_for_timeout(1500)
                (output/'workflow.json').write_text(json.dumps(workflow,indent=2),encoding='utf-8')
                assert workflow['status']=='REVIEW_REQUIRED',workflow.get('error')
                if args.rebuild:assert workflow['usage']['input_tokens']==workflow['usage']['output_tokens']==0
                page.reload(wait_until='domcontentloaded');page.locator('#project').select_option(args.project)
                page.get_by_role('button',name='3D workspace',exact=True).click()
            report=page.get_by_role('region',name='Blender photo reconstruction');expect(report).to_be_visible()
            for preview in report.locator('img').all():
                expect(preview).to_have_js_property('complete',True);expect(preview).not_to_have_js_property('naturalWidth',0)
            page.get_by_role('button',name='Blender reconstruction',exact=True).click()
            expect(page.locator('.viewer-layout')).to_have_attribute('data-model-ready','true')
            assert page.locator('.component-row').count()>0
            page.screenshot(path=str(output/'blender-native-desktop.png'),full_page=True)
            page.get_by_role('button',name='CAD model (review)',exact=True).click()
            expect(page.locator('.viewer-layout')).to_have_attribute('data-model-ready','true')
            page.screenshot(path=str(output/'cad-comparison-desktop.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            page.screenshot(path=str(output/'blender-native-mobile.png'),full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth'),'Mobile horizontal overflow'
            assert page.locator('input[type=password]').count()==0
            expect(page.get_by_role('button',name='Refine with Sol + Blender',exact=True)).to_be_visible()
            assert not errors,errors
            print('Native Blender UI passed: real renders, private artifacts, GLB/CAD preview switching, component list, mobile layout.',flush=True)
        finally:browser.close()


if __name__=='__main__':main()
