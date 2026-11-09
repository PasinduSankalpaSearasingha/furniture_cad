"""Isolated native FreeCAD exports; no FreeCAD/CadQuery DLL mixing."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def find_freecad_python():
    configured=os.getenv('FREECAD_PYTHON')
    if configured:return Path(configured)
    for path in (Path('C:/Program Files/FreeCAD 1.1/bin/python.exe'),Path('C:/Program Files/FreeCAD 1.0/bin/python.exe')):
        if path.is_file():return path
    return None


def export_freecad(plan, output, *, locked=()):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    source=Path(__file__).with_name('furniture_cad_proxy.py')
    shutil.copy2(source,output/source.name)
    macro="""# Loads the packaged parametric implementations before opening the document.
import sys
from pathlib import Path
import FreeCAD
import FreeCADGui
folder=Path(globals().get('__file__',MACRO_PATH)).resolve().parent
filename=folder/'assembly.FCStd'
if not filename.is_file():
    from PySide import QtGui
    selected=QtGui.QFileDialog.getOpenFileName(None,'Select the packaged assembly or part',str(folder),'FreeCAD (*.FCStd)')[0]
    if not selected:raise RuntimeError('No parametric CAD document selected')
    filename=Path(selected);folder=filename.parent
    if folder.parent.name=='parts':folder=folder.parent.parent
sys.path.insert(0,str(folder))
import furniture_cad_proxy
doc=FreeCAD.openDocument(str(filename))
for obj in doc.Objects:
    if obj.TypeId=='Sketcher::SketchObject' and obj.ViewObject:obj.ViewObject.Visibility=False
FreeCADGui.activeDocument().activeView().viewAxonometric()
FreeCADGui.activeDocument().activeView().fitAll()
""".replace('MACRO_PATH',repr(str(output/'open_project.FCMacro')))
    (output/'open_project.FCMacro').write_text(macro,encoding='utf-8')
    python=find_freecad_python()
    if python is None:
        return dict(status='unavailable',reason='Configure FREECAD_PYTHON to FreeCAD embedded Python; FCStd files were not generated')
    request=dict(design=plan.model_dump(mode='json'),output=str(output),locked=list(locked),
                 project_root=str(Path(__file__).resolve().parent.parent),python=sys.executable)
    private=output/'private';private.mkdir(exist_ok=True)
    path=private/'freecad_request.json';path.write_text(json.dumps(request),encoding='utf-8')
    completed=subprocess.run([str(python),str(Path(__file__).with_name('freecad_project_worker.py')),str(path)],
        capture_output=True,text=True,timeout=600,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    result=path.with_name('freecad_result.json')
    if completed.returncode or not result.is_file():
        raise RuntimeError('FreeCAD export failed: '+completed.stdout[-1000:]+completed.stderr[-1500:])
    report=json.loads(result.read_text(encoding='utf-8'))
    if report.get('error'):raise RuntimeError(report['error'])
    from .cad_editability_validator import validate_edited_views
    for key in ('editability','curved_editability'):
        check=report.get(key)
        if check and check.get('edited_specification'):
            specification=check.pop('edited_specification')
            check['independent_regeneration']=validate_edited_views(specification,private/key)
    return dict(status='validated',**report)
