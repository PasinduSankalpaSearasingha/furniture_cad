import json
import time
from uuid import uuid4
from app.mesh_reconstruction_jobs import ReconstructionJobs


def test_stopped_worker_becomes_resumable_without_new_provider_job(tmp_path):
    store = ReconstructionJobs(tmp_path)
    job_id, created = store.create(str(uuid4()), dict(idempotency_key='recover'))
    assert created
    store.update(job_id, status='RUNNING', worker_pid=999999999, worker_started_at=0)
    job = store.reconcile(store.get(job_id))
    assert job['status'] == 'FAILED'
    assert store.update(job_id, expected=['FAILED'], status='QUEUED')
    second_id, created = store.create(job['project_id'], dict(idempotency_key='recover'))
    assert not created and second_id == job_id


def test_completed_orphaned_worker_result_is_recovered(tmp_path):
    store = ReconstructionJobs(tmp_path)
    job_id, _ = store.create(str(uuid4()), dict(idempotency_key='finished'))
    store.update(job_id, status='RUNNING', worker_pid=999999999, worker_started_at=0)
    directory = store.root/job_id
    report = dict(final=dict(symmetric_mean_mm=.1), photo_verification_status='unverified', reconstruction={})
    (directory/'optimization_report.json').write_text(json.dumps(report))
    (directory/'manifest.json').write_text('{}')
    (directory/'package.zip').write_bytes(b'synthetic test archive')
    recovered = store.reconcile(store.get(job_id))
    assert recovered['status'] == 'SUCCEEDED'
    assert recovered['summary']['recovered_after_restart']
