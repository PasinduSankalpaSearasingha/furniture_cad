"""Trusted native Blender modeler. Input is resolved, bounded geometry JSON, never code."""
import argparse
import bisect
import json
import math
from pathlib import Path
import sys
import bpy
import bmesh
from mathutils import Vector,Matrix


def lerp(a,b,t):return a*(1-t)+b*t


def interpolate(rows,t):
    """Interpolating local cubic; endpoints and supplied stations are retained."""
    if len(rows)==2:return [lerp(a,b,t) for a,b in zip(*rows)]
    if len(rows)==3:
        # A quadratic interpolant matches a three-station smooth loft without
        # the artificial endpoint flattening of a cardinal cubic.
        a,b,c=rows;return [p*2*(t-.5)*(t-1)-q*4*t*(t-1)+r*2*t*(t-.5) for p,q,r in zip(a,b,c)]
    position=max(0,min(len(rows)-1,t*(len(rows)-1)));i=min(int(position),len(rows)-2);u=position-i
    a,b,c,d=[rows[max(0,min(len(rows)-1,j))] for j in (i-1,i,i+1,i+2)]
    # Linear endpoint extrapolation avoids flattening the end tangent.
    if i==0:a=[2*x-y for x,y in zip(b,c)]
    if i==len(rows)-2:d=[2*x-y for x,y in zip(c,b)]
    return [.5*((2*q)+(-p+r)*u+(2*p-5*q+4*r-s)*u*u+(-p+3*q-3*r+s)*u*u*u) for p,q,r,s in zip(a,b,c,d)]


def curve(points,mode,steps=32):
    if mode!='bezier':return [Vector(p) for p in points]
    result=[]
    for start in range(0,len(points)-1,3):
        a,b,c,d=[Vector(v) for v in points[start:start+4]]
        result.extend((1-t)**3*a+3*(1-t)**2*t*b+3*(1-t)*t*t*c+t**3*d
                      for t in (i/steps for i in range(steps)))
    return result+[Vector(points[-1])]


def make_mesh(name,points,faces):
    data=bpy.data.meshes.new(name+'_geometry');data.from_pydata(points,[],faces);data.update()
    obj=bpy.data.objects.new(name,data);bpy.context.scene.collection.objects.link(obj)
    bm=bmesh.new();bm.from_mesh(data);bmesh.ops.recalc_face_normals(bm,faces=list(bm.faces));bm.to_mesh(data);bm.free()
    return obj


def rings_mesh(name,rings):
    count=len(rings[0]);points=[tuple(p) for ring in rings for p in ring]
    faces=[tuple(reversed(range(count))),tuple((len(rings)-1)*count+j for j in range(count))]
    faces.extend((i*count+j,i*count+(j+1)%count,(i+1)*count+(j+1)%count,(i+1)*count+j)
                 for i in range(len(rings)-1) for j in range(count))
    return make_mesh(name,points,faces)


def rectangle(width,depth,radius=0,count=64):
    radius=min(radius,min(width,depth)/2-.00001)
    if radius<=.000001:
        # Equal perimeter samples keep section topology consistent with rounded sections.
        corners=[(width/2,-depth/2),(width/2,depth/2),(-width/2,depth/2),(-width/2,-depth/2)]
        return [tuple(lerp(a,b,(i%(count//4))/(count//4)) for a,b in zip(corners[i//(count//4)],corners[(i//(count//4)+1)%4])) for i in range(count)]
    points=[]
    for cx,cy,start in [(width/2-radius,-depth/2+radius,-90),(width/2-radius,depth/2-radius,0),
                        (-width/2+radius,depth/2-radius,90),(-width/2+radius,-depth/2+radius,180)]:
        # Eight arc and eight straight-edge samples per quadrant.
        for j in range(8):
            angle=math.radians(start+90*j/8);points.append((cx+radius*math.cos(angle),cy+radius*math.sin(angle)))
        angle=math.radians(start+90);end=(cx+radius*math.cos(angle),cy+radius*math.sin(angle))
        # Next arc's starting point, following the original contour.
        next_corner={-90:(width/2,depth/2-radius),0:(-width/2+radius,depth/2),90:(-width/2,-depth/2+radius),180:(width/2-radius,-depth/2)}[start]
        points.extend(tuple(lerp(a,b,j/8) for a,b in zip(end,next_corner)) for j in range(8))
    return points


def ellipse(width,depth,count=64):
    return [(width/2*math.cos(2*math.pi*i/count),depth/2*math.sin(2*math.pi*i/count)) for i in range(count)]


def active(obj):
    bpy.ops.object.select_all(action='DESELECT');obj.select_set(True);bpy.context.view_layer.objects.active=obj


def boolean(obj,cutter,operation='DIFFERENCE'):
    active(obj);modifier=obj.modifiers.new('Controlled geometry cut','BOOLEAN');modifier.operation=operation;modifier.solver='EXACT';modifier.object=cutter
    bpy.ops.object.modifier_apply(modifier=modifier.name)


def bevel(obj,radius,vertical=False):
    if not radius:return
    active(obj);modifier=obj.modifiers.new('Controlled edge rounding','BEVEL');modifier.width=radius;modifier.segments=4
    modifier.limit_method='ANGLE';modifier.angle_limit=.55
    bpy.ops.object.modifier_apply(modifier=modifier.name)


def sphere(name,center,size):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=96,ring_count=64,radius=1,location=center)
    obj=bpy.context.object;obj.name=name;obj.scale=Vector(size)/2
    active(obj);bpy.ops.object.transform_apply(location=False,rotation=False,scale=True);return obj


def top_grid(name,size,grid,resolution=40):
    sx,sy,sz=size;ny,nx=len(grid),len(grid[0]);points=[]
    for y in range(resolution+1):
        v=y/resolution
        for x in range(resolution+1):
            u=x/resolution
            rows=[[interpolate([[float(d)] for d in row],u)[0]] for row in grid]
            depth=max(0,min(sz*.8,interpolate(rows,v)[0]))
            points.append((-sx/2+sx*u,-sy/2+sy*v,sz/2-depth))
    faces=[];n=resolution+1
    faces.extend((y*n+x,y*n+x+1,(y+1)*n+x+1,(y+1)*n+x) for y in range(resolution) for x in range(resolution))
    boundary=[*range(n),*(y*n+n-1 for y in range(1,n)),*((n-1)*n+x for x in range(n-2,-1,-1)),*(y*n for y in range(n-2,0,-1))]
    bottom=[]
    for index in boundary:bottom.append(len(points));points.append((points[index][0],points[index][1],-sz/2-1))
    faces.append(tuple(reversed(bottom)))
    faces.extend((boundary[i],bottom[i],bottom[(i+1)%len(bottom)],boundary[(i+1)%len(boundary)]) for i in range(len(boundary)))
    return make_mesh(name,points,faces)


def build_part(part):
    name=part['part_id'];sx,sy,sz=part['size'];kind=part['geometry']
    if kind in ('box','outline_panel','cylinder'):
        if kind=='outline_panel':outline=[tuple(p[:2]) for p in curve([(*p,0) for p in part['outline']],part['curve_mode'])]
        elif kind=='cylinder':outline=ellipse(sx,sy,96)
        else:outline=rectangle(sx,sy,part['corner_radius']) if part['corner_radius'] else [(-sx/2,-sy/2),(sx/2,-sy/2),(sx/2,sy/2),(-sx/2,sy/2)]
        if (Vector(outline[0])-Vector(outline[-1])).length<1e-7:outline=outline[:-1]
        obj=rings_mesh(name,[[(*p,z) for p in outline] for z in (-sz/2,sz/2)])
        if part.get('surface_grid'):
            cutter=top_grid('Sculpted panel volume',part['size'],part['surface_grid']['depths'])
            boolean(obj,cutter,'INTERSECT');bpy.data.objects.remove(cutter,do_unlink=True)
    elif kind=='ellipsoid':obj=sphere(name,(0,0,0),part['size'])
    elif kind=='loft':
        sections=part['sections'];profiles=[]
        for section in sections:
            ring=ellipse(section['width'],section['depth']) if section['profile']=='ellipse' else rectangle(section['width'],section['depth'],section.get('corner_radius',0))
            profiles.append([v for point in ring for v in (point[0]+section['center'][0],point[1]+section['center'][1],section['center'][2])])
        rings=[]
        for i in range((len(sections)-1)*16+1):
            row=interpolate(profiles,i/((len(sections)-1)*16));rings.append([row[j:j+3] for j in range(0,len(row),3)])
        obj=rings_mesh(name,rings)
    else:
        path=curve(part['path'],part['curve_mode'],48)
        # Resample polyline segments as well as Bezier chains for varying sections.
        if part['curve_mode']!='bezier':
            path=[a.lerp(b,j/12) for a,b in zip(path,path[1:]) for j in range(12)]+[path[-1]]
        lengths=[0.]
        for a,b in zip(path,path[1:]):lengths.append(lengths[-1]+(b-a).length)
        if lengths[-1]<.001:raise ValueError('A swept path has zero length')
        rings=[]
        for i,p in enumerate(path):
            tangent=(path[min(i+1,len(path)-1)]-path[max(0,i-1)]).normalized()
            horizontal=Vector((0,0,1)).cross(tangent)
            if horizontal.length<1e-6:horizontal=Vector((1,0,0)).cross(tangent)
            horizontal.normalize();up=tangent.cross(horizontal).normalized()
            width,depth=sx,sx if kind=='round_sweep' else sy
            if kind=='profile_sweep':
                f=lengths[i]/lengths[-1];sections=part['sweep_sections'];station=min(len(sections)-2,max(0,bisect.bisect_right([s['fraction'] for s in sections],f)-1))
                a,b=sections[station:station+2];u=(f-a['fraction'])/(b['fraction']-a['fraction'])
                # Smooth section interpolation with positive dimensions.
                u=u*u*(3-2*u);width=lerp(a['width'],b['width'],u);depth=lerp(a['depth'],b['depth'],u)
            profile=rectangle(width,depth) if kind=='rect_sweep' else ellipse(width,depth)
            rings.append([p+horizontal*x+up*y for x,y in profile])
        obj=rings_mesh(name,rings)
        if part.get('rounded_ends'):bevel(obj,min(sx,sy if kind=='ellipse_sweep' else sx)*.16)
    if part['dish_depth']:
        radius=max(sx,sy)*1.8;cutter=sphere('Seat dish',(0,0,sz/2+radius-part['dish_depth']),(radius*2,)*3)
        boolean(obj,cutter);bpy.data.objects.remove(cutter,do_unlink=True)
    for dish in part.get('surface_dishes',[]):
        cutter=sphere('Sculpted seat cutter',(*dish['center'],sz/2+dish['radius_z']-dish['depth']),
                      [dish[k]*2 for k in ('radius_x','radius_y','radius_z')])
        boolean(obj,cutter);bpy.data.objects.remove(cutter,do_unlink=True)
    if part.get('edge_radius'):bevel(obj,part['edge_radius'])
    if part.get('features'):raise ValueError('Blender reconstruction does not yet model machining features; retain them in the CAD engineering workflow')
    active(obj)
    for axis,angle in enumerate(part['rotation_deg']):obj.rotation_euler[axis]=math.radians(angle)
    obj.location=part['position'];bpy.context.view_layer.update()
    return obj


def fitted_camera(scene,data,name,width,height):
    az,el,roll=[math.radians(data[k]) for k in ('azimuth_deg','elevation_deg','roll_deg')]
    toward=Vector((math.cos(el)*math.cos(az),math.cos(el)*math.sin(az),math.sin(el)))
    right=Vector((0,0,1)).cross(toward).normalized();up=toward.cross(right)
    right,up=right*math.cos(roll)+up*math.sin(roll),-right*math.sin(roll)+up*math.cos(roll)
    camera_data=bpy.data.cameras.new(name);camera=bpy.data.objects.new(name,camera_data);scene.collection.objects.link(camera)
    inverse=data['inverse_distance'];radius=data['radius']*.001;center=Vector(data['center'])*.001
    camera.location=center+toward*radius/(inverse if inverse>.00001 else .01)
    camera.rotation_euler=Matrix((right,up,toward)).transposed().to_euler()
    camera_data.sensor_fit='HORIZONTAL';camera_data.sensor_width=36
    scale=math.exp(data['log_scale'])*width/data.get('viewport',[width,height])[0]
    if inverse>.00001:camera_data.type='PERSP';camera_data.lens=36*scale/(inverse*width)
    else:camera_data.type='ORTHO';camera_data.ortho_scale=width*radius/scale
    camera_data.shift_x=.5-data['pan_x'];camera_data.shift_y=(data['pan_y']-.5)*height/width
    camera_data.clip_start=.0001;camera_data.clip_end=1000
    return camera


def main(config):
    output=Path(config['output']);scene=bpy.context.scene
    bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
    scene.unit_settings.system='METRIC';scene.unit_settings.scale_length=1
    root=bpy.data.objects.new('Photo_reconstruction_REVIEW',None);scene.collection.objects.link(root)
    root['plan_sha256']=config['plan_sha256'];root['source']='SOL_CONTROLLED_NATIVE_BLENDER_MODELING'
    objects=[];meshes={};report=[];triangles=0
    for part in config['plan']['parts']:
        obj=build_part(part)
        # Mirror the ordered contact trims used by the independent CAD builder.
        for previous in objects:
            alo,ahi=[Vector([min((obj.matrix_world@Vector(c))[i] for c in obj.bound_box) for i in range(3)]),Vector([max((obj.matrix_world@Vector(c))[i] for c in obj.bound_box) for i in range(3)])]
            blo,bhi=[Vector([min((previous.matrix_world@Vector(c))[i] for c in previous.bound_box) for i in range(3)]),Vector([max((previous.matrix_world@Vector(c))[i] for c in previous.bound_box) for i in range(3)])]
            if all(min(ahi[i],bhi[i])-max(alo[i],blo[i])>1e-5 for i in range(3)):boolean(obj,previous)
        # Bake placement in mm, then convert to metres for GLB and rendering.
        active(obj);bpy.ops.object.transform_apply(location=True,rotation=True,scale=True)
        bm=bmesh.new();bm.from_mesh(obj.data);bmesh.ops.recalc_face_normals(bm,faces=list(bm.faces))
        closed=all(e.is_manifold for e in bm.edges);volume=abs(bm.calc_volume())
        connected=set();pending=[next(iter(bm.verts))] if bm.verts else []
        while pending:
            vertex=pending.pop()
            if vertex in connected:continue
            connected.add(vertex);pending.extend(edge.other_vert(vertex) for edge in vertex.link_edges)
        closed=closed and len(connected)==len(bm.verts)
        bm.to_mesh(obj.data);bm.free()
        if not closed or volume<.001:raise ValueError(part['part_id']+': native Blender component is open, empty or disconnected')
        obj.data.calc_loop_triangles();points=[list(v.co) for v in obj.data.vertices];faces=[list(t.vertices) for t in obj.data.loop_triangles]
        triangles+=len(faces)
        if triangles>config['max_triangles']:raise ValueError('Native model exceeds the configured triangle budget')
        meshes[part['part_id']]=dict(points=points,triangles=faces)
        report.append(dict(part_id=part['part_id'],label=part['label'],native_geometry=part['geometry'],closed=closed,volume_mm3=volume,
                           minimum_mm=[min(p[i] for p in points) for i in range(3)],maximum_mm=[max(p[i] for p in points) for i in range(3)]))
        obj.parent=root;obj['component_id']=part['part_id'];obj['dimensions_status']='ESTIMATED_REVIEW'
        material=bpy.data.materials.new(part['part_id']+'_clay');material.diffuse_color=(*part['color_rgb'],1);material.use_nodes=True
        material.node_tree.nodes.get('Principled BSDF').inputs['Base Color'].default_value=(*part['color_rgb'],1)
        material.node_tree.nodes.get('Principled BSDF').inputs['Roughness'].default_value=.55
        # Boolean cutters can leave empty/inherited material slots. Assign the
        # photographed part's material to every resulting surface explicitly.
        obj.data.materials.clear();obj.data.materials.append(material)
        for polygon in obj.data.polygons:
            polygon.material_index=0;polygon.use_smooth=part['geometry'] not in ('box','outline_panel')
        objects.append(obj)
    for obj in objects:
        for v in obj.data.vertices:v.co*=.001
        obj.data.update()
    bpy.ops.object.select_all(action='DESELECT');root.select_set(True)
    for obj in objects:obj.select_set(True)
    bpy.ops.export_scene.gltf(filepath=str(output/'reconstruction.glb'),export_format='GLB',use_selection=True,export_yup=True)
    (output/'model_mesh.json').write_text(json.dumps(meshes,separators=(',',':')),encoding='utf-8')
    scene.render.engine='CYCLES';scene.cycles.samples=8;scene.cycles.use_denoising=True
    sys.path.insert(0,str(Path(__file__).resolve().parent));from render_scene import devices
    selected_device=devices(scene,config['device']);scene.render.threads_mode='FIXED';scene.render.threads=config['threads']
    world=bpy.data.worlds.new('Neutral modeling studio');scene.world=world;world.use_nodes=True;world.node_tree.nodes.get('Background').inputs['Strength'].default_value=.6
    for name,position,energy in [('Model_Key',(-2,-3,4),350),('Model_Fill',(3,1,3),180)]:
        light_data=bpy.data.lights.new(name,'AREA');light_data.energy=energy;light_data.shape='DISK';light_data.size=3
        light=bpy.data.objects.new(name,light_data);scene.collection.objects.link(light);light.location=position
    scene.render.image_settings.file_format='PNG';scene.render.film_transparent=True
    cameras=[];projection_errors=[]
    for index,view in enumerate(config['cameras']):
        width,height=view['size'];scene.render.resolution_x=width;scene.render.resolution_y=height;scene.render.resolution_percentage=100
        camera=fitted_camera(scene,view['camera'],'Photo_view_'+str(index),width,height);cameras.append(camera);scene.camera=camera
        bpy.context.view_layer.update()
        from bpy_extras.object_utils import world_to_camera_view
        data=view['camera'];az,el,roll=[math.radians(data[k]) for k in ('azimuth_deg','elevation_deg','roll_deg')]
        toward=Vector((math.cos(el)*math.cos(az),math.cos(el)*math.sin(az),math.sin(el)));right=Vector((0,0,1)).cross(toward).normalized();up=toward.cross(right)
        right,up=right*math.cos(roll)+up*math.sin(roll),-right*math.sin(roll)+up*math.cos(roll)
        scale=math.exp(data['log_scale'])*width/data['viewport'][0]
        for obj in objects:
            for vertex in list(obj.data.vertices)[::max(1,len(obj.data.vertices)//5)][:5]:
                world=obj.matrix_world@vertex.co;local=(world*1000-Vector(data['center']))/data['radius']
                divisor=1-local.dot(toward)*data['inverse_distance']
                expected=Vector((local.dot(right)/divisor*scale+data['pan_x']*width,-local.dot(up)/divisor*scale+data['pan_y']*height))
                actual=world_to_camera_view(scene,camera,world);projection_errors.append((expected-Vector((actual.x*width,(1-actual.y)*height))).length)
        if max(projection_errors)>.25:raise ValueError('Blender camera projection differs from the measured photo camera')
        scene.render.filepath=str(output/f'view_{index}.png')
        try:bpy.ops.render.render(write_still=True)
        except RuntimeError:
            if scene.cycles.device!='GPU':raise
            scene.cycles.device='CPU';selected_device='CPU';bpy.ops.render.render(write_still=True)
    scene.camera=cameras[0];bpy.ops.wm.save_as_mainfile(filepath=str(output/'reconstruction.blend'))
    result=dict(source='NATIVE_BLENDER_GEOMETRY',plan_sha256=config['plan_sha256'],component_count=len(objects),components=report,
        triangles=triangles,blender_version=bpy.app.version_string,device=selected_device,engine='CYCLES',
        units='model_mesh.json millimetres; GLB metres Y up; Blender metres Z up',exact_reconstruction_verified=False)
    result['maximum_camera_projection_error_pixels']=max(projection_errors,default=0)
    (output/'model_report.json').write_text(json.dumps(result,indent=2),encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True)
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:]);configuration=json.loads(Path(args.config).read_text(encoding='utf-8'))
    try:main(configuration)
    except Exception as exc:
        (Path(configuration['output'])/'model_failure.json').write_text(json.dumps(dict(message=str(exc)[:600])),encoding='utf-8')
        raise
