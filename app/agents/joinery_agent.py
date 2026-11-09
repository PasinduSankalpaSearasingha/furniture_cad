from app.ai.schemas import JoineryRecommendation, JoineryLookup
from app.ai.prompt_templates import JOINERY
from app.models import JointProfile


def recommend(gateway,design,catalog):
    approved = {}
    for payload in catalog:
        profile = JointProfile.model_validate(payload)
        if profile.approved and profile.evidence_reference:
            approved[profile.profile_id] = profile
    def lookup(arguments):
        return [p.model_dump(mode='json') for p in approved.values()
                if set(arguments.materials) <= {m.value for m in p.compatible_materials}]
    result = gateway.structured('joinery',JOINERY,dict(design=design.model_dump(mode='json')),
        JoineryRecommendation,tools={'lookup_approved_joinery':dict(schema=JoineryLookup,
            description='Read approved, evidence-referenced factory profiles compatible with every requested material',handler=lookup)})
    connections = {c.connection_id:c for c in design.relationships}
    components = {c.component_id:c for c in design.components}
    for suggestion in result.joints:
        profile = approved.get(suggestion.profile_id)
        connection = connections.get(suggestion.connection_id)
        materials = set()
        if connection:
            for pid in (connection.first_component_id,connection.second_component_id):
                materials.update(components[pid].possible_materials)
        if (not profile or not connection or not materials or not materials <= set(profile.compatible_materials)):
            suggestion.profile_id = None
            suggestion.status = 'ENGINEERING_REVIEW_REQUIRED'
            suggestion.reason = 'Server could not verify an approved compatible catalog reference. '+suggestion.reason
    return result
