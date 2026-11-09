# Image-specific custom dimensions

The default flow is **upload → analyse → review dimensions → generate CAD files**.
Uploading starts analysis without creating a CAD revision. Once analysis
finishes, the form shows the detected item and up to 24 relevant numeric fields.
The fields include overall width/depth/height plus dimensions for visible major
parts: for example tabletop thickness and leg width, chair seat/back dimensions,
or cabinet door, drawer-front and shelf dimensions.

Every input displays the available estimate or matching drawing value. Change
any measurements to the sizes you want; clearing a field allows estimation.
Untouched suggested values remain labelled estimates, rather than being
submitted as user measurements. **Generate CAD files** submits changed entries
and retains previously supplied measurements when editing a saved model. The planners estimate the remaining
dimensions from the image and supplied constraints. An exact matching sample
drawing supplies its known overall sizes; user entries override those sizes.
Placeholders never become user measurements.

The same form works for saved furniture: choose **Analyse photo again** to
discover relevant part fields. Saved analysis is restored after a page reload.
Analysis alone leaves existing CAD revisions and packages intact. Creating CAD
with changed dimensions produces a new immutable revision. The browser always
waits for an explicit generation request, including after reloading a tab that
used the older automatic workflow.

The API first uses `POST /api/projects/{id}/analyze` with `image_ids: [image_id]`.
Poll the returned workflow status until `SUCCEEDED`, then read
`result.matching_inputs`. To create CAD, pass its `analysis_workflow_id`, the same
`image_id`, and optional `known_values` to
`POST /api/projects/{id}/generate-all`. The API verifies source project/image,
completed state and frozen hashes before reusing analysis. Custom fields must
match the analysed item. Length keys end in `_mm`, angle keys in `_deg` and
count keys in `_count`; counts are whole numbers. The existing generation API
still supports direct submissions without a preceding analysis.

Supplied values must remain unchanged in the plan and drive geometry expressions
or actual component counts. All identified fields must be retained as named
values; blank ones remain estimates. Dimension sources appear in API results,
review tables and downloads. Solid and export checks still do not establish
physical measurements or manufacturing approval.

Verification on 23 October 2026: 103 regression tests passed across the custom
dimension, engineering CAD, storage completion, optional dimension, project
scope, sample matching, photo reconstruction and local workspace suites. Real
STEP checks verify that a supplied 42 mm top thickness produces a 42 mm solid,
while blank depth/height remain estimates. All-blank generation, wrong-image
analysis, checkpoint tampering, invalid values and idempotent submissions are
covered. Production frontend build and synthetic desktop/mobile browser checks
passed, including separate analysis and CAD actions, optional part fields,
angles/counts, reload recovery, empty submissions and private package previews.

Evidence: `outputs/custom-dimensions-regression.xml`,
`outputs/custom-dimensions-compatibility.xml`,
`outputs/custom-dimensions-browser/audit.json`. Browser checks can be rerun using
`scripts/check_custom_dimensions_browser.py`; add `--project PROJECT_ID` for
read-only checks of an already analysed project.

The existing `test1` sideboard was analysed with the configured backend model.
It now has 14 optional fields for overall, body, border, door, drawer-front,
base and corner dimensions. Live desktop/mobile checks confirmed those fields,
enabled CAD creation, blank optional inputs, no horizontal overflow and no
JavaScript errors. Analysis did not change its existing 37-part CAD job.
Live evidence is under `outputs/custom-dimensions-live`.
