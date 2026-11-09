"""Read-only UI regression: direct model link, visible pixels, reload and classic UI."""
import argparse
import json
from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode
import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright,expect


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--project',required=True)
    parser.add_argument('--url',default='http://127.0.0.1:8000');args=parser.parse_args()
    output=Path('outputs/model-visibility-browser');output.mkdir(exist_ok=True,parents=True);errors=[]
    url=args.url+'/studio?'+urlencode(dict(project=args.project,tab='model',model='cad'))
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True,
            args=['--use-angle=swiftshader','--enable-unsafe-swiftshader'])
        try:
            page=browser.new_page(viewport=dict(width=1440,height=1080));page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(url,wait_until='domcontentloaded');expect(page.locator('#project')).to_have_value(args.project,timeout=120000)
            viewer=page.locator('.viewer-layout');expect(viewer).to_have_attribute('data-model-source','Engineering CAD assembly')
            expect(viewer).to_have_attribute('data-model-ready','true',timeout=120000)
            bounds=viewer.bounding_box();assert bounds and bounds['y']<350,bounds
            assert viewer.evaluate('(element)=>element.compareDocumentPosition(document.querySelector(".delivery-card")) & Node.DOCUMENT_POSITION_FOLLOWING')
            page.locator('.viewport').screenshot(path=str(output/'engineering-viewport.png'))
            def pixels():return np.array(Image.open(BytesIO(page.locator('.viewport').screenshot())).convert('RGB')).astype(float)
            original=pixels();wood=(original[:,:,0]>original[:,:,1]*1.1)&(original[:,:,1]>original[:,:,2]*1.02)&(original[:,:,0]>40)
            assert wood.sum()>1000,'The loaded WebGL frame does not contain the brown chair'
            viewer.get_by_role('button',name='front',exact=True).click();page.wait_for_timeout(350)
            changed=pixels();assert np.mean(np.abs(changed-original))>.5,'Camera control did not redraw the model'
            page.reload(wait_until='domcontentloaded');expect(page.locator('#project')).to_have_value(args.project)
            expect(viewer).to_have_attribute('data-model-source','Engineering CAD assembly');expect(viewer).to_have_attribute('data-model-ready','true',timeout=120000)
            page.screenshot(path=str(output/'desktop.png'),full_page=False)
            page.set_viewport_size(dict(width=390,height=844));page.screenshot(path=str(output/'mobile.png'),full_page=False)
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
            page.set_viewport_size(dict(width=1440,height=1080))
            page.goto(args.url+'/#project/'+args.project,wait_until='domcontentloaded')
            link=page.locator('#open-modern-studio');expect(link).to_be_visible()
            assert 'project='+args.project in link.get_attribute('href')
            assert 'model=cad' in link.get_attribute('href')
            page.screenshot(path=str(output/'classic.png'),full_page=False)
            link.click();expect(page.locator('#project')).to_have_value(args.project)
            expect(page.locator('.viewer-layout')).to_have_attribute('data-model-source','Engineering CAD assembly')
            assert not errors,errors
            (output/'audit.json').write_text(json.dumps(dict(project_id=args.project,direct_link=url,
                actual_chair_pixels=int(wood.sum()),viewer_above_reports=True,camera_redraw=True,reload_preserves_model=True,
                classic_direct_link=True,mobile_overflow=False,paid_generations=0,javascript_errors=errors),indent=2),encoding='utf-8')
            print('Model visibility passed: actual chair pixels, top viewer, camera redraw, reload, mobile and classic links.',flush=True)
        finally:browser.close()


if __name__=='__main__':main()
