"""Budget enforcement over the real SDK with synthetic HTTP; no paid calls."""
import json
from copy import deepcopy
from decimal import Decimal
import httpx2
import pytest
from openai import OpenAI,DefaultHttpxClient
from sqlalchemy import select
from app.ai.openai_client import ResponsesGateway,AIServiceError,calculate_cost
from app.ai.furniture_budget import FurnitureBudget,policy,summary,RATES
from app.ai.response_store import WorkflowResponseStore
from app.agents.orchestrator import WorkflowRunner
from app.database import AgentWorkflow,AIInvocation
from app.pipeline import canonical_hash
from app.engineering_repairs import EngineeringRepairPatch,merge_repair
from app.budget_drafts import retain_exportable_draft,available_budget_draft
from app.ai.schemas import FurnitureVisionResult
from examples.ai_demo.mock_gateway import fixtures
from tests.test_ai_provider import response_body
from tests.test_agent_workflows import agents_app
from tests.test_engineering_cad import engineering_app,EngineeringGateway,launch,table_plan


def usage(i=1000,o=100,cached=300,written=500):
    return dict(input_tokens=i,output_tokens=o,cached_input_tokens=cached,generated_images=0,
        raw=dict(input_tokens=i,output_tokens=o,total_tokens=i+o,
            output_tokens_details=dict(reasoning_tokens=0),
            input_tokens_details=dict(cached_tokens=cached,cache_write_tokens=written)))


def runner(env,limit='2.00',analysis=None):
    _,app,headers,project,_=env
    app.state.settings.furniture_budget_usd=limit
    raw=dict(ai_budget=policy(app.state.settings),**({'analysis_workflow_id':analysis} if analysis else {}))
    with app.state.db.sessions.begin() as session:
        row=AgentWorkflow(project_id=project,mode='IMAGE_TO_CAD',state='RUNNING',input_data=raw,
            input_hash=canonical_hash(raw),model_config={'design':'gpt-6-astra'},lease_token='cost-test',created_by='cost-test')
        # Use a real user to retain database FK behavior.
        from app.database import Project
        row.created_by=session.get(Project,project).owner_id
        session.add(row);session.flush();wid=row.id
    r=WorkflowRunner(wid,app.state.db,app.state.settings);r.lease='cost-test';r.inputs=raw;r.project_id=project;r.stage='test_cost'
    return r


def gateway(r,handler):
    sdk=OpenAI(api_key='SYNTHETIC_NOT_A_KEY',base_url='http://openai.test/v1',max_retries=0,
        http_client=DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    g=ResponsesGateway(r.config,{'design':'gpt-6-astra'},r.record,sdk=sdk,response_store=WorkflowResponseStore(r))
    g.request_budget=FurnitureBudget(r)
    return g


def test_cache_reads_writes_and_reasoning_are_not_double_counted():
    u=usage();u['raw']['output_tokens_details']={'reasoning_tokens':50}
    # 200 ordinary + 300 cached + 500 written + 100 output.
    cost=calculate_cost(u,RATES['gpt-6-astra'])
    assert Decimal(cost['total'])==Decimal('.01355')
    u['raw']['input_tokens_details']['cache_write_tokens_details']={'ttl_30m_tokens':300,'ttl_12h_tokens':200}
    assert Decimal(calculate_cost(u,RATES['gpt-6-astra'])['total'])==Decimal('.01605')
    assert calculate_cost(u,dict(RATES['gpt-6-astra'],cache_write_per_million=None))['total'] is None


def test_budget_blocks_generation_before_provider_post(agents_app):
    r=runner(agents_app,limit='.01');posts=[]
    def handler(request):
        posts.append(request.url.path)
        assert request.url.path.endswith('/responses/input_tokens')
        return httpx2.Response(200,json={'input_tokens':1000,'object':'response.input_tokens'})
    with pytest.raises(AIServiceError,match='budget cannot cover') as caught:
        gateway(r,handler).structured('design','Synthetic test',{},FurnitureVisionResult)
    assert caught.value.code=='AI_BUDGET_REACHED'
    assert posts==['/v1/responses/input_tokens']
    with r.db.sessions() as session:assert not session.scalars(select(AIInvocation).where(AIInvocation.workflow_id==r.id)).all()


def test_rejected_key_during_token_count_keeps_the_authentication_error(agents_app):
    r=runner(agents_app);requests=[]
    def handler(request):
        requests.append(request.url.path)
        return httpx2.Response(401,json={'error':dict(type='invalid_request_error',code='invalid_api_key',message='Synthetic invalid key')})
    with pytest.raises(AIServiceError) as caught:gateway(r,handler).structured('design','test',{},FurnitureVisionResult)
    assert caught.value.code=='AI_AUTHENTICATION_FAILED' and caught.value.provider['http_status']==401
    assert requests==['/v1/responses/input_tokens']


def test_count_uses_images_schemas_and_replay_makes_no_new_request(agents_app):
    r=runner(agents_app);posts=[];counts=[]
    def handler(request):
        if request.method=='GET':return httpx2.Response(200,json={'id':'gpt-6-astra'})
        data=json.loads(request.content)
        if request.url.path.endswith('/input_tokens'):
            counts.append(data);return httpx2.Response(200,json={'input_tokens':1000,'object':'response.input_tokens'})
        posts.append(data);body=response_body(fixtures()['FurnitureVisionResult']);body['usage']=usage()['raw']
        body.update(parallel_tool_calls=False,tools=[],tool_choice='auto')
        return httpx2.Response(200,json=body)
    g=gateway(r,handler);image={'type':'input_image','image_url':'data:image/png;base64,AA==','detail':'high'}
    first=g.structured('design','Synthetic test',{'notes':'same request'},FurnitureVisionResult,[image])
    again=g.structured('design','Synthetic test',{'notes':'same request'},FurnitureVisionResult,[image])
    assert first==again and len(posts)==len(counts)==1
    assert counts[0]['input']==posts[0]['input'] and counts[0]['text']==posts[0]['text']
    assert posts[0]['service_tier']=='default' and posts[0]['prompt_cache_options']=={'ttl':'30m'}
    with r.db.sessions() as session:
        state=summary(session,session.get(AgentWorkflow,r.id))
        assert state['calls']==1 and Decimal(state['spent_usd'])==Decimal('.01355') and not state['uncertain']


def test_uncertain_submission_keeps_reservation_across_runner_restart(agents_app):
    r=runner(agents_app);submitted=[]
    def handler(request):
        if request.method=='GET':return httpx2.Response(200,json={'id':'gpt-6-astra'})
        if request.url.path.endswith('/input_tokens'):return httpx2.Response(200,json={'input_tokens':1000})
        submitted.append(True);raise httpx2.ReadTimeout('Synthetic lost response',request=request)
    with pytest.raises(AIServiceError) as caught:gateway(r,handler).structured('design','Synthetic first',{},FurnitureVisionResult)
    assert caught.value.code=='AI_TIMEOUT'
    fresh=WorkflowRunner(r.id,r.db,r.config);fresh.lease=r.lease;fresh.inputs=r.inputs;fresh.stage='another_stage'
    with pytest.raises(AIServiceError) as caught:gateway(fresh,handler).structured('design','Different request',{},FurnitureVisionResult)
    assert caught.value.code=='AI_BUDGET_UNCERTAIN' and len(submitted)==1
    with r.db.sessions() as session:
        state=summary(session,session.get(AgentWorkflow,r.id))
        assert state['uncertain'] and Decimal(state['reserved_usd'])>0


def test_missing_cache_usage_blocks_new_paid_calls(agents_app):
    r=runner(agents_app);posts=[]
    def handler(request):
        if request.method=='GET':return httpx2.Response(200,json={'id':'gpt-6-astra'})
        if request.url.path.endswith('/input_tokens'):return httpx2.Response(200,json={'input_tokens':1000})
        posts.append(True);return httpx2.Response(200,json=response_body(fixtures()['FurnitureVisionResult']))
    g=gateway(r,handler);g.structured('design','first',{},FurnitureVisionResult)
    with pytest.raises(AIServiceError) as caught:g.structured('design','second',{},FurnitureVisionResult)
    assert caught.value.code=='AI_BUDGET_UNCERTAIN' and len(posts)==1


def test_analysis_cost_is_included_and_foreign_analysis_rejected(agents_app):
    r=runner(agents_app,limit='.01')
    with r.db.sessions.begin() as session:
        analysis=AgentWorkflow(project_id=r.project_id,created_by=session.get(AgentWorkflow,r.id).created_by,
            mode='PHOTO_ANALYZE',state='SUCCEEDED',input_data={},input_hash=canonical_hash({}),model_config={})
        session.add(analysis);session.flush();aid=analysis.id
        session.add(AIInvocation(workflow_id=aid,stage='photo_analysis',model='gpt-6-astra',request_hash='fixture',status='COMPLETED',usage=usage()))
        row=session.get(AgentWorkflow,r.id);row.input_data={**row.input_data,'analysis_workflow_id':aid}
    with r.db.sessions() as session:
        state=summary(session,session.get(AgentWorkflow,r.id))
        assert state['includes_linked_analysis'] and Decimal(state['spent_usd'])>Decimal('.01')
    def forbidden(request):raise AssertionError('An exhausted budget must not contact the provider')
    with pytest.raises(AIServiceError) as caught:gateway(r,forbidden).structured('design','test',{},FurnitureVisionResult)
    assert caught.value.code=='AI_BUDGET_REACHED'


def test_repair_preserves_omitted_parts_values_and_supplied_dimensions():
    source=table_plan(720);before=source.model_dump(mode='json');part=deepcopy(source.parts[1]);part.edge_radius=3
    patch=EngineeringRepairPatch(parts=[part],summary='Round only the selected leg')
    merged=merge_repair(before,patch,[{'key':'width_mm','value':720}]);after=merged.model_dump(mode='json')
    assert before==source.model_dump(mode='json') and after['values']==before['values']
    assert after['parts'][0]==before['parts'][0] and after['parts'][2:]==before['parts'][2:]
    bad=EngineeringRepairPatch(values=[dict(key='width_mm',value=800,explanation='Rejected change')],summary='Invalid supplied dimension change')
    with pytest.raises(ValueError,match='supplied dimension'):merge_repair(before,bad,[{'key':'width_mm','value':720}])
    with pytest.raises(ValueError,match='did not change'):merge_repair(before,EngineeringRepairPatch(summary='Empty patch'),[])


def test_real_sdk_repairs_with_one_small_patch_and_exports(engineering_app,monkeypatch):
    from app.agents import orchestrator
    client,app,headers,project,image=engineering_app
    app.state.settings.ai_models.update(vision='gpt-6.1-sol',design='gpt-6-astra',review='gpt-6-astra',fallback='')
    app.state.settings.openai_background_mode=False
    sent=[];counts=[];source=table_plan(720);bad=source.model_dump(mode='json');bad['parts'][1]['position'][0]='5000'
    def handler(request):
        if request.method=='GET':return httpx2.Response(200,json={'id':request.url.path.rsplit('/',1)[-1]})
        body=json.loads(request.content)
        if request.url.path.endswith('/input_tokens'):
            counts.append(body);return httpx2.Response(200,json={'input_tokens':2000})
        sent.append(body);name=(body.get('text',{}).get('format') or {}).get('name')
        if name=='EngineeringDimensions':
            payload=dict(values=[v.model_dump(mode='json') for v in source.values],assumptions=source.assumptions,
                appearance=dict(summary='Synthetic table',critical_features=[dict(feature_id='V001',label='Top and legs',
                    visible_shape='Rectangular top and four legs',relative_layout='Legs under top')],continuity=[],symmetry='Four corner legs',avoid_substitutions=[]))
            answer=response_body(payload)
        elif name=='EngineeringReview':
            answer=response_body(dict(accepted=True,findings=[],limitations=['Synthetic comparison only'],
                feature_checks=[dict(feature_id='V001',status='MATCH',component_ids=[],evidence='Synthetic match',correction='')]))
        else:
            assert 'text' not in body,'Terminal CAD schema must not be repeated as a text schema'
            schema=body['tools'][0]['parameters']
            if 'remove_part_ids' in schema['properties']:
                arguments=EngineeringRepairPatch(parts=[source.parts[1]],summary='Restore the misplaced leg').model_dump(mode='json')
            else:arguments=bad
            answer=response_body(output=[dict(type='function_call',id=f'fc_{len(sent)}',call_id=f'call_{len(sent)}',
                name='cad_generation',arguments=json.dumps(arguments),status='completed')])
        answer.update(id=f'resp_{len(sent)}',parallel_tool_calls=False,tools=body.get('tools',[]),tool_choice=body.get('tool_choice','auto'))
        answer['usage']=usage()['raw']
        return httpx2.Response(200,json=answer)
    def real(config,models,record):
        sdk=OpenAI(api_key='SYNTHETIC_NOT_A_KEY',base_url='http://openai.test/v1',max_retries=0,
            http_client=DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
        return ResponsesGateway(config,models,record,sdk=sdk)
    monkeypatch.setattr(orchestrator,'ResponsesGateway',real)
    state=launch(engineering_app,output_scope='WHOLE_FURNITURE')
    assert state['status']=='REVIEW_REQUIRED',state.get('error')
    assert len(sent)==len(counts)==4  # dimensions, rejected plan, small repair, actual visual review
    tools=[b for b in sent if b.get('tools')]
    assert state['result']['engineering_cad']['cad_export_agreement']['valid_closed_solids']
    assert state['ai_budget']['calls']==4 and Decimal(state['ai_budget']['spent_usd'])<Decimal('2')
    assert len(tools)==2 and tools[1]['tools'][0]['parameters']['properties'].get('remove_part_ids')
    with app.state.db.sessions() as session:
        row=session.get(AgentWorkflow,state['id']);plan=row.checkpoints['engineering_plan']['payload']
        assert plan['parts']==source.model_dump(mode='json')['parts']


@pytest.mark.parametrize('stop_stage',['EngineeringReview','EngineeringRepairPatch'])
def test_budget_stop_exports_valid_draft_without_repeating_ai(engineering_app,monkeypatch,stop_stage):
    original=EngineeringGateway.structured;seen=[]
    EngineeringGateway.reject_visual=True
    def limited(self,role,instructions,payload,schema,**options):
        seen.append(schema.__name__)
        if schema.__name__==stop_stage:raise AIServiceError('AI_BUDGET_REACHED','Synthetic $2.00 budget reached')
        return original(self,role,instructions,payload,schema,**options)
    monkeypatch.setattr(EngineeringGateway,'structured',limited)
    state=launch(engineering_app,output_scope='WHOLE_FURNITURE')
    assert state['status']=='REVIEW_REQUIRED',state.get('error')
    assert seen.count(stop_stage)==1
    result=state['result'];assert result['engineering_cad']['cad_export_agreement']['valid_closed_solids']
    assert result['engineering_cad']['workflow']['cost_control_stop']['code']=='AI_BUDGET_REACHED'
    assert result['engineering_cad']['photo_match']['accepted'] is False
    assert result['delivery']['status']=='PHOTO_MATCH_NEEDS_REVISION'
    assert result['engineering_cad']['status']=='BUDGET_LIMIT_DRAFT'
    assert result['engineering_cad']['workflow']['budget_draft']['remaining_issues']==[]
    assert_budget_files(engineering_app,state)


def assert_budget_files(env,state):
    import ezdxf
    _,app,_,_,_=env
    directory=app.state.settings.storage/state['job_id']
    expected={'cad/cabinet.step','cad/cabinet.glb','cad/cabinet.stl',
        'cad/all_dimensions_REVIEW.dxf','cad/all_dimensions_REVIEW.pdf','cad/all_dimensions_REVIEW.svg'}
    assert expected<={item['path'] for item in state['result']['delivery']['items']}
    drawing=ezdxf.readfile(directory/'all_dimensions_REVIEW.dxf')
    assert any('STATUS: BUDGET DRAFT' in entity.dxf.text for entity in drawing.modelspace().query('TEXT'))
    assert not state['result']['approved_for_manufacturing']


def test_budget_stop_after_tool_acceptance_still_exports_without_another_ai_call(engineering_app,monkeypatch):
    original=EngineeringGateway.structured;seen=[]
    def limited(self,role,instructions,payload,schema,**options):
        seen.append(schema.__name__)
        if schema.__name__=='PhotoDesign':
            checked=options['tools']['cad_generation']['handler'](table_plan(720))
            assert checked['valid']
            raise AIServiceError('AI_BUDGET_REACHED','Synthetic budget stopped after building a candidate')
        return original(self,role,instructions,payload,schema,**options)
    monkeypatch.setattr(EngineeringGateway,'structured',limited)
    state=launch(engineering_app,output_scope='WHOLE_FURNITURE')
    assert state['status']=='REVIEW_REQUIRED',state.get('error')
    assert seen==['EngineeringDimensions','PhotoDesign']
    draft=state['result']['engineering_cad']['workflow']['budget_draft']
    assert draft['fully_connected'] and not draft['remaining_issues']
    assert draft['source_checkpoint'].startswith('engineering_exportable_draft_')
    assert state['result']['engineering_cad']['visual_review']['visual']['accepted'] is False
    assert_budget_files(engineering_app,state)


def test_budget_stop_in_legacy_contract_review_retains_files_and_skips_further_ai(engineering_app,monkeypatch):
    original=EngineeringGateway.structured;seen=[]
    def limited(self,role,instructions,payload,schema,**options):
        seen.append(schema.__name__)
        if schema.__name__=='EngineeringReview':
            raise AIServiceError('AI_BUDGET_REACHED','Synthetic contract review cannot fit in the budget')
        return original(self,role,instructions,payload,schema,**options)
    monkeypatch.setattr(EngineeringGateway,'structured',limited)
    state=launch(engineering_app,output_scope='PARTS')
    assert state['status']=='REVIEW_REQUIRED',state.get('error')
    assert seen==['EngineeringDimensions','EngineeringAssembly','PhotoDesign','EngineeringReview']
    assert state['result']['delivery']['items']
    assert state['result']['engineering_cad']['status']=='BUDGET_LIMIT_DRAFT'
    assert not state['result']['engineering_cad']['visual_review']['visual']['accepted']


@pytest.mark.parametrize('invalid_solid',[False,True])
def test_budget_stop_during_repair_keeps_all_available_solids_and_reports_gaps(engineering_app,monkeypatch,invalid_solid):
    original=EngineeringGateway.structured;seen=[];source=table_plan(720)
    for part in source.parts[1:]:
        part.size[2]='height_mm-top_thickness_mm-20'
        part.position[2]='(height_mm-top_thickness_mm-20)/2'
    if invalid_solid:source.parts[1].corner_radius=10000
    def limited(self,role,instructions,payload,schema,**options):
        seen.append(schema.__name__)
        if schema.__name__=='PhotoDesign':
            checked=options['tools']['cad_generation']['handler'](source)
            assert checked['valid'] is False
            raise AIServiceError('AI_CAD_CANDIDATE_INVALID',checked['message'],True)
        if schema.__name__=='EngineeringRepairPatch':
            raise AIServiceError('AI_BUDGET_REACHED','Synthetic $2 budget cannot cover another repair')
        return original(self,role,instructions,payload,schema,**options)
    monkeypatch.setattr(EngineeringGateway,'structured',limited)
    state=launch(engineering_app,output_scope='WHOLE_FURNITURE')
    assert seen==['EngineeringDimensions','PhotoDesign','EngineeringRepairPatch']
    if invalid_solid:
        assert state['status']=='BUDGET_REACHED' and state['error'] is None
        assert state['result']['available_output']['has_cad'] is False
        assert not (state.get('result') or {}).get('delivery')
        return
    assert state['status']=='REVIEW_REQUIRED',state.get('error')
    result=state['result'];engineering=result['engineering_cad'];draft=engineering['workflow']['budget_draft']
    assert draft['available_solid_count']==5 and not draft['fully_connected']
    assert draft['remaining_issues'][0]['code']=='ASSEMBLY_CONNECTIONS_INCOMPLETE'
    assert not engineering['visual_review']['critic']['accepted'] and not engineering['visual_review']['visual']['accepted']
    assert engineering['cad_export_agreement']['valid_closed_solids']
    assert not engineering['cad_export_agreement']['geometry_checks']['furniture_items_connected']
    assert any(f['code']=='ASSEMBLY_CONNECTIONS_INCOMPLETE' and f['severity']=='BLOCKER'
        for f in result['deterministic_validation']['findings'])
    import cadquery as cq
    _,app,_,_,_=engineering_app
    solid=cq.importers.importStep(str(app.state.settings.storage/state['job_id']/'cabinet.step')).val()
    assert solid.isValid() and len(solid.Solids())==5
    with app.state.db.sessions() as session:
        saved=session.get(AgentWorkflow,state['id']).checkpoints['engineering_plan']['payload']
    assert saved['parts']==source.model_dump(mode='json')['parts']
    assert_budget_files(engineering_app,state)


def test_saved_draft_selection_prefers_connected_geometry_and_verifies_integrity(agents_app):
    r=runner(agents_app)
    connected=table_plan(720);partial=table_plan(720);partial.parts[1].position[2]=20
    checks=dict(valid_closed_solids=True,part_count=5,assembly_connected=True,furniture_items_connected=True,contact_groups=[['F001','F002','F003','F004','F005']])
    retain_exportable_draft(r,connected,checks)
    retain_exportable_draft(r,partial,{**checks,'assembly_connected':False,'furniture_items_connected':False,
        'contact_groups':[['F001','F003','F004','F005'],['F002']]})
    fresh=WorkflowRunner(r.id,r.db,r.config);fresh.lease=r.lease;fresh.inputs=r.inputs
    plan,draft=available_budget_draft(fresh)
    assert plan==connected and draft['fully_connected']
    with r.db.sessions.begin() as session:
        row=session.get(AgentWorkflow,r.id);snapshots=deepcopy(row.checkpoints)
        snapshots[draft['source_checkpoint']]['payload']['plan']['summary']='Synthetic tampering'
        row.checkpoints=snapshots
    with pytest.raises(AIServiceError) as caught:available_budget_draft(fresh)
    assert caught.value.code=='CHECKPOINT_INTEGRITY_FAILED'


def test_budget_stop_before_any_valid_geometry_has_clear_state(engineering_app,monkeypatch):
    original=EngineeringGateway.structured
    def limited(self,role,instructions,payload,schema,**options):
        if schema.__name__=='PhotoDesign':raise AIServiceError('AI_BUDGET_REACHED','Synthetic budget cannot cover CAD planning')
        return original(self,role,instructions,payload,schema,**options)
    monkeypatch.setattr(EngineeringGateway,'structured',limited)
    state=launch(engineering_app,output_scope='WHOLE_FURNITURE')
    assert state['status']=='BUDGET_REACHED' and state['error'] is None
    available=state['result']['available_output']
    assert available['stop_reason']['code']=='AI_BUDGET_REACHED'
    assert available['dimensions'] and available['references'] and not available['has_cad']
    assert not (state.get('result') or {}).get('delivery')
    client,app,headers,_,_=engineering_app
    assert client.post(f'/api/v1/agents/workflows/{state["id"]}/retry',headers=headers['operator']).status_code==409


@pytest.mark.parametrize('code',['insufficient_quota','credit_balance_exhausted','organization_spend_limit_exceeded',
    'project_spend_limit_exceeded','organization_usage_limit_exceeded'])
def test_provider_credit_limits_never_try_another_model(code):
    from tests.test_ai_provider import gateway as provider_gateway
    submitted=[]
    def handler(request):
        if request.method=='GET':return httpx2.Response(200,json={'id':'configured-model'})
        submitted.append(json.loads(request.content)['model'])
        return httpx2.Response(429,json={'error':dict(code=code,type='insufficient_quota',message='Synthetic limit')})
    client=provider_gateway(handler,models={'vision':'configured-model','fallback':'unwanted-fallback'})
    with pytest.raises(AIServiceError) as caught:client.structured('vision','test',{},FurnitureVisionResult)
    assert caught.value.code=='AI_CREDIT_LIMIT' and not caught.value.retryable
    assert submitted==['configured-model']


def test_credit_limit_on_token_count_stops_before_generation(agents_app):
    r=runner(agents_app);requests=[]
    def handler(request):
        requests.append(request.url.path)
        return httpx2.Response(429,json={'error':dict(code='credit_balance_exhausted',type='insufficient_quota',message='Synthetic limit')})
    with pytest.raises(AIServiceError) as caught:gateway(r,handler).structured('design','test',{},FurnitureVisionResult)
    assert caught.value.code=='AI_CREDIT_LIMIT'
    assert requests==['/v1/responses/input_tokens']


def test_repair_limit_exports_available_disconnected_draft_without_final_ai(engineering_app,monkeypatch):
    original=EngineeringGateway.structured;seen=[];plan=table_plan(720)
    for part in plan.parts[1:]:
        part.size[2]='height_mm-top_thickness_mm-20'
        part.position[2]='(height_mm-top_thickness_mm-20)/2'
    def limited(self,role,instructions,payload,schema,**options):
        seen.append(schema.__name__)
        if schema.__name__ in ('PhotoDesign','EngineeringRepairPatch'):
            candidate=plan if schema.__name__=='PhotoDesign' else EngineeringRepairPatch(parts=[plan.parts[1]],summary='Synthetic attempted repair')
            if schema.__name__=='PhotoDesign':assert not options['tools']['cad_generation']['handler'](candidate)['valid']
            raise AIServiceError('AI_TOOL_LIMIT','Synthetic repair attempts exhausted')
        return original(self,role,instructions,payload,schema,**options)
    monkeypatch.setattr(EngineeringGateway,'structured',limited)
    state=launch(engineering_app,output_scope='WHOLE_FURNITURE',final_ai_correction=True)
    assert state['status']=='REVIEW_REQUIRED' and state['error'] is None
    assert seen==['EngineeringDimensions','PhotoDesign','EngineeringRepairPatch']
    draft=state['result']['engineering_cad']['workflow']['budget_draft']
    assert draft['status']=='REPAIR_LIMIT_DRAFT' and draft['available_solid_count']==5
    assert not draft['fully_connected'] and draft['remaining_issues']
    assert state['result']['delivery']['items']
    assert state['result']['engineering_cad']['cad_export_agreement']['valid_closed_solids']


@pytest.mark.parametrize('stage',['EngineeringRepairPatch','EngineeringReview','FinalCADReview'])
def test_credit_limit_keeps_cad_downloads_and_explicit_review_status(engineering_app,monkeypatch,stage):
    original=EngineeringGateway.structured;seen=[]
    EngineeringGateway.reject_visual=stage=='EngineeringRepairPatch'
    def limited(self,role,instructions,payload,schema,**options):
        seen.append(schema.__name__)
        if schema.__name__==stage:raise AIServiceError('AI_CREDIT_LIMIT','Synthetic credits exhausted')
        return original(self,role,instructions,payload,schema,**options)
    monkeypatch.setattr(EngineeringGateway,'structured',limited)
    state=launch(engineering_app,output_scope='WHOLE_FURNITURE',final_ai_correction=stage=='FinalCADReview')
    assert state['status']=='REVIEW_REQUIRED' and state['error'] is None
    assert seen.count(stage)==1
    result=state['result'];assert result['delivery']['items']
    assert result['engineering_cad']['status']=='BUDGET_LIMIT_DRAFT'
    assert result['engineering_cad']['workflow']['cost_control_stop']['code']=='AI_CREDIT_LIMIT'
    assert not result['engineering_cad']['visual_review']['visual']['accepted']
    assert_budget_files(engineering_app,state)


def test_credit_limit_without_new_cad_keeps_previous_model_and_downloadable_report(engineering_app,monkeypatch):
    source=launch(engineering_app,key='saved-source',output_scope='WHOLE_FURNITURE')
    original=EngineeringGateway.structured;calls=[]
    def limited(self,role,instructions,payload,schema,**options):
        calls.append(schema.__name__)
        if schema.__name__=='PhotoDesign':raise AIServiceError('AI_CREDIT_LIMIT','Synthetic credits exhausted')
        return original(self,role,instructions,payload,schema,**options)
    monkeypatch.setattr(EngineeringGateway,'structured',limited)
    stopped=launch(engineering_app,key='credit-stopped',output_scope='WHOLE_FURNITURE')
    assert stopped['status']=='BUDGET_REACHED' and stopped['error'] is None
    assert stopped['result']['available_output']['has_cad'] is False
    client,app,headers,project,_=engineering_app
    report=client.get(f'/api/v1/agents/workflows/{stopped["id"]}/result',headers=headers['operator'])
    assert report.status_code==200 and report.json()['available_output']['dimensions']
    state=client.get(f'/api/projects/{project}/status',headers=headers['operator']).json()
    assert state['model_scope']['job_id']==source['job_id']==state['delivery']['job_id']
    assert state['model_scope']['available_output_workflow_id']==stopped['id']
    assert stopped['id'] in {w['id'] for w in state['workflows']}
    assert calls==['EngineeringDimensions','PhotoDesign']


def test_reexport_preserves_draft_warnings_without_new_ai(engineering_app,monkeypatch):
    original=EngineeringGateway.structured;seen=[]
    def limited(self,role,instructions,payload,schema,**options):
        seen.append(schema.__name__)
        if schema.__name__=='EngineeringReview':raise AIServiceError('AI_CREDIT_LIMIT','Synthetic credits exhausted')
        return original(self,role,instructions,payload,schema,**options)
    monkeypatch.setattr(EngineeringGateway,'structured',limited)
    source=launch(engineering_app,key='draft-source',output_scope='WHOLE_FURNITURE')
    before=list(seen)
    copied=launch(engineering_app,key='draft-reexport',output_scope='WHOLE_FURNITURE',engineering_reference_workflow_id=source['id'])
    assert copied['status']=='REVIEW_REQUIRED' and copied['error'] is None
    assert copied['result']['engineering_cad']['status']=='BUDGET_LIMIT_DRAFT'
    assert copied['result']['engineering_cad']['workflow']['budget_draft']==source['result']['engineering_cad']['workflow']['budget_draft']
    assert seen==before
    assert_budget_files(engineering_app,copied)


def test_credit_stop_before_dimensions_provides_real_references_only(engineering_app,monkeypatch):
    seen=[]
    def limited(self,role,instructions,payload,schema,**options):
        seen.append(schema.__name__)
        raise AIServiceError('AI_CREDIT_LIMIT','Synthetic credits exhausted')
    monkeypatch.setattr(EngineeringGateway,'structured',limited)
    state=launch(engineering_app,output_scope='WHOLE_FURNITURE')
    assert state['status']=='BUDGET_REACHED' and state['error'] is None
    output=state['result']['available_output']
    assert output['dimensions'] is None and output['references'] and not output['has_cad']
    assert not state['result'].get('delivery')
    assert seen==['EngineeringDimensions']
