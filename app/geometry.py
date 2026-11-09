"""Exact geometry bounds, independent of cached visualization triangulations."""
import math
import cadquery as cq
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box


def exact_bounds(shape):
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape.wrapped,box,False,False)
    return cq.BoundBox(box)


def trimmed_bounds(shape):
    """Measure remaining B-Rep material after spline faces have been trimmed.

    AddOptimal can include extrema of a spline's underlying untrimmed surface.
    Distances to six planes outside that conservative box instead measure the
    actual trimmed shape. No preview mesh or declared dimension is substituted.
    Use this more expensive path for explicitly envelope-clipped lofts.
    """
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    coarse=exact_bounds(shape)
    margin=max(1.,max(coarse.xlen,coarse.ylen,coarse.zlen)*1e-4)
    extrema={}
    for index,axis in enumerate('xyz'):
        low,high=getattr(coarse,axis+'min'),getattr(coarse,axis+'max')
        if not all(math.isfinite(v) for v in (low,high)):
            raise ValueError('Trimmed envelope requires finite conservative bounds')
        for side,sign in (('min',1),('max',-1)):
            coordinate=(low-margin if side=='min' else high+margin)
            origin=[0.,0.,0.];origin[index]=coordinate
            normal=[0.,0.,0.];normal[index]=1.
            plane=cq.Face.makePlane(basePnt=origin,dir=normal)
            distance=BRepExtrema_DistShapeShape(shape.wrapped,plane.wrapped,1e-7)
            if not distance.IsDone():raise ValueError('Trimmed B-Rep envelope measurement did not complete')
            value=coordinate+sign*distance.Value()
            if not math.isfinite(value) or not low-1e-5<=value<=high+1e-5:
                raise ValueError('Trimmed B-Rep envelope is inconsistent with conservative bounds')
            extrema[axis+side]=value
        if extrema[axis+'min']>extrema[axis+'max']+1e-7:
            raise ValueError('Trimmed B-Rep envelope has inverted bounds')
    box=Bnd_Box()
    box.Update(*(extrema[axis+'min'] for axis in 'xyz'),*(extrema[axis+'max'] for axis in 'xyz'))
    return cq.BoundBox(box)
