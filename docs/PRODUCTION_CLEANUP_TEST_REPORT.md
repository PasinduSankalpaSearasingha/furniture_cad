# Photo-to-CAD cleanup verification — 24 October 2026

## Executed checks

| Check | Result |
| --- | --- |
| Workspace, local access and browser sessions | 20 passed |
| Private API/downloads, CAD edits, custom dimensions, image-to-CAD and model scope | 40 passed |
| Solid CAD, 360° construction, optional dimensions, sample matches, dimension sheets, photo reconstruction, provider contracts, agent workflows, local execution, Blender compatibility and artifact integrity | 140 passed, 1 skipped |
| Production React build, Windows | Passed, Vite 7.3.7 |
| Production React build, Linux Docker frontend stage | Passed, Vite 7.3.7 |
| npm dependency audit, updated Linux install | 0 reported vulnerabilities |
| Compose configuration | Passed |
| Alembic schema check | No new upgrade operations |
| Lost-response workflow submissions, including reload and blocked browser storage | Passed |

The 200 passing tests above are the selected regression suites, not a claim that
every repository test ran. The 3D viewer remains a large chunk, loaded on demand.
The focused submission check is `node scripts/check_frontend_submissions.mjs`.

## Browser checks

`scripts/check_production_workspace_browser.py` passed after the final build:
automatic photo upload; continuation after reload; one CAD request; upload in an
existing design; resuming an already accepted workflow; custom dimensions;
fractional-count rejection; blank fields omitted; native ZIP downloads; file
preview; missing configuration; failed provider authentication; shared sign-in
and mobile sign-out; saved designs; New design; mobile layout. No JavaScript
errors were recorded.

`scripts/check_engineering_edits_browser.py` passed with real CAD fixture meshes
and synthetic API responses: select all four chair legs; scoped shape request;
saved model/downloads during an edit and after failed packaging; retry; atomic
switch to new mesh and downloads; updated drawings and individual part links;
desktop and mobile layouts.

`scripts/check_custom_dimensions_browser.py` passed for seven item-specific
fields, custom lengths/counts/angles, all-blank generation and reload recovery.

A separate read-only browser check opened existing project
`8e491fea-ec13-4b33-890d-31c79d10b641`, verified its editor and mobile layout, and
confirmed job `599a7bce-7646-4290-b952-cbb759590899` remained unchanged. It made
no mutation requests. The protected chair project was not modified.

Browser output is under `outputs/production-workspace-browser`,
`outputs/engineering-edit-browser`, `outputs/custom-dimensions-browser` and
`outputs/engineering-edit-live`. Test XML is in
`outputs/production-cleanup-tests.xml` and
`outputs/production-compatibility-tests.xml`.

## Live-service limits

No paid generation or edit was made. Metadata-only model access checks returned
HTTP 401 `invalid_api_key`. The configured backend `.env` was unchanged at the
time of the recheck; the user chose to update it later. A valid backend key and
service restart are required before real photo generation can succeed.

The full Docker CAD-runtime build did not finish. The first attempt hit a Python
download timeout. A retry used the official pinned uv binary and completed the
OS-library install, but the large CAD dependency downloads remained stalled and
retrying; that build was stopped. The Linux frontend stage built successfully
with the updated lock and reported zero npm vulnerabilities. The full CAD image
must be built and run on the deployment host. Production deployment is not
certified by the local tests. HTTPS, proxy configuration,
operational limits and backup/restore validation remain deployment tasks.
