"""Isolated local UI fixture server. Never enables AI or provider submissions."""
import argparse
import json
from pathlib import Path
import shutil
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.main import create_app
from app.config import Settings
from app.database import User,Project
from app.auth import LOCAL_WORKSPACE_USER_ID
from app.ai.images import store_image
from app.mesh_cad_schema import MeshReconstructionRequest
from app.mesh_reconstruction_jobs import ReconstructionJobs
from app.pipeline import file_hash


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8120)
    parser.add_argument('--refresh-only',action='store_true');args=parser.parse_args()
    root=Path('outputs/editable_cad_browser').resolve();root.mkdir(parents=True,exist_ok=True)
    config=Settings(database_url='sqlite:///'+str(root/'demo.db'),storage=root/'storage',executor='local',
                    local_workspace=True,ai_enabled=False)
    app=create_app(config);app.state.db.create_schema_for_tests()
    state=root/'state.json'
    if not state.exists():
        source=Path('outputs/editable_delivery_benchmark/table')
        with app.state.db.sessions.begin() as session:
            user=User(id=LOCAL_WORKSPACE_USER_ID,username='local-workspace',password_hash='!',role='designer')
            session.add(user);session.flush()
            project=Project(name='Synthetic native table - CAD output verification',owner_id=user.id)
            session.add(project);session.flush()
            image=store_image(session,config,project.id,user.id,(source/'reference'/'cad_evidence.png').read_bytes())
            project_id=project.id;image_id=image.id
        request=MeshReconstructionRequest(image_id=image_id,idempotency_key='synthetic-demo',part_recognition=False,
            reference_up_axis='Z',known_dimensions={'width_mm':700}).model_dump(mode='json')
        store=ReconstructionJobs(config.storage);job_id,_=store.create(project_id,request);directory=store.root/job_id
        shutil.copytree(source,directory,dirs_exist_ok=True)
        shutil.copy2(directory/'original_reference.glb',directory/'original_neural4d.glb')
        shutil.copy2(directory/'reference'/'cad_evidence.png',directory/'original_photo.png')
        request['image_sha256']=file_hash(directory/'original_photo.png')
        # Only fixture metadata changes: no provider generation occurred.
        record=store.get(job_id);store.update(job_id,request=dict(record['request'],**request))
        manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
        for name in ('original_neural4d.glb','original_photo.png'):manifest['files'][name]=file_hash(directory/name)
        (directory/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
        store.update(job_id,status='SUCCEEDED',stage='complete',progress=100,manifest_sha256=file_hash(directory/'manifest.json'),
                     package_sha256=file_hash(directory/'package.zip'))
        state.write_text(json.dumps(dict(project=project_id,job=job_id,fixture_only=True)),encoding='utf-8')
    print(state.read_text(encoding='utf-8'),flush=True)
    if args.refresh_only:
        saved=json.loads(state.read_text(encoding='utf-8'))
        if saved.get('fixture_only') is not True:raise ValueError('Only the isolated synthetic fixture may be refreshed')
        store=ReconstructionJobs(config.storage);directory=store.root/saved['job']
        shutil.copytree(Path('outputs/editable_delivery_benchmark/table'),directory,dirs_exist_ok=True)
        shutil.copy2(directory/'original_reference.glb',directory/'original_neural4d.glb')
        shutil.copy2(directory/'reference'/'cad_evidence.png',directory/'original_photo.png')
        manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
        for name in ('original_neural4d.glb','original_photo.png'):manifest['files'][name]=file_hash(directory/name)
        (directory/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
        record=store.get(saved['job'])
        store.update(saved['job'],request=dict(record['request'],image_sha256=file_hash(directory/'original_photo.png')),
            manifest_sha256=file_hash(directory/'manifest.json'),package_sha256=file_hash(directory/'package.zip'))
        return
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=args.port,log_level='warning')


if __name__=='__main__':main()
