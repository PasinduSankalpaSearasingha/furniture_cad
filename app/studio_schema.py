"""Strict public workflow inputs and bounded advisory correction outputs."""
from typing import Literal
from uuid import UUID
from pydantic import Field
from .agents.photo_agent import PhotoRequest
from .ai.schemas import WorkflowRequest
from .photo_schema import PhotoModel, InputValue, CADFeature, PhotoPart
from .rendering.config import RenderOptions
from .reference_fit_schema import ReferenceFitOptions,FitView


class StudioRequest(PhotoRequest):
    modeling_engine: Literal['cad','blender']='cad'
    modeling_views: list[FitView] = Field(default_factory=list,max_length=4)
    blender_local_only: bool = False
    reference_fit: ReferenceFitOptions | None = None
    prompt: str = Field(default='',max_length=6000)
    concept_id: UUID | None = None
    auto_render: bool = True
    render_options: RenderOptions = Field(default_factory=RenderOptions)
    source_revision_id: UUID | None = None
    parameter_changes: list[InputValue] = Field(default_factory=list,max_length=40)
    accepted_correction_id: str | None = Field(default=None,max_length=80)
    review_workflow_id: UUID | None = None
    feature_component_id: str | None = Field(default=None,pattern=r'^F[0-9]{3}$')
    features: list[CADFeature] = Field(default_factory=list,max_length=24)


class ConceptRequest(WorkflowRequest):
    prompt: str = Field(min_length=3,max_length=6000)
    count: int = Field(default=1,ge=1,le=3)
    quality: Literal['low','medium','high','xhigh','max']='low'
    size: Literal['1024x1024','1536x1024','1024x1536']='1024x1024'
    output_format: Literal['png','jpeg','webp']='png'
    backend: Literal['responses','direct']='responses'
    operation: Literal['generate','variation','edit','material','marketing']='generate'


class BlenderRequest(WorkflowRequest):
    render_options: RenderOptions = Field(default_factory=RenderOptions)
    render_workflow_id: UUID | None = None


class Correction(PhotoModel):
    correction_id: str = Field(pattern=r'^C[0-9]{3}$')
    component_id: str | None = Field(default=None,max_length=80)
    issue: str = Field(min_length=1,max_length=800)
    proposed_operation: Literal['update_parameter','modify_geometry','material_only']
    parameter_key: str | None = Field(default=None,pattern=r'^[a-z][a-z0-9_]{0,59}$')
    new_value: float | None = Field(default=None,ge=-5000,le=10000)
    instructions: str = Field(min_length=1,max_length=1200)
    requires_confirmation: Literal[True]=True


class VisualReview(PhotoModel):
    summary: str = Field(min_length=1,max_length=2400)
    geometry_differences: list[str] = Field(max_length=30)
    appearance_differences: list[str] = Field(max_length=30)
    unknown_geometry: list[str] = Field(max_length=30)
    corrections: list[Correction] = Field(max_length=12)
    engineering_accuracy_verified: Literal[False]=False
    approved_for_manufacturing: Literal[False]=False
