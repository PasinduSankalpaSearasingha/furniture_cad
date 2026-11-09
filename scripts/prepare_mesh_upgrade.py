"""Prepare a reviewable manifest and measured report before updating the source."""
import hashlib
import json
from pathlib import Path
import platform
import shutil
import xml.etree.ElementTree as ET
import psutil

WORKSPACE = Path(__file__).resolve().parent.parent
ORIGINAL = Path(r'D:\Enfection\furniture_planner\furniture_manufacturing_starter')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    docs = WORKSPACE/'docs'; docs.mkdir(exist_ok=True)
    benchmark = WORKSPACE/'outputs/dense_benchmark'
    for source, name in [('benchmarks.json','dense-benchmarks.json'), ('legacy_comparison.json','dense-legacy-comparison.json')]:
        shutil.copy2(benchmark/source, docs/name)
    sample = WORKSPACE/'examples/dense_mesh_pipeline'; sample.mkdir(parents=True, exist_ok=True)
    for name in ('assembly.step','F001.step','F002.step','F003.step','F004.step','F005.step',
                 'feature_specification.json','editable_cad.py','synthetic_reference.obj','original_photo.png',
                 'optimization_report.json','geometry_deviation_report.json','parameter_revision_history.json',
                 'overlay.glb','optimized_cad.glb','reference.glb','deviation.glb','component_graph.json'):
        shutil.copy2(WORKSPACE/'outputs/dense_sample'/name, sample/name)
    rows = json.loads((docs/'dense-benchmarks.json').read_text())
    legacy = json.loads((docs/'dense-legacy-comparison.json').read_text())
    demo = json.loads((sample/'optimization_report.json').read_text())
    files = []
    for root in ('app','tests','frontend/src','scripts','examples/dense_mesh_pipeline'):
        for path in (WORKSPACE/root).rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix not in ('.pyc', '.part'):
                relative = path.relative_to(WORKSPACE)
                before = ORIGINAL/relative
                if not before.is_file() or sha(before) != sha(path):
                    files.append(relative.as_posix())
    for name in ('requirements.txt','pyproject.toml','uv.lock','.env.example','README.md'):
        if sha(WORKSPACE/name) != sha(ORIGINAL/name): files.append(name)
    code_files = sorted(set(files))
    bench_table = '\n'.join(f"| {r['mode']} | {r['actual_points']:,} | {r['virtual_views']} | {r['total_seconds']:.3f} | {r['mesh_query_seconds']:.3f} | {r['raycasting_seconds']:.3f} | {r['final_mean_deviation_mm']:.6f} | {r['peak_process_rss_mb']:.1f} |" for r in rows)
    report = f'''# Neural4D furniture mesh-to-native-CAD implementation

Date: 28 October 2026 (Asia/Colombo).

## Status

Implemented and tested in the existing application source copied into `D:\\Enfection\\furniture cad`.
The reviewed update targets the original project at `{ORIGINAL}`. Python installations and all Python checks use `.venv\\Scripts\\python.exe`; no global Python packages were installed.

This is a working development implementation for supported component plans and conservative primitive reconstruction. It is **not production-ready** and does not demonstrate physical reconstruction accuracy.

## Repository audit and reuse

Reused `PhotoDesign`, `PhotoPart`, `create_photo_furniture`, the CAD parameter registry and measurement locks, exact native solid/contact/interference checks, per-component STEP round-trip verification, project authorization, the local/Celery dispatchers, and existing foreground/camera/photo-projection code. The existing photo-engineering workflow and retired legacy paid routes retain their contracts. [Source inventory](source_inventory.txt) records inspected module symbols.

## Neural4D integration

The implementation follows the [official API v1.2 documentation](https://blog.neural4d.com/wp-content/uploads/2026/01/Neural4D-API-Documentation.pdf), linked from the provider references. It uses Bearer authentication, multipart matting uploads, JSON matting-result retrieval, `generateModelWithImage` with `modelCount=1` and `disablePbr=1`, `retrieveModel`, and bounded GLB downloads. Optional OBJ conversion requires explicit enablement.

Submission intents are committed in SQLite before outbound requests. Paid submission failures never retry automatically. Polling has bounded read-only retries. Task UUIDs, ambiguous outcomes, usage attempts and cached mesh hashes persist. Per-job bindings survive credential rotation and application restarts. Successful downloads are reused across jobs. Tokens are environment-only. Provider usage is distinct from local processing; documented point estimates are not asserted as verified charges or USD costs.

**Live provider generation remains unverified**: no Neural4D credentials were configured in the working environment. All provider tests use HTTPX mock transports. No paid provider or LLM calls were made for this work. Pending OBJ conversions require manual reconciliation because automatic repeated conversion requests could incur charges.

## Geometry capabilities

- GLB scene instances retain node transforms; OBJ and GLB validation records degenerate faces, boundaries, non-manifold edges, winding, components, normals, dihedral curvature and uncertainty.
- Deterministic stratified face-area sampling supports **25,000 / 100,000 / 300,000 / 750,000** points and optional **1,000,000** points. Component quotas, curvature/error importance and inverse-probability area weights prevent sample-density bias. Counts automatically decrease under RAM limits.
- Open3D CPU RaycastingScene performs actual closest-triangle queries and ray intersections in chunks; cKDTree is explicitly identified as a fast point-cloud approximation. The reference index is reused through optimization.
- **12, 24, 36 and 48** virtual camera layouts, with optional top/bottom views, share identical CAD/reference camera parameters. Adaptive selection rewards new visible area and component coverage. Final verification can evaluate all 48 views.
- Registration uses a uniform calibrated scale, Y-up/Z-up conversion, 24 proper axis orientations and trimmed multiscale rigid ICP. Transforms and calibration decisions are stored separately from CAD deformation. Inconsistent supplied proportions produce an explicit calibration failure.
- Automatic segmentation conservatively recognizes disconnected, near-full-volume oriented box regions and records contact/repetition hypotheses. Fused or sculpted regions require an existing reviewed engineering plan or remain unresolved; connectivity is never treated as proof of a manufactured part.
- The authoritative geometry remains the existing CadQuery/OCP B-Rep. Existing feature plans support boxes, cylinders, ellipsoids, outline extrusions, lofts, round/elliptical/rectangular/profile sweeps, Bezier curves, spline-shaped panel grids, fillets and Boolean features. Native B-Spline curves and rational clamped NURBS patches are available and tested. Automatic multi-patch NURBS reconstruction is not implemented.

## Optimization and constraints

`cad_optimizer.optimize_mesh_cad` adds dense fitting alongside the existing image optimizer. Bounded SciPy `least_squares` uses grouped global/position/dimension/curve controls, sensitivity probes, adaptive steps, robust `soft_l1`, evaluation/time limits and stagnation stopping. Dense reference samples are fixed per stage. Fast trials use cached component samples and mathematical proxy deformations; shortlisted trials rebuild real native solids, enforce supplied measurements and contacts, reject interference/invalid solids, and compare bidirectional triangle-surface distances and multiview silhouettes. Only improving valid revisions survive. STEP checkpoints preserve the last valid revision before final verification.

The actual uploaded photo is compared using the existing foreground/camera fitting machinery. Automatically estimated masks and cameras are labelled unreviewed estimates. Reliable camera/landmark/curve/reviewed-silhouette evidence uses native projection, and a worsening photo score vetoes a candidate even if the reference mesh prefers it. Virtual views supply no additional physical evidence. Local optimization makes zero provider-generation and zero LLM calls.

## Backend, frontend and outputs

New authenticated routes are `GET /api/system/neural4d/status`, `POST /api/projects/{{project_id}}/mesh-reconstruction`, job status/resume routes under `/api/mesh-reconstruction/{{job_id}}`, and project-private artifact downloads. Heavy work runs in isolated child processes dispatched by the existing local/Celery system. The new job registry uses durable SQLite under the shared artifact root and recovers interrupted workers without regenerating a provider model. Artifact manifests and packages have integrity hashes.

The existing frontend now offers a mesh workflow independently of photo engineering analysis, confirmed dimension entry, Economy/Balanced/Quality/Extended modes, progress, five geometry previews, deviation colors, component uncertainty, partial-result status and native downloads. The mesh workflow does not trigger the existing OpenAI analysis on upload. The production frontend builds successfully; a browser/WebGL interaction test was not run.

Outputs include assembly and individual STEP files, editable feature JSON and CadQuery source, preserved original reference mesh/photo, initial/optimized/overlay/deviation GLB previews, compressed point deviations, calibration/registration/component reports, parameter history and a ZIP package. The new mesh workflow does not yet invoke the existing dimensioned-drawing exporter. Optional STL export is not exposed in this workflow.

## Measured synthetic benchmark

Same five-part table, same 15 mm perturbation of one leg, same bounded parameter selection. CPU only; no GPU backend or provider generation. Timings include sampling, query/view exercises and fitting/final measurement; they exclude interpreter/library startup, provider processing and STEP export. These are one-run shared-host measurements, not an isolated performance study. Peak RAM is sampled process RSS at 50 ms intervals. The host has {psutil.virtual_memory().total/1024**3:.2f} GiB RAM; automatic fallback is expected under concurrent memory pressure.

| Mode | Actual points | Views | Total seconds | Query seconds | Ray/view seconds | Final mean mm | Sampled peak RSS MiB |
|---|---:|---:|---:|---:|---:|---:|---:|
{bench_table}

Initial mean deviation was {rows[0]['initial_mean_deviation_mm']:.6f} mm. All benchmark candidates retained valid solids. Per-evaluation timings, rebuild time, percentiles and stage decisions are in [raw benchmark data](dense-benchmarks.json). The 300,000-point stage produced no accepted additional revision; the small change in reported mean between 100,000 and 300,000 points does not demonstrate meaningful fitting improvement. Automatic density escalation stopped accordingly.

The existing photo optimizer took {legacy['seconds']:.3f} s and measured {legacy['final_surface_deviation_mm']:.6f} mm surface deviation on the same fixture, using synthetic image landmarks. Its evidence and objective units differ, so this does not establish a speed or accuracy advantage for dense fitting. [Legacy comparison](dense-legacy-comparison.json).

The complete sample in [examples/dense_mesh_pipeline](../examples/dense_mesh_pipeline) uses a CAD-derived raster and an exactly known synthetic projection. Mean reference deviation changed from **{demo['initial']['symmetric_mean_mm']:.6f} to {demo['final']['symmetric_mean_mm']:.6f} mm**. Final verification actually used **{demo['actual_verification_points']:,} points and {demo['multiview']['count']} views**. The assembly and all five component STEP files reopened and passed the existing native checks. [Sample STEP](../examples/dense_mesh_pipeline/assembly.step), [sample report](../examples/dense_mesh_pipeline/optimization_report.json), [editable specification](../examples/dense_mesh_pipeline/feature_specification.json).

## Validation results

- Latest focused mesh/provider/legacy-route tests: **50 passed**; [JUnit](neural4d-mesh-test-results.xml).
- Existing CAD optimizer, CAD meshing, exports and API compatibility checks: **56 passed**; [JUnit](mesh-compatibility-results.xml).
- Broad regression run: **574 passed, 6 skipped, 3 failed** in 1126.55 s; [JUnit](full-test-results.xml). One failure was the superseded assertion that Neural4D status routes must not exist; its replacement now passes in the focused run. The other two failures reproduced unchanged in the original repository: `test_invalid_visual_correction_retains_the_previously_valid_draft` (event-name expectation) and `test_envelope_measurement_coarsens_within_a_bounded_mesh_budget` (mock targets a tessellation path the implementation no longer uses). Their unrelated implementation/tests were left unchanged.
- One-million-point sampling/queries passed in the broad run when resources allowed. A later concurrent stress run correctly lowered density under host RAM pressure; the deterministic test now freezes its allocation grant, while the separate resource-fallback test checks automatic reduction.
- Real-photo foreground/camera comparison is exercised against the existing JPEG fixture without asserting measured 3D truth. Synthetic tests cover fitting reduction, confirmed-width preservation, invalid-candidate rollback, photo-conflict veto, native rational surfaces, STEP reopening, caches, parallel read-only queries, safe paid-call handling, account-key rotation and interrupted-job recovery.
- Frontend production build and existing submission/measurement JavaScript checks pass. Python compile checks pass. `uv pip check --python .venv\\Scripts\\python.exe` reports all 149 installed packages compatible.

## Running it

Configure `NEURAL4D_API_KEY` and optional `NEURAL4D_BASE_URL` in the backend environment, retain the one-generation/zero-correction defaults in `.env.example`, then restart the backend using its project `.venv`. No existing secrets or databases are replaced by the source update.

Offline reconstruction makes no provider calls:

```powershell
.\\.venv\\Scripts\\python.exe -m scripts.run_mesh_reconstruction furniture.obj --up-axis Z --mode quality --height 750
.\\.venv\\Scripts\\python.exe -m scripts.create_dense_mesh_sample
.\\.venv\\Scripts\\python.exe -m scripts.benchmark_dense_geometry
```

Use `--plan` for an existing `PhotoDesign`, `--photo` for an original raster, and `--known` for confirmed dimension JSON. `--height` alone is an estimate.

## Remaining limitations / readiness

Live Neural4D availability, billing behavior and production asset-host behavior are unverified. Automatic component recovery is conservative and does not reconstruct arbitrary fused, upholstered or sculpted furniture. Multi-patch NURBS fitting/continuity, symmetry-driven manufactured-part inference and guaranteed hidden construction recovery are incomplete. Curved proxy trials remain approximations; only exact rebuilt native solids are accepted. Final deviation is measured against a temporary CAD tessellation at 0.25 mm, not an exact B-Rep distance for every reference point. Photo camera fits and foreground estimates need review. Final verification is separately budgeted and may outlast the numerical solver's cooperative time limit; the worker has an additional hard deadline and RAM monitor. Celery/shared-storage deployment and browser interaction require deployment-specific testing. The known baseline regression failures remain. These results support further development and review, not manufacturing release.

## Created / modified implementation files

'''
    report += '\n'.join('- `'+name+'`' for name in code_files if not name.startswith('examples/'))
    report += '\n\nSample artifacts, measured reports and JUnit files accompany these source changes. `mesh-upgrade-manifest.json` lists exact hashes and original-file hashes for the reviewed update.\n'
    (docs/'NEURAL4D_MESH_REPORT.md').write_text(report, encoding='utf-8')
    files.extend('docs/'+name for name in ('NEURAL4D_MESH_REPORT.md','source_inventory.txt','dense-benchmarks.json',
        'dense-legacy-comparison.json','neural4d-mesh-test-results.xml','mesh-compatibility-results.xml','full-test-results.xml'))
    entries = []
    for name in sorted(set(files)):
        source, before = WORKSPACE/name, ORIGINAL/name
        entries.append(dict(path=name, source_sha256=sha(source), previous_sha256=sha(before) if before.is_file() else None))
    manifest = dict(version=1, original_root=str(ORIGINAL), files=entries)
    (docs/'mesh-upgrade-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(f'Prepared {len(entries)} reviewed file updates, measured report, and sample native STEP')


if __name__ == '__main__':
    main()
