"""AI estimation stays separate from evidence-backed cabinet verification."""
from typing import Literal
from pydantic import Field, ValidationError
from app.models import StrictModel, CabinetSpec, FurnitureParameters
from app.ai.schemas import WorkflowRequest
from app.ai.prompt_templates import BASE
from app.ai.openai_client import AIServiceError
from app.furniture_catalog import catalog, template


class Dimensions(StrictModel):
    width_mm: float | None = Field(default=None, gt=150, le=5000)
    depth_mm: float | None = Field(default=None, gt=80, le=3000)
    height_mm: float | None = Field(default=None, gt=100, le=3000)


class EstimateRequest(WorkflowRequest):
    template_id: str | None = Field(default=None,max_length=80)
    dimensions: Dimensions | None = None


class FurnitureEstimate(StrictModel):
    template_id: str = Field(min_length=1,max_length=80)
    width_mm: float = Field(gt=150,le=5000)
    depth_mm: float = Field(gt=80,le=3000)
    height_mm: float = Field(gt=100,le=3000)
    material: Literal['solid_wood','plywood','mdf']
    parameters: FurnitureParameters
    summary: str = Field(min_length=1,max_length=2000)
    assumptions: list[str] = Field(min_length=1,max_length=30)
    unrepresented_features: list[str] = Field(default_factory=list,max_length=30)
    common_default_fields: list[str] = Field(default_factory=list,max_length=40)


PROMPT=BASE+''' Estimate an editable furniture REVIEW model from the supplied photo/notes.
Choose a template from the catalog. Use its common starting dimensions when there is no scale;
these are estimates, never recovered measurements. Preserve requested template and supplied dimensions.
Predict visible design details using the bounded parameters: curved rails, sculpted seats, taper,
splay, leg curvature, back supports, shelves, doors and drawers. Use curve_profile wraparound
for a continuous horseshoe chair rail; arched for bowed backs; wave only if visible.
All other parameters must be populated with sensible defaults from the selected template.
Chair/stool/bench seat_height_mm must be <= overall height; arm height between seat+rail diameter and height.
Leg width*3+2*splay must be less than overall width AND depth. Leave room above the seat for the back rail.
curve_depth_mm controls back-rail bow; leg_curve_mm controls a three-section curved leg loft.
Back_slats are supporting uprights. Rounded/sculpted seats are approximations. Do not claim exact reconstruction.
For storage never combine drawers with doors or shelves in the same compartment in this version.
List every important feature that these primitives cannot represent, including mechanisms,
upholstery construction, fine carving, hidden connections or unknown profiles.
List common_default_fields only when a returned value exactly equals its catalog default;
use names width_mm, depth_mm, height_mm or parameters.FIELD. All remaining inferred values are AI estimates.
No measurements, joinery, stock or safety approvals may be invented.'''


def estimate(gateway,inputs,vision,images):
    chosen=inputs.get('template_id')
    candidates=[template(chosen)] if chosen else catalog()
    defaults=FurnitureParameters().model_dump(mode='json')
    return gateway.structured('design',PROMPT,dict(notes=inputs.get('notes',''),
        requested_template=chosen,supplied_dimensions=inputs.get('dimensions'),
        observations=vision.model_dump(mode='json') if vision else None,
        parameter_defaults=defaults,
        catalog=[{**{k:r[k] for k in ('id','name','family','dimensions_mm','limitations')},
            'parameter_overrides':{k:v for k,v in r['parameters'].items() if v!=defaults[k]}} for r in candidates]),
        FurnitureEstimate,images=images)


def specification(result,inputs,name):
    item=template(result.template_id)
    if not item or (inputs.get('template_id') and result.template_id!=inputs['template_id']):
        raise AIServiceError('AI_ESTIMATE_INVALID','AI selected a template outside the requested furniture catalog')
    values={k:getattr(result,k) for k in ('width_mm','depth_mm','height_mm')}
    supplied={k:v for k,v in (inputs.get('dimensions') or {}).items() if v is not None}
    values.update(supplied)
    try:
        spec=CabinetSpec(project_name=name,family=item['family'],furniture_template=item['id'],
            furniture_parameters=result.parameters,carcass_material=result.material,panel_thickness_mm=18,
            dimension_provenance='estimated',**values)
        if item['family']=='storage' and result.parameters.drawers and (result.parameters.doors or result.parameters.shelves):
            raise ValueError('Drawer templates require zero doors and shelves')
    except (ValidationError,ValueError):
        raise AIServiceError('AI_ESTIMATE_INVALID','Estimated dimensions or features do not fit this template. Adjust your dimensions or design notes and try again.')
    provenance={}
    defaults={k+'_mm':v for k,v in item['dimensions_mm'].items()}
    defaults.update({'parameters.'+k:v for k,v in item['parameters'].items()})
    actual={**values,**{'parameters.'+k:v for k,v in result.parameters.model_dump().items()}}
    for field,value in actual.items():
        source='USER_PROVIDED' if field in supplied else (
            'COMMON_DEFAULT_ESTIMATE' if field in result.common_default_fields and value==defaults.get(field) else 'AI_ESTIMATED')
        provenance[field]=dict(value=value,source=source,verified=False)
    return spec,provenance
