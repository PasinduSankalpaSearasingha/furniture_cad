"""Read-only independent inspection of a completed local furniture delivery."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
from zipfile import ZipFile
import httpx
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.pipeline import file_hash,canonical_hash,safe_directory


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('workflow')
    parser.add_argument('--url',default='http://127.0.0.1:8000');args=parser.parse_args()
    response=httpx.get(args.url+'/api/v1/agents/workflows/'+args.workflow,timeout=120);response.raise_for_status();workflow=response.json()
    assert workflow['status']=='REVIEW_REQUIRED' and workflow['progress']==100
    result=workflow['result'];delivery=result['delivery'];storage=Path('outputs').resolve()
    directory=safe_directory(storage,delivery['directory'])
    for name,digest in delivery['files'].items():assert file_hash(safe_directory(directory,name))==digest,name
    manifest=json.loads((directory/'delivery_manifest.json').read_text(encoding='utf-8'))
    assert canonical_hash(manifest)==delivery['manifest_sha256']
    assert manifest['workflow_id']==args.workflow and manifest['revision_id']==workflow['revision_id'] and manifest['job_id']==workflow['job_id']
    package=directory/'furniture_complete_package.zip'
    with ZipFile(package) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist())=={item['path'] for item in manifest['files']}|{'delivery_manifest.json'}
        for item in manifest['files']:
            with archive.open(item['path']) as stream:assert hashlib.file_digest(stream,'sha256').hexdigest()==item['sha256'],item['path']
    cad=safe_directory(storage,workflow['job_id'])
    import cadquery as cq
    shape=cq.importers.importStep(str(cad/'cabinet.step')).val();solids=shape.Solids()
    assert solids and all(solid.isValid() and solid.Volume()>0 for solid in solids)
    for item in manifest['files']:
        if item['path'].startswith('renders/') and item['path'].endswith('.png'):
            render=safe_directory(storage,result['blender']['artifact_directory'])/Path(item['path']).name
            with Image.open(render) as image:assert image.width>=128 and image.height>=128;image.verify()
    report=json.loads((directory/'accuracy_report.json').read_text(encoding='utf-8'))
    assert not report['approved_for_manufacturing'] and not report['exact_reconstruction_verified']
    agreement=result['engineering_cad']['cad_export_agreement']
    evidence=dict(workflow_id=args.workflow,revision_id=workflow['revision_id'],job_id=workflow['job_id'],
        package_sha256=file_hash(package),package_bytes=package.stat().st_size,file_count=len(manifest['files']),
        step_valid_positive_solids=len(solids),cad_export_agreement=agreement,
        geometry_source='ENGINEERING_CAD',approved_for_manufacturing=False)
    output=storage/'image-to-cad-live';output.mkdir(exist_ok=True)
    (output/'audit.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    print(json.dumps(evidence,indent=2),flush=True)


if __name__=='__main__':main()
