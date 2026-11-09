"""Synthetic provider comparisons exercise actual solids, previews and frozen revisions."""
import json
import struct
from copy import deepcopy
from io import BytesIO
import numpy as np
from PIL import Image
import pytest
from app.photo_schema import PhotoDesign,PhotoPart
from app.agents.photo_fidelity import PhotoGeometryReview,PhotoGeometryPatch,SparseGeometryPatch,expand_sparse_patch,apply_geometry_patch,validate_correction
from app.agents.photo_agent import specification
from app.photo_cad import create_photo_furniture
from app.ai.geometry_preview import render_build
from app.review_mesh import export_glb
from app.geometry import exact_bounds
from app.database import Revision,AgentWorkflow
from app.estimated_workflow import validate_estimated_revision
from tests.test_agent_workflows import agents_app
from tests.photo_fixtures import analysis_fixture,photo_plan_fixture
from examples.ai_demo.mock_gateway import MockGateway,fixtures


def comparison(matches=False):
    return dict(summary='Synthetic reference geometry comparison',visible_geometry_matches=matches,
        differences=[] if matches else [dict(component_ids=['F001'],severity='MAJOR',operation='reshape',
            observed_difference='Synthetic seat dish needs to be deeper',correction='Increase the existing shallow dish depth')],
        preserved_features=['Existing legs and supports'],unknown_geometry=['Unseen underside'],exact_reconstruction_verified=False)


def setup():
    MockGateway.responses={**fixtures(),'PhotoAnalysis':analysis_fixture(),'PhotoDesign':photo_plan_fixture().model_dump(mode='json')}


@pytest.mark.parametrize('modern',[False,True])
def test_same_photo_dimension_edit_reuses_components_and_other_values(agents_app,monkeypatch,modern):
    c,app,h,p,image=agents_app;setup()
    if modern:
        from tests.test_studio import setup_studio
        setup_studio(monkeypatch)
    endpoint=f'/api/projects/{p}/generate-cad' if modern else '/api/v1/ai/reconstruct-furniture'
    payload=dict(project_id=p,image_ids=[image],auto_render=False) if modern else dict(project_id=p,image_ids=[image])
    first=c.post(endpoint,headers=h['operator'],json=payload)
    first=c.get(first.json()['status_url'],headers=h['operator']).json()
    assert first['status']=='REVIEW_REQUIRED',first
    source=first['result']['specification']['photo_design']
    with app.state.db.sessions() as s: original=deepcopy(s.get(Revision,first['revision_id']).specification)
    second=c.post(endpoint,headers=h['operator'],json={**payload,'known_values':[dict(key='width_mm',value=740)]})
    second=c.get(second.json()['status_url'],headers=h['operator']).json()
    assert second['status']=='REVIEW_REQUIRED',second
    target=second['result']['specification']['photo_design']
    assert target['parts']==source['parts']
    before={v['key']:v['value'] for v in source['values']};after={v['key']:v['value'] for v in target['values']}
    assert after=={**before,'width_mm':740}
    assert second['result']['reference_fidelity']['status']=='PARAMETER_EDIT_PRESERVED'
    assert second['usage']['input_tokens']==0
    with app.state.db.sessions() as s:
        assert s.get(Revision,first['revision_id']).specification==original
        validate_estimated_revision(s.get(Revision,second['revision_id']),s)


def test_patch_is_limited_to_flagged_geometry_and_cannot_replace_values():
    source=photo_plan_fixture();review=PhotoGeometryReview(**comparison())
    patch=dict(summary='Synthetic correction',updated_parts=[source.parts[1].model_dump(mode='json')],added_parts=[],removed_part_ids=[],added_values=[])
    with pytest.raises(ValueError,match='unflagged'): apply_geometry_patch(source,PhotoGeometryPatch(**patch),review)
    patch['updated_parts']=[source.parts[0].model_dump(mode='json')]
    patch['added_values']=[dict(key='width_mm',value=1000,explanation='Unrequested replacement')]
    with pytest.raises(ValueError,match='existing parameter'): apply_geometry_patch(source,PhotoGeometryPatch(**patch),review)
    patch['added_values']=[];patch['updated_parts'][0]['color_rgb']=[1,0,0]
    with pytest.raises(ValueError,match='material or color'): apply_geometry_patch(source,PhotoGeometryPatch(**patch),review)


def test_missing_simple_alias_keeps_the_existing_section_dimension():
    source=photo_plan_fixture();before=source.model_dump(mode='json')
    sparse=SparseGeometryPatch(summary='Retain an unprovided section alias',changes=[dict(
        part_id='F003',field='width',section_index=0,value='retained_foot_width_mm')],added_parts=[],removed_part_ids=[],added_values=[])
    expanded=expand_sparse_patch(source,sparse)
    assert expanded.added_values[0].key=='retained_foot_width_mm' and expanded.added_values[0].value==28
    assert source.model_dump(mode='json')==before


def test_valid_component_correction_survives_another_invalid_proposal():
    source=photo_plan_fixture();spec,_=specification(source,{},'Partial correction')
    build=create_photo_furniture(spec);review=comparison()
    review['differences'].append(dict(component_ids=['F002'],severity='MAJOR',operation='reshape',
        observed_difference='Synthetic rail correction',correction='Synthetic bounded reshape'))
    seat=source.parts[0].model_copy(update={'dish_depth':8});rail=source.parts[1].model_copy(update={'size':[-1,1,1]})
    patch=PhotoGeometryPatch(summary='One valid and one invalid proposal',updated_parts=[seat,rail],
        added_parts=[],removed_part_ids=[],added_values=[])
    result,errors=validate_correction(source,patch,PhotoGeometryReview(**review),{},'Partial correction',build)
    assert result.parts[0].dish_depth==8 and result.parts[1]==source.parts[1]
    assert len(errors)==1 and errors[0]['part_id']=='F002'


def test_invalid_visual_correction_is_rejected_then_repaired_within_the_budget(agents_app,monkeypatch):
    from app.agents import orchestrator,photo_fidelity
    from examples.ai_demo.mock_gateway import png_bytes
    c,app,h,p,image=agents_app;setup();app.state.settings.photo_refinement_passes=2
    attempts=[];reviews=[]
    monkeypatch.setattr(photo_fidelity,'render_build',lambda *a,**k:png_bytes())
    class RepairGateway(MockGateway):
        def structured(self,role,instructions,payload,schema,images=(),**kwargs):
            if schema is PhotoGeometryReview:
                reviews.append(1);return schema.model_validate(comparison(len(reviews)==3))
            if schema is SparseGeometryPatch:
                part=deepcopy(payload['previous_plan']['parts'][0])
                part['dish_depth']=400 if not attempts else 8
                if attempts: assert 'Seat dish depth' in payload['previous_correction_failure']
                attempts.append(part['dish_depth'])
                return schema(summary='Synthetic bounded repair',changes=[dict(part_id=part['part_id'],field='dish_depth',
                    section_index=None,value=part['dish_depth'])],added_parts=[],removed_part_ids=[],added_values=[])
            return super().structured(role,instructions,payload,schema,images=images,**kwargs)
    monkeypatch.setattr(orchestrator,'ResponsesGateway',RepairGateway)
    result=c.post('/api/v1/ai/reconstruct-furniture',headers=h['operator'],json=dict(project_id=p,image_ids=[image]))
    result=c.get(result.json()['status_url'],headers=h['operator']).json()
    assert result['status']=='REVIEW_REQUIRED',result
    fidelity=result['result']['reference_fidelity']
    assert attempts==[400,8] and fidelity['status']=='VISIBLE_MATCH_REVIEWED'
    assert fidelity['history'][0]['plan_sha256']==fidelity['history'][1]['plan_sha256']
    assert fidelity['history'][2]['plan_sha256']!=fidelity['history'][0]['plan_sha256']
    assert result['result']['specification']['photo_design']['parts'][0]['dish_depth']==8


def test_provider_failure_retains_valid_cad_without_claiming_a_visual_match(agents_app,monkeypatch):
    from app.agents import orchestrator,photo_fidelity
    from app.ai.openai_client import AIServiceError
    from examples.ai_demo.mock_gateway import png_bytes
    c,app,h,p,image=agents_app;setup();app.state.settings.photo_refinement_passes=1
    monkeypatch.setattr(photo_fidelity,'render_build',lambda *a,**k:png_bytes())
    class InterruptedGateway(MockGateway):
        def structured(self,role,instructions,payload,schema,images=(),**kwargs):
            if schema is PhotoGeometryReview: return schema.model_validate(comparison())
            if schema is SparseGeometryPatch: raise AIServiceError('AI_TIMEOUT','Synthetic provider timeout',True)
            return super().structured(role,instructions,payload,schema,images=images,**kwargs)
    monkeypatch.setattr(orchestrator,'ResponsesGateway',InterruptedGateway)
    result=c.post('/api/v1/ai/reconstruct-furniture',headers=h['operator'],json=dict(project_id=p,image_ids=[image]))
    result=c.get(result.json()['status_url'],headers=h['operator']).json()
    assert result['status']=='REVIEW_REQUIRED',result
    fidelity=result['result']['reference_fidelity']
    assert fidelity['status']=='NEEDS_VISUAL_REVIEW' and fidelity['error']['code']=='AI_TIMEOUT'
    assert not fidelity['exact_reconstruction_verified'] and not fidelity['review']['visible_geometry_matches']
    assert (app.state.settings.storage/result['job_id']/'cabinet.step').is_file()


@pytest.mark.parametrize('legacy',[False,True])
def test_actual_preview_review_correction_then_freeze_and_private_preview(agents_app,monkeypatch,legacy):
    from app.agents import orchestrator
    c,app,h,p,image=agents_app;setup()
    if legacy:
        old=c.post('/api/v1/ai/reconstruct-furniture',headers=h['operator'],json=dict(project_id=p,image_ids=[image]))
        old=c.get(old.json()['status_url'],headers=h['operator']).json()
        assert old['status']=='REVIEW_REQUIRED',old
        with app.state.db.sessions.begin() as s:
            workflow=s.get(AgentWorkflow,old['id']);result=dict(workflow.result);result.pop('reference_fidelity')
            workflow.result=result
        MockGateway.calls=[]
    app.state.settings.photo_refinement_passes=1
    reviews=[]
    class FidelityGateway(MockGateway):
        def structured(self,role,instructions,payload,schema,images=(),**kwargs):
            if schema is PhotoGeometryReview:
                assert len(images)==3 and all(x['image_url'].startswith('data:image/png;base64,') for x in images[-2:])
                result=comparison(bool(reviews));reviews.append(result)
                return schema.model_validate(result)
            if schema is SparseGeometryPatch:
                part=deepcopy(payload['previous_plan']['parts'][0]);part['dish_depth']=8
                return schema(summary='Synthetic seat correction',changes=[dict(part_id=part['part_id'],field='dish_depth',
                    section_index=None,value=8)],added_parts=[],removed_part_ids=[],added_values=[])
            return super().structured(role,instructions,payload,schema,images=images,**kwargs)
    monkeypatch.setattr(orchestrator,'ResponsesGateway',FidelityGateway)
    response=c.post('/api/v1/ai/reconstruct-furniture',headers=h['operator'],json=dict(project_id=p,image_ids=[image]))
    result=c.get(response.json()['status_url'],headers=h['operator']).json()
    assert result['status']=='REVIEW_REQUIRED',result
    fidelity=result['result']['reference_fidelity']
    assert fidelity['status']=='VISIBLE_MATCH_REVIEWED' and fidelity['iterations']==1
    assert not fidelity['exact_reconstruction_verified']
    assert result['result']['specification']['photo_design']['parts'][0]['dish_depth']==8
    assert len(reviews)==2
    if legacy: assert MockGateway.calls==[]  # Reuse the original plan; no fresh analysis/design or duplicate final review.
    filename=next(iter(fidelity['previews']['files']))
    url=f'/api/v1/agents/workflows/{result["id"]}/fidelity/{filename}'
    assert c.get(url).status_code==401
    raster=c.get(url,headers=h['operator']);assert raster.status_code==200
    with Image.open(BytesIO(raster.content)) as im: assert im.size==(620,700)
    with app.state.db.sessions() as s: validate_estimated_revision(s.get(Revision,result['revision_id']),s)


@pytest.mark.parametrize('kind',['loft','ellipse_sweep'])
def test_real_rounded_profiles_and_caps_are_valid(kind):
    part=dict(part_id='F001',label='Synthetic rounded member',evidence='Synthetic test',geometry=kind,
        material='solid_wood',color_rgb=[.5,.3,.1],size=[30,20,100],position=[200,200,200])
    if kind=='loft': part['sections']=[dict(center=[0,0,0],width=40,depth=30,corner_radius=4),dict(center=[10,5,150],width=28,depth=22,corner_radius=3)]
    else: part.update(path=[[0,0,0],[20,0,30],[30,30,60],[0,50,100]],curve_mode='bezier',rounded_ends=True)
    plan=photo_plan_fixture();plan.parts=[PhotoPart(**part)]
    shape=create_photo_furniture(specification(plan,{},'Rounded member')[0]).shapes['F001'].val()
    assert shape.isValid() and shape.Volume()>0 and len(shape.Solids())==1


def test_glb_has_unit_normals_and_depth_preview_contains_actual_parts(tmp_path):
    build=create_photo_furniture(specification(photo_plan_fixture(),{},'Normals test')[0])
    path=tmp_path/'model.glb';export_glb(build,path,.25);data=path.read_bytes()
    size,_=struct.unpack_from('<II',data,12);gltf=json.loads(data[20:20+size]);binary_offset=20+size+8
    for mesh in gltf['meshes']:
        accessor=gltf['accessors'][mesh['primitives'][0]['attributes']['NORMAL']]
        view=gltf['bufferViews'][accessor['bufferView']]
        normals=np.frombuffer(data,dtype='<f4',count=accessor['count']*3,offset=binary_offset+view['byteOffset']).reshape(-1,3)
        assert np.allclose(np.linalg.norm(normals,axis=1),1,atol=1e-5)
    with Image.open(BytesIO(render_build(build))) as im:
        pixels=np.array(im);assert np.count_nonzero(np.any(pixels[:650]!=244,axis=2))>10000


def test_sculpted_surface_has_two_real_hollows_and_a_higher_central_transition():
    import cadquery as cq
    from pydantic import ValidationError
    part=dict(part_id='F001',label='Synthetic sculpted seat',evidence='Synthetic surface test',geometry='box',
        material='solid_wood',color_rgb=[.5,.3,.1],size=[300,240,30],position=[300,300,300],
        surface_dishes=[dict(center=[x,0],radius_x=130,radius_y=150,radius_z=40,depth=7) for x in (-65,65)])
    plan=photo_plan_fixture();plan.parts=[PhotoPart(**part)]
    shape=create_photo_furniture(specification(plan,{},'Sculpted surface')[0]).shapes['F001'].val()
    assert shape.isValid() and len(shape.Solids())==1 and shape.Volume()<300*240*30
    def top(x):
        probe=cq.Workplane('XY').box(1,1,100).translate((x,300,300)).val()
        return exact_bounds(shape.intersect(probe)).zmax
    assert top(300)>top(235)+3 and top(300)>top(365)+3
    invalid=plan.model_dump(mode='json');invalid['parts'][0]['surface_dishes'][0]['depth']=24
    with pytest.raises(ValidationError,match='continuous seat'): PhotoDesign.model_validate(invalid)
