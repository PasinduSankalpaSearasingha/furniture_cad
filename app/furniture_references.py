"""Ordered photographs of the same furniture; the first view is primary."""
from typing import Literal
from uuid import UUID
from pydantic import Field
from app.models import StrictModel

ANGLES=Literal['unspecified','perspective','front','rear','left','right','top','bottom','detail']


class FurnitureReferenceView(StrictModel):
    image_id: UUID
    angle: ANGLES = 'unspecified'


def validate_references(ids,views):
    keys=[str(value) for value in ids]
    if len(keys)!=len(set(keys)):raise ValueError('Select each furniture photo only once')
    if views and [str(view.image_id) for view in views]!=keys:
        raise ValueError('Photo angle labels must match the selected images in order; the first photo is primary')


MULTIVIEW='''All reference photographs show the SAME furniture from different angles, not additional copies.
Use every reference together to resolve visible curves, supports, counts, rear/side details and occlusion.
Do not duplicate legs or furniture because they appear in several photographs. Reconcile conflicting
perspectives; do not average silhouettes from unrelated cameras or claim that photos provide physical measurements.
The first reference is primary and defines reference_view; additional views supply complementary evidence.
reference_views labels correspond to the input images in order. Treat unspecified angles as unknown.
'''
