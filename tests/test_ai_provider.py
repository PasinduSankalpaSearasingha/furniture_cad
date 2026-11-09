import json
import base64
from copy import deepcopy
import pytest
import httpx2
from openai import OpenAI, DefaultHttpxClient
from pydantic import ValidationError
from app.config import Settings
from app.ai.openai_client import ResponsesGateway, AIServiceError, calculate_cost,ToolResult
from app.ai.schemas import FurnitureVisionResult, QualityReviewResult, DesignProposal, JoineryLookup, CADGenerationRequest
from app.agents import design_agent, joinery_agent, review_agent
from examples.ai_demo.mock_gateway import fixtures, MockGateway, png_bytes


def response_body(payload=None,output=None,status='completed'):
    return dict(id='resp_test',object='response',created_at=1,status=status,model='configured-model',
        output=output if output is not None else [dict(id='msg_test',type='message',role='assistant',status='completed',
            content=[dict(type='output_text',text=json.dumps(payload),annotations=[])])],
        usage=dict(input_tokens=100,output_tokens=50,total_tokens=150,input_tokens_details={'cached_tokens':0}))


def gateway(handler,models=None,record=lambda event,data:None):
    client = OpenAI(api_key='TEST_NOT_A_REAL_KEY',base_url='http://openai.test/v1',max_retries=0,
        http_client=DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    return ResponsesGateway(Settings(),models or {'vision':'configured-model','joinery':'configured-model','render':'configured-model','image':'configured-image'},record,sdk=client)


def test_opt_in_tool_validation_feedback_never_executes_invalid_arguments():
    from pydantic import BaseModel,Field,ConfigDict
    class Arguments(BaseModel):
        model_config=ConfigDict(extra='forbid')
        count:int=Field(ge=1)
    requests=[];executed=[];events=[]
    def handle(request):
        if request.method=='GET':return httpx2.Response(200,json={'id':'configured-model'})
        body=json.loads(request.content);requests.append(body)
        if len(requests)<=2:
            n=len(requests);return httpx2.Response(200,json=response_body(output=[dict(
                type='function_call',id=f'fc_{n}',call_id=f'call_{n}',name='build',
                arguments=json.dumps(dict(count=0 if n==1 else 2)),status='completed')]))
        return httpx2.Response(200,json=response_body(dict(count=2)))
    g=gateway(handle,record=lambda event,data:events.append((event,data)))
    result=g.structured('vision','test',{},Arguments,tools={'build':dict(description='Validated tool',schema=Arguments,
        handler=lambda args:executed.append(args.count) or dict(valid=True))},require_tool='build',repair_invalid_tool_inputs=True)
    assert result.count==2 and executed==[2] and len(requests)==3
    error=next(item for item in requests[1]['input'] if item.get('type')=='function_call_output')
    assert json.loads(error['output'])['code']=='AI_TOOL_INPUT_INVALID'
    assert any(event=='tool_input_rejected' for event,data in events)


def test_terminal_cad_tool_returns_exact_validated_input_without_retranscription():
    from pydantic import BaseModel,ConfigDict
    class Candidate(BaseModel):
        model_config=ConfigDict(extra='forbid')
        dimension_mm:float
    requests=[]
    def handle(request):
        if request.method=='GET':return httpx2.Response(200,json={'id':'configured-model'})
        requests.append(json.loads(request.content))
        assert len(requests)==1,'Accepted CAD must not be regenerated as a final LLM response'
        return httpx2.Response(200,json=response_body(output=[dict(type='function_call',id='fc_valid',call_id='valid',
            name='cad_generation',arguments='{"dimension_mm":700}',status='completed')]))
    result=gateway(handle).structured('vision','test',{},Candidate,tools={'cad_generation':dict(description='Test CAD validator',
        schema=Candidate,handler=lambda args:dict(valid=True))},require_tool='cad_generation',return_validated_tool_input=True)
    assert result.dimension_mm==700 and len(requests)==1


def test_real_sdk_structured_vision_request_and_parsing():
    requests = []
    def handler(request):
        if request.method=='GET': return httpx2.Response(200,json={'id':'configured-model','object':'model','created':1,'owned_by':'test'})
        data = json.loads(request.content)
        requests.append(data)
        assert data['store'] is False
        assert data['text']['format']['strict'] is True
        assert data['text']['format']['schema']['additionalProperties'] is False
        assert data['input'][0]['content'][1]['type']=='input_image'
        return httpx2.Response(200,json=response_body(fixtures()['FurnitureVisionResult']))
    g = gateway(handler)
    result = g.structured('vision','fixture',{},FurnitureVisionResult,[{'type':'input_image','image_url':'data:image/png;base64,AA=='}])
    assert result.category=='display cabinet' and len(requests)==1


def test_sol_tool_can_return_actual_render_images_with_structured_measurements():
    sent=[];image='data:image/png;base64,'+base64.b64encode(png_bytes()).decode()
    def handler(request):
        if request.method=='GET':return httpx2.Response(200,json={'id':'gpt-6.1-sol'})
        payload=json.loads(request.content);sent.append(payload)
        assert payload['model']=='gpt-6.1-sol' and payload['reasoning']['effort']=='medium'
        if len(sent)==1:
            assert payload['tool_choice']=={'type':'function','name':'blender_build'}
            return httpx2.Response(200,json=response_body(output=[dict(type='function_call',id='fc_render',call_id='call_render',
                name='blender_build',arguments='{"materials":["solid_wood"]}',status='completed')]))
        evidence=next(i for i in payload['input'] if i.get('type')=='function_call_output')
        assert json.loads(evidence['output'][0]['text'])==dict(valid=True,sampled_gap_mm=.25)
        assert evidence['output'][1]==dict(type='input_image',image_url=image,detail='high')
        return httpx2.Response(200,json=response_body(fixtures()['QualityReviewResult']))
    g=gateway(handler,models={'design':'gpt-6.1-sol'})
    result=g.structured('design','Inspect the actual Blender render',{},QualityReviewResult,tools={'blender_build':dict(
        schema=JoineryLookup,description='Synthetic test of native model evidence transport',handler=lambda args:ToolResult(
            dict(valid=True,sampled_gap_mm=.25),[dict(type='input_image',image_url=image,detail='high')]))},require_tool='blender_build',reasoning_effort='medium')
    assert result.engineering_certified is False and len(sent)==2


def test_photo_budget_reasoning_and_timeout_use_supported_sdk_options():
    sent=[]
    def handler(request):
        if request.method=='GET': return httpx2.Response(200,json={'id':'configured-model'})
        body=json.loads(request.content)
        assert body['reasoning']=={'effort':'low'} and body['max_output_tokens']==12000
        assert 'timeout' not in body
        assert body['background'] is True
        assert request.extensions['timeout']['read']==30
        sent.append(body)
        return httpx2.Response(200,json=response_body(fixtures()['FurnitureVisionResult']))
    gateway(handler).structured('vision','fixture',{},FurnitureVisionResult,
        max_output_tokens=12000,timeout_seconds=300,reasoning_effort='low')
    assert len(sent)==1


@pytest.mark.parametrize('kind,code',[('invalid','AI_INVALID_STRUCTURED_OUTPUT'),('incomplete','AI_RESPONSE_INCOMPLETE'),('refusal','AI_REFUSAL')])
def test_invalid_incomplete_and_refusal_are_explicit(kind,code):
    def handler(request):
        if request.method=='GET': return httpx2.Response(200,json={'id':'configured-model'})
        body = response_body({'arbitrary':'not a vision result'})
        if kind=='incomplete': body['status']='incomplete'
        if kind=='refusal': body['output'][0]['content']=[{'type':'refusal','refusal':'test refusal'}]
        return httpx2.Response(200,json=body)
    with pytest.raises(AIServiceError) as error: gateway(handler).structured('vision','',{},FurnitureVisionResult)
    assert error.value.code==code


def test_rate_limit_uses_only_configured_account_checked_fallback():
    models = []
    def handler(request):
        if request.method=='GET':
            models.append(request.url.path.rsplit('/',1)[-1])
            return httpx2.Response(200,json={'id':models[-1]})
        payload = json.loads(request.content)
        if payload['model']=='primary': return httpx2.Response(429,json={'error':{'message':'rate limited','type':'rate_limit_error'}})
        return httpx2.Response(200,json=response_body(fixtures()['FurnitureVisionResult']))
    result = gateway(handler,{'vision':'primary','fallback':'secondary'}).structured('vision','',{},FurnitureVisionResult)
    assert models==['primary','secondary'] and result.category=='display cabinet'


def test_timeout_does_not_silently_repeat_possibly_billed_generation():
    count = []
    def handler(request):
        if request.method=='GET': return httpx2.Response(200,json={'id':'primary'})
        count.append(1)
        raise httpx2.ReadTimeout('synthetic',request=request)
    with pytest.raises(AIServiceError) as error:
        gateway(handler,{'vision':'primary','fallback':'secondary'}).structured('vision','',{},FurnitureVisionResult)
    assert error.value.code=='AI_TIMEOUT' and len(count)==1


def test_function_calls_are_allowlisted_validated_and_bounded():
    sent = []
    def handler(request):
        if request.method=='GET': return httpx2.Response(200,json={'id':'configured-model'})
        payload = json.loads(request.content)
        sent.append(payload)
        if len(sent)==1:
            return httpx2.Response(200,json=response_body(output=[dict(type='function_call',id='fc_test',call_id='call_test',
                name='lookup_approved_joinery',arguments='{"materials":["plywood"]}',status='completed')]))
        assert any(i.get('type')=='function_call_output' for i in payload['input'])
        return httpx2.Response(200,json=response_body(fixtures()['QualityReviewResult']))
    calls = []
    gateway(handler).structured('joinery','',{},QualityReviewResult,tools={'lookup_approved_joinery':dict(
        schema=JoineryLookup,description='test',handler=lambda arguments:calls.append(arguments.materials) or [])})
    assert calls==[['plywood']] and len(sent)==2
    assert sent[0]['tools'][0]['strict'] is True


def test_stateless_tool_followups_preserve_wire_fields_and_reasoning_across_rounds():
    sent,outputs = [],[]
    def handler(request):
        if request.method=='GET': return httpx2.Response(200,json={'id':'configured-model'})
        payload = json.loads(request.content)
        sent.append(payload)
        assert payload['store'] is False
        if len(sent)>1:
            expected = [sent[0]['input'][0]]
            for index,items in enumerate(outputs):
                expected.extend(items)
                expected.append(dict(type='function_call_output',call_id=f'call_{index}',output='[]'))
            # Reject extra SDK defaults and Python aliases such as async_.
            assert payload['input']==expected
        if len(sent)==3:
            return httpx2.Response(200,json=response_body(fixtures()['QualityReviewResult']))
        index = len(outputs)
        items = [dict(type='reasoning',id=f'rs_{index}',summary=[],encrypted_content=f'opaque-test-state-{index}'),
            dict(type='message',id=f'msg_{index}',role='assistant',status='completed',phase='commentary',
                 content=[dict(type='output_text',text='Looking up approved profiles.',annotations=[])]),
            dict(type='function_call',id=f'fc_{index}',call_id=f'call_{index}',name='lookup_approved_joinery',
                 arguments='{"materials":["plywood"]}',status='completed',**{'async':False,'caller':{'type':'direct'}})]
        outputs.append(items)
        return httpx2.Response(200,json=response_body(output=items))
    events = []
    gateway(handler,record=lambda name,data:events.append((name,data))).structured('joinery','',{},QualityReviewResult,
        tools={'lookup_approved_joinery':dict(schema=JoineryLookup,description='test',handler=lambda arguments:[])})
    assert len(sent)==3
    assert 'opaque-test-state' not in json.dumps(events)


@pytest.mark.parametrize('http_status,code,retryable',[
    (400,'AI_BAD_REQUEST',False),(422,'AI_UNPROCESSABLE_REQUEST',False),(500,'AI_API_ERROR',True)])
def test_provider_errors_keep_safe_diagnostics_without_storing_provider_message(http_status,code,retryable):
    sent,events = [],[]
    def handler(request):
        if request.method=='GET': return httpx2.Response(200,json={'id':'primary'})
        sent.append(json.loads(request.content))
        return httpx2.Response(http_status,headers={'x-request-id':'req_test_diagnostic'},json={'error':dict(
            type='invalid_request_error',code='unknown_parameter',param='input[2].async_',
            message='SENSITIVE_INPUT_FROM_PROVIDER: sk-test-secret and private furniture notes')})
    with pytest.raises(AIServiceError) as error:
        gateway(handler,{'joinery':'primary','fallback':'secondary'},record=lambda name,data:events.append((name,data))).structured(
            'joinery','',{},QualityReviewResult)
    assert error.value.code==code and error.value.retryable is retryable
    assert error.value.provider==dict(http_status=http_status,type='invalid_request_error',code='unknown_parameter',
        param='input[2].async_',request_id='req_test_diagnostic')
    assert events[-1][1]['provider']==error.value.provider
    assert len(sent)==1
    assert str(http_status) in error.value.message
    assert 'SENSITIVE_INPUT' not in json.dumps(events)+str(error.value)
    assert 'sk-test-secret' not in json.dumps(error.value.as_dict())


@pytest.mark.parametrize('name,arguments,code',[('execute_python','{}','AI_TOOL_NOT_ALLOWED'),
    ('lookup_approved_joinery','{"materials":["plywood"],"shell":"bad"}','AI_TOOL_INPUT_INVALID')])
def test_tool_injection_is_rejected(name,arguments,code):
    def handler(request):
        if request.method=='GET': return httpx2.Response(200,json={'id':'configured-model'})
        return httpx2.Response(200,json=response_body(output=[dict(type='function_call',id='fc',call_id='call',name=name,arguments=arguments,status='completed')]))
    with pytest.raises(AIServiceError) as error:
        gateway(handler).structured('joinery','',{},QualityReviewResult,tools={'lookup_approved_joinery':dict(
            schema=JoineryLookup,description='test',handler=lambda args:pytest.fail('Must not execute invalid tool'))})
    assert error.value.code==code


def test_image_generation_uses_responses_tool_and_propagates_lineage_inputs():
    def handler(request):
        if request.method=='GET': return httpx2.Response(200,json={'id':request.url.path.rsplit('/',1)[-1]})
        data = json.loads(request.content)
        assert data['tools'][0]['type']=='image_generation'
        assert data['tools'][0]['model']=='configured-image' and data['tools'][0]['action']=='edit'
        return httpx2.Response(200,json=response_body(output=[dict(type='image_generation_call',id='ig_test',status='completed',
            result=base64.b64encode(png_bytes()).decode())]))
    encoded = gateway(handler).image('','studio cabinet',[{'type':'input_image','image_url':'data:image/png;base64,AA=='}],'medium','1024x1024')
    assert base64.b64decode(encoded).startswith(b'\x89PNG')


def test_provenance_schema_rejects_claimed_verification_and_certification():
    value = fixtures()['FurnitureVisionResult']
    value['components'][0]['provenance']='DESIGNER_VERIFIED'
    with pytest.raises(ValidationError): FurnitureVisionResult.model_validate(value)
    review = fixtures()['QualityReviewResult']
    review['engineering_certified']=True
    with pytest.raises(ValidationError): QualityReviewResult.model_validate(review)
    with pytest.raises(ValidationError): CADGenerationRequest(revision_id='bad',specification_hash='bad',template_id='exec')


def test_missing_measurements_unsupported_features_and_critical_findings_cannot_disappear():
    MockGateway.responses = None
    MockGateway.fail_role = None
    g = MockGateway(None,{})
    vision = FurnitureVisionResult.model_validate(fixtures()['FurnitureVisionResult'])
    result = design_agent.propose(g,vision,'')
    assert {'width_mm','depth_mm','height_mm','glass_thickness_mm'} <= {m.field for m in result.required_measurements}
    vision.components[0].kind='shelf'
    unsupported = design_agent.propose(g,vision,'')
    assert unsupported.status=='CUSTOM_DESIGN_REVIEW_REQUIRED'
    quality = review_agent.review(g,{'deterministic_validation':{'findings':[
        {'code':'GLASS_REQUIRED','severity':'BLOCKER','message':'Real glass requirement','part_ids':['G001']}]}})
    assert quality.issues[0].code=='DETERMINISTIC_GLASS_REQUIRED' and quality.issues[0].severity=='BLOCKER'


def test_unknown_prices_are_null_and_configured_rates_are_explicit():
    usage = dict(input_tokens=100,output_tokens=50,cached_input_tokens=0,generated_images=1)
    assert calculate_cost(usage,None)['total'] is None
    rate = dict(input_per_million=2,output_per_million=8,currency='USD',image_price=1,
                image_model='test-image',image_quality='medium',image_size='1024x1024')
    assert calculate_cost(usage,rate)['total'] is None
    assert calculate_cost(usage,rate,dict(model='test-image',quality='medium',size='1024x1024'))['total']=='1.0006'


def test_joinery_references_must_resolve_to_approved_compatible_factory_profiles():
    design = DesignProposal.model_validate(fixtures()['DesignProposal'])
    class RecommendationGateway:
        def structured(self,*args,**kwargs):
            result = fixtures()['JoineryRecommendation']
            result['joints'][0].update(profile_id='TEST-APPROVED',status='FACTORY_APPROVED_REFERENCE')
            return args[3].model_validate(result)
    profile = dict(profile_id='TEST-APPROVED',joint_type='dowel',compatible_materials=['plywood'],
        approved=True,evidence_reference='SYNTHETIC TEST ONLY',diameter_mm=6,depths_mm=[8,12],clearance_mm=0,tolerance_mm=0.1)
    good = joinery_agent.recommend(RecommendationGateway(),design,[profile])
    assert good.joints[0].profile_id=='TEST-APPROVED'
    for bad_catalog in ([],[{**profile,'approved':False}],[{**profile,'compatible_materials':['glass']}]):
        bad = joinery_agent.recommend(RecommendationGateway(),design,bad_catalog)
        assert bad.joints[0].status=='ENGINEERING_REVIEW_REQUIRED' and bad.joints[0].profile_id is None


def test_repeated_function_calls_hit_a_fixed_limit():
    calls = []
    def handler(request):
        if request.method=='GET': return httpx2.Response(200,json={'id':'configured-model'})
        return httpx2.Response(200,json=response_body(output=[dict(type='function_call',id='fc',call_id='call',
            name='lookup_approved_joinery',arguments='{"materials":["plywood"]}',status='completed')]))
    with pytest.raises(AIServiceError) as error:
        gateway(handler).structured('joinery','',{},QualityReviewResult,tools={'lookup_approved_joinery':dict(
            schema=JoineryLookup,description='test',handler=lambda args:calls.append(1) or [])})
    assert error.value.code=='AI_TOOL_LIMIT' and len(calls)==2


def test_cross_revision_package_inputs_are_rejected_before_writing(tmp_path):
    from app.pipeline import build_package,canonical_hash
    from app.agents.cad_agent import VerifiedCADContext
    from .test_cabinet import spec
    verified = spec()
    context = VerifiedCADContext('test-revision',canonical_hash(verified.model_dump(mode='json')),verified)
    output = tmp_path/'mismatched-package'
    with pytest.raises(ValueError,match='differs from verified CAD context'):
        build_package(spec(width_mm=900),output,verified_context=context)
    assert not output.exists()
