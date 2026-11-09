"""One furniture model from an ordered set of private reference photographs."""
from io import BytesIO
from PIL import Image
import pytest
from app.agents import orchestrator
from app.agents.photo_agent import PhotoAnalysis
from app.database import AgentWorkflow
from tests.test_agent_workflows import agents_app
from tests.test_engineering_cad import engineering_app,EngineeringGateway
from tests.photo_fixtures import analysis_fixture


def upload(client,headers,project,color='blue'):
    b=BytesIO();Image.new('RGB',(80,80),color).save(b,format='PNG')
    r=client.post(f'/api/projects/{project}/upload-image',headers=headers['operator'],files={'file':('other-angle.png',b.getvalue(),'image/png')})
    assert r.status_code==201,r.text
    return r.json()['id']


def observe(monkeypatch):
    original=EngineeringGateway.structured;seen=[]
    def structured(self,role,instructions,payload,schema,images=(),**options):
        seen.append(dict(schema=schema.__name__,images=len(images),views=payload.get('reference_views'),reference_count=payload.get('reference_image_count')))
        assert 'SAME furniture' in instructions
        if schema is PhotoAnalysis:
            result=analysis_fixture();result['matching_inputs']=result['matching_inputs'][:3]
            return PhotoAnalysis.model_validate(result)
        return original(self,role,instructions,payload,schema,images=images,**options)
    monkeypatch.setattr(EngineeringGateway,'structured',structured)
    return seen


def analyze(env,views):
    client,app,h,project,_=env
    r=client.post(f'/api/projects/{project}/analyze',headers=h['operator'],json=dict(
        image_ids=[v['image_id'] for v in views],reference_views=views))
    assert r.status_code==202,r.text
    state=client.get(r.json()['status_url'],headers=h['operator']).json()
    assert state['status']=='SUCCEEDED',state.get('error')
    return state


def test_all_views_reach_analysis_planning_and_actual_cad_review(engineering_app,monkeypatch):
    client,app,h,project,primary=engineering_app;seen=observe(monkeypatch)
    secondary=upload(client,h,project)
    views=[dict(image_id=primary,angle='front'),dict(image_id=secondary,angle='rear')]
    analysis=analyze(engineering_app,views)
    assert analysis['reference_views']==views
    r=client.post(f'/api/projects/{project}/generate-all',headers=h['operator'],json=dict(
        image_id=primary,image_ids=[primary,secondary],reference_views=views,analysis_workflow_id=analysis['id'],
        known_values=[dict(key='width_mm',value=720)]))
    assert r.status_code==202,r.text
    state=client.get(r.json()['status_url'],headers=h['operator']).json()
    assert state['status']=='REVIEW_REQUIRED',state.get('error')
    assert [(v['schema'],v['images']) for v in seen]==[('PhotoAnalysis',2),('EngineeringDimensions',2),('PhotoDesign',2),('EngineeringReview',9)]
    assert all(v['views']==views and v['reference_count']==2 for v in seen)
    assert state['result']['engineering_cad']['cad_export_agreement']['part_count']==5
    assert state['result']['engineering_cad']['workflow']['reference_views']==views
    assert state['result']['reference_image_ids']==[primary,secondary]
    def forbidden(*a,**k):raise AssertionError('Saved multi-photo CAD re-export must use no AI')
    monkeypatch.setattr(orchestrator,'ResponsesGateway',forbidden)
    r=client.post(f'/api/projects/{project}/generate-all',headers=h['operator'],json=dict(
        image_id=primary,engineering_reference_workflow_id=state['id']))
    assert r.status_code==202,r.text
    again=client.get(r.json()['status_url'],headers=h['operator']).json()
    assert again['status']=='REVIEW_REQUIRED',again.get('error')
    assert again['reference_image_ids']==[primary,secondary] and again['reference_views']==views


def test_generation_cannot_substitute_or_reorder_analyzed_views(engineering_app,monkeypatch):
    client,app,h,project,primary=engineering_app;observe(monkeypatch)
    secondary=upload(client,h,project);third=upload(client,h,project,'red')
    views=[dict(image_id=primary,angle='front'),dict(image_id=secondary,angle='rear')]
    analysis=analyze(engineering_app,views)
    base=dict(image_id=primary,analysis_workflow_id=analysis['id'])
    for changes in [dict(image_ids=[primary]),dict(image_ids=[primary,third]),
        dict(image_id=secondary,image_ids=[secondary,primary]),
        dict(image_ids=[primary,secondary],reference_views=[dict(image_id=primary,angle='top'),views[1]])]:
        r=client.post(f'/api/projects/{project}/generate-all',headers=h['operator'],json={**base,**changes})
        assert r.status_code==422,r.text
    # Legacy callers supplying only the primary ID inherit the complete, frozen analysis set.
    monkeypatch.setattr(orchestrator,'dispatch_workflow',lambda *a:None)
    r=client.post(f'/api/projects/{project}/generate-all',headers=h['operator'],json=base)
    assert r.status_code==202,r.text
    with app.state.db.sessions() as session:
        row=session.get(AgentWorkflow,r.json()['workflow_id'])
        assert row.input_data['image_ids']==[primary,secondary] and row.input_data['reference_views']==views


def test_photo_set_limits_duplicates_angles_and_project_ownership(engineering_app,monkeypatch):
    client,app,h,project,primary=engineering_app
    monkeypatch.setattr(orchestrator,'dispatch_workflow',lambda *a:None)
    ids=[primary]+[upload(client,h,project,color) for color in ['red','blue','green','yellow','cyan']]
    r=client.post(f'/api/projects/{project}/analyze',headers=h['operator'],json=dict(image_ids=ids))
    assert r.status_code==202,r.text
    for body in [dict(image_ids=ids+[ids[0]]),dict(image_ids=[primary,primary]),
        dict(image_ids=[primary],reference_views=[dict(image_id=primary,angle='invalid')]),
        dict(image_ids=[primary],reference_views=[dict(image_id=ids[1],angle='front')])]:
        assert client.post(f'/api/projects/{project}/analyze',headers=h['operator'],json=body).status_code==422
    foreign=client.post('/api/projects',headers=h['operator'],json={'name':'Other furniture'}).json()['id']
    foreign_image=upload(client,h,foreign)
    assert client.post(f'/api/projects/{project}/generate-all',headers=h['operator'],json=dict(
        image_id=primary,image_ids=[primary,foreign_image])).status_code==404
    assert client.post(f'/api/projects/{project}/analyze',headers=h['operator'],json=dict(image_ids=[primary,foreign_image])).status_code==404
