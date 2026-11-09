# Furniture library verification — 2026-10-19

The complete automated suite passed **168 tests; one optional live test skipped** (`test-results.xml`). The furniture-specific suite passed 58 tests (`furniture-test-results.xml`).

Headless Chrome checked the running local application at desktop 1440 × 1080 and mobile 390 × 844:

- All 43 template choices and nine categories load from the authenticated API.
- Choosing the wooden armchair loads its common dimensions and wraparound/sculpted profile controls.
- Choosing a chest of drawers loads its four-drawer layout.
- The original glazed cabinet form remains available.
- Photo mode defaults to estimated automatic review CAD, with optional supplied dimensions.
- The live chair project shows actual generated GLB geometry and per-field estimate attribution.
- Filtering the gallery to revision 2 exposes the current nine components. F006 opens as an isolated curved-rail 3D preview.
- No JavaScript errors or horizontal page overflow were found.

Local screenshots: `outputs/furniture-library-review/template_library_desktop.png`, `template_library_mobile.png`, `chair_actual_cad_desktop.png`, and `chair_rail_component.png`.

A live configured-model run reused the earlier chair observations and completed parameter estimation plus advisory CAD review. The model selected a 620 × 600 × 820 mm study, a sculpted rounded seat, 20 mm leg curvature, tapered/splayed legs, a continuous wraparound rail and three uprights. The model listed unrepresented construction details. The exported study contains nine solids/parts. Revision 2 corrects a post/rail contact and an STL degeneracy without another paid AI call.

Reopened export checks: STEP valid positive-volume components; nine named GLB components with metre extents approximately 0.620 × 0.820 × 0.600 (Y up); watertight STL with 620 × 600 × 820 mm extents. Archive CRC and manifest SHA-256 checks passed. Outputs are in `outputs/chair-ai-estimated-review` and the existing chair project's preview gallery.

The live example is an **estimated review model**. It is not evidence of physical measurement accuracy, exact photo reconstruction, structural qualification or manufacturing approval.
