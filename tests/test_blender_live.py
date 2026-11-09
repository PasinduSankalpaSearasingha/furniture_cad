"""Opt-in integration: actual Blender executable, synthetic source geometry, no paid AI."""
import json
import os
from zipfile import ZipFile
import pytest
from app.rendering.blender_service import render_geometry,discover_blender
from app.rendering.config import RenderOptions
from app.config import Settings
from app.agents.photo_agent import specification
from app.photo_cad import create_photo_furniture
from app.cad import export_cad
from tests.photo_fixtures import photo_plan_fixture


@pytest.mark.skipif(os.getenv('BLENDER_INTEGRATION_TESTS')!='1',reason='Set BLENDER_INTEGRATION_TESTS=1 to run installed Blender')
def test_actual_cad_to_blender_units_components_cycles_png_and_editable_scene(tmp_path):
    settings=Settings(blender_device='CPU',blender_width=1280,blender_height=960)
    discover_blender(settings.blender_executable)
    spec,_=specification(photo_plan_fixture(),{},'Synthetic Blender integration')
    build=create_photo_furniture(spec);source=tmp_path/'cad';source.mkdir();export_cad(build,source)
    (source/'parts_registry.json').write_text(json.dumps(build.registry.snapshot()),encoding='utf-8')
    output=tmp_path/'render'
    result=render_geometry(settings,source,output,RenderOptions(quality='draft',views=['hero','front'],finish='walnut'),{'synthetic_test':True})
    assert result['report']['component_count']==7 and result['report']['geometry_verified']
    assert result['report']['engine']=='CYCLES' and result['report']['device']=='CPU'
    assert result['report']['resolution']==[320,240]
    assert (output/'furniture_scene.blend').stat().st_size>1000
    with ZipFile(output/'blender_render_package.zip') as archive:
        assert archive.testzip() is None and 'furniture_scene.blend' in archive.namelist()
