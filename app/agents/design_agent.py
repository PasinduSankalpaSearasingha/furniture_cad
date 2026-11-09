from app.ai.schemas import DesignProposal, DIMENSION_FIELDS, DOOR_DIMENSION_FIELDS, MeasurementRequirement
from app.ai.prompt_templates import DESIGN


def propose(gateway,vision,notes,feedback=None):
    result = gateway.structured('design',DESIGN,dict(vision=vision.model_dump(mode='json'),notes=notes,
        supported_templates=[{'id':'cabinet.v1','capabilities':'carcass plus optional single framed glass door'}],
        feedback=feedback.model_dump(mode='json') if feedback else None),DesignProposal)
    unsupported = [c.label for c in vision.components if c.kind in ('shelf','drawer','leg')]
    if sum(c.kind=='door' for c in vision.components)>1 or sum(c.kind=='glass' for c in vision.components)>1:
        unsupported.append('Multiple doors or glass panels exceed cabinet.v1 capabilities')
    if unsupported:
        result = result.model_copy(update={'status':'CUSTOM_DESIGN_REVIEW_REQUIRED','template_id':None,
                                          'unrepresented_features':sorted(set(result.unrepresented_features+unsupported))})
    # Missing measurements cannot disappear because an LLM omitted them.
    required = list(DIMENSION_FIELDS)
    if any(c.kind in ('door','frame','glass') for c in vision.components): required += list(DOOR_DIMENSION_FIELDS)
    existing = {m.field for m in result.required_measurements}
    result.required_measurements += [MeasurementRequirement(field=f,reason='Required by the deterministic cabinet template; designer verification required')
                                     for f in required if f not in existing]
    return result
