# Local execution and generated-file verification — 18 October 2026

The reported `POST /api/v1/agents/run-workflow` HTTP 503 was traced to `AGENT_QUEUE_UNAVAILABLE`: the local configuration selected Celery while no Redis broker was listening. The local workspace now selects `JOB_EXECUTOR=local`, using a bounded serial background queue inside the API process. Submission returns HTTP 202 while tasks run, and CAD can enqueue its post-CAD specialist review. Existing failed workflows remain available for explicit retry without re-uploading images. Celery remains available for shared deployments.

At this milestone the full Python suite passed **93 tests, 1 skipped, 0 failed** in 67.82 seconds. The skipped case is the opt-in live OpenAI smoke test. Later complete-suite results and current [JUnit output](test-results.xml) are described in [TEST_REPORT.md](TEST_REPORT.md). Ten added tests cover asynchronous local execution, capacity, broker-failure recovery, real CAD and post-CAD review, complete manifest discovery, worksheet/document previews, access restrictions and hash tampering.

Chrome browser checks used Playwright, an isolated SQLite database, real CadQuery geometry/exports and the offline specialist fixture. They passed at desktop 1440 × 1080 and mobile 390 × 844, with no JavaScript errors or page overflow:

1. CAD and AI workflow submissions returned 202 without Redis. The API remained responsive during background execution.
2. All 33 generated package files were listed, including the STEP-derived PNG, manifest and ZIP. Actual GLB geometry rendered and rotated using pointer input.
3. DXF drawings displayed as SVG, and the generated PDF opened inside the file browser.
4. Excel worksheet selection displayed the real Glass and Cutlist rows; CSV tables, JSON reports and Markdown text were readable.
5. PNG previews, archive inventories and private downloads worked.
6. Older revision files remained accessible through revision and category filters.
7. The complete AI proposal JSON was visible before measurement verification or CAD generation.
8. The mobile file browser and 3D viewer worked without password prompts.

Machine-readable evidence: [FILE_BROWSER_TEST_RESULTS.json](FILE_BROWSER_TEST_RESULTS.json). Screenshots: [3D model](workspace-files.png), [drawing](workspace-drawing.png), [workbook](workspace-schedules.png) and [mobile](workspace-files-mobile.png).

Private previews use the existing project access and artifact hash checks. Table/text previews are bounded and full files remain downloadable. STEP/STL entries display the associated generated GLB; their original source files are downloadable. The viewer supports this application's untextured generated GLB files and falls back to the CAD-derived PNG when WebGL is unavailable.

No external OpenAI requests or charges occurred during verification. AI responses and measurement evidence were synthetic test fixtures; the geometry and document exports were real. These checks do not establish live model availability or manufacturing qualification. Redis/Celery/PostgreSQL container execution remains unverified. The local executor drains on graceful shutdown but does not persist queued work across forced termination; use the documented recovery commands followed by explicit retry.

Implementation references: Python's [ThreadPoolExecutor](https://docs.python.org/3.11/library/concurrent.futures.html) and ezdxf's [drawing export](https://ezdxf.readthedocs.io/en/stable/tutorials/image_export.html).
