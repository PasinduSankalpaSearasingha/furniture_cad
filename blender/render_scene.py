"""Trusted headless script, fed only validated JSON from the backend."""
import argparse
import json
import math
from pathlib import Path
import sys
import bpy
from mathutils import Vector
sys.path.insert(0,str(Path(__file__).resolve().parent))
from materials import make_material


def bounds(objects):
    points=[obj.matrix_world@Vector(c) for obj in objects for c in obj.bound_box]
    return Vector([min(p[i] for p in points) for i in range(3)]),Vector([max(p[i] for p in points) for i in range(3)])


def aim(obj,target): obj.rotation_euler=(Vector(target)-obj.location).to_track_quat('-Z','Y').to_euler()


def devices(scene,requested):
    scene.cycles.device='CPU'
    if requested=='CPU': return 'CPU'
    prefs=bpy.context.preferences.addons['cycles'].preferences
    for backend in ('OPTIX','CUDA','HIP','ONEAPI','METAL'):
        try:
            prefs.compute_device_type=backend;prefs.get_devices()
            gpu=[d for d in prefs.devices if d.type!='CPU']
            if not gpu: continue
            for d in prefs.devices: d.use=d.type!='CPU'
            scene.cycles.device='GPU'
            return backend
        except (TypeError,RuntimeError): continue
    return 'CPU'


def main(config):
    output=Path(config['output']);scene=bpy.context.scene
    bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
    scene.unit_settings.system='METRIC';scene.unit_settings.scale_length=1
    bpy.ops.import_scene.gltf(filepath=config['input_glb'])
    bpy.context.view_layer.update()
    meshes=[o for o in scene.objects if o.type=='MESH']
    expected={p['part_id']:p for p in config['parts']}
    actual={o.name:o for o in meshes}
    if set(actual)!=set(expected):
        raise ValueError('Imported component names/count differ from the CAD registry: '+str(list(actual)))
    triangles=sum(sum(max(0,len(p.vertices)-2) for p in o.data.polygons) for o in meshes)
    if triangles>config['max_triangles']: raise ValueError('Render mesh exceeds configured triangle budget')
    estimated_mb=(triangles*384+config['width']*config['height']*192)/(1024*1024)
    if estimated_mb>config.get('memory_budget_mb',4096):
        raise ValueError('Estimated render working set exceeds the configured memory budget; reduce quality')
    checked=[]
    for name,obj in actual.items():
        part=expected[name];lo,hi=bounds([obj]);extent=hi-lo;center=(lo+hi)/2
        want=Vector([x*.001 for x in part['size_xyz_mm']]);want_center=Vector([x*.001 for x in part['center_xyz_mm']])
        # glTF importer converts its Y-up metre scene back to Blender's Z-up metre space.
        if any(abs(extent[i]-want[i])>max(.00075,want[i]*.0002) for i in range(3)):
            raise ValueError('GLB size/orientation mismatch for '+name)
        if any(abs(center[i]-want_center[i])>.001 for i in range(3)):
            raise ValueError('GLB placement mismatch for '+name)
        if obj.parent is None: raise ValueError('Assembly hierarchy missing for '+name)
        obj['component_id']=name;obj['engineering_source']='CadQuery/OpenCascade STEP; GLB is the render mesh'
        obj['material_category']=part['material'];obj['dimensions_mm']=part['size_xyz_mm']
        obj.data.materials.clear();obj.data.materials.append(make_material(part,config['options']['finish']))
        # Preserve the source tessellation and normals; do not distort the CAD mesh with modifiers.
        checked.append(dict(id=name,dimensions_m=list(extent),center_m=list(center),parent=obj.parent.name))
    lo,hi=bounds(meshes);center=(lo+hi)/2;size=hi-lo;span=max(size)
    bpy.ops.mesh.primitive_plane_add(size=span*200,location=(center.x,center.y,lo.z-.002))
    ground=bpy.context.object;ground.name='Studio_Ground'
    floor=bpy.data.materials.new('Warm studio floor');floor.diffuse_color=(.72,.71,.67,1)
    ground.data.materials.append(floor)
    world=bpy.data.worlds.new('Forma Studio Environment');world.use_nodes=True;scene.world=world
    background=world.node_tree.nodes.get('Background');background.inputs['Color'].default_value=(.52,.57,.64,1)
    background.inputs['Strength'].default_value=.25
    if config['hdri']:
        env=world.node_tree.nodes.new('ShaderNodeTexEnvironment');env.image=bpy.data.images.load(config['hdri']);env.image.pack()
        world.node_tree.links.new(env.outputs['Color'],background.inputs['Color'])
    product=config['options']['environment']=='product'
    for name,offset,power,scale in [('Key',(-1.4,-1.8,2.4),140,1.7),('Fill',(1.8,-.3,1.5),65,1.5),('Rim',(.4,1.8,2.2),180,1.1)]:
        data=bpy.data.lights.new(name,'AREA');data.energy=power*span*span*(1.2 if product else 1);data.shape='DISK';data.size=span*scale
        light=bpy.data.objects.new(name,data);scene.collection.objects.link(light);light.location=center+Vector(offset)*span;aim(light,center)
    scene.render.engine='CYCLES';scene.cycles.samples=config['samples'];scene.cycles.use_denoising=True
    scene.cycles.use_adaptive_sampling=True;scene.cycles.adaptive_threshold=.05
    scene.cycles.max_bounces=8;scene.cycles.transmission_bounces=6
    scene.render.threads_mode='FIXED';scene.render.threads=config['threads']
    scene.render.resolution_x=config['width'];scene.render.resolution_y=config['height'];scene.render.resolution_percentage=100
    scene.render.image_settings.file_format='PNG';scene.render.image_settings.color_mode='RGBA'
    scene.render.film_transparent=False
    selected_device=devices(scene,config['device']);fallback=selected_device=='CPU' and config['device']!='CPU'
    directions={'front':(0,-1,0),'back':(0,1,0),'side':(1,0,0),'top':(0,0,1),
                'isometric':(1,-1,1),'hero':(1.4,-2.0,1.2),'detail':(1,-2,.7)}
    cameras=[]
    for view in dict.fromkeys(config['options']['views']):
        camera_data=bpy.data.cameras.new('Camera_'+view);camera=bpy.data.objects.new('Camera_'+view,camera_data)
        scene.collection.objects.link(camera);cameras.append(camera)
        orthographic=(view in ('front','side','back','top','isometric')) if config['options']['projection']=='auto' else config['options']['projection']=='orthographic'
        camera_data.type='ORTHO' if orthographic else 'PERSP';camera_data.lens=config['options']['focal_length_mm']
        target=center.copy()
        if view=='detail': target.z=lo.z+size.z*.68
        direction=Vector(directions[view]).normalized()
        aspect=config['width']/config['height']
        distance=span*max(2.8,1.6/max(.3,math.tan(camera_data.angle_y/2)))
        camera.location=target+direction*distance;aim(camera,target)
        # Project all bounding-box corners into camera coordinates for reliable wide/tall framing.
        inv=camera.rotation_euler.to_matrix().transposed()
        corners=[inv@(Vector((x,y,z))-target) for x in (lo.x,hi.x) for y in (lo.y,hi.y) for z in (lo.z,hi.z)]
        needed=max(max(abs(c.x)*2 for c in corners),max(abs(c.y)*2 for c in corners)*aspect)
        camera_data.ortho_scale=needed*1.18
        if not orthographic:
            tan_x=camera_data.sensor_width/(2*camera_data.lens)
            tan_y=tan_x/aspect
            distance=max(c.z+max(abs(c.x)/tan_x,abs(c.y)/tan_y) for c in corners)*1.15
            camera.location=target+direction*distance
        scene.camera=camera;scene.render.filepath=str(output/f'render_{view}.png')
        try: bpy.ops.render.render(write_still=True)
        except RuntimeError:
            if scene.cycles.device!='GPU': raise
            scene.cycles.device='CPU';selected_device='CPU';fallback=True
            bpy.ops.render.render(write_still=True)
    scene.camera=next((c for c in cameras if c.name=='Camera_hero'),cameras[0])
    bpy.ops.wm.save_as_mainfile(filepath=str(output/'furniture_scene.blend'))
    report=dict(geometry_verified=True,component_count=len(meshes),components=checked,triangles=triangles,
        units='metres; Z up; no additional scale applied to GLB import',engine=scene.render.engine,
        blender_version=bpy.app.version_string,device=selected_device,gpu_fallback=fallback,
        device_requested=config['device'],estimated_working_set_mb=estimated_mb,memory_budget_mb=config.get('memory_budget_mb',4096),
        samples=config['samples'],resolution=[config['width'],config['height']],
        materials='Procedural PBR: grain, bump, roughness, transmission and metallic response; no external textures',
        hdri_used=bool(config['hdri']),approved_for_manufacturing=False)
    (output/'blender_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True)
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:]);main(json.loads(Path(args.config).read_text(encoding='utf-8')))
