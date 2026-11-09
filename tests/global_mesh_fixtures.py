from app.photo_schema import PhotoDesign
from app.mesh_to_cad import build_native,tessellate_build
from tests.test_engineering_cad import table_plan


def backrest_plan(control_depth=65):
    return PhotoDesign(furniture_type='synthetic curved backrest',summary='Known native swept curve fixture',
        values=[dict(key=key,value=value,explanation='Synthetic fixture') for key,value in
                [('width_mm',500),('depth_mm',200),('height_mm',700)]],
        parts=[dict(part_id='F001',label='Curved wooden backrest',evidence='Synthetic Bezier path',
            geometry='round_sweep',material='solid_wood',color_rgb=[.6,.4,.2],size=[30,30,30],curve_mode='bezier',
            path=[[-220,0,400],[-100,control_depth,580],[100,65,580],[220,0,400]])],
        assumptions=['Known synthetic geometry; no physical accuracy claim'])


def nurbs_panel_plan(center_z=30,center_weight=1.2):
    controls=[[[i*250-250,j*100-100,center_z if i==j==1 else 0] for j in range(3)] for i in range(3)]
    return PhotoDesign(furniture_type='synthetic rational back panel',summary='Known native rational surface fixture',
        values=[dict(key=key,value=value,explanation='Synthetic fixture') for key,value in
                [('width_mm',500),('depth_mm',200),('height_mm',700)]],
        parts=[dict(part_id='F001',label='Rational curved backrest',evidence='Synthetic rational surface controls',
            geometry='nurbs_panel',material='solid_wood',color_rgb=[.6,.4,.2],size=[500,200,12],
            surface_nurbs=dict(control_points=controls,weights=[[1,1,1],[1,center_weight,1],[1,1,1]]))],
        assumptions=['Known single-patch NURBS geometry; no physical accuracy claim'])


def cabinet_plan(shelf_height=500):
    values=dict(width_mm=700,depth_mm=450,height_mm=1000,shelf_height_mm=shelf_height)
    parts=[]
    def add(label,size,position):
        parts.append(dict(part_id=f'F{len(parts)+1:03d}',label=label,geometry='box',material='plywood',
            color_rgb=[.6,.4,.2],evidence='Synthetic panel fixture',size=size,position=position))
    add('Left panel',[18,'depth_mm','height_mm'],['-width_mm/2+9',0,'height_mm/2'])
    add('Right panel',[18,'depth_mm','height_mm'],['width_mm/2-9',0,'height_mm/2'])
    add('Top',['width_mm-36','depth_mm',18],[0,0,'height_mm-9'])
    add('Bottom',['width_mm-36','depth_mm',18],[0,0,9])
    add('Back',['width_mm-36',18,'height_mm-36'],[0,'depth_mm/2-9','height_mm/2'])
    add('Shelf',['width_mm-36','depth_mm-18',18],[0,-9,'shelf_height_mm'])
    return PhotoDesign(furniture_type='synthetic cabinet',summary='Open front and visible internal shelf',
        values=[dict(key=key,value=value,explanation='Known fixture') for key,value in values.items()],parts=parts,
        assumptions=['Synthetic panel geometry; physical construction unverified'])
