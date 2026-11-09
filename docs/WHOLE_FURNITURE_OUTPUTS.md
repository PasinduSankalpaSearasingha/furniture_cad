# Whole-furniture outputs

New `/generate-all` requests default to `output_scope: "WHOLE_FURNITURE"`.
The download package contains only:

- `cabinet.step`: assembled editable solid CAD geometry. The historical basename
  remains for compatibility; this file can represent any furniture type.
- `cabinet.glb` and `cabinet.stl`: assembled mesh models for preview/interchange.
- `all_dimensions_REVIEW.dxf`, `.pdf`, `.svg`: one general arrangement sheet with
  top, front, side and isometric views, model dimensions and a title block.
- `furniture_REVIEW.FCStd` when the optional FreeCAD executor is configured and
  its assembly document passes validation. No individual part pages are made.

DXF retains native editable curves, text, layers and DIMENSION entities in
millimetres. STEP retains separate solids within one assembled model; imported
STEP solids do not retain parametric modelling history. DWG is not an export
format in this application.

The sheet is derived from the same verified CAD used by the model. It does not
contain a part-size table, cut list or individual part labels. Solid identifiers
remain internal so selecting and editing geometry still works.

## AI usage

The successful generation path has three paid stages:

1. Estimate dimensions, retaining supplied values and saved image analysis.
2. Plan the whole-furniture geometry with the deterministic CAD tool.
3. Visually review seven actual CAD views against the reference.

The Studio now requests one final comparison/correction response: references and
all seven CAD views are combined into one labelled image, and that same response
can return a small parameter/shape patch. Corrections are rebuilt and validated
locally, with no second paid visual review. See
[final CAD correction](FINAL_CAD_CORRECTION.md). Unflagged API clients retain the
legacy visual-review and bounded repair behavior below.

The separate AI assembly decomposition and code-critic stages are omitted.
Internal component descriptions are derived from the accepted model. Local
geometry checks still verify closed solids, contacts, interference and supplied
dimensions. Rejected geometry or visual review can trigger one bounded correction.
Photo analysis, when requested before generation, is an additional AI stage in
both workflows. Editing a whole-furniture model uses a model patch and visual
review; dimensions are reused from the source.

This removes two paid stages from the previous five-stage CAD path. It does not
establish a fixed percentage saving: tokens, images, model settings and retries
affect the bill. Merely selecting fewer export formats would not remove AI calls.
Tests use synthetic provider responses and real CAD exports; no live billing
comparison has been performed.

## Revision and compatibility behavior

Temporary individual STEP files support the existing solid round-trip checks and
optional native exporter. They are removed before the completed revision is
published. Individual model previews and drawing pages are never generated.
Verification reports and internal registries remain private revision artifacts;
the user-facing delivery ZIP and file list contain only drawing/model outputs.
The dimension-sheet endpoint copies the verified sheet into its private cache
instead of requiring separate part STEP artifacts.

Existing jobs without an output scope retain their saved workflow behavior.
Explicit `output_scope: "PARTS"` supports older integrations. A local saved-plan
re-export can produce whole-furniture outputs with no new AI gateway or requests.
Previous revisions and files remain unchanged.

Run the offline regression checks with:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_whole_furniture.py tests/test_engineering_cad.py tests/test_dimension_sheet.py tests/test_engineering_edits.py tests/test_techdraw.py
```
