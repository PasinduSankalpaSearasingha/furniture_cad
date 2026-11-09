"""Real B-Rep round trips and the adapted upstream workflow, without paid calls."""
import json
from io import BytesIO
from zipfile import ZipFile
import cadquery as cq
import pytest
from app.photo_schema import PhotoDesign
from app.engineering_cad import check_solid,verify_solid_step,check_supplied_dimensions
from app.engineering_upstream import prompt,provenance
from app.agents import orchestrator
from tests.test_agent_workflows import agents_app


def table_plan(width=700):
    values=dict(width_mm=width,depth_mm=500,height_mm=750,top_thickness_mm=30,leg_width_mm=40)
    parts=[]
    def add(label,size,position):
        parts.append(dict(part_id=f'F{len(parts)+1:03d}',label=label,geometry='box',size=size,position=position,
            material='solid_wood',color_rgb=[.55,.34,.18],evidence='Synthetic fixture; not actual photo recognition'))
    add('Table top',['width_mm','depth_mm','top_thickness_mm'],[0,0,'height_mm-top_thickness_mm/2'])
    for x in ('-width_mm/2+60','width_mm/2-60'):
        for y in ('-depth_mm/2+60','depth_mm/2-60'):
            add('Table leg',['leg_width_mm','leg_width_mm','height_mm-top_thickness_mm'],[x,y,'(height_mm-top_thickness_mm)/2'])
    return PhotoDesign(furniture_type='synthetic table',summary='Synthetic separate-part B-Rep assembly',
        values=[dict(key=k,value=v,explanation='Synthetic estimate') for k,v in values.items()],parts=parts,
        assumptions=['Synthetic geometry, hidden joints unverified'])


class EngineeringGateway:
    calls=[];reject_visual=False
    def __init__(self,config,models,record):self.config=config;self.models=models;self.record=record
    def structured(self,role,instructions,payload,schema,images=(),tools=None,**kwargs):
        self.calls.append(schema.__name__)
        known={x['key']:x['value'] for x in payload.get('known_values',[])}
        plan=table_plan(known.get('width_mm',700))
        if schema.__name__=='EngineeringRecognition':
            result=dict(design=plan.model_dump(mode='json'),dimensions=dict(values=[v.model_dump() for v in plan.values],assumptions=plan.assumptions,
                appearance=dict(summary='Synthetic furniture visible identity',critical_features=[dict(feature_id='V001',label='Furniture silhouette',
                    visible_shape='Synthetic top and supports',relative_layout='Supports below the top')],continuity=[],symmetry='Fixture geometry only',avoid_substitutions=[])))
        elif schema.__name__=='EngineeringDimensions':result=dict(values=[v.model_dump() for v in plan.values],assumptions=plan.assumptions,
            appearance=dict(summary='Synthetic furniture visible identity',critical_features=[dict(feature_id='V001',label='Furniture silhouette',
                visible_shape='Synthetic top and supports',relative_layout='Supports below the top')],continuity=[],symmetry='Fixture geometry only',avoid_substitutions=[]))
        elif schema.__name__=='EngineeringAssembly':result=dict(furniture_type=plan.furniture_type,
            parts=[dict(part_id=p.part_id,label=p.label,description='Separate fixture component',visible_evidence=p.evidence) for p in plan.parts],assumptions=plan.assumptions)
        elif schema.__name__=='EngineeringReview':
            result=dict(accepted=not(self.reject_visual and len(images)>1),findings=[],limitations=['Hidden physical details unverified'],
                feature_checks=[dict(feature_id='V001',status='MATCH',component_ids=[],evidence='Synthetic geometry comparison',correction='')])
        elif schema.__name__=='EngineeringRepairPatch':
            from app.engineering_repairs import EngineeringRepairPatch
            # A small, real profile change; untouched fixture solids stay fixed.
            source=payload['source_plan'];part=dict(source['parts'][0]);part['corner_radius']=5
            patch=EngineeringRepairPatch(parts=[part],summary='Synthetic top corner correction')
            checked=tools['cad_generation']['handler'](patch);assert checked['valid'],checked
            return patch
        else:
            result=plan.model_dump(mode='json')
            assert tools and kwargs.get('require_tool')=='cad_generation'
            checked=tools['cad_generation']['handler'](plan);assert checked['valid'],checked
        return schema.model_validate(result)


@pytest.fixture
def engineering_app(agents_app,monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','synthetic-test-key')
    EngineeringGateway.calls=[];monkeypatch.setattr(EngineeringGateway,'reject_visual',False)
    monkeypatch.setattr(orchestrator,'ResponsesGateway',EngineeringGateway)
    def forbidden(*a,**k):raise AssertionError('Engineering CAD must not require Blender')
    monkeypatch.setattr('app.rendering.blender_service.discover_blender',forbidden)
    return agents_app


def launch(env,key='engineering-test',**changes):
    client,app,h,project,image=env
    response=client.post(f'/api/projects/{project}/generate-all',headers=h['operator'],json=dict(
        image_id=image,known_values=[dict(key='width_mm',value=720)],idempotency_key=key,
        output_scope=changes.pop('output_scope','PARTS'),**changes))
    assert response.status_code==202,response.text
    return client.get(response.json()['status_url'],headers=h['operator']).json()


def test_source_prompts_pinned_and_loaded_without_importing_upstream_python():
    assert provenance()['commit']=='702b90e0d26b630d7eba09f85d29bb446f6a1b96'
    assert 'Assembly Decomposer' in prompt('assembly_decomposer','SYSTEM_WITH_IMAGE')
    assert 'Never say "unknown"' not in prompt('dimension_extractor','SYSTEM_WITH_IMAGE')


def test_exhausted_geometry_repairs_report_the_actual_validation_error(engineering_app,monkeypatch):
    from app.ai.openai_client import AIServiceError
    structured=EngineeringGateway.structured
    def reject(self,role,instructions,payload,schema,images=(),tools=None,**options):
        if schema.__name__=='EngineeringRecognition':
            result=structured(self,role,instructions,payload,schema,images=images,tools=tools,**options)
            result.design.parts[1].position[0]='5000'
            return result
        if schema.__name__ in ('PhotoDesign','EngineeringRepairPatch'):
            from app.engineering_repairs import EngineeringRepairPatch
            plan=table_plan(720)
            plan.parts[1].position[0]='5000'
            args=plan if schema is PhotoDesign else EngineeringRepairPatch(parts=[plan.parts[1]],summary='Synthetic invalid repair')
            checked=tools['cad_generation']['handler'](args)
            assert not checked['valid']
            raise AIServiceError('AI_TOOL_LIMIT','Synthetic repair budget exhausted')
        return structured(self,role,instructions,payload,schema,images=images,tools=tools,**options)
    monkeypatch.setattr(EngineeringGateway,'structured',reject)
    state=launch(engineering_app,output_scope='WHOLE_FURNITURE')
    assert state['status']=='REVIEW_REQUIRED' and state['error'] is None
    output=state['result']['available_output']
    assert not output['has_cad'] and not state['result'].get('delivery')
    assert output['stop_reason']['code']=='CAD_BUILD_NEEDS_REVISION'
    assert 'Last geometry error: Actual width_mm envelope' in output['stop_reason']['message']


def test_step_verifier_requires_closed_solids_and_checks_actual_export(tmp_path):
    shape=cq.Workplane('XY').box(100,80,20).edges('|Z').fillet(4).val()
    path=tmp_path/'part.step';cq.exporters.export(shape,str(path))
    actual,proof=verify_solid_step(shape,path)
    assert proof['valid_closed_solid'] and proof['sampled_surface_max_gap_mm']<.001
    cq.exporters.export(cq.Workplane('XY').box(101,80,20),str(path))
    with pytest.raises(ValueError,match='envelope or volume'):verify_solid_step(shape,path)
    with pytest.raises(ValueError):check_solid(shape.Faces()[0])


def test_exact_surface_comparison_handles_valid_stationary_bezier_end(tmp_path):
    # This member reproduced the live export failure: its preview mesh has
    # off-surface nodes even when compared to its own unchanged B-Rep.
    from app.photo_cad import curve
    points=[(0,790,0),(0,790*2/3,0),(0,790/3,0),(0,0,0),
        (22/3,0,0),(44/3,0,0),(22,0,0),(37,80,0),(45,135,0),(45,160,0),
        (45,370,0),(45,790,0),(45,790,0),(30,790,0),(15,790,0),(0,790,0)]
    wire=curve(points,'bezier',closed=True)
    source=cq.Solid.extrudeLinear(wire,[],cq.Vector(0,0,45)).translate((0,0,-22.5))
    source=source.rotate((0,0,0),(1,0,0),90).translate((0,427.5,0))
    path=tmp_path/'curved_member.step';cq.exporters.export(source,str(path))
    _,proof=verify_solid_step(source,path)
    assert proof['sampled_surface_max_gap_mm']<1e-7
    assert proof['surface_tolerance_mm']==.001
    assert proof['surface_sampling_method']=='EXACT_BREP_TRIMMED_UV_EDGES_AND_VERTICES'
    # Confirm this is a regression for the former triangulation-based check.
    nodes,_=source.tessellate(.25,.12)
    assert max(source.distance(cq.Vertex.makeVertex(*p.toTuple())) for p in nodes)>.001


def test_surface_check_rejects_moved_hole_with_same_volume_and_outer_bounds(tmp_path):
    def drilled(x):
        return cq.Workplane('XY').box(100,80,20).faces('>Z').workplane().center(x,0).hole(8).val()
    source=drilled(-15);changed=drilled(15)
    assert source.Volume()==pytest.approx(changed.Volume(),abs=1e-6)
    path=tmp_path/'changed_hole.step';cq.exporters.export(changed,str(path))
    with pytest.raises(ValueError,match=r'exceeds 0.001 mm.*changed_hole.step.*measured'):
        verify_solid_step(source,path)


def test_sculpted_quilt_round_trip_uses_precise_volume_without_relaxing_limits(tmp_path):
    from pathlib import Path
    from app.agents.photo_agent import specification
    from app.photo_cad import create_photo_furniture
    plan=PhotoDesign.model_validate_json((Path(__file__).parent/'fixtures'/'sculpted_quilt.json').read_text())
    spec,_=specification(plan,dict(known_values=[]),'Sculpted volume regression')
    source=create_photo_furniture(spec).shapes['F005'].val();path=tmp_path/'sculpted_quilt.step'
    cq.exporters.export(source,str(path));actual=cq.importers.importStep(str(path)).val()
    # This same closed spline solid fails when using OCCT's default integration.
    assert abs(source.Volume()-actual.Volume())>max(1e-3,source.Volume()*1e-7)
    _,proof=verify_solid_step(source,path)
    assert proof['volume_error_mm3']<proof['volume_tolerance_mm3']
    assert proof['volume_tolerance_mm3']==pytest.approx(max(1e-3,proof['source_volume_mm3']*1e-7))
    assert proof['sampled_surface_max_gap_mm']<.001 and proof['volume_integration_relative_tolerance']==1e-10


def test_estimate_reconciliation_retains_valid_long_member_expression():
    from app.agents.photo_agent import resolve_estimated_geometry
    raw=table_plan().model_dump(mode='json')
    for key,value in dict(underseat_support_thickness_mm=2,rail_section_width_mm=2,seat_depth_mm=300,upright_thickness_mm=5).items():
        raw['values'].append(dict(key=key,value=value,explanation='Synthetic member estimate'))
    raw['parts'][0]['size'][1]='depth_mm*1.2'
    expression='depth_mm-rail_section_width_mm/2-(depth_mm/2-underseat_support_thickness_mm+seat_depth_mm/2-upright_thickness_mm/2)'
    raw['parts'][1]['position'][1]=expression
    resolved,spec,values,build,adjustments=resolve_estimated_geometry(PhotoDesign.model_validate(raw),dict(known_values=[]),'Long expression test')
    assert adjustments['depth_mm']['resolved_value']==600
    assert len(resolved.parts[1].position[1])>160
    assert values['depth_mm']['verified'] is False and len(build.parts)==5


def test_supplied_measurement_is_checked_against_real_envelope():
    from app.agents.photo_agent import resolve_estimated_geometry
    _,_,_,build,_=resolve_estimated_geometry(table_plan(),dict(known_values=[]),'Dimension check')
    assert check_supplied_dimensions(build,[dict(key='width_mm',value=700)])[0]['error_mm']<.001
    with pytest.raises(ValueError,match='within 0.01 mm'):
        check_supplied_dimensions(build,[dict(key='width_mm',value=700.2)])


def test_trimmed_curved_support_step_envelope_uses_actual_surface(tmp_path):
    from tests.photo_fixtures import photo_plan_fixture
    from app.agents.photo_agent import resolve_estimated_geometry
    _,_,_,build,_=resolve_estimated_geometry(photo_plan_fixture(),dict(known_values=[]),'Curved support')
    shape=build.shapes['F007'].val();path=tmp_path/'curved_support.step';cq.exporters.export(shape,str(path))
    _,proof=verify_solid_step(shape,path)
    assert proof['valid_closed_solid'] and proof['envelope_max_error_mm']<.005
    assert proof['sampled_surface_max_gap_mm']<.001


def test_complete_engineering_workflow_separate_solids_files_and_source_scoping(engineering_app):
    env=engineering_app;c,app,h,p,image=env;result=launch(env)
    assert result['status']=='REVIEW_REQUIRED',result.get('error')
    directory=app.state.settings.storage/result['job_id'];proof=result['result']['engineering_cad']['cad_export_agreement']
    assert proof['valid_closed_solids'] and proof['assembly_solid_count']==5
    assert proof['geometry_checks']['assembly_connected'] and proof['all_part_steps_reopened']
    assert result['result']['value_provenance']['width_mm']['source']=='USER_PROVIDED'
    assert result['result']['value_provenance']['height_mm']['source']=='AI_ESTIMATED'
    assert not result['result'].get('neural4d') and not result['result']['approved_for_manufacturing']
    assert len(cq.importers.importStep(str(directory/'cabinet.step')).solids().vals())==5
    for part in json.loads((directory/'parts_registry.json').read_text()):
        assert part['solid_valid'] and part['geometry_source']=='ENGINEERING_CAD'
        for name in part['files'].values():assert (directory/name).is_file()
        check_solid(cq.importers.importStep(str(directory/part['files']['step'])).val())
    state=c.get(f'/api/projects/{p}',headers=h['operator']).json()
    assert state['model_scope']['geometry_source']=='ENGINEERING_CAD' and state['model_scope']['solid_geometry_verified']
    assert state['model_scope']['job_id']==result['job_id']
    components=c.get(f'/api/v1/jobs/{result["job_id"]}/components',headers=h['operator']).json()['components']
    assert len(components)==5 and components[1]['model_url'].endswith('/part_F002_REVIEW.glb')
    delivery=result['result']['delivery'];response=c.get(delivery['download_base']+'/furniture_complete_package.zip',headers=h['operator'])
    assert response.status_code==200
    with ZipFile(BytesIO(response.content)) as archive:
        assert archive.testzip() is None and 'cad/part_F005_REVIEW.step' in archive.namelist()
        assert json.loads(archive.read('accuracy_report.json'))['preview_geometry_source']=='ENGINEERING_CAD'
    before=len(EngineeringGateway.calls);again=launch(env)
    assert again['id']==result['id'] and len(EngineeringGateway.calls)==before
    assert c.post(f'/api/v1/revisions/{result["revision_id"]}/release',headers=h['designer']).status_code==409


def test_no_render_cannot_silently_pass_visual_review(engineering_app,monkeypatch):
    def failed(*a,**k):raise ValueError('Synthetic renderer failure')
    monkeypatch.setattr('app.ai.geometry_preview.render_build',failed)
    result=launch(engineering_app)
    assert result['status']=='REVIEW_REQUIRED' and result['error'] is None
    assert not result['result'].get('delivery') and result['result']['available_output']['has_saved_cad_plan']
    assert result['result']['approved_for_manufacturing'] is False
    assert EngineeringGateway.calls==['EngineeringRecognition']  # critic is local; missing renders prevent paid visual review


def test_delivery_resume_reuses_existing_geometry_and_ai_checkpoints(engineering_app,monkeypatch):
    from app import delivery
    real=delivery.build_delivery
    def failure(*a):raise RuntimeError('Synthetic interrupted delivery')
    monkeypatch.setattr(delivery,'build_delivery',failure)
    failed=launch(engineering_app);assert failed['status']=='FAILED'
    calls=len(EngineeringGateway.calls);monkeypatch.setattr(delivery,'build_delivery',real)
    def no_rebuild(*a,**k):raise AssertionError('Completed CAD must not be rebuilt for delivery retry')
    monkeypatch.setattr('app.agents.photo_agent.resolve_estimated_geometry',no_rebuild)
    c,app,h,p,image=engineering_app
    response=c.post(f'/api/v1/agents/workflows/{failed["id"]}/retry',headers=h['operator']);assert response.status_code==202
    result=c.get(f'/api/v1/agents/workflows/{failed["id"]}',headers=h['operator']).json()
    assert result['status']=='REVIEW_REQUIRED',result.get('error')
    assert result['job_id']==failed['job_id'] and len(EngineeringGateway.calls)==calls


def test_frozen_engineering_plan_reexport_needs_no_ai_and_keeps_original_files(engineering_app,monkeypatch):
    env=engineering_app;c,app,h,p,image=env;first=launch(env)
    assert first['status']=='REVIEW_REQUIRED',first.get('error')
    source=app.state.settings.storage/first['job_id'];original=(source/'manifest.json').read_bytes()
    def forbidden(*a,**k):raise AssertionError('Saved-plan export must not initialize an AI gateway')
    monkeypatch.setattr(orchestrator,'ResponsesGateway',forbidden)
    monkeypatch.delenv('OPENAI_API_KEY',raising=False);app.state.settings.ai_enabled=False
    response=c.post(f'/api/projects/{p}/generate-all',headers=h['operator'],json=dict(image_id=image,cad_source='ENGINEERING_CAD',
        engineering_reference_workflow_id=first['id'],idempotency_key='local-reexport',output_scope='PARTS'))
    assert response.status_code==202,response.text
    result=c.get(response.json()['status_url'],headers=h['operator']).json()
    assert result['status']=='REVIEW_REQUIRED',result.get('error')
    assert result['job_id']!=first['job_id'] and (source/'manifest.json').read_bytes()==original
    assert result['result']['engineering_cad']['workflow']['reexport_of']==first['id']
