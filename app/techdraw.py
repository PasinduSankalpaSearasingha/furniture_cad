"""Optional isolated TechDraw drawings from this revision's verified STEP files."""
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory
from uuid import uuid4
from zipfile import ZipFile,BadZipFile
import xml.etree.ElementTree as ET

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError


def drawing_request(spec, parts, proofs, overall, output, *, assembly_only=False):
    sources=[];sheets=[]
    for part,proof in zip(parts,proofs,strict=True):
        pid=part['part_id']
        if pid!=proof['part_id'] or not re.fullmatch(r'F\d{3}',pid):
            raise ValueError('Drawing source inventory differs from the verified parts')
        filename=f'part_{pid}_REVIEW.step'
        digest=hashlib.sha256((output/filename).read_bytes()).hexdigest()
        if digest!=proof['step_sha256']:raise ValueError('Drawing STEP source changed')
        sources.append(dict(part_id=pid,label=part['label'],step=filename,step_sha256=digest,
            volume_mm3=proof.get('step_kernel_default_volume_mm3',proof['step_volume_mm3'])))
        x=part['length_axis'];z=part['thickness_axis']
        y=[z[1]*x[2]-z[2]*x[1],z[2]*x[0]-z[0]*x[2],z[0]*x[1]-z[1]*x[0]]
        length,width,thickness=[part[key] for key in ('length_mm','width_mm','thickness_mm')]
        sheets.append(dict(part_id=pid,title=pid+' '+part['label'],views=[
            dict(name='LOCAL XY',direction=z,x_direction=x,expected_mm=[length,width]),
            dict(name='LOCAL XZ',direction=[-v for v in y],x_direction=x,expected_mm=[length,thickness]),
            dict(name='LOCAL YZ',direction=x,x_direction=y,expected_mm=[width,thickness])]))
    w,d,h=[overall[key] for key in ('width','depth','height')]
    sheets.insert(0,dict(part_id='ASSEMBLY',title=spec.project_name,views=[
        dict(name='FRONT',direction=[0,-1,0],x_direction=[1,0,0],expected_mm=[w,h]),
        dict(name='TOP',direction=[0,0,1],x_direction=[1,0,0],expected_mm=[w,d]),
        dict(name='SIDE',direction=[1,0,0],x_direction=[0,1,0],expected_mm=[d,h])]))
    return dict(schema_version=1,project_name=spec.project_name,revision=spec.revision,parts=sources,
        sheets=sheets[:1] if assembly_only else sheets)


def validate_exports(request, directory):
    report=json.loads((directory/'techdraw_verification.json').read_text(encoding='utf-8'))
    if report.get('units')!='mm' or report.get('engine')!='FreeCAD TechDraw' or report.get('native_document_reopened') is not True:
        raise ValueError('Unexpected drawing engine or units')
    expected={part['part_id']:part for part in request['parts']}
    actual={part['part_id']:part for part in report['source_parts']}
    if actual.keys()!=expected.keys() or len(report['source_parts'])!=len(expected):
        raise ValueError('TechDraw source parts differ')
    for pid,part in actual.items():
        original=expected[pid];volume=part['volume_mm3']
        if (part['step_sha256']!=original['step_sha256'] or part['solid_count']!=1 or part['valid'] is not True
            or not math.isfinite(volume) or abs(volume-original['volume_mm3'])>max(.01,original['volume_mm3']*1e-6)):
            raise ValueError('TechDraw STEP verification differs for '+pid)
    dimensions={(s['part_id'],v['name'],axis):value for s in request['sheets']
        for v in s['views'] for axis,value in enumerate(v['expected_mm'])}
    measured={(d['part_id'],d['view'],d['axis']):d['value_mm'] for d in report['dimensions']}
    if measured.keys()!=dimensions.keys() or len(report['dimensions'])!=len(dimensions):
        raise ValueError('TechDraw dimension inventory differs')
    for key,value in measured.items():
        if not math.isfinite(value) or abs(value-dimensions[key])>.1:
            raise ValueError('TechDraw dimension does not match its STEP envelope')
    paper={(v['part_id'],v['view']):v for v in report['paper_views']}
    expected_views={(s['part_id'],v['name']):v for s in request['sheets'] for v in s['views']}
    if paper.keys()!=expected_views.keys() or len(paper)!=len(report['paper_views']):
        raise ValueError('TechDraw projected view inventory differs')
    for key,view in paper.items():
        scale=view['scale'];x,y=view['center_mm'];width,height=view['envelope_mm']
        if not all(math.isfinite(v) for v in (scale,x,y,width,height)) or not 0<scale<=1:
            raise ValueError('Invalid TechDraw paper scale')
        if (width>105.1 or height>100.1 or x-width/2<8 or x+width/2+12>412
            or y-height/2-14<8 or y+height/2>289):
            raise ValueError('TechDraw view exceeds the paper layout')
        if any(abs(actual-value*scale)>.001 for actual,value in zip((width,height),expected_views[key]['expected_mm'])):
            raise ValueError('TechDraw paper scale differs from the source geometry')
    files=['furniture_REVIEW.FCStd','techdraw_verification.json']
    if set(report['pages'])!={s['part_id'] for s in request['sheets']}:
        raise ValueError('TechDraw page inventory differs')
    for sheet in request['sheets']:
        pid=sheet['part_id']
        stem='techdraw_assembly_REVIEW' if pid=='ASSEMBLY' else f'part_{pid}_techdraw_REVIEW'
        for kind in ('pdf','svg'):
            filename=stem+'.'+kind
            if report['pages'][pid][kind]!=filename:raise ValueError('Unexpected drawing filename')
            path=directory/filename
            if not 100<path.stat().st_size<25*1024*1024:raise ValueError('Invalid drawing file size')
            if kind=='pdf':
                if len(PdfReader(path).pages)!=1:raise ValueError('Drawing must contain one page')
            elif ET.parse(path).getroot().tag!='{http://www.w3.org/2000/svg}svg':
                raise ValueError('Invalid drawing SVG')
            files.append(filename)
    with ZipFile(directory/'furniture_REVIEW.FCStd') as archive:
        if sum(i.file_size for i in archive.infolist())>100*1024*1024 or archive.testzip():
            raise ValueError('Invalid native drawing document')
        root=ET.fromstring(archive.read('Document.xml'))
        allowed={'Part::Feature','TechDraw::DrawPage','TechDraw::DrawSVGTemplate',
            'TechDraw::DrawViewPart','TechDraw::DrawViewDimExtent','TechDraw::DrawViewAnnotation'}
        if any(obj.attrib.get('type') not in allowed for obj in root.findall('./Objects/Object')):
            raise ValueError('Unexpected executable object in native document')
    return report,files


def enhance_drawings(config, spec, parts, proofs, overall, output, *, assembly_only=False):
    image=getattr(config,'techdraw_docker_image','')
    status=dict(engine='build123d',native_curves=True,techdraw_status='NOT_CONFIGURED')
    if image:
        status['techdraw_status']='FAILED'
        try:
            if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._:/@-]{0,200}',image):
                raise ValueError('Invalid TechDraw image name')
            request=drawing_request(spec,parts,proofs,overall,output,assembly_only=assembly_only)
            # Only this generated revision is mounted. No credentials, uploads,
            # database or project source are exposed to the CAD process.
            with TemporaryDirectory(prefix='.techdraw-',dir=output) as temporary:
                stage=Path(temporary).resolve();output=output.resolve()
                (stage/'request.json').write_text(json.dumps(request),encoding='utf-8')
                name='fiske-techdraw-'+uuid4().hex
                command=['docker','run','--rm','--init','--name',name,'--network','none',
                    '--cap-drop','ALL','--security-opt','no-new-privileges','--cpus','2','--memory','2g',
                    '--pids-limit','256','--mount',f'type=bind,source={output},target=/input,readonly',
                    '--mount',f'type=bind,source={stage},target=/output',image,
                    '/output/request.json','/input','/output']
                try:
                    result=subprocess.run(command,capture_output=True,text=True,
                        timeout=config.techdraw_timeout_seconds,check=False)
                except subprocess.TimeoutExpired:
                    subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=15,check=False)
                    raise ValueError('TechDraw exceeded its time limit') from None
                if result.returncode:
                    raise ValueError('TechDraw worker failed: '+(result.stderr or result.stdout)[-1600:])
                report,files=validate_exports(request,stage)
                writer=PdfWriter()
                for item in request['sheets']:writer.append(stage/report['pages'][item['part_id']]['pdf'])
                writer.write(stage/'techdraw_review_drawings.pdf');writer.close()
                files.append('techdraw_review_drawings.pdf')
                # Publish only after the complete native drawing set passes.
                for filename in files:shutil.copyfile(stage/filename,output/filename)
                status.update(techdraw_status='COMPLETED',techdraw_version=report['version'],
                    native_file='furniture_REVIEW.FCStd',files=files,
                    dimension_count=len(report['dimensions']),dimension_agreement_mm=.1)
        except (OSError,ValueError,KeyError,TypeError,ET.ParseError,BadZipFile,PdfReadError,subprocess.SubprocessError) as exc:
            status['reason']=str(exc)[:2000]
    (output/'drawing_generation.json').write_text(json.dumps(status,indent=2),encoding='utf-8')
    return status
