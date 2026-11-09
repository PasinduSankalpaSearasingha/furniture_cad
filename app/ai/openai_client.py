"""Responses API with strict output schemas, bounded tools, access checks and redacted errors."""
import json
import os
import re
import time
from decimal import Decimal
from openai import OpenAI, APITimeoutError, APIConnectionError, APIStatusError
from openai.lib._pydantic import to_strict_json_schema
from openai.types.responses import Response
from pydantic import ValidationError
from app.pipeline import canonical_hash
from dataclasses import dataclass


@dataclass
class ToolResult:
    """Server-owned tool evidence, including real model renders for visual feedback."""
    payload: dict
    images: list


class AIServiceError(RuntimeError):
    def __init__(self,code,message,retryable=False,*,provider=None):
        super().__init__(message)
        self.code,self.message,self.retryable = code,message,retryable
        self.provider = provider or {}

    def as_dict(self):
        result = dict(code=self.code,message=self.message,retryable=self.retryable)
        if self.provider: result['provider'] = self.provider
        return result


def credit_limit(provider):
    """Billing limits require account changes, not retries or model fallback."""
    return provider.get('type')=='insufficient_quota' or provider.get('code') in {
        'insufficient_quota','credit_balance_exhausted','organization_spend_limit_exceeded',
        'project_spend_limit_exceeded','organization_usage_limit_exceeded'}


def provider_diagnostics(error):
    """Keep only provider identifiers, never free-text errors or request bodies."""
    body = error.body if isinstance(error.body,dict) else {}
    if isinstance(body.get('error'),dict): body = body['error']
    details = dict(http_status=error.status_code)
    identifiers = {key:body.get(key) for key in ('type','code','param')}
    identifiers['request_id'] = getattr(error,'request_id',None)
    for key,value in identifiers.items():
        if (isinstance(value,str) and len(value)<=160 and
                re.fullmatch(r'[A-Za-z0-9_.\[\]-]+',value) and not value.startswith('sk-')):
            details[key] = value
    return details


def usage_dict(response):
    raw = response.model_dump(mode='json') if hasattr(response,'model_dump') else vars(response)
    usage = raw.get('usage') or {}
    generated = [o for o in raw.get('output',[]) if o.get('type')=='image_generation_call']
    return dict(input_tokens=usage.get('input_tokens',0),output_tokens=usage.get('output_tokens',0),
                cached_input_tokens=(usage.get('input_tokens_details') or {}).get('cached_tokens',0),
                generated_images=len(generated),image_usage=[o.get('usage') for o in generated],
                raw=usage)


def calculate_cost(usage,profile,render_options=None):
    missing = {'status':'PRICE_REQUIRED','total':None,'currency':None}
    if profile is None: return missing
    cached = usage.get('cached_input_tokens',0)
    if cached and profile.get('cached_input_per_million') is None: return missing
    details=(usage.get('raw') or {}).get('input_tokens_details') or {}
    writes=details.get('cache_write_tokens',0) or 0
    lifetime=details.get('cache_write_tokens_details') or {}
    long_writes=lifetime.get('ttl_12h_tokens',0) or 0
    if writes and profile.get('cache_write_per_million') is None:return missing
    if long_writes and profile.get('cache_write_12h_per_million') is None:return missing
    ordinary=usage.get('input_tokens',0)-cached-writes
    if min(ordinary,cached,writes,long_writes,writes-long_writes)<0:return missing
    d = lambda value: Decimal(str(value))
    long_context=bool(profile.get('long_context_threshold') and usage.get('input_tokens',0)>profile['long_context_threshold'])
    total = ((d(ordinary)*d(profile['input_per_million']) +
             d(cached)*d(profile.get('cached_input_per_million') or 0) +
             d(writes-long_writes)*d(profile.get('cache_write_per_million') or 0) +
             d(long_writes)*d(profile.get('cache_write_12h_per_million') or 0))*d(2 if long_context else 1) +
             d(usage.get('output_tokens',0))*d(profile['output_per_million'])*d('1.5' if long_context else 1))/d(1_000_000)
    if usage.get('generated_images'):
        if not render_options or profile.get('image_price') is None or any(
            profile.get('image_'+key) != render_options.get(key) for key in ('model','quality','size')):
            return missing
        total += d(usage['generated_images'])*d(profile['image_price'])
    return dict(status='ESTIMATE_FROM_CONFIGURED_RATES',total=str(total),currency=profile['currency'],rate_snapshot=profile)


class ResponsesGateway:
    def __init__(self,config,models,record=lambda event,data:None,sdk=None,response_store=None):
        self.config,self.models,self.record = config,dict(models),record
        self.response_store = response_store
        self.request_budget = None
        if sdk is None:
            if not config.ai_enabled or not os.getenv('OPENAI_API_KEY'):
                raise AIServiceError('AI_CONFIGURATION_REQUIRED','Enable AI and configure a backend OpenAI API key')
            sdk = OpenAI(timeout=config.openai_timeout_seconds,max_retries=0)
        self.sdk = sdk
        self.available = set()

    def model_for(self,role):
        model = self.models.get(role,'')
        if not model: raise AIServiceError('MODEL_CONFIGURATION_REQUIRED',f'Configure the {role} model explicitly')
        return model

    def check_model(self,model):
        if model in self.available: return
        self.sdk.models.retrieve(model)
        self.available.add(model)
        self.record('model_access_checked',{'model':model})

    def _poll_response(self,response_id,invocation,model,budget,response=None):
        """Retry only retrievals; an accepted generation is never re-submitted."""
        deadline = time.monotonic() + budget
        interval = self.config.openai_poll_interval_seconds
        failures = 0
        while response is None or response.status in ('queued','in_progress'):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AIServiceError('AI_BACKGROUND_PENDING',
                    'OpenAI is still processing the saved response. Resume this workflow to check the same request.',True)
            self.record('invocation_polling',dict(invocation_id=invocation,model=model,response_id=response_id,
                status=response.status if response else 'in_progress'))
            try:
                result = self.sdk.responses.retrieve(response_id,timeout=min(30,remaining))
                if result.id != response_id:
                    raise AIServiceError('AI_RESPONSE_ID_MISMATCH','OpenAI returned a different response ID')
                response = result
                failures = 0
            except (APITimeoutError,APIConnectionError):
                failures += 1
            except APIStatusError as exc:
                if exc.status_code==429 and credit_limit(provider_diagnostics(exc)):
                    raise AIServiceError('AI_CREDIT_LIMIT','AI credits or the account spending limit were reached. Available work has been saved.',
                        provider=provider_diagnostics(exc)) from exc
                if exc.status_code == 404:
                    raise AIServiceError('AI_BACKGROUND_EXPIRED',
                        'The saved OpenAI response is no longer available. Start a new generation explicitly.',
                        provider=provider_diagnostics(exc)) from exc
                if exc.status_code != 429 and exc.status_code < 500:
                    raise
                failures += 1
            if failures >= 3:
                raise AIServiceError('AI_BACKGROUND_POLL_INTERRUPTED',
                    'OpenAI status checks were interrupted. Resume this workflow to retrieve the saved response.',True)
            if response is None or response.status in ('queued','in_progress'):
                time.sleep(max(0,min(interval * 2**failures,deadline-time.monotonic())))
        return response

    def _create(self,role,**kwargs):
        primary = self.model_for(role)
        candidates = [primary]
        fallback = self.models.get('fallback')
        if fallback and fallback != primary: candidates.append(fallback)
        last = None
        budget = kwargs.get('timeout',self.config.openai_timeout_seconds)
        requests = {model:dict(model=model,store=False,**kwargs) for model in candidates}
        if self.request_budget:
            for request in requests.values():
                request.update(service_tier='default',prompt_cache_options={'ttl':'30m'})
        request_hashes = {model:canonical_hash({k:v for k,v in request.items() if k not in ('timeout','background')})
            for model,request in requests.items()}
        saved_requests = {}
        if self.response_store:
            # Prefer an accepted fallback response before attempting the primary
            # again. Polling settings must not change a request's identity.
            for model in candidates:
                saved = self.response_store.load(model,request_hashes[model])
                if saved:
                    saved_requests[model] = saved
                    candidates = [model]
                    break
        for model in candidates:
            saved = saved_requests.get(model)
            background = self.config.openai_background_mode or bool(saved)
            request = requests[model]
            if background: request['background'] = True
            reservation=self.request_budget.quote(self.sdk,request) if self.request_budget and not saved else None
            invocation = saved['invocation_id'] if saved else self.record('invocation_started',
                {'model':model,'request_hash':request_hashes[model],
                    **({'budget_reservation':reservation} if reservation else {})})
            response_id = saved['response_id'] if saved else None
            submission_started = False
            response = None
            try:
                if saved and saved.get('response'):
                    try: response = Response.model_validate(saved['response'])
                    except ValidationError as exc:
                        raise AIServiceError('AI_RESPONSE_CACHE_INVALID','Saved OpenAI response is invalid') from exc
                elif response_id:
                    response = self._poll_response(response_id,invocation,model,budget)
                else:
                    self.check_model(model)
                    if background:
                        request['timeout'] = min(30,budget)
                    self.record('invocation_submitting',dict(invocation_id=invocation,model=model))
                    submission_started = True
                    response = self.sdk.responses.create(**request)
                    response_id = response.id
                    self.record('invocation_submitted',dict(invocation_id=invocation,model=model,
                        response_id=response_id,status=response.status))
                    if background:
                        response = self._poll_response(response_id,invocation,model,budget,response)
                if self.response_store and response.status=='completed':
                    self.response_store.save(invocation,response)
                self.record('invocation_completed',{'invocation_id':invocation,'model':model,
                    'response_id':response.id,'usage':usage_dict(response)})
                if response.status == 'cancelled':
                    raise AIServiceError('AI_RESPONSE_CANCELLED','OpenAI generation was cancelled')
                if response.status == 'failed':
                    code = getattr(getattr(response,'error',None),'code',None)
                    provider = dict(code=code) if (isinstance(code,str) and not code.startswith('sk-') and
                        re.fullmatch(r'[A-Za-z0-9_-]{1,100}',code)) else {}
                    if credit_limit(provider):
                        raise AIServiceError('AI_CREDIT_LIMIT','AI credits or the account spending limit were reached. Available work has been saved.',provider=provider)
                    raise AIServiceError('AI_RESPONSE_FAILED','OpenAI generation failed',
                        code in ('server_error','rate_limit_exceeded'),provider=provider)
                if response.status != 'completed':
                    raise AIServiceError('AI_RESPONSE_INCOMPLETE','The model response did not complete',True)
                for item in response.output:
                    if getattr(item,'type',None)=='message' and any(getattr(c,'type',None)=='refusal' for c in item.content):
                        raise AIServiceError('AI_REFUSAL','The model declined the request')
                return response
            except AIServiceError as exc:
                if exc.code=='WORKFLOW_LEASE_LOST': raise
                pending = bool(background and response_id and exc.code!='AI_BACKGROUND_EXPIRED' and
                    (response is None or response.status in ('queued','in_progress')))
                self.record('invocation_pending' if pending else 'invocation_failed',
                    dict(invocation_id=invocation,model=model,error_code=exc.code,
                        **({'provider':exc.provider} if exc.provider else {})))
                raise
            except APITimeoutError:
                last = AIServiceError('AI_TIMEOUT','OpenAI request timed out; retry explicitly to resume',True)
            except APIConnectionError:
                last = AIServiceError('AI_CONNECTION_ERROR','OpenAI connection failed',True)
            except APIStatusError as exc:
                provider = provider_diagnostics(exc)
                if exc.status_code==429: code,message,retryable = 'AI_RATE_LIMIT','OpenAI rate limit reached',True
                elif exc.status_code in (403,404): code,message,retryable = 'MODEL_UNAVAILABLE','Configured model is unavailable to this account',True
                elif exc.status_code==401: code,message,retryable = 'AI_AUTHENTICATION_FAILED','Backend OpenAI credentials were rejected',False
                elif exc.status_code==400: code,message,retryable = 'AI_BAD_REQUEST','OpenAI rejected the request format',False
                elif exc.status_code==422: code,message,retryable = 'AI_UNPROCESSABLE_REQUEST','OpenAI could not process the request',False
                else: code,message,retryable = 'AI_API_ERROR','OpenAI rejected or failed the request',exc.status_code>=500
                if exc.status_code==429 and credit_limit(provider):
                    code,message,retryable='AI_CREDIT_LIMIT','AI credits or the account spending limit were reached. Available work has been saved.',False
                message += f' (HTTP {exc.status_code})'
                if provider.get('param'): message += f'. Request parameter: {provider["param"]}'
                last = AIServiceError(code,message,retryable,provider=provider)
            self.record('invocation_pending' if response_id and background else 'invocation_failed',
                {'invocation_id':invocation,'model':model,'error_code':last.code,
                    'submission_uncertain':bool(submission_started and not response_id and
                        last.code in ('AI_TIMEOUT','AI_CONNECTION_ERROR','AI_API_ERROR')),
                                             **({'provider':last.provider} if last.provider else {})})
            # Only access/rate-limit failures use a configured fallback. Never
            # silently duplicate a possibly billed timed-out generation.
            if response_id or last.code not in ('MODEL_UNAVAILABLE','AI_RATE_LIMIT'): raise last
            self.record('model_fallback',{'from_model':model,'reason':last.code})
        raise last

    def structured(self,role,instructions,payload,schema,images=(),tools=None,max_output_tokens=None,timeout_seconds=None,reasoning_effort=None,require_tool=None,repair_invalid_tool_inputs=False,return_validated_tool_input=False,stop_on_invalid_tool_result=False,compact_tool_output=False):
        content = [{'type':'input_text','text':json.dumps(payload,allow_nan=False)}]+list(images)
        inputs = [{'role':'user','content':content}]
        functions = tools or {}
        definitions = [dict(type='function',name=name,description=entry['description'],
                            parameters=to_strict_json_schema(entry['schema']),strict=True)
                       for name,entry in functions.items()]
        format_spec = dict(type='json_schema',name=schema.__name__,schema=to_strict_json_schema(schema),strict=True)
        called=set()
        rounds=4 if repair_invalid_tool_inputs else 3
        for round_number in range(rounds):
            kwargs = dict(instructions=instructions,input=inputs,text={'format':format_spec},
                          max_output_tokens=max_output_tokens or self.config.ai_max_output_tokens)
            if compact_tool_output and require_tool and return_validated_tool_input:
                # Strict function parameters already contain the schema. Do not
                # repeat that large schema as a second, unused final text format.
                kwargs.pop('text')
            if timeout_seconds is not None: kwargs['timeout']=timeout_seconds
            effort=reasoning_effort or (self.config.reasoning_effort if self.model_for(role).startswith('gpt-6') else None)
            if effort: kwargs['reasoning']=dict(effort=effort)
            if definitions: kwargs.update(tools=definitions,parallel_tool_calls=False)
            if require_tool and (require_tool not in called or (compact_tool_output and return_validated_tool_input)):
                if require_tool not in functions: raise AIServiceError('AI_TOOL_NOT_ALLOWED','Required tool is not registered')
                kwargs['tool_choice']={'type':'function','name':require_tool}
            response = self._create(role,**kwargs)
            calls = [o for o in response.output if getattr(o,'type',None)=='function_call']
            if not calls:
                if require_tool and require_tool not in called:
                    raise AIServiceError('AI_TOOL_REQUIRED','The model did not execute the required CAD tool')
                try:
                    return schema.model_validate_json(response.output_text)
                except (ValidationError,ValueError) as exc:
                    if round_number < rounds - 1:
                        errors = ([dict(location=list(map(str,error['loc'])),
                                        message=error['msg'][:700],
                                        type=error['type'])
                                   for error in exc.errors(include_input=False,include_url=False)[:20]]
                                  if isinstance(exc,ValidationError)
                                  else [dict(message=str(exc)[:700])])
                        self.record('structured_output_rejected',
                                    dict(schema=schema.__name__,errors=errors))
                        inputs += [o.to_dict(mode='json') for o in response.output]
                        inputs.append({'role':'user','content':[{
                            'type':'input_text',
                            'text':json.dumps({
                                'valid':False,
                                'code':'AI_INVALID_STRUCTURED_OUTPUT',
                                'errors':errors,
                                'instruction':'Return the complete corrected JSON object. '
                                'Every geometry expression must use only parameter keys declared '
                                'in the response values list. Do not invent parameter names.'
                            })}]})
                        continue
                    raise AIServiceError('AI_INVALID_STRUCTURED_OUTPUT',
                                         'Model output did not satisfy the application schema') from exc
            if round_number==rounds-1 or len(calls)>4:
                raise AIServiceError('AI_TOOL_LIMIT','The bounded tool-call budget was exhausted')
            # SDK serialization keeps API aliases (async rather than async_) and
            # omits defaults absent from the response. Preserve all output items,
            # including opaque reasoning state and assistant message phases.
            inputs += [o.to_dict(mode='json') for o in response.output]
            for call in calls:
                entry = functions.get(call.name)
                if entry is None: raise AIServiceError('AI_TOOL_NOT_ALLOWED','The model requested an unregistered tool')
                try: arguments = entry['schema'].model_validate_json(call.arguments)
                except (ValidationError,ValueError) as exc:
                    if not repair_invalid_tool_inputs:
                        raise AIServiceError('AI_TOOL_INPUT_INVALID','Tool parameters did not satisfy the strict schema')
                    errors=([dict(location=list(map(str,error['loc'])),message=error['msg'][:700],type=error['type'])
                        for error in exc.errors(include_input=False,include_url=False)[:12]] if isinstance(exc,ValidationError)
                        else [dict(message=str(exc)[:700])])
                    self.record('tool_input_rejected',dict(name=call.name,errors=errors))
                    inputs.append(dict(type='function_call_output',call_id=call.call_id,output=json.dumps(dict(
                        valid=False,code='AI_TOOL_INPUT_INVALID',errors=errors,
                        instruction='Correct these schema/geometry errors and call the same registered tool again. Invalid inputs were not executed.'))))
                    continue
                self.record('tool_called',{'name':call.name,'arguments':arguments.model_dump(mode='json')})
                result = entry['handler'](arguments)
                called.add(call.name)
                if stop_on_invalid_tool_result and isinstance(result,dict) and result.get('valid') is False:
                    raise AIServiceError('AI_CAD_CANDIDATE_INVALID',str(result.get('message','CAD candidate needs correction'))[:1300],True)
                if return_validated_tool_input and call.name==require_tool and isinstance(result,dict) and result.get('valid') is True:
                    if entry['schema'] is not schema:raise AIServiceError('AI_TOOL_SCHEMA_MISMATCH','The terminal tool input must match the final schema')
                    self.record('tool_input_accepted',dict(name=call.name,plan_sha256=canonical_hash(arguments.model_dump(mode='json'))))
                    # The application has the exact server-validated plan. A
                    # second LLM transcription adds latency and can change it.
                    return schema.model_validate(arguments.model_dump(mode='json'))
                output=([{'type':'input_text','text':json.dumps(result.payload,allow_nan=False)},*result.images]
                    if isinstance(result,ToolResult) else json.dumps(result,allow_nan=False))
                inputs.append({'type':'function_call_output','call_id':call.call_id,'output':output})
        raise AIServiceError('AI_TOOL_LIMIT','The bounded tool-call budget was exhausted')

    def image(self,instructions,prompt,images,quality,size,output_format='png'):
        image_model = self.model_for('image')
        try: self.check_model(image_model)
        except (APITimeoutError,APIConnectionError,APIStatusError):
            raise AIServiceError('IMAGE_MODEL_UNAVAILABLE','Configured image model access could not be verified',True)
        response = self._create('render',instructions=instructions,
            input=[{'role':'user','content':[{'type':'input_text','text':prompt}]+list(images)}],
            tools=[{'type':'image_generation','model':image_model,'quality':quality,'size':size,
                    'output_format':output_format,'action':'edit' if images else 'generate'}],
            tool_choice={'type':'image_generation'},parallel_tool_calls=False,max_tool_calls=1,
            timeout=300,reasoning={'effort':self.config.reasoning_effort})
        images = [o for o in response.output if getattr(o,'type',None)=='image_generation_call' and getattr(o,'result',None)]
        if len(images)!=1: raise AIServiceError('IMAGE_GENERATION_FAILED','Expected one completed image-generation result')
        return images[0].result

    def image_api(self,prompt,images=(),quality='low',size='1024x1024',output_format='png'):
        """Direct Image API; one explicit, potentially billed attempt, with the same audit sink."""
        import hashlib
        model=self.model_for('image')
        invocation=self.record('invocation_started',dict(model=model,request_hash=canonical_hash(dict(
            prompt=prompt,images=[hashlib.sha256(b).hexdigest() for b in images],quality=quality,size=size,output_format=output_format))))
        try:
            self.check_model(model)
            arguments=dict(model=model,prompt=prompt,n=1,quality=quality,size=size,output_format=output_format,timeout=300)
            if images:
                response=self.sdk.images.edit(image=[(f'reference_{i}.png',b,'image/png') for i,b in enumerate(images)],**arguments)
            else: response=self.sdk.images.generate(**arguments)
            raw=response.model_dump(mode='json');usage=raw.get('usage') or {}
            self.record('invocation_completed',dict(invocation_id=invocation,model=model,response_id=None,
                usage=dict(input_tokens=usage.get('input_tokens',0),output_tokens=usage.get('output_tokens',0),
                    generated_images=len(response.data or []),image_usage=[usage],raw=usage)))
            if len(response.data or [])!=1 or not response.data[0].b64_json:
                raise AIServiceError('IMAGE_GENERATION_FAILED','The Image API returned no image data')
            return response.data[0].b64_json
        except (APITimeoutError,APIConnectionError,APIStatusError) as exc:
            provider=provider_diagnostics(exc) if isinstance(exc,APIStatusError) else {}
            code='AI_TIMEOUT' if isinstance(exc,APITimeoutError) else 'AI_API_ERROR'
            self.record('invocation_failed',dict(invocation_id=invocation,model=model,error_code=code,provider=provider))
            raise AIServiceError(code,'Image generation failed; inspect provider diagnostics before retrying',True,provider=provider) from exc
