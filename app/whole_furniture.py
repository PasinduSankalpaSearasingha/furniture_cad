"""Assembly-only deliverables with temporary STEP sources for solid verification."""
from dataclasses import replace
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from zipfile import ZipFile,ZIP_DEFLATED

import cadquery as cq
from app.pipeline import canonical_hash,file_hash
from app.registry import PartsRegistry
from app.validation_engine import ValidationReport,Finding
from app.cad_meshing import CADMeshError


def build_whole_furniture_package(config,spec,output,evidence,progress,source_build=None,source_checks=None):
    from app.photo_cad import create_photo_furniture
    from app.engineering_cad import (SOURCE,geometry_checks,verify_solid_step,cad_bounds,
        mesh_absolute,render_preview)
    from app.review_mesh import export_glb,export_component_stl
    from app.furniture_sheet import create_furniture_sheet
    from app.techdraw import enhance_drawings
    output.mkdir(exist_ok=False);build=source_build or create_photo_furniture(spec)
    registry=PartsRegistry()
    for part in build.registry:registry.add(part.model_copy(update={'revision':spec.revision}))
    build=replace(build,registry=registry,parts=list(registry))
    checks=source_checks or geometry_checks(build)
    records=[];proofs=[];solids={};locals_={};verified=PartsRegistry()
    assembly=cq.Assembly(name='Whole_Furniture_REVIEW')
    # Separate sources exist only inside this temporary directory. They never
    # become part downloads, previews, drawing pages or packaged deliverables.
    with TemporaryDirectory(prefix='.solid-verification-',dir=output) as temporary:
        scratch=Path(temporary)
        for i,part in enumerate(build.registry):
            progress('engineering_verifying_furniture',76+round(i/len(build.parts)*10))
            path=scratch/f'part_{part.part_id}_REVIEW.step'
            cq.exporters.export(build.shapes[part.part_id],str(path))
            try:actual,proof=verify_solid_step(build.shapes[part.part_id].val(),path)
            except CADMeshError as exc:raise CADMeshError(f'{part.part_id} {part.label}: {exc}') from exc
            except ValueError as exc:raise ValueError(f'{part.part_id} {part.label}: {exc}') from exc
            solids[part.part_id]=cq.Workplane('XY').newObject([actual])
            plane=cq.Plane(origin=part.center_xyz_mm,xDir=part.length_axis,normal=part.thickness_axis)
            local=plane.toLocalCoords(actual);locals_[part.part_id]=cq.Workplane('XY').newObject([local])
            assembly.add(actual,name=part.part_id,color=build.assembly.objects[part.part_id].color)
            b=cad_bounds(local)
            part=part.model_copy(update=dict(length_mm=b.xlen,width_mm=b.ylen,thickness_mm=b.zlen,
                blank_length_mm=b.xlen,blank_width_mm=b.ylen,blank_thickness_mm=b.zlen))
            verified.add(part)
            records.append(dict(part.model_dump(mode='json'),files={},model_file='cabinet.glb',
                preview_file='cad_preview.png',output_scope='WHOLE_FURNITURE',geometry_source=SOURCE,
                solid_valid=True,physical_part_verified=False,manufacturing_status='ENGINEERING_SOLID_REVIEW'))
            proofs.append(dict(part_id=part.part_id,step_sha256=file_hash(path),**proof))
        reopened=replace(build,shapes=solids,local_shapes=locals_,assembly=assembly,registry=verified,parts=list(verified))
        assembly.export(str(output/'cabinet.step'))
        full=cq.importers.importStep(str(output/'cabinet.step')).val()
        if not full.isValid() or len(full.Solids())!=len(records):raise ValueError('Assembly STEP lost valid solid parts')
        for shape in solids.values():mesh_absolute(shape.val(),.1,clear=True)
        export_glb(reopened,output/'cabinet.glb',.1);export_component_stl(reopened,output/'cabinet.stl',.1)
        progress('engineering_exporting_furniture_drawing',90)
        sheet=create_furniture_sheet(spec,reopened,output,provenance=evidence.get('value_provenance'),
            job_id=output.name.split('_engineering_')[0],budget_draft=evidence.get('budget_draft'))
        native=enhance_drawings(config,spec,records,proofs,sheet.get('layout_dimensions_mm',sheet['overall_dimensions_mm']),scratch,assembly_only=True)
        for filename in ['drawing_generation.json',*native.get('files',[])]:
            shutil.copyfile(scratch/filename,output/filename)
    deliverables=['cabinet.step','cabinet.glb','cabinet.stl',*sheet['files'].values()]
    if native['techdraw_status']=='COMPLETED':deliverables.append('furniture_REVIEW.FCStd')
    report=ValidationReport(revision=spec.revision,findings=[
        Finding(code='CLOSED_SOLID_EXPORT_CHECKED',severity='INFO',category='geometric',
            message='Internal solids, assembly contacts, dimensions and reopened STEP geometry were checked.'),
        Finding(code='PHYSICAL_DIMENSIONS_UNVERIFIED',severity='BLOCKER',category='dimensional',
            message='Supplied measurements are design inputs; missing measurements are estimates.'),
        Finding(code='CONSTRUCTION_ENGINEERING_REQUIRED',severity='BLOCKER',category='manufacturing',
            message='This whole-furniture model does not specify production joints, stock, tolerances or structural strength.')])
    visual=(evidence.get('reviews') or {}).get('visual',{})
    budget_draft=evidence.get('budget_draft')
    if budget_draft:
        report.findings.append(Finding(code={'REPAIR_LIMIT_DRAFT':'CAD_REPAIR_LIMIT_DRAFT','PREVIEW_UNAVAILABLE_DRAFT':'CAD_PREVIEW_UNAVAILABLE'}.get(budget_draft['status'],'AI_BUDGET_DRAFT'),severity='WARNING',category='review',
            message=budget_draft.get('message','Further corrections stopped. Available geometry was exported as an unfinished draft.')))
        for issue in budget_draft['remaining_issues']:
            report.findings.append(Finding(**issue,severity='BLOCKER',category='geometric'))
    if not visual.get('accepted',False):
        report.findings.append(Finding(code='PHOTO_MATCH_NEEDS_REVIEW',severity='WARNING',category='visual',
            message='Inspect retained differences from the reference photograph.'))
    validation=report.as_dict();validation.update(geometry_source=SOURCE,geometry_checks=checks,
        manufacturing_approved=False,has_blockers=True)
    agreement=dict(geometry_source=SOURCE,output_scope='WHOLE_FURNITURE',valid_closed_solids=True,
        budget_draft=budget_draft,
        part_count=len(records),assembly_solid_count=len(full.Solids()),all_part_steps_reopened=True,
        preview_source='REOPENED_PART_STEP',preview_tessellation_mm=.1,preview_mesh_mode='ABSOLUTE',
        parts=proofs,assembly_step_sha256=file_hash(output/'cabinet.step'),assembly_glb_sha256=file_hash(output/'cabinet.glb'),
        geometry_checks=checks,physical_dimensions_verified=False,exact_reconstruction_verified=False,
        approved_for_manufacturing=False)
    camera=evidence.get('reference_view') or dict(azimuth_deg=-110,elevation_deg=15)
    views=[('cad_preview',camera['azimuth_deg'],camera['elevation_deg']),('engineering_front',-90,0),('engineering_rear',90,0),
        ('engineering_left',180,0),('engineering_right',0,0),('engineering_top',-90,89),('engineering_bottom',-90,-89)]
    agreement['view_files']={name.removeprefix('engineering_'):name+'.png' for name,_,_ in views}
    agreement['view_files']['reference']=agreement['view_files'].pop('cad_preview')
    agreement['photo_match']=evidence.get('photo_match')
    from app.ai.geometry_preview import preview_meshes
    mesh_report={};meshes=preview_meshes(reopened,report=mesh_report)
    agreement['raster_preview_meshes']=mesh_report
    for name,az,el in views:(output/(name+'.png')).write_bytes(render_preview(reopened,az,el,
        size=tuple(evidence.get('reference_render_size',(620,700))) if name=='cad_preview' else (620,700),meshes=meshes))
    drawings=dict(output_scope='WHOLE_FURNITURE',drawings=[],generation=native,
        combined_dimension_sheet=dict(files=sheet['files'],views=sheet['views'],pages=1,
            dimension_count=len(sheet['dimensions']),parameter_count=len(sheet['parameters']),part_count=0))
    drawings['combined_dimension_sheet']['radius_dimension_count']=len(sheet.get('radius_dimensions',[]))
    metadata={'parts_registry.json':records,'geometry_verification.json':agreement,'validation_report.json':validation,
        'engineering_workflow.json':evidence,'specification.json':spec.model_dump(mode='json'),'drawing_manifest.json':drawings,
        'schedules_REVIEW.json':dict(parts=[],bom=[],hardware=[],output_scope='WHOLE_FURNITURE')}
    for name,value in metadata.items():(output/name).write_text(json.dumps(value,indent=2),encoding='utf-8')
    manifest=dict(schema_version=1,revision=spec.revision,status='REVIEW',geometry_source=SOURCE,
        output_scope='WHOLE_FURNITURE',deliverables=deliverables,primary_model='cabinet.glb',
        cad_files=dict(step='cabinet.step',glb='cabinet.glb',stl='cabinet.stl'),
        spec_hash=canonical_hash(spec.model_dump(mode='json')),part_count=len(records),approved_for_manufacturing=False,
        files={path.name:file_hash(path) for path in sorted(output.iterdir()) if path.is_file()})
    if native['techdraw_status']=='COMPLETED':manifest['cad_files']['freecad']='furniture_REVIEW.FCStd'
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    with ZipFile(output/'design_review_package.zip','w',ZIP_DEFLATED) as archive:
        for path in sorted(output.iterdir()):
            if path.suffix!='.zip':archive.write(path,path.name)
    return dict(agreement=agreement,validation=validation,package_hash=file_hash(output/'design_review_package.zip'),
        manifest_hash=file_hash(output/'manifest.json'))
