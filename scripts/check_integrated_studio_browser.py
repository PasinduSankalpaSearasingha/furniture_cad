"""Read-only browser QA against actual saved projects, geometry and renders."""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright,expect


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8000/studio')
    parser.add_argument('--project',default='fa9d0cff-0966-447b-9f09-d960f5fcc66a');args=parser.parse_args()
    output=Path('outputs/integrated-studio-qa');output.mkdir(parents=True,exist_ok=True)
    expect.set_options(timeout=45000);errors=[]
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True,
            args=['--use-angle=swiftshader','--enable-unsafe-swiftshader'])
        try:
            page=browser.new_page(viewport={'width':1440,'height':1050});page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(args.url,wait_until='networkidle')
            if page.locator('.concept-card').count():
                page.screenshot(path=str(output/'react_concepts_desktop.png'),full_page=True)
            page.locator('#project').select_option(args.project)
            page.get_by_role('button',name='3D workspace',exact=True).click()
            expect(page.locator('.viewer-layout')).to_have_attribute('data-model-ready','true')
            expect(page.locator('.component-row')).to_have_count(12)
            assert page.locator('.review-card').count()==0,'Review from a different project was displayed'
            page.get_by_role('button',name='F002',exact=False).click()
            expect(page.locator('.viewer-layout')).to_have_attribute('data-selected-component','F002')
            page.get_by_role('button',name='Explode off',exact=True).click()
            expect(page.get_by_role('button',name='Explode on',exact=True)).to_have_attribute('aria-pressed','true')
            page.get_by_role('button',name='Explode on',exact=True).click()
            page.get_by_label('Show Rounded sculpted seat',exact=True).uncheck()
            expect(page.get_by_label('Show Rounded sculpted seat',exact=True)).not_to_be_checked()
            page.get_by_role('button',name='Show all',exact=True).click()
            page.screenshot(path=str(output/'react_cad_desktop.png'),full_page=True)
            page.get_by_role('button',name='Blender renders',exact=False).click()
            expect(page.locator('.render-stage>img')).to_be_visible()
            expect(page.locator('.render-grid>a')).to_have_count(5)
            page.screenshot(path=str(output/'react_blender_desktop.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            page.screenshot(path=str(output/'react_blender_mobile.png'),full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth'),'Mobile horizontal overflow'
            page.get_by_role('button',name='Manufacturing',exact=True).click()
            expect(page.get_by_text('Engineering review required',exact=True)).to_be_visible()
            expect(page.locator('tbody tr')).to_have_count(12)
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth'),'Manufacturing horizontal overflow'
            page.get_by_role('button',name='Image to CAD',exact=True).click()
            expect(page.locator('.reference-thumbs img').first).to_be_visible()
            expect(page.locator('.constraints input[type=number]').first).to_have_value('')
            assert page.locator('input[type=password]').count()==0
            demo=Path('outputs/integrated-studio-demo/demo_state.json')
            if demo.is_file():
                desk=json.loads(demo.read_text(encoding='utf-8'))['project_id']
                page.locator('#project').select_option(desk)
                page.get_by_role('button',name='3D workspace',exact=True).click()
                expect(page.locator('.viewer-layout')).to_have_attribute('data-model-ready','true')
                expect(page.locator('.component-row')).to_have_count(7)
                expect(page.locator('.review-card')).to_be_visible()
                assert '1200' in page.locator('.review-card').inner_text()
                assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
            assert not errors,errors
            print('React studio passed: actual 12-component WebGL assembly, selection, visibility, explode, five real Blender renders, manufacturing and mobile layout; no JavaScript errors.')
        finally: browser.close()


if __name__=='__main__': main()
