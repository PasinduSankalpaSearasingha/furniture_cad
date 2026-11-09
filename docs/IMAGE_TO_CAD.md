# Image analysis, custom dimensions and CAD delivery

Fiske Studio at `/studio` opens on Image to CAD. Upload a PNG, JPG or WebP
photograph; large images are resized automatically. Upload starts image analysis
and discovers editable overall and part dimensions for that item. Enter known
values or leave any fields blank, then press **Create 360° CAD & files**.
This starts the `IMAGE_TO_CAD` workflow. See [custom dimensions](CUSTOM_DIMENSIONS.md).

1. Analyze the photograph and retain supplied dimensions.
2. Plan separate parametric furniture components with the configured backend AI.
3. Build and check closed solids locally with CadQuery.
4. Review the plan and compare actual CAD previews with the reference photograph.
5. Export and reopen STEP parts, then generate matching GLB/STL previews,
   dimensioned DXF/PDF review drawings, schedules and geometry reports.
6. Package the files into `furniture_complete_package.zip` with a hash-bound manifest.

`POST /api/projects/{project_id}/generate-all` accepts `image_id`, optional
`known_values`, `analysis_workflow_id`, `notes`, `render_options`, `idempotency_key`, and
`cad_source: "ENGINEERING_CAD"` (the default and only supported source).
A new generation requires backend AI credentials and vision, design and review
models. See `.env.example` for configuration.

To export a completed engineering plan locally, pass
`engineering_reference_workflow_id` with the same project and image. Saved dimensions
and verified plan/review checkpoints are retained. This does not initialize an AI
client. Use a new generation to change dimensions.

Retries resume completed stages and retain existing CAD files. Every package is
bound to its workflow, revision and job; unrelated revisions cannot replace the
displayed assembly. Older project files remain on disk and in revision history.

Missing measurements are estimates. Closed-solid checks and successful generation
do not verify photographic identity, hidden construction, physical dimensions,
joinery or manufacturing suitability. Release still requires engineering review.

`tests/test_engineering_cad.py` exercises real CAD/STEP exports with synthetic AI
responses, private downloads, checkpoints and local re-export.
`tests/test_image_to_cad.py` checks that removed provider routes, inputs and queued
workflows cannot submit requests. `scripts/check_engineering_upload_browser.py`
checks upload, progress, downloads and mobile layout with intercepted API calls.
