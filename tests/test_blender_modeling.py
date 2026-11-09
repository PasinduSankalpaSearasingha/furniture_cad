"""Bounded native modeling contracts; providers mocked, actual Blender separately opt-in."""
import base64
import copy
import json
import os
from pathlib import Path
from uuid import uuid4
from io import BytesIO
import numpy as np
import pytest
from PIL import Image
from sqlalchemy import select
from app.ai.openai_client import AIServiceError,ToolResult
from app.photo_schema import PhotoDesign
from app.pipeline import file_hash,canonical_hash
from app.reference_fit_schema import FitView
from app.rendering import modeling
from app.agents import orchestrator
from app.database import AgentWorkflow,AIInvocation,Revision
from app.agents.photo_agent import resolve_estimated_geometry
from tests.photo_fixtures import photo_plan_fixture,analysis_fixture
from tests.test_agent_workflows import agents_app
from tests.test_studio import workflow_status


def test_point_to_triangle_distance_uses_surfaces_and_units():
    triangle=np.array([[[0,0,0],[100,0,0],[0,100,0]]],float)
    distances=modeling.triangle_distance(np.array([[20,20,3],[20,20,0],[50,50,-4],[110,0,0]]),triangle)
    assert distances==pytest.approx([3,0,4,10],abs=1e-8)


def test_indexed_surface_distance_matches_brute_force_and_keeps_long_triangles():
    from app.ai.reference_fitting import MeshPart
    rng=np.random.default_rng(17)
    triangles=rng.normal(size=(20010,3,3))*30
    # Centroid-nearest pruning would miss this surface near its distant corner.
    triangles[0]=[[1000,0,0],[0,1000,0],[0,0,0]]
    points=np.array([[999,0,2],[300,300,1],[11,12,13]],float)
    mesh=MeshPart(triangles.reshape(-1,3),np.arange(len(triangles)*3).reshape(-1,3))
    exact=modeling.triangle_distance(points,triangles)
    assert modeling.indexed_triangle_distance(points,mesh)==pytest.approx(exact,abs=1e-8)


def test_numeric_plan_resolves_curves_and_sculpting_without_executable_code():
    plan=photo_plan_fixture();raw=plan.model_dump(mode='json')
    raw['parts'][0]['dish_depth']=0;raw['parts'][0]['surface_grid']=dict(depths=[[0,0,0],[0,'seat_thickness_mm/4',0],[0,0,0]])
    plan=PhotoDesign.model_validate(raw);numeric=modeling.numeric_plan(plan)
    assert numeric['parts'][0]['surface_grid']['depths'][1][1]==7.5
    assert numeric['parts'][1]['path'][-1][0]==590
    with pytest.raises(ValueError):PhotoDesign.model_validate({**raw,'parts':[{**raw['parts'][0],'position':['__import__("os")',0,0]}]})


@pytest.fixture
def native_fixture(monkeypatch):
    """Fast server contract fixture built from synthetic CAD, NOT a real Blender result."""
    import trimesh
    from app.ai.reference_fitting import mesh_from_build,metrics,render_mask
    def build(settings,plan,build,output,context,progress):
        output.mkdir();meshes=mesh_from_build(build,plan);raw={k:dict(points=m.points.tolist(),triangles=m.triangles.tolist()) for k,m in meshes.items()}
        (output/'model_mesh.json').write_text(json.dumps(raw),encoding='utf-8')
        scene=trimesh.Scene()
        for k,m in meshes.items():scene.add_geometry(trimesh.Trimesh(m.points*.001,m.triangles,process=False),node_name=k,geom_name=k)
        (output/'reconstruction.glb').write_bytes(scene.export(file_type='glb'));(output/'reconstruction.blend').write_bytes(b'SYNTHETIC_TEST_ONLY')
        views=[];files=['model_mesh.json','reconstruction.glb','reconstruction.blend']
        for i,(target,camera) in enumerate(context):
            measured=metrics(render_mask(meshes,camera,target['size']),target) if target['usable'] else None
            views.append(dict(image_id=str(target['view'].image_id),camera=camera,mask_usable=target['usable'],mask_reviewed=False,
                metrics=measured,cad_photo_metrics=measured,mask_method='SYNTHETIC_TEST',warnings=[]))
            for name in (f'view_{i}.png',f'comparison_{i}.png',f'mask_{i}.png'):
                Image.fromarray(target['mask'].astype('uint8')*255).save(output/name);files.append(name)
        Image.new('RGB',(64,64),'tan').save(output/f'view_{len(context)}.png');files.append(f'view_{len(context)}.png')
        report=dict(source='SYNTHETIC_TEST_ONLY',component_count=len(plan.parts),device='MOCK',views=views,
            cad_agreement=dict(status='SAMPLED_WITHIN_TOLERANCE',sampled_max_surface_gap_mm=0,tolerance_mm=2,parts=[]))
        (output/'model_report.json').write_text(json.dumps(report));files.append('model_report.json')
        (output/'blender_model_package.zip').write_bytes(b'SYNTHETIC_TEST_ONLY');files.append('blender_model_package.zip')
        return dict(plan_sha256=canonical_hash(plan.model_dump(mode='json')),directory=output.name,
            files={n:file_hash(output/n) for n in files},report=report,component_labels=[dict(part_id=p.part_id,label=p.label,material=p.material) for p in plan.parts])
    monkeypatch.setattr(modeling,'build_model',build)
    return build


class ModelingGateway:
    builds=0
    def __init__(self,config,models,record):
        self.config=config;self.models=models;self.record=record
        assert models['design']=='gpt-6.1-sol' and not models.get('fallback')
    def structured(self,role,instructions,payload,schema,images=(),tools=None,require_tool=None,**kwargs):
        if schema.__name__=='PhotoAnalysis':return schema.model_validate(analysis_fixture())
        if schema.__name__=='PhotoGeometryReview':
            return schema(summary='Synthetic visible review, not a live accuracy claim',visible_geometry_matches=True,
                differences=[],preserved_features=['Synthetic part identities'],unknown_geometry=['Hidden joints'])
        assert require_tool=='blender_build' and tools and images
        result=photo_plan_fixture(**{v['key']:v['value'] for v in payload['known_values']})
        evidence=tools['blender_build']['handler'](result)
        assert isinstance(evidence,ToolResult) and evidence.payload['valid'] and evidence.images
        type(self).builds+=1
        return result


def test_sol_native_workflow_validates_exports_and_private_model_artifacts(agents_app,monkeypatch,native_fixture):
    c,app,h,p,image=agents_app;monkeypatch.setattr(orchestrator,'ResponsesGateway',ModelingGateway)
    response=c.post(f'/api/projects/{p}/reconstruct-with-blender',headers=h['operator'],json=dict(image_ids=[image],
        known_values=[dict(key='width_mm',value=700)],auto_render=False,idempotency_key='native-sol'))
    result=workflow_status(c,h,response);assert result['status']=='REVIEW_REQUIRED',result.get('error')
    model=result['result']['blender_modeling'];assert model['ai_model']=='gpt-6.1-sol' and model['ai_executed']
    assert model['cad_export_agreement']['export_reopened'] and model['cad_export_agreement']['step_valid_solids']==7
    artifact=model['artifact'];url=artifact['download_base']+'/reconstruction.glb'
    assert c.get(url,headers=h['operator']).status_code==200 and c.get(url).status_code==401
    assert result['result']['value_provenance']['width_mm']['source']=='USER_PROVIDED'
    with app.state.db.sessions() as session:
        workflow=session.get(AgentWorkflow,result['id']);assert workflow.model_config['design']=='gpt-6.1-sol'
        assert workflow.model_config['fallback']=='';source=session.get(Revision,result['revision_id']);original=source.spec_hash
    app.state.settings.ai_enabled=False
    def fail(*a,**k):raise AssertionError('Local native rebuild must not construct an AI gateway')
    monkeypatch.setattr(orchestrator,'ResponsesGateway',fail)
    local=workflow_status(c,h,c.post(f'/api/projects/{p}/rebuild-blender',headers=h['operator'],json={}))
    assert local['status']=='REVIEW_REQUIRED',local.get('error')
    assert local['revision_id']!=result['revision_id'] and not local['result']['blender_modeling']['ai_executed']
    assert local['usage']['input_tokens']==local['usage']['output_tokens']==0
    with app.state.db.sessions() as session:assert session.get(Revision,result['revision_id']).spec_hash==original
    local_artifact=local['result']['blender_modeling']['artifact'];assert local_artifact['download_base']!=artifact['download_base']
    path=app.state.settings.storage/artifact['directory']/'reconstruction.glb';path.write_bytes(b'tampered')
    assert c.get(url,headers=h['operator']).status_code==409
    assert c.get(local_artifact['download_base']+'/reconstruction.glb',headers=h['operator']).status_code==200


def test_native_inputs_reject_unknown_reference_and_arbitrary_commands(agents_app):
    c,app,h,p,image=agents_app
    for body in [dict(image_ids=[image],modeling_views=[dict(image_id=str(uuid4()))]),
                 dict(image_ids=[image],modeling_views=[dict(image_id=image,mask_png='bad')]),
                 dict(image_ids=[image],python='import os')]:
        response=c.post(f'/api/projects/{p}/reconstruct-with-blender',headers=h['operator'],json=body)
        assert response.status_code in (404,422),response.text


def test_measured_corrections_reject_photo_landmark_and_cad_regressions():
    from app.agents.blender_modeling_agent import BlenderModelingAgent
    from types import SimpleNamespace
    agent=object.__new__(BlenderModelingAgent);agent.r=SimpleNamespace(config=SimpleNamespace(blender_cad_tolerance_mm=2))
    def artifact(loss,cad_loss=None,gap=.5,landmark=.01):
        value=dict(loss=loss,landmark_error_fraction=landmark)
        return dict(report=dict(views=[dict(metrics=value,cad_photo_metrics={**value,'loss':loss if cad_loss is None else cad_loss})],
            cad_agreement=dict(sampled_max_surface_gap_mm=gap)))
    before=artifact(.4)
    agent.accept(before,artifact(.3))
    for changed in [artifact(.41),artifact(.3,cad_loss=.42),artifact(.3,gap=3),artifact(.3,landmark=.03)]:
        with pytest.raises(ValueError):agent.accept(before,changed)


def test_sol_correction_uses_actual_build_tool_and_preserves_parameter_values(agents_app,monkeypatch,native_fixture):
    from app.agents.photo_fidelity import SparseGeometryPatch
    class CorrectionGateway(ModelingGateway):
        reviews=0
        def structured(self,role,instructions,payload,schema,images=(),tools=None,require_tool=None,**kwargs):
            if schema.__name__=='PhotoGeometryReview':
                type(self).reviews+=1;done=type(self).reviews>1
                return schema(summary='Synthetic controlled correction test',visible_geometry_matches=done,
                    differences=[] if done else [dict(component_ids=['F007'],severity='MAJOR',operation='reshape',
                        observed_difference='Synthetic support shift',correction='Move the support 1 mm along X')],
                    preserved_features=['Supplied widths'],unknown_geometry=['Hidden joints'])
            if schema.__name__=='SparseGeometryPatch':
                patch=SparseGeometryPatch(summary='Synthetic support shift',changes=[dict(part_id='F007',field='position',
                    value=['width_mm/2+1','depth_mm-20','(height_mm+seat_height_mm-15)/2'])],added_parts=[],removed_part_ids=[],added_values=[])
                assert require_tool=='blender_apply_changes'
                evidence=tools[require_tool]['handler'](patch);assert isinstance(evidence,ToolResult),evidence
                assert evidence.payload['valid'];return patch
            return super().structured(role,instructions,payload,schema,images,tools,require_tool,**kwargs)
    c,app,h,p,image=agents_app;monkeypatch.setattr(orchestrator,'ResponsesGateway',CorrectionGateway)
    result=workflow_status(c,h,c.post(f'/api/projects/{p}/reconstruct-with-blender',headers=h['operator'],json=dict(image_ids=[image],auto_render=False)))
    assert result['status']=='REVIEW_REQUIRED',result.get('error')
    assert len(result['result']['blender_modeling']['history'])==2
    assert len(result['modeling_previews'])==2
    plan=result['result']['specification']['photo_design'];assert plan['parts'][6]['position'][0]=='width_mm/2+1'
    assert {v['key']:v['value'] for v in plan['values']}['seat_thickness_mm']==30
    first,second=result['modeling_previews'];assert first['download_base']!=second['download_base']
    for artifact in (first,second):assert c.get(artifact['download_base']+'/reconstruction.glb',headers=h['operator']).status_code==200


@pytest.mark.parametrize('error_code',['AI_RESPONSE_INCOMPLETE','AI_TOOL_LIMIT'])
def test_incomplete_final_model_response_retains_successful_native_build(agents_app,monkeypatch,native_fixture,error_code):
    class IncompleteGateway(ModelingGateway):
        def structured(self,*args,**kwargs):
            result=super().structured(*args,**kwargs)
            if args[3].__name__=='PhotoDesign':raise AIServiceError(error_code,'Synthetic final response limit',True)
            return result
    c,app,h,p,image=agents_app;monkeypatch.setattr(orchestrator,'ResponsesGateway',IncompleteGateway)
    result=workflow_status(c,h,c.post(f'/api/projects/{p}/reconstruct-with-blender',headers=h['operator'],json=dict(image_ids=[image],auto_render=False)))
    assert result['status']=='REVIEW_REQUIRED',result.get('error')
    model=result['result']['blender_modeling'];assert model['error']['code']==error_code
    assert model['history']==[] and model['cad_export_agreement']['export_reopened']


def test_failed_edge_rounding_identifies_component_and_preserves_plan():
    from app.agents.photo_agent import specification
    from app.photo_cad import create_photo_furniture
    raw=photo_plan_fixture().model_dump(mode='json');raw['parts'][0]['edge_radius']=1000
    plan=PhotoDesign.model_validate(raw);original=canonical_hash(plan.model_dump(mode='json'))
    spec,_=specification(plan,{'known_values':[]},'Fillet diagnostic')
    with pytest.raises(ValueError,match='F001.*edge_radius=1000.*outline_panel'):
        create_photo_furniture(spec)
    assert canonical_hash(plan.model_dump(mode='json'))==original


@pytest.mark.skipif(os.getenv('BLENDER_MODELING_TESTS')!='1',reason='Opt in to actual local Blender modeling')
def test_actual_native_blender_builds_photo_geometry_and_camera(tmp_path):
    from app.config import Settings
    settings=Settings(blender_device='CPU');raw=photo_plan_fixture().model_dump(mode='json')
    raw['parts'][0]['dish_depth']=0;raw['parts'][0]['surface_grid']=dict(depths=[[0,0,0],[0,9,0],[0,0,0]])
    raw['parts'][1].update(geometry='profile_sweep',size=[40,28,1],sweep_sections=[dict(fraction=f,width=w,depth=d)
        for f,w,d in [(0,.4,.4),(.05,20,14),(.25,40,28),(.5,40,28),(.75,40,28),(.95,20,14),(1,.4,.4)]])
    plan=PhotoDesign.model_validate(raw)
    plan,_,_,build,_=resolve_estimated_geometry(plan,{},'Native test')
    image=BytesIO();Image.new('RGB',(180,240),'white').save(image,format='PNG')
    context=modeling.context_for(plan,build,[(FitView(image_id=uuid4()),image.getvalue())],{},settings)
    artifact=modeling.build_model(settings,plan,build,tmp_path/'native',context)
    report=artifact['report'];assert report['source']=='NATIVE_BLENDER_GEOMETRY' and report['component_count']==7
    assert report['maximum_camera_projection_error_pixels']<.25
    assert all(p['closed'] and p['volume_mm3']>0 for p in report['components'])
    assert artifact['files']['reconstruction.glb'] and artifact['files']['reconstruction.blend']
    assert report['cad_agreement']['complete_surface_bound_verified'] is False
    # Real Boolean operations must not replace the part colors with empty or
    # inherited cutter material slots in either the saved model or its preview.
    import struct
    glb=(tmp_path/'native'/'reconstruction.glb').read_bytes()
    chunk_size=struct.unpack_from('<I',glb,12)[0];document=json.loads(glb[20:20+chunk_size])
    for part in plan.parts:
        node=next(node for node in document['nodes'] if node['name']==part.part_id)
        for primitive in document['meshes'][node['mesh']]['primitives']:
            material=document['materials'][primitive['material']]
            color=material['pbrMetallicRoughness']['baseColorFactor'][:3]
            assert color==pytest.approx(part.color_rgb,abs=1e-5)
