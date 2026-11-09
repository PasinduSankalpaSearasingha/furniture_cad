"""Engineering CAD selection never substitutes an unrelated project revision."""
from copy import deepcopy
import json
from zipfile import ZipFile
import pytest
from app.database import AgentWorkflow, Job, Project, Revision
from app.pipeline import canonical_hash, file_hash
from tests.test_agent_workflows import agents_app


@pytest.fixture
def scoped_project(agents_app):
    client, app, headers, project_id, image_id = agents_app
    records = []
    with app.state.db.sessions.begin() as session:
        project = session.get(Project, project_id)
        for number in (1, 2, 3):
            spec = {'test_revision': number}
            revision = Revision(project_id=project_id, number=number, specification=spec,
                spec_hash=canonical_hash(spec), created_by=project.owner_id)
            session.add(revision); session.flush()
            job = Job(revision_id=revision.id, state='SUCCEEDED')
            session.add(job); session.flush(); revision.active_job_id = job.id
            directory = app.state.settings.storage / job.id; directory.mkdir(parents=True)
            (directory/'cabinet.step').write_text(f'SYNTHETIC ROUTING FIXTURE {number}')
            manifest = dict(spec_hash=revision.spec_hash, files={'cabinet.step':file_hash(directory/'cabinet.step')})
            (directory/'manifest.json').write_text(json.dumps(manifest))
            with ZipFile(directory/'design_review_package.zip', 'w') as archive:
                for name in ('cabinet.step','manifest.json'): archive.write(directory/name, name)
            job.package_hash=file_hash(directory/'design_review_package.zip')
            job.manifest_hash=file_hash(directory/'manifest.json')
            result = dict(delivery=dict(revision_id=revision.id, job_id=job.id))
            inputs = {'cad_source':'ENGINEERING_CAD'} if number == 2 else {}
            if number == 2:
                result['engineering_cad'] = dict(cad_export_agreement=dict(valid_closed_solids=True,
                    assembly_glb_sha256='a'*64, part_count=5))
            workflow = AgentWorkflow(project_id=project_id, created_by=project.owner_id,
                mode='IMAGE_TO_CAD' if number == 2 else 'PHOTO', state='REVIEW_REQUIRED',
                revision_id=revision.id, job_id=job.id, input_data=inputs,
                input_hash=canonical_hash(inputs), model_config={}, result=result,
                created_at=f'2026-01-0{number}')
            session.add(workflow); session.flush()
            records.append(dict(revision_id=revision.id, job_id=job.id, workflow_id=workflow.id))
        project.revision_counter=3
    return agents_app, records


def test_downloads_select_verified_engineering_job_over_newer_unrelated_revision(scoped_project):
    (client,app,headers,project,image), records = scoped_project
    bound = records[1]
    state = client.get(f'/api/projects/{project}', headers=headers['operator']).json()
    assert [r['id'] for r in state['revisions']] == [bound['revision_id']]
    assert state['model_scope']['geometry_source'] == 'ENGINEERING_CAD'
    assert state['model_scope']['solid_geometry_verified'] is True
    assert state['model_scope']['job_id'] == bound['job_id']
    assert state['model_scope']['hidden_revision_count'] == 2
    assert state['delivery']['job_id'] == bound['job_id']
    assert {w['id'] for w in state['workflows']} == {bound['workflow_id']}
    assert client.get(f'/api/projects/{project}/download/step', headers=headers['operator']).text == 'SYNTHETIC ROUTING FIXTURE 2'
    assert len(client.get(f'/api/v1/projects/{project}/revisions', headers=headers['operator']).json()) == 3


@pytest.mark.parametrize('conflict', ['proof','job_state','job_revision','active_job'])
def test_conflicting_bindings_never_fall_back_to_other_cad(scoped_project, conflict):
    (client,app,headers,project,image), records = scoped_project
    with app.state.db.sessions.begin() as session:
        workflow = session.get(AgentWorkflow, records[1]['workflow_id'])
        if conflict == 'proof':
            result = deepcopy(workflow.result)
            result['engineering_cad']['cad_export_agreement']['valid_closed_solids'] = False
            workflow.result = result
        elif conflict == 'job_state': session.get(Job, records[1]['job_id']).state='FAILED'
        elif conflict == 'job_revision': session.get(Job, records[1]['job_id']).revision_id=records[0]['revision_id']
        elif conflict == 'active_job': session.get(Revision, records[1]['revision_id']).active_job_id=records[0]['job_id']
    state=client.get(f'/api/projects/{project}', headers=headers['operator']).json()
    assert state['model_scope']['job_id'] is None and state['revisions']==[] and state['delivery'] is None
    for path in ('download/step','download/package','components'):
        assert client.get(f'/api/projects/{project}/{path}', headers=headers['operator']).status_code==409


def test_failed_new_engineering_upload_is_visible_and_does_not_show_old_cad(scoped_project):
    (client,app,headers,project,image),records=scoped_project
    with app.state.db.sessions.begin() as session:
        previous=session.get(AgentWorkflow,records[1]['workflow_id'])
        inputs={'cad_source':'ENGINEERING_CAD'}
        failed=AgentWorkflow(project_id=project,created_by=previous.created_by,mode='IMAGE_TO_CAD',state='FAILED',
            input_data=inputs,input_hash=canonical_hash(inputs),model_config={},created_at='2026-01-09',
            error=dict(code='AI_TIMEOUT',message='Synthetic failed upload'))
        session.add(failed);session.flush();failed_id=failed.id
    state=client.get(f'/api/projects/{project}',headers=headers['operator']).json()
    assert state['workflows'][0]['id']==failed_id and state['workflows'][0]['status']=='FAILED'
    assert state['model_scope']['source_workflow_id']==failed_id and state['model_scope']['job_id'] is None
    assert state['revisions']==[] and state['delivery'] is None
