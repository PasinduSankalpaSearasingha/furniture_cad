"""Private, bounded inputs for local reference fitting; no model-generated code."""
from typing import Literal, Annotated
from uuid import UUID
from pydantic import Field, model_validator
from .photo_schema import PhotoModel

UnitCoordinate = Annotated[float, Field(ge=0,le=1)]


class FitLandmark(PhotoModel):
    part_id: str = Field(pattern=r'^F[0-9]{3}$')
    anchor: Literal['center','top','bottom','path_start','path_end']='center'
    image_point: tuple[UnitCoordinate,UnitCoordinate]


class FitView(PhotoModel):
    image_id: UUID
    mask_png: str | None = Field(default=None,max_length=450000)
    mask_reviewed: bool = False
    landmarks: list[FitLandmark] = Field(default_factory=list,max_length=30)
    azimuth_deg: float | None = Field(default=None,ge=-180,le=180)
    elevation_deg: float | None = Field(default=None,ge=-10,le=75)


class ReferenceFitOptions(PhotoModel):
    views: list[FitView] = Field(default_factory=list,max_length=4)
    component_ids: list[str] = Field(default_factory=list,max_length=30)
    resolution: int = Field(default=160,ge=96,le=256)
    camera_evaluations: int = Field(default=180,ge=24,le=600)
    geometry_evaluations: int = Field(default=80,ge=0,le=200)
    cad_candidates: int = Field(default=2,ge=0,le=3)
    max_displacement_mm: float = Field(default=12,ge=.1,le=30)
    max_seconds: float = Field(default=180,ge=10,le=600)

    @model_validator(mode='after')
    def unique_inputs(self):
        if len({v.image_id for v in self.views})!=len(self.views): raise ValueError('Duplicate fitting reference')
        if len(set(self.component_ids))!=len(self.component_ids): raise ValueError('Duplicate fitting component')
        return self


class MaskStroke(PhotoModel):
    kind: Literal['foreground','background']
    points: list[tuple[UnitCoordinate,UnitCoordinate]] = Field(min_length=1,max_length=300)
    radius: float = Field(default=.012,ge=.002,le=.1)


class ReferenceMaskRequest(PhotoModel):
    image_id: UUID
    strokes: list[MaskStroke] = Field(default_factory=list,max_length=32)

    @model_validator(mode='after')
    def pixel_budget(self):
        if sum(len(s.points) for s in self.strokes)>3000: raise ValueError('Mask stroke budget exceeded')
        return self
