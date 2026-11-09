"""Real B-Rep repairs of failed curves before another AI request is needed."""
import copy
import json
from pathlib import Path
import cadquery as cq
import pytest
from app import loft_repair
from app.loft_repair import build_bounded_loft,profile_wire
from app.photo_cad import bound_loft
from app.geometry import exact_bounds
from app.photo_schema import PhotoDesign
from app.engineering_schema import EngineeringDimensions
from app.database import AgentWorkflow
from tests.test_agent_workflows import agents_app
from tests.test_engineering_cad import engineering_app,EngineeringGateway,launch


def sections():return json.loads((Path(__file__).parent/'fixtures/rounded_arm_loft.json').read_text())['sections']


def test_real_arm_is_rebuilt_locally_without_changing_original_sections_or_radii(tmp_path):
    profiles=sections();unchanged=copy.deepcopy(profiles);wires=[profile_wire(s) for s in profiles]
    default=cq.Workplane('XY').newObject([cq.Solid.makeLoft(wires,False)])
    with pytest.raises(ValueError,match='outside its section envelope'):bound_loft(default,wires,'ARM')
    result,reports=build_bounded_loft(wires,profiles,'ARM',bound_loft)
    assert profiles==unchanged and result.val().isValid() and len(result.val().Solids())==1
    repair=next(r for r in reports if r['kind']=='LOCAL_LOFT_REBUILD')
    assert repair['additional_ai_requests']==0 and not repair['parameter_values_changed']
    assert repair['original_corner_radii_retained'] and repair['maximum_sampled_section_gap_mm']<.005
    assert repair['local_attempts']<=loft_repair.MAX_LOCAL_ATTEMPTS
    for original in profiles:
        section=result.section(original['center'][2]).val();bounds=exact_bounds(section)
        assert bounds.xlen==pytest.approx(original['width'],abs=.005)
        assert bounds.ylen==pytest.approx(original['depth'],abs=.005)
        assert (bounds.xmin+bounds.xmax)/2==pytest.approx(original['center'][0],abs=.005)
        assert (bounds.ymin+bounds.ymax)/2==pytest.approx(original['center'][1],abs=.005)
    from app.engineering_cad import verify_solid_step
    path=tmp_path/'arm.step';cq.exporters.export(result,str(path));actual,proof=verify_solid_step(result.val(),path)
    assert proof['valid_closed_solid'] and proof['sampled_surface_max_gap_mm']<=.001


def test_shape_preserving_profiles_are_available_when_native_parameter_changes_fail(monkeypatch):
    original=loft_repair._native_loft;attempts=[]
    def native(wires,degree,parameterization):
        attempts.append(len(wires))
        if len(wires)==4:raise ValueError('Synthetic rejection of sparse global interpolation')
        return original(wires,degree,parameterization)
    monkeypatch.setattr(loft_repair,'_native_loft',native)
    profiles=sections();before=copy.deepcopy(profiles)
    result,reports=build_bounded_loft([profile_wire(s) for s in profiles],profiles,'ARM',bound_loft)
    repair=next(r for r in reports if r['kind']=='LOCAL_LOFT_REBUILD')
    assert repair['method'].startswith('SHAPE_PRESERVING_') and repair['constructed_section_count']>4
    assert result.val().isValid() and profiles==before and len(attempts)<=5
    assert repair['maximum_sampled_section_gap_mm']<=.005


def test_valid_existing_loft_keeps_its_original_construction(monkeypatch):
    profiles=[dict(center=[0,0,0],width=100,depth=80,profile='rectangle',corner_radius=10),
        dict(center=[5,0,100],width=80,depth=60,profile='rectangle',corner_radius=10)]
    wires=[profile_wire(p) for p in profiles];expected=cq.Solid.makeLoft(wires,False)
    def forbidden(*a,**k):raise AssertionError('A valid existing loft must not enter the repair ladder')
    monkeypatch.setattr(loft_repair,'_native_loft',forbidden)
    result,reports=build_bounded_loft(wires,profiles,'GOOD',bound_loft)
    assert not reports and result.val().Volume(1e-10)==pytest.approx(expected.Volume(1e-10),abs=1e-7)


def test_local_repairs_cannot_bypass_the_original_envelope_or_unlimitedly_retry(monkeypatch):
    profiles=sections();wires=[profile_wire(s) for s in profiles];calls=[]
    def reject(shape,targets,part_id):
        assert targets is wires,'Intermediate profiles must not enlarge the permitted original envelope'
        calls.append(True);raise ValueError('Synthetic original envelope rejection')
    with pytest.raises(ValueError,match='Local loft repair exhausted 5'):
        build_bounded_loft(wires,profiles,'ARM',reject)
    assert len(calls)==1+loft_repair.MAX_LOCAL_ATTEMPTS


def test_changed_section_shape_is_rejected_even_when_its_solid_and_bounds_pass(monkeypatch):
    profiles=sections();wires=[profile_wire(s) for s in profiles]
    def corrupt(shape,targets,part_id):return shape.translate((1,0,0)),None
    with pytest.raises(ValueError,match='changed a declared section'):
        # Force the default construction into the local ladder; every repaired
        # candidate receives a small displacement that only fidelity can catch.
        original=loft_repair._check_solid;first=[True]
        def check(shape):
            if first[0]:first[0]=False;raise ValueError('Synthetic initial construction failure')
            return original(shape)
        monkeypatch.setattr(loft_repair,'_check_solid',check)
        build_bounded_loft(wires,profiles,'ARM',corrupt)


def test_contact_trim_uses_actual_material_for_strict_dimensions_and_part_metadata():
    from app.agents.photo_agent import specification
    from app.photo_cad import create_photo_furniture
    from app.engineering_cad import check_supplied_dimensions
    from app.geometry import trimmed_bounds
    profiles=sections()
    plan=PhotoDesign(furniture_type='Synthetic upholstered base and arm',summary='Trimmed spline envelope regression',
        values=[dict(key=k,value=v,explanation='Synthetic supplied dimension') for k,v in
            dict(width_mm=3000,depth_mm=1450,height_mm=610).items()],
        parts=[dict(part_id='F001',label='Base',geometry='box',size=[3000,1450,210],position=[1500,725,105],
            material='fabric',color_rgb=[.5,.5,.5],evidence='Synthetic base beneath arm'),
            dict(part_id='F002',label='Rounded arm',geometry='loft',loft_mode='BOUNDED',size=[240,650,610],
                position=[2880,365,0],sections=profiles,material='fabric',color_rgb=[.5,.5,.5],
                evidence='Synthetic arm meets base at z=210')],assumptions=['Synthetic contact regression'])
    spec,_=specification(plan,dict(known_values=[]),'Trimmed curve test')
    build=create_photo_furniture(spec);arm=build.shapes['F002'].val()
    assert exact_bounds(arm).xmax>3003,'Fixture must reproduce the conservative untrimmed-spline overbound'
    actual=trimmed_bounds(arm)
    assert actual.xmin==pytest.approx(2760,abs=1e-5) and actual.xmax==pytest.approx(3000,abs=1e-5)
    assert actual.zmin==pytest.approx(210,abs=1e-5)
    part=next(p for p in build.registry if p.part_id=='F002')
    assert part.size_xyz_mm[0]==pytest.approx(240,abs=1e-5)
    assert part.blank_length_mm==pytest.approx(240,abs=1e-5)
    assert part.center_xyz_mm[0]==pytest.approx(2880,abs=1e-5)
    supplied=[dict(key='width_mm',value=3000),dict(key='depth_mm',value=1450),dict(key='height_mm',value=610)]
    proof=check_supplied_dimensions(build,supplied)
    assert all(p['error_mm']<.01 for p in proof)
    # The check must measure material rather than assuming the requested value.
    with pytest.raises(ValueError,match='Supplied width_mm'):
        check_supplied_dimensions(build,[dict(key='width_mm',value=3001)])


def test_whole_furniture_uses_one_plan_when_curve_repair_is_local(engineering_app,monkeypatch):
    profiles=sections()
    for p in profiles:p['center'][2]-=84
    values=dict(width_mm=720,depth_mm=700,height_mm=556,top_thickness_mm=30,arm_corner_radius_mm=75)
    part=dict(part_id='F002',label='Rounded sculpted pedestal',geometry='loft',loft_mode='BOUNDED',
        size=[240,650,526],sections=[dict(p,corner_radius='arm_corner_radius_mm') for p in profiles],
        material='solid_wood',color_rgb=[.4,.3,.2],evidence='Synthetic rounded-profile pedestal regression')
    plan=PhotoDesign(furniture_type='Synthetic sculpted pedestal table',summary='Local loft reconstruction regression',
        values=[dict(key=k,value=v,explanation='Synthetic dimension') for k,v in values.items()],
        parts=[dict(part_id='F001',label='Top',geometry='box',size=['width_mm','depth_mm','top_thickness_mm'],position=[0,0,'height_mm-top_thickness_mm/2'],
            material='solid_wood',color_rgb=[.6,.5,.3],evidence='Synthetic table top'),part],assumptions=['Synthetic geometry; no photo fidelity claim'])
    original=EngineeringGateway.structured
    def gateway(self,role,instructions,payload,schema,images=(),tools=None,**options):
        if schema is EngineeringDimensions:
            self.calls.append(schema.__name__)
            return schema(values=plan.values,assumptions=plan.assumptions,appearance=dict(summary='Synthetic top and pedestal',
                critical_features=[dict(feature_id='V001',label='Top and support',visible_shape='Top with broad rounded pedestal',relative_layout='Pedestal under top')],
                continuity=[],symmetry='Fixture only',avoid_substitutions=[]))
        if schema is PhotoDesign:
            self.calls.append(schema.__name__);checked=tools['cad_generation']['handler'](plan)
            assert checked['valid'],checked
            return plan
        return original(self,role,instructions,payload,schema,images=images,tools=tools,**options)
    monkeypatch.setattr(EngineeringGateway,'structured',gateway)
    state=launch(engineering_app,output_scope='WHOLE_FURNITURE')
    assert state['status']=='REVIEW_REQUIRED',state.get('error')
    assert EngineeringGateway.calls==['EngineeringDimensions','PhotoDesign','EngineeringReview']
    repairs=state['result']['engineering_cad']['workflow']['geometry_repairs']
    assert any(r['kind']=='LOCAL_LOFT_REBUILD' and r['additional_ai_requests']==0 for r in repairs)
    client,app,headers,project,image=engineering_app
    with app.state.db.sessions() as session:
        saved=session.get(AgentWorkflow,state['id']).checkpoints['engineering_plan']['payload']
        assert saved['parts'][1]['sections']==plan.model_dump(mode='json')['parts'][1]['sections']
