# Image to engineering CAD

New uploads in `/studio` use Engineering CAD through
`POST /api/projects/{id}/generate-all`. The API defaults to
`cad_source: "ENGINEERING_CAD"`, its only supported geometry source.
Existing project records and generated files remain stored as history.

New requests default to whole-furniture outputs and a reduced three-stage AI
workflow. The separate decomposition and code-critic AI calls described below
apply to legacy `PARTS` workflows. Whole-furniture models retain local solid and
dimension checks plus seven-view visual review, and deliver only one assembled
model and one drawing sheet in the supported formats. See
[whole-furniture outputs](WHOLE_FURNITURE_OUTPUTS.md).

The implementation adapts the dimension extractor, assembly decomposer, planner,
coder, code critic, repair specialist and visual reviewer from
[Agentic-AI-for-CadQuery](https://github.com/naturecodec-stack/Agentic-AI-for-CadQuery),
pinned to `702b90e0d26b630d7eba09f85d29bb446f6a1b96`.
`third_party/cadquery_agent/provenance.json` records source hashes and the upstream
README's declared license. Unmodified reference files live under `upstream/`.

This is an adapted backend integration, not the upstream CQ-Editor widget. Prompt
constants are extracted with Python AST as data. Upstream Python, its unrestricted
subprocess code executor, Gemini client, Qt interface and memory store are not run.
The app uses its existing configured OpenAI Responses client, strict schemas,
bounded deterministic CAD tools, audit records and resumable checkpoints.
Opt-in tool-input repair returns schema errors to the model without executing
invalid arguments. This uses a bounded four-response tool loop; other workflows
retain the existing behavior. Geometry failures are saved for the bounded repair
stage, rather than silently exporting a rejected model.
After a successful CAD tool call, the server retains the exact validated input
and checkpoints it immediately. It does not ask the model to transcribe the full
plan again. This avoids geometry drift and an unnecessary billed response.

## Pipeline

1. Estimate dimensions and retain explicit user inputs.
2. Decompose the photograph into separate component descriptions.
3. Generate a declarative parametric `PhotoDesign` and execute the registered
   `cad_generation` tool. The final plan must match an accepted tool input.
4. Check every component for a valid closed positive-volume solid. Check assembly
   contacts, disconnected groups, interfering volume and supplied dimensions.
   Supplied overall dimensions must match the actual CAD envelope within 0.01 mm;
   retaining the requested number in a parameter table alone is insufficient.
5. Critique the plan, render actual CAD views and compare them with the source
   photograph using seven views including rear and underside. Closed storage
   receives a missing-construction completion pass; see [360° models](FULL_360_MODELS.md).
   A missing render fails the workflow. A rejected visual review
   permits one bounded correction; remaining findings stay visible on review files.
6. Export individual STEP solids, reopen them and check B-Rep validity, closed
   shells, volume, envelope and bidirectional sampled surface distances.
7. Tessellate the reopened part STEP geometry for GLB/STL previews. Export the
   assembly, dimensioned DXF/PDF part sheets, schedules and complete package.
   build123d preserves native drawing curves; optional isolated FreeCAD TechDraw
   adds native dimensioned PDF/SVG pages and a reopened, validated `.FCStd`
   document. See [native drawing exports](NATIVE_CAD_DRAWINGS.md).

The public API accepts optional overall dimensions and item-specific part
dimensions, angles and counts. Studio analyses the image first, displays those
fields, and creates CAD only after the user selects **Create 360° CAD & files**.
The frozen image analysis is passed to the dimension and component planners.
Omitted dimensions are AI estimates or matched drawing values, and provided
values remain unverified physical measurements. See [custom dimensions](CUSTOM_DIMENSIONS.md). The
current input limits follow the existing photo-CAD schema. Engineering CAD does
not require Blender or an image-generation request.

For a local re-export, supply `engineering_reference_workflow_id` with the same
project and image. The API verifies and copies the saved plan/review checkpoints
into a new workflow and revision; previous files are immutable. This mode retains
the saved dimensions and does not initialize an AI client or use provider credits.
Generate a new design when changing measurements. Preview and envelope meshing
explicitly use OpenCascade's absolute-unit mode, rather than CadQuery's relative
deflection default.

## Outputs and verification

`cabinet.step` is the multi-part B-Rep assembly (the historical basename remains
for API compatibility). Each `part_Fxxx_REVIEW.step` contains one closed solid.
Each part also has GLB, STL, three-view DXF and PDF exports and an isolated PNG.
GLB preview tessellation tolerance is 0.1 mm. STEP round-trip verification checks
the full solid's validity, bounds and volume plus sampled surfaces; it is not an
exhaustive mathematical surface-equality proof.
Imported trimmed splines can receive conservative OCCT bounding boxes that
include unused portions of their underlying surfaces. Round-trip envelopes use
adaptive surface tessellation with requested absolute 0.02 mm deflection and a
0.005 mm round-trip envelope comparison tolerance, while
also recording the raw OCCT bound difference. Surface-distance and volume checks
remain independent. Per-part DXF dimensions use these adaptive surface envelopes.
Deflection values are requested meshing settings, not certified global error
bounds. Reports also record OpenCascade's resulting mesh-deflection estimates.

`geometry_verification.json` records part hashes and check results;
STEP volume comparison uses adaptive numerical integration with requested
relative tolerance `1e-10` for both solids. The acceptance limit remains
`max(0.001 mm3, source volume * 1e-7)`, alongside the independent envelope and
bidirectional surface checks. Default kernel volumes are retained as diagnostic
values; trimmed spline surfaces can make those coarser values disagree after
export even when the surfaces agree. This does not change physical accuracy or
manufacturing status.

`engineering_workflow.json` records upstream provenance, assumptions and reviews.
The UI exposes all parts, source dimensions, review findings and downloads.
Solid geometry checks do not verify photo identity, unseen construction, stock,
machining allowances/tolerances, joinery or structural strength. Manufacturing
approval remains blocked. A connected contact graph does not establish an
engineered joint or a load-bearing assembly.

## Curved-profile rounding and retries

Whole-furniture generation can locally repair an estimated all-edge fillet that
fails on an otherwise unchanged Bezier outline panel. It applies the same radius
to explicit sharp extrusion-profile corners, retains the outline, placement and
parameters, and rebuilds the entire assembly. Broad-face rims remain unrounded;
this change is recorded in assumptions, build warnings and `geometry_repairs`
and is included in the normal visual review. This is a change of edge selection,
not an exact substitute for rounding every edge. Supplied radius parameters,
existing explicit edge features and unsupported surfaces are not revised by this
repair. At most four such repairs are tried for a candidate, and every normal
solid, contact, dimension and export check still applies.

When retrying a failed generation, the latest hash-verified rejected plan is
validated locally first. If it now passes after a kernel or validator fix, no new
planning request is needed for that candidate. Visual review and its existing
bounded correction remain active. Local repair does not confer manufacturing
approval.
If a visual correction cannot produce valid geometry, the previous validated
candidate is exported as an explicitly rejected photo-match draft. A failed
correction cannot turn that draft into an approved design or discard its files.

## Tests

`tests/test_engineering_cad.py` uses synthetic model responses and real CadQuery
solids, exports, reopened STEP checks, private downloads and resumption. It also
checks that missing previews cannot silently pass visual review. Live tests use
`scripts/test_engineering_live.py`, which always creates a separate test project
and refuses the protected `chair11` project. Provider calls use the configured
backend model and may incur API charges.
