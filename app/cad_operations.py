"""Controlled CAD feature registry. All locations are in the component's local mm frame."""
import math
import cadquery as cq
from .photo_schema import number


OPERATIONS=('hole','groove','pocket','fillet','chamfer','dowel','dado','rabbet','mortise','tenon','confirmat')


def apply_features(shape,features,values):
    n=lambda x:number(x,values)
    for feature in features:
        operation=feature.operation
        origin=tuple(n(x) for x in feature.position);size=tuple(n(x) for x in feature.size)
        before=shape.val().Volume()
        if operation in ('fillet','chamfer'):
            selector={'vertical_corners':'|Z','top_edges':'>Z','all_edges':None}[feature.edge_selector]
            edges=shape.edges(selector) if selector else shape.edges()
            shape=edges.fillet(n(feature.radius)) if operation=='fillet' else edges.chamfer(n(feature.radius))
        elif operation in ('hole','dowel','confirmat'):
            direction={'X':(1,0,0),'Y':(0,1,0),'Z':(0,0,1)}[feature.axis]
            cutter=cq.Solid.makeCylinder(n(feature.radius),n(feature.depth),cq.Vector(*origin),cq.Vector(*direction))
            shape=shape.cut(cutter)
        else:
            block=cq.Workplane('XY').box(*size).translate(origin)
            shape=shape.union(block) if operation=='tenon' else shape.cut(block)
        solid=shape.val()
        if not solid.isValid() or len(solid.Solids())!=1 or solid.Volume()<=0:
            raise ValueError(f'{operation} produced an invalid or disconnected component')
        if math.isclose(before,solid.Volume(),rel_tol=1e-10,abs_tol=1e-7):
            raise ValueError(f'{operation} did not affect the component; check its local position and dimensions')
    return shape
