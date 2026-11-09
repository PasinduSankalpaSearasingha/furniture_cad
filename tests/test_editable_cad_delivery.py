"""Real CAD/drawing integration; external inference stays mocked."""
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import numpy as np
import pytest
from app.ai_call_ledger import CallLedger,GenerationLimit
from app.ai_part_detector import recognize
from app.mesh_to_cad import build_native,tessellate_build
from app.cad_six_view_generator import VIEWS,six_views,layout
from app.part_segmentation_validator import validate_regions
from app.freecad_project_exporter import export_freecad,find_freecad_python
from app.engineering_drawing_generator import generate_drawings
from app.drawing_validator import validate_drawings
from tests.global_mesh_fixtures import table_plan,cabinet_plan


def chair_plan():
    plan=table_plan(500)
    plan.furniture_type='Synthetic curved chair'
    for v in plan.values:
        if v.key=='height_mm':v.value=450
        if v.key=='depth_mm':v.value=450
    plan.parts[0].label='Seat'
    from app.photo_schema import PhotoPart,ResolvedValue
    plan.values.append(ResolvedValue(key='back_curve_mm',value=65,explanation='Synthetic unlocked curve control'))
    plan.parts.append(PhotoPart(part_id='F006',label='Curved backrest',evidence='Known synthetic continuous curved member',
        geometry='round_sweep',material='solid_wood',color_rgb=[.6,.4,.2],size=[25,25,25],curve_mode='bezier',
        path=[['-width_mm/2+40',0,'height_mm+100'],['-width_mm/4','back_curve_mm','height_mm+260'],
              ['width_mm/4','back_curve_mm','height_mm+260'],['width_mm/2-40',0,'height_mm+100']],position=[0,'depth_mm/2-40',0]))
    return plan


def test_durable_two_call_cap_caches_and_counts_uncertainty(tmp_path):
    path=tmp_path/'calls.sqlite';ledger=CallLedger(path);calls=[]
    def submit(request):calls.append(request);return {'text':'ok'}
    assert ledger.generate({'a':1},submit)=={'text':'ok'}
    assert CallLedger(path).generate({'a':1},submit)=={'text':'ok'}
    with pytest.raises(TimeoutError):
        ledger.generate({'a':2},lambda _:(_ for _ in ()).throw(TimeoutError()))
    with pytest.raises(GenerationLimit):CallLedger(path).generate({'a':3},submit)
    with pytest.raises(GenerationLimit):CallLedger(path).generate({'a':2},submit)
    assert len(calls)==1 and ledger.summary()['reserved_requests']==2


def test_parallel_reservations_never_exceed_two(tmp_path):
    path=tmp_path/'concurrent.sqlite';CallLedger(path)
    def reserve(i):
        try:return CallLedger(path).generate({'i':i},lambda _: {'ok':True})
        except GenerationLimit:return None
    with ThreadPoolExecutor(max_workers=6) as pool:results=list(pool.map(reserve,range(8)))
    assert sum(r is not None for r in results)==2


def test_existing_workflow_cap_survives_failed_submission_and_runner_restart(tmp_path):
    from app.database import Database,User,Project,AgentWorkflow,AgentEvent
    from app.agents.orchestrator import WorkflowRunner
    from app.ai.openai_client import AIServiceError
    from app.config import Settings
    from sqlalchemy import select
    db=Database(f'sqlite:///{tmp_path / "cap.db"}');db.create_schema_for_tests()
    with db.sessions.begin() as session:
        user=User(username='cap-test',password_hash='!',role='designer');session.add(user);session.flush()
        project=Project(name='Request cap',owner_id=user.id);session.add(project);session.flush()
        workflow=AgentWorkflow(project_id=project.id,created_by=user.id,mode='IMAGE_TO_CAD',state='RUNNING',
            lease_token='test-lease',input_data={},input_hash='a'*64,model_config={})
        session.add(workflow);session.flush();wid=workflow.id
    for index in range(3):
        runner=WorkflowRunner(wid,db,Settings(storage=tmp_path/'out'));runner.lease='test-lease'
        invocation=runner.record('invocation_started',dict(model='gpt-6.1-sol',request_hash=str(index)*64))
        if index<2:
            runner.record('invocation_submitting',dict(invocation_id=invocation,model='gpt-6.1-sol'))
            runner.record('invocation_failed',dict(invocation_id=invocation,error_code='AI_TIMEOUT',submission_uncertain=True))
        else:
            with pytest.raises(AIServiceError,match='two generation request limit'):
                runner.record('invocation_submitting',dict(invocation_id=invocation,model='gpt-6.1-sol'))
    with db.sessions() as session:
        assert len(list(session.scalars(select(AgentEvent).where(AgentEvent.event=='invocation_submitting'))))==2
    db.engine.dispose()


def test_local_revision_worker_never_submits_provider_or_sol(tmp_path,monkeypatch):
    import hashlib
    from app.mesh_pipeline_worker import main
    image=b'original-photo';mesh=b'preserved-reference';(tmp_path/'original_photo.png').write_bytes(image)
    (tmp_path/'original_neural4d.glb').write_bytes(mesh)
    (tmp_path/'provider_binding.json').write_text(json.dumps(dict(local_mesh_sha256=hashlib.sha256(mesh).hexdigest())))
    request=dict(local_revision=True,part_recognition=True,image_sha256=hashlib.sha256(image).hexdigest(),
                 known_dimensions={},furniture_type='table',reference_up_axis='Z',estimated_height_mm=750,optimization={})
    (tmp_path/'request.json').write_text(json.dumps(request));seen=[]
    monkeypatch.setattr('app.mesh_pipeline.run_reconstruction',lambda *a,**kw:seen.append(kw))
    monkeypatch.setattr('app.ai_part_detector.recognize',lambda *a,**kw:pytest.fail('Sol must not run during a manual revision'))
    monkeypatch.setattr('app.integrations.neural4d_client.Neural4DClient.ensure_mesh',lambda *a,**kw:pytest.fail('Provider generation must not run during a manual revision'))
    monkeypatch.setattr('sys.argv',['worker',str(tmp_path)]);main()
    assert seen[0]['part_recognition'] is False


def test_sol_combines_parts_and_native_plan_and_replays(tmp_path):
    plan=table_plan();data=tessellate_build(build_native(plan));requests=[]
    answer=dict(design=plan.model_dump(mode='json'),hypotheses=[dict(part_id='F001',category='tabletop',region_ids=[0],
        evidence='Mock photograph interpretation',uncertainties=['Uncalibrated boundary'])])
    def submit(**request):
        requests.append(request)
        return SimpleNamespace(id='mock-response',status='completed',output_text=json.dumps(answer))
    sdk=SimpleNamespace(responses=SimpleNamespace(create=submit))
    graph={'components':[dict(id=0,label='top',face_count=12)]}
    for _ in range(2):
        result=recognize(data,graph,b'not-a-real-photo',tmp_path,sdk=sdk)
        assert result.design.parts[0].part_id=='F001'
    assert len(requests)==1 and requests[0]['model']=='gpt-6.1-sol'
    assert 'input_image' in json.dumps(requests[0])


def test_face_region_validation_rejects_overlap_and_out_of_range():
    import trimesh
    mesh=trimesh.creation.box()
    result=validate_regions(mesh,{'regions':[dict(face_ids=list(range(12)),category='seat')]})
    assert result['regions'][0]['confidence'] is None
    assert len(result['regions'][0]['vertex_ids'])==8
    for regions in ([dict(face_ids=[12])],[dict(face_ids=[0,1]),dict(face_ids=[1,2])]):
        with pytest.raises(ValueError):validate_regions(mesh,{'regions':regions})


def test_original_triangle_and_vertex_ids_survive_cleanup_and_registration():
    import trimesh
    from app.mesh_analyzer import analyze_mesh
    from app.mesh_registration import normalize_mesh
    mesh=trimesh.creation.box(extents=[700,500,750])
    # A removed triangle and a duplicate vertex change both working index spaces.
    vertices=np.vstack((mesh.vertices,mesh.vertices[0]))
    faces=np.vstack(([0,0,0],mesh.faces))
    row,column=np.argwhere(faces[1:]==0)[0];faces[row+1,column]=len(vertices)-1
    raw=trimesh.Trimesh(vertices=vertices,faces=faces,process=False)
    data=analyze_mesh(raw,track_source=True);normalized,_=normalize_mesh(data,up_axis='Z')
    for item in (data,normalized):
        assert item.mesh.metadata['_cad_source_face_ids'].tolist()==list(range(1,13))
        pairs=item.mesh.metadata['_cad_source_vertex_pairs']
        assert {0,8}<=set(pairs[:,1])
        source_for_zero=set(pairs[pairs[:,1]==0,0]);source_for_duplicate=set(pairs[pairs[:,1]==8,0])
        assert source_for_zero==source_for_duplicate


def test_exported_source_uses_edited_parameter_file_from_another_working_directory(tmp_path):
    import subprocess,sys
    import cadquery as cq
    from app.cad_delivery import assembly_source
    plan=table_plan();(tmp_path/'feature_specification.json').write_text(plan.model_dump_json(),encoding='utf-8')
    values=[v.model_dump() for v in plan.values]
    next(v for v in values if v['key']=='width_mm')['value']=714
    (tmp_path/'cad_parameters.json').write_text(json.dumps(dict(values=values)),encoding='utf-8')
    script=tmp_path/'assembly.py';script.write_text(assembly_source(Path(__file__).resolve().parents[1]),encoding='utf-8')
    subprocess.run([sys.executable,str(script)],cwd=tmp_path,check=True,capture_output=True,timeout=90)
    edited=cq.importers.importStep(str(tmp_path/'edited_assembly.step')).val()
    assert edited.isValid() and abs(edited.BoundingBox().xlen-714)<1e-5


def test_six_canonical_bases_and_asymmetric_projection():
    import cadquery as cq
    for basis in VIEWS.values():assert np.array_equal(np.cross(basis.right,basis.up),basis.look_from)
    shape=cq.Workplane('XY').box(100,60,40).union(cq.Workplane('XY').box(20,20,30).translate((30,0,35))).val()
    views=six_views([shape])
    # Asymmetric upper feature must switch left/right in back view, without a mirrored basis.
    def top_edge_centers(view):
        return np.mean([e.center().X for e in view.visible.edges() if e.center().Y>25])
    assert top_edge_centers(views['front'])>0
    assert top_edge_centers(views['back'])<0
    assert layout('first_angle')['top'][1]<layout('third_angle')['top'][1]


def test_exact_six_view_files_reopen_and_detect_mirroring(tmp_path):
    plan=table_plan();build=build_native(plan)
    manifest=generate_drawings(build,plan,tmp_path,known_dimensions={'width_mm':700},convention='first_angle')
    result=validate_drawings(tmp_path,manifest)
    assert len(result['checks'])==6
    assert manifest['sheets'][0]['views']['front']['dimensions'][0]['classification']=='confirmed'
    manifest['sheets'][0]['views']['back']['right']=(1,0,0)
    with pytest.raises(ValueError,match='orientation'):validate_drawings(tmp_path,manifest)


def test_exact_projection_cache_reuses_unchanged_geometry_and_rejects_tampering(tmp_path):
    import cadquery as cq
    from app.cad_projection_cache import ProjectionCache,fingerprint
    shape=cq.Workplane('XY').box(100,60,40).val();digest=fingerprint(shape)
    first=ProjectionCache(tmp_path/'first');views=six_views([shape]);first.save('F001',digest,views);first.finish()
    second=ProjectionCache(tmp_path/'second',tmp_path/'first')
    cached=second.load('F001',digest)
    assert cached and set(cached)==set(VIEWS)
    assert len(cached['front'].visible.edges())==len(views['front'].visible.edges())
    assert second.load('F001','different-geometry') is None
    (tmp_path/'first/cache/native_projections/F001/front_visible.brep').write_text('tampered')
    assert second.load('F001',digest) is None


@pytest.mark.parametrize('factory',[table_plan,cabinet_plan,chair_plan],ids=['table','cabinet','curved_chair'])
def test_actual_freecad_regenerates_and_reopens(factory,tmp_path):
    if not find_freecad_python():pytest.skip('FreeCAD embedded Python unavailable')
    result=export_freecad(factory(),tmp_path/'cad')
    assert result['status']=='validated'
    assert result['editability']['status']=='parameter_regenerated'
    assert result['editability']['edited_step_regenerated']
    assert (tmp_path/'cad'/'assembly.FCStd').stat().st_size>1000
    assert len(list((tmp_path/'cad'/'parts').glob('*/part.FCStd')))==len(factory().parts)
