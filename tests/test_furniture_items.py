"""Detached companions never change primary dimensions or bridge broken parts."""
import json
from pathlib import Path
import pytest
from pydantic import ValidationError
from app.photo_schema import PhotoDesign
from app.agents.photo_agent import specification,geometry_envelope
from app.photo_cad import create_photo_furniture
from app.furniture_items import dimensions_for,infer_saved_companion
from app.geometry_repairs import resolve_repaired_geometry
from app.engineering_cad import geometry_checks,check_supplied_dimensions
from tests.test_agent_workflows import agents_app
from tests.test_engineering_cad import engineering_app,table_plan,launch


def furniture_set():
    raw=table_plan(720).model_dump(mode='json')
    raw['values'] += [dict(key='ottoman_'+k+'_mm',value=v,explanation='Synthetic companion') for k,v in [('width',300),('depth',300),('height',400)]]
    for label,height,z in [('Ottoman top',150,325),('Ottoman body',250,125)]:
        raw['parts'].append(dict(part_id=f'F{len(raw["parts"])+1:03d}',label=label,geometry='box',size=[300,300,height],
            position=[0,-700,z],material='foam',color_rgb=[.5,.5,.5],evidence='Synthetic detached companion'))
    raw['separate_items']=[dict(item_id='A001',label='Detached ottoman',part_ids=['F006','F007'],
        width_key='ottoman_width_mm',depth_key='ottoman_depth_mm',height_key='ottoman_height_mm',evidence='Synthetic detached furniture')]
    return PhotoDesign.model_validate(raw)


def build(plan):
    spec,_=specification(plan,dict(known_values=[]),'Independent furniture regression')
    return create_photo_furniture(spec)


def test_detached_item_has_its_own_dimensions_and_connection_check():
    actual=build(furniture_set());proof=geometry_checks(actual)
    assert not proof['assembly_connected'] and proof['furniture_items_connected']
    assert geometry_envelope(actual)==pytest.approx(dict(width_mm=720,depth_mm=500,height_mm=750),abs=1e-5)
    assert dimensions_for(actual)['depth']==pytest.approx(1100)
    assert len(proof['separate_item_dimensions'])==3
    assert len(check_supplied_dimensions(actual,[dict(key='depth_mm',value=500),dict(key='ottoman_depth_mm',value=300)]))==2


@pytest.mark.parametrize('change',['duplicate','unknown','all_primary','shared_dimensions'])
def test_invalid_item_membership_or_dimension_scope_is_rejected(change):
    raw=furniture_set().model_dump(mode='json')
    if change=='duplicate':raw['separate_items'][0]['part_ids'].append('F006')
    elif change=='unknown':raw['separate_items'][0]['part_ids'].append('F099')
    elif change=='all_primary':raw['separate_items'][0]['part_ids']=[p['part_id'] for p in raw['parts']]
    else:raw['separate_items'][0]['depth_key']='depth_mm'
    with pytest.raises(ValidationError):PhotoDesign.model_validate(raw)


def test_companion_cannot_bridge_disconnected_parts_of_the_primary_furniture():
    raw=dict(furniture_type='Synthetic furniture set',summary='A detached block must not validate disconnected primary parts',
        values=[dict(key=k,value=v,explanation='Synthetic test') for k,v in [('width_mm',300),('depth_mm',100),('height_mm',150),
            ('companion_width_mm',100),('companion_depth_mm',100),('companion_height_mm',150)]],parts=[],assumptions=['Synthetic test'])
    for i,x in enumerate([-100,100,0],1):
        raw['parts'].append(dict(part_id=f'F{i:03d}',label='Block',geometry='box',size=[100,100,150],position=[x,0,75],
            material='solid_wood',color_rgb=[.5,.5,.5],evidence='Synthetic test'))
    raw['separate_items']=[dict(item_id='A001',label='Companion',part_ids=['F003'],width_key='companion_width_mm',
        depth_key='companion_depth_mm',height_key='companion_height_mm',evidence='Synthetic test')]
    proof=geometry_checks(build(PhotoDesign.model_validate(raw)))
    assert proof['assembly_connected'] and not proof['furniture_items_connected']
    assert not proof['furniture_items'][0]['connected']


def test_whole_furniture_exports_keep_primary_and_companion_dimensions_distinct(engineering_app,monkeypatch):
    monkeypatch.setattr('tests.test_engineering_cad.table_plan',lambda width=720:furniture_set())
    state=launch(engineering_app,output_scope='WHOLE_FURNITURE')
    assert state['status']=='REVIEW_REQUIRED',state.get('error')
    proof=state['result']['engineering_cad']['cad_export_agreement']
    assert proof['geometry_checks']['furniture_items_connected'] and not proof['geometry_checks']['assembly_connected']
    _,app,_,_,_=engineering_app
    sheet=json.loads((app.state.settings.storage/state['job_id']/'all_dimensions_REVIEW.json').read_text())
    assert sheet['overall_dimensions_mm']['depth']==pytest.approx(500)
    assert sheet['layout_dimensions_mm']['depth']==pytest.approx(1100)
    assert [v['value_mm'] for v in sheet['dimensions'] if v['purpose']=='overall depth']==[500,500]
    assert any(v['purpose']=='A001 depth' and v['value_mm']==300 for v in sheet['dimensions'])


def test_saved_sofa_scope_migration_requires_photo_evidence_and_preserves_parameters():
    plan=PhotoDesign.model_validate_json((Path(__file__).parent/'fixtures/sofa_with_ottoman.json').read_text())
    inputs=dict(output_scope='WHOLE_FURNITURE',source_photo_analysis=dict(summary='Curved sofa with a separate rounded ottoman'))
    migrated,report=infer_saved_companion(plan,inputs)
    assert migrated.values==plan.values and migrated.parts==plan.parts and not report['geometry_changed']
    assert migrated.separate_items[0].part_ids==['F012','F015']
    assert infer_saved_companion(plan,{**inputs,'engineering_reexport_of':'saved'}) is None
    assert infer_saved_companion(plan,{}) is None
