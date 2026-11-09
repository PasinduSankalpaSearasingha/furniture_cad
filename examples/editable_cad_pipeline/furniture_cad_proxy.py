"""Packaged FreeCAD FeaturePython implementation (stdlib + FreeCAD only).

Complex curves and contact trims regenerate through the authoritative project
.venv builder in an isolated process. No triangle or imported STEP feature is
presented as parametric. Install this module on FreeCAD's Python path or use the
packaged open_project.FCMacro, and keep the source project .venv available.
"""
import hashlib
import json
from pathlib import Path
import subprocess


class ParametersProxy:
    def __init__(self,obj):obj.Proxy=self
    def __getstate__(self):return None
    def __setstate__(self,state):pass
    def onChanged(self,obj,name):
        if name in getattr(obj,'ParameterKeys',[]):
            for dependent in obj.InList:
                if hasattr(dependent,'touch'):dependent.touch()
    def execute(self,obj):
        design=json.loads(obj.DesignJSON)
        values={key:float(getattr(obj,key)) for key in obj.ParameterKeys}
        source_parts={p['part_id']:p for p in design['parts']}
        constructions={p.Name:p.ConstructionJSON for p in obj.Document.Objects if hasattr(p,'ConstructionJSON')
                       and json.loads(p.ConstructionJSON)!=source_parts.get(p.Name)}
        request=dict(design=design,values=values,constructions=constructions)
        digest=hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest()
        directory=Path(obj.Document.FileName).parent if obj.Document.FileName else Path(obj.CacheRoot)
        if directory.parent.name=='parts':directory=directory.parent.parent
        cache=directory/'regeneration_cache'/digest
        expected=[p['part_id'] for p in design['parts']]
        if not all((cache/(pid+'.brep')).exists() for pid in expected):
            cache.mkdir(parents=True,exist_ok=True)
            source=cache/'request.json';source.write_text(json.dumps(request),encoding='utf-8')
            result=subprocess.run([obj.PythonExecutable,'-m','app.cad_regeneration_bridge',str(source),str(cache)],
                cwd=obj.ProjectRoot,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=300,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode:
                obj.LastError=result.stdout[-3000:]
                raise RuntimeError('CAD regeneration failed: '+obj.LastError)
        obj.CacheDirectory=str(cache)
        obj.LastError=''
        for feature in obj.Document.Objects:
            if hasattr(feature,'PartID'):feature.touch()


class PartProxy:
    def __init__(self,obj):obj.Proxy=self
    def __getstate__(self):return None
    def __setstate__(self,state):pass
    def onChanged(self,obj,name):
        if name=='ConstructionJSON' and getattr(obj,'Parameters',None):obj.Parameters.touch()
    def execute(self,obj):
        import Part
        if obj.Parameters.LastError:raise RuntimeError(obj.Parameters.LastError)
        source=Path(obj.Parameters.CacheDirectory)/(obj.PartID+('.local.brep' if obj.UseLocalFrame else '.brep'))
        if not source.is_file():raise RuntimeError('Regeneration source is missing')
        shape=Part.Shape();shape.read(str(source))
        if not shape.isValid() or len(shape.Solids)!=1:raise ValueError('Regenerated native component is invalid')
        obj.Shape=shape
