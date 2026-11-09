"""Reproducible synthetic benchmarks; never contact an AI provider.

Run: .venv\Scripts\python.exe -m scripts.benchmark_dense_geometry
"""
import json
from pathlib import Path
import threading
import time
import numpy as np
import psutil
from app.mesh_analyzer import analyze_mesh
from app.mesh_sampling import sample_surface, STAGES
from app.mesh_to_cad import build_native, tessellate_build, export_native
from app.geometry_acceleration import SurfaceIndex
from app.geometry_cache import ResourceBudget
from app.multiview_geometry import generate_cameras, multiview_report
from app.cad_optimizer import optimize_mesh_cad
from tests.test_engineering_cad import table_plan


def benchmark(output='outputs/dense_benchmark'):
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    target = table_plan()
    rebuild_started = time.perf_counter()
    target_build = build_native(target)
    rebuild_seconds = time.perf_counter()-rebuild_started
    reference = tessellate_build(target_build)
    reference.mesh.export(output/'synthetic_reference.obj')
    initial = target.model_copy(deep=True)
    initial.parts[1].position[0] = '-width_mm/2+75'
    initial_build = build_native(initial)
    index = SurfaceIndex(reference)
    rows = []
    for mode, (count, views) in STAGES.items():
        peak = [psutil.Process().memory_info().rss]
        finished = threading.Event()
        def monitor():
            while not finished.wait(.05):
                peak[0] = max(peak[0], psutil.Process().memory_info().rss)
        watcher = threading.Thread(target=monitor, daemon=True); watcher.start()
        start = time.perf_counter()
        try:
            sampling_start = time.perf_counter()
            samples = sample_surface(reference, count, budget=ResourceBudget())
            sampling_seconds = time.perf_counter()-sampling_start
            query_start = time.perf_counter()
            query = index.closest(samples.points)
            query_seconds = time.perf_counter()-query_start
            ray_start = time.perf_counter()
            cameras = generate_cameras(reference.mesh.bounds, views, resolution=48)
            view_report = multiview_report(index, index, cameras)
            ray_seconds = time.perf_counter()-ray_start
            fit_start = time.perf_counter()
            fit = optimize_mesh_cad(initial, reference, dict(mode=mode, verify_points=count, final_views=views,
                camera_resolution=48, max_evaluations=24, max_seconds=120, exact_candidates=3,
                controls=dict(parameter_ids=['F002.position.0'])),
                inputs={'known_values':[dict(key='width_mm',value=700)]})
            fit_seconds = time.perf_counter()-fit_start
            row = dict(mode=mode, requested_points=count, actual_points=len(samples.points), virtual_views=views,
                sampling_seconds=sampling_seconds, mesh_query_seconds=query_seconds,
                raycasting_seconds=ray_seconds, cad_rebuild_seconds=rebuild_seconds,
                fitting_and_verification_seconds=fit_seconds, total_seconds=time.perf_counter()-start,
                evaluations=fit.report['evaluations'],
                average_fitting_evaluation_seconds=(sum(s['seconds'] for s in fit.report['stages'])/max(fit.report['evaluations'],1)),
                initial_mean_deviation_mm=fit.report['initial']['symmetric_mean_mm'],
                final_mean_deviation_mm=fit.report['final']['symmetric_mean_mm'],
                final_p95_mm=fit.report['final']['reference_to_cad']['p95_mm'],
                cad_valid=fit.report['solid_checks']['valid_closed_solids'],
                photo_error=None, photo_error_reason='Synthetic reference has no original photograph',
                peak_gpu_mb=None, device='CPU', stage_reports=fit.report['stages'],
                stop_reason=fit.report['stop_reason'])
            if mode == 'economy':
                exports = export_native(fit.build, fit.plan, output/'sample_cad')
                row['sample_step_reimport_valid'] = exports['assembly_reopened']
            print(json.dumps(row), flush=True)
        finally:
            finished.set(); watcher.join()
        row['peak_process_rss_mb'] = peak[0]/1024**2
        rows.append(row)
        (output/'benchmarks.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
    # A measured low-density configuration of the same surface solver is a fair
    # numerical baseline. The old photo optimizer uses different evidence/units.
    from app.cad_optimizer import optimize_cad
    from tests.test_cad_optimizer import view_from_plan, options
    legacy_start = time.perf_counter()
    legacy = optimize_cad(initial, options(view_from_plan(target), parameter_ids=['F002.position.0']))
    legacy_build = build_native(legacy.plan)
    from app.dense_cad_optimizer import bidirectional_report, assign_reference_components
    labeled = assign_reference_components(reference, tessellate_build(legacy_build))
    measurement, _ = bidirectional_report(SurfaceIndex(labeled), tessellate_build(legacy_build),
        sample_surface(labeled, 25_000), 25_000, ResourceBudget(), None, True)
    legacy_report = dict(seconds=time.perf_counter()-legacy_start, evaluations=legacy.report['evaluations'],
        image_error=legacy.report['final_error'], image_error_units=legacy.report['error_unit'],
        final_surface_deviation_mm=measurement['symmetric_mean_mm'],
        comparison_limit='Legacy uses synthetic photo landmarks; dense fitting uses the generated surface. Evidence differs.')
    (output/'legacy_comparison.json').write_text(json.dumps(legacy_report, indent=2), encoding='utf-8')
    return rows


if __name__ == '__main__':
    benchmark()
