"""Real app UI checks against an isolated local native fixture; no paid calls."""
import json
import argparse
import time
from pathlib import Path
from playwright.sync_api import sync_playwright,expect


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--revise',action='store_true');args=parser.parse_args()
    root=Path('outputs/editable_cad_browser');state=json.loads((root/'state.json').read_text());errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True,
            args=['--use-angle=swiftshader','--enable-unsafe-swiftshader'])
        try:
            page=browser.new_page(viewport={'width':1440,'height':1100})
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.add_init_script('try { if(window===window.top && window.localStorage) localStorage.setItem('+json.dumps('mesh-reconstruction:'+state['project'])+','+json.dumps(state['job'])+'); } catch {}')
            page.goto('http://127.0.0.1:8120/studio?project='+state['project']+'&tab=model',wait_until='domcontentloaded')
            workspace=page.get_by_role('region',name='Editable CAD and engineering drawings')
            expect(workspace).to_be_visible(timeout=60000)
            viewer=workspace.locator('.viewer-layout')
            expect(viewer).to_have_attribute('data-model-ready','true',timeout=120000)
            expect(viewer.locator('.component-row')).to_have_count(5)
            viewer.screenshot(path=str(root/'assembly.png'))
            workspace.get_by_role('button',name='Six sides',exact=True).click()
            for name in ['front','back','left','right','top','bottom']:
                workspace.get_by_role('button',name=name,exact=True).click()
                image=workspace.get_by_role('img',name=f'assembly {name} native vector drawing')
                expect(image).to_have_js_property('complete',True,timeout=30000)
                expect(image).not_to_have_js_property('naturalWidth',0)
            workspace.get_by_role('img').screenshot(path=str(root/'bottom_vector.png'))
            workspace.get_by_label('Inspect component').select_option('F001')
            expect(workspace.get_by_role('img',name='F001 bottom native vector drawing')).to_be_visible()
            workspace.get_by_role('button',name='Drawing PDF',exact=True).click()
            expect(workspace.locator('iframe')).to_have_attribute('src',__import__('re').compile('six_views.pdf$'))
            assert page.request.get('http://127.0.0.1:8120'+workspace.locator('iframe').get_attribute('src')).status==200
            workspace.get_by_text('Review dimensions and components',exact=True).click()
            expect(workspace.get_by_label('Component label')).to_have_value('Table top')
            workspace.get_by_label('Boundary review').select_option('accepted')
            workspace.screenshot(path=str(root/'part_review.png'))
            revision=None
            if args.revise:
                workspace.get_by_label('Component label').fill('Reviewed table top')
                with page.expect_response(lambda r:'/revise' in r.url and r.request.method=='POST') as response:
                    workspace.get_by_role('button',name='Save review and regenerate CAD',exact=True).click()
                assert response.value.status==202,response.value.text()
                queued=response.value.json();deadline=time.monotonic()+240
                while time.monotonic()<deadline:
                    status=page.request.get('http://127.0.0.1:8120'+queued['status_url']).json()
                    if status['status'] in ('SUCCEEDED','FAILED'):break
                    page.wait_for_timeout(1000)
                assert status['status']=='SUCCEEDED',status
                base='http://127.0.0.1:8120'+queued['status_url']+'/files/'
                delivery=page.request.get(base+'cad_delivery.json').json()
                report=page.request.get(base+'optimization_report.json').json()
                provider=page.request.get(base+'provider_usage.json').json()
                assert delivery['ai_usage']['reserved_requests']==0 and provider['generation_attempts']==0
                assert delivery['revision']=='R2'
                assert next(p for p in delivery['parts'] if p['part_id']=='F001')['review_status']=='accepted'
                assert len(report['projection_cache']['reused'])==6,report['projection_cache']
                revision=dict(job=queued['job_id'],sol_requests=0,neural4d_jobs=0,reused_projections=report['projection_cache']['reused'])
                expect(workspace.get_by_label('Inspect component')).to_be_visible(timeout=30000)
            page.set_viewport_size({'width':390,'height':844})
            expect(workspace).to_be_visible();page.wait_for_timeout(300)
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),'Mobile page overflow'
            assert not errors,errors
            (root/'browser_validation.json').write_text(json.dumps(dict(webgl_assembly=True,parts=5,six_svg_views=True,
                part_selection=True,pdf_http_200=True,boundary_review_controls=True,mobile_no_overflow=True,
                browser_errors=errors,sol_requests=0,neural4d_jobs=0,local_revision=revision),indent=2))
            print('CAD workspace browser checks passed; no external AI requests.',flush=True)
        finally:browser.close()


if __name__=='__main__':main()
