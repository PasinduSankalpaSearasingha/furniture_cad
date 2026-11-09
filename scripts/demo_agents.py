"""Offline, explicitly mocked collaborative workflow with genuine CadQuery exports.

Run: python -m scripts.demo_agents --output examples/generated/collaborative_cabinet_demo
No external API calls, real measurements, engineering approvals or production releases occur.
"""
import argparse
import json
import secrets
import shutil
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import create_app
from app.config import Settings
from app.database import User
from app.auth import hash_password, issue_token
from app.ai.schemas import DIMENSION_FIELDS, DOOR_DIMENSION_FIELDS
from app.agents import orchestrator
from examples.ai_demo.mock_gateway import MockGateway


def run(output: Path):
    output = output.resolve()
    output.mkdir(parents=True,exist_ok=False)
    original = orchestrator.ResponsesGateway
    orchestrator.ResponsesGateway = MockGateway
    MockGateway.calls,MockGateway.responses,MockGateway.fail_role = [],None,None
    try:
        with tempfile.TemporaryDirectory(prefix='furniture-agent-demo-') as temporary:
            root = Path(temporary).resolve()
            if not root.is_relative_to(Path(tempfile.gettempdir()).resolve()):
                raise RuntimeError('Unexpected temporary directory')
            app = create_app(Settings(database_url=f'sqlite:///{root / "demo.db"}',storage=root/'private',executor='eager',
                ai_models={k:'MOCK_MODEL_NOT_FOR_API' for k in ('vision','design','joinery','review','render','image')}))
            app.state.db.create_schema_for_tests()
            with app.state.db.sessions.begin() as session:
                designer = User(username='offline-demo-designer',role='designer',password_hash=hash_password(secrets.token_urlsafe(24)))
                session.add(designer)
                session.flush()
                headers = {'Authorization':'Bearer '+issue_token(session,designer,1)}
            def save(name,value): (output/name).write_text(json.dumps(value,indent=2),encoding='utf-8')
            with TestClient(app) as client:
                def post(path,body=None,**kwargs):
                    response = client.post(path,headers=headers,json=body,**kwargs)
                    if response.status_code>=400: raise RuntimeError(f'{path}: {response.status_code}: {response.text}')
                    return response.json()
                project = post('/api/v1/projects',dict(name='OFFLINE DEMO - synthetic inputs, no factory approval'))
                reference = Path('examples/ai_demo/wooden_glass_cabinet_CAD_REFERENCE.png')
                asset = post(f'/api/v1/projects/{project["id"]}/images',files={'file':(reference.name,reference.read_bytes(),'image/png')})
                save('reference_image_record.json',asset)
                job = post('/api/v1/agents/run-workflow',dict(project_id=project['id'],image_ids=[asset['id']],
                    notes='OFFLINE MOCK: CAD-derived wooden/glass cabinet reference, not a measured photograph'))
                wid = job['workflow_id']
                status = lambda:client.get(f'/api/v1/agents/workflows/{wid}',headers=headers).json()
                analysis = status()
                if analysis['status']!='WAITING_FOR_DESIGNER': raise RuntimeError(json.dumps(analysis))
                save('collaborative_analysis_MOCK.json',analysis)
                blocked = client.post(f'/api/v1/designs/{wid}/generate-cad',headers=headers)
                assert blocked.status_code==409
                spec = json.loads(Path('examples/cabinet_materials_review.json').read_text())
                spec.update(project_name='OFFLINE DEMO - SYNTHETIC INPUTS - NOT PHYSICALLY VERIFIED',
                            glass_edge_clearance_mm=1,door_standoff_mm=1)
                for profile in spec['material_profiles']: post('/api/v1/catalogs/materials',profile)
                evidence = 'SYNTHETIC DEMO FIXTURE ONLY - NO ACTUAL PHYSICAL MEASUREMENT OR FACTORY APPROVAL'
                records = [dict(field=f,value_mm=spec[f],provenance='PHYSICALLY_MEASURED' if f in ('width_mm','depth_mm','height_mm') else 'USER_PROVIDED',
                                evidence_reference=evidence) for f in DIMENSION_FIELDS+DOOR_DIMENSION_FIELDS]
                verification = dict(specification=spec,measurements=records,designer_comment=evidence)
                verified = post(f'/api/v1/designs/{wid}/verify',verification)
                save('simulated_designer_input_NOT_REAL_MEASUREMENTS.json',verification)
                generated = post(f'/api/v1/designs/{wid}/generate-cad')
                final = status()
                if final['status']!='REVIEW_REQUIRED': raise RuntimeError(json.dumps(final))
                save('final_workflow_MOCK_AI_REVIEW.json',final)
                report = post(f'/api/v1/designs/{wid}/validate')
                save('deterministic_validation.json',report)
                approval = client.post(f'/api/v1/designs/{wid}/approve',headers=headers,json=dict(decision='APPROVED',comment='Must remain blocked'))
                release = client.post(f'/api/v1/designs/{wid}/release',headers=headers)
                assert approval.status_code==409 and release.status_code==409
                rejected = post(f'/api/v1/designs/{wid}/approve',dict(decision='CHANGES_REQUESTED',comment='Obtain genuine factory joinery, glazing, hardware and benchmark data'))
                events = client.get(f'/api/v1/agents/workflows/{wid}/events',headers=headers).json()
                save('workflow_events.json',events)
                shutil.copytree(app.state.settings.storage/generated['job_id'],output/'cad_review_package')
                shutil.copy2(reference,output/reference.name)
                summary = dict(demo='OFFLINE PROTOTYPE - MOCKED AI AND SIMULATED DESIGNER',external_openai_calls=0,
                    reference_kind='CAD_DERIVED_REVIEW_IMAGE',physical_measurements_performed=False,
                    workflow_id=wid,revision_id=verified['revision_id'],job_id=generated['job_id'],
                    initial_status=analysis['status'],final_status=final['status'],
                    cad_before_verification_http_status=blocked.status_code,approval_http_status=approval.status_code,
                    release_http_status=release.status_code,designer_rejection_http_status=201,
                    parts=len(final['result']['cad']['part_ids']),agent_calls=MockGateway.calls,
                    approved_for_manufacturing=False,package='cad_review_package/design_review_package.zip')
                save('run_summary.json',summary)
                (output/'README.md').write_text('# Offline collaborative cabinet prototype\n\n'
                    'AI responses and designer measurement verification are SYNTHETIC TEST FIXTURES. No real image analysis, '
                    'physical measurement, live API call, engineering approval or production release occurred. '
                    'The reference is rendered from an existing review STEP, not a photograph. '
                    'CAD solids, exports, schedules, review drawings and deterministic findings were generated by the real engines. '
                    'Approval and release were correctly blocked; the designer-rejection path was exercised. '
                    'API IDs/download URLs in captured records belonged to a temporary test application; copied artifacts are provided here.\n',encoding='utf-8')
                print(json.dumps(summary,indent=2))
            app.state.db.engine.dispose()
    finally: orchestrator.ResponsesGateway = original


if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args().output)
