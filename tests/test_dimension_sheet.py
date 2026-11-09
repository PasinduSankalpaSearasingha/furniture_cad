from dataclasses import replace
from io import BytesIO
import json
from uuid import uuid4
from zipfile import ZipFile
import xml.etree.ElementTree as ET

import cadquery as cq
import ezdxf
from pypdf import PdfReader
import pytest

from app.dimension_sheet import create_dimension_sheet,STEM,VERSION
from app.dimension_sheet_clip import clip_polyline
from app.agents.photo_agent import resolve_estimated_geometry
from app.pipeline import file_hash
from tests.test_agent_workflows import agents_app
from tests.test_engineering_cad import engineering_app,launch,table_plan


def test_combined_sheet_has_one_page_real_dimensions_and_native_dxf(tmp_path):
    _,spec,provenance,build,_=resolve_estimated_geometry(table_plan(720),
        dict(known_values=[dict(key='width_mm',value=720)]),'Table <A&B>')
    shapes={}
    for part,shape in build.shapes.items():
        path=tmp_path/(part+'.step');cq.exporters.export(shape,str(path));shapes[part]=cq.importers.importStep(str(path))
    metadata=create_dimension_sheet(spec,replace(build,shapes=shapes),tmp_path,provenance=provenance,job_id='saved-job')
    pdf=PdfReader(tmp_path/(STEM+'.pdf'))
    assert len(pdf.pages)==1
    text=pdf.pages[0].extract_text()
    for value in ('TOP VIEW','FRONT ELEVATION','SIDE ELEVATION','DETAIL A','SHEET 1 OF 1','720','500','750','F005'):
        assert value in text
    assert metadata['overall_dimensions_mm']==pytest.approx(dict(width=720,depth=500,height=750))
    assert len(metadata['parameters'])==len(spec.photo_design.values)
    assert len(metadata['parts'])==5 and metadata['parameters'][0]['source']=='USER_PROVIDED'
    root=ET.parse(tmp_path/(STEM+'.svg')).getroot()
    assert root.attrib['width']=='420mm' and 'TABLE <A&B>' in ''.join(root.itertext())
    dxf=ezdxf.readfile(tmp_path/(STEM+'.dxf'))
    assert dxf.units==4 and not dxf.audit().errors
    dimensions=list(dxf.modelspace().query('DIMENSION'))
    assert len(dimensions)==len(metadata['dimensions'])
    for entity,record in zip(dimensions,metadata['dimensions']):
        actual=entity.get_measurement()*entity.override().get('dimlfac',1)
        assert actual==pytest.approx(record['value_mm'],abs=1e-5)


def test_detail_crop_keeps_segments_inside_window():
    segments=list(clip_polyline([(-5,5),(5,5),(15,15)],(0,10,0,10)))
    assert segments==[[(0,5),(5,5)],[(5,5),(10,10)]]
    assert list(clip_polyline([(-5,-5),(-2,-2)],(0,10,0,10)))==[]


def test_completed_packages_and_existing_jobs_have_private_cached_sheet(engineering_app):
    client,app,headers,project,_=engineering_app
    state=launch(engineering_app);assert state['status']=='REVIEW_REQUIRED',state.get('error')
    job=state['job_id'];source=app.state.settings.storage/job
    original=file_hash(source/'manifest.json')
    with ZipFile(source/'design_review_package.zip') as archive:
        assert STEM+'.pdf' in archive.namelist() and STEM+'.svg' in archive.namelist()
    assert json.loads((source/'drawing_manifest.json').read_text())['combined_dimension_sheet']['pages']==1
    response=client.get(f'/api/v1/jobs/{job}/dimension-sheet',headers=headers['operator'])
    assert response.status_code==200,response.text
    data=response.json()
    assert data['sheet']['job_id']==job and data['sheet']['page']['pages']==1
    assert client.get(data['preview_url'],headers=headers['operator']).headers['content-type'].startswith('image/svg+xml')
    assert len(PdfReader(BytesIO(client.get(data['files']['pdf'],headers=headers['operator']).content)).pages)==1
    assert client.get(data['preview_url']).status_code==401
    assert client.get(f'/api/v1/jobs/{uuid4()}/dimension-sheet',headers=headers['operator']).status_code==404
    assert client.get(f'/api/v1/jobs/{job}/dimension-sheet/files/missing.pdf',headers=headers['operator']).status_code==404
    with ZipFile(BytesIO(client.get(data['files']['zip'],headers=headers['operator']).content)) as archive:
        assert archive.testzip() is None and STEM+'.dxf' in archive.namelist()
    again=client.get(f'/api/v1/jobs/{job}/dimension-sheet',headers=headers['operator'])
    assert again.json()==data and file_hash(source/'manifest.json')==original
    cached=app.state.settings.storage/f'dimension_sheet_{job}_v{VERSION}'/f'{STEM}.svg'
    cached.write_text('changed',encoding='utf-8')
    assert client.get(data['preview_url'],headers=headers['operator']).status_code==409
