"""Image-specific optional inputs must drive real parts without fixing blank estimates."""
import json
from io import BytesIO
from uuid import uuid4

import cadquery as cq
import pytest
from PIL import Image
from pydantic import ValidationError
from app.agents import orchestrator
from app.api.image_to_cad import ImageToCADRequest
from app.database import AgentWorkflow
from app.geometry import exact_bounds
from app.pipeline import canonical_hash
from examples.ai_demo.mock_gateway import fixtures
from tests.test_agent_workflows import agents_app
from tests.test_engineering_cad import EngineeringGateway,table_plan
from tests.test_storage_completion import completed_sideboard


def table_analysis():
    vision=fixtures()['FurnitureVisionResult']
    vision.update(category='wooden table',observations=['Synthetic table with a top and four legs'])
    fields=[('width_mm','Overall width','mm',700),('depth_mm','Overall depth','mm',500),
        ('height_mm','Overall height','mm',750),('top_thickness_mm','Top thickness','mm',30),
        ('leg_width_mm','Leg width','mm',40)]
    return dict(vision=vision,matching_inputs=[dict(key=key,label=label,unit=unit,estimated_value=value,
        explanation='Synthetic image-specific suggestion, not a measured size') for key,label,unit,value in fields],
        summary='Synthetic table analysis with optional part measurements')


@pytest.fixture
def custom_app(agents_app,monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','synthetic-no-paid-requests')
    class ItemGateway(EngineeringGateway):
        def structured(self,role,instructions,payload,schema,**options):
            if schema.__name__=='PhotoAnalysis':
                self.calls.append(schema.__name__)
                return schema.model_validate(table_analysis())
            if schema.__name__=='EngineeringDimensions':
                assert payload['image_analysis']['vision']['category']=='wooden table'
            known={entry['key']:entry['value'] for entry in payload.get('known_values',[])}
            def customised(width=700):
                plan=table_plan(width)
                for value in plan.values:value.value=known.get(value.key,value.value)
                return plan
            with monkeypatch.context() as scope:
                scope.setattr('tests.test_engineering_cad.table_plan',customised)
                return super().structured(role,instructions,payload,schema,**options)
    EngineeringGateway.calls=[]
    monkeypatch.setattr(orchestrator,'ResponsesGateway',ItemGateway)
    return agents_app


def analyse(env):
    client,app,headers,project,image=env
    response=client.post(f'/api/projects/{project}/analyze',headers=headers['operator'],
        json=dict(image_ids=[image],idempotency_key='image-specific-analysis'))
    assert response.status_code==202,response.text
    state=client.get(response.json()['status_url'],headers=headers['operator']).json()
    assert state['status']=='SUCCEEDED' and state['job_id'] is None and state['revision_id'] is None
    assert state['reference_image_ids']==[image]
    assert state['result']['matching_inputs'][3]['key']=='top_thickness_mm'
    return state


@pytest.mark.parametrize('provided',[[],[dict(key='width_mm',value=920),dict(key='top_thickness_mm',value=42)]])
def test_analyse_then_customise_real_parts_or_estimate_every_blank(custom_app,provided):
    client,app,headers,project,image=custom_app
    analysis=analyse(custom_app)
    assert EngineeringGateway.calls==['PhotoAnalysis']
    request=dict(image_id=image,analysis_workflow_id=analysis['id'],known_values=provided,idempotency_key='custom-cad')
    response=client.post(f'/api/projects/{project}/generate-all',headers=headers['operator'],json=request)
    assert response.status_code==202,response.text
    state=client.get(response.json()['status_url'],headers=headers['operator']).json()
    assert state['status']=='REVIEW_REQUIRED',state.get('error')
    result=state['result'];sources=result['value_provenance'];custom={entry['key']:entry['value'] for entry in provided}
    assert sources['top_thickness_mm']['value']==custom.get('top_thickness_mm',30)
    assert sources['top_thickness_mm']['source']==('USER_PROVIDED' if provided else 'AI_ESTIMATED')
    assert sources['width_mm']['value']==custom.get('width_mm',700)
    assert sources['height_mm']['source']==sources['depth_mm']['source']=='AI_ESTIMATED'
    solids=cq.importers.importStep(str(app.state.settings.storage/state['job_id']/'cabinet.step')).solids().vals()
    solid=max(solids,key=lambda shape:shape.Center().z)
    bounds=exact_bounds(solid)
    assert bounds.xlen==pytest.approx(custom.get('width_mm',700))
    assert bounds.zlen==pytest.approx(custom.get('top_thickness_mm',30))
    assert result['engineering_cad']['workflow']['analysis_workflow_id']==analysis['id']
    assert len(state['dimensions']['fields'])==5
    with app.state.db.sessions() as session:
        workflow=session.get(AgentWorkflow,state['id'])
        assert canonical_hash(workflow.input_data)==workflow.input_hash
        assert workflow.input_data['user_known_values']==provided
        assert workflow.input_data['source_photo_analysis']['matching_inputs']==analysis['result']['matching_inputs']
        source=session.get(AgentWorkflow,analysis['id'])
        assert source.job_id is None and source.state=='SUCCEEDED'
    calls=list(EngineeringGateway.calls)
    assert client.post(f'/api/projects/{project}/generate-all',headers=headers['operator'],json=request).json()['workflow_id']==state['id']
    assert EngineeringGateway.calls==calls


def test_analysis_cannot_be_reused_for_other_image_or_modified_checkpoint(custom_app,monkeypatch):
    client,app,headers,project,image=custom_app;analysis=analyse(custom_app)
    monkeypatch.setattr(orchestrator,'dispatch_workflow',lambda *args:None)
    request=dict(image_id=image,analysis_workflow_id=analysis['id'])
    content=BytesIO();Image.new('RGB',(32,32),'navy').save(content,format='PNG')
    other=client.post(f'/api/projects/{project}/upload-image',headers=headers['operator'],
        files={'file':('other.png',content.getvalue(),'image/png')}).json()
    assert client.post(f'/api/projects/{project}/generate-all',headers=headers['operator'],json={**request,'image_id':other['id']}).status_code==422
    assert client.post(f'/api/projects/{project}/generate-all',headers=headers['operator'],
        json={**request,'known_values':[dict(key='drawer_height_mm',value=70)]}).status_code==422
    assert client.post(f'/api/projects/{uuid4()}/generate-all',headers=headers['operator'],json=request).status_code==404
    with app.state.db.sessions.begin() as session:
        workflow=session.get(AgentWorkflow,analysis['id']);checkpoints=json.loads(json.dumps(workflow.checkpoints))
        checkpoints['photo_analysis']['payload']['summary']='Changed analysis'
        workflow.checkpoints=checkpoints
    assert client.post(f'/api/projects/{project}/generate-all',headers=headers['operator'],json=request).status_code==409


@pytest.mark.parametrize('key,value',[('top_thickness_mm',0),('width_mm',9),('leg_count',2.5),('back_angle_deg',181),('unknown',25)])
def test_invalid_custom_dimension_ranges_are_rejected(key,value):
    with pytest.raises(ValidationError):
        ImageToCADRequest(image_id=uuid4(),known_values=[dict(key=key,value=value)])


def test_custom_angles_counts_and_subten_millimetre_parts_are_valid():
    values=[dict(key=key,value=value) for key,value in [('back_angle_deg',-12),('leg_count',4),('glass_thickness_mm',6)]]
    assert len(ImageToCADRequest(image_id=uuid4(),known_values=values).known_values)==3


def test_drawer_count_counts_drawers_instead_of_individual_box_walls(completed_sideboard):
    from app.agents.photo_agent import specification
    from app.ai.openai_client import AIServiceError
    from app.photo_schema import PhotoDesign
    _,_,completed,_,_=completed_sideboard
    raw=completed.model_dump(mode='json')
    raw['values'].append(dict(key='drawer_count',value=4,explanation='Synthetic custom count'))
    spec,_=specification(PhotoDesign.model_validate(raw),dict(known_values=[dict(key='drawer_count',value=4)]),'Four drawers')
    assert len(spec.photo_design.parts)==37
    raw['values'][-1]['value']=3
    with pytest.raises(AIServiceError,match='component plan'):
        specification(PhotoDesign.model_validate(raw),dict(known_values=[dict(key='drawer_count',value=3)]),'Wrong drawer count')
