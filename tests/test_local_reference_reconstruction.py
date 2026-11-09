"""Local reference drafts retain normal CAD checks and never fabricate AI approval."""
import copy
import cadquery as cq
import pytest
from app.agents import orchestrator
from app.database import AgentWorkflow,AIInvocation
from app.pipeline import canonical_hash
from app.photo_cad import bound_loft
from tests.test_agent_workflows import agents_app
from tests.test_engineering_cad import engineering_app
from tests.test_whole_furniture import generate


def test_local_reference_draft_exports_without_initializing_an_ai_gateway(engineering_app,monkeypatch):
    client,app,headers,project,image=engineering_app;source=generate(engineering_app)
    with app.state.db.sessions.begin() as session:
        before=session.get(AgentWorkflow,source['id']);raw=copy.deepcopy(before.input_data)
        raw['engineering_manual_reconstruction']=dict(source_workflow_id=before.id,origin='LOCAL_REFERENCE_RECONSTRUCTION')
        checkpoints={key:copy.deepcopy(before.checkpoints[key]) for key in
            ('engineering_dimensions','engineering_assembly','engineering_plan','engineering_critic_0','engineering_visual_review_0')}
        for key in ('engineering_critic_0','engineering_visual_review_0'):
            payload=checkpoints[key]['payload'];payload['accepted']=False
            payload['findings']=['Manual interpretation; inspect the reference match.']
            checkpoints[key]['sha256']=canonical_hash(payload)
        row=AgentWorkflow(project_id=project,created_by=before.created_by,mode='IMAGE_TO_CAD',input_data=raw,
            input_hash=canonical_hash(raw),model_config=before.model_config,checkpoints=checkpoints)
        session.add(row);session.flush();wid=row.id
    def forbidden(*a,**k):raise AssertionError('A local reconstruction must not initialize or pay an AI gateway')
    monkeypatch.setattr(orchestrator,'ResponsesGateway',forbidden)
    orchestrator.run_workflow(wid,app.state.db,app.state.settings)
    state=client.get(f'/api/v1/agents/workflows/{wid}',headers=headers['operator']).json()
    assert state['status']=='REVIEW_REQUIRED',state.get('error')
    assert state['result']['engineering_cad']['cad_export_agreement']['valid_closed_solids']
    assert state['result']['engineering_cad']['workflow']['manual_reconstruction']['origin']=='LOCAL_REFERENCE_RECONSTRUCTION'
    assert state['result']['engineering_cad']['photo_match']['accepted'] is False
    assert state['usage']['input_tokens']==state['usage']['output_tokens']==0
    assert state['job_id']!=source['job_id']
    from app.engineering_edits import edit_inputs
    with app.state.db.sessions() as session:
        edits,_=edit_inputs(session.get(AgentWorkflow,wid),['F002'],'Refine the selected leg','custom')
        assert 'engineering_manual_reconstruction' not in edits
        assert edits['engineering_edit']['source_reconstruction_origin']=='LOCAL_REFERENCE_RECONSTRUCTION'
    assert client.get(f'/api/v1/agents/workflows/{source["id"]}',headers=headers['operator']).json()['job_id']==source['job_id']
    # The server-owned mode cannot be enabled through an ordinary generation request.
    assert client.post(f'/api/projects/{project}/generate-all',headers=headers['operator'],json=dict(
        image_id=image,engineering_manual_reconstruction=raw['engineering_manual_reconstruction'])).status_code==422


@pytest.mark.parametrize('integration_noise',[1e-6,.1])
def test_bounded_loft_tolerates_only_tiny_integration_noise_after_intersection(monkeypatch,integration_noise):
    from types import SimpleNamespace
    from app import photo_cad
    from app.geometry import exact_bounds
    original=cq.Workplane('XY').box(10,10,10)
    wires=[cq.Workplane('XY',origin=(0,0,z)).rect(10,10).val() for z in (-5,5)]
    def measured_bounds(shape):
        bounds=exact_bounds(shape)
        if shape is original.val():
            values={a+side:getattr(bounds,a+side) for a in 'xyz' for side in ('min','max')}
            values['xmin']-=.001  # A small conservative kernel bound triggers the clipping path.
            return SimpleNamespace(**values)
        return bounds
    volume=cq.Compound.Volume
    monkeypatch.setattr(photo_cad,'exact_bounds',measured_bounds)
    monkeypatch.setattr(cq.Compound,'Volume',lambda self,*a,**k:volume(self,*a,**k)+integration_noise)
    if integration_noise>.001:
        with pytest.raises(ValueError,match='section envelope'):bound_loft(original,wires,'TEST')
    else:
        result,report=bound_loft(original,wires,'TEST')
        assert result.val().isValid() and len(result.val().Solids())==1
        assert report['removed_volume_mm3']==0 and report['section_dimensions_changed'] is False
        assert report['volume_integration_tolerance_mm3']==.001
