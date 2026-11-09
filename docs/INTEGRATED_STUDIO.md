# Integrated AI, CAD and Blender studio

Fiske Studio uploads use [Engineering CAD](ENGINEERING_CAD.md). This guide also
covers the optional CAD/Blender APIs retained for existing projects.

The React interface is served at **http://127.0.0.1:8000/studio**. The original interface remains at `/`, with a link to the new studio. Existing project IDs, image storage, CAD packages, database tables, versioned APIs and engineering release gates are retained.

## Windows setup

Use Python 3.11, Node.js 22+ and Blender 4.5 or newer. This machine was verified with Python 3.11.9, Node 24 and Blender 5.2.2 LTS. Install Blender from its official distribution; no Python `bpy` package is needed in the backend environment.

```powershell
uv sync --frozen
.venv\Scripts\python.exe -m alembic upgrade head
cd frontend
npm ci
npm run build
cd ..
uv run --no-sync --env-file .env python -m uvicorn app.main:app --reload
```

The built frontend is included in the source delivery. Rebuild it after changing React source. For frontend development, run `npm run dev` in `frontend`; Vite proxies `/api` to port 8000. Production uses the compiled same-origin assets without CDNs.

The prepared `.env` preserves the existing key and selects:

```dotenv
LOCAL_WORKSPACE=1
JOB_EXECUTOR=local
AI_ENABLED=1
OPENAI_REASONING_MODEL=gpt-6.1-sol
OPENAI_IMAGE_MODEL=gpt-image-2.5-sunburst
OPENAI_REASONING_EFFORT=high
BLENDER_RENDER_DEVICE=AUTO
```

Restart the server after changing `.env`: Uvicorn code reload does not reload a process's original environment. Keep the key backend-only. There are no password/key-entry forms in the local React studio. Local mode requires a direct same-origin loopback connection; shared deployments use the existing authenticated API.

`BLENDER_EXECUTABLE` may be blank. Discovery checks PATH, Windows Blender Foundation installations, macOS application binaries and Linux executable directories, then runs `--version`. An explicit path takes priority and fails clearly if missing. No Blender version or installation folder is hardcoded in the rendering service.

## Connected workflows

1. **Text → concept → CAD → render:** generate one to three concepts with the configured reasoning model's forced native Responses `image_generation` tool or the direct Sunburst Image API. Select a real saved concept, enter known dimensions, then create CAD and Blender renders. Quality, dimensions and format are configurable. Concepts are saved only after raster validation; generated originals are downloadable for new concept jobs, with normalized PNGs for private UI previews.
2. **Photo → CAD → render:** upload a reference, analyze it for relevant optional inputs, enter any known values, then generate. Missing values are explicitly estimated. Actual outlines, curves and components drive geometry; no furniture template selection is required.
3. **Existing plan → modified CAD → render:** change named parameters deterministically, or request a natural-language change through GPT-6.1 Sol. Preserve previous revisions, regenerate STEP/GLB and schedules, then render the new geometry. Editing is available for photo/component-plan revisions. Older cabinet/library APIs remain available; arbitrary imported STEP files do not have recoverable parametric history.
4. **Visual review → accepted correction:** compare original design references with the actual Cycles render. The configured reasoning model returns typed proposals distinguishing geometry and appearance. Accepting a numeric correction changes an eligible named parameter; accepting a geometry proposal creates a fresh plan/revision. Rejecting is audited. User-fixed or verified dimensions cannot change through visual correction. At most three accepted visual-refinement generations are allowed in a lineage. Saved revisions are never changed automatically. Material-only proposals use the finish controls rather than CAD correction.

All dimension inputs may be left blank. The model infers missing values from the reference, common proportions and any supplied constraints. If an unprovided overall estimate differs from the generated CAD envelope beyond the existing estimate tolerance (the greater of 2 mm or 1%), the workflow resolves that estimate to the actual CAD bounds before freezing the revision. It rebinds parameter expressions and rebuilds the plan to verify that component volumes and placements remain unchanged. This prevents a guessed 600 mm width from blocking valid 584.47 mm geometry. Supplied values retain their strict checks; invalid solids still fail. Resolved estimates remain unverified, with their original estimate and CAD-envelope basis recorded in provenance. This policy applies to both the classic photo workflow and the Studio CAD tool.

Before freezing a new image-derived plan, the workflow renders actual CAD triangles with depth occlusion from an estimated reference camera and an opposite view. A separate typed comparison identifies visible geometry discrepancies. Up to `AI_PHOTO_REFINEMENT_PASSES=2` targeted correction passes may update only the flagged components; all existing parameter values and component materials/colors are protected. Every accepted candidate is rebuilt and checked against supplied constraints. Comparison images, plan hashes, review history and correction events are checkpointed. Preview and comparison checkpoints include the plan and camera hashes, so a retry cannot mistake an earlier rejected candidate for the corrected model. This adds bounded AI review/design usage. A remaining mismatch is displayed as **Reference match needs review**; an AI visual match is advisory and never sets `exact_reconstruction_verified=true`.

Repeated dimension-only submissions for the same reference reuse a saved plan. Image content hashes identify equivalent uploads within the same project. Only submitted numeric values and their dependent parametric geometry change; other parameter values and component expressions, profiles and colors stay intact. No new planning or automatic visual redesign occurs for these edits. An explicit `source_revision_id` also supports a deliberate edit of an older plan. A blank submission can compare and refine an older saved plan that has not yet passed this policy, or one still marked as needing visual review. New notes or a new material/design request require a new planning path. A single unscaled reference cannot determine exact hidden surfaces or true dimensions, and these mechanisms do not establish exact photographic reconstruction.

Photo planning defaults to high reasoning, a 28,000-token output budget and a 600-second request timeout; `AI_PHOTO_REASONING_EFFORT`, `AI_PHOTO_MAX_OUTPUT_TOKENS` and `AI_PHOTO_TIMEOUT_SECONDS` override these. Analysis and bounded field-edit comparisons use medium reasoning. Corrections return changed fields rather than repeating complete part definitions. Undefined simple aliases may retain the corresponding existing scalar only when all occurrences agree. Intermediate arithmetic may reach 10^12, while final geometry expressions retain their 100,000-unit bound. Optional comparison/provider failures retain valid CAD with an incomplete-review status; integrity failures still stop the workflow.

Rounded rectangular loft sections, small solid edge fillets, softened sweep-end rims and multiple shallow ellipsoid seat hollows are available as actual CAD geometry. An unsupported all-edge fillet on a compound sculpted seat or rounded rail is deferred with an explicit warning while retaining the previous edge rounding. Independently valid component corrections can survive another component's invalid proposal; accepted solids still undergo the full validation. GLB exports contain smooth normals within each CAD face while retaining sharp boundaries between distinct faces. These rendering improvements do not change existing STEP files or substitute texture appearance for surface reconstruction.

The `cad_generation` function is a real Responses API tool. Its strict arguments are a declarative `PhotoDesign`; the handler executes CadQuery/OpenCascade preflight on positive valid solids and measured envelopes. The final AI plan must hash-match a plan successfully validated by that tool. There are at most two geometry trials per planning attempt and two planning attempts. No generated Python, shell code or arbitrary library selectors execute.

## Geometry and manufacturing

STEP/B-Rep remains the engineering source. GLB is its metre/Y-up visualization mesh. Parts retain names, materials, transforms, dimensions, revision, evidence and review status. Supported primitives include boxes, elliptic cylinders, ellipsoids, closed outline panels, multi-section lofts and round/oval/rectangular sweeps.

Controlled local features include holes, pockets, grooves, fillets, chamfers, dowel/confirmat bores, dados, rabbets, mortises and integral tenons. Feature history and machining coordinates are exported. These photo-design features are **unverified geometry**, not approved connections.

The existing approved-profile joint engine additionally supports paired blind dowels, cardinal confirmat clearance/pilot bores, and rectangular receiving pockets with matching integral tenons for dado/rabbet/mortise-tenon profiles. Explicit profile dimensions, material compatibility, approval and evidence references are required. Pocket containment, supported tenon roots, residual depth, connected solids and absence of interference are checked atomically. Added tenons update actual blank envelopes and cutting schedules. These checks establish software geometry only; they do not qualify strength, residual wall safety or a factory method.

Schedules derive from the parts registry and actual solids: CSV/XLSX/JSON cutting envelopes, material area/volume BOM, glass/hardware records, geometry-derived DXF/PDF views and classified validation findings. Unknown stock, joints, hardware, tolerances and procurement data remain blocked. Curved components use enclosing review blanks. No approved CNC programs or complete shop-floor instructions are claimed.

## Blender execution and artifacts

The backend launches trusted `blender/render_scene.py` with `--background --factory-startup --disable-autoexec`, validated JSON, an argument array and `shell=False`. Blender runs in a separate process. Local rendering has its own bounded queue; shared deployments route rendering to a separate Celery `renders` queue.

The script imports the real CAD GLB and checks every component name, parent, metre dimensions, orientation and placement against the registry. It applies no blind 1000× scale conversion. Rendering limits include timeout, threads, triangles, image resolution and an estimated working-set budget. The budget estimate is not an operating-system RAM quota.

Materials are self-authored procedural PBR shader graphs: wood grain, bump and variable roughness; clear/frosted/tinted glass transmission; brushed/chrome/black/aluminum metal responses; plywood, MDF, laminates and painted surfaces. They are more than flat colors but are not scanned licensed texture sets. No external textures are downloaded. HDRI uses a configured local `.hdr`/`.exr` supplied under an appropriate licence; selecting HDRI without a file returns an error. Studio/product presets provide real area lights, environment light and ground shadows without external assets.

Draft/preview/high/ultra presets bound samples and resolution. AUTO tries available Cycles GPU backends and falls back to CPU if unavailable or a render raises a device error. Output includes the actual device and fallback status. Cameras frame model bounds for front, side, back, top, isometric, perspective hero and detail views; orthographic/perspective selection and focal length are supported by the API.

Each render persists:

```text
render_front.png / render_side.png / render_top.png
render_isometric.png / render_hero.png
furniture_scene.blend
blender_report.json
render_manifest.json
blender_render_package.zip
blender_stdout.log / blender_stderr.log
```

Only requested views are generated. PNG dimensions, scene presence, component validation and SHA-256 digests are checked before success. Private downloads verify their digests. The editable `.blend` preserves separate components, materials, lights and cameras. A failed or timed-out render remains failed; no mock render is substituted.

## API examples

Import [the Postman collection](forma_studio.postman_collection.json), or use `/docs`. Long operations return a workflow ID and `status_url`; poll `/api/v1/agents/workflows/{id}`. Progress/events, invocations, usage, explicit retries and source hashes use the existing persistence engine. Unknown pricing remains `PRICE_REQUIRED`; timeouts can leave usage incomplete.

```http
POST /api/projects
{"name":"Oak writing desk"}

POST /api/projects/{project_id}/generate-concept
{"prompt":"Modern oak desk with rounded corners","quality":"low","count":1}

POST /api/projects/{project_id}/concepts/{image_id}/select
{}

POST /api/projects/{project_id}/generate-cad
{"concept_id":"IMAGE_UUID","known_values":[{"key":"width_mm","value":1200},{"key":"depth_mm","value":600},{"key":"height_mm","value":750}],"render_options":{"quality":"preview","finish":"oak"}}

PATCH /api/projects/{project_id}/parameters
{"parameter_changes":[{"key":"width_mm","value":1300}],"auto_render":true}

POST /api/projects/{project_id}/modify
{"prompt":"Make the tabletop thicker while retaining the overall height"}

POST /api/projects/{project_id}/render/preview
{"render_options":{"quality":"preview","finish":"walnut","views":["hero","front"]}}

POST /api/projects/{project_id}/refine
{"render_workflow_id":"COMPLETED_RENDER_WORKFLOW_UUID"}
```

Also available: upload-image, analyze, edit-concept, concepts, revisions, status, components, renders, cut-list, bom, drawings, render/final, operations, correction accept/reject, and download/{step,glb,stl,blend,renders,package}. The CAD package and Blender package have separate downloads. `/api/system/{cad,blender,openai}/status` reports configuration without exposing credentials. Status does not make a paid model probe.

The original `/api/v1/ai/generate-render` endpoint remains an **AI presentation image**, clearly different from the new geometry-faithful `/render/preview` and `/render/final` endpoints.

## Workers and deployment

### Image to Engineering CAD

The upload screen creates separate solid CAD parts with the configured backend AI
and local CadQuery. See [IMAGE_TO_CAD.md](IMAGE_TO_CAD.md) and
[ENGINEERING_CAD.md](ENGINEERING_CAD.md) for configuration, checks and downloads.

### GPT-6.1 Sol with native background Blender modeling

The optional `/reconstruct-with-blender` API supports native background modeling. This path pins vision/design/review to `gpt-6.1-sol`, disables cross-model fallback, and uses the Responses API with strict tool schemas. It adds an actual modeling stage before CAD export: Sol must call `blender_build`, the trusted background script creates furniture meshes from resolved component parameters, and real Cycles renders plus measured differences return to Sol as multimodal tool evidence. It does not import a pre-existing CAD GLB as its native modeling step. Existing `/generate-cad` behavior remains available with `modeling_engine="cad"` for API compatibility; the upload UI uses Engineering CAD.

The modeler supports panels/Bezier outlines, cylinders/ellipsoids, smooth lofts, round/oval/rectangular and varying-section swept rails, seat dishing and continuous surface grids. Ordered contact trims mirror the CAD component order. Each part must form one connected, closed positive-volume mesh. The server independently builds valid CadQuery solids from the same bounded declarative plan. No model-written Python, shell commands or arbitrary operator names execute in Blender. Blender uses background/factory-startup/disabled-autoexec mode and does not receive the OpenAI credential in its environment.

Sol reviews native renders and may call `blender_apply_changes` for sparse edits to flagged components. The first fitted cameras and foreground masks remain fixed across candidates. Corrections with usable masks must improve native rendered silhouette/boundary/landmark fit without regressing the CAD photo fit or sampled 3D agreement; surface-only edits may retain nonregressing projections while interior accuracy stays unmeasured. Supplied values and landmark component identities are preserved. Independent CAD failures, unchanged geometry and measured regressions reject an edit. Checkpoints keep valid candidates and review history. If an optional review/correction or final response fails after a valid build, the last valid model remains review-only with its error recorded.

The UI displays **Blender reconstruction** and **CAD model (review)** separately, photo overlays, candidate previews, component differences, and private GLB/`.blend`/model-package downloads. After export, the worker reopens the actual STEP, checks positive solids and placement against the frozen CAD, and samples surface differences to the native Blender model in both directions. The reported threshold defaults to 2 mm (`BLENDER_CAD_TOLERANCE_MM`); it is a visual reconstruction review threshold, not a factory tolerance. Bounds-based solid-ID ambiguity is flagged. The maximum *sampled* gap is not a mathematically proven whole-surface bound. Any disagreement stays visible as `CAD_MATCH_NEEDS_REVIEW` and does not approve manufacturing.

**Refine with Sol + Blender** uses an existing revision plus the masks/views chosen in the reference editor. **Rebuild saved plan in Blender** runs the native build and export checks locally without constructing an AI gateway or spending API credits. Parameter-only edits through the Blender path also rebuild deterministically. Final photorealistic CAD rendering remains a separate local Cycles stage. No Blender desktop window or embedded Blender UI is required.

```http
POST /api/projects/{project_id}/reconstruct-with-blender
{"image_ids":["REFERENCE_UUID"],"known_values":[],"auto_render":true}

POST /api/projects/{project_id}/reconstruct-with-blender
{"source_revision_id":"REVISION_UUID","modeling_views":[{"image_id":"REFERENCE_UUID","mask_png":"data:image/png;base64,...","mask_reviewed":true}],"auto_render":false}

POST /api/projects/{project_id}/rebuild-blender
{"source_revision_id":"REVISION_UUID"}
```

`blender_modeling` in workflow/project status contains selected artifacts, reviews and `cad_export_agreement`. Workflow `modeling_previews` exposes completed native candidate artifacts while a run is active. Downloads include the artifact identity in their URL so previews from different corrections cannot collide. Every file is ownership-checked and SHA-256 verified. `BLENDER_MODELING_PASSES=2` permits two correction rounds; the initial and final visual reviews are separate calls. Provider charges depend on actual input/output/reasoning usage and retries.

The native visual modeler currently rejects machining-feature plans instead of silently dropping holes/joints. Use the existing CAD operations workflow for those engineering details. Native Blender loft/sweep/surface tessellation can differ from the independent CAD reconstruction; those differences are measured and flagged. One photograph still leaves hidden geometry and physical scale uncertain. No exact-copy or Meshy-equivalent accuracy claim is made.

```powershell
# Existing plan -> native Blender (zero AI calls):
.venv\Scripts\python.exe scripts/build_blender_model.py plan.json reference.webp outputs/native-model
# Explicit actual Blender integration check (no OpenAI calls):
$env:BLENDER_MODELING_TESTS='1'
.venv\Scripts\python.exe -m pytest tests/test_blender_modeling.py -q
# Read-only audit of a completed native workflow:
.venv\Scripts\python.exe scripts/verify_blender_modeling.py WORKFLOW_UUID
```

### Local reference fitting

In `/studio`, open **3D workspace** or **Image to CAD** after creating CAD. Select reference views, prepare and review their foreground masks, paint corrections, and optionally click corresponding component landmarks. **Fit reference locally** aligns the perspective camera and proposes bounded part-position, Bezier-knot and loft-bend changes. It runs OpenCV/SciPy on the local machine and makes **zero OpenAI requests**. It works with AI disabled or an exhausted API balance.

The fitter compares all supplied views using frozen masks and fitted cameras. Mesh proxies propose changes; rebuilt CadQuery/OpenCascade solids decide acceptance. A candidate must improve the combined silhouette/boundary/landmark score without materially regressing a supplied view or landmark. Components referencing user or verified dimensions are frozen, including their resulting CAD geometry. Invalid candidates retain the previous plan. An unchanged result still records the measurements in a new review revision; existing revisions remain immutable. Geometry proposals are limited to 12 mm by default and do not establish engineered support contacts or joints.

The comparison shows camera alignment separately from geometry improvement, with reference/before/after overlays. Scores are **silhouette overlap**, not 3D reconstruction or manufacturing accuracy. Automatic masks can be wrong; review them. One view cannot constrain occluded surfaces, physical scale or sculpting inside the silhouette. Additional real photos and explicit landmark correspondences help constrain the fit.

```http
POST /api/projects/{project_id}/reference-mask
{"image_id":"REFERENCE_UUID","strokes":[]}

POST /api/projects/{project_id}/fit-reference
{"source_revision_id":"REVISION_UUID","reference_fit":{"views":[{"image_id":"REFERENCE_UUID","mask_png":"data:image/png;base64,...","mask_reviewed":true,"landmarks":[{"part_id":"F002","anchor":"path_start","image_point":[0.28,0.18]}]}]}}
```

The mask endpoint accepts normalized foreground/background strokes; masks must match the reference aspect ratio. Up to four views are accepted. The local action takes no measurement or feature edits; submit those separately through the existing parameter editor. Results and private hash-checked PNGs are available in `reference_fidelity.numeric_fit` and its `download_base`. Mask/geometry/image hashes bind checkpoints to the measured plan. Bounds on resolution, evaluations and candidate rebuilds limit work; the time budget is soft because an in-progress native CAD operation cannot be interrupted.

`PHOTO_NUMERIC_FIT_ENABLED=1` enables this numerical stage before optional AI visual corrections for new photo designs. Set it to `0` to skip automatic fitting; the explicit local action remains available. Parameter-only edits retain the saved shape definitions and skip refitting. AI corrections are measured using the same fitted cameras; interior-surface-only edits require nonregression and remain explicitly unmeasured by silhouette.

Photo plans now support a bounded `surface_grid.depths` for a continuous B-spline panel top with a flat bottom, and `profile_sweep.sweep_sections` for changing oval rail sections and connected tapered tips. These are actual CAD operations; they are not generated texture details. Every resulting part must remain a single valid positive-volume solid. Existing plans keep their original serialization and hashes when these optional fields are absent.

Offline fitting of an existing plan JSON also works without the server:

```powershell
.venv\Scripts\python.exe scripts/fit_reference.py plan.json reference.webp outputs/local-fit
```

Local mode needs no Redis. Its bounded AI/CAD queue and independent render queue drain on graceful shutdown. Use one API process. After a forced termination, stop workers and recover stale jobs/workflows with `app.cli recover-jobs` and `recover-agent-workflows`, then explicitly retry. Checkpoints preserve completed stages; a crash between an external response and checkpoint commit can still duplicate billing on explicit retry.

For PostgreSQL/Redis/Celery, use `compose.yaml` or separate workers with the same environment/storage:

```powershell
uv run --no-sync --env-file .env python -m celery -A app.jobs.celery_app worker --pool=solo --concurrency=1 -Q celery --loglevel=INFO
uv run --no-sync --env-file .env python -m celery -A app.jobs.celery_app worker --pool=solo --concurrency=1 -Q renders -n render --loglevel=INFO
```

Containers require an official Linux Blender installation mounted through `BLENDER_LINUX_DIRECTORY`; a Windows Blender executable cannot run in Linux containers. Compose exposes the same installation to API health checks and rendering workers. GPU access in containers needs separate host-driver/container-runtime setup; CPU is supported. This deployment has not been run here because the Docker engine is stopped.

## Verification and remaining limits

The ordinary test suite uses synthetic provider fixtures with real CAD, plus SDK request/response tests using mock HTTP. Separate live evidence is documented in [STUDIO_VERIFICATION.md](STUDIO_VERIFICATION.md). Tests do not assert live calls unless those calls actually occurred.

```powershell
.venv\Scripts\python.exe -m pytest -q --junitxml=docs/test-results.xml
$env:BLENDER_INTEGRATION_TESTS='1'
.venv\Scripts\python.exe -m pytest tests/test_blender_live.py -q
# These explicit demonstrations use real OpenAI requests and incur usage:
.venv\Scripts\python.exe scripts/demo_integrated_studio.py
.venv\Scripts\python.exe scripts/check_integrated_studio_browser.py
```

Browser QA optionally needs `uv pip install -r requirements-browser.txt` and the installed Chrome executable used by the checker. It performs read-only inspection of existing projects and does not generate paid images. The full live demo command is the operation that incurs new OpenAI usage; rerunning it resumes its recorded workflow IDs.

An exact physical copy cannot be recovered from an unscaled single photograph. Additional images generated by AI are concepts, not hidden-surface measurements. Numeric user constraints are retained, while physical verification remains separate. Photo designs, render similarity, procedural materials and valid solids do not establish manufacturability. Factory profiles, full tolerance/section drawings, qualified structural review and an approved manufacturing-package generator are still required for release. Sheet nesting, kerf optimization, wood movement calculations and CNC/CAM are not implemented. Rendering or AI failure can occur after CAD succeeds; resume/retry the failed workflow rather than discarding valid CAD.
