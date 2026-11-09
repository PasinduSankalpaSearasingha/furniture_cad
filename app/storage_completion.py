"""Complete closed storage geometry for all-angle review; hidden parts are assumptions."""
import re
import cadquery as cq
from app.photo_schema import PhotoDesign
from app.geometry import exact_bounds

POLICY='COMPLETE_360_V1'
MARKER='ASSUMED_360_COMPLETION:'


def storage_design(plan):
    name=plan.furniture_type.lower()
    return bool(re.search(r'cabinet|sideboard|cupboard|credenza|wardrobe|dresser',name)
        and not re.search(r'open.back|backless',name))


def complete_storage_plan(plan,build):
    """Preserve photographed parts and add plausible, separately named solids.

    This is a visual reconstruction of hidden construction, not manufacturer
    hardware, verified joinery, or evidence that unseen parts match the object.
    """
    if not storage_design(plan):return plan,dict(applicable=False,added_parts=[])
    if any(part.evidence.startswith(MARKER) for part in plan.parts):
        return plan,dict(applicable=True,added_parts=[p.part_id for p in plan.parts if p.evidence.startswith(MARKER)])
    from app.furniture_items import primary_part_ids
    primary=set(primary_part_ids(build))
    bounds={part.part_id:exact_bounds(build.shapes[part.part_id].val()) for part in build.registry if part.part_id in primary}
    x0=min(b.xmin for b in bounds.values());x1=max(b.xmax for b in bounds.values())
    y0=min(b.ymin for b in bounds.values());y1=max(b.ymax for b in bounds.values())
    z0=min(b.zmin for b in bounds.values());z1=max(b.zmax for b in bounds.values())
    width,depth,height=x1-x0,y1-y0,z1-z0
    # A broad thin rear solid already provides the missing exterior surface.
    rear=[pid for pid,b in bounds.items() if b.xlen>width*.7 and b.zlen>height*.55
        and b.ylen<depth*.15 and b.ymax>=y1-.1]
    if rear:return plan,dict(applicable=True,added_parts=[],existing_rear_parts=rear)
    values={value.key:value.value for value in plan.values};raw=plan.model_dump(mode='json');added=[]
    stock=values.get('carcass_panel_thickness_mm',values.get('panel_thickness_mm',18))
    stock=max(8,min(stock,40));back=min(12,stock);wall=min(12,stock)
    base=values.get('base_height_mm',0)
    floor_candidates=[b for b in bounds.values() if b.xlen>width*.7 and b.ylen>depth*.5 and b.zlen<height*.15 and b.zmin<z0+height*.4]
    floor_top=max((b.zmax for b in floor_candidates),default=z0+base+stock)
    # The old facade-only plan can contain an L-section contact surrogate.
    # Its actual floor remains at base+stock, independently of its tall upstand.
    floor_top=min(floor_top,z0+base+stock) if base else floor_top
    top_candidates=[b for b in bounds.values() if b.xlen>width*.7 and b.ylen>depth*.5 and b.zmax>=z1-.1]
    ceiling=min((b.zmin for b in top_candidates),default=z1-stock)
    if ceiling-floor_top<stock*2:raise ValueError('Cabinet interior is too small for a completed carcass')
    inner_x0,inner_x1=x0+stock,x1-stock
    inner_front=y0+max(values.get('front_panel_thickness_mm',stock),stock)*2
    inner_rear=y1-back
    colours=[part.color_rgb for part in plan.parts if re.search(r'side|carcass|base',part.label,re.I)]
    colour=colours[0] if colours else [.36,.27,.2]
    new_values={'completion_back_thickness_mm':back,'completion_inner_panel_mm':stock,
        'completion_drawer_wall_mm':wall,'completion_drawer_clearance_mm':8.}
    for key,value in new_values.items():
        if key not in values and len(raw['values'])<60:raw['values'].append(dict(key=key,value=value,explanation='Unverified hidden construction estimate for complete 360 degree review'))

    def panel(label,low,high,material='plywood'):
        size=[round(b-a,6) for a,b in zip(low,high)]
        if min(size)<=.01:return
        pid=f'F{max(int(part["part_id"][1:]) for part in raw["parts"])+1:03d}'
        raw['parts'].append(dict(part_id=pid,label=label,geometry='box',size=size,
            position=[round((a+b)/2,6) for a,b in zip(low,high)],material=material,color_rgb=colour,
            evidence=MARKER+' Unseen '+label.lower()+'. Estimated from the saved exterior envelope; construction, stock and fixings require review.'))
        added.append(pid)

    panel('Estimated full rear panel',(inner_x0,inner_rear,floor_top),(inner_x1,y1,ceiling))
    # Named facade zones allow a complete cabinet interior without replacing
    # the photograph's distinctive door/drawer shapes with a generic cabinet.
    left=values.get('left_door_bank_width_mm');middle=values.get('central_drawer_bank_width_mm')
    if left and middle and inner_front<inner_rear-stock:
        a,b=x0+left,x0+left+middle
        for number,position in enumerate((a,b),1):
            panel(f'Estimated internal partition {number}',(position-stock/2,inner_front,floor_top),(position+stock/2,inner_rear,ceiling))
        shelf_z=(floor_top+ceiling-stock)/2
        for name,low,high in [('left',inner_x0,a-stock/2),('right',b+stock/2,inner_x1)]:
            panel(f'Estimated {name} adjustable shelf',(low,inner_front,shelf_z),(high,inner_rear,shelf_z+stock))
        drawers=[part for part in plan.parts if re.search(r'drawer.*front|front.*drawer',part.label,re.I)]
        for number,part in enumerate(drawers,1):
            front=bounds[part.part_id];lx,hx=front.xmin+8,front.xmax-8
            ly,hy=inner_front,inner_rear-10
            lz=front.zmin+wall;hz=front.zmax-max(25,values.get('drawer_curved_pull_vertical_sweep_mm',25))
            if hz-lz<wall*2:continue
            panel(f'Estimated drawer {number} bottom',(lx,ly,lz),(hx,hy,lz+wall))
            panel(f'Estimated drawer {number} left wall',(lx,ly,lz+wall),(lx+wall,hy,hz))
            panel(f'Estimated drawer {number} right wall',(hx-wall,ly,lz+wall),(hx,hy,hz))
            panel(f'Estimated drawer {number} rear wall',(lx+wall,hy-wall,lz+wall),(hx-wall,hy,hz))
    if base and 'base_front_setback_mm' in values:
        inset=values.get('base_end_setback_mm',stock);setback=values['base_front_setback_mm']
        low_x,high_x=x0+inset,x1-inset;front_y=y0+setback;rear_y=y1-setback
        panel('Estimated recessed base left return',(low_x,front_y+stock,z0),(low_x+stock,rear_y,z0+base),'solid_wood')
        panel('Estimated recessed base right return',(high_x-stock,front_y+stock,z0),(high_x,rear_y,z0+base),'solid_wood')
        panel('Estimated recessed base rear member',(low_x+stock,rear_y-stock,z0),(high_x-stock,rear_y,z0+base),'solid_wood')
    note='360 degree completion adds estimated rear/interior/base solids. Original visible parts and overall dimensions are retained. Unseen construction is not photographically verified.'
    raw['summary']=(raw['summary']+' '+note)[:2400]
    raw['assumptions']=[note,*[text for text in raw['assumptions'] if not re.search(r'^No back,|F013 represents only',text,re.I)]][:30]
    raw['unrepresented_features']=[text for text in raw['unrepresented_features'] if not re.search(r'unseen rear and interior construction',text,re.I)]
    return PhotoDesign.model_validate(raw),dict(applicable=True,added_parts=added,hidden_construction_verified=False)


def coverage_360(plan,build):
    if not storage_design(plan):return dict(applicable=False)
    from app.furniture_items import primary_part_ids
    primary=set(primary_part_ids(build))
    bounds={part.part_id:exact_bounds(build.shapes[part.part_id].val()) for part in build.registry if part.part_id in primary}
    xs=(min(b.xmin for b in bounds.values()),max(b.xmax for b in bounds.values()))
    ys=(min(b.ymin for b in bounds.values()),max(b.ymax for b in bounds.values()))
    zs=(min(b.zmin for b in bounds.values()),max(b.zmax for b in bounds.values()))
    values={v.key:v.value for v in plan.values};base=values.get('base_height_mm',0)
    width,depth,height=xs[1]-xs[0],ys[1]-ys[0],zs[1]-zs[0]
    # Check the enclosure above its floor, rather than requiring a back panel
    # across the open space between raised cabinet legs. Use actual geometry;
    # an estimated leg height alone must not hide a missing part of the back.
    horizontal=[b for b in bounds.values() if b.xlen>width*.7 and b.ylen>depth*.5 and b.zlen<height*.15]
    ceiling=min((b.zmin for b in horizontal if b.zmax>=zs[1]-.1),default=zs[1])
    floor_top=min((b.zmax for b in horizontal if b.zmax<ceiling-.1),default=zs[0]+base)
    if ceiling-floor_top<=.01:raise ValueError('Cabinet rear check requires a positive enclosure height')
    rear_y=ys[1]-.1;results=[]
    solids=[solid for pid,shape in build.shapes.items() if pid in primary for solid in shape.val().Solids()]
    for ix in range(7):
        for iz in range(5):
            point=cq.Vector(xs[0]+(xs[1]-xs[0])*(ix+.5)/7,rear_y,floor_top+(ceiling-floor_top)*(iz+.5)/5)
            results.append(any(solid.isInside(point,1e-6) for solid in solids))
    coverage=sum(results)/len(results)
    return dict(applicable=True,rear_coverage=coverage,rear_closed=coverage>=.9,
        sampled_rear_points=len(results),rear_sample_z_range_mm=[floor_top,ceiling],hidden_construction_verified=False,
        estimated_part_ids=[p.part_id for p in plan.parts if p.evidence.startswith(MARKER)])
