"""Curve-preserving visible and hidden projections from the actual CAD solids."""
import json
from pathlib import Path
import cadquery as cq
import ezdxf
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A3, landscape
from .models import CabinetSpec
from .geometry import exact_bounds
from .drawing_projection import Projection,write_dxf,ENGINE


def projected_edges(shapes, axes):
    lines = []
    for shape in shapes:
        for edge in shape.Edges():
            if edge.geomType()=='LINE':points=[edge.startPoint().toTuple(),edge.endPoint().toTuple()]
            else:
                # Solve the sampling distribution once. Repeated positionAt()
                # calls each reinvert arc length and can take minutes on a
                # trimmed spline edge.
                closed=(edge.startPoint()-edge.endPoint()).Length<1e-8
                samples,_=edge.sample(48 if closed else 49)
                points=[point.toTuple() for point in samples]
                if closed and points:points.append(points[0])
            lines.append([(p[axes[0]], p[axes[1]]) for p in points])
    return lines


def bounds_for(shapes, axes):
    bb = exact_bounds(cq.Compound.makeCompound(shapes))
    bounds = [(bb.xmin,bb.xmax), (bb.ymin,bb.ymax), (bb.zmin,bb.zmax)]
    return (*bounds[axes[0]], *bounds[axes[1]])


def dxf_view(path, shapes, axes, title, number, revision, labels=(),bounds_function=bounds_for,projection=None):
    projection = projection or Projection(shapes,axes)
    doc = write_dxf(path,[(projection,0,0)])
    doc.header['$INSUNITS'] = 4
    for layer, color in [('CAD',7),('DIMENSIONS',2),('NOTES',3)]:
        if layer not in doc.layers:doc.layers.add(layer, color=color)
    msp = doc.modelspace()
    x0,x1,y0,y1 = bounds_function(shapes, axes)
    offset = max(x1-x0,y1-y0)*0.06
    for base,p1,p2,angle in [((x0,y0-offset),(x0,y0),(x1,y0),0),
                             ((x0-offset,y0),(x0,y0),(x0,y1),90)]:
        dim = msp.add_linear_dim(base=base,p1=p1,p2=p2,angle=angle,dimstyle='EZDXF',
                                  dxfattribs={'layer':'DIMENSIONS'})
        dim.render()
    for text,x,y in labels:
        msp.add_text(text,dxfattribs={'height':max(offset/5,2),'insert':(x,y),'layer':'NOTES'})
    for i,text in enumerate([title, f'{number} | REV {revision} | mm | SCALE 1:1 MODEL SPACE',
                             'REVIEW - NOT FOR MANUFACTURING | APPROVAL: NONE',
                             'VISIBLE / HIDDEN CAD EDGES; NATIVE CURVES; NOT A CNC CUTTING PATH']):
        msp.add_text(text,dxfattribs={'height':max(offset/5,2),'insert':(x0,y0-offset*(2+i*0.5)),'layer':'NOTES'})
    doc.saveas(path)
    return dict(file=path.name, drawing_number=number, revision=revision, status='REVIEW',
                axes=list(axes), bounds_mm=[x0,x1,y0,y1], width_mm=x1-x0,height_mm=y1-y0,
                scale='1:1 model space', units='mm',drawing_engine=ENGINE,hidden_lines=True,
                curve_representation='NATIVE_DXF')


def pdf_view(pdf, shapes, axes, rect, title,bounds_function=bounds_for,projection=None):
    x,y,w,h = rect
    x0,x1,y0,y1 = bounds_function(shapes, axes)
    scale = min((w-60)/(x1-x0),(h-65)/(y1-y0))
    ox,oy = x+35-x0*scale,y+35-y0*scale
    pdf.setFont('Helvetica',10)
    pdf.drawString(x,y+h-15,title+f' | plotted scale 1:{1/scale*72/25.4:.3f}')
    (projection or Projection(shapes,axes)).draw_pdf(pdf,ox,oy,scale)
    pdf.setFont('Helvetica',9)
    pdf.drawString(x+35,y+15,f'{x1-x0:.3f} mm x {y1-y0:.3f} mm (CAD envelope)')


def export_drawings(spec, build, outdir: Path,bounds_function=bounds_for,include_parts=True):
    outdir.mkdir(parents=True,exist_ok=True)
    shapes = [s.val() for s in build.shapes.values()]
    records = []
    projections = {axes:Projection(shapes,axes) for axes in ((0,2),(1,2),(0,1))}
    for i,(name,axes) in enumerate([('front_elevation',(0,2)),('side_elevation',(1,2)),('top_view',(0,1))],1):
        labels = [(p.part_id,p.center_xyz_mm[axes[0]],p.center_xyz_mm[axes[1]]) for p in build.registry]
        records.append(dxf_view(outdir/f'{name}_REVIEW.dxf',shapes,axes,
                                spec.project_name,f'GA-{i:03}',spec.revision,labels,bounds_function,projections[axes]))
    pdf = canvas.Canvas(str(outdir/'drawings_REVIEW.pdf'),pagesize=landscape(A3))
    pw,ph = landscape(A3)
    pdf.setTitle(f'{spec.project_name} revision {spec.revision} REVIEW')
    pdf.setFont('Helvetica-Bold',16)
    pdf.drawString(30,ph-35,f'{spec.family.upper()} | REV {spec.revision} | REVIEW ONLY | mm')
    for i,(title,axes) in enumerate([('FRONT / GA-001',(0,2)),('SIDE / GA-002',(1,2)),('TOP / GA-003',(0,1))]):
        pdf_view(pdf,shapes,axes,(25+i*(pw-50)/3,90,(pw-60)/3,ph-145),title,bounds_function,projections[axes])
    pdf.setFont('Helvetica',10)
    pdf.drawString(30,50,'Visible and hidden solid edges; vector curves. Physical measurements, joints and tolerances remain unverified.')
    pdf.showPage()
    for part in build.registry if include_parts else []:
        local = [build.local_shapes[part.part_id].val()]
        number = f'PT-{part.part_id}'
        records.append(dxf_view(outdir/f'part_{part.part_id}_REVIEW.dxf',local,(0,1),
            f'{part.part_id} {part.label} | {part.material_id}',number,spec.revision,bounds_function=bounds_function))
        pdf.setFont('Helvetica-Bold',14)
        pdf.drawString(30,ph-35,f'{number} | REV {spec.revision} | {part.material_id} | REVIEW ONLY')
        pdf_view(pdf,local,(0,1),(30,100,pw*0.6,ph-170),'LOCAL LENGTH / WIDTH',bounds_function)
        pdf_view(pdf,local,(0,2),(pw*0.65,100,pw*0.3,ph-170),'LOCAL LENGTH / THICKNESS',bounds_function)
        pdf.setFont('Helvetica',10)
        pdf.drawString(30,70,f'Blank: {part.blank_length_mm:g} x {part.blank_width_mm:g} x {part.blank_thickness_mm:g} mm; quantity {part.quantity}; grain {part.grain_axis}')
        pdf.drawString(30,50,'Approval: NONE. Confirm blanks, all features and tolerances against engineered shop drawings.')
        pdf.showPage()
    pdf.save()
    manifest = dict(revision=spec.revision, units='mm',status='REVIEW',drawing_engine=ENGINE,
                    hidden_lines=True,curve_representation='NATIVE_DXF',
                    part_ids=[p.part_id for p in build.registry],drawings=records,
                    incomplete=['sections','exploded assembly','joint details','hardware mounting','manufacturing tolerances'])
    (outdir/'drawing_manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return manifest


def export_front_elevation(spec: CabinetSpec, outdir: Path):
    """Backward-compatible entry point; geometry comes from the same cabinet template."""
    from .cad import create_cabinet
    build = create_cabinet(spec)
    outdir.mkdir(parents=True,exist_ok=True)
    dxf_view(outdir/'front_elevation_REVIEW.dxf',[s.val() for s in build.shapes.values()],
             (0,2),spec.project_name,'GA-001',spec.revision)
