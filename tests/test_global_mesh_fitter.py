import numpy as np
import pytest
import trimesh
from app.global_mesh_fitter import GlobalMeshFitter,inside_outside_status,NativeMeshProxy
from app.mesh_analyzer import analyze_mesh
from app.mesh_sampling import sample_surface
from app.geometry_cache import GeometryCache,ResourceBudget
from app.mesh_cad_schema import DenseOptimizationOptions


def test_global_distances_use_triangles_and_cover_unseen_bottom_and_back():
    mesh=trimesh.creation.box(extents=[700,500,750])
    reference=analyze_mesh(mesh)
    cad=mesh.copy()
    cad.vertices[cad.vertices[:,2]<0,2]-=20
    cad=analyze_mesh(cad)
    fitter=GlobalMeshFitter(reference,dict(sdf='false'))
    evaluation=fitter.evaluate(cad,sample_surface(cad,25_000),fitter.samples(25_000))
    assert evaluation.report['reference_to_cad']['mean_mm']>1
    assert evaluation.report['cad_to_reference']['mean_mm']>1
    assert evaluation.report['maximum_observed_mm']>19
    assert evaluation.report['surface_coverage'].startswith('entire')
    assert evaluation.report['rms_mm']>=evaluation.report['symmetric_mean_mm']


def test_watertight_sdf_has_negative_inside_and_positive_outside():
    reference=analyze_mesh(trimesh.creation.box(extents=[10,10,10]))
    fitter=GlobalMeshFitter(reference)
    assert fitter.sdf_status['enabled'],fitter.sdf_status
    assert fitter.signed_or_unsigned([[0,0,0],[0,0,8]])==pytest.approx([-5,3])


def test_open_mesh_and_intersections_fall_back_to_unsigned():
    mesh=trimesh.creation.box(extents=[10,10,10]); mesh.update_faces(np.arange(10))
    fitter=GlobalMeshFitter(analyze_mesh(mesh),dict(sdf='true'))
    assert not fitter.sdf_status['enabled']
    assert np.all(fitter.signed_or_unsigned([[0,0,0],[20,0,0]])>=0)
    left=trimesh.creation.box(); right=left.copy(); right.apply_translation([.4,0,0])
    status=inside_outside_status(analyze_mesh(trimesh.util.concatenate([left,right])))
    assert not status['eligible']


def test_missing_regions_are_detected_bidirectionally():
    left=trimesh.creation.box(extents=[50,50,100])
    right=left.copy(); right.apply_translation([200,0,0])
    reference=analyze_mesh(trimesh.util.concatenate([left,right]),np.repeat([0,1],12))
    cad=analyze_mesh(left)
    fitter=GlobalMeshFitter(reference,dict(sdf='false',component_aware=False))
    result=fitter.evaluate(cad,sample_surface(cad,25_000),fitter.samples(25_000))
    assert result.report['cad_to_reference']['mean_mm']<.001
    assert result.report['reference_to_cad']['mean_mm']>50
    assert result.report['unmatched_reference_area_fraction']>.4


def test_reference_index_and_samples_cached_but_changed_geometry_invalidates():
    reference=analyze_mesh(trimesh.creation.box())
    cache=GeometryCache()
    first=GlobalMeshFitter(reference,cache=cache)
    second=GlobalMeshFitter(reference,cache=cache)
    assert first.reference_index is second.reference_index
    assert first.samples(25_000) is first.samples(25_000)
    reference.mesh.apply_translation([1,0,0])
    third=GlobalMeshFitter(reference,cache=cache)
    assert third.reference_index is not first.reference_index
    assert first.signed_or_unsigned([[0,0,0]])[0]<0


def test_sample_configuration_and_memory_fallback(monkeypatch):
    monkeypatch.setenv('GLOBAL_COARSE_POINTS','1000')
    monkeypatch.setenv('GLOBAL_MEDIUM_POINTS','4000')
    monkeypatch.setenv('GLOBAL_FINE_POINTS','8000')
    monkeypatch.setenv('ENABLE_SDF_FITTING','false')
    options=DenseOptimizationOptions(memory_mb=8)
    assert (options.coarse_points,options.medium_points,options.fine_points)==(1000,4000,8000)
    fitter=GlobalMeshFitter(analyze_mesh(trimesh.creation.box()),options)
    samples=fitter.samples(750_000)
    assert samples.fallback and len(samples.points)<100_000
    with pytest.raises(ValueError,match='increase'):
        DenseOptimizationOptions(coarse_points=5000,medium_points=1000)


@pytest.mark.parametrize('count',[25_000,100_000,300_000,750_000,1_000_000])
def test_sparse_signed_distance_queries_support_verification_densities(count):
    reference=analyze_mesh(trimesh.creation.box(extents=[10,10,10]))
    fitter=GlobalMeshFitter(reference)
    points=np.zeros((count,3),dtype=np.float32)
    values=fitter.signed_or_unsigned(points)
    assert values.shape==(count,)
    assert np.max(np.abs(values+5))<1e-5


def test_inside_surfaces_are_unmatched_but_not_reported_as_oversized():
    reference=analyze_mesh(trimesh.creation.box(extents=[10,10,10]))
    cad=analyze_mesh(trimesh.creation.box(extents=[8,8,8]))
    fitter=GlobalMeshFitter(reference,dict(unmatched_tolerance_mm=.1))
    result=fitter.evaluate(cad,sample_surface(cad,1000),fitter.samples(1000))
    assert result.report['unmatched_cad_area_fraction']>.99
    assert result.report['oversized_cad_area_fraction']==0
