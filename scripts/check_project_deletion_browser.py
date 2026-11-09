"""Mocked delete lifecycle; never deletes a real project or submits AI requests."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright,expect


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8000');args=parser.parse_args()
    output=Path('outputs/project-deletion-browser');output.mkdir(parents=True,exist_ok=True)
    ids=['00000000-0000-4000-8000-000000000701','00000000-0000-4000-8000-000000000702']
    workflow='00000000-0000-4000-8000-000000000703'
    errors=[];unexpected=[];report={}
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True)
        try:
            def setup(active=False,viewer=False):
                context=browser.new_context(viewport=dict(width=1440,height=1000));page=context.new_page()
                state=dict(projects=[dict(id=pid,name=name,owner_id='owner') for pid,name in zip(ids,['Dining table','Lounge chair'])],deletes=[],fail=False)
                page.on('pageerror',lambda error:errors.append(str(error)))
                def running():return dict(id=workflow,mode='IMAGE_TO_CAD',cad_source='ENGINEERING_CAD',
                    project_id=ids[0],status='RUNNING',stage='engineering_dimensions',progress=10)
                def intercept(route):
                    path=urlsplit(route.request.url).path;method=route.request.method;status=200
                    if path=='/api/v1/workspace/access':data=dict(mode='local',user=dict(id='viewer' if viewer else 'owner',role='viewer' if viewer else 'designer'))
                    elif path=='/api/system/openai/status':data=dict(enabled=True,credentials_configured=True,models={r:'FIXTURE' for r in ('vision','design','review')})
                    elif path=='/api/system/cad/status':data=dict(available=True)
                    elif path=='/api/projects':data=state['projects']
                    elif path in ['/api/projects/'+pid for pid in ids]:
                        pid=path.rsplit('/',1)[1]
                        if method=='DELETE':
                            state['deletes'].append(pid)
                            if state['fail']:status=409;data=dict(error=dict(message='Wait for generation to finish before deleting this design'))
                            else:
                                state['projects']=[p for p in state['projects'] if p['id']!=pid]
                                data=dict(id=pid,status='DELETED',cleanup_pending=False)
                        else:data=dict(next(p for p in state['projects'] if p['id']==pid),revisions=[],workflows=[running()] if active else [])
                    elif path.endswith('/images'):data=[]
                    elif path.endswith('/workflows'):data=[running()] if active else []
                    elif path=='/api/v1/agents/workflows/'+workflow:data=running()
                    else:unexpected.append(method+' '+path);status=404;data={}
                    route.fulfill(status=status,content_type='application/json',body=json.dumps(data))
                page.route('**/api/**',intercept)
                page.goto(args.url+'/?project='+ids[0],wait_until='domcontentloaded')
                expect(page.locator('#project')).to_have_value(ids[0])
                return context,page,state

            context,page,state=setup()
            page.get_by_role('button',name='Delete design',exact=True).click()
            dialog=page.get_by_role('dialog');expect(dialog).to_contain_text('Dining table')
            expect(dialog.get_by_role('button',name='Cancel',exact=True)).to_be_focused()
            dialog.get_by_role('button',name='Cancel',exact=True).click()
            assert not state['deletes']
            state['fail']=True
            page.get_by_role('button',name='Delete design',exact=True).click()
            dialog.get_by_role('button',name='Delete permanently').click()
            expect(dialog.get_by_role('alert')).to_contain_text('Wait for generation')
            expect(page.locator('#project')).to_have_value(ids[0])
            dialog.get_by_role('button',name='Cancel',exact=True).click();state['fail']=False
            page.evaluate('(id)=>sessionStorage.setItem("fiske:pending-cad:"+id,JSON.stringify({paused:true}))',ids[0])
            page.get_by_role('button',name='Delete design',exact=True).click()
            page.screenshot(path=str(output/'confirm-desktop.png'),full_page=True)
            page.set_viewport_size(dict(width=390,height=844))
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(output/'confirm-mobile.png'),full_page=True)
            dialog.get_by_role('button',name='Delete permanently').click()
            expect(page.get_by_role('dialog')).to_have_count(0)
            expect(page.locator('#project')).to_have_value('')
            expect(page.get_by_role('option',name='Dining table',exact=True)).to_have_count(0)
            expect(page.get_by_role('status')).to_contain_text('Dining table')
            assert page.evaluate('(id)=>sessionStorage.getItem("fiske:pending-cad:"+id)',ids[0]) is None
            expect(page).not_to_have_url('.*project=.*')
            page.locator('#project').select_option(ids[1]);expect(page.locator('#project')).to_have_value(ids[1])
            page.get_by_role('button',name='Delete design',exact=True).click();page.keyboard.press('Escape')
            expect(page.get_by_role('dialog')).to_have_count(0)
            assert state['deletes']==[ids[0],ids[0]]
            page.get_by_role('button',name='Delete design',exact=True).click()
            page.get_by_role('dialog').get_by_role('button',name='Delete permanently').click()
            expect(page.locator('#project option')).to_have_count(1)
            page.reload();expect(page.locator('#project option')).to_have_count(1)
            expect(page.get_by_role('button',name='Delete design',exact=True)).to_have_count(0)
            report.update(cancel_preserves_design=True,server_rejection_preserves_design=True,
                deletion_clears_saved_selection_and_pending_intent=True,last_design_removal=True,
                mobile_overflow=False,escape_cancels=True)
            context.close()
            context,page,state=setup(active=True)
            expect(page.get_by_role('button',name='Delete design',exact=True)).to_be_disabled()
            assert not state['deletes'];report['active_generation_blocks_delete']=True;context.close()
            context,page,state=setup(viewer=True)
            expect(page.get_by_role('button',name='Delete design',exact=True)).to_have_count(0)
            assert not state['deletes'];report['read_only_hides_delete']=True;context.close()
            assert not errors and not unexpected,(errors,unexpected)
            report.update(javascript_errors=errors,real_project_deletions=0,paid_requests=0)
            (output/'audit.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            print(json.dumps(report,indent=2))
        finally:browser.close()


if __name__=='__main__':main()
