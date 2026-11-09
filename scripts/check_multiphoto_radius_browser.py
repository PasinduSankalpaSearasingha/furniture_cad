"""Synthetic UI lifecycle with actual radius drawing assets; no provider/project writes."""
import argparse
import base64
import json
import sys
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from playwright.sync_api import sync_playwright,expect
from app.agents.photo_agent import resolve_estimated_geometry
from app.photo_schema import PhotoDesign
from app.furniture_sheet import create_furniture_sheet
from app.dimension_sheet import STEM
from tests.test_engineering_cad import table_plan


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8000');parser.add_argument('--drawing-only',action='store_true');args=parser.parse_args()
    output=Path('outputs/multiphoto-radius');output.mkdir(parents=True,exist_ok=True)
    raw=table_plan(720).model_dump(mode='json');raw['parts'][0]['corner_radius']=20
    _,spec,provenance,build,_=resolve_estimated_geometry(PhotoDesign.model_validate(raw),dict(known_values=[]),'Radius drawing example')
    sheet=create_furniture_sheet(spec,build,output,provenance=provenance)
    pixels=BytesIO();Image.new('RGB',(160,100),'#cad4bf').save(pixels,format='PNG');png=pixels.getvalue()
    project='00000000-0000-4000-8000-000000000801';workflow='00000000-0000-4000-8000-000000000802';job='radius-demo'
    errors=[];unexpected=[];report={};state=dict(images=[],analyses=[],cad=[],complete=False)
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True)
        try:
            page=browser.new_page(viewport=dict(width=1440,height=1100));page.on('pageerror',lambda error:errors.append(str(error)))
            def capture_drawing():
                encoded=base64.b64encode((output/(STEM+'.svg')).read_bytes()).decode()
                page.set_content('<!doctype html><html><head><style>body{margin:0;background:white}img{width:100%;height:auto}</style></head><body><img src="data:image/svg+xml;base64,'+encoded+'"></body></html>')
                page.wait_for_function('document.querySelector("img").complete && document.querySelector("img").naturalWidth>0')
                page.screenshot(path=str(output/'radius-drawing.png'),full_page=True,timeout=15000)
            if args.drawing_only:
                capture_drawing();print('Actual radius drawing captured');return
            def photo(record):
                return dict(id=record['id'],mode='PHOTO_ANALYZE',project_id=project,status='SUCCEEDED',
                    reference_image_ids=record['input']['image_ids'],reference_views=record['input'].get('reference_views',[]),
                    result=dict(vision=dict(category='One table from several angles'),summary='Synthetic photo set',matching_inputs=[
                        dict(key=k,label=label,unit='mm',estimated_value=v,explanation='Synthetic estimate') for k,label,v in
                        [('width_mm','Overall width',720),('depth_mm','Overall depth',500),('height_mm','Overall height',750)]]))
            def cad():
                proof=dict(valid_closed_solids=True,output_scope='WHOLE_FURNITURE')
                paths=['cabinet.step','cabinet.stl','cabinet.glb',STEM+'.dxf',STEM+'.pdf',STEM+'.svg']
                delivery=dict(download_base='/api/demo/delivery',cad_export_agreement=proof,items=[dict(path='cad/'+name,download_url='/api/demo/'+name) for name in paths])
                return dict(id=workflow,mode='IMAGE_TO_CAD',cad_source='ENGINEERING_CAD',project_id=project,
                    status='REVIEW_REQUIRED' if state['complete'] else 'RUNNING',reference_image_ids=state['cad'][-1]['image_ids'],
                    reference_views=state['cad'][-1].get('reference_views',[]),stage='engineering_dimensions',progress=100 if state['complete'] else 20,job_id=job,
                    result=dict(reference_image_ids=state['cad'][-1]['image_ids'],delivery=delivery,engineering_cad=dict(
                        workflow=dict(reference_image_ids=state['cad'][-1]['image_ids']),cad_export_agreement=proof,
                        visual_review=dict(visual=dict(accepted=True),critic=dict(accepted=True)))) if state['complete'] else None)
            def intercept(route):
                request=route.request;path=urlsplit(request.url).path
                if path.endswith('.png'):route.fulfill(content_type='image/png',body=png);return
                if path==f'/api/demo/{STEM}.svg':route.fulfill(content_type='image/svg+xml',body=(output/(STEM+'.svg')).read_bytes());return
                if path=='/api/v1/workspace/access':data=dict(mode='local',user=dict(id='owner',role='designer'))
                elif path=='/api/projects':data=[dict(id=project,name='Multi-angle furniture',owner_id='owner')]
                elif path=='/api/system/openai/status':data=dict(enabled=True,credentials_configured=True,models={role:'gpt-6.1-sol' for role in ('vision','design','review')})
                elif path=='/api/system/cad/status':data=dict(available=True)
                elif path==f'/api/projects/{project}':data=dict(id=project,name='Multi-angle furniture',workflows=([cad()] if state['cad'] else [])+[photo(r) for r in state['analyses'][::-1]],revisions=[])
                elif path.endswith('/upload-image'):
                    n=len(state['images'])+1;data=dict(id=f'00000000-0000-4000-8000-{n:012d}',kind='REFERENCE',download_url=f'/api/demo/reference{n}.png')
                    state['images'].append(data)
                elif path.endswith('/images'):data=state['images']
                elif path.endswith('/analyze'):
                    aid=f'00000000-0000-4000-8001-{len(state["analyses"])+1:012d}'
                    state['analyses'].append(dict(id=aid,input=request.post_data_json));data=dict(workflow_id=aid)
                elif path.endswith('/generate-all'):
                    state['cad'].append(request.post_data_json);data=dict(workflow_id=workflow,status='RUNNING')
                elif path.endswith('/workflows'):data=([cad()] if state['cad'] else [])+[photo(r) for r in state['analyses'][::-1]]
                elif path.startswith('/api/v1/agents/workflows/'):
                    wid=path.rsplit('/',1)[-1]
                    data=cad() if wid==workflow else photo(next(r for r in state['analyses'] if r['id']==wid))
                elif path.endswith('/components'):data=dict(components=[])
                elif path.endswith('/dimension-sheet'):data=dict(sheet=sheet,files={k:f'/api/demo/{STEM}.{k}' for k in ('pdf','dxf','zip')},preview_url=f'/api/demo/{STEM}.svg')
                else:unexpected.append(request.method+' '+path);route.abort();return
                route.fulfill(content_type='application/json',body=json.dumps(data))
            page.route('**/api/**',intercept)
            page.goto(args.url+f'/studio?project={project}&tab=reference',wait_until='domcontentloaded')
            upload=page.get_by_label('Upload furniture photo',exact=True)
            expect(upload).to_be_enabled(timeout=30000);assert upload.get_attribute('multiple') is not None
            def files(n):return [dict(name=f'angle-{i}.png',mimeType='image/png',buffer=png) for i in range(n)]
            upload.set_input_files(files(3))
            expect(page.get_by_role('heading',name='Review furniture measurements')).to_be_visible(timeout=30000)
            assert len(state['analyses'])==1 and len(state['analyses'][0]['input']['image_ids'])==3 and not state['cad']
            page.get_by_label('Angle for photo 1',exact=True).select_option('front')
            page.get_by_label('Angle for photo 2',exact=True).select_option('rear')
            page.get_by_label('Angle for photo 3',exact=True).select_option('left')
            page.get_by_role('button',name='Make primary',exact=True).last.click()
            page.get_by_role('button',name='Remove photo 3',exact=True).click()
            page.get_by_label('Add furniture angles',exact=True).set_input_files(files(2))
            expect(page.get_by_role('heading',name='Review furniture measurements')).to_be_visible(timeout=30000)
            assert len(state['analyses'][-1]['input']['image_ids'])==4
            before=(len(state['images']),len(state['analyses']))
            page.get_by_label('Add furniture angles',exact=True).set_input_files(files(3))
            expect(page.get_by_role('alert').filter(has_text='Choose up to 6 photos')).to_be_visible()
            assert before==(len(state['images']),len(state['analyses']))
            page.get_by_role('button',name='Dismiss error',exact=True).click()
            page.screenshot(path=str(output/'multiple-photos-desktop.png'),full_page=True)
            page.reload(wait_until='domcontentloaded')
            expect(page.get_by_role('heading',name='Review furniture measurements')).to_be_visible(timeout=30000)
            expect(page.get_by_label('Angle for photo 1',exact=True)).to_have_value('left')
            assert page.locator('.reference-view').count()==4
            page.set_viewport_size(dict(width=390,height=844));assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
            page.screenshot(path=str(output/'multiple-photos-mobile.png'),full_page=True)
            page.set_viewport_size(dict(width=1440,height=1100))
            page.get_by_role('button',name='Generate CAD files',exact=True).click()
            page.wait_for_function('document.querySelector("progress")!==null',timeout=30000)
            assert len(state['cad'])==1 and state['cad'][0]['image_ids']==state['analyses'][-1]['input']['image_ids']
            assert state['cad'][0]['reference_views']==state['analyses'][-1]['input']['reference_views']
            assert state['cad'][0]['image_id']==state['cad'][0]['image_ids'][0]
            state['complete']=True
            page.goto(args.url+f'/studio?project={project}&tab=manufacturing',wait_until='domcontentloaded')
            expect(page.get_by_text('Curve radii from CAD',exact=True)).to_be_visible(timeout=30000)
            page.get_by_text('Curve radii from CAD',exact=True).click()
            expect(page.get_by_role('cell',name='R20 mm',exact=True)).to_be_visible()
            page.screenshot(path=str(output/'radius-table-browser.png'),full_page=True)
            capture_drawing()
            assert not errors and not unexpected,(errors,unexpected)
            report=dict(multiple_file_selection=True,one_analysis_for_photo_set=True,angle_labels=True,primary_choice=True,
                selected_photo_removal=True,append_angles=True,six_photo_limit=True,selection_restored=True,
                complete_photo_set_sent_to_cad=True,mobile_overflow=False,radius_table_visible=True,
                javascript_errors=errors,unexpected_requests=unexpected,real_project_writes=0,paid_generation_requests=0)
            (output/'browser-verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report,indent=2))
        finally:browser.close()


if __name__=='__main__':main()
