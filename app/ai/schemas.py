from typing import Literal
from uuid import UUID
from pydantic import ConfigDict, Field, model_validator
from app.models import StrictModel, CabinetSpec, Material

Provenance = Literal['IMAGE_OBSERVED','AI_ESTIMATED','USER_PROVIDED','PHYSICALLY_MEASURED','DESIGNER_VERIFIED','FACTORY_APPROVED']
Severity = Literal['INFO','WARNING','ERROR','BLOCKER']
TEMPLATE_ID = 'cabinet.v1'
DIMENSION_FIELDS = ('width_mm','depth_mm','height_mm','panel_thickness_mm','back_thickness_mm')
DOOR_DIMENSION_FIELDS = ('door_frame_width_mm','door_frame_depth_mm','glass_thickness_mm',
                        'door_perimeter_gap_mm','glass_edge_clearance_mm','door_standoff_mm')


class ObservedComponent(StrictModel):
    component_id: str = Field(min_length=1,max_length=80)
    label: str = Field(min_length=1,max_length=200)
    kind: Literal['carcass','panel','door','frame','glass','shelf','drawer','leg','decoration','unknown']
    possible_materials: list[Material] = Field(max_length=4)
    provenance: Literal['IMAGE_OBSERVED','AI_ESTIMATED']
    evidence: str = Field(max_length=1000)


class MeasurementRequirement(StrictModel):
    field: str = Field(min_length=1,max_length=100)
    reason: str = Field(max_length=1000)


class FurnitureVisionResult(StrictModel):
    category: str = Field(min_length=1,max_length=100)
    components: list[ObservedComponent] = Field(max_length=100)
    proportions: list[str] = Field(max_length=20)
    observations: list[str] = Field(max_length=50)
    assumptions: list[str] = Field(max_length=50)
    missing_measurements: list[MeasurementRequirement] = Field(max_length=50)
    limitations: list[str] = Field(max_length=30)


class CandidateParameter(StrictModel):
    field: Literal['width_mm','depth_mm','height_mm','panel_thickness_mm','back_thickness_mm',
        'include_glazed_door','carcass_material','door_frame_wood','door_frame_width_mm','door_frame_depth_mm',
        'glass_thickness_mm','door_perimeter_gap_mm','glass_edge_clearance_mm','door_standoff_mm']
    suggested_value: float | str | bool | None
    provenance: Literal['IMAGE_OBSERVED','AI_ESTIMATED']
    explanation: str = Field(max_length=1000)


class ComponentRelationship(StrictModel):
    connection_id: str = Field(min_length=1,max_length=80)
    first_component_id: str = Field(min_length=1,max_length=80)
    second_component_id: str = Field(min_length=1,max_length=80)
    relationship: str = Field(max_length=200)


class DesignProposal(StrictModel):
    status: Literal['SUPPORTED_TEMPLATE','CUSTOM_DESIGN_REVIEW_REQUIRED']
    template_id: Literal['cabinet.v1'] | None
    summary: str = Field(max_length=3000)
    components: list[ObservedComponent] = Field(max_length=100)
    relationships: list[ComponentRelationship] = Field(max_length=200)
    parameters: list[CandidateParameter] = Field(max_length=50)
    required_measurements: list[MeasurementRequirement] = Field(max_length=50)
    unrepresented_features: list[str] = Field(max_length=50)

    @model_validator(mode='after')
    def coherent(self):
        if self.status == 'SUPPORTED_TEMPLATE' and (self.template_id != TEMPLATE_ID or self.unrepresented_features):
            raise ValueError('Unsupported features require custom design review')
        ids = [c.component_id for c in self.components]
        if len(ids) != len(set(ids)): raise ValueError('Duplicate proposed component ID')
        connections = [r.connection_id for r in self.relationships]
        if len(connections)!=len(set(connections)): raise ValueError('Duplicate proposed connection ID')
        if any(r.first_component_id not in ids or r.second_component_id not in ids for r in self.relationships):
            raise ValueError('Relationship refers to a missing component')
        return self


class JointSuggestion(StrictModel):
    connection_id: str = Field(max_length=80)
    profile_id: str | None
    status: Literal['FACTORY_APPROVED_REFERENCE','ENGINEERING_REVIEW_REQUIRED']
    reason: str = Field(max_length=1500)


class JoineryRecommendation(StrictModel):
    joints: list[JointSuggestion] = Field(max_length=200)
    required_hardware_types: list[str] = Field(max_length=30)
    unsupported_connections: list[str] = Field(max_length=100)


class QualityIssue(StrictModel):
    code: str = Field(min_length=1,max_length=100)
    severity: Severity
    message: str = Field(max_length=2000)
    component_ids: list[str] = Field(max_length=100)


class QualityReviewResult(StrictModel):
    summary: str = Field(max_length=4000)
    issues: list[QualityIssue] = Field(max_length=200)
    revision_recommended: bool
    # Explicitly constrained: AI can never certify engineering.
    engineering_certified: Literal[False]


class VerifiedMeasurement(StrictModel):
    field: str = Field(max_length=100)
    value_mm: float = Field(ge=0)
    provenance: Literal['USER_PROVIDED','PHYSICALLY_MEASURED','FACTORY_APPROVED']
    evidence_reference: str = Field(min_length=1,max_length=1000)


class VerifiedFurnitureSpecification(StrictModel):
    specification: CabinetSpec
    measurements: list[VerifiedMeasurement] = Field(min_length=5,max_length=50)
    designer_comment: str = Field(min_length=1,max_length=4000)


class CADGenerationRequest(StrictModel):
    model_config = ConfigDict(extra='forbid',strict=True,allow_inf_nan=False)
    revision_id: str = Field(pattern=r'^[0-9a-f-]{36}$')
    specification_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    template_id: Literal['cabinet.v1']


class CADGenerationResult(StrictModel):
    revision_id: str
    job_id: str
    specification_hash: str
    part_ids: list[str]
    status: Literal['GENERATED_REVIEW_ONLY']
    validation: dict


class ManufacturingPackage(StrictModel):
    revision_id: str
    job_id: str
    package_sha256: str
    manifest_sha256: str
    status: Literal['REVIEW']
    approved_for_manufacturing: Literal[False]
    part_count: int


class WorkflowRequest(StrictModel):
    project_id: UUID
    image_ids: list[UUID] = Field(default_factory=list,max_length=6)
    notes: str = Field(default='',max_length=4000)
    source_workflow_id: UUID | None = None
    revision_id: UUID | None = None
    max_corrections: int = Field(default=0,ge=0,le=1)
    idempotency_key: str | None = Field(default=None,min_length=1,max_length=80)


class PresentationRequest(StrictModel):
    project_id: UUID
    prompt: str = Field(min_length=1,max_length=4000)
    image_ids: list[UUID] = Field(default_factory=list,max_length=6)
    revision_id: UUID | None = None
    quality: Literal['low','medium','high'] = 'medium'
    size: Literal['1024x1024','1024x1536','1536x1024'] = '1024x1024'
    idempotency_key: str | None = Field(default=None,min_length=1,max_length=80)


class JoineryLookup(StrictModel):
    materials: list[Literal['solid_wood','plywood','mdf','glass']] = Field(min_length=1,max_length=4)


class AIPriceProfile(StrictModel):
    model_id: str = Field(min_length=1,max_length=80,pattern=r'^[A-Za-z0-9_.-]+$')
    currency: str = Field(pattern=r'^[A-Z]{3}$')
    input_per_million: float = Field(ge=0)
    output_per_million: float = Field(ge=0)
    cached_input_per_million: float | None = Field(default=None,ge=0)
    cache_write_per_million: float | None = Field(default=None,ge=0)
    cache_write_12h_per_million: float | None = Field(default=None,ge=0)
    long_context_threshold: int | None = Field(default=None,ge=1)
    image_price: float | None = Field(default=None,ge=0)
    image_model: str | None = None
    image_quality: Literal['low','medium','high'] | None = None
    image_size: str | None = None
    evidence_reference: str = Field(min_length=1,max_length=1000)
