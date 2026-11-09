"""Whole-furniture general arrangement: three elevations and an isometric view."""
from reportlab.lib.pagesizes import A3,landscape
from app.dimension_sheet import Sheet,VERSION,INK,DIM,fit_text
from app.drawing_projection import Projection,ENGINE


def create_furniture_sheet(spec,build,output,*,provenance=None,job_id=None,budget_draft=None):
    from app.engineering_cad import cad_bounds
    shapes=[shape.val() for shape in build.shapes.values()]
    bounds=[cad_bounds(shape) for shape in shapes]
    by_id=dict(zip(build.shapes,bounds))
    spans=[(min(getattr(b,a+'min') for b in bounds),max(getattr(b,a+'max') for b in bounds)) for a in 'xyz']
    width,depth,height=[hi-lo for lo,hi in spans]
    top=Projection(shapes,(0,1));front=Projection(shapes,(0,2));side=Projection(shapes,(1,2))
    iso=Projection(shapes,look_from=(1,-1,.8),look_up=(0,0,1))
    maximum=min(520/width,225/depth,245/height,330/depth,
        450/iso.bounds.size.X,265/iso.bounds.size.Y)
    denominator=next((n for n in (1,2,3,4,5,6,8,10,12,15,20,25,30,40,50,75,100,150,200)
        if 72/25.4/n<=maximum),None)
    if denominator is None:raise ValueError('Furniture dimensions exceed the general arrangement layout limits')
    scale=72/25.4/denominator;pw,ph=landscape(A3);sheet=Sheet(pw,ph,scale)
    front_x=90;front_y=145;top_y=480;side_x=775
    curve_placements=[]
    for projection,origin,axes,title in (
        (top,(front_x,top_y),(0,1),'TOP VIEW'),
        (front,(front_x,front_y),(0,2),'FRONT ELEVATION'),
        (side,(side_x,front_y),(1,2),'SIDE ELEVATION')):
        x,y=origin
        sheet.projections.append((projection,x-spans[axes[0]][0]*scale,y-spans[axes[1]][0]*scale))
        curve_placements.append((title,projection,x-spans[axes[0]][0]*scale,y-spans[axes[1]][0]*scale))
        sheet.text(x,y-61,title,8,bold=True)
    ix=675+(450-iso.bounds.size.X*scale)/2-iso.bounds.min.X*scale
    iy=480+(265-iso.bounds.size.Y*scale)/2-iso.bounds.min.Y*scale
    sheet.projections.append((iso,ix,iy))
    sheet.text(900,457,'ISOMETRIC VIEW',8,bold=True,align='center')
    items=getattr(build,'furniture_items',[]) or [dict(item_id='PRIMARY',label=spec.project_name,part_ids=list(build.shapes))]
    item_dimensions=[]
    for item in items:
        bb=[by_id[pid] for pid in item['part_ids']]
        ss=[(min(getattr(b,a+'min') for b in bb),max(getattr(b,a+'max') for b in bb)) for a in 'xyz']
        dims=dict(zip(('width','depth','height'),(hi-lo for lo,hi in ss)))
        item_dimensions.append(dict(item_id=item['item_id'],label=item['label'],part_ids=item['part_ids'],dimensions_mm=dims))
        x0,x1=[front_x+(v-spans[0][0])*scale for v in ss[0]]
        y0,y1=[top_y+(v-spans[1][0])*scale for v in ss[1]]
        primary=item['item_id']=='PRIMARY';prefix='overall' if primary else item['item_id']
        sheet.dimension((x0,y1),(x1,y1),y1+24 if primary else y0-18,0,'TOP',prefix+' width')
        sheet.dimension((x1,y0),(x1,y1),x1+25 if primary else x0-18,1,'TOP',prefix+' depth')
        if len(items)>1:
            text='MAIN FURNITURE' if primary else fit_text(item['label'].upper(),230,7)
            if not primary:text+=f' / H {dims["height"]:.0f} mm'
            sheet.text((x0+x1)/2,y1+39 if primary else y0-33,text,7,align='center')
        if primary:
            z0,z1=[front_y+(v-spans[2][0])*scale for v in ss[2]]
            sx0,sx1=[side_x+(v-spans[1][0])*scale for v in ss[1]]
            sheet.dimension((x0,z0),(x1,z0),front_y-40,0,'FRONT','overall width')
            sheet.dimension((x0,z0),(x0,z1),front_x-30,1,'FRONT','overall height')
            sheet.dimension((sx0,z0),(sx1,z0),front_y-40,0,'SIDE','overall depth')
            sheet.dimension((sx1,z0),(sx1,z1),sx1+35,1,'SIDE','overall height')
    # Dimension the assembled support footprint rather than individual pieces.
    primary_ids=set(items[0]['part_ids'])
    lower=[b for pid,b in by_id.items() if pid in primary_ids and b.zmax<spans[2][1]-height*.05]
    if lower:
        for axis,origin,view in ((0,front_x,'FRONT'),(1,side_x,'SIDE')):
            a='xy'[axis];lo=min(getattr(b,a+'min') for b in lower);hi=max(getattr(b,a+'max') for b in lower)
            if hi-lo<(.98*(width if axis==0 else depth)):
                sheet.dimension((origin+(lo-spans[axis][0])*scale,front_y),
                    (origin+(hi-spans[axis][0])*scale,front_y),front_y-18,0,view,'support footprint '+('width' if axis==0 else 'depth'))
    from app.curve_dimensions import add_curve_dimensions
    curves=add_curve_dimensions(sheet,curve_placements,build.shapes)
    sheet.box(18,18,pw-36,ph-36,INK,.8);sheet.line([(18,77),(pw-18,77)],INK,.6)
    sheet.text(32,ph-39,'FISKE / WHOLE FURNITURE',11,bold=True)
    sheet.text(pw-32,ph-39,'GENERAL ARRANGEMENT',9,bold=True,align='right')
    sheet.text(32,58,fit_text(spec.project_name.upper(),420,11),11,bold=True)
    sheet.text(32,41,'Dimensions from the assembled CAD model / mm',7)
    sheet.text(32,27,'CAD radii / missing measurements are estimates until verified. Construction unverified.',6.2,colour=DIM)
    sheet.text(495,58,f'DRAWING GA / REV {spec.revision}',9,bold=True)
    sheet.text(495,41,'Native editable curves and dimensions in DXF',7)
    sheet.text(495,27,'Dashed lines indicate hidden edges.',6.5,colour=DIM)
    sheet.text(825,58,f'SCALE 1:{denominator} / A3',9,bold=True)
    sheet.text(825,41,'SHEET 1 OF 1',8)
    draft_label={'REPAIR_LIMIT_DRAFT':'REPAIR DRAFT','PREVIEW_UNAVAILABLE_DRAFT':'CAD DRAFT'}.get((budget_draft or {}).get('status'),'BUDGET DRAFT')
    sheet.text(825,27,'STATUS: '+draft_label if budget_draft else 'STATUS: REVIEW',7,colour=DIM)
    provenance=provenance or {};parameters=[]
    for item in spec.photo_design.values:
        parameters.append(dict(key=item.key,label=item.key.removesuffix('_mm').removesuffix('_deg').replace('_',' '),
            value=item.value,unit='mm' if item.key.endswith('_mm') else 'deg' if item.key.endswith('_deg') else 'count',
            source=(provenance.get(item.key) or {}).get('source','AI_ESTIMATED')))
    metadata=dict(schema_version=VERSION,title=spec.project_name,revision=spec.revision,job_id=job_id,
        output_scope='WHOLE_FURNITURE',geometry_source='REOPENED_STEP',units='mm',scale_denominator=denominator,
        budget_draft=budget_draft,
        overall_dimensions_mm=item_dimensions[0]['dimensions_mm'],layout_dimensions_mm=dict(width=width,depth=depth,height=height),
        furniture_items=item_dimensions,parameters=parameters,parts=[],
        views=['TOP','FRONT','SIDE','ISOMETRIC'],hidden_lines=True,drawing_engine=ENGINE,
        main_view_curves='NATIVE_DXF',physical_dimensions_verified=False,approved_for_manufacturing=False)
    metadata['curves']=curves
    return sheet.save(output,metadata)
