"""Scoped chair-leg edits regenerate one coherent real-CAD revision and package."""
import copy
import json
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4
from zipfile import ZipFile

import cadquery as cq
import ezdxf
import pytest
from app.agents import orchestrator
from app.database import AgentWorkflow,Revision
from app.engineering_edits import EngineeringEditPatch,merge_edit,verify_edit_contract,verify_edit_solids
from app.photo_schema import PhotoDesign
from app.pipeline import canonical_hash,file_hash
from tests.test_agent_workflows import agents_app
from tests.test_engineering_cad import EngineeringGateway


def chair_plan(width=600):
    values=dict(width_mm=width,depth_mm=550,height_mm=900,seat_height_mm=450,seat_thickness_mm=30,leg_width_mm=40)
    parts=[]
    def add(label,size,position):
        parts.append(dict(part_id=f'F{len(parts)+1:03d}',label=label,geometry='box',size=size,position=position,
            material='solid_wood',color_rgb=[.5,.3,.18],evidence='Synthetic chair fixture, not image recognition'))
    add('Chair seat',['width_mm','depth_mm','seat_thickness_mm'],[0,0,'seat_height_mm-seat_thickness_mm/2'])
    for x in ('-width_mm/2+40','width_mm/2-40'):
        for y in ('-depth_mm/2+40','depth_mm/2-40'):
            add('Chair leg',['leg_width_mm','leg_width_mm','seat_height_mm-seat_thickness_mm'],[x,y,'(seat_height_mm-seat_thickness_mm)/2'])
    add('Chair back',['width_mm',25,'height_mm-seat_height_mm'],[0,'depth_mm/2-12.5','(height_mm+seat_height_mm)/2'])
    return PhotoDesign(furniture_type='Synthetic six-part chair',summary='Synthetic chair for post-generation editing',
        values=[dict(key=key,value=value,explanation='Synthetic estimate') for key,value in values.items()],
        parts=parts,assumptions=['Synthetic review model; physical measurements and joinery unverified'])


def patch_for(edit,style='round'):
    parts=[]
    for source in edit['source_plan']['parts']:
        if source['part_id'] not in edit['part_ids']:continue
        part=copy.deepcopy(source)
        if style=='round':part['geometry']='cylinder'
        else:
            part['geometry']='loft'
            half='(seat_height_mm-seat_thickness_mm)/2'
            part['sections']=[dict(center=[0,0,'-'+half],width='leg_width_mm*.65',depth='leg_width_mm*.65'),
                dict(center=[0,0,half],width='leg_width_mm',depth='leg_width_mm')]
        part['label']=style.capitalize()+' chair leg'
        parts.append(part)
    return EngineeringEditPatch(parts=parts,values=[],summary='Requested '+style+' legs',assumptions=['Synthetic requested profile; joinery unverified'])


@pytest.fixture
def edit_app(agents_app,monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','synthetic-no-paid-requests')
    monkeypatch.setattr('tests.test_engineering_cad.table_plan',chair_plan)
    EngineeringGateway.calls=[]
    class EditGateway(EngineeringGateway):
        def structured(self,role,instructions,payload,schema,tools=None,**options):
            if schema is EngineeringEditPatch:
                self.calls.append('EngineeringEditPatch')
                edit=dict(source_plan=payload['source_plan'],part_ids=payload['selected_part_ids'])
                patch=patch_for(edit,payload['component_edit']['style'])
                assert tools and options['require_tool']=='cad_generation'
                receipt=tools['cad_generation']['handler'](patch)
                assert receipt['valid'],receipt
                return patch
            return super().structured(role,instructions,payload,schema,tools=tools,**options)
    monkeypatch.setattr(orchestrator,'ResponsesGateway',EditGateway)
    client,app,headers,project,image=agents_app
    response=client.post(f'/api/projects/{project}/generate-all',headers=headers['operator'],
        json=dict(image_id=image,known_values=[dict(key='width_mm',value=600)],output_scope='PARTS'))
    source=client.get(response.json()['status_url'],headers=headers['operator']).json()
    assert source['status']=='REVIEW_REQUIRED',source.get('error')
    return agents_app,source


def submit(env,source,style='round',key='round-leg-edit'):
    client,app,headers,project,image=env
    return client.post(f'/api/projects/{project}/edit-cad',headers=headers['operator'],json=dict(
        source_workflow_id=source['id'],part_ids=['F002','F003','F004','F005'],style=style,
        instructions='Change the chair legs; retain mounting positions and overall size',idempotency_key=key))


def test_whole_furniture_edits_reuse_dimensions_and_keep_only_assembly_outputs(edit_app):
    env,source=edit_app;client,app,headers,project,image=env
    response=client.post(f'/api/projects/{project}/generate-all',headers=headers['operator'],json=dict(
        image_id=image,engineering_reference_workflow_id=source['id'],idempotency_key='whole-source'))
    assert response.status_code==202,response.text
    whole=client.get(response.json()['status_url'],headers=headers['operator']).json()
    assert whole['status']=='REVIEW_REQUIRED',whole.get('error')
    before=len(EngineeringGateway.calls);response=submit(env,whole)
    assert response.status_code==202,response.text
    edited=client.get(response.json()['status_url'],headers=headers['operator']).json()
    assert edited['status']=='REVIEW_REQUIRED',edited.get('error')
    assert EngineeringGateway.calls[before:]==['EngineeringEditPatch','EngineeringReview']
    directory=app.state.settings.storage/edited['job_id']
    assert not list(directory.glob('part_*')) and (directory/'all_dimensions_REVIEW.dxf').is_file()
    assert len(cq.importers.importStep(str(directory/'cabinet.step')).solids().vals())==6
    assert all('part_' not in item['path'] for item in edited['result']['delivery']['items'])


def test_chair_leg_edit_regenerates_models_drawings_schedules_and_archive(edit_app):
    env,source=edit_app;client,app,headers,project,image=env
    old=app.state.settings.storage/source['job_id'];original_hashes={name:file_hash(old/name) for name in ('manifest.json','design_review_package.zip','cabinet.glb')}
    original_calls=list(EngineeringGateway.calls)
    response=submit(env,source);assert response.status_code==202,response.text
    changed=client.get(response.json()['status_url'],headers=headers['operator']).json()
    assert changed['status']=='REVIEW_REQUIRED',changed.get('error')
    assert changed['job_id']!=source['job_id'] and changed['revision_id']!=source['revision_id']
    after=app.state.settings.storage/changed['job_id'];result=changed['result'];proof=result['engineering_cad']['cad_export_agreement']
    assert EngineeringGateway.calls[len(original_calls):]==['EngineeringEditPatch','EngineeringReview','EngineeringReview']
    assert proof['geometry_checks']['component_edit']['changed_part_ids']==['F002','F003','F004','F005']
    assert proof['geometry_checks']['component_edit']['unselected_part_ids_preserved']==['F001','F006']
    assert result['engineering_cad']['workflow']['component_edit']['source_workflow_id']==source['id']
    assert proof['geometry_checks']['assembly_connected'] and proof['all_part_steps_reopened']
    assert len(cq.importers.importStep(str(after/'cabinet.step')).solids().vals())==6
    for pid in ('F002','F003','F004','F005'):
        before=cq.importers.importStep(str(old/f'part_{pid}_REVIEW.step')).val()
        solid=cq.importers.importStep(str(after/f'part_{pid}_REVIEW.step')).val()
        assert solid.Volume()==pytest.approx(before.Volume()*3.141592653589793/4,rel=1e-7)
        drawing=ezdxf.readfile(after/f'part_{pid}_REVIEW.dxf')
        round_profiles=[entity.dxf.radius for entity in drawing.modelspace().query('CIRCLE')]
        round_profiles.extend(entity.dxf.major_axis.magnitude for entity in drawing.modelspace().query('ELLIPSE')
            if entity.dxf.ratio==pytest.approx(1,abs=1e-7))
        assert any(radius==pytest.approx(20,abs=1e-5) for radius in round_profiles)
        assert (after/f'part_{pid}_REVIEW.pdf').is_file() and (after/f'part_{pid}_REVIEW.glb').is_file()
    assert all(file_hash(old/name)==digest for name,digest in original_hashes.items())
    assert file_hash(after/'cabinet.glb')!=original_hashes['cabinet.glb']
    assert result['value_provenance']['width_mm']==source['result']['value_provenance']['width_mm']
    assert (after/'schedules_REVIEW.xlsx').is_file() and (after/'all_dimensions_REVIEW.pdf').is_file()
    delivery=result['delivery'];package=client.get(delivery['download_base']+'/furniture_complete_package.zip',headers=headers['operator'])
    with ZipFile(BytesIO(package.content)) as archive:
        assert archive.testzip() is None
        manifest=json.loads(archive.read('delivery_manifest.json'))
        assert manifest['job_id']==changed['job_id'] and manifest['revision_id']==changed['revision_id']
        assert {'cad/cabinet.step','cad/cabinet.glb','cad/cabinet.stl','cad/schedules_REVIEW.xlsx','cad/all_dimensions_REVIEW.pdf'}<=set(archive.namelist())
    state=client.get(f'/api/projects/{project}',headers=headers['operator']).json()
    assert state['model_scope']['job_id']==changed['job_id'] and state['delivery']['job_id']==changed['job_id']
    calls=list(EngineeringGateway.calls)
    assert submit(env,source).json()['workflow_id']==changed['id']
    assert EngineeringGateway.calls==calls
    assert submit(env,source,style='tapered').status_code==409
    second=submit(env,changed,style='tapered',key='taper-round-legs')
    second_state=client.get(second.json()['status_url'],headers=headers['operator']).json()
    assert second_state['status']=='REVIEW_REQUIRED',second_state.get('error')
    assert second_state['result']['engineering_cad']['workflow']['component_edit']['source_workflow_id']==changed['id']


def test_failed_delivery_keeps_saved_model_until_retry_finishes_whole_package(edit_app,monkeypatch):
    env,source=edit_app;client,app,headers,project,image=env
    from app import delivery
    real=delivery.build_delivery
    def fail(r,result):raise RuntimeError('Synthetic interrupted edited-package delivery')
    monkeypatch.setattr(delivery,'build_delivery',fail)
    response=submit(env,source);failed=client.get(response.json()['status_url'],headers=headers['operator']).json()
    assert failed['status']=='FAILED' and failed['job_id']!=source['job_id']
    state=client.get(f'/api/projects/{project}',headers=headers['operator']).json()
    assert state['model_scope']['job_id']==state['delivery']['job_id']==source['job_id']
    assert state['model_scope']['pending_edit_workflow_id']==failed['id']
    assert state['workflows'][0]['id']==failed['id'] and state['workflows'][0]['edit_source_workflow_id']==source['id']
    calls=list(EngineeringGateway.calls);monkeypatch.setattr(delivery,'build_delivery',real)
    assert client.post(f'/api/v1/agents/workflows/{failed["id"]}/retry',headers=headers['operator']).status_code==202
    complete=client.get(response.json()['status_url'],headers=headers['operator']).json()
    assert complete['status']=='REVIEW_REQUIRED' and EngineeringGateway.calls==calls
    state=client.get(f'/api/projects/{project}',headers=headers['operator']).json()
    assert state['model_scope']['job_id']==state['delivery']['job_id']==complete['job_id']


def test_edit_scope_and_saved_source_integrity_are_enforced(edit_app):
    env,source=edit_app;client,app,headers,project,image=env
    request=dict(source_workflow_id=source['id'],part_ids=['F999'],style='round',idempotency_key='bad-edit')
    assert client.post(f'/api/projects/{project}/edit-cad',headers=headers['operator'],json=request).status_code==409
    request['part_ids']=[]
    assert client.post(f'/api/projects/{project}/edit-cad',headers=headers['operator'],json=request).status_code==422
    request['part_ids']=['F002'];request['style']='custom'
    assert client.post(f'/api/projects/{project}/edit-cad',headers=headers['operator'],json=request).status_code==422
    other=client.post('/api/v1/projects',headers=headers['operator'],json=dict(name='Other project')).json()['id']
    request['style']='round'
    assert client.post(f'/api/projects/{other}/edit-cad',headers=headers['operator'],json=request).status_code==404
    with app.state.db.sessions.begin() as session:
        workflow=session.get(AgentWorkflow,source['id']);checkpoints=copy.deepcopy(workflow.checkpoints)
        checkpoints['engineering_plan']['payload']['summary']='Changed frozen source';workflow.checkpoints=checkpoints
    assert client.post(f'/api/projects/{project}/edit-cad',headers=headers['operator'],json=request).status_code==409


def test_patch_cannot_change_unselected_parts_or_existing_dimensions():
    original=chair_plan();edit=dict(source_plan=original.model_dump(mode='json'),source_plan_sha256=canonical_hash(original.model_dump(mode='json')),part_ids=['F002'])
    patch=patch_for(edit);candidate=merge_edit(edit,patch);verify_edit_contract(edit,candidate)
    candidate.parts[0].size[2]=99
    with pytest.raises(ValueError,match='unselected'):verify_edit_contract(edit,candidate)
    patch.values=[dict(key='leg_width_mm',value=99,explanation='Attempt to change a saved value')]
    with pytest.raises(ValueError,match='Existing dimensions'):merge_edit(edit,EngineeringEditPatch.model_validate(patch.model_dump()))


def test_actual_solid_scope_detects_contact_trimming_and_noop_edits():
    from app.agents.photo_agent import resolve_estimated_geometry
    original=chair_plan();_,_,_,before,_=resolve_estimated_geometry(original,dict(known_values=[]),'Original')
    edit=dict(source_workflow_id='synthetic',part_ids=['F001'])
    with pytest.raises(ValueError,match='did not change'):verify_edit_solids(edit,before,before)
    raw=original.model_dump(mode='json');raw['parts'][0]['size'][2]='seat_thickness_mm+15'
    _,_,_,after,_=resolve_estimated_geometry(PhotoDesign.model_validate(raw),dict(known_values=[]),'Changed seat')
    with pytest.raises(ValueError,match='unselected part'):verify_edit_solids(edit,before,after)
