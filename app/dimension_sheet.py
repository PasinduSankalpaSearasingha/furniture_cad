"""One vector sheet containing CAD views, dimensions, details and schedules."""
from dataclasses import dataclass, field
from html import escape
import json
import math
from pathlib import Path

import cadquery as cq
import ezdxf
from reportlab.lib.pagesizes import A3, landscape
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas
from app.drawing_projection import Projection,write_dxf,ENGINE

STEM = 'all_dimensions_REVIEW'
VERSION = 4
INK = '#202625'
DIM = '#67716d'
HIDDEN = '#a3aaa6'
DETAIL_DEFLECTION_MM = .005


def label(value):
    return f'{value:.2f}'.rstrip('0').rstrip('.') if abs(value-round(value)) > .005 else str(round(value))


def edge_lines(shape):
    result = []
    for edge in shape.Edges():
        if edge.geomType() == 'LINE':points = [edge.startPoint(), edge.endPoint()]
        else:
            points, _ = edge.sample(DETAIL_DEFLECTION_MM)
            if (edge.startPoint()-edge.endPoint()).Length < 1e-7 and points:points.append(points[0])
        if len(points) > 1:result.append([(point.x, point.y) for point in points])
    return result


def orthographic_lines(shapes, axes):
    """OCCT hidden-line projection in an explicit drawing coordinate frame."""
    from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
    from OCP.HLRAlgo import HLRAlgo_Projector
    from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir
    from OCP.BRepLib import BRepLib
    x = cq.Vector(*[(1 if i == axes[0] else 0) for i in range(3)])
    y = cq.Vector(*[(1 if i == axes[1] else 0) for i in range(3)])
    normal = x.cross(y)
    algorithm = HLRBRep_Algo();algorithm.Add(cq.Compound.makeCompound(shapes).wrapped)
    algorithm.Projector(HLRAlgo_Projector(gp_Ax2(gp_Pnt(), gp_Dir(*normal.toTuple()), gp_Dir(*x.toTuple()))))
    algorithm.Update();algorithm.Hide();projected = HLRBRep_HLRToShape(algorithm)
    result = dict(visible=[], hidden=[])
    for kind, compounds in [('visible', [projected.VCompound(), projected.Rg1LineVCompound(), projected.OutLineVCompound()]),
                             ('hidden', [projected.HCompound(), projected.OutLineHCompound()])]:
        for compound in compounds:
            if not compound.IsNull():
                BRepLib.BuildCurves3d_s(compound, 1e-7)
                result[kind].extend(edge_lines(cq.Shape(compound)))
    if not result['visible']:raise ValueError('The dimension sheet has no visible CAD projection')
    return result


@dataclass
class Sheet:
    width: float
    height: float
    scale: float
    lines: list = field(default_factory=list)
    texts: list = field(default_factory=list)
    dimensions: list = field(default_factory=list)
    radius_dimensions: list = field(default_factory=list)
    projections: list = field(default_factory=list)

    def line(self, points, colour=INK, width=.45, dashed=False):
        self.lines.append(dict(points=points, colour=colour, width=width, dashed=dashed))

    def box(self, x, y, w, h, colour=INK, width=.45):
        self.line([(x,y),(x+w,y),(x+w,y+h),(x,y+h),(x,y)], colour, width)

    def text(self, x, y, text, size=7, bold=False, align='left', rotation=0, colour=INK):
        self.texts.append(dict(x=x,y=y,text=str(text),size=size,bold=bold,align=align,rotation=rotation,colour=colour))

    def dimension(self, p1, p2, position, axis, view, purpose):
        horizontal = axis == 0
        a, b = sorted((p1[axis],p2[axis]));value = (b-a)/self.scale
        if value < .01:return
        first_line,first_text=len(self.lines),len(self.texts)
        endpoints = [(a,position),(b,position)] if horizontal else [(position,a),(position,b)]
        for anchor, end in zip(sorted((p1,p2), key=lambda p:p[axis]), endpoints):
            self.line([anchor,end], DIM, .25)
            self.line([(end[0]-2,end[1]-2),(end[0]+2,end[1]+2)], DIM, .3)
        self.line(endpoints, DIM, .3)
        if horizontal:self.text((a+b)/2,position+4,label(value),7,align='center',colour=DIM)
        else:self.text(position-4,(a+b)/2,label(value),7,align='center',rotation=90,colour=DIM)
        for item in self.lines[first_line:]:item['role']='dimension'
        for item in self.texts[first_text:]:item['role']='dimension'
        self.dimensions.append(dict(view=view,purpose=purpose,value_mm=round(value,6),
            p1=list(p1),p2=list(p2),position=position,axis=axis))

    def save(self, directory, metadata, *, stem=STEM):
        directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
        pdf=canvas.Canvas(str(directory/(stem+'.pdf')),pagesize=(self.width,self.height),pageCompression=1)
        pdf.setTitle(metadata['title']+' - all dimensions - REVIEW')
        for projection,ox,oy in self.projections:projection.draw_pdf(pdf,ox,oy,self.scale)
        for item in self.lines:
            pdf.setStrokeColor(item['colour']);pdf.setLineWidth(item['width']);pdf.setDash([3,2] if item['dashed'] else [])
            path=pdf.beginPath();path.moveTo(*item['points'][0])
            for point in item['points'][1:]:path.lineTo(*point)
            pdf.drawPath(path)
        for item in self.texts:
            pdf.saveState();pdf.translate(item['x'],item['y']);pdf.rotate(item['rotation'])
            pdf.setFillColor(item['colour']);pdf.setFont('Helvetica-Bold' if item['bold'] else 'Helvetica',item['size'])
            draw={'left':pdf.drawString,'center':pdf.drawCentredString,'right':pdf.drawRightString}[item['align']]
            draw(0,0,item['text']);pdf.restoreState()
        pdf.showPage();pdf.save()
        svg=[f'<svg xmlns="http://www.w3.org/2000/svg" width="420mm" height="297mm" viewBox="0 0 {self.width} {self.height}" role="img" aria-label="All furniture dimensions">',
             '<rect width="100%" height="100%" fill="white"/>']
        import xml.etree.ElementTree as ET
        for projection,ox,oy in self.projections:
            view=projection.svg_element(self.scale);bounds=projection.bounds
            view.attrib.update(x=str(ox+bounds.min.X*self.scale),
                y=str(self.height-oy-bounds.max.Y*self.scale),
                width=str(bounds.size.X*self.scale),height=str(bounds.size.Y*self.scale))
            svg.append(ET.tostring(view,encoding='unicode'))
        for item in self.lines:
            points=' '.join(f'{x:.4f},{self.height-y:.4f}' for x,y in item['points'])
            dash=' stroke-dasharray="3 2"' if item['dashed'] else ''
            svg.append(f'<polyline points="{points}" fill="none" stroke="{item["colour"]}" stroke-width="{item["width"]}"{dash}/>')
        for item in self.texts:
            x,y=item['x'],self.height-item['y'];anchor={'left':'start','center':'middle','right':'end'}[item['align']]
            transform=f' transform="rotate({-item["rotation"]} {x} {y})"' if item['rotation'] else ''
            weight='bold' if item['bold'] else 'normal'
            svg.append(f'<text x="{x}" y="{y}" font-size="{item["size"]}" font-family="Arial,Helvetica,sans-serif" font-weight="{weight}" text-anchor="{anchor}" fill="{item["colour"]}"{transform}>{escape(item["text"])}</text>')
        svg.append('</svg>');(directory/(stem+'.svg')).write_text('\n'.join(svg),encoding='utf-8')

        # Keep model-space geometry and native DIMENSION measurements in mm.
        # At the stated paper scale, this sheet prints to an A3 landscape page.
        factor=1/self.scale
        doc=write_dxf(directory/(stem+'.dxf'),[(projection,ox*factor,oy*factor)
            for projection,ox,oy in self.projections])
        doc.units=4;msp=doc.modelspace()
        for name,colour in [('CAD',7),('HIDDEN',8),('NOTES',7),('DIMENSIONS',8)]:
            if name not in doc.layers:doc.layers.new(name,dxfattribs={'color':colour})
        if 'DASHED' not in doc.linetypes:doc.linetypes.new('DASHED',dxfattribs={'pattern':[6,3,-3]})
        for item in self.lines:
            if item.get('role')=='dimension':continue
            msp.add_lwpolyline([(x*factor,y*factor) for x,y in item['points']],dxfattribs={
                'layer':'HIDDEN' if item['dashed'] else 'CAD','linetype':'DASHED' if item['dashed'] else 'CONTINUOUS'})
        for item in self.texts:
            if item.get('role')=='dimension':continue
            x,y=item['x']*factor,item['y']*factor
            width=stringWidth(item['text'],'Helvetica-Bold' if item['bold'] else 'Helvetica',item['size'])*factor
            shift=width*(.5 if item['align']=='center' else 1 if item['align']=='right' else 0)
            angle=math.radians(item['rotation']);x-=shift*math.cos(angle);y-=shift*math.sin(angle)
            msp.add_text(item['text'],dxfattribs={'height':item['size']*factor,'insert':(x,y),'rotation':item['rotation'],'layer':'NOTES'})
        for dimension in self.dimensions:
            p1,p2=[tuple(v*factor for v in dimension[key]) for key in ('p1','p2')]
            base=(p1[0],dimension['position']*factor) if dimension['axis']==0 else (dimension['position']*factor,p1[1])
            # Native associative measurements are retained on their own layer;
            # custom presentation lines/text above are the shared PDF/SVG layout.
            msp.add_linear_dim(base=base,p1=p1,p2=p2,angle=0 if dimension['axis']==0 else 90,
                override=dict(dimtxt=7*factor,dimasz=2*factor,dimdec=2,
                    dimlfac=self.scale/dimension.get('detail_scale',self.scale)),dxfattribs={'layer':'DIMENSIONS'}).render()
        for dimension in self.radius_dimensions:
            center,point,location=[tuple(v*factor for v in dimension[key]) for key in ('center','point','location')]
            radial=msp.add_radius_dim(center=center,mpoint=point,dimstyle='EZDXF',
                override=dict(dimtxt=7*factor,dimasz=2*factor,dimdec=2),dxfattribs={'layer':'DIMENSIONS'})
            radial.user_location_override(location);radial.render()
        doc.saveas(directory/(stem+'.dxf'))
        metadata.update(dimensions=self.dimensions,radius_dimensions=self.radius_dimensions,page=dict(format='A3',orientation='landscape',pages=1),
            files={kind:stem+'.'+kind for kind in ('pdf','svg','dxf')})
        (directory/(stem+'.json')).write_text(json.dumps(metadata,indent=2),encoding='utf-8')
        return metadata

    def radius_dimension(self,center,point,location,radius,view,purpose):
        first_line,first_text=len(self.lines),len(self.texts)
        self.line([center,point,location],DIM,.3)
        dx,dy=point[0]-center[0],point[1]-center[1];length=math.hypot(dx,dy)
        if length>1e-9:
            ux,uy=dx/length,dy/length
            for sign in (-1,1):self.line([point,(point[0]-5*ux+sign*1.7*uy,point[1]-5*uy-sign*1.7*ux)],DIM,.3)
        self.text(location[0],location[1]+4,'R'+label(radius),7,colour=DIM)
        for item in self.lines[first_line:]:item['role']='dimension'
        for item in self.texts[first_text:]:item['role']='dimension'
        self.radius_dimensions.append(dict(view=view,purpose=purpose,value_mm=radius,
            center=list(center),point=list(point),location=list(location),basis='CAD_GEOMETRY'))


def fit_text(text, width, size=7):
    while stringWidth(text,'Helvetica',size)>width and len(text)>3:text=text[:-4]+'...'
    return text


def create_dimension_sheet(spec, build, output, *, provenance=None, job_id=None, bounds_function=None):
    from app.engineering_cad import cad_bounds
    bounds_function=bounds_function or cad_bounds
    shapes=[shape.val() for shape in build.shapes.values()];parts=list(build.registry)
    bounds={part.part_id:bounds_function(build.shapes[part.part_id].val()) for part in parts}
    extents=[(min(getattr(b,axis+'min') for b in bounds.values()),max(getattr(b,axis+'max') for b in bounds.values())) for axis in 'xyz']
    width,depth,height=[high-low for low,high in extents]
    provenance=provenance or {}
    raw_values=spec.photo_design.values if spec.photo_design else []
    parameters=[]
    for item in raw_values:
        source=(provenance.get(item.key) or {}).get('source','AI_ESTIMATED')
        unit='mm' if item.key.endswith('_mm') else 'deg' if item.key.endswith(('_deg','_degrees')) else 'count' if item.key.endswith('_count') else 'ratio'
        parameters.append(dict(key=item.key,label=item.key.removesuffix('_mm').removesuffix('_deg').replace('_',' '),
            value=item.value,unit=unit,source=source))
    page_width,page_height=landscape(A3)
    row_height=9.7;table_rows=max(math.ceil(len(parameters)/2),len(parts),3)
    table_top=92+row_height*table_rows
    front_y=table_top+67
    available=page_height-80-front_y
    maximum_scale=min(650/width,170/depth,(available-68)/(height+depth))
    denominator=next((n for n in (1,2,5,10,15,20,25,30,40,50,75,100,150,200) if 72/25.4/n<=maximum_scale),None)
    if denominator is None:raise ValueError('Furniture dimensions exceed the single-sheet layout limits')
    scale=72/25.4/denominator;sheet=Sheet(page_width,page_height,scale)
    front_x=84;side_x=page_width-255;top_y=front_y+height*scale+68
    views={}
    for name,axes,origin in [('TOP VIEW',(0,1),(front_x,top_y)),('FRONT ELEVATION',(0,2),(front_x,front_y)),('SIDE ELEVATION',(1,2),(side_x,front_y))]:
        projection=Projection(shapes,axes);x0,x1=extents[axes[0]];y0,y1=extents[axes[1]];ox,oy=origin
        sheet.projections.append((projection,ox-x0*scale,oy-y0*scale))
        sheet.text(ox,oy+(y1-y0)*scale+39,name,8,bold=True)
        views[name]=dict(axes=axes,origin=origin,bounds=[x0,x1,y0,y1],projection=projection)
    # Common projection scale and alignment connect the three assembly views.
    sheet.dimension((front_x,top_y+depth*scale),(front_x+width*scale,top_y+depth*scale),top_y+depth*scale+18,0,'TOP','overall width')
    sheet.dimension((front_x+width*scale,top_y),(front_x+width*scale,top_y+depth*scale),front_x+width*scale+20,1,'TOP','overall depth')
    sheet.dimension((front_x,front_y),(front_x+width*scale,front_y),front_y-39,0,'FRONT','overall width')
    sheet.dimension((front_x+width*scale,front_y),(front_x+width*scale,front_y+height*scale),front_x+width*scale+22,1,'FRONT','overall height')
    sheet.dimension((side_x,front_y),(side_x+depth*scale,front_y),front_y-22,0,'SIDE','overall depth')
    sheet.dimension((side_x+depth*scale,front_y),(side_x+depth*scale,front_y+height*scale),side_x+depth*scale+23,1,'SIDE','overall height')

    # Dimension broad front components separately; small gaps remain legible in
    # the parameter schedule rather than being crowded between adjacent doors.
    selected=[]
    for part in parts:
        b=bounds[part.part_id]
        if b.xlen>=width*.08 and b.zlen>=height*.15 and b.ymin<=extents[1][0]+depth*.25:
            if all(min(b.xmax,bounds[p.part_id].xmax)-max(b.xmin,bounds[p.part_id].xmin)<=1 for p in selected):selected.append(part)
    for part in selected[:8]:
        b=bounds[part.part_id];x1=front_x+(b.xmin-extents[0][0])*scale;x2=front_x+(b.xmax-extents[0][0])*scale
        sheet.dimension((x1,front_y),(x2,front_y),front_y-18,0,'FRONT',part.part_id+' width')
        sheet.text((x1+x2)/2,front_y+(b.zmin-extents[2][0]+b.zlen*.5)*scale,part.part_id,7,align='center')
    levels=sorted({round(value,4) for b in bounds.values() for value in (b.zmin,b.zmax)})
    for lower,upper in zip(levels,levels[1:]):
        if (upper-lower)*scale>=13:
            y1=front_y+(lower-extents[2][0])*scale;y2=front_y+(upper-extents[2][0])*scale
            sheet.dimension((front_x,y1),(front_x,y2),front_x-27,1,'FRONT','component height interval')

    # Enlarge real projected edges at the upper side corner. This is a detail
    # view, not an invented section through unavailable internal construction.
    detail_x=side_x-15;detail_y=top_y-8;detail_w=220;detail_h=min(150,page_height-detail_y-70)
    sheet.box(detail_x,detail_y,detail_w,detail_h,'#b5bcb8',.3)
    sheet.text(detail_x+8,detail_y+detail_h-14,'DETAIL A - TOP CORNER',8,bold=True)
    sheet.text(detail_x+8,detail_y+detail_h-26,'Enlarged side projection / mm',6.5,colour=DIM)
    detail_scale=min(2.5,scale*5);span_x=(detail_w-55)/detail_scale;span_y=(detail_h-44)/detail_scale
    crop=(extents[1][0],extents[1][0]+span_x,extents[2][1]-span_y,extents[2][1])
    from app.dimension_sheet_clip import clip_polyline
    for kind in ('hidden','visible'):
        source=getattr(views['SIDE ELEVATION']['projection'],kind)
        for line in edge_lines(cq.Shape(source.wrapped)):
            for segment in clip_polyline(line,crop):
                sheet.line([(detail_x+20+(x-crop[0])*detail_scale,detail_y+10+(z-crop[2])*detail_scale) for x,z in segment],
                    HIDDEN if kind=='hidden' else INK,.3 if kind=='hidden' else .55,kind=='hidden')
    top_parts=[p for p in parts if bounds[p.part_id].zmax>=extents[2][1]-.01 and bounds[p.part_id].zlen<height*.2]
    if top_parts:
        top=max(top_parts,key=lambda p:bounds[p.part_id].xlen*bounds[p.part_id].ylen);b=bounds[top.part_id]
        anchor=detail_x+20
        y1=detail_y+10+(b.zmin-crop[2])*detail_scale;y2=detail_y+10+(b.zmax-crop[2])*detail_scale
        if detail_y+5<=y1<=y2<=detail_y+detail_h-30:
            # Detail scale differs from the common assembly plotting scale.
            original=sheet.scale;sheet.scale=detail_scale
            sheet.dimension((anchor,y1),(anchor,y2),detail_x+detail_w-21,1,'DETAIL A',top.part_id+' top thickness')
            sheet.dimensions[-1]['detail_scale']=detail_scale;sheet.scale=original

    sheet.text(31,table_top+16,'DESIGN DIMENSIONS & SOURCES',8,bold=True)
    sheet.text(31,table_top+5,'E = estimate   U = your input   S = sample drawing   P = measured',6,colour=DIM)
    columns=2;rows=max(1,math.ceil(len(parameters)/columns));column_width=325
    for index,item in enumerate(parameters):
        col=index//rows;row=index%rows;x=31+col*column_width;y=table_top-10-row*row_height
        source={'USER_PROVIDED':'U','SAMPLE_DRAWING':'S','AI_ESTIMATED':'E','PHYSICALLY_MEASURED':'P','DESIGNER_VERIFIED':'P'}.get(item['source'],'E')
        sheet.text(x,y,fit_text(item['label'].capitalize(),column_width-90),6.6)
        sheet.text(x+column_width-10,y,f"{label(item['value'])} {item['unit']}  {source}",6.6,align='right')
    part_x=page_width-467
    sheet.text(part_x,table_top+16,'PART DIMENSIONS - CAD LOCAL ENVELOPES / mm',8,bold=True)
    sheet.text(part_x,table_top+5,'ID / component                                             LOCAL X x Y x Z',6,colour=DIM)
    part_records=[]
    for index,part in enumerate(parts):
        y=table_top-10-index*row_height
        sheet.text(part_x,y,fit_text(part.part_id+'  '+part.label,250),6.6)
        sheet.text(page_width-31,y,' x '.join(label(v) for v in (part.length_mm,part.width_mm,part.thickness_mm)),6.6,align='right')
        part_records.append(dict(part_id=part.part_id,label=part.label,length_mm=part.length_mm,width_mm=part.width_mm,thickness_mm=part.thickness_mm))
    sheet.box(18,18,page_width-36,page_height-36,INK,.8)
    sheet.line([(18,68),(page_width-18,68)],INK,.6)
    sheet.text(31,page_height-36,'FISKE / GENERAL ARRANGEMENT - ALL DIMENSIONS',11,bold=True)
    sheet.text(page_width-31,page_height-36,f'{len(parts)} PARTS',11,bold=True,align='right')
    for x in (470,770,970):sheet.line([(x,18),(x,68)],INK,.4)
    sheet.text(31,52,fit_text(spec.project_name.upper(),425,10),10,bold=True)
    sheet.text(31,36,'CAD DIMENSIONS / REVIEW - physical measurements and construction unverified',7)
    sheet.text(31,24,'Native curves in main views. Detail curves: requested 0.005 mm deflection. Dashed lines: hidden edges.',6,colour=DIM)
    sheet.text(482,52,f'DRAWING GA-ALL / REV {spec.revision}',9,bold=True)
    sheet.text(482,36,'Dimensions from exported CAD; parameter sources shown above.',6.5)
    sheet.text(482,24,'Part sizes are local X/Y/Z envelopes; machining sizes unverified.',6,colour=DIM)
    sheet.text(782,52,f'SCALE 1:{denominator} / A3',9,bold=True)
    sheet.text(782,36,'ALL DIMENSIONS IN mm',7)
    sheet.text(782,24,'DETAIL A enlarged independently',6,colour=DIM)
    sheet.text(982,52,'SHEET 1 OF 1',9,bold=True)
    sheet.text(982,36,'STATUS: REVIEW',7)
    sheet.text(982,24,'JOB '+str(job_id or 'LOCAL')[:14],6,colour=DIM)
    metadata=dict(schema_version=VERSION,title=spec.project_name,revision=spec.revision,job_id=job_id,
        geometry_source='REOPENED_STEP',units='mm',scale_denominator=denominator,
        overall_dimensions_mm=dict(width=width,depth=depth,height=height),parameters=parameters,parts=part_records,
        views=['TOP','FRONT','SIDE','DETAIL A'],hidden_lines=True,drawing_engine=ENGINE,
        main_view_curves='NATIVE_DXF',detail_requested_deflection_mm=DETAIL_DEFLECTION_MM,
        physical_dimensions_verified=False,approved_for_manufacturing=False)
    return sheet.save(output,metadata)
