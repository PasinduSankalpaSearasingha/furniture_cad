"""Reference fitting is checked against independent projected targets and actual B-Reps."""
import base64
import copy
import json
import math
import time
from io import BytesIO
from uuid import uuid4
import numpy as np
import pytest
from PIL import Image,ImageDraw
from app.ai.reference_mask import foreground,png_bytes,decode_mask
from app.ai.reference_fitting import (MeshPart,frame_for,render_mask,prepare_target,initial_camera,
    fit_camera,fit_plan,actions_for,adjusted_plan,mesh_from_build)
from app.reference_fit_schema import ReferenceFitOptions,FitView
from app.photo_schema import PhotoPart,PhotoDesign
from app.agents.photo_agent import specification,preflight
from app.agents.reference_fit_agent import require_improvement
from app.pipeline import canonical_hash,file_hash
from app.database import Revision,AIInvocation
from sqlalchemy import select
from tests.test_agent_workflows import agents_app
from tests.test_studio import setup_studio,workflow_status


def encoded(array):return base64.b64encode(png_bytes(array.astype(np.uint8)*255)).decode()


def simple_plan():
    parts=[PhotoPart(part_id=f'F{i+1:03d}',label='Synthetic upright',evidence='Synthetic test only',geometry='box',
        material='solid_wood',color_rgb=[.5,.3,.1],size=size,position=position)
        for i,(size,position) in enumerate([([80,80,600],[-130,0,300]),([80,80,600],[130,0,300]),([340,100,60],[0,0,600])])]
    return PhotoDesign(furniture_type='synthetic frame',summary='Independent fitting fixture',
        values=[dict(key=k,value=v,explanation='Synthetic estimate') for k,v in [('width_mm',340),('depth_mm',100),('height_mm',630)]],
        parts=parts,assumptions=['Synthetic fixture, not physical evidence'])


def test_segmentation_keeps_furniture_and_holes_and_rejects_empty_background():
    pixels=np.full((180,180,3),232,np.uint8);pixels[20:160,35:65]=[125,61,20];pixels[20:160,115:145]=[125,61,20];pixels[20:45,35:145]=[125,61,20]
    result=foreground(png_bytes(pixels));assert result['usable'] and result['mask'][70,50] and not result['mask'][80,90]
    empty=foreground(png_bytes(np.full((100,100,3),255,np.uint8)))
    assert not empty['usable'] and not empty['mask'].any()
    guided=foreground(png_bytes(pixels),strokes=[dict(kind='background',points=[(.28,.5)],radius=.06)])
    assert not guided['mask'][90,50] and guided['method']=='USER_GUIDED_GRABCUT'


@pytest.mark.parametrize('bad',['not base64',encoded(np.zeros((30,60),bool))])
def test_mask_input_validation(bad):
    with pytest.raises(ValueError):decode_mask(bad,1,(96,96))


def test_perspective_camera_fitting_improves_independent_target():
    plan=simple_plan();spec,_=specification(plan,{},'Fixture');mesh=mesh_from_build(preflight(spec,set()),plan);frame=frame_for(mesh)
    true_camera={**frame,'azimuth_deg':-80.,'elevation_deg':18.,'roll_deg':2.,'inverse_distance':.12,
        'log_scale':math.log(66),'pan_x':.53,'pan_y':.51,'viewport':[160,160]}
    target_mask=render_mask(mesh,true_camera,(160,160));pixels=np.full((160,160,3),220,np.uint8)
    target=prepare_target(png_bytes(pixels),FitView(image_id=uuid4(),mask_png=encoded(target_mask)),160)
    result=fit_camera(mesh,target,frame,dict(azimuth_deg=-110,elevation_deg=10),ReferenceFitOptions(camera_evaluations=180),time.monotonic()+20)
    assert result['camera_metrics']['loss']<result['original_metrics']['loss']-.015
    from app.ai.reference_fitting import project
    points=next(iter(mesh.values())).points
    assert np.allclose(project(points,result['camera'],(320,320)),2*project(points,result['camera'],(160,160)))
    assert result['evaluations']<=180


def test_proxy_proposal_requires_an_improvement_after_real_cad_rebuilding(monkeypatch):
    from app.ai import reference_fitting as fitting
    source=simple_plan();spec,_=specification(source,{},'Fixture');build=preflight(spec,set());mesh=mesh_from_build(build,source)
    frame=frame_for(mesh);camera={**frame,'azimuth_deg':-90.,'elevation_deg':15.,'roll_deg':0.,'inverse_distance':.1,
        'log_scale':math.log(70),'pan_x':.5,'pan_y':.5,'viewport':[128,128]}
    raw=source.model_dump(mode='json');raw['parts'][1]['position'][0]-=12;expected=PhotoDesign.model_validate(raw)
    spec,_=specification(expected,{},'Fixture');target_mesh=mesh_from_build(preflight(spec,set(),check_estimates=False),expected)
    mask=render_mask(target_mesh,camera,(128,128))
    def fixed_camera(mesh,target,*args):
        m=fitting.metrics(render_mask(mesh,camera,target['size']),target)
        return dict(camera=camera,original_camera=camera,original_metrics=m,camera_metrics=m,evaluations=0)
    monkeypatch.setattr(fitting,'fit_camera',fixed_camera)
    options=ReferenceFitOptions(resolution=128,geometry_evaluations=40,component_ids=['F002'],cad_candidates=1,max_seconds=20)
    view=FitView(image_id=uuid4(),mask_png=encoded(mask))
    selected,report,_,_=fit_plan(source,{},'Fixture',[(view,png_bytes(np.full((128,128,3),220,np.uint8)))],options)
    assert report['geometry_changed'] and report['geometry_loss_after']<report['geometry_loss_before']-.001
    assert selected.parts[0]==source.parts[0] and selected.parts[1].position[0]<source.parts[1].position[0]
    from app.agents import photo_agent
    monkeypatch.setattr(photo_agent,'resolve_estimated_geometry',lambda candidate,*args:(candidate,spec,{},build,{}))
    unchanged,rejected,_,_=fit_plan(source,{},'Fixture',[(view,png_bytes(np.full((128,128,3),220,np.uint8)))],options)
    assert unchanged==source and not rejected['geometry_changed'] and rejected['rejections']


def test_fixed_dimensions_freeze_referencing_components_and_plan_is_immutable():
    plan=simple_plan();raw=plan.model_dump(mode='json');raw['parts'][0]['size'][0]='width_mm/4';plan=PhotoDesign.model_validate(raw)
    spec,_=specification(plan,{},'Fixture');mesh=mesh_from_build(preflight(spec,set()),plan)
    from app.ai.reference_fitting import frame_for
    target=prepare_target(png_bytes(np.full((100,100,3),220,np.uint8)),FitView(image_id=uuid4(),mask_png=encoded(np.pad(np.ones((60,40)),((20,20),(30,30))))),96)
    camera=initial_camera(mesh,target,frame_for(mesh),-100,10)
    original=canonical_hash(plan.model_dump(mode='json'))
    actions,locked=actions_for(plan,mesh,{'known_values':[dict(key='width_mm',value=340)]},ReferenceFitOptions(),[target],[camera])
    assert 'F001' in locked and all(a['part_id']!='F001' for a in actions)
    adjusted_plan(plan,actions,np.ones(len(actions))*.5)
    assert canonical_hash(plan.model_dump(mode='json'))==original


def test_multiview_or_landmark_regressions_are_rejected():
    base=[dict(loss=.4,landmark_error_fraction=.01),dict(loss=.3,landmark_error_fraction=None)]
    require_improvement(base,[dict(loss=.3,landmark_error_fraction=.01),dict(loss=.29,landmark_error_fraction=None)])
    for after in [[dict(loss=.2,landmark_error_fraction=.01),dict(loss=.32,landmark_error_fraction=None)],
                  [dict(loss=.2,landmark_error_fraction=.02),dict(loss=.29,landmark_error_fraction=None)],base]:
        with pytest.raises(ValueError):require_improvement(base,after)


def test_sculpted_panel_and_variable_rail_are_single_valid_solids():
    plan=simple_plan();raw=plan.model_dump(mode='json');raw['parts']=raw['parts'][:1]
    raw['parts'][0].update(size=[500,440,30],position=[0,0,400],surface_grid=dict(depths=[[0,1,0],[2,9,2],[0,1,0]]))
    sculpted=PhotoDesign.model_validate(raw);spec,_=specification(sculpted,{},'Sculpted panel');build=preflight(spec,set(),check_estimates=False)
    solid=build.shapes['F001'].val()
    assert solid.isValid() and len(solid.Solids())==1 and 0<solid.Volume()<500*440*30
    assert solid.BoundingBox().zmin==pytest.approx(385,abs=.01)
    assert any(face.geomType()=='BSPLINE' for face in solid.Faces())
    raw['parts'][0].pop('surface_grid');raw['parts'][0].update(geometry='profile_sweep',position=[0,0,0],
        curve_mode='bezier',path=[[-220,0,700],[-220,300,780],[220,300,780],[220,0,700]],
        sweep_sections=[dict(fraction=f,width=w,depth=d) for f,w,d in [(0,.4,.4),(.05,22,15),(.2,48,30),(.5,50,32),(.8,48,30),(.95,22,15),(1,.4,.4)]])
    rail=PhotoDesign.model_validate(raw);spec,_=specification(rail,{},'Variable rail');build=preflight(spec,set(),check_estimates=False)
    solid=build.shapes['F001'].val();assert solid.isValid() and len(solid.Solids())==1 and solid.Volume()>10000
    raw['parts'][0]['sweep_sections'][2]['fraction']=0
    with pytest.raises(ValueError):PhotoDesign.model_validate(raw)


def test_local_fit_works_without_ai_and_artifacts_are_private_and_immutable(agents_app,monkeypatch):
    setup_studio(monkeypatch);c,app,h,p,image=agents_app
    built=workflow_status(c,h,c.post(f'/api/projects/{p}/generate-cad',headers=h['operator'],json=dict(image_ids=[image],auto_render=False)))
    assert built['status']=='REVIEW_REQUIRED',built
    with app.state.db.sessions() as s:
        source=s.get(Revision,built['revision_id']);original=source.spec_hash;before=len(list(s.scalars(select(AIInvocation))))
    app.state.settings.ai_enabled=False
    from app.agents import orchestrator
    def no_gateway(*args,**kwargs):raise AssertionError('Local fit must not construct an AI gateway')
    monkeypatch.setattr(orchestrator,'ResponsesGateway',no_gateway)
    prepared=c.post(f'/api/projects/{p}/reference-mask',headers=h['operator'],json=dict(image_id=image))
    assert prepared.status_code==200 and prepared.json()['mask_png'].startswith('data:image/png;base64,')
    mask=np.zeros((128,96),bool);mask[10:118,20:76]=True
    options=dict(views=[dict(image_id=image,mask_png=encoded(mask),mask_reviewed=True)],camera_evaluations=24,geometry_evaluations=0,max_seconds=10)
    response=c.post(f'/api/projects/{p}/fit-reference',headers=h['operator'],json=dict(reference_fit=options))
    fitted=workflow_status(c,h,response);assert fitted['status']=='REVIEW_REQUIRED',fitted
    assert fitted['revision_id']!=built['revision_id'] and fitted['result']['reference_fidelity']['status']=='LOCAL_FIT_REVIEW_REQUIRED'
    report=fitted['result']['reference_fidelity']['numeric_fit'];assert report['views'][0]['mask_reviewed']
    filename=next(iter(report['files']));url=report['download_base']+'/'+filename
    assert c.get(url,headers=h['operator']).status_code==200 and c.get(url).status_code==401
    with app.state.db.sessions() as s:
        assert s.get(Revision,built['revision_id']).spec_hash==original
        assert len(list(s.scalars(select(AIInvocation))))==before
    path=app.state.settings.storage/report['directory']/filename;path.write_bytes(b'tampered')
    assert c.get(url,headers=h['operator']).status_code==409
    bad=c.post(f'/api/projects/{p}/fit-reference',headers=h['operator'],json=dict(parameter_changes=[dict(key='width_mm',value=800)]))
    assert bad.status_code==422
    wrong=copy.deepcopy(options);wrong['views'][0]['image_id']=str(uuid4())
    assert c.post(f'/api/projects/{p}/fit-reference',headers=h['operator'],json=dict(reference_fit=wrong)).status_code==404
    wrong['views'][0]['image_id']=image;wrong['views'][0]['mask_png']='invalid'
    assert c.post(f'/api/projects/{p}/fit-reference',headers=h['operator'],json=dict(reference_fit=wrong)).status_code==422
