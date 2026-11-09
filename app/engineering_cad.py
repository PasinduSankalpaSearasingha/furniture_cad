"""Closed B-Rep part exports and previews made from reopened STEP geometry."""
import json
from dataclasses import replace
from zipfile import ZipFile,ZIP_DEFLATED
import cadquery as cq
import numpy as np
from app.pipeline import file_hash,canonical_hash
from app.geometry import exact_bounds
from app.ai.geometry_preview import tessellation,render_build,preview_png
from app.review_mesh import export_glb,export_component_stl
from app.cad_meshing import mesh_absolute,absolute_triangles,absolute_mesh_bounds,CADMeshError,CADTriangleBudgetError

SOURCE='ENGINEERING_CAD'
VOLUME_INTEGRATION_TOLERANCE=1e-10


def cad_bounds(shape):
    """Adaptive review envelope, avoiding trimmed-spline overbounds.

    OCCT AddOptimal can bound untrimmed portions of an imported spline even
    when its material, topology, volume and actual surface are unchanged.
    """
    from OCP.Bnd import Bnd_Box
    for tolerance in (.02,.05,.1,.2):
        try:minimum,maximum,mesh_report=absolute_mesh_bounds(shape,tolerance,.08)
        except CADTriangleBudgetError:continue
        recorded=mesh_report['recorded_deflection_mm']
        box=Bnd_Box();box.Update(*minimum,*maximum);result=cq.BoundBox(box)
        result.recorded_deflection_mm=recorded;result.requested_tolerance_mm=tolerance;return result
    raise CADTriangleBudgetError('Engineering envelope tessellation budget exceeded after bounded mesh coarsening')


def cad_bounds_for(shapes,axes):
    bounds=[cad_bounds(shape) for shape in shapes]
    spans=[(min(getattr(b,axis+'min') for b in bounds),max(getattr(b,axis+'max') for b in bounds)) for axis in 'xyz']
    return (*spans[axes[0]],*spans[axes[1]])


def render_preview(build,azimuth,elevation,size=(620,700),meshes=None):
    from app.ai.geometry_preview import render_build
    # Envelope checks use much finer meshes than a pixel image needs. Do not
    # accidentally rasterize their cached meshes for every preview.
    return render_build(build,azimuth=azimuth,elevation=elevation,size=size,meshes=meshes)


def check_supplied_dimensions(build,known_values):
    if not known_values:return []
    from app.agents.photo_agent import geometry_envelope
    actual=geometry_envelope(build);checked=[]
    from app.furniture_items import dimensions_for
    for item in getattr(build,'furniture_items',[])[1:]:
        actual.update({item['dimension_keys'][axis]:value for axis,value in dimensions_for(build,item['part_ids']).items()})
    for value in known_values:
        key=value['key']
        if key not in actual:continue
        error=abs(actual[key]-value['value'])
        if error>.01:raise ValueError(f'Supplied {key} must match the CAD envelope within 0.01 mm; requested {value["value"]:g}, actual {actual[key]:.6f}')
        checked.append(dict(parameter=key,requested_mm=value['value'],actual_mm=actual[key],error_mm=error,tolerance_mm=.01))
    return checked


def check_solid(shape):
    if not shape.isValid() or len(shape.Solids())!=1 or shape.Volume()<=1e-5:
        raise ValueError('Each engineering component must be one valid positive-volume solid')
    if any(not shell.Closed() for shell in shape.Shells()):raise ValueError('An engineering component has an open shell')


def brep_surface_samples(shape):
    """Sample trimmed analytic surfaces and edges independently of preview meshes.

    Triangulation nodes can be outside a trimmed face or off the exact surface,
    including on valid Bezier members with a stationary end tangent. They must
    not be treated as exact B-Rep points in a 0.001 mm STEP comparison.
    """
    from OCP.BRepClass import BRepClass_FaceClassifier
    from OCP.gp import gp_Pnt2d
    from OCP.TopAbs import TopAbs_IN,TopAbs_ON
    points=[]
    for face in shape.Faces():
        u0,u1,v0,v1=face.uvBounds()
        if not all(np.isfinite(value) for value in (u0,u1,v0,v1)):
            raise ValueError('STEP verification requires bounded surface parameters')
        for fu in (.1,.3,.5,.7,.9):
            for fv in (.1,.3,.5,.7,.9):
                u=u0+(u1-u0)*fu;v=v0+(v1-v0)*fv
                state=BRepClass_FaceClassifier(face.wrapped,gp_Pnt2d(u,v),1e-7).State()
                if state in (TopAbs_IN,TopAbs_ON):points.append(face.positionAt(u,v))
    for edge in shape.Edges():points.extend(edge.positions((.25,.5,.75)))
    points.extend(vertex.Center() for vertex in shape.Vertices())
    if not points:raise ValueError('STEP verification found no B-Rep surface samples')
    return points


def verify_solid_step(source,path):
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    check_solid(source);actual=cq.importers.importStep(str(path)).val();check_solid(actual)
    raw_a,raw_b=exact_bounds(source),exact_bounds(actual)
    raw_error=max(abs(getattr(raw_a,key)-getattr(raw_b,key)) for key in ('xmin','xmax','ymin','ymax','zmin','zmax'))
    a,b=cad_bounds(source),cad_bounds(actual)
    error=max(abs(getattr(a,key)-getattr(b,key)) for key in ('xmin','xmax','ymin','ymax','zmin','zmax'))
    # Default OCCT integration can disagree on equivalent trimmed spline faces.
    # Measure both solids adaptively instead of relaxing the round-trip limit.
    source_volume=source.Volume(VOLUME_INTEGRATION_TOLERANCE)
    step_volume=actual.Volume(VOLUME_INTEGRATION_TOLERANCE)
    if not all(np.isfinite(value) and value>1e-5 for value in (source_volume,step_volume)):
        raise ValueError('STEP verification requires finite positive adaptive volumes')
    volume_error=abs(source_volume-step_volume);volume_tolerance=max(1e-3,source_volume*1e-7)
    if error>.005 or volume_error>volume_tolerance:raise ValueError(
        f'STEP round-trip changed the component envelope or volume: bounds error {error:.9g} mm; '
        f'volume error {volume_error:.9g} mm3 / source volume {source_volume:.9g} mm3')
    maximum=0.;samples=0;worst_direction=None
    for direction,before,after in (('source to STEP',source,actual),('STEP to source',actual,source)):
        # Compare boundaries, so a point inside the other solid is not silently
        # accepted as a zero surface gap. Keep both directions and the same limit.
        boundary=cq.Compound.makeCompound(after.Faces())
        for point in brep_surface_samples(before):
            distance=BRepExtrema_DistShapeShape(cq.Vertex.makeVertex(*point.toTuple()).wrapped,boundary.wrapped)
            distance.Perform()
            if not distance.IsDone():raise ValueError('STEP surface-distance check failed')
            if distance.Value()>maximum:maximum=distance.Value();worst_direction=direction
            samples+=1
    if maximum>.001:raise ValueError(
        f'STEP surface round-trip exceeds 0.001 mm for {path.name}: '
        f'measured {maximum:.9g} mm ({worst_direction})')
    return actual,dict(valid_closed_solid=True,solid_count=1,step_reopened=True,
        source_volume_mm3=source_volume,step_volume_mm3=step_volume,volume_error_mm3=volume_error,
        volume_tolerance_mm3=volume_tolerance,volume_integration_relative_tolerance=VOLUME_INTEGRATION_TOLERANCE,
        source_kernel_default_volume_mm3=source.Volume(),step_kernel_default_volume_mm3=actual.Volume(),
        envelope_max_error_mm=error,envelope_tolerance_mm=.005,envelope_tessellation_mm=max(a.requested_tolerance_mm,b.requested_tolerance_mm),
        envelope_mesh_mode='ABSOLUTE',envelope_recorded_mesh_deflection_mm=max(a.recorded_deflection_mm,b.recorded_deflection_mm),
        raw_occt_bound_difference_mm=raw_error,sampled_surface_max_gap_mm=maximum,surface_samples=samples,
        surface_sampling_method='EXACT_BREP_TRIMMED_UV_EDGES_AND_VERTICES',surface_tolerance_mm=.001,
        verification='B-Rep validity, closed shells, volume, bounds and bidirectional surface samples; not an exact surface proof',
        physical_dimensions_verified=False,approved_for_manufacturing=False)


def part_sheet(output,part,solid,combined):
    import ezdxf
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4,landscape
    from app.drawings import pdf_view
    from app.drawing_projection import Projection,write_dxf
    stem=f'part_{part.part_id}_REVIEW';offset=0
    bb=cad_bounds(solid);text=max(bb.xlen,bb.ylen,bb.zlen)/65
    views=[('LOCAL XY',(0,1)),('LOCAL XZ',(0,2)),('LOCAL YZ',(1,2))]
    projections={axes:Projection([solid],axes) for _,axes in views}
    placements=[];positions=[]
    for title,axes in views:
        bounds=[(bb.xmin,bb.xmax),(bb.ymin,bb.ymax),(bb.zmin,bb.zmax)]
        x0,x1=bounds[axes[0]];y0,y1=bounds[axes[1]];width=x1-x0;height=y1-y0
        placements.append((projections[axes],offset-x0,-y0));positions.append((title,axes,offset,width,height))
        offset+=width+text*18
    doc=write_dxf(output/(stem+'.dxf'),placements);msp=doc.modelspace()
    for title,axes,offset,width,height in positions:
        style=dict(dimtxt=text,dimasz=text,dimdec=2)
        msp.add_linear_dim(base=(offset,-text*4),p1=(offset,0),p2=(offset+width,0),angle=0,override=style).render()
        msp.add_linear_dim(base=(offset-text*4,0),p1=(offset,0),p2=(offset,height),angle=90,override=style).render()
        msp.add_text(title,dxfattribs={'height':text}).set_placement((offset,height+text*3))
    for i,line in enumerate([part.part_id+' '+part.label,'CLOSED SOLID CAD - DIMENSIONS ARE MODEL ENVELOPES IN mm',
        'REVIEW: PHYSICAL MEASUREMENTS, STOCK, JOINTS AND TOLERANCES UNVERIFIED',
        'VISIBLE / HIDDEN CAD EDGES; NATIVE CURVES; NOT A CNC CUTTING PATH']):
        msp.add_text(line,dxfattribs={'height':text}).set_placement((0,-text*(12+i*2)))
    doc.saveas(output/(stem+'.dxf'))
    single=canvas.Canvas(str(output/(stem+'.pdf')),pagesize=landscape(A4))
    for pdf in (single,combined):
        pdf.setFont('Helvetica-Bold',15);pdf.drawString(25,565,part.part_id+' - '+part.label)
        pdf.setFont('Helvetica',9);pdf.drawString(25,544,'Closed solid geometry. Physical dimensions, stock and joints require engineering review.')
        for i,(title,axes) in enumerate(views):pdf_view(pdf,[solid],axes,(25+i*270,100,245,410),title,cad_bounds_for,projections[axes])
        pdf.drawString(25,70,'Vector projections with hidden lines. These review sheets do not specify machining tolerances.')
        pdf.showPage()
    single.save()
    return dict(file=stem+'.dxf',drawing_number='PT-'+part.part_id,revision=part.revision,
        status='REVIEW',units='mm',views=['LOCAL XY','LOCAL XZ','LOCAL YZ'],
        geometry_source=SOURCE,drawing_engine='build123d',hidden_lines=True,curve_representation='NATIVE_DXF')


def geometry_checks(build):
    from itertools import combinations
    contacts=[];interferences=[];edges={p.part_id:set() for p in build.registry}
    for shape in build.shapes.values():check_solid(shape.val())
    for a,b in combinations(build.shapes,2):
        sa,sb=build.shapes[a].val(),build.shapes[b].val()
        ba,bb=exact_bounds(sa),exact_bounds(sb)
        if any(getattr(ba,c+'max')+.5<getattr(bb,c+'min') or getattr(bb,c+'max')+.5<getattr(ba,c+'min') for c in 'xyz'):continue
        gap=sa.distance(sb)
        if gap<=.5:
            overlap=sa.intersect(sb).Volume()
            contacts.append(dict(a=a,b=b,gap_mm=gap,overlap_mm3=overlap));edges[a].add(b);edges[b].add(a)
            if overlap>max(.01,min(sa.Volume(),sb.Volume())*1e-6):interferences.append(dict(a=a,b=b,overlap_mm3=overlap))
    if interferences:raise ValueError('Engineering parts intersect after contact trimming: '+str(interferences)[:400])
    visited=set();groups=[]
    for pid in edges:
        if pid in visited:continue
        queue=[pid];group=[]
        while queue:
            item=queue.pop()
            if item in visited:continue
            visited.add(item);group.append(item);queue.extend(edges[item]-visited)
        groups.append(group)
    from app.furniture_items import separate_item_checks
    item_checks=[]
    for item in getattr(build,'furniture_items',[]):
        members=set(item['part_ids'])
        components=[];seen=set()
        for pid in item['part_ids']:
            if pid in seen:continue
            queue=[pid];component=[]
            while queue:
                member=queue.pop()
                if member in seen:continue
                seen.add(member);component.append(member);queue.extend((edges[member] & members)-seen)
            components.append(sorted(component))
        item_checks.append(dict(item_id=item['item_id'],label=item['label'],part_ids=item['part_ids'],
            connected=len(components)==1,contact_groups=components))
    return dict(valid_closed_solids=True,part_count=len(build.parts),contacts=contacts,
        contact_groups=groups,assembly_connected=len(groups)==1,contact_tolerance_mm=.5,
        furniture_items_connected=all(item['connected'] for item in item_checks) if item_checks else len(groups)==1,
        furniture_items=item_checks,separate_item_dimensions=separate_item_checks(build),
        interference_check_passed=True,physical_joinery_verified=False,structural_strength_verified=False)


def build_engineering_package(config,spec,output,evidence,progress=lambda *_:None,source_build=None,source_checks=None):
    if evidence.get('output_scope')=='WHOLE_FURNITURE':
        from app.whole_furniture import build_whole_furniture_package
        return build_whole_furniture_package(config,spec,output,evidence,progress,source_build,source_checks)
    from app.photo_cad import create_photo_furniture
    from app.drawings import export_drawings
    from app.agents.manufacturing_agent import ManufacturingDocumentationAgent
    from app.validation_engine import ValidationReport,Finding
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4,landscape
    output.mkdir(exist_ok=False);build=source_build or create_photo_furniture(spec)
    if source_build is not None:
        from app.registry import PartsRegistry
        registry=PartsRegistry()
        for part in build.registry:registry.add(part.model_copy(update={'revision':spec.revision}))
        build=replace(build,parts=list(registry),registry=registry)
    checks=source_checks or geometry_checks(build)
    from app.registry import PartsRegistry
    solids={};locals_={};proofs=[];records=[];step_registry=PartsRegistry()
    assembly=cq.Assembly(name='Engineering_CAD_REVIEW')
    for i,part in enumerate(build.registry):
        progress('engineering_exporting_parts',76+round(i/len(build.parts)*12))
        stem=f'part_{part.part_id}_REVIEW';path=output/(stem+'.step')
        cq.exporters.export(build.shapes[part.part_id],str(path))
        try:actual,proof=verify_solid_step(build.shapes[part.part_id].val(),path)
        except ValueError as exc:raise ValueError(f'{part.part_id} {part.label}: {exc}') from exc
        solids[part.part_id]=cq.Workplane('XY').newObject([actual])
        plane=cq.Plane(origin=part.center_xyz_mm,xDir=part.length_axis,normal=part.thickness_axis)
        local=plane.toLocalCoords(actual);locals_[part.part_id]=cq.Workplane('XY').newObject([local])
        assembly.add(actual,name=part.part_id,color=build.assembly.objects[part.part_id].color)
        single=replace(build,parts=[part],registry=[part],shapes={part.part_id:solids[part.part_id]})
        preview_deflection=mesh_absolute(actual,.1,clear=True)
        export_glb(single,output/(stem+'.glb'),.1);export_component_stl(single,output/(stem+'.stl'),.1)
        points,triangles,_=tessellation(actual,.1);preview_png(output/f'part_{part.part_id}_preview.png',points,triangles)
        record=part.model_dump(mode='json');lb=cad_bounds(local)
        corrected=dict(length_mm=lb.xlen,width_mm=lb.ylen,thickness_mm=lb.zlen,
            blank_length_mm=lb.xlen,blank_width_mm=lb.ylen,blank_thickness_mm=lb.zlen)
        step_registry.add(part.model_copy(update=corrected))
        record.update(length_mm=lb.xlen,width_mm=lb.ylen,thickness_mm=lb.zlen,
            blank_length_mm=lb.xlen,blank_width_mm=lb.ylen,blank_thickness_mm=lb.zlen,
            dimension_method='Adaptive STEP surface envelope; requested 0.02 mm absolute deflection',geometry_source=SOURCE,solid_valid=True,
            manufacturing_status='ENGINEERING_SOLID_REVIEW',physical_part_verified=False,boundaries_estimated=False,
            model_file=stem+'.glb',preview_file=f'part_{part.part_id}_preview.png',
            files={kind:stem+'.'+kind for kind in ('step','stl','glb','dxf','pdf')},
            note='Closed B-Rep part. GLB/STL use reopened STEP with requested absolute 0.1 mm mesh deflection; recorded mesh details are in the geometry report. Dimensions are CAD envelopes; stock, joints and physical measurements are unverified.')
        records.append(record);proofs.append(dict(part_id=part.part_id,**proof,step_sha256=file_hash(path),
            preview_source='REOPENED_PART_STEP',preview_tessellation_mm=.1,preview_mesh_mode='ABSOLUTE',
            preview_recorded_mesh_deflection_mm=preview_deflection))
    reopened=replace(build,shapes=solids,local_shapes=locals_,assembly=assembly,registry=step_registry,parts=list(step_registry))
    progress('engineering_verifying_exports',90)
    assembly.export(str(output/'cabinet.step'))
    full=cq.importers.importStep(str(output/'cabinet.step')).val()
    if not full.isValid() or len(full.Solids())!=len(records):raise ValueError('Assembly STEP lost valid solid parts')
    for shape in reopened.shapes.values():mesh_absolute(shape.val(),.1,clear=True)
    export_glb(reopened,output/'cabinet.glb',.1);export_component_stl(reopened,output/'cabinet.stl',.1)
    drawings=export_drawings(spec,reopened,output,cad_bounds_for,include_parts=False)
    # Each physical part gets one consistent three-view drawing.
    combined=canvas.Canvas(str(output/'parts_review_drawings.pdf'),pagesize=landscape(A4))
    for part in build.registry:
        drawings['drawings'].append(part_sheet(output,part,locals_[part.part_id].val(),combined))
    combined.save()
    for item in drawings['drawings']:
        if item['file'].startswith('part_'):item.update(views=['LOCAL XY','LOCAL XZ','LOCAL YZ'],geometry_source=SOURCE)
    from app.dimension_sheet import create_dimension_sheet
    sheet=create_dimension_sheet(spec,reopened,output,provenance=evidence.get('value_provenance'),job_id=output.name.split('_engineering_')[0])
    drawings['combined_dimension_sheet']=dict(files=sheet['files'],views=sheet['views'],pages=1,
        dimension_count=len(sheet['dimensions']),parameter_count=len(sheet['parameters']),part_count=len(sheet['parts']))
    from app.techdraw import enhance_drawings
    drawings['generation']=enhance_drawings(config,spec,records,proofs,sheet['overall_dimensions_mm'],output)
    (output/'drawing_manifest.json').write_text(json.dumps(drawings,indent=2),encoding='utf-8')
    documentation=ManufacturingDocumentationAgent(spec,reopened)
    report=ValidationReport(revision=spec.revision,findings=[
        Finding(code='CLOSED_SOLID_EXPORT_CHECKED',severity='INFO',category='geometric',
            message='Separate closed solids, assembly contacts, interfering volumes and reopened STEP geometry were checked.'),
        Finding(code='PHYSICAL_DIMENSIONS_UNVERIFIED',severity='BLOCKER',category='dimensional',
            message='Provided dimensions express design inputs; unsupplied measurements are estimates. Physical furniture measurements are unverified.'),
        Finding(code='CONSTRUCTION_ENGINEERING_REQUIRED',severity='BLOCKER',category='manufacturing',
            message='Stock, joints, hardware, machining tolerances and structural strength require engineering review.')])
    if not (evidence.get('reviews') or {}).get('visual',{}).get('accepted',False):
        report.findings.append(Finding(code='PHOTO_MATCH_NEEDS_REVIEW',severity='WARNING',category='visual',
            message='The visual review retained differences from the reference photograph. Inspect engineering_workflow.json.'))
    documentation.export_documentation(output,report)
    validation=report.as_dict();validation.update(geometry_source=SOURCE,geometry_checks=checks,
        manufacturing_approved=False,has_blockers=True)
    agreement=dict(geometry_source=SOURCE,valid_closed_solids=True,part_count=len(records),
        assembly_solid_count=len(full.Solids()),all_part_steps_reopened=True,preview_source='REOPENED_PART_STEP',
        preview_tessellation_mm=.1,raster_preview_tessellation_mm=.3,parts=proofs,assembly_step_sha256=file_hash(output/'cabinet.step'),
        assembly_glb_sha256=file_hash(output/'cabinet.glb'),geometry_checks=checks,
        physical_dimensions_verified=False,exact_reconstruction_verified=False,approved_for_manufacturing=False)
    metadata={'parts_registry.json':records,'geometry_verification.json':agreement,'validation_report.json':validation,
        'engineering_workflow.json':evidence,'specification.json':spec.model_dump(mode='json')}
    for name,value in metadata.items():(output/name).write_text(json.dumps(value,indent=2),encoding='utf-8')
    view_angles=[('cad_preview',-110,15),('engineering_front',-90,0),('engineering_rear',90,0),
        ('engineering_left',180,0),('engineering_right',0,0),('engineering_top',-90,89),('engineering_bottom',-90,-89)]
    agreement['view_files']={name.removeprefix('engineering_'):name+'.png' for name,_,_ in view_angles}
    from app.ai.geometry_preview import preview_meshes
    mesh_report={};meshes=preview_meshes(reopened,report=mesh_report)
    agreement['raster_preview_meshes']=mesh_report
    for name,az,el in view_angles:
        (output/(name+'.png')).write_bytes(render_preview(reopened,az,el,meshes=meshes))
    # The view inventory is available to the UI and to downstream packages.
    (output/'geometry_verification.json').write_text(json.dumps(agreement,indent=2),encoding='utf-8')
    (output/'engineering_notes.md').write_text('# Engineering CAD review\n\nOne closed solid per part. '
        'STEP is authoritative; all previews are tessellated from reopened part STEP files. '
        'Dimensions omitted by the user are AI estimates. Physical construction, stock, joints, tolerances and structural strength remain unverified. '
        'Assembly STL contains separate component shells; no printable union is certified.\n',encoding='utf-8')
    manifest=dict(schema_version=1,revision=spec.revision,status='REVIEW',geometry_source=SOURCE,
        primary_model='cabinet.glb',cad_files=dict(step='cabinet.step',glb='cabinet.glb',stl='cabinet.stl'),
        spec_hash=canonical_hash(spec.model_dump(mode='json')),part_count=len(records),
        files={p.name:file_hash(p) for p in sorted(output.iterdir()) if p.is_file()},approved_for_manufacturing=False)
    if drawings['generation']['techdraw_status']=='COMPLETED':
        manifest['cad_files']['freecad']='furniture_REVIEW.FCStd'
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    with ZipFile(output/'design_review_package.zip','w',ZIP_DEFLATED) as archive:
        for path in sorted(output.iterdir()):
            if path.suffix!='.zip':archive.write(path,path.name)
    return dict(agreement=agreement,validation=validation,package_hash=file_hash(output/'design_review_package.zip'),
        manifest_hash=file_hash(output/'manifest.json'))
