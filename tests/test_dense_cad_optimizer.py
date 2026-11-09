import json
import numpy as np
import pytest
from app.cad_optimizer import optimize_mesh_cad
from app.mesh_to_cad import build_native, tessellate_build
from app.mesh_cad_schema import DenseOptimizationOptions
from app.photo_schema import PhotoDesign
from tests.test_engineering_cad import table_plan


def test_dense_fit_improves_leg_position_and_preserves_known_width(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('No network calls inside local CAD fitting')
    monkeypatch.setattr('httpx.Client.send', forbidden)
    monkeypatch.setattr('httpx.AsyncClient.send', forbidden)
    target = table_plan()
    reference = tessellate_build(build_native(target))
    initial = target.model_copy(deep=True)
    initial.parts[1].position[0] = '-width_mm/2+75'
    result = optimize_mesh_cad(initial, reference,
        options=dict(mode='economy', verify_points=25_000, multi_angle=False, max_evaluations=30,
                     max_seconds=90, controls=dict(parameter_ids=['F002.position.0'], max_displacement_mm=30)),
        inputs={'known_values': [dict(key='width_mm', value=700)]})
    assert result.report['status'] == 'improved', result.report
    assert result.report['final']['symmetric_mean_mm'] < result.report['initial']['symmetric_mean_mm']*.7
    assert result.report['fixed_dimensions_preserved']
    assert result.report['additional_neural4d_generations'] == result.report['additional_llm_calls'] == 0
    assert result.report['solid_checks']['valid_closed_solids']
    assert result.plan.parts[0] == initial.parts[0]


def test_locked_parameters_cannot_be_optimized():
    plan = table_plan()
    reference = tessellate_build(build_native(plan))
    result = optimize_mesh_cad(plan, reference,
        dict(mode='economy', verify_points=1000, multi_angle=False, controls=dict(parameter_ids=['width_mm'])),
        inputs={'known_values': [dict(key='width_mm', value=700)]})
    assert result.report['evaluations'] == 0
    assert next(v.value for v in result.plan.values if v.key == 'width_mm') == 700


def test_extended_verification_requires_explicit_enablement():
    with pytest.raises(ValueError, match='explicit'):
        DenseOptimizationOptions(verify_points=1_000_000)
    assert DenseOptimizationOptions(verify_points=1_000_000, extended_verification=True).extended_verification
    with pytest.raises(ValueError, match='zero'):
        DenseOptimizationOptions(controls=dict(max_structural_llm_calls=1))


def test_invalid_native_initial_geometry_is_rejected():
    plan = table_plan()
    reference = tessellate_build(build_native(plan))
    plan.parts[1].position = plan.parts[0].position[:]
    plan.parts[1].size = ['width_mm', 'depth_mm', 'top_thickness_mm']
    with pytest.raises(ValueError):
        optimize_mesh_cad(plan, reference, dict(verify_points=1000, multi_angle=False))


def test_invalid_shortlisted_cad_keeps_last_valid_revision(monkeypatch):
    import app.dense_cad_optimizer as dense
    target = table_plan()
    reference = tessellate_build(build_native(target))
    initial = target.model_copy(deep=True)
    initial.parts[1].position[0] = '-width_mm/2+75'
    native = dense.build_native
    calls = []
    def reject_changed(plan, *args, **kwargs):
        calls.append(plan)
        if len(calls) > 1:
            raise ValueError('Synthetic invalid candidate B-Rep')
        return native(plan, *args, **kwargs)
    monkeypatch.setattr(dense, 'build_native', reject_changed)
    result = optimize_mesh_cad(initial, reference, dict(mode='economy', verify_points=1000,
        multi_angle=False, max_evaluations=20, controls=dict(parameter_ids=['F002.position.0'])))
    assert result.plan == initial
    assert any(not attempt['valid'] for attempt in result.report['attempts'])
    assert result.report['solid_checks']['valid_closed_solids']


def test_original_photo_conflict_vetoes_mesh_preference(monkeypatch):
    import app.dense_cad_optimizer as dense
    target = table_plan()
    reference = tessellate_build(build_native(target))
    initial = target.model_copy(deep=True)
    initial.parts[1].position[0] = '-width_mm/2+75'
    def observed_photo(plan, *_):
        return (0. if plan.parts[1].position == initial.parts[1].position else 1.), []
    monkeypatch.setattr(dense, 'photo_error', observed_photo)
    result = optimize_mesh_cad(initial, reference, dict(mode='economy', verify_points=1000,
        multi_angle=False, max_evaluations=20, controls=dict(parameter_ids=['F002.position.0'])))
    assert result.plan == initial
    assert any('original-photo' in attempt.get('reason','') for attempt in result.report['attempts'])
