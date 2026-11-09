"""Four views of a fresh, unpublished table sample, sourced from verified STEP."""
import json
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED

import cadquery as cq
import ezdxf
from pypdf import PdfReader
from reportlab.lib.pagesizes import A3,landscape

from app.dimension_sheet import Sheet
from app.drawing_projection import Projection,MM_TO_PT
from app.engineering_cad import cad_bounds
from app.pipeline import file_hash

STEM='platform_table_all_views'


def create_comparison_sheet(output):
    output=Path(output)
    if not output.name.startswith('table-platform-generated-'):
        raise ValueError('Comparison export requires a fresh isolated platform sample')
    if (output/(STEM+'.pdf')).exists():raise ValueError('This sample already has a comparison sheet')
    registry=json.loads((output/'parts_registry.json').read_text())
    proofs=json.loads((output/'geometry_verification.json').read_text())
    checks={p['part_id']:p for p in proofs['parts']}
    shapes={}
    for part in registry:
        path=output/f'part_{part["part_id"]}_REVIEW.step'
        if file_hash(path)!=checks[part['part_id']]['step_sha256']:
            raise ValueError('Verified source STEP changed before comparison export')
        shapes[part['part_id']]=cq.importers.importStep(str(path)).val()
    bounds={pid:cad_bounds(shape) for pid,shape in shapes.items()}
    top=bounds['F001'];base=[b for pid,b in bounds.items() if pid!='F001']
    x0,x1=top.xmin,top.xmax;y0,y1=top.ymin,top.ymax
    z0=min(b.zmin for b in bounds.values());z1=max(b.zmax for b in bounds.values())
    bx0,bx1=min(b.xmin for b in base),max(b.xmax for b in base)
    by0,by1=min(b.ymin for b in base),max(b.ymax for b in base)
    width,depth,height=x1-x0,y1-y0,z1-z0
    scale=MM_TO_PT/20;pw,ph=landscape(A3);sheet=Sheet(pw,ph,scale)
    fx,ty,fy,sx=70,475,218,780
    orthographic=[('TOP VIEW',(0,1),(fx,ty),(x0,y0)),
        ('FRONT VIEW',(0,2),(fx,fy),(x0,z0)),('SIDE VIEW',(1,2),(sx,fy),(y0,z0))]
    for name,axes,(ox,oy),(minimum_x,minimum_y) in orthographic:
        projection=Projection(list(shapes.values()),axes)
        sheet.projections.append((projection,ox-minimum_x*scale,oy-minimum_y*scale))
        sheet.text(ox,701 if name=='TOP VIEW' else 356,name,10,bold=True)
    iso=Projection(list(shapes.values()),look_from=(1,-1,1),look_up=(0,0,1))
    ib=iso.bounds;center_x,center_y=890,600
    iw,ih=ib.size.X*scale,ib.size.Y*scale
    if iw>500 or ih>340:raise ValueError('Isometric view exceeds its paper area')
    sheet.projections.append((iso,center_x-(ib.min.X+ib.max.X)*scale/2,
        center_y-(ib.min.Y+ib.max.Y)*scale/2))
    sheet.text(650,426,'ISOMETRIC VIEW',10,bold=True)
    # All displayed measurements are calculated from these reopened solids.
    tx=lambda value:fx+(value-x0)*scale
    sy=lambda value:sx+(value-y0)*scale
    sheet.dimension((fx,ty+depth*scale),(fx+width*scale,ty+depth*scale),ty+depth*scale+27,0,'TOP','overall width')
    sheet.dimension((fx+width*scale,ty),(fx+width*scale,ty+depth*scale),fx+width*scale+27,1,'TOP','overall depth')
    for a,b,purpose in ((x0,bx0,'left overhang'),(bx0,bx1,'base length'),(bx1,x1,'right overhang')):
        sheet.dimension((tx(a),ty),(tx(b),ty),ty-27,0,'TOP',purpose)
        sheet.dimension((tx(a),fy),(tx(b),fy),fy-25,0,'FRONT',purpose)
    sheet.dimension((fx,fy),(fx+width*scale,fy),fy-48,0,'FRONT','overall width')
    sheet.dimension((fx+width*scale,fy),(fx+width*scale,fy+height*scale),fx+width*scale+27,1,'FRONT','overall height')
    sheet.dimension((sx,fy+height*scale),(sx+depth*scale,fy+height*scale),fy+height*scale+23,0,'SIDE','overall depth')
    sheet.dimension((sx+depth*scale,fy),(sx+depth*scale,fy+height*scale),sx+depth*scale+28,1,'SIDE','overall height')
    sheet.dimension((sx+depth*scale,fy+(top.zmin-z0)*scale),(sx+depth*scale,fy+height*scale),
        sx+depth*scale+62,1,'SIDE','top thickness')
    for a,b,purpose in ((y0,by0,'front overhang'),(by0,by1,'base depth'),(by1,y1,'rear overhang')):
        sheet.dimension((sy(a),fy),(sy(b),fy),fy-25,0,'SIDE',purpose)
    sheet.box(20,20,pw-40,ph-40,'#25313b',.7)
    sheet.text(36,ph-48,'FISKE / PLATFORM-GENERATED TABLE SAMPLE',17,bold=True)
    sheet.text(pw-36,ph-46,'01 / ALL VIEWS',10,bold=True,align='right')
    sheet.text(36,ph-66,'Fresh CAD solids and projections from the supplied photo interpretation.',8)
    sheet.line([(20,145),(pw-20,145)],'#697782',.4)
    sheet.text(36,125,'MANUAL RECONSTRUCTION / REVIEW',10,bold=True)
    sheet.text(36,109,'Not an automatic photo-analysis test. No OpenAI requests were made.',8)
    sheet.text(36,94,'Reference target: 2800 x 1100 x 750 mm; top 45 mm; base 2100 x 766 mm.',8)
    sheet.text(36,79,'Estimated: sculpted base profile, component split, 55 mm section and R100 corners.',8)
    sheet.text(36,64,'Joinery, materials, physical dimensions and structural performance require review.',8)
    sheet.text(36,42,'Source: reopened part STEP files. Original PDF geometry was not used.',8)
    sheet.text(790,125,'4 CLOSED SOLIDS / REVISION 1',10,bold=True)
    sheet.text(790,107,'A3 / SCALE 1:20 / DIMENSIONS IN mm',9,bold=True)
    sheet.text(790,89,'TOP + FRONT + SIDE + ISOMETRIC',8)
    sheet.text(790,71,'Native DXF curves; visible and hidden edges.',8)
    sheet.text(790,42,'NOT APPROVED FOR MANUFACTURE',9,bold=True)
    metadata=dict(schema_version=1,title='Platform generated sculpted table',revision=1,
        geometry_source='REOPENED_STEP',generation_method='MANUAL_PHOTO_RECONSTRUCTION_THROUGH_PLATFORM_CAD',
        automatic_photo_analysis=False,reference_pdf_geometry_used=False,paid_ai_calls=0,
        views=['TOP','FRONT','SIDE','ISOMETRIC'],units='mm',scale_denominator=20,
        overall_dimensions_mm=dict(width=width,depth=depth,height=height),
        actual_base_dimensions_mm=dict(length=bx1-bx0,depth=by1-by0),top_thickness_mm=top.zlen,
        part_count=len(registry),source_step_hashes={pid:checks[pid]['step_sha256'] for pid in shapes})
    sheet.save(output,metadata,stem=STEM)
    pdf=PdfReader(output/(STEM+'.pdf'))
    if len(pdf.pages)!=1:raise ValueError('Comparison drawing is not one page')
    text=pdf.pages[0].extract_text()
    if not all(v in text for v in ('TOP VIEW','FRONT VIEW','SIDE VIEW','ISOMETRIC VIEW','2800','1100','750')):
        raise ValueError('Comparison sheet is missing its required views or dimensions')
    dxf=ezdxf.readfile(output/(STEM+'.dxf'))
    if dxf.units!=4 or dxf.audit().errors:raise ValueError('Comparison DXF failed its unit/entity audit')
    dimensions=list(dxf.modelspace().query('DIMENSION'))
    if len(dimensions)!=len(sheet.dimensions):raise ValueError('Comparison dimension inventory differs')
    for entity,dimension in zip(dimensions,sheet.dimensions):
        if abs(entity.get_measurement()-dimension['value_mm'])>1e-4:
            raise ValueError('Native comparison dimension does not match its source value')
    for pid in shapes:
        if file_hash(output/f'part_{pid}_REVIEW.step')!=checks[pid]['step_sha256']:
            raise ValueError('Comparison export changed its source geometry')
    # Rendering is optional outside the isolated sample runner.
    try:import pypdfium2 as pdfium
    except ImportError:pass
    else:
        doc=pdfium.PdfDocument(str(output/(STEM+'.pdf')));page=doc[0];bitmap=page.render(scale=2.5)
        image=bitmap.to_pil();image.save(output/(STEM+'.png'));image.close();bitmap.close();page.close();doc.close()
    manifest_path=output/'manifest.json';manifest=json.loads(manifest_path.read_text())
    drawing_path=output/'drawing_manifest.json';drawings=json.loads(drawing_path.read_text())
    drawings['comparison_sheet']=dict(files=metadata['files'],views=metadata['views'],pages=1,
        generation_method=metadata['generation_method'],automatic_photo_analysis=False)
    drawing_path.write_text(json.dumps(drawings,indent=2),encoding='utf-8')
    manifest['files']={p.name:file_hash(p) for p in sorted(output.iterdir())
        if p.is_file() and p.name not in ('manifest.json','design_review_package.zip')}
    manifest_path.write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    with ZipFile(output/'design_review_package.zip','w',ZIP_DEFLATED) as archive:
        for path in sorted(output.iterdir()):
            if path.is_file() and path.suffix!='.zip':archive.write(path,path.name)
    return metadata
