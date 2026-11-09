from app.ai.schemas import QualityReviewResult, QualityIssue
from app.ai.prompt_templates import REVIEW


def review(gateway,payload,images=()):
    result = gateway.structured('review',REVIEW,payload,QualityReviewResult,images)
    return retain_deterministic(result,payload)


def retain_deterministic(result,payload):
    # Retain the actual deterministic findings separately and append immutable
    # copies of every critical issue to the advisory review result.
    deterministic = payload.get('deterministic_validation') or {}
    for finding in deterministic.get('findings',[]):
        if finding['severity'] in ('BLOCKER','ERROR'):
            result.issues.append(QualityIssue(code='DETERMINISTIC_'+finding['code'],severity=finding['severity'],
                message=finding['message'],component_ids=finding.get('part_ids',[])))
    if any(i.severity in ('BLOCKER','ERROR') for i in result.issues): result.revision_recommended = True
    return result
