"""Durable per-generation spending checks; cached replay and local exports are free.

Rates checked against official OpenAI Standard pricing on 2026-10-27. They are
frozen into new workflow inputs, never silently refreshed during a retry.
"""
import copy
from decimal import Decimal
from openai import APIStatusError
from sqlalchemy import select
from app.database import AIInvocation,AgentWorkflow
from app.ai.openai_client import AIServiceError,calculate_cost,provider_diagnostics,credit_limit

SOURCE='https://developers.openai.com/api/docs/pricing'
STOP_CODES={'AI_BUDGET_REACHED','AI_BUDGET_UNCERTAIN','AI_BUDGET_PRICE_REQUIRED','AI_BUDGET_CHECK_UNAVAILABLE','AI_CREDIT_LIMIT','AI_GENERATION_LIMIT'}
RATES={model:dict(model_id=model,currency='USD',input_per_million=i,cached_input_per_million=c,
    cache_write_per_million=w,cache_write_12h_per_million=w*2,output_per_million=o,
    long_context_threshold=272000,evidence_reference=SOURCE,verified_at='2026-10-27')
    for model,i,c,w,o in [('gpt-6-astra',10,1,12.5,50),('gpt-6.1-sol',2,.1,2.5,10)]}


def policy(config):
    return dict(version='FURNITURE_SPEND_V1',maximum_usd=str(Decimal(config.furniture_budget_usd)),
        rates=copy.deepcopy(RATES),service_tier='default',cache_ttl='30m')


def summary(session,workflow):
    config=(workflow.input_data or {}).get('ai_budget')
    if not config:return None
    ids=[workflow.id];analysis=(workflow.input_data or {}).get('analysis_workflow_id')
    if analysis and not (workflow.input_data or {}).get('engineering_edit'):
        parent=session.get(AgentWorkflow,analysis)
        if not parent or parent.project_id!=workflow.project_id or parent.mode!='PHOTO_ANALYZE':
            raise AIServiceError('AI_BUDGET_UNCERTAIN','The linked image analysis cannot be verified')
        ids.append(analysis)
    spent=Decimal(0);reserved=Decimal(0);uncertain=False;count=0
    for invocation in session.scalars(select(AIInvocation).where(AIInvocation.workflow_id.in_(ids))):
        count+=1;cost=invocation.cost or {}
        if invocation.usage and 'input_tokens' in invocation.usage:
            rate=cost.get('rate_snapshot') or config['rates'].get(invocation.model)
            actual=calculate_cost(invocation.usage,rate)
            if actual.get('total') is None:uncertain=True
            else:spent+=Decimal(actual['total'])
        elif cost.get('status')=='BUDGET_RESERVED':
            reserved+=Decimal(cost['reserved_usd'])
            uncertain=True
        elif invocation.status not in ('FAILED',):uncertain=True
        if cost.get('status')=='USAGE_UNCERTAIN':uncertain=True
    return dict(maximum_usd=config['maximum_usd'],spent_usd=str(spent),reserved_usd=str(reserved),
        remaining_usd=str(max(Decimal(0),Decimal(config['maximum_usd'])-spent-reserved)),
        uncertain=uncertain,calls=count,currency='USD',includes_linked_analysis=len(ids)>1)


def reserve(session,workflow,quote):
    current=summary(session,workflow)
    if not current:raise AIServiceError('AI_BUDGET_UNCERTAIN','The saved AI spending policy is missing')
    if current['uncertain']:
        raise AIServiceError('AI_BUDGET_UNCERTAIN','A previous AI request has unconfirmed usage. Saved responses can be retrieved, but no new paid request will be submitted.')
    if Decimal(quote['reserved_usd'])>Decimal(current['remaining_usd']):
        raise AIServiceError('AI_BUDGET_REACHED',f"The ${current['maximum_usd']} AI budget cannot cover another request. Any valid CAD draft is retained; automatic paid corrections have stopped.")
    return dict(status='BUDGET_RESERVED',currency='USD',**quote)


class FurnitureBudget:
    def __init__(self,runner):self.runner=runner

    def quote(self,sdk,request):
        runner=self.runner
        with runner.db.sessions() as session:
            workflow=runner.checked_workflow(session);current=summary(session,workflow)
            rate=workflow.input_data['ai_budget']['rates'].get(request['model'])
        if not rate:raise AIServiceError('AI_BUDGET_PRICE_REQUIRED','Verify the configured model price before submitting a paid furniture request')
        if current['uncertain']:raise AIServiceError('AI_BUDGET_UNCERTAIN','Previous AI usage is unconfirmed. Resume its saved response before making another request.')
        if Decimal(current['remaining_usd'])<=0:raise AIServiceError('AI_BUDGET_REACHED',f"The ${current['maximum_usd']} furniture AI budget has been reached.")
        # The count endpoint includes images, schemas and accumulated history.
        # It does not generate a completion. Never guess image token costs.
        fields=('model','input','instructions','parallel_tool_calls','reasoning','text','tool_choice','tools')
        try:
            count=sdk.responses.input_tokens.count(**{k:request[k] for k in fields if k in request},timeout=30).input_tokens
            if type(count) is not int or count<0:raise ValueError('Invalid input count')
        except APIStatusError as exc:
            if exc.status_code==429 and credit_limit(provider_diagnostics(exc)):
                raise AIServiceError('AI_CREDIT_LIMIT','AI credits or the account spending limit were reached. Available work has been saved.',
                    provider=provider_diagnostics(exc)) from exc
            if exc.status_code==401:
                raise AIServiceError('AI_AUTHENTICATION_FAILED','Backend OpenAI credentials were rejected (HTTP 401). Update the backend API key before generating furniture.',
                    provider=provider_diagnostics(exc)) from exc
            raise AIServiceError('AI_BUDGET_CHECK_UNAVAILABLE','The AI spending check is temporarily unavailable. No new generation request was submitted.',True,
                provider=provider_diagnostics(exc)) from exc
        except Exception as exc:
            raise AIServiceError('AI_BUDGET_CHECK_UNAVAILABLE','The AI spending check is temporarily unavailable. No new generation request was submitted.',True) from exc
        # Include headroom for provider-internal formatting tokens. Cache hits
        # are an upside: reserve at the highest applicable 30-minute write rate.
        count+=1024
        if count>272000:raise AIServiceError('AI_BUDGET_REACHED','The CAD repair context is too large for the furniture spending policy. The saved design is retained.')
        amount=(Decimal(count)*Decimal(str(max(rate['input_per_million'],rate['cache_write_per_million'])))+
            Decimal(request['max_output_tokens'])*Decimal(str(rate['output_per_million'])))/Decimal(1000000)
        return dict(reserved_usd=str(amount),input_token_bound=count,output_token_bound=request['max_output_tokens'],rate_snapshot=rate)
