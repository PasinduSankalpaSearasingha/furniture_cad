"""Bounded high-density reconstruction controls; defaults make no LLM calls."""
from typing import Literal
from pydantic import Field, model_validator
from .photo_schema import PhotoModel, PhotoDesign,PhotoPart
from .cad_optimizer_schema import CADOptimizationOptions, CADViewEvidence
from .reference_fit_schema import FitView
import os


def configured_bool(name, default=True):
    value = os.getenv(name, 'true' if default else 'false').lower()
    if value not in ('true','false','1','0'):
        raise ValueError(f'{name} must be true or false')
    return value in ('true','1')


def configured_points(name, default):
    return int(os.getenv(name, str(default)))


class DenseOptimizationOptions(PhotoModel):
    mode: Literal['economy', 'balanced', 'quality', 'extended'] = 'balanced'
    enabled: bool = Field(default_factory=lambda: configured_bool('GLOBAL_3D_FITTING_ENABLED'))
    adaptive_sampling: bool = True
    # Accepted for older saved requests, but never used by the global objective.
    multi_angle: bool = False
    primary_fitting_method: Literal['global_surface'] = Field(default_factory=lambda: os.getenv('PRIMARY_FITTING_METHOD','global_surface'))
    original_photo_validation: bool = Field(default_factory=lambda: configured_bool('ORIGINAL_PHOTO_VALIDATION_ENABLED'))
    coarse_points: int = Field(default_factory=lambda: configured_points('GLOBAL_COARSE_POINTS',25_000), ge=100, le=300_000)
    medium_points: int = Field(default_factory=lambda: configured_points('GLOBAL_MEDIUM_POINTS',100_000), ge=100, le=300_000)
    fine_points: int = Field(default_factory=lambda: configured_points('GLOBAL_FINE_POINTS',300_000), ge=100, le=500_000)
    verify_points: int = Field(default_factory=lambda: configured_points('GLOBAL_VERIFY_POINTS',750_000), ge=100, le=1_000_000)
    final_views: Literal[12, 24, 36, 48] = 48
    extended_verification: bool = Field(default_factory=lambda: configured_bool('GLOBAL_EXTENDED_VERIFY_ENABLED',False))
    bidirectional: bool = Field(default_factory=lambda: configured_bool('ENABLE_BIDIRECTIONAL_SURFACE_LOSS'))
    point_to_plane: bool = Field(default_factory=lambda: configured_bool('ENABLE_POINT_TO_PLANE_LOSS'))
    normal_loss: bool = Field(default_factory=lambda: configured_bool('ENABLE_SURFACE_NORMAL_LOSS'))
    sdf: Literal['auto','true','false'] = Field(default_factory=lambda: os.getenv('ENABLE_SDF_FITTING','auto').lower())
    geometry_cache: bool = Field(default_factory=lambda: configured_bool('ENABLE_CAD_GEOMETRY_CACHE'))
    align_reference: bool = True
    component_map: dict[int,str] = Field(default_factory=dict,max_length=60)
    robust_delta_mm: float = Field(default=2.,gt=0,le=100)
    point_to_plane_weight: float = Field(default=.25,ge=0,le=100)
    component_weight: float = Field(default=.25,ge=0,le=100)
    unmatched_tolerance_mm: float = Field(default=2.,gt=0,le=100)
    max_seconds: float = Field(default=180, ge=.1, le=1800)
    max_evaluations: int = Field(default=60, ge=1, le=300)
    max_parameters: int = Field(default=8, ge=1, le=24)
    exact_candidates: int = Field(default=4, ge=1, le=12)
    memory_mb: int = Field(default=2048, ge=8, le=16384)
    workers: int = Field(default=4, ge=1, le=16)
    camera_resolution: int = Field(default=64, ge=24, le=256)
    component_aware: bool = Field(default_factory=lambda: configured_bool('ENABLE_COMPONENT_AWARE_FITTING'))
    surface_weight: float = Field(default=1, gt=0, le=100)
    silhouette_weight: float = Field(default=.15, ge=0, le=100)
    normal_weight: float = Field(default=.05, ge=0, le=100)
    photo_weight: float = Field(default=2, ge=0, le=100)
    regularization_weight: float = Field(default=.001, ge=0, le=1)
    minimum_improvement: float = Field(default=1e-5, ge=0, le=1)
    controls: CADOptimizationOptions = Field(default_factory=CADOptimizationOptions)
    photo_views: list[CADViewEvidence] = Field(default_factory=list, max_length=6)
    photo_reference: FitView | None = None

    @model_validator(mode='after')
    def local_only(self):
        if not self.coarse_points <= self.medium_points <= self.fine_points:
            raise ValueError('Global point counts must increase from coarse to fine')
        if self.extended_verification and 'verify_points' not in self.model_fields_set:
            self.verify_points = configured_points('GLOBAL_EXTENDED_VERIFY_POINTS',1_000_000)
            if not 100 <= self.verify_points <= 1_000_000:
                raise ValueError('Extended verification exceeds the supported point limits')
        if self.controls.max_structural_llm_calls:
            raise ValueError('Mesh fitting permits zero structural LLM calls')
        if self.verify_points > 750_000 and not self.extended_verification:
            raise ValueError('One-million-point verification must be explicitly enabled')
        return self


class MeshReconstructionRequest(PhotoModel):
    image_id: str = Field(pattern=r'^[0-9a-fA-F-]{36}$')
    idempotency_key: str = Field(min_length=1, max_length=80)
    furniture_type: Literal['table', 'cabinet', 'chair'] = 'table'
    known_dimensions: dict[str, float] = Field(default_factory=dict, max_length=3)
    initial_plan: PhotoDesign | None = None
    reference_up_axis: Literal['Y', 'Z'] = 'Y'
    estimated_height_mm: float = Field(default=750, gt=100, le=3000)
    optimization: DenseOptimizationOptions = Field(default_factory=DenseOptimizationOptions)
    part_recognition: bool = True
    projection_convention: Literal['first_angle','third_angle'] = 'third_angle'
    drawing_paper: Literal['A4','A3','A2'] = 'A3'
    custom_paper_mm: tuple[float,float] | None = None

    @model_validator(mode='after')
    def measured_sizes(self):
        if self.custom_paper_mm and any(not 150<=value<=1200 for value in self.custom_paper_mm):
            raise ValueError('Custom drawing sheet dimensions must be 150..1200 mm')
        limits = {'width_mm': (150, 5000), 'depth_mm': (80, 3000), 'height_mm': (100, 3000)}
        for key, value in self.known_dimensions.items():
            if key not in limits or not limits[key][0] < value <= limits[key][1]:
                raise ValueError('Confirmed dimensions exceed the native furniture schema limits')
        if any(str(view.image_id) != self.image_id for view in self.optimization.photo_views):
            raise ValueError('Photo evidence must belong to the reconstruction image')
        if self.optimization.photo_reference and str(self.optimization.photo_reference.image_id) != self.image_id:
            raise ValueError('Foreground mask must belong to the reconstruction image')
        return self


class CADRevisionRequest(PhotoModel):
    idempotency_key: str = Field(min_length=1,max_length=80)
    parameters: dict[str,float] = Field(default_factory=dict,max_length=60)
    confirmed_dimensions: dict[str,float] = Field(default_factory=dict,max_length=3)
    part_labels: dict[str,str] = Field(default_factory=dict,max_length=60)
    construction_updates: list[PhotoPart] = Field(default_factory=list,max_length=60)
    boundary_reviews: dict[str,Literal['accepted','rejected','pending']] = Field(default_factory=dict,max_length=60)
    face_assignments: dict[str,list[int]] = Field(default_factory=dict,max_length=60)

    @model_validator(mode='after')
    def bounded_edit(self):
        ids=[p.part_id for p in self.construction_updates]
        if len(ids)!=len(set(ids)):raise ValueError('Only one construction replacement is allowed for each part ID')
        if sum(len(v) for v in self.face_assignments.values())>1_000_000:raise ValueError('Face review exceeds one million triangles')
        if any(not 1<=len(v)<=120 for v in self.part_labels.values()):raise ValueError('Part labels must contain 1..120 characters')
        return self
