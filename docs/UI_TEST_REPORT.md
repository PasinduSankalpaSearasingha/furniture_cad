# Forma Studio verification - 19 October 2026

The latest full Python suite passed **110 tests, 1 skipped, 0 failed** in 85.18 seconds, with 233 dependency warnings. The live OpenAI test remained disabled. [JUnit output](test-results.xml). The browser checks below record earlier workspace milestones; the later [image-study](IMAGE_STUDY.md) and [preview gallery](PREVIEW_GALLERY.md) checks exercised actual generated geometry, all output cards and individual components on desktop and mobile.

The frontend contains no login, password-entry or account-creation forms. The prepared local `.env` enables `LOCAL_WORKSPACE=1`; the UI opens automatically as **My workspace**. Model credentials remain on the backend.

## API verification

New local-workspace tests verify project/revision/material creation without a password, a persistent identity across reopened browser sessions, and isolation from projects owned by existing accounts. They reject remote clients, unexpected hosts, cross-site requests and requests from another localhost origin. Local mode can be disabled, preserving the authenticated API. The local designer cannot create administrator accounts or log in with a password as the generated local identity.

Earlier workspace tests continue to cover public assets, content security policy, private-file traversal rejection, project-scoped workflow discovery and commit-before-response behavior. Existing engineering, export, authentication, job, workflow and release checks remain green. JavaScript syntax passed `node --check` for `workspace.js`, `model-viewer.js` and `preview-gallery.js`.

## Browser verification

Real Chrome checks through Playwright passed:

1. Automatic workspace loading with no sign-in step or browser-stored bearer token.
2. Project creation, revision saving, real CAD generation, ten parts and private ZIP download without password entry.
3. Reference upload, mocked AI proposal, per-field measurement/evidence entry, designer verification and real CAD generation.
4. Material creation and settings with no account-creation or password controls.
5. Project persistence after browser reload without a sign-in step.
6. Password-free mobile navigation and design studio at 390 x 844 pixels.

Desktop checks used 1440 x 1080 pixels. No JavaScript errors or horizontal overflow were observed at these sizes. Prior responsive checks also covered widths of 320, 768 and 1024 pixels. The browser summary is retained in [UI-browser-results.json](UI-browser-results.json).

The earlier transaction race remains fixed: tokens, projects and other mutations commit before success is returned. Shared review logic commits after associated workflow changes are ready. FastAPI's request dependency lifecycle is described in the [official documentation](https://fastapi.tiangolo.com/advanced/advanced-dependencies/).

Browser checks used a separate SQLite database and artifact folder in the ignored test cache. AI used the offline fixture only. **No external OpenAI requests or charges occurred.** Measurement evidence was synthetic test data, not physical measurements or factory approval. No production release was made. The temporary preview server was stopped after verification.

Current screenshots show the password-free workspace with isolated test data:

- [Desktop overview](workspace-desktop.png).
- [Design studio](workspace-studio.png), with an illustrative dimension preview rather than a CAD tessellation.
- [Project workflow](workspace-project.png), with mocked AI and real CAD.
- [Phone overview](workspace-mobile.png).

The later [file browser verification](FILE_BROWSER_TEST_REPORT.md) uses the real local background executor without Redis. It confirms asynchronous AI/CAD submission, all 33 generated package files, interactive CAD geometry, DXF/PDF previews, workbook sheets, data reports, images, downloads and older revisions. Screenshots: [3D file browser](workspace-files.png), [drawing](workspace-drawing.png), [schedules](workspace-schedules.png) and [mobile](workspace-files-mobile.png). Its machine-readable results are [FILE_BROWSER_TEST_RESULTS.json](FILE_BROWSER_TEST_RESULTS.json).

These checks do not qualify live model quality, a Docker/Redis/Celery deployment or factory manufacture.
