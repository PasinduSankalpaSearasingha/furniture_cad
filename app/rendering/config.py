from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


FINISHES=('source','teak','oak','walnut','mahogany','plywood','mdf','laminated_mdf','painted_mdf',
          'clear_glass','frosted_glass','tinted_glass','brushed_metal','chrome','matte_black','aluminum')


class RenderOptions(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    quality: Literal['draft','preview','high','ultra']='preview'
    finish: Literal[FINISHES]='source'
    environment: Literal['studio','product','hdri']='studio'
    views: list[Literal['front','side','back','top','isometric','hero','detail']]=Field(
        default_factory=lambda:['front','side','top','isometric','hero'],min_length=1,max_length=7)
    projection: Literal['auto','orthographic','perspective']='auto'
    focal_length_mm: float=Field(default=55,ge=20,le=120)


def preset(options,settings):
    scale,samples={'draft':(.25,8),'preview':(.5,settings.blender_preview_samples),
        'high':(1,settings.blender_final_samples),'ultra':(1,min(512,settings.blender_final_samples*2))}[options.quality]
    return dict(width=max(128,round(settings.blender_width*scale)),height=max(128,round(settings.blender_height*scale)),
        samples=samples,threads=settings.blender_threads,device=settings.blender_device,max_triangles=settings.blender_max_triangles,
        memory_budget_mb=settings.blender_memory_budget_mb)
