"""Bounded numerical evidence and controls; qualitative AI labels are not targets."""
from typing import Literal
from uuid import UUID
from pydantic import Field, model_validator
from app.photo_schema import PhotoModel


class CADCamera(PhotoModel):
    image_size: tuple[int, int] = Field(description='Width and height in pixels')
    projection: Literal['perspective', 'orthographic'] = 'perspective'
    intrinsic_matrix: list[list[float]]
    rotation_vector: tuple[float, float, float] = (0, 0, 0)
    translation_mm: tuple[float, float, float] = (0, 0, 2000)
    distortion: list[float] = Field(default_factory=list, max_length=14)
    reliable: bool = False
    uncertainty: str = Field(default='Uncalibrated camera pose', max_length=400)

    @model_validator(mode='after')
    def valid_camera(self):
        import numpy as np
        if not all(16 <= n <= 16384 for n in self.image_size):
            raise ValueError('Camera image dimensions must be within 16..16384 pixels')
        k = np.asarray(self.intrinsic_matrix)
        if (k.shape != (3, 3) or k[0, 0] <= 0 or k[1, 1] <= 0 or
                not np.allclose(k[2], [0, 0, 1]) or k[0, 1] != 0 or k[1, 0] != 0):
            raise ValueError('Use a positive, zero-skew camera intrinsic matrix')
        if len(self.distortion) not in (0, 4, 5, 8, 12, 14):
            raise ValueError('Unsupported OpenCV distortion coefficient count')
        if self.projection == 'orthographic' and any(self.distortion):
            raise ValueError('Lens distortion requires a perspective camera')
        return self


class CADLandmark(PhotoModel):
    part_id: str = Field(pattern=r'^F[0-9]{3}$')
    anchor: Literal['center', 'top', 'bottom', 'local_point', 'size_corner', 'section_center', 'path_point', 'outline_point'] = 'center'
    local_point: tuple[float, float, float] = (0, 0, 0)
    point_index: int = Field(default=0, ge=0, le=36)
    image_point: tuple[float, float]
    confidence: float = Field(default=1, gt=0, le=1)
    visible: bool = True
    evidence_id: str | None = Field(default=None, pattern=r'^V[0-9]{3}$')
    fixed_world_point: tuple[float, float, float] | None = None


class CADCurveObservation(PhotoModel):
    part_id: str = Field(pattern=r'^F[0-9]{3}$')
    source: Literal['path', 'outline', 'edge'] = 'path'
    edge_index: int = Field(default=0, ge=0, le=1000)
    image_points: list[tuple[float, float]] = Field(min_length=3, max_length=256)
    confidence: float = Field(default=1, gt=0, le=1)
    visible: bool = True
    evidence_id: str | None = Field(default=None, pattern=r'^V[0-9]{3}$')


class CADViewEvidence(PhotoModel):
    image_id: UUID
    camera: CADCamera | None = None
    landmarks: list[CADLandmark] = Field(default_factory=list, max_length=80)
    curves: list[CADCurveObservation] = Field(default_factory=list, max_length=20)
    silhouette: list[tuple[float, float]] = Field(default_factory=list, max_length=1024)
    silhouette_reviewed: bool = False
    silhouette_confidence: float = Field(default=1, gt=0, le=1)
    silhouette_complete: bool = True
    confidence: float = Field(default=1, gt=0, le=1)

    @model_validator(mode='after')
    def valid_pixels(self):
        if self.silhouette and len(self.silhouette) < 3:
            raise ValueError('A silhouette requires at least three boundary samples')
        if self.camera:
            w, h = self.camera.image_size
            pixels = [p.image_point for p in self.landmarks]
            pixels += [p for curve in self.curves for p in curve.image_points] + self.silhouette
            if any(not (0 <= x < w and 0 <= y < h) for x, y in pixels):
                raise ValueError('Evidence pixels must lie inside their camera image')
        return self


class CADParameterOverride(PhotoModel):
    parameter_id: str = Field(min_length=1, max_length=160)
    minimum: float | None = None
    maximum: float | None = None
    optimize: bool = True
    uncertainty: float | None = Field(default=None, ge=0)
    evidence_ids: list[str] = Field(default_factory=list, max_length=12)


class CADParameterRelation(PhotoModel):
    """Hard linear coupling, e.g. mirrored X coordinates: left = -right."""
    left: str = Field(max_length=160)
    right: str = Field(max_length=160)
    factor: float = Field(default=1, ge=-10, le=10)
    offset: float = Field(default=0, ge=-10000, le=10000)
    tolerance: float = Field(default=.01, gt=0, le=1)


class CADLossWeights(PhotoModel):
    landmark: float = Field(default=1, ge=0, le=100)
    silhouette: float = Field(default=1, ge=0, le=100)
    curve: float = Field(default=1, ge=0, le=100)
    constraint: float = Field(default=10, ge=0, le=100)
    regularization: float = Field(default=.00000001, ge=0, le=1)


class CADOptimizationOptions(PhotoModel):
    enabled: bool = True
    mode: Literal['economy', 'balanced', 'quality'] = 'balanced'
    views: list[CADViewEvidence] = Field(default_factory=list, max_length=6)
    component_ids: list[str] = Field(default_factory=list, max_length=60)
    feature_components: dict[str, list[str]] = Field(default_factory=dict, max_length=12)
    mismatch_feature_ids: list[str] = Field(default_factory=list, max_length=12)
    parameter_ids: list[str] = Field(default_factory=list, max_length=64)
    parameters: list[CADParameterOverride] = Field(default_factory=list, max_length=64)
    relations: list[CADParameterRelation] = Field(default_factory=list, max_length=32)
    required_contacts: list[tuple[str, str]] = Field(default_factory=list, max_length=120)
    weights: CADLossWeights = Field(default_factory=CADLossWeights)
    max_evaluations: int | None = Field(default=None, ge=1, le=300)
    max_iterations: int = Field(default=20, ge=1, le=100)
    max_seconds: float | None = Field(default=None, ge=.05, le=600)
    max_parameters: int | None = Field(default=None, ge=1, le=24)
    max_displacement_mm: float = Field(default=30, gt=0, le=100)
    max_rotation_deg: float = Field(default=12, gt=0, le=30)
    max_relative_change: float = Field(default=.25, gt=0, le=.5)
    minimum_improvement: float = Field(default=1e-7, ge=0, le=.1)
    target_error: float = Field(default=1e-5, ge=0, le=.1)
    stagnation_evaluations: int = Field(default=20, ge=3, le=100)
    align_cameras: bool = False
    allow_uncertain_camera: bool = False
    max_structural_llm_calls: int = Field(default=0, ge=0, le=1)

    @model_validator(mode='after')
    def unique_inputs(self):
        if len({v.image_id for v in self.views}) != len(self.views):
            raise ValueError('Duplicate optimization reference image')
        if len({v.parameter_id for v in self.parameters}) != len(self.parameters):
            raise ValueError('Duplicate optimization parameter override')
        return self

    def budgets(self):
        evaluations, seconds, parameters = {
            'economy': (15, 20, 3), 'balanced': (40, 60, 8), 'quality': (100, 180, 16)
        }[self.mode]
        return (self.max_evaluations or evaluations,
                self.max_seconds or seconds, self.max_parameters or parameters)
