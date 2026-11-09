"""Bounded furniture construction. AI selects parameters; it never supplies code."""
import math
import cadquery as cq
from .models import Material, Part
from .registry import PartsRegistry
from .geometry import exact_bounds


def create_furniture(spec):
    from .cad import BuildResult
    W,D,H=spec.width_mm,spec.depth_mm,spec.height_mm
    t=spec.panel_thickness_mm
    p=spec.furniture_parameters
    registry,shapes,local=PartsRegistry(),{},{}
    assembly=cq.Assembly(name='Furniture_ESTIMATED_REVIEW')
    mat=Material(spec.carcass_material)
    trimmed=[]
    clipped=[]
    envelope=cq.Workplane('XY').box(W,D,H,centered=(False,False,False)).val()

    def put(label,solid,category='frame',material=None):
        pid=f'F{len(registry)+1:03d}'
        shape=solid.val() if isinstance(solid,cq.Workplane) else solid
        bb=exact_bounds(shape)
        if any(getattr(bb,k+'min') < -1e-6 or getattr(bb,k+'max') > limit+1e-6 for k,limit in zip(('x','y','z'),(W,D,H))):
            shape=shape.intersect(envelope).clean()
            clipped.append(pid)
        # Fixed template contacts are trimmed to flush mating faces. No tenons,
        # fasteners, bend allowances or load-bearing joints are implied.
        for other in shapes.values():
            a,b=exact_bounds(shape),exact_bounds(other.val())
            if all(min(getattr(a,k+'max'),getattr(b,k+'max'))-max(getattr(a,k+'min'),getattr(b,k+'min'))>1e-7 for k in ('x','y','z')):
                overlap=shape.intersect(other.val()).Volume()
                if overlap>1e-5:
                    shape=shape.cut(other.val()).clean()
                    trimmed.append(pid)
        if len(shape.Solids())!=1 or not shape.isValid() or shape.Volume()<=0:
            raise ValueError(f'Template proportions produced an invalid component: {label}. Adjust the dimensions or curve.')
        bb=exact_bounds(shape)
        xyz=(bb.xlen,bb.ylen,bb.zlen)
        center=((bb.xmin+bb.xmax)/2,(bb.ymin+bb.ymax)/2,(bb.zmin+bb.zmax)/2)
        axes=sorted(range(3),key=lambda i:xyz[i],reverse=True)
        vector=lambda i:tuple(1 if j==i else 0 for j in range(3))
        plane=cq.Plane(origin=center,xDir=vector(axes[0]),normal=vector(axes[2]))
        blank=tuple(xyz[i] for i in axes)
        part=Part(part_id=pid,label=label,material=material or mat,category=category,
            blank_length_mm=blank[0],blank_width_mm=blank[1],blank_thickness_mm=blank[2],
            length_mm=blank[0],width_mm=blank[1],thickness_mm=blank[2],
            size_xyz_mm=xyz,center_xyz_mm=center,revision=spec.revision,
            length_axis=vector(axes[0]),thickness_axis=vector(axes[2]),
            manufacturing_status='ESTIMATED_REVIEW',blank_verified=False,
            note='Estimated geometry. Blank is an enclosing envelope, without machining/bending allowances. Flush trimmed contacts need engineered joinery.')
        registry.add(part)
        shapes[pid]=cq.Workplane('XY').newObject([shape])
        local[pid]=cq.Workplane('XY').newObject([plane.toLocalCoords(shape)])
        assembly.add(shapes[pid],name=pid,color=cq.Color(0.61,0.39,0.22) if material!=Material.mdf else cq.Color(0.74,0.70,0.64))

    def box(label,xyz,center,category='panel'):
        if min(xyz)<=0: raise ValueError(f'Insufficient space for {label}')
        put(label,cq.Workplane('XY').box(*xyz).translate(center),category)

    def panel(label,w,d,z,thick,style='rectangle',x=None,y=None):
        x=W/2 if x is None else x
        y=D/2 if y is None else y
        if style=='ellipse': shape=cq.Workplane('XY').ellipse(w/2,d/2).extrude(thick)
        else:
            shape=cq.Workplane('XY').rect(w,d).extrude(thick)
            if style in ('rounded','sculpted'): shape=shape.edges('|Z').fillet(min(w,d)*0.12)
        if style=='sculpted':
            # Shallow spherical dish; a visual seat contour, not an ergonomic claim.
            radius=max(w,d)*1.8
            dish=cq.Workplane('XY').sphere(radius).translate((0,0,radius+thick-min(7,thick*0.20)))
            shape=shape.cut(dish)
        put(label,shape.translate((x,y,z)),category='panel')

    def leg(label,x,y,z0,z1,width=None,splay=True):
        lw=width or p.leg_width_mm
        sx=(p.leg_splay_mm if x<W/2 else -p.leg_splay_mm) if splay else 0
        sy=(p.leg_splay_mm if y<D/2 else -p.leg_splay_mm) if splay else 0
        profile=cq.Workplane('XY').workplane(offset=z0).center(x,y).rect(lw*p.leg_taper_ratio,lw*p.leg_taper_ratio)
        if p.leg_curve_mm and splay:
            bend=min(p.leg_curve_mm,min(W,D)*.06)*(1 if y<D/2 else -1)
            profile=profile.workplane(offset=(z1-z0)/2).center(sx/2,sy/2+bend).rect(lw*.85,lw*.85)
            shape=profile.workplane(offset=(z1-z0)/2).center(sx/2,sy/2-bend).rect(lw,lw).loft(combine=True)
        else: shape=profile.workplane(offset=z1-z0).center(sx,sy).rect(lw,lw).loft(combine=True)
        put(label,shape)

    def four_legs(z,lw=None):
        lw=lw or p.leg_width_mm
        inset=lw/2+min(p.leg_splay_mm+5,min(W,D)*0.12)
        for i,(x,y) in enumerate(((inset,inset),(W-inset,inset),(inset,D-inset),(W-inset,D-inset)),1):
            leg(f'Leg {i}',x,y,30 if spec.furniture_template=='rocking_chair' else 0,z,lw)

    def rail(label,points,diameter,wrap=False):
        # Circle normal follows the initial tangent of the actual spline path.
        if wrap:
            x0,x1,z=diameter/2,W-diameter/2,points[0][2]
            rear=D-diameter/2
            front=D*.22+min(p.curve_depth_mm,D*.20)
            arm_z=min(z,p.arm_height_mm) if p.armrests else z
            start=(x0,front,arm_z)
            left=[start,(x0,rear,arm_z),(W*.08,rear,z),(W*.3,rear,z)]
            right=[(W*.7,rear,z),(W*.92,rear,z),(x1,rear,arm_z),(x1,front,arm_z)]
            edges=[cq.Edge.makeBezier([cq.Vector(*v) for v in left]),
                cq.Edge.makeLine(cq.Vector(*left[-1]),cq.Vector(*right[0])),
                cq.Edge.makeBezier([cq.Vector(*v) for v in right])]
            points=[start]
        else: edges=[cq.Edge.makeBezier([cq.Vector(*point) for point in points])]
        path=cq.Workplane('XY').newObject([cq.Wire.assembleEdges(edges)])
        tangent=path.val().tangentAt(0)
        plane=cq.Plane(origin=points[0],normal=tangent)
        put(label,cq.Workplane(plane).circle(diameter/2).sweep(path,isFrenet=True))
        return path

    if spec.family in ('chair','stool','bench'):
        sh,st=p.seat_height_mm,p.seat_thickness_mm
        panel('Contoured seat' if p.seat_shape=='sculpted' else 'Seat',W,D,sh-st,st,p.seat_shape)
        four_legs(sh-st)
        if H-sh>p.rail_diameter_mm+20 and p.backrest:
            r=p.rail_diameter_mm
            z=H-r/2
            y=D-r/2
            x0,x1=r/2,W-r/2
            c=min(p.curve_depth_mm,D*0.22)
            if p.curve_profile=='wraparound':
                points=[(x0,D*0.28,z),(x0,D*0.70,z),(W*.20,y,z),(W*.50,y,z),(W*.80,y,z),(x1,D*.70,z),(x1,D*.28,z)]
            else:
                points=[(x0,y-c,z),(W*.25,y-c*.25,z),(W*.5,y,z),(W*.75,y-c*.25,z),(x1,y-c,z)]
                if p.curve_profile=='straight': points=[(x0,y,z),(x1,y,z)]
                if p.curve_profile=='wave': points=[(x0,y-c,z),(W*.25,y,z),(W*.5,y-c,z),(W*.75,y,z),(x1,y-c,z)]
            back_path=rail('Curved back rail' if p.curve_profile!='straight' else 'Back rail',points,r,wrap=p.curve_profile=='wraparound')
            for i in range(p.back_slats):
                u=(i+1)/(p.back_slats+1)
                if p.curve_profile=='wraparound': u=.2+.6*u
                point=back_path.val().positionAt(u)
                # Extend into the rail before the contact trim so the post end
                # follows its curved underside rather than touching at one edge.
                leg(f'Back support {i+1}',point.x,point.y,sh,point.z,width=min(r*.65,25),splay=False)
            if p.armrests and p.curve_profile!='wraparound':
                ah=p.arm_height_mm
                for side,x in (('Left',r/2),('Right',W-r/2)):
                    rail(f'{side} arm rail',[(x,D*.18,ah),(x,D*.5,ah+c*.12),(x,D-r,ah)],r)
                    box(f'{side} arm support',(r*.65,r*.65,ah-sh-r/2),(x,D*.25,(sh+ah-r/2)/2),'frame')
        if spec.furniture_template=='rocking_chair':
            # Raise the entire chair by rocker thickness while staying inside H.
            # Rockers are simplified segment profiles, not a stability solution.
            r=18
            for x in (p.leg_width_mm/2+17,W-p.leg_width_mm/2-17):
                rail('Curved rocker',[(x,r,21),(x,D*.25,21),(x,D*.5,-11),(x,D*.75,21),(x,D-r,21)],r)
    elif spec.family=='table':
        panel('Table top',W,D,H-t,t,p.top_shape)
        if p.pedestal:
            base=min(W,D)*.6
            panel('Pedestal base',base,base,0,t,'ellipse')
            put('Pedestal column',cq.Workplane('XY').circle(min(W,D)*.10).extrude(H-2*t).translate((W/2,D/2,t)))
        else: four_legs(H-t,max(p.leg_width_mm,min(W,D)*.065))
    elif spec.family=='storage':
        b=min(spec.back_thickness_mm,t)
        box('Left side',(t,D,H),(t/2,D/2,H/2))
        box('Right side',(t,D,H),(W-t/2,D/2,H/2))
        box('Top',(W-2*t,D,t),(W/2,D/2,H-t/2))
        box('Bottom',(W-2*t,D,t),(W/2,D/2,t/2))
        box('Back',(W-2*t,b,H-2*t),(W/2,D-b/2,H/2))
        for i in range(1,p.compartments):
            box(f'Divider {i}',(t,D-b-t,H-2*t),(W*i/p.compartments,(D-b+t)/2,H/2))
        for c in range(p.compartments):
            start=t if c==0 else W*c/p.compartments+t/2
            end=W-t if c==p.compartments-1 else W*(c+1)/p.compartments-t/2
            for i in range(p.shelves):
                box(f'Shelf {c+1}.{i+1}',(end-start,D-b-2*t,t),((start+end)/2,(D-b)/2,t+(H-2*t)*(i+1)/(p.shelves+1)))
        for i in range(p.doors):
            dw=(W-2*t)/p.doors
            box(f'Door {i+1}',(dw-3,t-2,H-2*t-4),(t+dw*(i+.5),t/2,H/2))
        for i in range(p.drawers):
            dh=(H-2*t)/p.drawers
            z=t+dh*i+3
            box(f'Drawer {i+1} front',(W-2*t-4,t-2,dh-6),(W/2,t/2,z+(dh-6)/2))
            box(f'Drawer {i+1} tray',(W-4*t,D-4*t,t),(W/2,D/2,z+t/2))
            for side,x in (('left',t*1.5),('right',W-t*1.5)):
                box(f'Drawer {i+1} {side}',(t,D-4*t,dh*.6),(x,D/2,z+t+dh*.3))
            box(f'Drawer {i+1} back',(W-4*t,t,dh*.6),(W/2,D-1.5*t,z+t+dh*.3))
    elif spec.family=='bed':
        level=350 if H<1300 else 330
        for side,x in (('Left',t/2),('Right',W-t/2)):
            box(side+' bed rail',(t,D-2*t,150),(x,D/2,level-75),'frame')
        box('Headboard',(W,t,H),(W/2,D-t/2,H/2))
        box('Footboard',(W,t,min(450,H*.6)),(W/2,t/2,min(450,H*.6)/2))
        levels=[level,H-330] if spec.furniture_template=='bunk_bed' else [level]
        for deck in levels:
            for i in range(p.slats):
                y=t+(D-2*t)*(i+.5)/p.slats
                box(f'Bed slat {deck:g}.{i+1}',(W-2*t,(D-2*t)/p.slats*.6,t),(W/2,y,deck+t/2))
        if len(levels)>1:
            for x in (t/2,W-t/2): box('Upper bed side rail',(t,D-2*t,150),(x,D/2,levels[1]-75),'frame')
    elif spec.family=='sofa':
        ch=p.cushion_thickness_mm
        seat=min(p.seat_height_mm,H)
        panel('Seat support deck',W,D,seat-ch-t,t,'rounded')
        four_legs(seat-ch-t)
        for i in range(p.seats):
            panel(f'Upholstery volume study {i+1}',(W-2*t)/p.seats-6,D-2*t,seat-ch,ch,'rounded',t+(W-2*t)*(i+.5)/p.seats,D/2)
        if H-seat>30 and p.backrest:
            box('Back support',(W,t,H-seat),(W/2,D-t/2,(seat+H)/2))
        if p.armrests:
            for x in (t/2,W-t/2): box('Arm side',(t,D,H*.70-seat+ch),(x,D/2,(seat-ch+H*.70)/2))
    elif spec.family=='screen':
        for i in range(max(2,p.back_slats)):
            x=t/2+(W-t)*i/(max(2,p.back_slats)-1)
            box(f'Divider upright {i+1}',(t,t,H-t),(x,D/2,(H+t)/2),'frame')
        box('Divider base',(W,D,t),(W/2,D/2,t/2))
    elif spec.family=='rack':
        panel('Rack base',W,D,0,t,'ellipse')
        put('Rack post',cq.Workplane('XY').circle(p.leg_width_mm/2).extrude(H-t).translate((W/2,D/2,t)))
        for i in range(4):
            angle=i*math.pi/2
            rail(f'Peg {i+1}',[(W/2,D/2,H-120),(W/2+math.cos(angle)*W*.32,D/2+math.sin(angle)*D*.32,H-80)],18)
    else: raise ValueError('Unsupported furniture family')
    warnings=['All dimensions and design features are estimates for visual review.',
        'Nonrectangular blank values are enclosing envelopes, not qualified cut sizes.',
        'Flush template contacts need engineered joinery, hardware and load/stability review.',
        'Upholstery volume studies use the selected proxy material; fabric, foam and seam construction are unresolved.']
    if trimmed: warnings.append('Flush contact trimming applied to: '+', '.join(dict.fromkeys(trimmed)))
    if clipped: warnings.append('Approximate curved profiles bounded to the overall design envelope: '+', '.join(clipped))
    return BuildResult(assembly,list(registry),shapes,warnings=warnings,registry=registry,local_shapes=local,mesh_tolerance_mm=.25)
