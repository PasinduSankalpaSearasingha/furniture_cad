"""Create an explicitly estimated TDC1261-style review through the local API.

This uses deterministic geometry; it makes no OpenAI requests or verification claims.
"""
import argparse
import hashlib
import json
import time
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile
import httpx
from app.models import CabinetSpec


def run(image,output,base_url):
    image,output = image.resolve(),output.resolve()
    pixels = image.read_bytes()
    digest = hashlib.sha256(pixels).hexdigest()
    output.mkdir(parents=True,exist_ok=False)
    with httpx.Client(base_url=base_url,timeout=30) as client:
        def request(method,path,**kwargs):
            response = client.request(method,path,**kwargs)
            response.raise_for_status()
            return response
        access = request('GET','/api/v1/workspace/access').json()
        if access['mode']!='local': raise RuntimeError('This script requires the trusted local workspace')
        schema = request('GET','/openapi.json').json()
        if 'display_layout' not in schema['components']['schemas']['CabinetSpec']['properties']:
            raise RuntimeError('Restart the API to load the image-study template')
        project = request('POST','/api/v1/projects',json=dict(name='TDC1261 - estimated photo review',reviewer_ids=[])).json()
        reference = request('POST',f'/api/v1/projects/{project["id"]}/images',
            files={'file':(image.name,pixels,'image/jpeg')}).json()
        spec = CabinetSpec(project_name=project['name'],width_mm=650,depth_mm=430,height_mm=1900,
            carcass_material='solid_wood',panel_thickness_mm=18,back_thickness_mm=8,include_glazed_door=True,
            door_frame_width_mm=55,door_frame_depth_mm=24,glass_thickness_mm=6,
            door_perimeter_gap_mm=3,glass_edge_clearance_mm=2,door_standoff_mm=3,
            dimension_provenance='estimated',measured_dimensions_confirmed=False,
            display_layout=dict(reference_filename=image.name,reference_sha256=digest))
        revision = request('POST',f'/api/v1/projects/{project["id"]}/revisions',json=spec.model_dump(mode='json')).json()
        job = request('POST',f'/api/v1/revisions/{revision["id"]}/jobs').json()
        print(json.dumps(dict(project_id=project['id'],job_id=job['job_id'],source_sha256=digest)),flush=True)
        stage = None
        deadline = time.monotonic()+300
        while time.monotonic()<deadline:
            status = request('GET',job['status_url']).json()
            if status['stage']!=stage:
                print(json.dumps({key:status[key] for key in ('status','stage','progress')}),flush=True)
                stage = status['stage']
            if status['status']=='FAILED': raise RuntimeError(status['error'])
            if status['status']=='SUCCEEDED': break
            time.sleep(1)
        else: raise TimeoutError('CAD job remains in progress; inspect its UI project')
        package = request('GET',status['download_url']).content
        with ZipFile(BytesIO(package)) as archive:
            if archive.testzip(): raise RuntimeError('Package CRC verification failed')
            names = archive.namelist()
            if any(Path(name).name!=name or '\\' in name or ':' in name for name in names):
                raise RuntimeError('Unexpected artifact path')
            manifest = json.loads(archive.read('manifest.json'))
            if set(names)!=set(manifest['files'])|{'manifest.json'}: raise RuntimeError('Manifest contents differ')
            for name in names:
                data = archive.read(name)
                if name in manifest['files'] and hashlib.sha256(data).hexdigest()!=manifest['files'][name]:
                    raise RuntimeError('Artifact hash mismatch')
                (output/name).write_bytes(data)
        (output/'design_review_package.zip').write_bytes(package)
        (output/'source_reference.jpg').write_bytes(pixels)
        files = request('GET',f'/api/v1/jobs/{job["job_id"]}/files').json()['files']
        summary = dict(project_id=project['id'],revision_id=revision['id'],job_id=job['job_id'],
            project_url=base_url.rstrip('/')+'/#project/'+project['id'],status='ESTIMATED_REVIEW_ONLY',
            dimensions_mm=dict(width=650,depth=430,height=1900),files_in_ui=len(files),
            source_reference_id=reference['id'],source_sha256=digest,external_openai_calls=0,
            package_sha256=hashlib.sha256(package).hexdigest(),output=str(output))
        (output/'generation_summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
        print(json.dumps(summary,indent=2),flush=True)
        return summary


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--base-url',default='http://127.0.0.1:8000')
    options = parser.parse_args()
    run(options.image,options.output,options.base_url)
