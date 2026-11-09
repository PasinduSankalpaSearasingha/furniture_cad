"""Radius information measured from CAD curves, never inferred from pixels."""
import math
import textwrap
import cadquery as cq


def curve_inventory(shapes):
    circular={};variable=[];seen_variable=set()
    for part_id,shape in shapes.items():
        solid=shape.val() if hasattr(shape,'val') else shape
        for edge in solid.Edges():
            kind=edge.geomType()
            if kind=='CIRCLE':
                radius=edge.radius()
                if not math.isfinite(radius) or radius<=1e-6:continue
                key=round(radius,6)
                record=circular.setdefault(key,dict(radius_mm=key,edge_count=0,component_ids=[]))
                record['edge_count']+=1
                if part_id not in record['component_ids']:record['component_ids'].append(part_id)
            elif kind in ('ELLIPSE','BSPLINE','BEZIER'):
                try:curvatures=edge.curvatures([i/32 for i in range(1,32)])
                except Exception:continue
                radii=[1/abs(v) for v in curvatures if math.isfinite(v) and abs(v)>1e-9]
                if not radii:continue
                lo,hi=min(radii),max(radii)
                signature=(part_id,kind,round(lo,3),round(hi,3))
                if signature in seen_variable:continue
                seen_variable.add(signature)
                variable.append(dict(component_id=part_id,geometry=kind,
                    sampled_local_radius_min_mm=round(lo,6),sampled_local_radius_max_mm=round(hi,6),
                    sample_count=len(curvatures),constant_radius=False))
    for i,item in enumerate(variable):item['curve_id']=f'V{i+1:03d}'
    return dict(circular_radii=sorted(circular.values(),key=lambda item:item['radius_mm']),variable_curves=variable,
        basis='ACTUAL_CAD_GEOMETRY',physical_radii_verified=False,
        variable_curve_note='Freeform/elliptical curves have variable local curvature. Sampled local radii are not one circular radius or a verified global range.')


def add_curve_dimensions(sheet,placements,shapes):
    """Add face-on circular radius leaders and a whole-furniture radius legend.

    Perspective ellipses and spline fits never become circular dimensions.
    Native radius measurements use the same 1:1 model units as linear DXF dims.
    """
    from app.dimension_sheet import label,DIM
    inventory=curve_inventory(shapes);seen=set()
    for view,projection,ox,oy in placements:
        candidates={}
        for edge in cq.Shape(projection.visible.wrapped).Edges():
            if edge.geomType()!='CIRCLE':continue
            radius=edge.radius();key=round(radius,6)
            if key in seen or key not in {item['radius_mm'] for item in inventory['circular_radii']}:continue
            candidates.setdefault(key,[]).append(edge)
        for index,(radius,edges) in enumerate(sorted(candidates.items())[:8]):
            x=min(sheet.width-85,ox+projection.bounds.max.X*sheet.scale+42)
            y=oy+projection.bounds.max.Y*sheet.scale-8-index*13
            edge=min(edges,key=lambda candidate:(ox+candidate.positionAt(.5).x*sheet.scale-x)**2+
                (oy+candidate.positionAt(.5).y*sheet.scale-y)**2)
            center=edge.arcCenter();point=edge.positionAt(.5)
            cx,cy=ox+center.x*sheet.scale,oy+center.y*sheet.scale
            px,py=ox+point.x*sheet.scale,oy+point.y*sheet.scale
            sheet.radius_dimension((cx,cy),(px,py),(x,y),radius,view,'circular CAD curve')
            seen.add(radius)
    radii=inventory['circular_radii']
    legend='Circular radii / mm: '+', '.join('R'+label(item['radius_mm']) for item in radii) if radii else ''
    if inventory['variable_curves']:legend+=('   ' if legend else '')+'Freeform / elliptical curves: variable radius; sampled local radii in the curve table.'
    if legend:
        rows=textwrap.wrap(legend,width=260)
        for index,row in enumerate(rows[:6]):sheet.text(32,sheet.height-55-index*8,row,6.3,colour=DIM)
        if len(rows)>6:sheet.text(32,sheet.height-103,'Further curve values are retained in the drawing data and native CAD geometry.',6.3,colour=DIM)
    inventory['annotated_radius_count']=len(seen)
    return inventory
