"""Local native-fixture benchmarks: no paid inference or provider requests."""
import argparse
import json
from pathlib import Path
import sys
import threading
import time
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import psutil
from app.mesh_pipeline import run_reconstruction
from app.mesh_to_cad import build_native,tessellate_build
from tests.test_editable_cad_delivery import table_plan,cabinet_plan,chair_plan


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='outputs/editable_delivery_benchmark');args=parser.parse_args()
    root=Path(args.output);root.mkdir(parents=True,exist_ok=True)
    process=psutil.Process();running=True;peak=[0]
    def monitor():
        while running:
            total=0
            for child in [process,*process.children(recursive=True)]:
                try:total+=child.memory_info().rss
                except (psutil.NoSuchProcess,psutil.AccessDenied):pass
            peak[0]=max(peak[0],total);time.sleep(.1)
    thread=threading.Thread(target=monitor,daemon=True);thread.start();results=[]
    try:
        for name,factory in [('table',table_plan),('cabinet',cabinet_plan),('curved_chair',chair_plan)]:
            started=time.perf_counter();plan=factory();mesh=tessellate_build(build_native(plan))
            source=root/(name+'.glb');mesh.mesh.export(source)
            report=run_reconstruction(source,root/name,initial_plan=plan.model_dump(mode='json'),up_axis='Z',
                estimated_height_mm=750,known_dimensions={'width_mm':next(v.value for v in plan.values if v.key=='width_mm')},
                options=dict(enabled=False,coarse_points=500,medium_points=1000,fine_points=2000,verify_points=2500,
                             memory_mb=1024,workers=2))
            delivery=json.loads((root/name/'cad_delivery.json').read_text(encoding='utf-8'))
            result=dict(case=name,seconds=time.perf_counter()-started,parts=len(plan.parts),drawing_sheets=len(delivery['drawings']['sheets']),
                six_views_per_sheet=6,freecad=report['editable_cad'],final_surface=report['final'],
                actual_verification_points=report['actual_verification_points'],sol_requests=0,neural4d_jobs=0,
                source='known synthetic native B-Rep fixture, not actual Neural4D output',
                package=str((root/name/'package.zip').resolve()))
            results.append(result);print(json.dumps(dict(case=name,seconds=result['seconds'],status='validated')),flush=True)
            (root/'benchmark.json').write_text(json.dumps(dict(results=results,peak_process_tree_rss_mb=peak[0]/1024**2),indent=2),encoding='utf-8')
    finally:running=False;thread.join(timeout=1)
    print('All native fixture exports completed. No external AI requests.',flush=True)


if __name__=='__main__':main()
