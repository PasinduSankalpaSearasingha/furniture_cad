"""CAD specialist: strict, deterministic tools; never a generated-code executor."""
from dataclasses import dataclass
from app.models import CabinetSpec
from app.database import Revision, User
from app.ai.schemas import CADGenerationRequest, TEMPLATE_ID
from app.ai.openai_client import AIServiceError


@dataclass(frozen=True)
class VerifiedCADContext:
    revision_id: str
    specification_hash: str
    specification: CabinetSpec


def select_furniture_template(template_id: str):
    if template_id != TEMPLATE_ID:
        raise AIServiceError('CUSTOM_DESIGN_REVIEW_REQUIRED','No deterministic implementation exists for this template')
    return dict(template_id=TEMPLATE_ID,family='cabinet',version=1,
                part_ids=['P001','P002','P003','P004','P005'],optional_glazed_door=['D001','D002','D003','D004','G001'])


def validate_design_specification(request: CADGenerationRequest, session):
    from app.pipeline import canonical_hash
    select_furniture_template(request.template_id)
    revision = session.get(Revision,request.revision_id)
    if revision is None: raise AIServiceError('DESIGN_REVISION_REQUIRED','Design revision was not found')
    verifier = session.get(User,revision.verified_by) if revision.verified_by else None
    if (not verifier or not verifier.active or verifier.role!='designer' or
            revision.verified_spec_hash != revision.spec_hash or revision.spec_hash != request.specification_hash or
            canonical_hash(revision.specification) != request.specification_hash):
        raise AIServiceError('DESIGNER_VERIFICATION_REQUIRED','Designer verification of the exact immutable specification is required')
    spec = CabinetSpec.model_validate(revision.specification)
    if spec.display_layout is not None or spec.family!='cabinet':
        raise AIServiceError('CUSTOM_DESIGN_REVIEW_REQUIRED','The image-study template is not a verified cabinet.v1 design')
    if not (spec.measured_dimensions_confirmed and spec.dimension_provenance=='physically_verified' and spec.measurement_reference):
        raise AIServiceError('MEASUREMENTS_REQUIRED','Verified physical measurements and evidence are required')
    return VerifiedCADContext(revision.id,revision.spec_hash,spec)


def generate_parametric_cad(context: VerifiedCADContext):
    from app.cad import FURNITURE_TEMPLATES
    from app.pipeline import canonical_hash
    if canonical_hash(context.specification.model_dump(mode='json')) != context.specification_hash:
        raise AIServiceError('SPECIFICATION_HASH_MISMATCH','Verified input changed before CAD generation')
    # Existing template applies approved joint features once, before assembling.
    return FURNITURE_TEMPLATES[context.specification.family](context.specification)


def validate_cad_geometry(context,build):
    from app.validation_engine import validate_build
    return validate_build(context.specification,build)


def export_cad_assembly(context,build,output):
    from app.cad import export_cad
    export_cad(build,output)


def tool_definitions():
    from openai.lib._pydantic import to_strict_json_schema
    # Public metadata only. Invocations additionally require a server-created
    # VerifiedCADContext; clients cannot provide a verified=true switch.
    return [dict(type='function',name='generate_parametric_cad',strict=True,
                 description='Generate an allowlisted CAD template for an existing designer-verified revision',
                 parameters=to_strict_json_schema(CADGenerationRequest))]
