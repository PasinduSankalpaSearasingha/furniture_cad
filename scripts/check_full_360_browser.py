"""Read-only browser verification of a completed storage model and all-angle views."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright,expect


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',required=True)
    parser.add_argument('--url',default='http://127.0.0.1:8000')
    args=parser.parse_args()
    output=Path('outputs/full-360-repair/browser');output.mkdir(parents=True,exist_ok=True)
    errors=[];mutations=[]
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',
            headless=True,args=['--use-angle=swiftshader','--enable-unsafe-swiftshader'])
        try:
            page=browser.new_page(viewport=dict(width=1440,height=1080))
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.on('request',lambda request:mutations.append(request.url) if request.method not in ('GET','HEAD','OPTIONS') else None)
            response=page.request.get(f'{args.url}/api/projects/{args.project}')
            assert response.ok,response.text()
            state=response.json();proof=state['engineering_cad']['cad_export_agreement']
            assert proof['geometry_checks']['full_360']['rear_closed']
            assert proof['geometry_checks']['assembly_connected'] and proof['all_part_steps_reopened']
            job=state['model_scope']['job_id']
            page.goto(args.url+'/studio?'+urlencode(dict(project=args.project,tab='model')),wait_until='domcontentloaded')
            viewer=page.locator('.viewer-layout').first
            expect(viewer).to_have_attribute('data-model-ready','true',timeout=180000)
            expect(page.get_by_role('heading',name='Complete model for 360° inspection')).to_be_visible()
            expect(viewer.locator('.component-row')).to_have_count(proof['part_count'])
            for view in ('front','back','left','right','top','bottom','isometric'):
                viewer.locator('.view-controls').get_by_role('button',name=view,exact=True).click()
                page.wait_for_timeout(500)
                viewer.screenshot(path=str(output/(view+'.png')))
            rotation=viewer.get_by_role('button',name='360° rotation off',exact=True)
            rotation.click()
            rotation=viewer.get_by_role('button',name='360° rotation on',exact=True)
            expect(rotation).to_have_attribute('aria-pressed','true')
            before=hashlib.sha256(viewer.locator('canvas').screenshot()).hexdigest()
            page.wait_for_timeout(1200)
            after=hashlib.sha256(viewer.locator('canvas').screenshot()).hexdigest()
            assert before!=after,'Automatic rotation did not change the rendered geometry'
            rotation.click()
            expect(viewer.get_by_role('button',name='360° rotation off',exact=True)).to_have_attribute('aria-pressed','false')
            page.get_by_role('button',name='Model views',exact=True).click()
            images=page.locator('.render-grid img')
            expect(images).to_have_count(7)
            for item in images.all():
                expect(item).to_have_js_property('complete',True,timeout=60000)
                expect(item).not_to_have_js_property('naturalWidth',0)
            page.screenshot(path=str(output/'all-views.png'),full_page=True)
            page.set_viewport_size(dict(width=390,height=844))
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
            page.get_by_role('button',name='3D workspace',exact=True).click()
            expect(viewer).to_have_attribute('data-model-ready','true',timeout=180000)
            expect(viewer.get_by_role('button',name='360° rotation off',exact=True)).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
            page.screenshot(path=str(output/'mobile.png'),full_page=True)
            assert not errors and not mutations,(errors,mutations)
            report=dict(project_id=args.project,job_id=job,part_count=proof['part_count'],
                rear_closed=True,seven_camera_views=True,automatic_rotation_verified=True,
                seven_exported_images_loaded=True,mobile_overflow=False,javascript_errors=errors,
                mutation_requests=mutations,paid_requests=0)
            (output/'audit.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            print(json.dumps(report,indent=2),flush=True)
        finally:
            browser.close()


if __name__=='__main__':main()
