"""Reopen a generated .blend in Blender and verify retained editable scene data."""
import argparse
import json
from pathlib import Path
import sys
import bpy

parser=argparse.ArgumentParser();parser.add_argument('--output',required=True)
args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
scene=bpy.context.scene
parts=[obj for obj in scene.objects if obj.type=='MESH' and obj.get('component_id')]
cameras=[obj.name for obj in scene.objects if obj.type=='CAMERA']
lights=[obj.name for obj in scene.objects if obj.type=='LIGHT']
assert scene.render.engine=='CYCLES' and parts and cameras and lights
assert len({obj['component_id'] for obj in parts})==len(parts)
assert all(obj.parent is not None and len(obj.data.materials)==1 and obj.data.materials[0].use_nodes for obj in parts)
materials=[dict(component=obj['component_id'],name=obj.data.materials[0].name,
    node_types=[node.bl_idname for node in obj.data.materials[0].node_tree.nodes]) for obj in parts]
report=dict(valid_editable_scene=True,engine=scene.render.engine,components=[obj['component_id'] for obj in parts],
    cameras=cameras,lights=lights,materials=materials,units=scene.unit_settings.system,scale_length=scene.unit_settings.scale_length)
Path(args.output).write_text(json.dumps(report,indent=2),encoding='utf-8')
print('REOPENED_SCENE_AUDIT',len(parts),'components,',len(cameras),'cameras,',len(lights),'lights')
