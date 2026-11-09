# Output and component previews — 19 October 2026

Completed projects now show their actual generated GLB assembly on the overview, replacing the generic dimension illustration. Project cards use the actual STEP-derived PNG. View controls provide 3D, front, left, right and top orientations, zoom and fit. Estimated studies retain their provenance badge.

**Preview gallery** presents every available generated file, source/reference image, presentation and AI report as a visual card. Models appear first. Search and category/revision filters apply to the complete collection. Existing file-list previews and downloads remain available. A direct gallery link is `/#project/{project_id}/previews`.

The **Components** collection comes from the hash-checked parts registry. Each card shows its actual local DXF drawing, material, dimensions and revision. Opening it displays only the selected named component from the generated GLB, normalized for viewing. The original assembly coordinates are retained in the downloadable CAD. Component downloads provide the individual DXF drawing; no separate part STEP export is implied.

CAD thumbnails use the actual CAD-derived PNG. DXF thumbnails render real entities, images use their original private assets, and CSV/XLSX/JSON/Markdown/ZIP thumbnails show actual content snippets. PDF cards explicitly show the associated front-elevation preview and open the complete PDF inline. Table/text previews remain bounded; downloads remain complete. Thumbnails load lazily with at most three concurrent requests. Opening a card uses a larger preview dialog with existing workbook sheets, document tables and downloads. Closing the dialog disposes its 3D resources.

`GET /api/v1/jobs/{job_id}/components` applies the same project access and full package/hash checks as file downloads. It returns registry metadata and links to existing private artifacts. No CAD regeneration, dependency download, external rendering service or OpenAI request is needed to preview an existing package. Production verification/release gates are unchanged.

The full suite passed **110 tests, 1 skipped, 0 failed**, with 233 dependency warnings, in 85.18 seconds. [JUnit evidence](test-results.xml). Two new API cases verify preview metadata, exact component discovery, access restrictions and tamper rejection. All three frontend scripts passed Node syntax checks.

Real Chrome checks passed at **1440 × 1080** and **390 × 844** against the existing local study. They loaded all **80 output thumbnails** (79 package files and the source image), displayed **55 component cards**, tested actual CAD view controls, isolated ornament/glass/panel geometry, downloaded the correct component DXF, opened every supported preview format, checked category/revision filters and retained the original file browser. There were no JavaScript errors or mobile horizontal overflow. [Machine-readable results](PREVIEW_GALLERY_TEST_RESULTS.json).

Screenshots remain beside the private runtime study: `overview_actual_cad.png`, `preview_gallery.png`, `component_preview.png` and `preview_gallery_mobile.png`. Private output storage and the original photograph are excluded from the source archive. No live AI calls, charges, physical measurements or manufacturing approval occurred during these checks.
