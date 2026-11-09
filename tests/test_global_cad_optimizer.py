import numpy as np
import pytest
from app.cad_optimizer import optimize_mesh_cad
from app.mesh_to_cad import build_native,tessellate_build,export_native
from app.mesh_analyzer import MeshData
from app.mesh_cad_schema import DenseOptimizationOptions
from tests.global_mesh_fixtures import table_plan,backrest_plan,nurbs_panel_plan,cabinet_plan


def controls(parameter_ids,**changes):
    return dict(mode='economy',align_reference=False,coarse_points=5000,medium_points=10000,fine_points=15000,
        verify_points=10_000,max_evaluations=40,max_seconds=90,controls=dict(parameter_ids=parameter_ids),**changes)


def test_global_dimension_correction_keeps_confirmed_height_and_exports_step(tmp_path,monkeypatch):
    def no_camera(*args,**kwargs): raise AssertionError('Global fitting must not generate virtual cameras')
    monkeypatch.setattr('app.multiview_geometry.generate_cameras',no_camera)
    monkeypatch.setattr('app.adaptive_view_selector.select_views',no_camera)
    monkeypatch.setattr('httpx.Client.send',no_camera)
    monkeypatch.setattr('httpx.AsyncClient.send',no_camera)
    target=table_plan(700); initial=table_plan(650)
    reference=tessellate_build(build_native(target))
    result=optimize_mesh_cad(initial,reference,controls(['width_mm']),
        inputs={'known_values':[dict(key='height_mm',value=750)]})
    assert result.report['final']['symmetric_mean_mm']<result.report['initial']['symmetric_mean_mm']*.15,result.report
    assert next(v.value for v in result.plan.values if v.key=='height_mm')==750
    assert result.report['virtual_camera_evaluations']==0
    assert result.report['external_ai_calls']==0
    assert export_native(result.build,result.plan,tmp_path)['assembly_reopened']


def test_curved_backrest_control_point_correction_reduces_whole_surface_error():
    target=backrest_plan(65); initial=backrest_plan(45)
    reference=tessellate_build(build_native(target))
    result=optimize_mesh_cad(initial,reference,controls(['F001.path.1.1']))
    assert result.report['status']=='improved',result.report
    assert result.report['final']['symmetric_mean_mm']<result.report['initial']['symmetric_mean_mm']*.5
    assert result.report['solid_checks']['valid_closed_solids']


@pytest.mark.parametrize('parameter,initial',[
    ('F001.surface_nurbs.control_points.1.1.2',nurbs_panel_plan(center_z=20)),
    ('F001.surface_nurbs.weights.1.1',nurbs_panel_plan(center_weight=1.))])
def test_native_nurbs_control_and_rational_weight_fitting(parameter,initial,tmp_path):
    target=nurbs_panel_plan()
    reference=tessellate_build(build_native(target))
    result=optimize_mesh_cad(initial,reference,controls([parameter],normal_loss=False))
    assert result.report['status']=='improved',result.report
    assert result.report['final']['symmetric_mean_mm']<result.report['initial']['symmetric_mean_mm']*.7
    assert result.build.shapes['F001'].val().isValid()
    assert export_native(result.build,result.plan,tmp_path)['assembly_reopened']


def test_registration_changes_only_rigid_frame_before_shape_fitting():
    plan=table_plan()
    reference=tessellate_build(build_native(plan))
    from scipy.spatial.transform import Rotation
    transform=np.eye(4);transform[:3,:3]=Rotation.from_euler('z',18,degrees=True).as_matrix();transform[:3,3]=[150,-80,25]
    reference.mesh.apply_transform(transform)
    result=optimize_mesh_cad(plan,reference,dict(enabled=False,verify_points=5000))
    assert result.plan==plan
    assert result.report['registration']['final_surface_mean_mm']<1
    assert not result.report['registration']['shape_parameters_optimized']
    actual=np.array(result.report['registration']['reference_to_cad_transform'])
    assert np.linalg.det(actual[:3,:3])==pytest.approx(1)


def test_known_missing_component_requires_structural_review_instead_of_deformation():
    target=table_plan()
    reference=tessellate_build(build_native(target))
    initial=target.model_copy(deep=True);initial.parts.pop()
    result=optimize_mesh_cad(initial,reference,controls(['width_mm']))
    assert result.report['structural_review_required']
    assert result.report['evaluations']==0 and len(result.plan.parts)==4
    assert result.report['final']['unmatched_reference_area_fraction']>0


def test_unchanged_native_components_are_reused_safely():
    cache={};plan=table_plan()
    before=build_native(plan,geometry_cache=cache)
    changed=plan.model_copy(deep=True);changed.parts[-1].position[0]='width_mm/2-65'
    after=build_native(changed,geometry_cache=cache)
    assert before.shapes['F001'].val().wrapped.IsSame(after.shapes['F001'].val().wrapped)
    assert not before.shapes['F005'].val().wrapped.IsSame(after.shapes['F005'].val().wrapped)


def test_progressive_stages_never_expand_the_original_parameter_bounds():
    target=cabinet_plan();initial=cabinet_plan(450)
    reference=tessellate_build(build_native(target))
    options=controls(['shelf_height_mm'])
    options.update(mode='quality',max_evaluations=60)
    result=optimize_mesh_cad(initial,reference,options)
    value=next(v.value for v in result.plan.values if v.key=='shelf_height_mm')
    assert value<=480.000001
    assert result.report['solid_checks']['valid_closed_solids']


def test_leg_angle_can_be_corrected_from_a_zero_initial_angle():
    from app.photo_schema import PhotoDesign
    target=PhotoDesign(furniture_type='synthetic tilted leg',summary='Native angle-control fixture',
        values=[dict(key=k,value=v,explanation='Fixture') for k,v in
                [('width_mm',500),('depth_mm',200),('height_mm',700)]],
        parts=[dict(part_id='F001',label='Tilted leg',geometry='box',size=[40,40,600],position=[0,0,300],
            rotation_deg=[0,6,0],material='solid_wood',color_rgb=[.6,.4,.2],evidence='Synthetic 6-degree leg')],
        assumptions=['Synthetic isolated control test'])
    initial=target.model_copy(deep=True);initial.parts[0].rotation_deg[1]=0
    reference=tessellate_build(build_native(target))
    options=controls(['F001.rotation_deg.1'])
    options['max_evaluations']=80
    result=optimize_mesh_cad(initial,reference,options)
    assert result.report['status']=='improved',result.report
    assert abs(result.plan.parts[0].rotation_deg[1]-6)<.5
