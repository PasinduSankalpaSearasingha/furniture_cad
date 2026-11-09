# Current photo workflow

The studio now uses an image-first flow with automatically matched optional inputs, predicted missing dimensions and photo-specific component geometry. Template selection has been removed. See [PHOTO_RECONSTRUCTION.md](PHOTO_RECONSTRUCTION.md). Earlier workflow descriptions below remain for historical saved designs.

# Forma Studio workspace

The application serves its interface at `/`. It uses local HTML, CSS, JavaScript and SVG illustrations, with no frontend build step, CDN or external font requests. Docker already copies these files with `app/`.

Start the API after installing dependencies and running migrations:

```powershell
uv run --no-sync --env-file .env python -m uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000/. The prepared `.env` uses `JOB_EXECUTOR=local`, which runs AI and CAD asynchronously without Redis or a separate worker. Celery remains available for explicit shared deployments. Docker Compose also reads `.env` automatically. A plain Python process does not automatically import `.env`.

The prepared local `.env` sets `LOCAL_WORKSPACE=1`. The UI opens automatically as **My workspace**, without login, password or account-creation forms. A single persistent local designer identity owns newly created projects. This mode accepts direct same-origin loopback requests only; it rejects remote clients, unexpected hosts and cross-origin requests. It does not take over private projects owned by existing API accounts. Shared/Docker API deployments retain authentication with local mode disabled by default.

## Workspace behavior

- **Overview:** project and revision counts, recent projects, activity and workflow orientation. When local access is disabled, the preview shows explicitly labeled sample projects rather than real jobs or generated CAD.
- **Projects:** search by name, filter statuses, open project details and create projects directly. Completed projects use their actual CAD-derived thumbnail.
- **Design studio:** upload up to six reference images and submit a collaborative AI workflow, or enter cabinet dimensions and save an unverified revision. The dimension illustration responds to input and supports zoom.
- **Project detail:** workflow stages and automatic status updates, measured specification verification for designers, CAD submission/retry, engineering findings, review decisions, parts, revision history, images and private downloads.
- **Project overview:** completed jobs display the actual interactive GLB assembly, with front/side/top views, zoom and fit controls. Estimated image studies retain an estimated-dimensions badge. The studio and unfinished designs retain their dimension illustrations.
- **Preview gallery:** visual cards for all generated outputs, uploaded references and AI reports across revisions. CAD cards use the actual CAD-derived image, drawings use their DXF geometry, and tables/reports show real content snippets. Open any card for its full preview. PDF cards show the associated front elevation and explicitly link to the complete inline PDF. Thumbnails load lazily with at most three concurrent requests.
- **Components:** the gallery's Components collection lists the actual parts registry, with a drawing thumbnail for each item. Opening a component isolates its named geometry from the generated GLB and displays material/dimensions. The component download button downloads its individual DXF drawing; the original STEP assembly remains available in All outputs. Parts & materials also links to the component collection.
- **Files & downloads:** the complete package manifest for every completed revision, source images, AI results and presentations. Search files and filter by revision/category. STEP, GLB and STL selections display the actual GLB assembly in a local WebGL viewer with orbit/zoom; a CAD-derived PNG is the fallback. DXFs render from their real drawing entities, PDFs open inline, XLSX sheets can be switched, and CSV/JSON/Markdown/ZIP content is viewable. Previews are bounded to 200 data rows/40 columns or 1 MB of text; downloads contain complete files.
- **Materials:** list real catalog profiles; designers and administrators can add unapproved, traceable profiles.
- **Manufacturing:** revision overview and links to the associated project packages.
- **Activity:** workflow and designer review events for accessible projects.
- **Settings:** local workspace status and safe backend model configuration. There are no password or account-creation controls. Model credentials are never returned to the browser.

All project data comes from the existing API with the local workspace identity and its project access rules. `GET /api/v1/workspace/access` initializes the local identity without returning a secret. `GET /api/v1/projects/{id}/workflows` returns bounded workflow summaries. Downloads use API fetches; images use private object URLs. The local UI does not request credentials or store bearer tokens. The backend retains its existing authentication endpoints for optional shared API deployments.

AI requests occur only after the user submits a generation form. AI configuration is reported from the running backend, rather than inferred from model names or a local file. No production release bypass is provided. Approval stays disabled when blocking findings remain, and the backend independently enforces all review permissions.

The design studio's cabinet illustrations show proportions and dimension envelopes, rather than machining details. Completed project overviews, component inspection and the file browser display actual generated CAD tessellations. STEP, GLB, STL, PNG, PDF and schedule downloads come from completed jobs and retain REVIEW status. Presentation images remain separate from CAD. Private file listings, `/api/v1/jobs/{id}/components` and previews apply the same project access and package hash checks as downloads. A project can link directly to its gallery with `/#project/{id}/previews`.

If a previous generation request failed with HTTP 503 and `AGENT_QUEUE_UNAVAILABLE`, refresh the app and use **Retry workflow** on that project. Its recorded input images/checkpoints are reused. The local runner returns HTTP 202 while work continues and keeps the UI available for progress polling. No failed AI request is automatically replayed during this configuration change.

The layout adapts to desktop, tablet and phone sizes, with mobile navigation, keyboard focus indicators, accessible form labels, modal dialogs and reduced-motion support.


Furniture library: see [FURNITURE_LIBRARY.md](FURNITURE_LIBRARY.md) for 43 common templates, editable sizes, curved details and the automatic estimated photo-to-review-CAD path. The original measured cabinet workflow is still available in the reference generation method selector.
