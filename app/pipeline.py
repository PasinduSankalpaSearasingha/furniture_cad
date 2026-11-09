"""Deterministic file pipeline, also usable without HTTP for engineering review."""
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from .models import CabinetSpec
from .cad import FURNITURE_TEMPLATES, export_cad
from .drawings import export_drawings
from .manufacturing import schedules_from_registry, export_manufacturing
from .validation_engine import validate_build


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def file_hash(path):
    digest = hashlib.sha256()
    with open(path,'rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): digest.update(block)
    return digest.hexdigest()


def build_package(spec: CabinetSpec, output: Path, progress=lambda stage,percent:None, verified_context=None):
    from .agents.manufacturing_agent import ManufacturingDocumentationAgent
    from .agents.cad_agent import generate_parametric_cad, export_cad_assembly
    if verified_context and canonical_hash(spec.model_dump(mode='json')) != verified_context.specification_hash:
        raise ValueError('Package specification differs from verified CAD context')
    output.mkdir(parents=True,exist_ok=False)
    progress('building_cad',10)
    build = generate_parametric_cad(verified_context) if verified_context else FURNITURE_TEMPLATES[spec.family](spec)
    progress('exporting_cad',30)
    if verified_context: export_cad_assembly(verified_context,build,output)
    else: export_cad(build,output)
    progress('exporting_drawings',55)
    documentation = ManufacturingDocumentationAgent(spec,build)
    drawings = documentation.generate_shop_drawings(output)
    progress('validating',75)
    schedules = documentation.schedules
    report = validate_build(spec,build,schedules,drawings)
    documentation.export_documentation(output,report)
    if spec.photo_design:
        (output/'feature_history.json').write_text(json.dumps(dict(
            units='mm; component-local frame',status='REVIEW',approved_for_manufacturing=False,
            parts=[dict(part_id=p.part_id,geometry=p.geometry,features=[f.model_dump(mode='json') for f in p.features])
                   for p in spec.photo_design.parts]),indent=2),encoding='utf-8')
        (output/'photo_reconstruction.json').write_text(json.dumps(dict(
            design=spec.photo_design.model_dump(mode='json'),status='PHOTO_DERIVED_ESTIMATE',
            approved_for_manufacturing=False,exact_reconstruction_verified=False,limitations=build.warnings),indent=2),encoding='utf-8')
    if spec.furniture_template:
        from .furniture_catalog import template
        (output/'furniture_estimates.json').write_text(json.dumps(dict(
            template=template(spec.furniture_template),specification=spec.model_dump(mode='json'),
            status='ESTIMATED_REVIEW_ONLY',approved_for_manufacturing=False,
            limitations=build.warnings),indent=2),encoding='utf-8')
    if spec.display_layout is not None:
        review = dict(template_id='two_section_display.review.v1',status='ESTIMATED_REVIEW_ONLY',
            source=spec.display_layout.model_dump(mode='json'),
            overall_dimensions_mm=dict(width=spec.width_mm,depth=spec.depth_mm,height=spec.height_mm),
            dimension_basis='Estimated proportions; no measurement or physical scale recovered from the photograph',
            represented=['two stacked compartments','two framed front glass doors','two upper side glass panels',
                         'stepped plinth, waist and cornice','raised pilasters','simplified X and diamond ornaments'],
            unresolved=['actual dimensions and stock','original carved profiles','hinges and handles','door swing',
                        'machining joinery','glass specification and retention','structural and stability qualification'],
            approved_for_manufacturing=False)
        (output/'reference_review.json').write_text(json.dumps(review,indent=2),encoding='utf-8')
    progress('rendering_preview',85)
    from .ai.cad_preview import render_step_preview
    if spec.photo_design:
        from .ai.geometry_preview import render_build
        preview=render_build(build)
    else: preview=render_step_preview(output)
    (output/'cad_preview.png').write_bytes(preview)
    progress('packaging',90)
    manifest = dict(schema_version=1,revision=spec.revision,status='REVIEW',
                    cad_units={'STEP':'mm; Z up','GLB':'metres; Y up','STL':'mm (unitless format)'},
                    approved_for_manufacturing=False,spec_hash=canonical_hash(spec.model_dump(mode='json')),
                    files={p.name:file_hash(p) for p in sorted(output.iterdir()) if p.is_file()})
    if spec.display_layout is not None: manifest['template_id'] = 'two_section_display.review.v1'
    if spec.furniture_template: manifest['template_id']=spec.furniture_template
    if spec.photo_design:
        manifest['design_source']='PHOTO_DERIVED_COMPONENT_PLAN'
        manifest['exact_reconstruction_verified']=False
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    package = output/'design_review_package.zip'
    with ZipFile(package,'w',ZIP_DEFLATED) as archive:
        for path in sorted(output.iterdir()):
            if path != package: archive.write(path,arcname=path.name)
    return dict(validation=report.as_dict(),package_hash=file_hash(package),
                manifest_hash=file_hash(output/'manifest.json'),parts=len(build.parts))


def safe_directory(root: Path, name: str):
    root = root.resolve()
    target = (root/name).resolve()
    if target.parent != root or (root/name).is_symlink():
        raise ValueError('Unsafe storage path')
    return target


def verify_package(directory, job, spec_hash):
    package = directory/'design_review_package.zip'
    manifest_file = directory/'manifest.json'
    if package.is_symlink() or manifest_file.is_symlink(): raise ValueError('Symlink artifact refused')
    if file_hash(package) != job.package_hash or file_hash(manifest_file) != job.manifest_hash:
        raise ValueError('Package hash mismatch')
    manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
    if manifest['spec_hash'] != spec_hash: raise ValueError('Specification hash mismatch')
    with ZipFile(package) as archive:
        if set(archive.namelist()) != set(manifest['files']) | {'manifest.json'}:
            raise ValueError('Archive contents mismatch')
        for name,expected in manifest['files'].items():
            path = directory/name
            if Path(name).name != name or path.is_symlink() or path.resolve().parent != directory.resolve():
                raise ValueError('Unsafe artifact path')
            if file_hash(path) != expected or hashlib.sha256(archive.read(name)).hexdigest() != expected:
                raise ValueError('Artifact hash mismatch')
        if archive.read('manifest.json') != manifest_file.read_bytes(): raise ValueError('Manifest mismatch')
    return package


def verify_artifact(directory,job,spec_hash,filename):
    """Check the DB-bound manifest and requested file without rehashing the ZIP.

    Whole-package checks still run on listings, exports and package downloads.
    Small thumbnails must not decompress every dense STEP in the package.
    """
    manifest_path=safe_directory(directory,'manifest.json')
    raw=manifest_path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=job.manifest_hash:raise ValueError('Manifest hash mismatch')
    manifest=json.loads(raw)
    if manifest['spec_hash']!=spec_hash:raise ValueError('Specification hash mismatch')
    if filename=='design_review_package.zip':return verify_package(directory,job,spec_hash)
    expected=job.manifest_hash if filename=='manifest.json' else manifest['files'][filename]
    path=safe_directory(directory,filename)
    if file_hash(path)!=expected:raise ValueError('Artifact hash mismatch')
    return path
