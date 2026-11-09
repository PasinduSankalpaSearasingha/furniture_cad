"""Six-sided native curve drawings, measured dimensions and honest evidence."""
from datetime import datetime,timezone
import io
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import build123d as bd
import ezdxf
from pypdf import PdfReader,PdfWriter
from reportlab.pdfgen import canvas
from reportlab.graphics import renderPDF
from svglib.svglib import svg2rlg
from .cad_six_view_generator import VIEWS,six_views,layout
from .drawing_projection import MM_TO_PT

PAPERS={'A4':(297,210),'A3':(420,297),'A2':(594,420)}
LAYERS=('VISIBLE','HIDDEN','CENTERLINES','DIMENSIONS','ANNOTATIONS')


def dimensions(projection,identifier,name,known):
    basis=VIEWS[name]
    sizes=(projection.bounds.size.X,projection.bounds.size.Y)
    answer=[]
    for axis,(direction,value) in enumerate(zip((basis.right,basis.up),sizes)):
        world=next(i for i,v in enumerate(direction) if v)
        key=('width_mm','depth_mm','height_mm')[world]
        confirmed=identifier=='assembly' and key in known and abs(known[key]-value)<.05
        answer.append(dict(axis='horizontal' if axis==0 else 'vertical',value_mm=float(value),
            classification='confirmed' if confirmed else 'derived',source='native B-Rep projected envelope',
            physical_size_status='supplied_measurement' if confirmed else 'estimated_geometry',parameter_key=key))
    return answer


def write_view_svg(projection,path,identifier,name,dims,revision,convention,*,paper='A3',custom_size=None):
    # Annotative paper-space sheet; model geometry scale is explicitly recorded.
    source=projection.svg_element();box=[float(v) for v in source.attrib['viewBox'].split()]
    # build123d expresses SVG dimensions in mm; its viewBox may use a different scale.
    w,h=projection.bounds.size.X,projection.bounds.size.Y
    width,height=custom_size or PAPERS[paper]
    scale=min(1,(width-100)/max(w,1),(height-90)/max(h,1))
    qw,qh=w*scale,h*scale;ox=(width-qw-40)/2;oy=32
    root=ET.Element('svg',dict(xmlns='http://www.w3.org/2000/svg',width=f'{width}mm',height=f'{height}mm',
        viewBox=f'0 0 {width} {height}',role='img'))
    ET.SubElement(root,'title').text=f'{identifier} {name} - native orthographic CAD - mm'
    ET.SubElement(root,'metadata').text=json.dumps(dict(view=name,units='mm',model_scale=scale,convention=convention,
        look_from=VIEWS[name].look_from,right=VIEWS[name].right,up=VIEWS[name].up,dimensions=dims))
    # Flatten the viewport: some engineering SVG renderers ignore nested SVGs.
    wrapper=ET.SubElement(root,'g',transform=f'translate({ox} {oy}) scale({scale}) translate({-box[0]} {-box[1]})')
    for node in source.iter():
        node.tag=node.tag.split('}')[-1]
        if node.attrib.get('id')=='CAD':node.set('id','VISIBLE')
        if 'stroke-width' in node.attrib:node.set('stroke-width',str((.25 if node.attrib.get('id')=='VISIBLE' else .13)/scale))
    for child in source:wrapper.append(child)
    layer_ids={node.attrib.get('id') for node in root.iter()}
    for layer in ('CENTERLINES','HIDDEN','VISIBLE'):
        if layer not in layer_ids:ET.SubElement(root,'g',id=layer)
    g=ET.SubElement(root,'g',id='DIMENSIONS',fill='none',stroke='#304b63',**{'stroke-width':'.25'})
    ET.SubElement(g,'path',d=f'M {ox} {oy+qh+3} v 12 M {ox+qw} {oy+qh+3} v 12 M {ox} {oy+qh+11} H {ox+qw}')
    ET.SubElement(g,'path',d=f'M {ox+qw+3} {oy} h 14 M {ox+qw+3} {oy+qh} h 14 M {ox+qw+13} {oy} V {oy+qh}')
    text=ET.SubElement(root,'g',id='ANNOTATIONS',fill='#172d40',**{'font-family':'sans-serif','font-size':'4'})
    ET.SubElement(text,'text',x=str(ox+qw/2),y=str(oy+qh+18),**{'text-anchor':'middle'}).text=f"{dims[0]['value_mm']:.2f} mm ({dims[0]['classification']})"
    ET.SubElement(text,'text',x=str(ox+qw+20),y=str(oy+qh/2),transform=f'rotate(90 {ox+qw+20} {oy+qh/2})',**{'text-anchor':'middle'}).text=f"{dims[1]['value_mm']:.2f} mm ({dims[1]['classification']})"
    ET.SubElement(text,'text',x='8',y='12').text=f'{identifier} | {name.upper()} | {revision} | {convention} | scale 1:{1/scale:.3g} | mm'
    ET.SubElement(text,'text',x='8',y='20').text=f'{datetime.now(timezone.utc).date()} | Material unconfirmed | Native B-Rep envelope dimensions'
    ET.SubElement(text,'text',x='8',y=str(height-10)).text='DRAFT - CAD-derived sizes; materials, boundaries and joinery require review.'
    ET.ElementTree(root).write(path,encoding='utf-8',xml_declaration=True)


def write_view_dxf(projection,path,dims,identifier,name):
    exporter=bd.ExportDXF(version='R2010',unit=bd.Unit.MM)
    exporter.add_layer('VISIBLE');exporter.add_layer('HIDDEN',color=bd.ColorIndex.GRAY,line_type=bd.LineType.HIDDEN)
    exporter.add_shape(projection.visible,layer='VISIBLE');exporter.add_shape(projection.hidden,layer='HIDDEN');exporter.write(path)
    doc=ezdxf.readfile(path);doc.units=4
    for layer in LAYERS:
        if layer not in doc.layers:doc.layers.new(layer)
    model=doc.modelspace();b=projection.bounds
    margin=max(8,min(30,max(b.size.X,b.size.Y)*.05))
    style=dict(dimtxt=max(2.5,margin*.22),dimasz=max(2,margin*.18),dimtad=1,dimdec=2)
    for p1,p2,base,angle in [((b.min.X,b.min.Y),(b.max.X,b.min.Y),(b.min.X,b.min.Y-margin),0),
                              ((b.max.X,b.min.Y),(b.max.X,b.max.Y),(b.max.X+margin,b.min.Y),90)]:
        dim=model.add_linear_dim(base=base,p1=p1,p2=p2,angle=angle,override=style,dxfattribs={'layer':'DIMENSIONS'})
        dim.render()
    model.add_text(f'{identifier} {name} | mm | 1:1 model space | DRAFT',height=3,dxfattribs={'layer':'ANNOTATIONS','insert':(b.min.X,b.min.Y-2*margin)})
    model.add_text('Envelope sizes derived from estimated native CAD; review physical measurements',height=2.5,
        dxfattribs={'layer':'ANNOTATIONS','insert':(b.min.X,b.min.Y-2*margin-5)})
    doc.header['$INSUNITS']=4;doc.header['$MEASUREMENT']=1;doc.saveas(path)
    reopened=ezdxf.readfile(path)
    if reopened.audit().has_errors:raise ValueError('DXF audit failed: '+str(path))


def drawing_pdf(path,identifier,label,views,dimtable,*,paper='A3',custom_size=None,convention='third_angle',revision='R1'):
    width,height=custom_size or PAPERS[paper];pt=MM_TO_PT
    c=canvas.Canvas(str(path),pagesize=(width*pt,height*pt),pageCompression=1)
    c.setTitle(f'{identifier} six orthographic views - draft')
    positions=layout(convention);cellw=(width-24)/4;cellh=(height-55)/3
    scale=min(min((cellw-14)/max(v.bounds.size.X,1),(cellh-20)/max(v.bounds.size.Y,1)) for v in views.values())
    scale=min(1,scale)
    def plotted(projection,factor):
        element=projection.svg_element(factor*pt)
        for node in element.iter():
            node.tag=node.tag.split('}')[-1]
            if 'stroke-width' in node.attrib:
                node.set('stroke-width',str((.18 if node.attrib.get('id')=='CAD' else .10)/factor))
        element.set('xmlns','http://www.w3.org/2000/svg')
        drawing=svg2rlg(io.BytesIO(ET.tostring(element)))
        if drawing is None:raise ValueError('Native projection failed vector PDF conversion')
        return drawing
    def title(page_label):
        c.setFont('Helvetica-Bold',12);c.drawString(12*pt,(height-14)*pt,f'{identifier} - {label[:65]}')
        c.setFont('Helvetica',8);c.drawString(12*pt,(height-21)*pt,page_label)
        c.line(10*pt,23*pt,(width-10)*pt,23*pt)
        c.drawString(12*pt,17*pt,f'{revision} | {datetime.now(timezone.utc).date()} | mm | {convention} | DRAFT / REVIEW REQUIRED')
        c.drawString(12*pt,11*pt,'Material and joinery unconfirmed. Dimensions are native CAD envelope measurements of estimated geometry.')
    title(f'Six sides | scale 1:{1/scale:.3g} | orthographic hidden-line removal from native B-Rep')
    bounds=[]
    for name,(column,row) in positions.items():
        projection=views[name];drawing=plotted(projection,scale)
        x=(12+column*cellw+(cellw-drawing.width/pt)/2)*pt
        y=(28+row*cellh+(cellh-drawing.height/pt)/2)*pt
        renderPDF.draw(drawing,c,x,y)
        dims=dimtable[name]
        # Explicit measured envelope dimensions, using CAD geometry positions.
        c.saveState();c.setStrokeColorRGB(.15,.27,.35);c.setFillColorRGB(.15,.27,.35);c.setLineWidth(.35)
        dx=x+drawing.width;dy=y+drawing.height;gap=3*pt;tick=.8*pt
        for px in (x,dx):
            c.line(px,y-pt,px,y-5*pt);c.line(px-tick,y-gap-tick,px+tick,y-gap+tick)
        c.line(x,y-gap,dx,y-gap)
        for py in (y,dy):
            c.line(dx+pt,py,dx+5*pt,py);c.line(dx+gap-tick,py-tick,dx+gap+tick,py+tick)
        c.line(dx+gap,y,dx+gap,dy)
        c.setFont('Helvetica',6);c.drawCentredString((x+dx)/2,y-6*pt,f"{dims[0]['value_mm']:.2f} mm")
        c.translate(dx+6*pt,(y+dy)/2);c.rotate(90);c.drawCentredString(0,0,f"{dims[1]['value_mm']:.2f} mm")
        c.restoreState()
        c.setFont('Helvetica-Bold',8);c.drawCentredString((12+(column+.5)*cellw)*pt,(28+row*cellh+3)*pt,name.upper())
        c.setFont('Helvetica',6)
        c.drawCentredString((12+(column+.5)*cellw)*pt,(28+row*cellh-1)*pt,
            f"{dims[0]['value_mm']:.2f} x {dims[1]['value_mm']:.2f} mm ({dims[0]['classification']}/{dims[1]['classification']})")
        bounds.append(dict(view=name,page=1,x_pt=x,y_pt=y,width_pt=drawing.width,height_pt=drawing.height))
    c.showPage()
    # Large furniture receives extra, legible detail pages automatically.
    if scale<.1:
        for name,projection in views.items():
            detail=min(1,(width-45)/max(projection.bounds.size.X,1),(height-75)/max(projection.bounds.size.Y,1))
            title(f'{name.upper()} detail | scale 1:{1/detail:.3g}')
            drawing=plotted(projection,detail)
            renderPDF.draw(drawing,c,(width*pt-drawing.width)/2,(height*pt-drawing.height)/2)
            c.showPage()
    c.save()
    reader=PdfReader(path)
    if not reader.pages or any(page.get_contents() is None or not page.get_contents().get_data().strip() for page in reader.pages):raise ValueError('Blank drawing PDF')
    return dict(pages=len(reader.pages),scale=scale,paper_mm=[width,height],placements=bounds)


def generate_drawings(build,plan,output,*,known_dimensions=None,paper='A3',custom_size=None,convention='third_angle',revision='R1',projection_cache=None):
    output=Path(output);known_dimensions=known_dimensions or {}
    result=dict(engine='OCCT exact native hidden-line removal via build123d',projection=convention,units='mm',sheets=[])
    items=[('assembly','Furniture assembly',[s.val() for s in build.shapes.values()],output/'assembly')]
    items += [(p.part_id,p.label,[build.local_shapes[p.part_id].val()],output/'parts'/p.part_id) for p in build.parts]
    for identifier,label,shapes,directory in items:
        directory.mkdir(parents=True,exist_ok=True)
        geometry_hash=None;views=None
        if projection_cache:
            from .cad_projection_cache import fingerprint
            import cadquery as cq
            geometry_hash=fingerprint(cq.Compound.makeCompound(shapes))
            views=projection_cache.load(identifier,geometry_hash)
        views=views or six_views(shapes);entries={};dimtable={}
        if projection_cache:projection_cache.save(identifier,geometry_hash,views)
        for name,projection in views.items():
            dims=dimensions(projection,identifier,name,known_dimensions);dimtable[name]=dims
            write_view_svg(projection,directory/(name+'.svg'),identifier,name,dims,revision,convention,paper=paper,custom_size=custom_size)
            write_view_dxf(projection,directory/(name+'.dxf'),dims,identifier,name)
            entries[name]=dict(look_from=VIEWS[name].look_from,right=VIEWS[name].right,up=VIEWS[name].up,
                visible_edges=len(projection.visible.edges()),hidden_edges=len(projection.hidden.edges()),
                svg=str((directory/(name+'.svg')).relative_to(output)),dxf=str((directory/(name+'.dxf')).relative_to(output)),dimensions=dims)
        name='assembly_six_views.pdf' if identifier=='assembly' else 'six_views.pdf'
        sheet=drawing_pdf(directory/name,identifier,label,views,dimtable,paper=paper,custom_size=custom_size,convention=convention,revision=revision)
        result['sheets'].append(dict(part_id=identifier,coordinate_system='assembly_world' if identifier=='assembly' else 'part_local_stock_frame',
            pdf=str((directory/name).relative_to(output)),views=entries,**sheet))
    (output/'drawing_manifest.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    if projection_cache:projection_cache.finish()
    return result


def make_reports(build,output,drawing_manifest,report,registry):
    output=Path(output);manufacturing=output/'manufacturing';manufacturing.mkdir(parents=True,exist_ok=True)
    pdf=manufacturing/'manufacturing_drawings.pdf'
    buffer=io.BytesIO();c=canvas.Canvas(buffer,pagesize=(420*MM_TO_PT,297*MM_TO_PT))
    def lines(title,values):
        c.setFont('Helvetica-Bold',17);c.drawString(40,790,title);y=760
        for text in values:
            if y<65:c.showPage();c.setFont('Helvetica',10);y=790
            c.setFont('Helvetica',10)
            # Wrap long notes without clipping.
            import textwrap
            for chunk in textwrap.wrap(str(text),145):c.drawString(40,y,chunk);y-=15
        c.showPage()
    lines('Furniture manufacturing review package',[
        'DRAFT - not approved for manufacturing',
        f'Native components: {len(build.parts)}. Units: millimetres. Projection: '+drawing_manifest['projection'],
        'Original photograph and generated geometry are estimation evidence. CAD fit is not physical accuracy verification.',
        'Confirmed dimensions are labeled separately from CAD-derived envelope sizes.',
        'Material species, stock allowances, grain, fixings, joinery, loads, tolerances and tooling require designer review.',
        'Review every inferred part boundary before manufacture. No assembly sequence has been inferred.',
        *build.warnings])
    lines('Bill of materials / review cutting envelopes',[
        f"{p['part_id']} | {p['label']} | qty 1 | {p['local_envelope_mm'][0]:.2f} x {p['local_envelope_mm'][1]:.2f} x {p['local_envelope_mm'][2]:.2f} mm | material: unconfirmed"
        for p in registry['parts']])
    c.save();buffer.seek(0);writer=PdfWriter();writer.append(PdfReader(buffer))
    for sheet in drawing_manifest['sheets']:writer.append(output/'drawings'/sheet['pdf'])
    with pdf.open('wb') as handle:writer.write(handle)
    validation=output/'reports'/'geometry_validation.pdf';validation.parent.mkdir(exist_ok=True)
    c=canvas.Canvas(str(validation),pagesize=(420*MM_TO_PT,297*MM_TO_PT))
    lines('Geometry and evidence validation',[
        'Status: geometry validated where checks passed; physical size and manufacturing approval require review.',
        'Full-surface fitting summary: '+json.dumps(report.get('final',{})),
        'Photo verification: '+str(report.get('photo_verification_status','not available')),
        'Native geometry/contact checks: '+json.dumps(report.get('native_geometry_checks',{})),
        'Reference mesh, actual CAD overlay and deviation map are included in reference/ and the interactive viewer.',
        'FreeCAD editability: '+json.dumps(report.get('editable_cad',{})),
        'Outstanding assumptions: '+json.dumps(registry.get('uncertainties',[])),
        'Geometry fit does not establish correct assembly contacts, engineering joints or verified materials.'])
    from reportlab.lib.utils import ImageReader
    images=[('Original photograph','original_photo.png'),('Accepted native CAD','cad_evidence.png'),
            ('Reference mesh','mesh_evidence.png'),('Surface deviation display','deviation_evidence.png')]
    c.setFont('Helvetica-Bold',16);c.drawString(35,800,'Original evidence, native reconstruction and deviations')
    for index,(label,name) in enumerate(images):
        image=output/'reference'/name
        if not image.is_file():continue
        x=35+(index%2)*560;y=440-(index//2)*360
        c.setFont('Helvetica',10);c.drawString(x,y+325,label)
        c.drawImage(ImageReader(str(image)),x,y,width=530,height=310,preserveAspectRatio=True,anchor='c',mask='auto')
    c.setFont('Helvetica',9);c.drawString(35,45,'PDF previews may be sampled for display. Full-surface measurements and original files are included separately.')
    c.showPage()
    c.save()
    return dict(manufacturing_pdf=str(pdf.relative_to(output)),validation_pdf=str(validation.relative_to(output)))
