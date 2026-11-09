"""Measured comparison with the frozen pre-upgrade multi-angle optimizer.

Run from the project root using .venv\Scripts\python.exe -m scripts.benchmark_global_fitting.
The legacy fixture is benchmark-only and is never imported by the application.
"""
import importlib.util
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
import sys
import threading
import time
import numpy as np
import psutil
from app.cad_optimizer import optimize_mesh_cad
from app.global_mesh_fitter import GlobalMeshFitter
from app.geometry_cache import ResourceBudget
from app.mesh_to_cad import build_native,tessellate_build,export_native
from app.mesh_sampling import sample_surface,STAGES
from tests.global_mesh_fixtures import table_plan,cabinet_plan,backrest_plan


def legacy_optimizer():
    path=Path('tests/fixtures/legacy_dense_optimizer_v1.py.txt')
    loader=SourceFileLoader('app._benchmark_legacy_dense',str(path))
    specification=importlib.util.spec_from_loader(loader.name,loader)
    module=importlib.util.module_from_spec(specification)
    sys.modules[loader.name]=module
    loader.exec_module(module)
    return module.optimize_dense_cad


def cases():
    target=table_plan();initial=target.model_copy(deep=True);initial.parts[1].position[0]='-width_mm/2+75'
    return [('table_leg',target,initial,['F002.position.0']),
            ('cabinet_shelf',cabinet_plan(),cabinet_plan(450),['shelf_height_mm']),
            ('curved_backrest',backrest_plan(65),backrest_plan(45),['F001.path.1.1'])]


def run(output='outputs/global_benchmark'):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    legacy=legacy_optimizer()
    results=[]
    for label,target,initial,parameters in cases():
        reference=tessellate_build(build_native(target))
        for mode,(count,views) in STAGES.items():
            common=dict(mode=mode,align_reference=False,verify_points=count,final_views=views,camera_resolution=48,
                        max_evaluations=32,max_seconds=120,exact_candidates=3,
                        controls=dict(parameter_ids=parameters,max_displacement_mm=60 if label=='cabinet_shelf' else 30))
            reference_samples=sample_surface(reference,count)
            measurement=GlobalMeshFitter(reference,dict(sdf='false',component_aware=False))
            for name,solver in [('legacy_multi_angle',legacy),('global_surface',optimize_mesh_cad)]:
                process=psutil.Process();peak=[process.memory_info().rss];stop=threading.Event()
                def monitor():
                    while not stop.wait(.05): peak[0]=max(peak[0],process.memory_info().rss)
                watcher=threading.Thread(target=monitor,daemon=True);watcher.start()
                started=time.perf_counter()
                try:
                    result=solver(initial,reference,{**common,'multi_angle':name=='legacy_multi_angle'})
                    elapsed=time.perf_counter()-started
                finally:
                    stop.set();watcher.join()
                build=getattr(result,'build',None) or build_native(result.plan)
                cad=tessellate_build(build)
                sampled=sample_surface(cad,len(reference_samples.points))
                measured=measurement.evaluate(cad,sampled,reference_samples).report
                row=dict(case=label,method=name,mode=mode,requested_points=count,
                    actual_reference_points=len(reference_samples.points),actual_cad_points=len(sampled.points),
                    seconds=elapsed,mean_mm=measured['symmetric_mean_mm'],rms_mm=measured['rms_mm'],
                    p95_mm=measured['p95_mm'],max_mm=measured['maximum_observed_mm'],
                    peak_rss_mb=peak[0]/1024**2,evaluations=result.report['evaluations'],
                    average_trial_seconds=sum(s['seconds'] for s in result.report['stages'])/max(result.report['evaluations'],1),
                    evaluated_virtual_views=result.report.get('fitting_views',0),
                    primary_fitting_method=result.report.get('primary_fitting_method','legacy_surface_plus_silhouette'),
                    final_verification_points=result.report['actual_verification_points'],
                    native_solids_valid=result.report['solid_checks']['valid_closed_solids'],
                    external_ai_calls=0,photo_error=None,device='CPU',
                    actual_stage_points=[s['actual_points'] for s in result.report['stages']],
                    stop_reason=result.report['stop_reason'])
                row['permitted_displacement_mm']=common['controls']['max_displacement_mm']
                results.append(row)
                (output/'benchmarks.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
                print(json.dumps(row),flush=True)
                if name=='global_surface' and mode=='quality':
                    export_native(build,result.plan,output/label/'cad')
    return results


if __name__=='__main__': run()
