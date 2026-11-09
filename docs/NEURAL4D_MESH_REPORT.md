# Neural4D furniture mesh-to-native-CAD implementation

Date: 28 October 2026 (Asia/Colombo).

## Status

Implemented and tested in the existing application source copied into `D:\Enfection\furniture cad`.
The reviewed update targets the original project at `D:\Enfection\furniture_planner\furniture_manufacturing_starter`. Python installations and all Python checks use `.venv\Scripts\python.exe`; no global Python packages were installed.

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

New authenticated routes are `GET /api/system/neural4d/status`, `POST /api/projects/{project_id}/mesh-reconstruction`, job status/resume routes under `/api/mesh-reconstruction/{job_id}`, and project-private artifact downloads. Heavy work runs in isolated child processes dispatched by the existing local/Celery system. The new job registry uses durable SQLite under the shared artifact root and recovers interrupted workers without regenerating a provider model. Artifact manifests and packages have integrity hashes.

The existing frontend now offers a mesh workflow independently of photo engineering analysis, confirmed dimension entry, Economy/Balanced/Quality/Extended modes, progress, five geometry previews, deviation colors, component uncertainty, partial-result status and native downloads. The mesh workflow does not trigger the existing OpenAI analysis on upload. The production frontend builds successfully; a browser/WebGL interaction test was not run.

Outputs include assembly and individual STEP files, editable feature JSON and CadQuery source, preserved original reference mesh/photo, initial/optimized/overlay/deviation GLB previews, compressed point deviations, calibration/registration/component reports, parameter history and a ZIP package. The new mesh workflow does not yet invoke the existing dimensioned-drawing exporter. Optional STL export is not exposed in this workflow.

## Measured synthetic benchmark

Same five-part table, same 15 mm perturbation of one leg, same bounded parameter selection. CPU only; no GPU backend or provider generation. Timings include sampling, query/view exercises and fitting/final measurement; they exclude interpreter/library startup, provider processing and STEP export. These are one-run shared-host measurements, not an isolated performance study. Peak RAM is sampled process RSS at 50 ms intervals. The host has 7.65 GiB RAM; automatic fallback is expected under concurrent memory pressure.

| Mode | Actual points | Views | Total seconds | Query seconds | Ray/view seconds | Final mean mm | Sampled peak RSS MiB |
|---|---:|---:|---:|---:|---:|---:|---:|
| economy | 25,000 | 12 | 5.846 | 0.007 | 0.025 | 0.012258 | 213.9 |
| balanced | 100,000 | 24 | 3.779 | 0.025 | 0.060 | 0.006325 | 135.8 |
| quality | 300,000 | 36 | 6.009 | 0.064 | 0.067 | 0.006292 | 161.1 |
| extended | 750,000 | 48 | 4.503 | 0.143 | 0.080 | 0.006105 | 326.4 |

Initial mean deviation was 0.698071 mm. All benchmark candidates retained valid solids. Per-evaluation timings, rebuild time, percentiles and stage decisions are in [raw benchmark data](dense-benchmarks.json). The 300,000-point stage produced no accepted additional revision; the small change in reported mean between 100,000 and 300,000 points does not demonstrate meaningful fitting improvement. Automatic density escalation stopped accordingly.

The existing photo optimizer took 0.847 s and measured 0.006265 mm surface deviation on the same fixture, using synthetic image landmarks. Its evidence and objective units differ, so this does not establish a speed or accuracy advantage for dense fitting. [Legacy comparison](dense-legacy-comparison.json).

The complete sample in [examples/dense_mesh_pipeline](../examples/dense_mesh_pipeline) uses a CAD-derived raster and an exactly known synthetic projection. Mean reference deviation changed from **0.707292 to 0.016370 mm**. Final verification actually used **750,000 points and 48 views**. The assembly and all five component STEP files reopened and passed the existing native checks. [Sample STEP](../examples/dense_mesh_pipeline/assembly.step), [sample report](../examples/dense_mesh_pipeline/optimization_report.json), [editable specification](../examples/dense_mesh_pipeline/feature_specification.json).

## Validation results

- Latest focused mesh/provider/legacy-route tests: **50 passed**; [JUnit](neural4d-mesh-test-results.xml).
- Existing CAD optimizer, CAD meshing, exports and API compatibility checks: **56 passed**; [JUnit](mesh-compatibility-results.xml).
- Broad regression run: **574 passed, 6 skipped, 3 failed** in 1126.55 s; [JUnit](full-test-results.xml). One failure was the superseded assertion that Neural4D status routes must not exist; its replacement now passes in the focused run. The other two failures reproduced unchanged in the original repository: `test_invalid_visual_correction_retains_the_previously_valid_draft` (event-name expectation) and `test_envelope_measurement_coarsens_within_a_bounded_mesh_budget` (mock targets a tessellation path the implementation no longer uses). Their unrelated implementation/tests were left unchanged.
- One-million-point sampling/queries passed in the broad run when resources allowed. A later concurrent stress run correctly lowered density under host RAM pressure; the deterministic test now freezes its allocation grant, while the separate resource-fallback test checks automatic reduction.
- Real-photo foreground/camera comparison is exercised against the existing JPEG fixture without asserting measured 3D truth. Synthetic tests cover fitting reduction, confirmed-width preservation, invalid-candidate rollback, photo-conflict veto, native rational surfaces, STEP reopening, caches, parallel read-only queries, safe paid-call handling, account-key rotation and interrupted-job recovery.
- Frontend production build and existing submission/measurement JavaScript checks pass. Python compile checks pass. `uv pip check --python .venv\Scripts\python.exe` reports all 149 installed packages compatible.

## Running it

Configure `NEURAL4D_API_KEY` and optional `NEURAL4D_BASE_URL` in the backend environment, retain the one-generation/zero-correction defaults in `.env.example`, then restart the backend using its project `.venv`. No existing secrets or databases are replaced by the source update.

Offline reconstruction makes no provider calls:

```powershell
.\.venv\Scripts\python.exe -m scripts.run_mesh_reconstruction furniture.obj --up-axis Z --mode quality --height 750
.\.venv\Scripts\python.exe -m scripts.create_dense_mesh_sample
.\.venv\Scripts\python.exe -m scripts.benchmark_dense_geometry
```

Use `--plan` for an existing `PhotoDesign`, `--photo` for an original raster, and `--known` for confirmed dimension JSON. `--height` alone is an estimate.

## Remaining limitations / readiness

Live Neural4D availability, billing behavior and production asset-host behavior are unverified. Automatic component recovery is conservative and does not reconstruct arbitrary fused, upholstered or sculpted furniture. Multi-patch NURBS fitting/continuity, symmetry-driven manufactured-part inference and guaranteed hidden construction recovery are incomplete. Curved proxy trials remain approximations; only exact rebuilt native solids are accepted. Final deviation is measured against a temporary CAD tessellation at 0.25 mm, not an exact B-Rep distance for every reference point. Photo camera fits and foreground estimates need review. Final verification is separately budgeted and may outlast the numerical solver's cooperative time limit; the worker has an additional hard deadline and RAM monitor. Celery/shared-storage deployment and browser interaction require deployment-specific testing. The known baseline regression failures remain. These results support further development and review, not manufacturing release.

## Created / modified implementation files

- `.env.example`
- `app/adaptive_view_selector.py`
- `app/api/neural4d.py`
- `app/cad_optimizer.py`
- `app/config.py`
- `app/dense_cad_optimizer.py`
- `app/geometry_acceleration.py`
- `app/geometry_cache.py`
- `app/integrations/__init__.py`
- `app/integrations/neural4d_client.py`
- `app/jobs.py`
- `app/main.py`
- `app/mesh_analyzer.py`
- `app/mesh_cad_schema.py`
- `app/mesh_photo_verification.py`
- `app/mesh_pipeline.py`
- `app/mesh_pipeline_worker.py`
- `app/mesh_reconstruction_jobs.py`
- `app/mesh_registration.py`
- `app/mesh_sampling.py`
- `app/mesh_segmentation.py`
- `app/mesh_to_cad.py`
- `app/multiview_geometry.py`
- `app/studio_dist/assets/DimensionSheet-B4_wpHnI.js`
- `app/studio_dist/assets/Viewer-C9h9u3D5.js`
- `app/studio_dist/assets/index-BKQA_KL4.css`
- `app/studio_dist/assets/index-Dzq3pJTz.js`
- `app/studio_dist/index.html`
- `frontend/src/MeshReconstruction.jsx`
- `frontend/src/main.jsx`
- `frontend/src/style.css`
- `pyproject.toml`
- `requirements.txt`
- `scripts/apply_mesh_upgrade.ps1`
- `scripts/benchmark_dense_geometry.py`
- `scripts/create_dense_mesh_sample.py`
- `scripts/prepare_mesh_upgrade.py`
- `scripts/run_mesh_reconstruction.py`
- `tests/test_dense_cad_optimizer.py`
- `tests/test_dense_mesh_geometry.py`
- `tests/test_image_to_cad.py`
- `tests/test_mesh_photo_verification.py`
- `tests/test_mesh_pipeline.py`
- `tests/test_mesh_reconstruction_api.py`
- `tests/test_mesh_reconstruction_recovery.py`
- `tests/test_neural4d_provider.py`
- `uv.lock`

Sample artifacts, measured reports and JUnit files accompany these source changes. `mesh-upgrade-manifest.json` lists exact hashes and original-file hashes for the reviewed update.
