# Fiske Studio — Photo to CAD

Upload a furniture photo, download CAD files, and edit the generated model.
Both `/` and `/studio` open the same focused workspace.

1. **Upload & review measurements.** Image analysis identifies the furniture and displays estimated dimensions relevant to that photo. Change any sizes you want, then select **Generate CAD files**. Untouched suggestions remain estimates; your changes become target dimensions. CAD waits for this review step.
2. **Model & edit.** Inspect the actual exported geometry from all sides, select parts and describe a change or choose round, tapered or curved shapes. Applying a change creates a new revision and rebuilds the model and whole-furniture drawings together. A pending or failed edit keeps the saved version available.
3. **CAD files.** Download the whole-furniture ZIP: an assembled STEP/STL/GLB model and one dimensioned general arrangement sheet in editable DXF, PDF and SVG. The sheet has top, front, side and isometric views. File access checks project ownership and artifact hashes.

AI plans and reviews the furniture. Local CadQuery builds and validates the solids; build123d preserves native curves in the drawings. Optional isolated FreeCAD TechDraw export adds associative dimensions and an editable `.FCStd` document. See [native CAD drawings](docs/NATIVE_CAD_DRAWINGS.md) for setup and verification. Blender, image generation and an extra rendering worker are not required for this flow. Existing backend APIs and saved project data remain compatible.

A photo does not establish exact dimensions, hidden joinery, material strength or machining tolerances. Missing values are labelled estimates. Generated files need engineering review before manufacture and are not machine-ready CAM.

## Run locally on Windows

Use Python 3.11 and Node 22. From the project directory:

```powershell
uv sync --frozen
.venv\Scripts\python.exe -m alembic upgrade head
cd frontend
npm ci
npm run build
cd ..
```

Copy `.env.example` to `.env` if needed; keep an existing configured `.env`.
Set `LOCAL_WORKSPACE=1`, `JOB_EXECUTOR=local`, `AI_ENABLED=1`, a valid backend
`OPENAI_API_KEY` and `OPENAI_REASONING_MODEL`. Never put the API key in frontend
code or a `VITE_` variable. Then start the local service:

```powershell
uv run --no-sync --env-file .env python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000/**. Local access requires a direct same-origin
loopback connection and opens without a password. One API process runs a bounded
background queue; Redis and Celery are unnecessary locally. Do not terminate it
while a workflow is running. Saved designs, revisions and artifacts persist in
`data/furniture.db` and `outputs` by default.

To remove a saved project, select it under **Saved designs** and choose
**Delete design**. The confirmation names the design and permanently removes
its versions, reference photos and generated files. Only its owner or an
administrator can delete it; running generation or CAD jobs must finish first.

If photo analysis reports HTTP 401, replace the rejected backend key and restart
the backend. Retrying with the same rejected key cannot fix authentication.
Readiness reports configuration only; account access is checked by the worker.
To check model access without generating anything or printing the key, run:

```powershell
uv run --no-sync --env-file .env python scripts/check_generation_access.py
```

This metadata request does not verify generation billing or quota.

## Shared deployment

```bash
cp .env.example .env
# Set POSTGRES_PASSWORD, AI_ENABLED=1, OPENAI_API_KEY and the chosen model.
docker compose up --build -d --wait
docker compose exec api python -m app.cli create-admin --username admin
```

The Docker build compiles the frontend from the locked npm dependencies.
Compose starts PostgreSQL, Redis, migrations, API and a CAD worker with private
persistent artifacts. It disables password-free local access. The UI supports
sign-in and sign-out using HttpOnly, SameSite=Strict cookies, with Secure cookies
on HTTPS. Existing bearer-token APIs remain supported. Cookies also authenticate
3D meshes, images, drawing previews and native file downloads.

The API binds to localhost. Put it behind a TLS reverse proxy with login/upload
rate limits, configure trusted forwarding for the HTTPS scheme, and back up the
database and artifacts together. The container stack has not been executed in
this Windows environment. See [deployment and operation](docs/PRODUCTION_WORKSPACE.md)
for the verified scope and remaining deployment checks.

For an EC2 host using a reverse proxy on the host, keep
`APP_BIND_ADDRESS=127.0.0.1`. If you intentionally publish port 8000 directly,
set `APP_BIND_ADDRESS=0.0.0.0` and restrict TCP/8000 in the EC2 security group
to trusted clients; HTTPS through a reverse proxy is still recommended.

Health endpoints: `/health/live` and `/health/ready`. API documentation: `/docs`.
`/health/ready` checks the database schema; it does not certify provider access or
worker availability. Compose separately checks Redis and its worker.

## Verification

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m alembic check
node scripts/check_frontend_submissions.mjs
.venv\Scripts\python.exe scripts/check_production_workspace_browser.py
.venv\Scripts\python.exe scripts/check_engineering_edits_browser.py
```

The browser checks intercept API calls and use synthetic provider responses;
the edit check displays real CAD fixture meshes. They do not make paid requests
or modify saved projects. Live provider tests are opt-in. Rebuild the frontend
after source changes. CI builds it before testing; Docker builds it in a separate
Node stage.

Generation is a paid AI operation. CAD exports and downloads are local. Editing
incurs new AI usage. A failed workflow can resume completed stages through its
retry endpoint. Photo analysis always stops at the measurement form; reloading
the browser restores the analysis without submitting a CAD job. Select
**Generate CAD files** after reviewing the suggested sizes.
Manual analysis, generation and edit submissions also retain their request key
after an uncertain response, so retrying unchanged inputs reuses the accepted
workflow. Acknowledged requests and changed inputs can create new versions.

New generation defaults to `output_scope: "WHOLE_FURNITURE"`. Its successful CAD
path uses three AI stages (dimensions, model planning, visual review), compared
with five in the legacy parts workflow. Component descriptions are retained from
the accepted model locally, and solid/dimension checks replace a separate AI code
critic. Fewer AI stages reduce paid work; token usage and correction attempts
determine the actual cost, so the stage count is not a billing percentage.
Individual STEP sources are temporary verification inputs. No individual part
drawings, models or manufacturing schedules are delivered. Legacy stored jobs
and the explicit `PARTS` API scope remain supported. See
[whole-furniture outputs](docs/WHOLE_FURNITURE_OUTPUTS.md).

New whole-furniture generations use a USD 2.00 AI allowance, including linked
photo analysis (`AI_FURNITURE_BUDGET_USD`). Corrections request small patches,
and a spending stop retains valid CAD as a clearly labelled draft. Saved
responses and local exports do not generate additional AI calls. See
[AI spending controls](docs/FURNITURE_AI_COST_CONTROL.md).

Upload up to six angles of one furniture item, choose a primary photo and label
the views. New whole-furniture drawings include circular R dimensions in editable
DXF, PDF and SVG; freeform curves retain variable curvature. See
[multiple photos and curve radii](docs/MULTIPLE_PHOTOS_AND_RADII.md).

OpenAI Responses use `background=True` by default. The backend saves each response
ID and polls for completion; resuming after an interrupted status check retrieves
that response. Configure `OPENAI_BACKGROUND_MODE` and
`OPENAI_POLL_INTERVAL_SECONDS` in the backend environment. See
[background response behavior and recovery](docs/PRODUCTION_WORKSPACE.md#openai-background-responses).

## Engineering details

- [Item-specific dimensions](docs/CUSTOM_DIMENSIONS.md)
- [Editing generated furniture](docs/EDIT_GENERATED_FURNITURE.md)
- [Matching the uploaded furniture photo](docs/PHOTO_MATCHING.md)
- [Local native CAD geometry optimization](docs/CAD_OPTIMIZER.md)
- [One final AI comparison and correction](docs/FINAL_CAD_CORRECTION.md)
- [Available drafts when credit or repair limits stop corrections](docs/AVAILABLE_OUTPUT.md)
- [Dense CAD previews and saved-plan recovery](docs/PREVIEW_RECOVERY.md)
- [Responsive local UI with isolated CAD workers](docs/LOCAL_CAD_WORKERS.md)
- [Engineering CAD and export checks](docs/ENGINEERING_CAD.md)
- [Furniture dimension scopes and generation reliability](docs/GENERATION_RELIABILITY.md)
- [All-angle cabinet construction](docs/FULL_360_MODELS.md)
- [Combined dimension drawing](docs/ALL_DIMENSIONS_SHEET.md)
- [Sample drawing matches](docs/SAMPLE_DIMENSIONS.md)

Dimensions use millimetres. STEP preserves solid components; GLB uses metres and
Y-up coordinates. STL uses millimetre coordinates and has no assembly hierarchy.
Revision-bound downloads never silently point at a different model. Previous
revisions and engineering audit history remain stored.

Stop workers before recovering abandoned jobs with `python -m app.cli recover-jobs
--stale-hours 24`, then explicitly retry affected workflows. Back up PostgreSQL and
the artifact volume at the same checkpoint and exercise a restore in an isolated
environment. `scripts/package_project.py` creates a source bundle excluding
credentials, databases, environments and caches.


# Editable furniture CAD and six-sided drawings

The photo-to-CAD workflow exports a native assembly and separate parts, editable
FreeCAD documents, CAD source and parameter files, six orthographic views per
assembly/part, SVG/DXF drawings, PDFs, and review BOM/envelopes. Use the
**Model & edit** workspace to inspect parts, change dimensions and regenerate a
local revision.

Recognition uses **at most two `gpt-6.1-sol` generation requests per job**.
Submission intents persist across restarts; failed or uncertain submissions
consume their slot. Numerical optimization, drawing/export generation and
manual revisions make no AI generation requests. Engineering generation combines
dimension extraction and initial CAD planning into one request and keeps local
validation.

Install application dependencies only in `.venv`. Editable CAD export discovers FreeCAD 1.1 on Windows or uses `FREECAD_PYTHON`. FreeCAD runs separately from CadQuery to avoid loading incompatible OCCT libraries in one process. Plain untrimmed axis-aligned boxes have native Sketcher/Pad histories. Complex and contact-trimmed parts use the included `furniture_cad_proxy.py` implementation and the authoritative project `.venv` for regeneration. Keep the project available when editing those documents; instructions are included in each package.

PartSAM and Point-SAM are optional administrator-installed adapters. Their weights are not downloaded automatically. The license guard requires explicit review of code, checkpoints and dependencies. The default fallback combines Sol photo/part hypotheses with local geometry checks, or uses conservative geometry alone when AI is disabled. Semantic confidence is reported as uncalibrated.

All outputs require manufacturing review. CAD envelope measurements and supplied dimensions do not verify materials, joints, stock allowances, tolerances or structural strength. See [the implementation and validation report](docs/EDITABLE_CAD_UPGRADE_REPORT.md).
