"""Audit a completed engineering delivery without generating another model."""
import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile
import httpx


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--workflow',required=True)
    parser.add_argument('--url',default='http://127.0.0.1:8000');args=parser.parse_args()
    output=Path('outputs/engineering-delivery-audit')/args.workflow;output.mkdir(parents=True,exist_ok=True)
    with httpx.Client(base_url=args.url,timeout=180) as client:
        response=client.get('/api/v1/agents/workflows/'+args.workflow);response.raise_for_status();workflow=response.json()
        assert workflow['status']=='REVIEW_REQUIRED';result=workflow['result'];delivery=result['delivery']
        proof=result['engineering_cad']['cad_export_agreement'];assert proof['valid_closed_solids']
        target=output/'engineering_cad_package.zip';digest=hashlib.sha256()
        with client.stream('GET',delivery['download_base']+'/furniture_complete_package.zip') as response:
            response.raise_for_status()
            with target.open('wb') as stream:
                for block in response.iter_bytes(1024*1024):digest.update(block);stream.write(block)
        assert digest.hexdigest()==delivery['files']['furniture_complete_package.zip']
    with ZipFile(target) as archive:
        assert archive.testzip() is None
        registry=json.loads(archive.read('cad/parts_registry.json'));assert len(registry)==proof['part_count']
        for part,item in zip(registry,proof['parts']):
            assert part['solid_valid'] and part['part_id']==item['part_id']
            assert item['valid_closed_solid'] and item['solid_count']==1 and item['step_reopened']
            assert item['sampled_surface_max_gap_mm']<=.001 and item['preview_source']=='REOPENED_PART_STEP'
            for name in part['files'].values():assert 'cad/'+name in archive.namelist()
            assert hashlib.sha256(archive.read('cad/'+part['files']['step'])).hexdigest()==item['step_sha256']
        assert hashlib.sha256(archive.read('cad/cabinet.step')).hexdigest()==proof['assembly_step_sha256']
        assert hashlib.sha256(archive.read('cad/cabinet.glb')).hexdigest()==proof['assembly_glb_sha256']
    audit=dict(workflow_id=workflow['id'],project_id=workflow['project_id'],job_id=workflow['job_id'],parts=len(registry),
        zip_sha256=digest.hexdigest(),zip_bytes=target.stat().st_size,zip_crc_valid=True,all_part_step_hashes_verified=True,
        valid_closed_solids=True,previews_from_reopened_step=True,physical_dimensions_verified=False,approved_for_manufacturing=False)
    (output/'audit.json').write_text(json.dumps(audit,indent=2),encoding='utf-8');print(json.dumps(audit,indent=2),flush=True)


if __name__=='__main__':main()
