"""Private cached sheets derived from a specific completed CAD package."""
import json
import shutil
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED

import cadquery as cq

from app.dimension_sheet import create_dimension_sheet, STEM, VERSION
from app.models import CabinetSpec, Part
from app.pipeline import canonical_hash, file_hash, safe_directory

_locks = {}
_guard = Lock()


def sheet_directory(config, job):
    return safe_directory(config.storage, f'dimension_sheet_{job.id}_v{VERSION}')


def verify_sheet(directory, job, revision):
    metadata=json.loads((directory/'sheet_manifest.json').read_text(encoding='utf-8'))
    if metadata['job_id']!=job.id or metadata['revision_hash']!=revision.spec_hash or metadata['source_manifest_hash']!=job.manifest_hash:
        raise ValueError('Dimension sheet source changed')
    for name,digest in metadata['files'].items():
        if file_hash(safe_directory(directory,name))!=digest:raise ValueError('Dimension sheet integrity verification failed')
    return metadata


def ensure_sheet(config, job, revision, source):
    """The caller authenticates access and verifies the original CAD package."""
    directory=sheet_directory(config,job)
    with _guard:lock=_locks.setdefault(str(directory),Lock())
    with lock:
        if directory.exists():return directory,verify_sheet(directory,job,revision)
        manifest=json.loads((source/'manifest.json').read_text(encoding='utf-8'))
        if manifest.get('geometry_source')!='ENGINEERING_CAD':raise ValueError('Generate closed solid Engineering CAD before creating this sheet')
        whole=manifest.get('output_scope')=='WHOLE_FURNITURE'
        registry=[] if whole else json.loads((source/'parts_registry.json').read_text(encoding='utf-8'));parts=[];shapes={}
        for record in registry:
            part=Part.model_validate({key:value for key,value in record.items() if key in Part.model_fields})
            name=record['files']['step']
            if name not in manifest['files'] or file_hash(safe_directory(source,name))!=manifest['files'][name]:raise ValueError('Part STEP integrity verification failed')
            shape=cq.importers.importStep(str(source/name))
            if len(shape.solids().vals())!=1 or not shape.val().isValid():raise ValueError('The drawing requires one valid solid per part')
            parts.append(part);shapes[part.part_id]=shape
        temporary=safe_directory(config.storage,directory.name+'_'+str(uuid4()))
        temporary.mkdir()
        spec=CabinetSpec.model_validate(revision.specification)
        if canonical_hash(spec.model_dump(mode='json'))!=revision.spec_hash:raise ValueError('Dimension sheet specification changed')
        if whole:
            # The exact accepted sheet is already in the verified revision; no
            # part STEP files or additional CAD/AI generation are needed here.
            for suffix in ('pdf','dxf','svg','json'):
                name=STEM+'.'+suffix
                if name not in manifest['files'] or file_hash(source/name)!=manifest['files'][name]:
                    raise ValueError('Furniture drawing integrity verification failed')
                shutil.copyfile(source/name,temporary/name)
            sheet=json.loads((temporary/(STEM+'.json')).read_text(encoding='utf-8'))
        else:
            sheet=create_dimension_sheet(spec,SimpleNamespace(registry=parts,shapes=shapes),temporary,
                provenance=revision.value_provenance,job_id=job.id)
        with ZipFile(temporary/(STEM+'.zip'),'w',ZIP_DEFLATED) as archive:
            for path in sorted(temporary.iterdir()):
                if path.suffix!='.zip':archive.write(path,path.name)
        metadata=dict(version=VERSION,job_id=job.id,revision_id=revision.id,revision_hash=revision.spec_hash,
            source_manifest_hash=job.manifest_hash,sheet=sheet,
            files={path.name:file_hash(path) for path in temporary.iterdir()})
        (temporary/'sheet_manifest.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
        try:temporary.rename(directory)
        except FileExistsError:return directory,verify_sheet(directory,job,revision)
        return directory,metadata


def sheet_json(job, metadata):
    base=f'/api/v1/jobs/{job.id}/dimension-sheet/files'
    return dict(job_id=job.id,revision_id=metadata['revision_id'],status='REVIEW',
        preview_url=base+'/'+STEM+'.svg',
        files={kind:base+'/'+STEM+'.'+kind for kind in ('pdf','dxf','svg','json','zip')},
        sheet=metadata['sheet'])
