"""Depth-tested previews of actual CAD triangles, with smooth normals inside each CAD face."""
from io import BytesIO
import math
import numpy as np
from PIL import Image, ImageDraw
from app.cad_meshing import absolute_triangles,simplify_triangles,CADMeshError,CADTriangleBudgetError


def tessellation(shape,tolerance=.35,*,preview=False,report=None):
    # Keep the source mm tolerance. A dense mesh is reduced only for display,
    # with volume-preserving reduction and sampled deviation checks recorded.
    budget=20000 if preview else 500000
    for factor in ((1,2,4) if preview else (1,)):
        try:
            vertices,faces=absolute_triangles(shape,tolerance*factor,min(.12*factor**.5,.35),
                max_triangles=2_000_000,report=report)
            points=np.array(vertices,dtype=float)
            triangles=np.array(faces,dtype=np.int32)
            original_count=len(triangles)
            if original_count>budget:
                allowance=max(tolerance,float(np.ptp(points,axis=0).max())/(400 if preview else 1000))
                points,triangles=simplify_triangles(points,triangles,budget,allowance,report=report)
                if report is not None:report.update(source_triangle_count=original_count,triangle_count=len(triangles),
                    display_mesh_reduced=True)
            break
        except CADTriangleBudgetError:
            if factor==(4 if preview else 1):raise
    # OCCT duplicates vertices at face boundaries: averaging here smooths curves
    # within a face while retaining actual sharp edges between separate faces.
    normals=np.zeros_like(points)
    cross=np.cross(points[triangles[:,1]]-points[triangles[:,0]],points[triangles[:,2]]-points[triangles[:,0]])
    for corner in range(3): np.add.at(normals,triangles[:,corner],cross)
    lengths=np.linalg.norm(normals,axis=1,keepdims=True)
    normals[lengths[:,0]<1e-20]=[0,0,1]
    normals/=np.maximum(np.linalg.norm(normals,axis=1,keepdims=True),1e-20)
    return points,triangles,normals


def preview_meshes(build,report=None):
    meshes=[]
    for part in build.registry:
        details={}
        try:points,triangles,normals=tessellation(build.shapes[part.part_id].val(),.3,preview=True,report=details)
        except CADMeshError as exc:raise CADMeshError(f'{part.part_id} {part.label}: {exc}') from exc
        if report is not None:report[part.part_id]=details
        meshes.append((points,triangles,normals,np.array(part.color_rgb or [.56,.34,.18])))
    return meshes


def render_build(build,azimuth=-110,elevation=12,size=(620,700),camera=None,meshes=None,label='ACTUAL CAD / ESTIMATED'):
    """Depth-tested CAD evidence, optionally projected with the fitted camera."""
    if camera:azimuth,elevation=camera['azimuth_deg'],camera['elevation_deg']
    if meshes is None:meshes=preview_meshes(build)
    all_points=np.concatenate([m[0] for m in meshes]);center=(all_points.min(0)+all_points.max(0))/2
    az,el=math.radians(azimuth),math.radians(elevation)
    toward=np.array([math.cos(el)*math.cos(az),math.cos(el)*math.sin(az),math.sin(el)])
    right=np.cross([0.,0.,1.],toward);right/=np.linalg.norm(right)
    up=np.cross(toward,right)
    projected=np.column_stack(((all_points-center)@right,(all_points-center)@up))
    span=np.maximum(np.ptp(projected,axis=0),1)
    width,height=size;scale=min((width-60)/span[0],(height-85)/span[1])
    middle=(projected.min(0)+projected.max(0))/2
    depth=np.full((height,width),-np.inf)
    pixels=np.full((height,width,3),244,dtype=np.uint8)
    light=np.array([-.3,-.6,.85]);light/=np.linalg.norm(light)
    for points,triangles,normals,color in meshes:
        local=points-center
        xy=np.column_stack(((local@right-middle[0])*scale+width/2,
                            height/2-(local@up-middle[1])*scale-12))
        z=local@toward
        if camera:
            from .reference_fitting import project
            xy=project(points,camera,size)
            # Reciprocal distance interpolates linearly across perspective triangles.
            relative=(points-np.array(camera['center']))/camera['radius']
            z=1/(1-relative@toward*camera['inverse_distance']) if camera['inverse_distance']>1e-8 else relative@toward
        intensities=.42+.58*np.maximum(normals@light,0)
        for triangle in triangles:
            a,b,c=xy[triangle]
            lower=np.maximum(np.floor(np.min(xy[triangle],axis=0)).astype(int),0)
            upper=np.minimum(np.ceil(np.max(xy[triangle],axis=0)).astype(int),[width-1,height-1])
            x0,y0=lower;x1,y1=upper
            if x1<x0 or y1<y0: continue
            denominator=(b[1]-c[1])*(a[0]-c[0])+(c[0]-b[0])*(a[1]-c[1])
            if abs(denominator)<1e-10: continue
            yy,xx=np.mgrid[y0:y1+1,x0:x1+1];xx=xx+.5;yy=yy+.5
            wa=((b[1]-c[1])*(xx-c[0])+(c[0]-b[0])*(yy-c[1]))/denominator
            wb=((c[1]-a[1])*(xx-c[0])+(a[0]-c[0])*(yy-c[1]))/denominator
            wc=1-wa-wb
            zz=wa*z[triangle[0]]+wb*z[triangle[1]]+wc*z[triangle[2]]
            section=depth[y0:y1+1,x0:x1+1]
            mask=(wa>=-1e-7)&(wb>=-1e-7)&(wc>=-1e-7)&(zz>section)
            if not np.any(mask): continue
            shade=wa*intensities[triangle[0]]+wb*intensities[triangle[1]]+wc*intensities[triangle[2]]
            rgb=np.clip(color[None,None,:]*shade[:,:,None]*255,0,255).astype(np.uint8)
            pixels[y0:y1+1,x0:x1+1][mask]=rgb[mask];section[mask]=zz[mask]
    image=Image.fromarray(pixels)
    ImageDraw.Draw(image).text((15,height-25),f'{label}   azimuth {azimuth:g}, elevation {elevation:g}',fill=(65,65,65))
    result=BytesIO();image.save(result,format='PNG');return result.getvalue()


def preview_png(path,points,faces):
    import cv2
    from PIL import Image
    direction=np.array([1.8,-2.4,1.5]);direction/=np.linalg.norm(direction)
    right=np.cross([0,0,1],direction);right/=np.linalg.norm(right);up=np.cross(direction,right)
    center=(points.min(0)+points.max(0))/2;local=points-center
    xy=np.column_stack([local@right,-local@up]);span=np.ptp(xy,axis=0);scale=430/max(float(span.max()),1e-6)
    xy=(xy-(xy.min(0)+xy.max(0))/2)*scale+[256,256]
    depth=local@direction;tri=points[faces];normals=np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0])
    lengths=np.linalg.norm(normals,axis=1);normals/=np.maximum(lengths[:,None],1e-30)
    light=np.array([.3,-.6,.75]);light/=np.linalg.norm(light)
    shade=.42+.58*np.abs(normals@light);colors=np.asarray([154,111,69])*shade[:,None]
    image=np.full((512,512,3),[247,248,244],np.uint8);projected=np.rint(xy[faces]).astype(np.int32)
    for index in np.argsort(depth[faces].mean(1)):cv2.fillConvexPoly(image,projected[index],tuple(map(int,colors[index])))
    Image.fromarray(image).save(path)

