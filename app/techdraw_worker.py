"""Standalone FreeCAD process: validated STEP snapshots to native review drawings.

Runs in the isolated TechDraw image, never in the web server's Python runtime.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate(request_file, source_directory, output):
    from PySide2 import QtWidgets
    application=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    import FreeCAD as App
    sys.path.extend(str(path) for path in (Path(App.getResourceDir())/'Mod').iterdir() if path.is_dir())
    import FreeCADGui as Gui
    import Part
    import TechDraw
    Gui.showMainWindow();Gui.getMainWindow().hide()
    import TechDrawGui
    request=json.loads(request_file.read_text(encoding='utf-8'))
    if request.get('schema_version')!=1 or not 1<=len(request['parts'])<=60:
        raise ValueError('Unsupported drawing request')
    output.mkdir(parents=True,exist_ok=True)
    Gui.showMainWindow();Gui.getMainWindow().hide()
    document=App.newDocument('FurnitureReview')
    document.Label=request['project_name']+' / REVIEW / revision '+str(request['revision'])
    sources=[];proofs=[];objects={}
    for record in request['parts']:
        pid=record['part_id'];filename=record['step']
        if not re.fullmatch(r'F[0-9]{3}',pid) or filename!=f'part_{pid}_REVIEW.step':
            raise ValueError('Unsupported component source')
        path=(source_directory/filename).resolve()
        if not path.is_relative_to(source_directory.resolve()) or digest(path)!=record['step_sha256']:
            raise ValueError('STEP source changed')
        shape=Part.read(str(path))
        if not shape.isValid() or len(shape.Solids)!=1 or shape.Volume<=0:
            raise ValueError('STEP does not contain one valid solid')
        feature=document.addObject('Part::Feature',pid)
        feature.Label=pid+' '+record['label'];feature.Shape=shape
        feature.addProperty('App::PropertyString','SourceSHA256','Review').SourceSHA256=record['step_sha256']
        objects[pid]=feature;sources.append(feature)
        proofs.append(dict(part_id=pid,step_sha256=record['step_sha256'],solid_count=1,valid=True,
            volume_mm3=shape.Volume))

    # A small self-contained template is embedded in the saved FreeCAD document.
    template_path=output/'techdraw_template.svg'
    template_path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="420mm" height="297mm" '
        'viewBox="0 0 420 297"><rect x="8" y="8" width="404" height="281" '
        'fill="none" stroke="black" stroke-width="0.25"/></svg>',encoding='utf-8')
    dimensions=[];pages={};paper_views=[]
    for item in request['sheets']:
        pid=item['part_id'];prefix='assembly' if pid=='ASSEMBLY' else pid
        if pid!='ASSEMBLY' and pid not in objects:raise ValueError('Drawing component not found')
        page=document.addObject('TechDraw::DrawPage','Page_'+prefix)
        template=document.addObject('TechDraw::DrawSVGTemplate','Template_'+prefix)
        template.Template=str(template_path);page.Template=template
        views=item['views']
        scale=min(105/max(v['expected_mm'][0] for v in views),
            100/max(v['expected_mm'][1] for v in views),1)
        annotation=document.addObject('TechDraw::DrawViewAnnotation','Note_'+prefix)
        annotation.Text=[item['title'],'REV '+str(request['revision'])+' / REVIEW / mm / SCALE '+f'{scale:.4f}',
            'MODEL ENVELOPES. Physical measurements, joints and tolerances unverified.']
        annotation.TextSize=3.5;page.addView(annotation);annotation.X=210;annotation.Y=281
        for index,record in enumerate(views):
            view=document.addObject('TechDraw::DrawViewPart',prefix+'_'+record['name'])
            page.addView(view);view.Source=sources if pid=='ASSEMBLY' else [objects[pid]]
            view.Direction=App.Vector(*record['direction']);view.XDirection=App.Vector(*record['x_direction'])
            view.ScaleType='Custom';view.Scale=scale;view.X=75+index*132;view.Y=155
            view.HardHidden=True
            if 'SmoothHidden' in view.PropertiesList:view.SmoothHidden=True
            document.recompute()
            if abs(float(view.Scale)-scale)>1e-9:raise ValueError('Drawing scale was overridden')
            paper_views.append(dict(part_id=pid,view=record['name'],scale=float(view.Scale),
                center_mm=[view.X.Value,view.Y.Value],
                envelope_mm=[value*float(view.Scale) for value in record['expected_mm']]))
            for axis in (0,1):
                previous={obj.Name for obj in document.Objects}
                dimension=TechDraw.makeExtentDim(view,[],axis)
                if dimension is None:
                    dimension=next(obj for obj in document.Objects if obj.Name not in previous and obj.isDerivedFrom('TechDraw::DrawViewDimension'))
                dimension.FormatSpec='%.2f mm'
                # TechDraw dimensions use paper offsets relative to their view.
                if axis==0:dimension.X=0;dimension.Y=-record['expected_mm'][1]*scale/2-14
                else:dimension.X=record['expected_mm'][0]*scale/2+12;dimension.Y=0
                document.recompute()
                value=float(dimension.getRawValue())
                expected=record['expected_mm'][axis]
                if not math.isfinite(value) or abs(value-expected)>.1:
                    raise ValueError(f'{pid} {record["name"]} dimension mismatch: {value:g} vs {expected:g} mm')
                dimensions.append(dict(part_id=pid,view=record['name'],axis=axis,value_mm=value,object_name=dimension.Name))
            caption=document.addObject('TechDraw::DrawViewAnnotation','Caption_'+view.Name)
            caption.Text=[record['name']];caption.TextSize=3.5
            page.addView(caption);caption.X=view.X;caption.Y=65
        document.recompute();page.ViewObject.show();application.processEvents()
        stem='techdraw_assembly_REVIEW' if pid=='ASSEMBLY' else f'part_{pid}_techdraw_REVIEW'
        TechDrawGui.exportPageAsPdf(page,str(output/(stem+'.pdf')))
        TechDrawGui.exportPageAsSvg(page,str(output/(stem+'.svg')))
        pages[pid]=dict(pdf=stem+'.pdf',svg=stem+'.svg')
    document.recompute()
    document.saveAs(str(output/'furniture_REVIEW.FCStd'))
    App.closeDocument(document.Name)
    document=App.openDocument(str(output/'furniture_REVIEW.FCStd'))
    document.recompute()
    for record in dimensions:
        restored=document.getObject(record['object_name'])
        if restored is None or 'Invalid' in restored.State or abs(float(restored.getRawValue())-record['value_mm'])>1e-6:
            raise ValueError('Saved native dimension could not be restored')
    for record in proofs:
        restored=document.getObject(record['part_id'])
        if restored is None or not restored.Shape.isValid() or restored.SourceSHA256!=record['step_sha256']:
            raise ValueError('Saved native STEP source could not be restored')
    report=dict(schema_version=1,engine='FreeCAD TechDraw',version='.'.join(App.Version()[:3]),
        units='mm',source_parts=proofs,dimensions=dimensions,pages=pages,paper_views=paper_views,native_file='furniture_REVIEW.FCStd',
        requested_dimension_agreement_mm=.1,native_document_reopened=True,approved_for_manufacturing=False)
    (output/'techdraw_verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    App.closeDocument(document.Name);application.quit()
    print(json.dumps(dict(status='COMPLETED',pages=len(pages),dimensions=len(dimensions))),flush=True)


if __name__=='__main__':
    # Avoid unsafe destruction order in the embedded Qt/FreeCAD Python modules.
    # All documents and deliverables are closed before process exit.
    try:
        generate(Path(sys.argv[1]),Path(sys.argv[2]),Path(sys.argv[3]))
    except Exception:
        import traceback
        traceback.print_exc();sys.stderr.flush();os._exit(1)
    os._exit(0)
