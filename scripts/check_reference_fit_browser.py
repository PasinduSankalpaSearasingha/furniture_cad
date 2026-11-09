"""Exercise foreground editing and optionally fit an existing project, without AI calls."""
import argparse
import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright,expect


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:8000/studio')
    parser.add_argument('--project',required=True);parser.add_argument('--fit',action='store_true')
    parser.add_argument('--workflow',help='Resume checking an already queued local fit')
    parser.add_argument('--output',type=Path,default=Path('outputs/reference-fit-browser'))
    args=parser.parse_args();args.output.mkdir(exist_ok=True,parents=True);errors=[]
    expect.set_options(timeout=45000)
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True,
            args=['--use-angle=swiftshader','--enable-unsafe-swiftshader'])
        try:
            from urllib.parse import urlsplit
            origin=urlsplit(args.url)
            page=browser.new_page(base_url=f'{origin.scheme}://{origin.netloc}',viewport={'width':1440,'height':1080});page.on('pageerror',lambda e:errors.append(str(e)))
            page.set_default_navigation_timeout(120000)
            page.goto(args.url,wait_until='domcontentloaded');page.locator('#project').select_option(args.project)
            page.get_by_role('button',name='Image to CAD',exact=True).click()
            panel=page.get_by_role('region',name='Local reference fitting')
            expect(panel).to_be_visible()
            page.get_by_role('button',name='Prepare foreground mask',exact=True).click()
            canvas=page.get_by_label('Editable furniture foreground mask');expect(canvas).to_be_visible()
            page.locator('.mask-toolbar select').select_option('background')
            box=canvas.bounding_box();canvas.click(position={'x':box['width']*.03,'y':box['height']*.03})
            page.locator('.mask-toolbar select').select_option('landmark')
            expect(page.locator('.mask-editor .two-columns select')).to_have_count(2)
            box=canvas.bounding_box();canvas.click(position={'x':box['width']*.5,'y':box['height']*.45})
            expect(page.locator('.fit-landmarks button')).to_have_count(1)
            page.locator('.fit-landmarks button').click();expect(page.locator('.fit-landmarks button')).to_have_count(0)
            page.get_by_role('button',name='Reset automatic mask',exact=True).click()
            expect(page.get_by_role('button',name='Reset automatic mask',exact=True)).to_be_enabled()
            page.get_by_label('I reviewed this foreground mask').check()
            page.screenshot(path=str(args.output/'mask-editor-desktop.png'),full_page=True)
            before=page.request.get('/api/projects/'+args.project).json()['revisions'][0]['id']
            if args.fit:
                with page.expect_response(lambda response:'/fit-reference' in response.url and response.request.method=='POST') as response:
                    page.get_by_role('button',name='Fit reference locally',exact=True).click()
                queued=response.value.json();assert response.value.status==202,queued
                (args.output/'receipt.json').write_text(json.dumps(queued,indent=2),encoding='utf-8')
                print('Local fit queued: '+queued['workflow_id'],flush=True)
            if args.fit or args.workflow:
                workflow_id=queued['workflow_id'] if args.fit else args.workflow
                deadline=time.monotonic()+360
                while True:
                    workflow=page.request.get('/api/v1/agents/workflows/'+workflow_id).json()
                    if workflow['status'] in ('REVIEW_REQUIRED','SUCCEEDED','FAILED'):break
                    assert time.monotonic()<deadline,workflow['status']
                    page.wait_for_timeout(1500)
                (args.output/'workflow.json').write_text(json.dumps(workflow,indent=2),encoding='utf-8')
                assert workflow['status']=='REVIEW_REQUIRED',workflow.get('error')
                if args.fit:assert workflow['revision_id']!=before
                assert workflow['usage']['input_tokens']==0 and workflow['usage']['output_tokens']==0
                print('Local fit saved: '+workflow['revision_id'],flush=True)
                page.reload(wait_until='networkidle');page.locator('#project').select_option(args.project)
                page.get_by_role('button',name='Image to CAD',exact=True).click()
            report=page.get_by_role('region',name='Measured reference comparison')
            if args.fit or args.workflow:
                expect(report).to_be_visible()
                expect(page.locator('.fit-comparisons img')).to_have_count(3)
                for preview in page.locator('.fit-comparisons img').all():
                    expect(preview).to_have_js_property('complete',True)
                    expect(preview).not_to_have_js_property('naturalWidth',0)
            page.screenshot(path=str(args.output/'comparison-desktop.png'),full_page=True)
            page.get_by_role('button',name='3D workspace',exact=True).click()
            expect(page.locator('.viewer-layout')).to_have_attribute('data-model-ready','true')
            page.screenshot(path=str(args.output/'cad-desktop.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            page.screenshot(path=str(args.output/'comparison-mobile.png'),full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth'),'Mobile horizontal overflow'
            assert page.locator('input[type=password]').count()==0
            assert not errors,errors
            print('Reference fitting UI passed: masks, brush, landmarks, comparison, real WebGL CAD, mobile layout, zero AI usage.',flush=True)
        finally:browser.close()


if __name__=='__main__':main()
