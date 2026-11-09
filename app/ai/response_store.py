"""Private, workflow-scoped replay of accepted Responses API requests."""
from sqlalchemy import select
from app.database import AIInvocation
from app.pipeline import canonical_hash
from app.ai.openai_client import AIServiceError

RESPONSE_PREFIX = 'openai_response_'


class WorkflowResponseStore:
    def __init__(self, runner):
        self.runner = runner

    def load(self, model, request_hash):
        runner = self.runner
        with runner.db.sessions() as session:
            workflow = runner.checked_workflow(session)
            invocation = session.scalar(select(AIInvocation).where(
                AIInvocation.workflow_id == runner.id, AIInvocation.stage == runner.stage,
                AIInvocation.model == model, AIInvocation.request_hash == request_hash,
            ).order_by(AIInvocation.created_at.desc()).limit(1))
            if invocation is None:
                return None
            cached = workflow.checkpoints.get(RESPONSE_PREFIX + invocation.id)
            if cached:
                payload = cached['payload']
                if canonical_hash(payload) != cached['sha256'] or payload.get('id') != invocation.response_id:
                    raise AIServiceError('AI_RESPONSE_CACHE_INVALID', 'Saved OpenAI response failed its integrity check')
                return dict(invocation_id=invocation.id, response_id=invocation.response_id, response=payload)
            if invocation.status == 'COMPLETED':
                raise AIServiceError('AI_RESPONSE_CACHE_MISSING', 'Completed OpenAI response is missing its saved result')
            if invocation.status in ('STARTED', 'QUEUED', 'IN_PROGRESS', 'PENDING', 'RECEIVED') and invocation.response_id:
                return dict(invocation_id=invocation.id, response_id=invocation.response_id)
            if invocation.status in ('SUBMITTING', 'UNTRACKED'):
                raise AIServiceError('AI_RESPONSE_UNTRACKED',
                    'OpenAI submission was interrupted before its response ID was received. '
                    'Its outcome is unknown; this workflow will not submit a duplicate generation.')
            if invocation.error_code == 'AI_BACKGROUND_EXPIRED':
                raise AIServiceError('AI_BACKGROUND_EXPIRED',
                    'The saved OpenAI response is no longer available. Start a new generation explicitly.')
            return None

    def save(self, invocation_id, response):
        runner = self.runner
        payload = response.to_dict(mode='json')
        with runner.db.sessions.begin() as session:
            workflow = runner.checked_workflow(session)
            invocation = session.get(AIInvocation, invocation_id)
            if not invocation or invocation.workflow_id != runner.id or invocation.stage != runner.stage:
                raise AIServiceError('INVOCATION_STATE_INVALID', 'Response does not belong to the active workflow stage')
            if invocation.response_id != response.id:
                raise AIServiceError('INVOCATION_STATE_INVALID', 'Response ID does not match the saved invocation')
            workflow.checkpoints = {**workflow.checkpoints, RESPONSE_PREFIX + invocation.id:
                dict(payload=payload, sha256=canonical_hash(payload))}
