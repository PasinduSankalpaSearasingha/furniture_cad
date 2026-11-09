"""Isolated drawing failure recovery and opt-in real FreeCAD cross-kernel checks."""
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
from zipfile import ZipFile

import cadquery as cq
from pypdf import PdfReader
import pytest
from reportlab.pdfgen import canvas

from app.techdraw import drawing_request,enhance_drawings,validate_exports
from tests.test_agent_workflows import agents_app
from tests.test_engineering_edits import edit_app,submit


def source_package(directory):
    directory.mkdir(exist_ok=True)
    path=directory/'part_F001_REVIEW.step';path.write_text('synthetic STEP sentinel')
    part=dict(part_id='F001',label='Part',length_axis=[1,0,0],thickness_axis=[0,0,1],
        length_mm=100,width_mm=60,thickness_mm=20)
    proof=dict(part_id='F001',step_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),step_volume_mm3=120000)
    return SimpleNamespace(project_name='Native check',revision=2),[part],[proof],dict(width=100,depth=60,height=20)


def fake_exports(request,directory):
    pages={};dimensions=[]
    for sheet in request['sheets']:
        pid=sheet['part_id'];stem='techdraw_assembly_REVIEW' if pid=='ASSEMBLY' else f'part_{pid}_techdraw_REVIEW'
        pdf=canvas.Canvas(str(directory/(stem+'.pdf')));pdf.drawString(20,20,'REVIEW');pdf.showPage();pdf.save()
        (directory/(stem+'.svg')).write_text('<svg xmlns="http://www.w3.org/2000/svg" width="420mm" height="297mm"><text x="20" y="20">REVIEW</text></svg>')
        pages[pid]=dict(pdf=stem+'.pdf',svg=stem+'.svg')
        dimensions.extend(dict(part_id=pid,view=view['name'],axis=axis,value_mm=value)
            for view in sheet['views'] for axis,value in enumerate(view['expected_mm']))
    paper=[]
    for sheet in request['sheets']:
        scale=min(105/max(v['expected_mm'][0] for v in sheet['views']),100/max(v['expected_mm'][1] for v in sheet['views']),1)
        paper.extend(dict(part_id=sheet['part_id'],view=v['name'],scale=scale,
            center_mm=[75+i*132,155],envelope_mm=[n*scale for n in v['expected_mm']])
            for i,v in enumerate(sheet['views']))
    report=dict(engine='FreeCAD TechDraw',units='mm',version='synthetic',pages=pages,dimensions=dimensions,
        native_document_reopened=True,paper_views=paper,
        source_parts=[dict(part_id=p['part_id'],step_sha256=p['step_sha256'],solid_count=1,valid=True,
            volume_mm3=p['volume_mm3']) for p in request['parts']])
    (directory/'techdraw_verification.json').write_text(json.dumps(report))
    with ZipFile(directory/'furniture_REVIEW.FCStd','w') as archive:
        archive.writestr('Document.xml','<Document><Objects><Object type="Part::Feature" name="F001"/></Objects></Document>')
    return report


def test_verified_native_set_is_published_without_changing_step_or_existing_drawings(tmp_path,monkeypatch):
    spec,parts,proofs,overall=source_package(tmp_path)
    original=tmp_path/'drawings_REVIEW.pdf';original.write_bytes(b'original vector drawing')
    calls=[]
    def run(command,**kwargs):
        calls.append(command)
        mount=next(arg for arg in command if arg.startswith('type=bind,source=') and arg.endswith('target=/output'))
        stage=Path(mount.split('source=',1)[1].rsplit(',target=',1)[0])
        fake_exports(json.loads((stage/'request.json').read_text()),stage)
        return SimpleNamespace(returncode=0,stderr='',stdout='')
    monkeypatch.setattr(subprocess,'run',run)
    config=SimpleNamespace(techdraw_docker_image='fiske-techdraw:0.20.2',techdraw_timeout_seconds=20)
    result=enhance_drawings(config,spec,parts,proofs,overall,tmp_path)
    assert result['techdraw_status']=='COMPLETED' and result['dimension_count']==12
    assert len(PdfReader(tmp_path/'techdraw_review_drawings.pdf').pages)==2
    assert original.read_bytes()==b'original vector drawing'
    assert hashlib.sha256((tmp_path/'part_F001_REVIEW.step').read_bytes()).hexdigest()==proofs[0]['step_sha256']
    command=calls[0]
    assert command[command.index('--network')+1]=='none' and '--init' in command
    assert not any('.env' in value or 'docker.sock' in value for value in command)
    assert not list(tmp_path.glob('.techdraw-*'))


def test_whole_furniture_native_document_has_only_an_assembly_page(tmp_path,monkeypatch):
    spec,parts,proofs,overall=source_package(tmp_path)
    def run(command,**kwargs):
        mount=next(arg for arg in command if arg.startswith('type=bind,source=') and arg.endswith('target=/output'))
        stage=Path(mount.split('source=',1)[1].rsplit(',target=',1)[0])
        request=json.loads((stage/'request.json').read_text())
        assert [sheet['part_id'] for sheet in request['sheets']]==['ASSEMBLY']
        assert len(request['parts'])==1  # Internal sources still receive solid verification.
        fake_exports(request,stage)
        return SimpleNamespace(returncode=0,stderr='',stdout='')
    monkeypatch.setattr(subprocess,'run',run)
    result=enhance_drawings(SimpleNamespace(techdraw_docker_image='fiske-techdraw:0.20.2',
        techdraw_timeout_seconds=20),spec,parts,proofs,overall,tmp_path,assembly_only=True)
    assert result['techdraw_status']=='COMPLETED' and result['dimension_count']==6
    assert len(PdfReader(tmp_path/'techdraw_review_drawings.pdf').pages)==1
    assert not any(name.startswith('part_') for name in result['files'])


@pytest.mark.parametrize('failure',['timeout','worker','wrong_dimension','bad_document','overflow'])
def test_native_failure_retains_existing_drawings_and_cleans_own_container(tmp_path,monkeypatch,failure):
    spec,parts,proofs,overall=source_package(tmp_path);calls=[]
    original=tmp_path/'drawings_REVIEW.pdf';original.write_bytes(b'original')
    def run(command,**kwargs):
        calls.append(command)
        if command[1]=='rm':return SimpleNamespace(returncode=0)
        if failure=='timeout':raise subprocess.TimeoutExpired(command,20)
        if failure=='worker':return SimpleNamespace(returncode=1,stderr='test crash',stdout='')
        mount=next(v for v in command if v.startswith('type=bind,source=') and v.endswith('target=/output'))
        stage=Path(mount.split('source=',1)[1].rsplit(',target=',1)[0])
        request=json.loads((stage/'request.json').read_text());report=fake_exports(request,stage)
        if failure=='wrong_dimension':
            report['dimensions'][0]['value_mm']+=3
            (stage/'techdraw_verification.json').write_text(json.dumps(report))
        elif failure=='bad_document':
            with ZipFile(stage/'furniture_REVIEW.FCStd','w') as archive:
                archive.writestr('Document.xml','<Document><Objects><Object type="App::FeaturePython"/></Objects></Document>')
        else:
            report['paper_views'][0]['envelope_mm'][0]=1000
            (stage/'techdraw_verification.json').write_text(json.dumps(report))
        return SimpleNamespace(returncode=0,stderr='',stdout='')
    monkeypatch.setattr(subprocess,'run',run)
    result=enhance_drawings(SimpleNamespace(techdraw_docker_image='fiske-techdraw:0.20.2',
        techdraw_timeout_seconds=20),spec,parts,proofs,overall,tmp_path)
    assert result['techdraw_status']=='FAILED' and result['reason']
    assert original.read_bytes()==b'original' and not (tmp_path/'furniture_REVIEW.FCStd').exists()
    assert not list(tmp_path.glob('.techdraw-*'))
    if failure=='timeout':
        assert calls[1]==['docker','rm','-f',calls[0][calls[0].index('--name')+1]]


def test_unconfigured_does_not_launch_docker_and_changed_step_is_rejected(tmp_path,monkeypatch):
    spec,parts,proofs,overall=source_package(tmp_path)
    def forbidden(*args,**kwargs):raise AssertionError('Docker must not start')
    monkeypatch.setattr(subprocess,'run',forbidden)
    result=enhance_drawings(SimpleNamespace(),spec,parts,proofs,overall,tmp_path)
    assert result['techdraw_status']=='NOT_CONFIGURED'
    (tmp_path/'part_F001_REVIEW.step').write_text('changed')
    with pytest.raises(ValueError,match='source changed'):drawing_request(spec,parts,proofs,overall,tmp_path)


@pytest.mark.skipif(os.getenv('TECHDRAW_INTEGRATION_TESTS')!='1',reason='Requires the isolated FreeCAD Docker image')
@pytest.mark.parametrize('kind',['round_hole','tilted_curved_rail'])
def test_real_freecad_native_dimensions_from_unchanged_step(tmp_path,kind):
    from app.engineering_cad import verify_solid_step,cad_bounds
    spec=SimpleNamespace(project_name=kind,revision=3)
    if kind=='round_hole':
        local=cq.Workplane('XY').circle(500).extrude(20).faces('>Z').workplane().hole(16).val()
        angle=0
    else:
        curve=cq.Workplane('XZ').spline([(0,0),(45,65),(70,115),(80,190)])
        local=cq.Workplane('YZ').circle(10).sweep(curve).val();angle=25
    shape=local.rotate((0,0,0),(0,1,0),angle).translate((120,75,30))
    path=tmp_path/'part_F001_REVIEW.step';cq.exporters.export(shape,str(path))
    actual,proof=verify_solid_step(shape,path)
    radians=math.radians(angle);x=[math.cos(radians),0,-math.sin(radians)];z=[math.sin(radians),0,math.cos(radians)]
    local_actual=cq.Plane(origin=(120,75,30),xDir=x,normal=z).toLocalCoords(actual)
    lb=cad_bounds(local_actual);bb=cad_bounds(actual)
    parts=[dict(part_id='F001',label=kind,length_axis=x,thickness_axis=z,
        length_mm=lb.xlen,width_mm=lb.ylen,thickness_mm=lb.zlen)]
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    proofs=[dict(part_id='F001',step_sha256=digest,**proof)]
    overall=dict(width=bb.xlen,depth=bb.ylen,height=bb.zlen)
    result=enhance_drawings(SimpleNamespace(techdraw_docker_image='fiske-techdraw:0.20.2',techdraw_timeout_seconds=180),
        spec,parts,proofs,overall,tmp_path)
    assert result['techdraw_status']=='COMPLETED',result
    assert result['dimension_count']==12
    assert len(PdfReader(tmp_path/'techdraw_review_drawings.pdf').pages)==2
    assert hashlib.sha256(path.read_bytes()).hexdigest()==digest
    validate_exports(drawing_request(spec,parts,proofs,overall,tmp_path),tmp_path)


@pytest.mark.skipif(os.getenv('TECHDRAW_INTEGRATION_TESTS')!='1',reason='Requires the isolated FreeCAD Docker image')
def test_real_freecad_edits_rebuild_native_documents_and_private_delivery(edit_app):
    env,source=edit_app;client,app,headers,project,image=env
    app.state.settings.techdraw_docker_image='fiske-techdraw:0.20.2'
    previous=source;old_hashes={}
    for style in ('round','tapered'):
        response=submit(env,previous,style=style,key='native-'+style)
        assert response.status_code==202,response.text
        changed=client.get(response.json()['status_url'],headers=headers['operator']).json()
        assert changed['status']=='REVIEW_REQUIRED',changed.get('error')
        directory=app.state.settings.storage/changed['job_id']
        manifest=json.loads((directory/'manifest.json').read_text())
        generation=json.loads((directory/'drawing_generation.json').read_text())
        assert generation['techdraw_status']=='COMPLETED',generation
        assert generation['dimension_count']==42
        assert manifest['cad_files']['freecad']=='furniture_REVIEW.FCStd'
        native=directory/'furniture_REVIEW.FCStd'
        response=client.get(f'/api/v1/jobs/{changed["job_id"]}/files/{native.name}',headers=headers['operator'])
        assert response.status_code==200 and response.content==native.read_bytes()
        assert client.get(f'/api/v1/jobs/{changed["job_id"]}/files/{native.name}').status_code==401
        delivery=changed['result']['delivery']
        package=client.get(delivery['download_base']+'/furniture_complete_package.zip',headers=headers['operator'])
        with ZipFile(__import__('io').BytesIO(package.content)) as archive:
            assert archive.read('cad/furniture_REVIEW.FCStd')==native.read_bytes()
            assert 'cad/techdraw_review_drawings.pdf' in archive.namelist()
        for path,digest in old_hashes.items():assert hashlib.sha256(path.read_bytes()).hexdigest()==digest
        for name in ('manifest.json','design_review_package.zip','furniture_REVIEW.FCStd'):
            path=directory/name;old_hashes[path]=hashlib.sha256(path.read_bytes()).hexdigest()
        previous=changed
