"""Immutable, revision-bound combined downloads; never uses a project's latest CAD."""
import json
from zipfile import ZipFile,ZIP_DEFLATED
from app.ai.openai_client import AIServiceError
from app.pipeline import canonical_hash,file_hash,safe_directory


def verify_delivery(config,delivery):
    directory=safe_directory(config.storage,delivery['directory'])
    for name,digest in delivery['files'].items():
        path=safe_directory(directory,name)
        if not path.is_file() or file_hash(path)!=digest:
            raise AIServiceError('DELIVERY_INTEGRITY_FAILED','A completed delivery artifact changed')
    manifest=json.loads((directory/'delivery_manifest.json').read_text(encoding='utf-8'))
    if canonical_hash(manifest)!=delivery['manifest_sha256']:
        raise AIServiceError('DELIVERY_INTEGRITY_FAILED','The delivery manifest changed')
    return directory


def build_delivery(r,result):
    revision,job,cad=r.artifacts(r.revision_id,r.job_id)
    manifest=json.loads((cad/'manifest.json').read_text(encoding='utf-8'));items=[]
    whole=manifest.get('output_scope')=='WHOLE_FURNITURE'
    directory=safe_directory(r.config.storage,f'delivery_{r.id}_{r.lease}');directory.mkdir(exist_ok=False)
    proof=result['engineering_cad']['cad_export_agreement']
    match=result['engineering_cad'].get('photo_match')
    status='PHOTO_MATCH_NEEDS_REVISION' if match and not match['accepted'] else 'GENERATED_REVIEW_REQUIRED'
    report=dict(workflow_id=r.id,job_id=job.id,revision_id=revision.id,preview_geometry_source='ENGINEERING_CAD',
        cad_export_agreement=proof,value_provenance=result['value_provenance'],reference_image_ids=result['reference_image_ids'],
        dimension_match=r.inputs.get('dimension_match'),
        exact_reconstruction_verified=False,approved_for_manufacturing=False)
    (directory/'accuracy_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    with ZipFile(directory/'furniture_complete_package.zip','w',ZIP_DEFLATED) as archive:
        for name,digest in {**manifest['files'],'manifest.json':job.manifest_hash}.items():
            if whole and name not in manifest['deliverables']:continue
            path=safe_directory(cad,name)
            if file_hash(path)!=digest:raise AIServiceError('DELIVERY_INTEGRITY_FAILED','Engineering CAD source changed')
            base=f'/api/v1/jobs/{job.id}/files/{name}'
            item=dict(path='cad/'+name,sha256=digest,size_bytes=path.stat().st_size,download_url=base)
            if path.suffix in ('.dxf','.pdf','.png','.svg','.csv','.json','.md'):item['preview_url']=base+'/preview'
            items.append(item);archive.write(path,'cad/'+name)
        base=f'/api/projects/{r.project_id}/deliveries/{r.id}/files'
        if not whole:
            items.append(dict(path='accuracy_report.json',sha256=file_hash(directory/'accuracy_report.json'),
                size_bytes=(directory/'accuracy_report.json').stat().st_size,download_url=base+'/accuracy_report.json'))
            archive.write(directory/'accuracy_report.json','accuracy_report.json')
        delivery_manifest=dict(schema_version=1,workflow_id=r.id,revision_id=revision.id,job_id=job.id,
            status=status,photo_match=match,approved_for_manufacturing=False,files=items)
        (directory/'delivery_manifest.json').write_text(json.dumps(delivery_manifest,indent=2),encoding='utf-8')
        if not whole:archive.write(directory/'delivery_manifest.json','delivery_manifest.json')
    return dict(workflow_id=r.id,revision_id=revision.id,job_id=job.id,status=status,photo_match=match,
        directory=directory.name,download_base=base,items=items,cad_export_agreement=proof,
        files={p.name:file_hash(p) for p in directory.iterdir()},manifest_sha256=canonical_hash(delivery_manifest),
        geometry_source='ENGINEERING_CAD',approved_for_manufacturing=False)
