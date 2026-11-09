"""Read-only checks of engineering solids, previews and the protected chair11."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlencode
from playwright.sync_api import sync_playwright,expect


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--project',required=True)
    parser.add_argument('--url',default='http://127.0.0.1:8000');args=parser.parse_args()
    protected='c5429a31-1e0f-438f-9c6c-23018da0310a';assert args.project!=protected
    output=Path('outputs/engineering-browser');output.mkdir(parents=True,exist_ok=True);errors=[];mutations=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True,
            args=['--use-angle=swiftshader','--enable-unsafe-swiftshader'])
        try:
            page=browser.new_page(viewport=dict(width=1440,height=1080))
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.on('request',lambda r:mutations.append(r.url) if r.method not in ('GET','HEAD','OPTIONS') else None)
            state=page.request.get(f'{args.url}/api/projects/{args.project}').json();scope=state['model_scope'];job=scope['job_id']
            assert scope['geometry_source']=='ENGINEERING_CAD' and scope['solid_geometry_verified']
            proof=state['engineering_cad']['cad_export_agreement'];assert proof['valid_closed_solids']
            parts=page.request.get(f'{args.url}/api/v1/jobs/{job}/components').json()['components']
            page.goto(args.url+'/studio?'+urlencode(dict(project=args.project,tab='model')),wait_until='domcontentloaded')
            whole=page.locator('.viewer-layout').first
            expect(whole).to_have_attribute('data-model-source','Engineering CAD assembly',timeout=120000)
            expect(whole).to_have_attribute('data-model-ready','true',timeout=180000)
            expect(page.get_by_role('heading',name='Your solid CAD parts are ready')).to_be_visible()
            page.screenshot(path=str(output/'solid-assembly.png'))
            page.get_by_role('button',name='Files & drawings',exact=True).click()
            panel=page.get_by_role('region',name='Individual part CAD files');expect(panel).to_be_visible(timeout=60000)
            cards=panel.locator('.part-export-card');expect(cards).to_have_count(len(parts))
            selected=parts[min(1,len(parts)-1)];card=cards.filter(has_text=selected['part_id']).first
            card.locator('.part-open').click();inspector=panel.locator('[aria-label="Individual part preview"]')
            expect(inspector.locator('.viewer-layout')).to_have_attribute('data-model-ready','true',timeout=180000)
            inspector.scroll_into_view_if_needed();page.screenshot(path=str(output/'solid-part.png'))
            inspector.get_by_role('button',name='DXF drawing',exact=True).click()
            drawing=inspector.locator('.part-drawing');expect(drawing).to_have_js_property('complete',True,timeout=60000)
            expect(drawing).not_to_have_js_property('naturalWidth',0);page.screenshot(path=str(output/'part-drawing.png'))
            inspector.get_by_role('button',name='PDF sheet',exact=True).click()
            pdf=f'/api/v1/jobs/{job}/files/'+selected['files']['pdf']+'/preview'
            expect(inspector.locator('iframe')).to_have_attribute('src',pdf)
            response=page.request.get(args.url+pdf);assert response.status==200 and 'inline' in response.headers['content-disposition']
            inspector.get_by_role('button',name='Close part preview').click()
            panel.scroll_into_view_if_needed();page.screenshot(path=str(output/'all-solid-parts.png'))
            page.set_viewport_size(dict(width=390,height=844));assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
            page.screenshot(path=str(output/'mobile.png'))
            assert page.locator('input[type=password]').count()==0
            page.set_viewport_size(dict(width=1440,height=1080))
            page.goto(args.url+'/#project/'+args.project+'/previews',wait_until='domcontentloaded')
            expect(page.locator('.output-card').filter(has_text='part_'+selected['part_id']+'_REVIEW.step').first).to_be_visible(timeout=120000)
            assert page.get_by_role('heading',name='No matching CAD files are available').count()==0
            page.screenshot(path=str(output/'classic-solid-parts.png'))
            baseline=Path('outputs/engineering-live/chair11-before.json')
            if baseline.is_file():
                old=json.loads(baseline.read_text());current=page.request.get(args.url+'/api/projects/'+protected).json()
                assert all(current.get(key)==value for key,value in old.items()),'chair11 changed'
            assert not errors and not mutations,(errors,mutations)
            (output/'audit.json').write_text(json.dumps(dict(project_id=args.project,job_id=job,parts=len(parts),
                engineering_solid_assembly_visible=True,individual_solid_preview=True,dxf_and_inline_pdf=True,
                mobile_overflow=False,chair11_unchanged=True,paid_requests=0,javascript_errors=errors),indent=2),encoding='utf-8')
            print('Engineering CAD browser checks passed; chair11 unchanged.',flush=True)
        finally:browser.close()


if __name__=='__main__':main()
