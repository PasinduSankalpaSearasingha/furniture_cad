"""Bounded contracts for the adapted upstream CadQuery agent workflow."""
from pydantic import Field,model_validator
from typing import Literal
from app.photo_schema import PhotoModel,ResolvedValue,PhotoDesign


class EngineeringPhotoView(PhotoModel):
    azimuth_deg: float = Field(ge=-180,le=180)
    elevation_deg: float = Field(ge=-70,le=70)


class VisibleShapeFeature(PhotoModel):
    feature_id: str = Field(pattern=r'^V[0-9]{3}$')
    label: str = Field(min_length=1,max_length=120)
    visible_shape: str = Field(min_length=1,max_length=700)
    relative_layout: str = Field(min_length=1,max_length=500)


class FurnitureAppearance(PhotoModel):
    summary: str = Field(min_length=1,max_length=1200)
    critical_features: list[VisibleShapeFeature] = Field(min_length=1,max_length=12)
    continuity: list[str] = Field(max_length=12)
    symmetry: str = Field(min_length=1,max_length=700)
    avoid_substitutions: list[str] = Field(max_length=10)

    @model_validator(mode='after')
    def unique_features(self):
        ids=[feature.feature_id for feature in self.critical_features]
        if len(ids)!=len(set(ids)):raise ValueError('Visible feature IDs must be unique')
        return self


class EngineeringDimensions(PhotoModel):
    values: list[ResolvedValue] = Field(min_length=3,max_length=60)
    assumptions: list[str] = Field(min_length=1,max_length=30)
    appearance: FurnitureAppearance | None = None
    reference_view: EngineeringPhotoView | None = None


class EngineeringRecognition(PhotoModel):
    """One request replaces separate dimension, decomposition and design calls."""
    dimensions: EngineeringDimensions
    design: PhotoDesign


class EngineeringComponent(PhotoModel):
    part_id: str = Field(pattern=r'^F[0-9]{3}$')
    label: str = Field(min_length=1,max_length=120)
    description: str = Field(min_length=1,max_length=1000)
    visible_evidence: str = Field(min_length=1,max_length=500)
    evidence_basis: Literal['IMAGE_OBSERVED','ASSUMED_COMPLETION'] = 'IMAGE_OBSERVED'


class EngineeringAssembly(PhotoModel):
    furniture_type: str = Field(min_length=1,max_length=120)
    parts: list[EngineeringComponent] = Field(min_length=1,max_length=60)
    assumptions: list[str] = Field(min_length=1,max_length=30)


class VisibleFeatureCheck(PhotoModel):
    feature_id: str = Field(pattern=r'^V[0-9]{3}$')
    status: Literal['MATCH','MISMATCH','UNVERIFIABLE']
    component_ids: list[str] = Field(max_length=60)
    evidence: str = Field(min_length=1,max_length=1000)
    correction: str = Field(max_length=1200)


class EngineeringReview(PhotoModel):
    accepted: bool
    findings: list[str] = Field(max_length=25)
    limitations: list[str] = Field(min_length=1,max_length=25)
    feature_checks: list[VisibleFeatureCheck] = Field(default_factory=list,max_length=12)
