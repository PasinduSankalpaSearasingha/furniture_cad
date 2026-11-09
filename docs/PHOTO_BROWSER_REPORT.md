# Photo-first UI verification — 2026-10-19

The complete suite passed **195 tests with one optional live test skipped**. See `test-results.xml`. The focused reconstruction suite covers 26 cases, including all eight geometry primitives and a regression for a silent STL kernel export failure.

Desktop (1440 × 1080) and mobile (390 × 844) browser checks confirmed:

- No template, furniture-family or generation-method selector in the design studio.
- An upload automatically starts analysis and reveals relevant optional inputs.
- Input values start blank; estimated suggestions appear as placeholders.
- A supplied width of 700 is sent as the only fixed constraint when all other fields are blank.
- A blank form sends no fixed values and requests a fully estimated reconstruction.
- Uploaded reference photos remain visible beside the form.
- The generation request uses the photo reconstruction API and reuses the analysis workflow.
- No JavaScript errors or horizontal mobile overflow.

These repeated UI checks mocked AI analysis/generation responses to avoid duplicate paid calls. Screenshots are in `outputs/photo-studio-review`.

A separate live run used the existing uploaded chair, no known dimensions, and the configured GPT-6.1 Sol model. It completed a 12-component photo-derived model with custom seat outline, an oval-section curved horseshoe rail, broad/narrow uprights, under-seat members and four individual legs. All named values remain `AI_ESTIMATED`. CAD validation found no geometric ERROR findings; measurement, stock, joint and engineering blockers remain.

The initial geometry request timed out. An explicit resume reused the completed photo analysis and used lower reasoning effort with a longer bounded timeout. The CAD kernel subsequently failed to write a union STL. The export fix retained the actual component meshes and a `COMPONENT_SHELLS` report. A new CAD job reused the immutable plan without another design-model request. STEP preserves 12 valid solids; GLB preserves their named geometry and colours. The STL component shells do not establish a watertight printable union.

The completed result was then checked in the running UI: actual 3D geometry, the original-image comparison, revision filtering, all 12 component cards and an isolated curved-member preview on desktop and mobile. Screenshots and hash-verified downloads are in `outputs/chair-photo-reconstruction`. The project is `fa9d0cff-0966-447b-9f09-d960f5fcc66a`, revision 3; the completed job is `15204465-473d-4397-ab22-bca141a2f635`.

The advisory visual comparison explicitly reports remaining seat-sculpting, rail-end, leg-profile and under-seat integration differences. **No exact reconstruction, physical measurement accuracy, manufacturing approval or printable solid union is claimed.**
