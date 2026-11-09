"""Persist exact projection curves; verify hashes before reuse across revisions."""
import hashlib
import io
import json
from pathlib import Path
import cadquery as cq
import build123d as bd
from .drawing_projection import Projection
from .cad_six_view_generator import VIEWS


def fingerprint(shape):
    stream=io.BytesIO();shape.exportBrep(stream)
    return hashlib.sha256(stream.getvalue()).hexdigest()


class ProjectionCache:
    def __init__(self,output,parent=None):
        self.output=Path(output);self.parent=Path(parent) if parent else None;self.entries={};self.reused=[]
        self.previous={}
        if self.parent and (self.parent/'reports'/'projection_cache_index.json').is_file():
            self.previous=json.loads((self.parent/'reports'/'projection_cache_index.json').read_text(encoding='utf-8'))

    def load(self,identifier,geometry_hash):
        previous=self.previous.get(identifier)
        basis={n:dict(look_from=b.look_from,right=b.right,up=b.up) for n,b in VIEWS.items()}
        version=hashlib.sha256(json.dumps(basis,sort_keys=True).encode()).hexdigest()
        if not previous or previous.get('geometry_hash')!=geometry_hash or previous.get('basis_hash')!=version:return None
        views={}
        try:
            for name,entry in previous['views'].items():
                shapes=[]
                for layer in ('visible','hidden'):
                    path=self.parent/'cache'/'native_projections'/identifier/(name+'_'+layer+'.brep')
                    if hashlib.sha256(path.read_bytes()).hexdigest()!=entry[layer]:return None
                    shape=cq.Shape.importBrep(str(path));shapes.append(bd.Compound(shape.wrapped))
                projection=object.__new__(Projection)
                projection.visible,projection.hidden=shapes
                projection.bounds=bd.Compound(shapes).bounding_box();projection._svg_cache={};projection.discarded_empty_edges=0
                if not projection.visible.edges():return None
                views[name]=projection
            if set(views)!=set(VIEWS):return None
        except (OSError,ValueError,RuntimeError):return None
        self.reused.append(identifier);return views

    def save(self,identifier,geometry_hash,views):
        directory=self.output/'cache'/'native_projections'/identifier;directory.mkdir(parents=True,exist_ok=True)
        entries={}
        for name,view in views.items():
            entries[name]={}
            for layer in ('visible','hidden'):
                path=directory/(name+'_'+layer+'.brep')
                cq.Shape(getattr(view,layer).wrapped).exportBrep(str(path))
                entries[name][layer]=hashlib.sha256(path.read_bytes()).hexdigest()
        basis={n:dict(look_from=b.look_from,right=b.right,up=b.up) for n,b in VIEWS.items()}
        self.entries[identifier]=dict(geometry_hash=geometry_hash,basis_hash=hashlib.sha256(json.dumps(basis,sort_keys=True).encode()).hexdigest(),views=entries)

    def finish(self):
        path=self.output/'reports'/'projection_cache_index.json';path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(self.entries,indent=2),encoding='utf-8')
