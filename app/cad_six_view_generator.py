"""Exact B-Rep hidden-line removal with explicit, right-handed view bases."""
from dataclasses import dataclass
import numpy as np
from .drawing_projection import Projection


@dataclass(frozen=True)
class ViewBasis:
    look_from: tuple
    right: tuple
    up: tuple


VIEWS = {
    'front':ViewBasis((0,-1,0),(1,0,0),(0,0,1)),
    'back':ViewBasis((0,1,0),(-1,0,0),(0,0,1)),
    'left':ViewBasis((-1,0,0),(0,-1,0),(0,0,1)),
    'right':ViewBasis((1,0,0),(0,1,0),(0,0,1)),
    'top':ViewBasis((0,0,1),(1,0,0),(0,1,0)),
    'bottom':ViewBasis((0,0,-1),(1,0,0),(0,-1,0)),
}


def six_views(shapes):
    result={}
    for name,basis in VIEWS.items():
        if not np.array_equal(np.cross(basis.right,basis.up),basis.look_from):
            raise ValueError('Mirrored view basis')
        result[name]=Projection(shapes,look_from=basis.look_from,look_up=basis.up)
    return result


def layout(convention):
    # Coordinates in a four-column, three-row sheet grid (origin lower left).
    if convention not in ('first_angle','third_angle'):raise ValueError('Unknown orthographic convention')
    if convention=='third_angle':
        return dict(front=(1,1),back=(3,1),left=(0,1),right=(2,1),top=(1,2),bottom=(1,0))
    return dict(front=(1,1),back=(3,1),left=(2,1),right=(0,1),top=(1,0),bottom=(1,2))
