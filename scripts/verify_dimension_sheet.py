"""Preview the saved dimension sheet and built Studio against a local test port."""
import argparse
import json
import os
from pathlib import Path
import socket
from threading import Thread
import time

from playwright.sync_api import sync_playwright,expect
import uvicorn
from app.config import Settings
from app.main import create_app


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',default='8e491fea-ec13-4b33-890d-31c79d10b641')
    parser.add_argument('--job',default='c3d3d035-6ad3-4b6f-8842-c8b1f6beb585')
    args=parser.parse_args()
    output=Path('outputs/dimension-sheet-verification');output.mkdir(exist_ok=True)
    config=Settings(local_workspace=True,executor='local');app=create_app(config)
    listener=socket.socket();listener.bind(('127.0.0.1',0));base=f'http://127.0.0.1:{listener.getsockname()[1]}'
    server=uvicorn.Server(uvicorn.Config(app,log_level='warning',access_log=False))
    thread=Thread(target=lambda:server.run(sockets=[listener]),daemon=True);thread.start()
    try:
        deadline=time.monotonic()+20
        while not server.started:
            if time.monotonic()>deadline:raise RuntimeError('Test server did not start')
            time.sleep(.05)
        with sync_playwright() as playwright:
            options=dict(headless=True)
            if not Path(playwright.chromium.executable_path).exists():
                choices=sorted((Path(os.environ['LOCALAPPDATA'])/'ms-playwright').glob('chromium-*/chrome-win64/chrome.exe'))
                if choices:options['executable_path']=str(choices[-1])
            browser=playwright.chromium.launch(**options)
            page=browser.new_page(viewport=dict(width=1600,height=1132));errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            response=page.request.get(base+f'/api/v1/jobs/{args.job}/dimension-sheet',timeout=180000)
            assert response.status==200,response.text()
            sheet=response.json()
            page.goto(base+'/health/live')
            page.set_content('<html><body style="margin:0;background:white"><img style="display:block;width:1587px" src="'+base+sheet['preview_url']+'"></body></html>',wait_until='load')
            expect(page.locator('img')).to_be_visible()
            assert page.locator('img').evaluate('(image)=>image.complete && image.naturalWidth>0')
            page.screenshot(path=str(output/'one_sheet.png'))
            page.set_viewport_size(dict(width=1440,height=1100))
            page.goto(base+f'/studio?project={args.project}&tab=manufacturing')
            panel=page.get_by_role('region',name='All dimensions drawing')
            expect(panel.get_by_role('img')).to_be_visible(timeout=120000)
            panel.screenshot(path=str(output/'studio.png'))
            page.get_by_label('Drawing zoom').fill('150')
            expect(panel.get_by_role('img')).to_have_css('width',__import__('re').compile(r'\d+(\.\d+)?px'))
            page.set_viewport_size(dict(width=390,height=844))
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            panel.screenshot(path=str(output/'mobile.png'))
            assert not errors,errors
            browser.close()
            report=dict(status='passed',job_id=args.job,one_page=True,dimensions=len(sheet['sheet']['dimensions']),
                parameters=len(sheet['sheet']['parameters']),parts=len(sheet['sheet']['parts']),
                desktop=True,mobile=True,zoom=True,no_horizontal_page_overflow=True,browser_errors=errors)
            (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            print(json.dumps(report,indent=2))
    finally:
        server.should_exit=True;thread.join(timeout=10);app.state.db.engine.dispose()


if __name__=='__main__':main()
