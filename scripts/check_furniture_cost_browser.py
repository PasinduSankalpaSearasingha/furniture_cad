"""Read-only live UI audit plus simulated budget messages; no paid requests."""
import argparse
import copy
import json
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright,expect


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:8000')
    parser.add_argument('--project',default='a5e09a5d-e538-4136-bd04-ba2ffff2ad41')
    parser.add_argument('--workflow',default='061d252b-f808-48ba-89c7-64dfb95b0e52')
    args=parser.parse_args();output=Path('outputs/cost-control');output.mkdir(parents=True,exist_ok=True)
    errors=[];mutations=[];report={}
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True,
            args=['--use-angle=swiftshader','--enable-unsafe-swiftshader'])
        try:
            def page_for(mode=None):
                context=browser.new_context(viewport=dict(width=1440,height=1000));page=context.new_page()
                page.on('pageerror',lambda error:errors.append(str(error)))
                def intercept(route):
                    if route.request.method not in ('GET','HEAD','OPTIONS'):
                        mutations.append(route.request.method+' '+route.request.url);route.abort();return
                    if mode and urlsplit(route.request.url).path==f'/api/v1/agents/workflows/{args.workflow}':
                        response=route.fetch();data=response.json()
                        if mode=='local_curves':
                            workflow=data['result']['engineering_cad']['workflow']
                            workflow.setdefault('geometry_repairs',[]).append(dict(kind='LOCAL_LOFT_REBUILD',
                                part_id='SYNTHETIC_BROWSER_FIXTURE',additional_ai_requests=0))
                            route.fulfill(response=response,json=data);return
                        data['ai_budget']=dict(maximum_usd='2.00',spent_usd='1.35',reserved_usd='0',uncertain=False,includes_linked_analysis=True)
                        message='The $2.00 AI budget cannot cover another request. Any valid CAD draft is retained; automatic paid corrections have stopped.'
                        if mode in ('draft','partial_draft'):
                            engineering=data['result']['engineering_cad']
                            engineering['workflow']['cost_control_stop']=dict(code='AI_BUDGET_REACHED',message=message)
                            engineering['visual_review']['visual']['accepted']=False
                            if mode=='partial_draft':
                                engineering['workflow']['budget_draft']=dict(status='BUDGET_LIMIT_DRAFT',fully_connected=False,
                                    remaining_issues=[dict(code='ASSEMBLY_CONNECTIONS_INCOMPLETE',
                                        message='Synthetic saved draft: some pieces do not connect.',part_ids=['F001','F002'])])
                        else:
                            data.update(status='BUDGET_REACHED',result=None,error=dict(code='AI_BUDGET_REACHED',message=message,retryable=False))
                        route.fulfill(response=response,json=data);return
                    route.continue_()
                page.route('**/api/**',intercept)
                return context,page
            context,page=page_for()
            page.goto(f'{args.url}/studio?project={args.project}&tab=manufacturing',wait_until='domcontentloaded')
            for label in ('STEP','STL','GLB','Drawing DXF','Drawing PDF','Drawing SVG'):
                expect(page.get_by_role('link',name='Download '+label,exact=True)).to_be_visible(timeout=60000)
            page.screenshot(path=str(output/'live-files.png'))
            page.set_viewport_size(dict(width=390,height=844))
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
            page.screenshot(path=str(output/'live-mobile.png'))
            page.set_viewport_size(dict(width=1440,height=1000))
            page.goto(f'{args.url}/studio?project={args.project}&tab=model',wait_until='domcontentloaded')
            expect(page.locator('.viewer-layout').first).to_have_attribute('data-model-ready','true',timeout=90000)
            page.screenshot(path=str(output/'live-model.png'));context.close()
            context,page=page_for('local_curves')
            page.goto(f'{args.url}/studio?project={args.project}&tab=manufacturing',wait_until='domcontentloaded')
            expect(page.get_by_text('Curves repaired locally.',exact=True)).to_be_visible(timeout=60000)
            expect(page.get_by_text('These repairs used no extra AI requests.',exact=False)).to_be_visible()
            page.screenshot(path=str(output/'local-curves-notice.png'));context.close()
            context,page=page_for('draft')
            page.goto(f'{args.url}/studio?project={args.project}&tab=manufacturing',wait_until='domcontentloaded')
            expect(page.get_by_text('Automatic AI corrections stopped.',exact=True)).to_be_visible(timeout=60000)
            expect(page.get_by_role('heading',name='Draft CAD / AI spending stopped',exact=True)).to_be_visible()
            expect(page.get_by_text('Estimated AI usage: $1.35 of $2.00 including photo analysis.',exact=True)).to_be_visible()
            expect(page.get_by_role('link',name='Download STEP',exact=True)).to_be_visible()
            page.screenshot(path=str(output/'budget-draft-preview.png'));context.close()
            context,page=page_for('partial_draft')
            page.goto(f'{args.url}/studio?project={args.project}&tab=manufacturing',wait_until='domcontentloaded')
            expect(page.get_by_text('Remaining design problems',exact=True)).to_be_visible(timeout=60000)
            expect(page.get_by_text('Synthetic saved draft: some pieces do not connect.',exact=True)).to_be_visible()
            for label in ('STEP','STL','GLB','Drawing DXF','Drawing PDF','Drawing SVG'):
                expect(page.get_by_role('link',name='Download '+label,exact=True)).to_be_visible()
            page.set_viewport_size(dict(width=390,height=844))
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
            page.screenshot(path=str(output/'budget-partial-draft.png'));context.close()
            context,page=page_for('stopped')
            page.goto(f'{args.url}/studio?project={args.project}&tab=reference',wait_until='domcontentloaded')
            expect(page.get_by_text('AI spending check stopped generation',exact=True)).to_be_visible(timeout=60000)
            assert page.locator('.workflow-error.error').count()==0
            assert page.get_by_role('button',name='Resume saved workflow',exact=True).count()==0
            page.screenshot(path=str(output/'budget-stop-preview.png'));context.close()
            assert not errors and not mutations,(errors,mutations)
            report=dict(live_model_loaded=True,six_download_formats=True,mobile_overflow=False,
                simulated_local_curve_repair_notice=True,
                simulated_partial_draft_downloads_and_findings=True,
                simulated_budget_draft_notice=True,simulated_budget_spend_visible=True,simulated_stop_message=True,
                errors=errors,mutations=mutations,generation_requests=0)
            (output/'browser-verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            print(json.dumps(report,indent=2))
        finally:browser.close()


if __name__=='__main__':main()
