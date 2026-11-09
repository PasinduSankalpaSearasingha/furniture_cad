"""Real CAD exports and AI-stage savings for the default whole-furniture workflow."""
from io import BytesIO
import json
from zipfile import ZipFile
import cadquery as cq
import ezdxf
from pypdf import PdfReader
import pytest
from app.agents import orchestrator
from app.dimension_sheet import STEM
from tests.test_agent_workflows import agents_app
from tests.test_engineering_cad import engineering_app,EngineeringGateway,launch


def generate(env,**changes):
    client,app,headers,project,image=env
    response=client.post(f'/api/projects/{project}/generate-all',headers=headers['operator'],json=dict(
        image_id=image,known_values=[dict(key='width_mm',value=720)],**changes))
    assert response.status_code==202,response.text
    result=client.get(response.json()['status_url'],headers=headers['operator']).json()
    assert result['status']=='REVIEW_REQUIRED',result.get('error')
    return result


def test_default_outputs_are_one_editable_furniture_sheet_and_assembly(engineering_app):
    env=engineering_app;client,app,headers,project,image=env;result=generate(env)
    assert EngineeringGateway.calls==['EngineeringDimensions','PhotoDesign','EngineeringReview']
    root=app.state.settings.storage/result['job_id'];engineering=result['result']['engineering_cad']
    assert engineering['workflow']['review_policy']=='LOCAL_GEOMETRY_AND_AI_VISUAL'
    assert engineering['cad_export_agreement']['valid_closed_solids']
    assert len(cq.importers.importStep(str(root/'cabinet.step')).solids().vals())==5
    assert not list(root.glob('part_*')) and not list(root.glob('*.xlsx'))
    assert not list(root.glob('.solid-verification-*'))
    sheet=json.loads((root/(STEM+'.json')).read_text())
    assert sheet['views']==['TOP','FRONT','SIDE','ISOMETRIC'] and sheet['parts']==[]
    assert sheet['overall_dimensions_mm']==pytest.approx(dict(width=720,depth=500,height=750))
    pdf=PdfReader(root/(STEM+'.pdf'));assert len(pdf.pages)==1
    text=pdf.pages[0].extract_text()
    for title in ('TOP VIEW','FRONT ELEVATION','SIDE ELEVATION','ISOMETRIC VIEW'):assert title in text
    assert 'PART DIMENSIONS' not in text and 'F001' not in text
    dxf=ezdxf.readfile(root/(STEM+'.dxf'));assert dxf.units==4 and not dxf.audit().errors
    for dimension,record in zip(dxf.modelspace().query('DIMENSION'),sheet['dimensions'],strict=True):
        assert dimension.get_measurement()==pytest.approx(record['value_mm'],abs=1e-5)
    expected={'cad/cabinet.step','cad/cabinet.glb','cad/cabinet.stl',*(f'cad/{STEM}.{kind}' for kind in ('pdf','dxf','svg'))}
    delivery=result['result']['delivery']
    assert {item['path'] for item in delivery['items']}==expected
    archive_response=client.get(delivery['download_base']+'/furniture_complete_package.zip',headers=headers['operator'])
    with ZipFile(BytesIO(archive_response.content)) as archive:
        assert set(archive.namelist())==expected and archive.testzip() is None
    components=client.get(f'/api/v1/jobs/{result["job_id"]}/components',headers=headers['operator']).json()['components']
    assert len(components)==5 and all(p['model_url'].endswith('/cabinet.glb') for p in components)
    response=client.get(f'/api/v1/jobs/{result["job_id"]}/dimension-sheet',headers=headers['operator'])
    assert response.status_code==200,response.text
    cached=response.json();assert cached['sheet']==sheet
    assert client.get(cached['files']['dxf'],headers=headers['operator']).content==(root/(STEM+'.dxf')).read_bytes()
    assert client.get(cached['preview_url']).status_code==401


def test_whole_workflow_removes_two_paid_ai_stages(engineering_app):
    legacy=launch(engineering_app);assert legacy['status']=='REVIEW_REQUIRED'
    assert EngineeringGateway.calls==['EngineeringDimensions','EngineeringAssembly','PhotoDesign','EngineeringReview','EngineeringReview']
    before=len(EngineeringGateway.calls);generate(engineering_app)
    assert EngineeringGateway.calls[before:]==['EngineeringDimensions','PhotoDesign','EngineeringReview']


def test_whole_frozen_plan_reexport_uses_no_ai(engineering_app,monkeypatch):
    env=engineering_app;client,app,headers,project,image=env;first=generate(env)
    def forbidden(*a,**k):raise AssertionError('Re-export must not initialize a paid gateway')
    monkeypatch.setattr(orchestrator,'ResponsesGateway',forbidden)
    monkeypatch.delenv('OPENAI_API_KEY');app.state.settings.ai_enabled=False
    second=generate(env,engineering_reference_workflow_id=first['id'])
    assert second['job_id']!=first['job_id']
    assert second['result']['engineering_cad']['cad_export_agreement']['output_scope']=='WHOLE_FURNITURE'


def test_whole_workflow_still_requires_actual_visual_review(engineering_app,monkeypatch):
    def failed(*a,**k):raise ValueError('Synthetic renderer failure')
    monkeypatch.setattr('app.ai.geometry_preview.render_build',failed)
    result=launch(engineering_app,output_scope='WHOLE_FURNITURE')
    assert result['status']=='REVIEW_REQUIRED' and result['error'] is None
    assert not result['result'].get('delivery') and result['result']['available_output']['has_saved_cad_plan']
    assert result['result']['approved_for_manufacturing'] is False
    assert EngineeringGateway.calls==['EngineeringDimensions','PhotoDesign']
