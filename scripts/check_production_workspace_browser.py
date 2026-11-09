"""Intercepted production UI checks: no provider calls or saved project mutations."""
import argparse
from io import BytesIO
import json
from pathlib import Path
import struct
from urllib.parse import urlsplit
from PIL import Image
from playwright.sync_api import sync_playwright,expect


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8000');args=parser.parse_args()
    output=Path('outputs/production-workspace-browser');output.mkdir(parents=True,exist_ok=True)
    ids={key:f'00000000-0000-4000-8000-{number:012}' for number,key in enumerate(['project','image','analysis','workflow','job'],501)}
    project,image,analysis_id,wid,job=(ids[key] for key in ['project','image','analysis','workflow','job'])
    pixels=BytesIO();Image.new('RGB',(120,90),(126,107,73)).save(pixels,format='PNG');png=pixels.getvalue()
    binary=struct.pack('<9f',0,0,0,1,0,0,0,1,0)
    doc=dict(asset=dict(version='2.0'),scene=0,scenes=[dict(nodes=[0])],nodes=[dict(mesh=0,name='F001')],
        meshes=[dict(primitives=[dict(attributes=dict(POSITION=0),mode=4)])],buffers=[dict(byteLength=len(binary))],
        bufferViews=[dict(buffer=0,byteLength=len(binary))],accessors=[dict(bufferView=0,componentType=5126,count=3,type='VEC3',min=[0,0,0],max=[1,1,0])])
    raw=json.dumps(doc).encode();raw+=b' '*((-len(raw))%4)
    glb=struct.pack('<III',0x46546c67,2,28+len(raw)+len(binary))+struct.pack('<II',len(raw),0x4e4f534a)+raw+struct.pack('<II',len(binary),0x004e4942)+binary
    fields=[dict(key=key,label=label,unit=unit,estimated_value=value) for key,label,unit,value in [
        ('width_mm','Overall width','mm',700),('height_mm','Overall height','mm',750),('depth_mm','Overall depth','mm',500),
        ('top_thickness_mm','Top thickness','mm',30),('leg_count','Leg count','count',4),('leg_angle_deg','Leg angle','degrees',8)]]
    errors=[];unexpected=[];report={}
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True,
            args=['--use-angle=swiftshader','--enable-unsafe-swiftshader'])
        try:
            def scenario(name,custom=False,fail=False,configured=True,shared=False,accepted=False,existing=False,furniture='Table',stale_intent=False,reject_photo=False):
                context=browser.new_context(viewport=dict(width=1440,height=1000));page=context.new_page()
                image_fields=fields if furniture=='Table' else [*fields[:3],
                    dict(key='seat_height_mm' if furniture=='Chair' else 'shelf_thickness_mm',
                        label='Seat height' if furniture=='Chair' else 'Shelf thickness',unit='mm',estimated_value=450 if furniture=='Chair' else 18)]
                if stale_intent:context.add_init_script('sessionStorage.setItem('+json.dumps('fiske:pending-cad:'+project)+','+
                    json.dumps(json.dumps(dict(project=project,image=image,analysis=analysis_id,key='old-automatic-intent')))+')')
                state=dict(created=existing,uploaded=False,analysed=False,analysis_complete=False,started=False,complete=False,analyses=0,requests=[],signed_in=False)
                page.on('pageerror',lambda error:errors.append(str(error)))
                proof=dict(valid_closed_solids=True,part_count=1,preview_tessellation_mm=.1)
                delivery=dict(download_base='/api/qa/delivery',items=[dict(path='cad/cabinet.step',size_bytes=30,download_url='/api/qa/cabinet.step'),
                    dict(path='cad/report.json',size_bytes=30,download_url='/api/qa/report.json',preview_url='/api/qa/report.json/preview')],cad_export_agreement=proof)
                def photo():return dict(id=analysis_id,mode='PHOTO_ANALYZE',project_id=project,reference_image_ids=[image],
                    status='FAILED' if fail else 'SUCCEEDED' if state['analysis_complete'] else 'RUNNING',stage='photo_analysis',progress=100 if state['analysis_complete'] else 10,
                    result=dict(vision=dict(category=furniture),matching_inputs=image_fields,summary='Measurements identified for this uploaded furniture') if state['analysis_complete'] and not fail else None,
                    error=dict(code='AI_AUTHENTICATION_FAILED',message='Backend OpenAI credentials were rejected (HTTP 401)',retryable=False) if fail else None)
                def workflow():return dict(id=wid,mode='IMAGE_TO_CAD',cad_source='ENGINEERING_CAD',project_id=project,reference_image_ids=[image],job_id=job,
                    status='REVIEW_REQUIRED' if state['complete'] else 'RUNNING',stage='review_required' if state['complete'] else 'engineering_dimensions',progress=100 if state['complete'] else 20,
                    result=dict(reference_image_ids=[image],engineering_cad=dict(cad_export_agreement=proof,visual_review=dict(critic=dict(accepted=True),visual=dict(accepted=not reject_photo,findings=['The support shape differs from the reference.'] if reject_photo else []))),delivery=delivery) if state['complete'] else None)
                def old_workflow():return dict(id='saved-old-workflow',mode='IMAGE_TO_CAD',cad_source='ENGINEERING_CAD',project_id=project,reference_image_ids=['saved-old-photo'],
                    status='REVIEW_REQUIRED',stage='review_required',progress=100,job_id=job,
                    result=dict(reference_image_ids=['saved-old-photo'],engineering_cad=dict(cad_export_agreement=proof),delivery=delivery))
                def intercept(route):
                    request=route.request;path=urlsplit(request.url).path;status=200
                    if path.endswith('.png'):route.fulfill(content_type='image/png',body=png);return
                    if path.endswith('.glb'):route.fulfill(content_type='model/gltf-binary',body=glb);return
                    if path=='/api/qa/delivery/furniture_complete_package.zip':route.fulfill(content_type='application/zip',headers={'Content-Disposition':'attachment; filename=furniture.zip'},body=b'PK\x05\x06'+b'\0'*18);return
                    if path=='/api/v1/workspace/access':data=dict(mode='authenticated',user=None) if shared else dict(mode='local',user=dict(id='local',role='designer',username='My workspace'))
                    elif path=='/api/v1/auth/me':
                        status=200 if state['signed_in'] else 401;data=dict(id='owner',role='designer',username='Owner') if state['signed_in'] else dict(error=dict(message='Authentication required'))
                    elif path=='/api/v1/workspace/session':state['signed_in']=request.method=='POST';data=dict(authenticated=state['signed_in']);status=200 if state['signed_in'] else 204
                    elif path=='/api/projects':
                        if request.method=='POST':state['created']=True;data=dict(id=project,name=request.post_data_json['name'])
                        else:data=[dict(id=project,name='Synthetic table')] if state['created'] else []
                    elif path==f'/api/projects/{project}':data=dict(id=project,name='Synthetic table',workflows=[workflow()] if state['started'] else [old_workflow()] if existing else [],revisions=[])
                    elif path.endswith('/upload-image'):state['uploaded']=True;data=dict(id=image,kind='REFERENCE',download_url='/api/qa/reference.png')
                    elif path.endswith('/analyze'):state['analysed']=True;state['analyses']+=1;data=dict(workflow_id=analysis_id)
                    elif path.endswith('/images'):data=[dict(id=image,kind='REFERENCE',download_url='/api/qa/reference.png')] if state['uploaded'] else []
                    elif path.endswith('/workflows'):data=([workflow()] if state['started'] else [])+([photo()] if state['analysed'] else [])+([old_workflow()] if existing else [])
                    elif path==f'/api/v1/agents/workflows/{analysis_id}':data=photo()
                    elif path==f'/api/v1/agents/workflows/{wid}':data=workflow()
                    elif path=='/api/v1/agents/workflows/saved-old-workflow':data=old_workflow()
                    elif path.endswith('/generate-all'):
                        state['started']=True;state['requests'].append(request.post_data_json)
                        if accepted:state['complete']=True
                        data=dict(workflow_id=wid,status='REVIEW_REQUIRED' if accepted else 'QUEUED')
                    elif path.endswith('/components'):data=dict(components=[])
                    elif path.endswith('/dimension-sheet'):data=dict(files={kind:f'/api/qa/sheet.{kind}' for kind in ('pdf','dxf','zip')},preview_url='/api/qa/sheet.png',sheet=dict(parameters=[],parts=[]))
                    elif path.endswith('/openai/status'):data=dict(enabled=configured,credentials_configured=configured)
                    elif path.endswith('/cad/status'):data=dict(available=True)
                    elif path=='/api/qa/report.json/preview':data=dict(text=json.dumps(proof))
                    else:unexpected.append(request.method+' '+path);data={}
                    route.fulfill(status=status,content_type='application/json',body='' if status==204 else json.dumps(data))
                page.route('**/api/**',intercept);page.goto(args.url+'/',wait_until='domcontentloaded')
                if shared:
                    expect(page.get_by_role('heading',name='Sign in to your workspace')).to_be_visible()
                    page.get_by_label('Username',exact=True).fill('owner');page.get_by_label('Password',exact=True).fill('test-password')
                    page.get_by_role('button',name='Sign in',exact=True).click()
                expect(page.get_by_role('heading',name='Upload a furniture photo')).to_be_visible()
                assert page.locator('.nav-item').count()==3
                assert not any(label in page.locator('body').inner_text() for label in ['Classic workspace','Blender','Concept generator','Single source geometry'])
                if not configured:
                    expect(page.get_by_label('Upload furniture photo',exact=True)).to_be_disabled()
                    assert not state['requests'];context.close();return
                page.get_by_label('Upload furniture photo',exact=True).set_input_files(dict(name='table.png',mimeType='image/png',buffer=png))
                expect(page.get_by_role('region',name='Generation progress')).to_be_visible()
                if fail:
                    expect(page.get_by_text('Photo analysis stopped',exact=True)).to_be_visible(timeout=20000)
                    assert not state['requests'];context.close();return
                if not custom:
                    page.reload(wait_until='domcontentloaded')
                    expect(page.get_by_role('heading',name='Upload a furniture photo')).to_be_visible()
                state['analysis_complete']=True
                expect(page.get_by_role('heading',name='Review furniture measurements',exact=True)).to_be_visible(timeout=20000)
                assert not state['requests'],'CAD must wait for the user to review the furniture measurements'
                for field in image_fields:expect(page.get_by_label(field['label'],exact=True)).to_have_value(str(field['estimated_value']))
                if furniture!='Table':expect(page.get_by_label('Top thickness',exact=True)).to_have_count(0)
                if furniture=='Chair':expect(page.get_by_label('Seat height',exact=True)).to_be_visible()
                if furniture=='Cabinet':expect(page.get_by_label('Shelf thickness',exact=True)).to_be_visible()
                if custom:
                    expect(page.get_by_label('Top thickness',exact=True)).to_be_visible(timeout=20000)
                    assert not state['requests']
                    page.get_by_label('Top thickness',exact=True).fill('42')
                    page.get_by_label('Leg count',exact=True).fill('4.5')
                    page.get_by_role('button',name='Generate CAD files',exact=True).click()
                    expect(page.get_by_role('alert')).to_contain_text('Enter a valid leg count')
                    assert not state['requests']
                    page.get_by_label('Leg count',exact=True).fill('4');page.get_by_label('Leg angle',exact=True).fill('-5')
                page.get_by_role('button',name='Generate CAD files',exact=True).click()
                expect(page.get_by_role('heading',name='Model & edit',exact=True)).to_be_visible(timeout=20000)
                assert len(state['requests'])==1 and state['analyses']==1
                request=state['requests'][0]
                assert request['analysis_workflow_id']==analysis_id and request['cad_source']=='ENGINEERING_CAD'
                assert request['known_values']==([dict(key='top_thickness_mm',value=42),dict(key='leg_count',value=4),dict(key='leg_angle_deg',value=-5)] if custom else [])
                state['complete']=True
                expect(page.get_by_role('link',name='Download CAD package (.zip)',exact=False)).to_have_attribute('href','/api/qa/delivery/furniture_complete_package.zip',timeout=20000)
                if reject_photo:
                    expect(page.get_by_role('region',name='Photo comparison')).to_contain_text('This draft still differs from your photo.')
                    expect(page.get_by_role('region',name='Photo comparison').locator('img')).to_have_count(2)
                page.get_by_role('button',name='CAD files',exact=True).click()
                expect(page.get_by_role('link',name='Download STEP',exact=True)).to_have_attribute('href','/api/qa/cabinet.step')
                with page.expect_download() as download:page.get_by_role('link',name='Download CAD package (.zip)',exact=False).click()
                assert download.value.suggested_filename=='furniture.zip'
                page.get_by_text('Browse all 2 files',exact=True).click();page.get_by_text('cad/report.json',exact=True).click()
                expect(page.locator('.delivery-preview pre')).to_contain_text('valid_closed_solids')
                page.screenshot(path=str(output/(name+'-desktop.png')),full_page=True)
                page.set_viewport_size(dict(width=390,height=844));assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
                page.screenshot(path=str(output/(name+'-mobile.png')),full_page=True)
                page.reload(wait_until='domcontentloaded');expect(page.get_by_role('heading',name='Draft CAD / closer photo match needed' if reject_photo else 'Your CAD files are ready')).to_be_visible()
                assert len(state['requests'])==1
                page.get_by_role('button',name='+ New design',exact=True).click()
                expect(page.get_by_role('heading',name='Upload a furniture photo')).to_be_visible()
                assert page.locator('.viewer-layout').count()==0 and 'project=' not in page.url
                if shared:
                    page.get_by_role('button',name='Sign out',exact=True).click();expect(page.get_by_role('heading',name='Sign in to your workspace')).to_be_visible()
                else:assert page.locator('input[type=password]').count()==0
                report[name]=dict(analysis_requests=state['analyses'],cad_requests=len(state['requests']),download=True,mobile_overflow=False)
                context.close()
            scenario('measurements-first')
            scenario('custom-dimensions',custom=True)
            scenario('existing-design-upload',existing=True)
            scenario('already-accepted-job',accepted=True)
            scenario('analysis-failure',fail=True)
            scenario('missing-configuration',configured=False)
            scenario('shared-workspace',shared=True)
            scenario('chair-measurements',furniture='Chair')
            scenario('cabinet-measurements',furniture='Cabinet')
            scenario('old-intent-requires-review',stale_intent=True)
            scenario('photo-mismatch-is-a-draft',reject_photo=True)
            assert not errors and not unexpected,(errors,unexpected)
            report.update(analysis_failure_stops_generation=True,measurement_review_required=True,item_specific_estimates_prefilled=True,
                missing_configuration_blocks_upload=True,shared_sign_in_and_sign_out=True,javascript_errors=errors,paid_requests=0)
            (output/'audit.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report,indent=2),flush=True)
        finally:browser.close()


if __name__=='__main__':main()
