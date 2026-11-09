"""Package measured global-fitting results and an original-source hash manifest."""
import ast
import hashlib
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parent.parent
ORIGINAL=Path(r'D:\Enfection\furniture_planner\furniture_manufacturing_starter')


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    docs=ROOT/'docs'
    benchmark=json.loads((ROOT/'outputs/global_benchmark/benchmarks.json').read_text())
    if len(benchmark)!=24: raise ValueError('All 24 benchmark comparisons must finish first')
    sample_root=ROOT/'outputs/global_surface_sample'
    sample=json.loads((sample_root/'optimization_report.json').read_text())
    if sample['virtual_camera_evaluations']!=0 or not sample['export_validation']['assembly_reopened']:
        raise ValueError('Sample must complete global fitting and native STEP reopening')
    summaries={}
    for name in ('global-fitting-test-results.xml','global-compatibility-test-results.xml'):
        suite=ET.parse(docs/name).getroot().find('testsuite')
        if int(suite.attrib['failures']) or int(suite.attrib['errors']):
            raise ValueError('Required validation suite contains failures')
        summaries[name]=dict(suite.attrib)
    shutil.copy2(ROOT/'outputs/global_benchmark/benchmarks.json',docs/'global-fitting-benchmarks.json')
    example=ROOT/'examples/global_surface_pipeline';example.mkdir(parents=True,exist_ok=True)
    names=('assembly.step','F001.step','F002.step','F003.step','F004.step','F005.step',
        'feature_specification.json','editable_cad.py','synthetic_reference.obj','original_photo.png',
        'optimization_report.json','geometry_deviation_report.json','parameter_revision_history.json',
        'overlay.glb','reference.glb','optimized_cad.glb','deviation.glb','component_graph.json',
        'calibration.json','registration.json')
    for name in names: shutil.copy2(sample_root/name,example/name)
    inventory=[]
    for path in sorted((ROOT/'app').rglob('*.py')):
        tree=ast.parse(path.read_text(encoding='utf-8-sig'))
        symbols=[node.name for node in tree.body if isinstance(node,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef))]
        inventory.append(str(path.relative_to(ROOT))+': '+', '.join(symbols))
    (docs/'global-source-inventory.txt').write_text('\n'.join(inventory),encoding='utf-8')
    candidates=[]
    for directory in ('app','frontend/src','scripts','tests','examples/global_surface_pipeline'):
        candidates.extend(path for path in (ROOT/directory).rglob('*') if path.is_file() and '__pycache__' not in path.parts and path.suffix not in ('.pyc','.pyo'))
    candidates.extend(ROOT/name for name in ('.env.example','README.md'))
    changed=[path for path in candidates if not (ORIGINAL/path.relative_to(ROOT)).is_file() or digest(path)!=digest(ORIGINAL/path.relative_to(ROOT))]
    source_names=sorted(path.relative_to(ROOT).as_posix() for path in changed if not path.relative_to(ROOT).as_posix().startswith('examples/'))
    quality=[]
    for case in ('table_leg','cabinet_shelf','curved_backrest'):
        previous=next(row for row in benchmark if row['case']==case and row['mode']=='quality' and row['method']=='legacy_multi_angle')
        current=next(row for row in benchmark if row['case']==case and row['mode']=='quality' and row['method']=='global_surface')
        quality.append(f"| {case.replace('_',' ')} | {previous['seconds']:.3f} | {current['seconds']:.3f} | {previous['mean_mm']:.6f} | {current['mean_mm']:.6f} | {current['rms_mm']:.6f} |")
    focused=summaries['global-fitting-test-results.xml']
    compat=summaries['global-compatibility-test-results.xml']
    report=f'''# Whole-surface global 3D fitting upgrade

Date: 28 October 2026. Target project: `{ORIGINAL}`. Python development, validation and benchmarks use the project `.venv`.

## Result and architecture

The primary optimizer now evaluates the entire 3D reference surface in one objective. It requires **zero virtual cameras**. The active path is:

**One Neural4D mesh → unit/measurement calibration → whole-assembly rigid registration → bidirectional surface fitting → bounded local native-CAD parameter optimization → original-photo verification → B-Rep validation → native STEP export and reopening.**

Global means a whole-assembly objective; it does not promise a globally optimal solution. Numerical search remains bounded and local. The Neural4D client, durable submission/polling safety, existing background worker dispatch, engineering constraints and exports are preserved. No new dependency or global Python installation was needed.

## Implemented methods

`app/global_mesh_fitter.py` owns an immutable reference copy and cached Open3D triangle acceleration structure. Both fast and rebuilt candidates use bidirectional closest-point-on-triangle queries, rather than vertex distances or a reverse point-cloud approximation. Area-weighted soft-L1 losses combine surface, reliable point-to-plane, normal and component correspondence terms. The full reference—including back, bottom and internal mesh surfaces—is sampled without visibility filtering. Unmatched reference/CAD surface area is reported; outside-reference oversized area is classified only when signed semantics are valid.

Fast evaluations warp a cached native tessellation and reuse its barycentric sample coordinates. Reference samples and the spatial index remain fixed within a stage. Promising candidates rebuild real CadQuery/OCP solids, preserve supplied dimensions and required contacts, reject invalid topology/interference, and are accepted only when the same whole-surface score improves. Native component caching reuses unchanged prefix components where contact trimming permits it. No mutable OCCT geometry is shared across concurrent workers.

Bounded SciPy trust-region least squares runs smaller global, position, dimension and curve groups. Sensitivity probes, control-unit finite differences, fixed normal correspondences within a Jacobian, evaluation/time limits, stagnation stopping and best-valid checkpoints are implemented. Bounds stay anchored to the original plan across all density stages; they do not expand after a successful revision. Missing explicitly identified components or unrepresented regions require structural review rather than being synthesized by parameter changes.

Registration is a separate rigid transform with 24 proper axis orientation candidates, optional known component-centroid seeds and multiscale ICP. Unit and supplied-scale calibration remain upstream. The optimizer does not alter component dimensions to repair an unaligned reference.

## Signed distances

Sparse SDF queries use the supported [Open3D RaycastingScene signed-distance API](https://www.open3d.org/docs/release/python_api/open3d.t.geometry.RaycastingScene.html#open3d.t.geometry.RaycastingScene.compute_signed_distance). Automatic eligibility requires closed, consistently oriented, manifold, nonintersecting geometry and unambiguous shell relationships. Open, inverted, self-intersecting, touching/nested-shell or over-budget references use unsigned closest-triangle distances. Even `ENABLE_SDF_FITTING=true` does not override these validity checks. No dense voxel volume is allocated. Single-component rational panels and watertight synthetic primitives are tested; touching furniture assemblies commonly use unsigned distance.

## Native geometry and corrections

Existing boxes, cylinders, ellipsoids, outline extrusions, loft sections, swept profiles, Bezier paths and spline-shaped panel grids remain supported. The new `nurbs_panel` feature adds editable native rational B-Spline surface controls and positive rational weights, with a native thickened solid. Tests fit both NURBS control coordinates and rational weights, then export and reopen their STEP solids. Automatic multi-patch reconstruction, patch continuity inference and arbitrary upholstery remain unsupported. The automatic mesh-to-component initializer is still conservative; complex furniture needs a suitable existing engineering feature plan.

## Point counts and configuration

Defaults are **25,000 → 100,000 → 300,000** progressive fitting samples, **750,000** final verification samples, and optional **1,000,000** verification samples. Trial point counts are configurable; the validated schema bounds fine fitting to 500,000 and verification to 1,000,000. Final verification is a separate pass, not a rebuild per point. Adaptive sampling retains flat-region coverage and component quotas while increasing curvature/error-region density. Area weights compensate for oversampling.

RAM-aware reduction, chunked Open3D queries, bounded caches, Jacobian-aware trial allocation, a sampled process-RSS monitor and the existing hard worker RAM/deadline guard remain active. All execution is CPU-compatible. Low RAM on the actual 8 GiB development host triggered fallback during some curved-model and sample runs; requested and actual counts are stored separately.

The central defaults are in `DenseOptimizationOptions`, using existing environment configuration conventions. `.env.example` includes:

```dotenv
GLOBAL_3D_FITTING_ENABLED=true
PRIMARY_FITTING_METHOD=global_surface
MULTI_ANGLE_OPTIMIZATION_ENABLED=false
ORIGINAL_PHOTO_VALIDATION_ENABLED=true
GLOBAL_COARSE_POINTS=25000
GLOBAL_MEDIUM_POINTS=100000
GLOBAL_FINE_POINTS=300000
GLOBAL_VERIFY_POINTS=750000
GLOBAL_EXTENDED_VERIFY_ENABLED=false
GLOBAL_EXTENDED_VERIFY_POINTS=1000000
ENABLE_BIDIRECTIONAL_SURFACE_LOSS=true
ENABLE_POINT_TO_PLANE_LOSS=true
ENABLE_SURFACE_NORMAL_LOSS=true
ENABLE_SDF_FITTING=auto
ENABLE_COMPONENT_AWARE_FITTING=true
ENABLE_CAD_GEOMETRY_CACHE=true
MAX_LLM_CORRECTION_CALLS=0
```

Legacy angle fields remain accepted for saved-request compatibility and are ignored by the new optimizer. The active API reports no required virtual views. Existing standalone view utilities are retained for legacy artifacts and the frozen benchmark fixture, not used as the primary matching objective.

## Original photograph protection and reports

Real-photo foreground/camera fitting and reliable native projections remain available. The photo score is separate from the primary 3D objective and vetoes a candidate which worsens available visible-feature evidence. The final selected model is compared again against the photo. Automatic masks and camera poses remain labelled estimates. Hidden geometry is explicitly unverified.

Reports include both directional means, area-weighted RMS, 95th percentile and maximum sampled deviation, component suberrors, point-to-plane/normal metrics, SDF eligibility, unmatched areas, B-Rep validity, checked supplied dimensions, photo status, runtime, evaluation counts, sampled peak RSS and zero local external-AI calls. Geometric deviations use a 0.25 mm native-CAD tessellation; STEP remains authoritative B-Rep. Very small synthetic mean deviations are not a physical accuracy claim or a guarantee about unsampled regions.

## Measured comparison with the previous optimizer

The benchmark loads the exact pre-upgrade optimizer from `tests/fixtures/legacy_dense_optimizer_v1.py.txt` exclusively for comparison. Production never imports it. Both methods receive the same native furniture, initial perturbations, selected controls, resource settings and evaluation limits. A cabinet shelf is displaced 50 mm, so both methods explicitly receive the same 60 mm displacement allowance; other fixtures use 30 mm. Common post-fit errors are recomputed using the same triangle-surface engine and the same reference sample set for each pair.

Three fixtures × four densities × two methods completed: **24 runs**. Times include the optimizer's own verification and exclude interpreter startup, provider generation, the common comparison measurement and STEP export. These are single runs on a shared host, not statistically controlled performance estimates. Peak RSS is sampled at 50 ms intervals. The per-evaluation timing includes stage preparation and native validation overhead.

Quality-mode common post-fit measurements:

| Synthetic fixture | Previous seconds | Global seconds | Previous mean mm | Global mean mm | Global RMS mm |
|---|---:|---:|---:|---:|---:|
{chr(10).join(quality)}

Global fitting was faster for the table and cabinet examples, but slower for the curved backrest. It reduced common measured surface error in these fixtures. This does not establish a general speed or physical-accuracy advantage. Density escalation stopped when a denser stage failed to improve the native holdout objective.

Table and cabinet runs completed full 750,000-point verification. RAM pressure reduced the quality curved run to 260,236 solver-verification points and a 201,708-point fine stage; its common post-fit measurement still used the shared 300,000 reference points. The extended curved comparison used 423,916 shared reference samples, with different automatic solver-verification fallbacks. All requested, actual fitting, verification and comparison counts are in [raw measured benchmark data](global-fitting-benchmarks.json). No fallback is presented as a completed requested density.

## End-to-end sample and validation

The [sample assembly STEP](../examples/global_surface_pipeline/assembly.step) comes from a known synthetic table and a CAD-derived raster with a known camera. It is not an actual furniture measurement. The sample's mean deviation changed from **{sample['initial']['symmetric_mean_mm']:.6f} to {sample['final']['symmetric_mean_mm']:.6f} mm**. It requested {sample['requested_verification_points']:,} final samples and completed **{sample['actual_verification_points']:,}** after RAM fallback. It used zero virtual-camera fitting evaluations, preserved the confirmed width, performed original-raster projection checks and reopened all five component STEP files and the assembly. [Sample report](../examples/global_surface_pipeline/optimization_report.json), [editable feature specification](../examples/global_surface_pipeline/feature_specification.json).

- Focused fitting/provider/API/resource/photo/STEP tests: **{focused['tests']} passed**, {focused['time']} seconds; [JUnit](global-fitting-test-results.xml).
- Existing CAD optimizer, meshing, exports, API, reconstruction and frame compatibility: **{compat['tests']} passed**, {compat['time']} seconds; [JUnit](global-compatibility-test-results.xml).
- The compatibility run explicitly deselected the known pre-existing `test_envelope_measurement_coarsens_within_a_bounded_mesh_budget` mock-path failure. The separate pre-existing visual-repair event expectation remains recorded in the previous implementation report. The entire historical suite was not rerun for this upgrade; those unrelated failures were not changed.
- Sparse signed-distance queries were actually tested at 25,000, 100,000, 300,000, 750,000 and 1,000,000 points. Sampling/fallback, cache invalidation, SDF fallback, internal/back/bottom coverage, zero-angle leg correction, overall dimensions, curved backrests, rational controls/weights, bounds preservation, invalid-candidate rollback and zero local network calls are covered.
- The updated frontend production build and existing JavaScript submission/measurement checks pass. Browser/WebGL interaction was not tested.

## Running and production readiness

Use the original project's `.venv`. Restart the backend and background workers after applying the update so they load the new schema and solver. Existing secrets and databases are preserved. Live Neural4D generation was not exercised; its client is unchanged and remains covered by mocked submission/polling/retry tests.

```powershell
.\\.venv\\Scripts\\python.exe -m scripts.run_mesh_reconstruction furniture.obj --up-axis Z --mode quality
.\\.venv\\Scripts\\python.exe -m scripts.benchmark_global_fitting
```

This is a **development implementation, not a production/manufacturing release**. Automatic segmentation, arbitrary sculpted/soft furniture, multi-patch continuity, real-photo camera reliability, hardware-dependent density limits, deployment behavior and physical/structural validation remain limitations. A bounded local optimum can retain residual error. Final verification may outlast the numerical solve budget; the isolated worker supplies the additional hard deadline. Whole-surface agreement with an estimated mesh is not proof of hidden construction or physical accuracy.

## Created / changed source files

'''
    report+='\n'.join('- `'+name+'`' for name in source_names)
    report+='\n\nGenerated sample artifacts, JUnit reports and measured benchmark JSON accompany these files. The hash-checked update is recorded in `global-upgrade-manifest.json`.\n'
    (docs/'GLOBAL_3D_FITTING_REPORT.md').write_text(report,encoding='utf-8')
    changed.extend(docs/name for name in ('GLOBAL_3D_FITTING_REPORT.md','global-fitting-benchmarks.json',
        'global-fitting-test-results.xml','global-compatibility-test-results.xml','global-source-inventory.txt'))
    baseline=json.loads((ROOT/'outputs/global_upgrade_baseline/original_hashes.json').read_text())
    previous=json.loads((docs/'mesh-upgrade-manifest.json').read_text())
    previous_hashes={entry['path']:entry['source_sha256'] for entry in previous['files']}
    entries=[]
    for path in sorted(set(changed)):
        relative=path.relative_to(ROOT).as_posix();before=ORIGINAL/relative
        expected=baseline.get(relative,previous_hashes.get(relative))
        actual=digest(before) if before.is_file() else None
        if actual!=expected:
            raise ValueError('Original project changed since the audited baseline: '+relative)
        entries.append(dict(path=relative,source_sha256=digest(path),previous_sha256=expected))
    manifest=dict(version=2,original_root=str(ORIGINAL),files=entries)
    (docs/'global-upgrade-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(f'Prepared {len(entries)} reviewed updates and the measured global fitting report')


if __name__=='__main__': main()
