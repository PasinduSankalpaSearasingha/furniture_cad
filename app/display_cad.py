"""Deterministic decorative image study; all dimensions remain unverified."""
import math
import cadquery as cq
from .models import Material, Part
from .registry import PartsRegistry
from .geometry import exact_bounds


def display_component_ids(spec):
    groups = [('L',5),('U',13),('G',4),('D',8),('M',8),('T',10)]
    if spec.display_layout.include_ornaments: groups.append(('O',7))
    return {f'{prefix}{number:03}' for prefix,count in groups for number in range(1,count+1)}


def create_display_cabinet(spec):
    from .cad import BuildResult
    layout = spec.display_layout
    W,D,H = spec.width_mm,spec.depth_mm,spec.height_mm
    t,b = spec.panel_thickness_mm,spec.back_thickness_mm
    inset,p = layout.body_inset_mm,layout.post_width_mm
    x0,x1,y0,y1 = inset,W-inset,inset,D-inset
    bw,bd = x1-x0,y1-y0
    base,split = layout.base_height_mm,layout.lower_section_height_mm
    upper0,upper1 = split+layout.transition_height_mm,H-layout.cornice_height_mm
    wood = Material(spec.carcass_material)
    frame_wood = Material(spec.door_frame_wood)
    registry,shapes,local_shapes = PartsRegistry(),{},{}
    assembly = cq.Assembly(name='Two_section_image_study_ESTIMATED_REVIEW_ONLY')

    def put(pid,label,shape,material=wood,category='frame',length_axis=(0,0,1),normal=(0,-1,0),note=''):
        bb = exact_bounds(shape.val())
        center = ((bb.xmin+bb.xmax)/2,(bb.ymin+bb.ymax)/2,(bb.zmin+bb.zmax)/2)
        plane = cq.Plane(origin=center,xDir=length_axis,normal=normal)
        local = plane.toLocalCoords(shape.val())
        bounds = exact_bounds(local)
        blank = (bounds.xlen,bounds.ylen,bounds.zlen)
        part = Part(part_id=pid,label=label,material=material,category=category,
            blank_length_mm=blank[0],blank_width_mm=blank[1],blank_thickness_mm=blank[2],
            length_mm=blank[0],width_mm=blank[1],thickness_mm=blank[2],
            size_xyz_mm=(bb.xlen,bb.ylen,bb.zlen),center_xyz_mm=center,
            grain_axis='none' if material==Material.glass else 'length',revision=spec.revision,
            length_axis=length_axis,thickness_axis=normal,blank_verified=False,
            note='ESTIMATED PHOTO STUDY. '+note)
        registry.add(part)
        shapes[pid],local_shapes[pid] = shape,cq.Workplane('XY').newObject([local])
        color = cq.Color(0.55,0.32,0.14) if material!=Material.glass else cq.Color(0.68,0.82,0.83,0.30)
        assembly.add(shape,name=pid,color=color)

    def box(pid,label,size,center,**options):
        put(pid,label,cq.Workplane('XY').box(*size).translate(center),**options)

    # Lower closed carcass: five panels, with an independent front door.
    lower_h = split-base
    box('L001','Lower left panel',(t,bd,lower_h),(x0+t/2,D/2,base+lower_h/2),category='panel',normal=(1,0,0))
    box('L002','Lower right panel',(t,bd,lower_h),(x1-t/2,D/2,base+lower_h/2),category='panel',normal=(1,0,0))
    for pid,label,z in [('L003','Lower bottom panel',base+t/2),('L004','Lower top panel',split-t/2)]:
        box(pid,label,(bw-2*t,bd,t),(W/2,D/2,z),category='panel',length_axis=(1,0,0),normal=(0,0,1))
    box('L005','Lower back panel',(bw-2*t,b,lower_h-2*t),(W/2,y1-b/2,base+lower_h/2),category='panel')

    # Upper glazed frame: four posts, eight ring rails and a rear panel.
    index = 1
    for x in (x0+p/2,x1-p/2):
        for y in (y0+p/2,y1-p/2):
            box(f'U{index:03}','Upper corner post',(p,p,upper1-upper0),(x,y,(upper0+upper1)/2))
            index += 1
    for z in (upper0+p/2,upper1-p/2):
        for y in (y0+p/2,y1-p/2):
            box(f'U{index:03}','Upper front/rear rail',(bw-2*p,p,p),(W/2,y,z),length_axis=(1,0,0))
            index += 1
        for x in (x0+p/2,x1-p/2):
            box(f'U{index:03}','Upper side rail',(p,bd-2*p,p),(x,D/2,z),length_axis=(0,1,0),normal=(1,0,0))
            index += 1
    box('U013','Upper rear panel',(bw-2*p,b,upper1-upper0-2*p),
        (W/2,y1-b/2,(upper0+upper1)/2),category='panel')
    c = spec.glass_edge_clearance_mm if spec.glass_edge_clearance_mm is not None else 2
    for pid,x in [('G003',x0+p/2),('G004',x1-p/2)]:
        box(pid,'Upper side glass - visual only',(spec.glass_thickness_mm,bd-2*p-2*c,upper1-upper0-2*p-2*c),
            (x,D/2,(upper0+upper1)/2),material=Material.glass,category='glass',normal=(1,0,0),
            note='Glass type, retention, cut size and safety are unverified.')

    gap,fw,ft = spec.door_perimeter_gap_mm,spec.door_frame_width_mm,spec.door_frame_depth_mm
    front = y0-(spec.door_standoff_mm if spec.door_standoff_mm is not None else 1)-ft
    cy = front+ft/2
    windows = []
    for door,(left,right,bottom,top) in enumerate([
        (x0+t+gap,x1-t-gap,base+t+gap,split-t-gap),
        (x0+p+gap,x1-p-gap,upper0+p+gap,upper1-p-gap)]):
        dw,dh = right-left,top-bottom
        offset = door*4
        for n,x in enumerate((left+fw/2,right-fw/2),1):
            box(f'D{offset+n:03}',f'{"Lower" if door==0 else "Upper"} door stile',
                (fw,ft,dh),(x,cy,(bottom+top)/2),material=frame_wood)
        for n,z in enumerate((bottom+fw/2,top-fw/2),3):
            box(f'D{offset+n:03}',f'{"Lower" if door==0 else "Upper"} door rail',
                (dw-2*fw,ft,fw),(W/2,cy,z),material=frame_wood,length_axis=(1,0,0))
        box(f'G{door+1:03}',f'{"Lower" if door==0 else "Upper"} door glass - visual only',
            (dw-2*fw-2*c,spec.glass_thickness_mm,dh-2*fw-2*c),(W/2,cy,(bottom+top)/2),
            material=Material.glass,category='glass',note='No glazing rebates, retainers or certified glass specification.')
        windows.append((dw-2*fw-2*c,bottom+fw+c,top-fw-c))

    # Stepped plinth, waist and crown, contained in the stated overall envelope.
    tiers = [(0,base/3,.55),(base/3,base/3,.8),(2*base/3,base/3,.35),
             (split,layout.transition_height_mm/2,.4),
             (split+layout.transition_height_mm/2,layout.transition_height_mm/2,.7),
             (upper1,layout.cornice_height_mm*2/7,.35),
             (upper1+layout.cornice_height_mm*2/7,layout.cornice_height_mm*2/7,.7),
             (upper1+layout.cornice_height_mm*4/7,layout.cornice_height_mm*3/7,1)]
    for index,(z,height,amount) in enumerate(tiers,1):
        box(f'M{index:03}','Stepped plinth / waist / cornice',
            (bw+2*inset*amount,bd+2*inset*amount,height),(W/2,D/2,z+height/2),
            length_axis=(1,0,0),normal=(0,0,1),note='Stepped approximation; original moulding profile is unknown.')

    # Raised pilaster beads and cap blocks, without invented routed profiles.
    trim = 1
    for x in (x0+p/2,x1-p/2):
        for dx in (-p/5,p/5):
            box(f'T{trim:03}','Upper pilaster bead',(5,5,upper1-upper0-165),
                (x+dx,y0-2.5,(upper0+65+upper1-100)/2))
            trim += 1
        for z,height in [(upper0,60),(upper1-90,90)]:
            box(f'T{trim:03}','Upper pilaster cap',(p,10,height),(x,y0-5,z+height/2))
            trim += 1
    for x in (x0+t/2,x1-t/2):
        box(f'T{trim:03}','Lower front pilaster',(t,5,lower_h),(x,y0-2.5,base+lower_h/2))
        trim += 1

    def ornament(pid,origin,normal,x_direction,width,height):
        plane = cq.Plane(origin=origin,xDir=x_direction,normal=normal)
        strip = min(layout.ornament_strip_width_mm,width/8)
        length = math.hypot(width,height)
        shape = None
        for sign in (-1,1):
            dx,dz = width/2,sign*height/2
            nx,nz = -sign*height/length*strip/2,width/length*strip/2
            points = [(-dx+nx,-dz+nz),(dx+nx,dz+nz),(dx-nx,dz-nz),(-dx-nx,-dz-nz)]
            bar = cq.Workplane(plane).polyline(points).close().extrude(4,both=True)
            shape = bar if shape is None else shape.union(bar)
        r = strip*1.5
        diamond = cq.Workplane(plane).polyline([(0,r),(r,0),(0,-r),(-r,0)]).close().extrude(4,both=True)
        shape = shape.union(diamond).clean()
        put(pid,'Simplified crossed ornament',shape,material=frame_wood,normal=normal,
            note='Fused X/diamond silhouette. Original carving and attachment details are not reproduced; blank is the local envelope.')

    if layout.include_ornaments:
        oi = 1
        for door,(width,z0,z1) in enumerate(windows):
            centers = [(z0+z1)/2] if door==0 else [z0+(z1-z0)*.32,z0+(z1-z0)*.70]
            for z in centers:
                ornament(f'O{oi:03}',(W/2,cy-spec.glass_thickness_mm/2-4,z),(0,-1,0),(1,0,0),width*.84,width*.65)
                oi += 1
        side_width = bd-2*p-2*c
        for x,normal,xdir in [(x0+p/2-spec.glass_thickness_mm/2-4,(-1,0,0),(0,-1,0)),
                               (x1-p/2+spec.glass_thickness_mm/2+4,(1,0,0),(0,1,0))]:
            for fraction in (.32,.70):
                z = upper0+p+(upper1-upper0-2*p)*fraction
                ornament(f'O{oi:03}',(x,D/2,z),normal,xdir,side_width*.8,side_width*.7)
                oi += 1

    return BuildResult(assembly,list(registry),shapes,warnings=[
        'All dimensions estimated from a single photograph; no physical scale is established.',
        'Two-section layout, glazing, mouldings and ornaments are simplified review geometry.',
        'Hardware, machining joinery, glazing retention and manufacturing release are not designed.'],
        registry=registry,local_shapes=local_shapes)
