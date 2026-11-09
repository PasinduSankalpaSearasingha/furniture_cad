# Estimated decorative cabinet study — 19 October 2026

The supplied `images.jpg` shows a tall, two-section display cabinet, two apparent front glazed doors, upper side glazing, stepped plinth/waist/cornice, pilasters and crossed decorative motifs. The visible product label is TDC1261. No physical scale is available. The user explicitly selected an estimated review model rather than supplying measured dimensions.

The generated study uses **650 mm overall width × 430 mm depth × 1900 mm height**, a lower-section top at 640 mm, 18 mm carcass panels, 8 mm back, 55 × 24 mm door-frame sections and 6 mm visual glass. Every dimension is an assumption. Wood species, actual material build-up and glazing safety are unknown.

`app/display_cad.py` creates 55 valid solids: a lower closed carcass; upper posts, rails and back; two front glazed doors; two upper side panes; stepped mouldings; raised pilaster details; and seven fused X/diamond ornament silhouettes. The model is a controlled approximation of the reference. Original carved moulding profiles, hardware, door swing, attachment details, glazing retention and machining joinery are not reproduced. Local blank envelopes are explicitly unverified, especially for the nonrectangular ornaments.

`CabinetSpec.display_layout` selects this review template. The absent optional extension is excluded from serialization, preserving basic specification hash inputs. Invalid openings and claimed physical verification are rejected. The AI verification route rejects substitution of this study for the basic `cabinet.v1` proposal. Existing engineering blockers and release refusal continue to apply.

The actual local job completed successfully, with **79 private package files** and the uploaded reference visible in Forma Studio. Outputs include component STEP in millimetres, GLB in metres/Y-up, a watertight union STL, a CAD-derived PNG, 58 review DXFs, a 56-page PDF, workbook/CSV/JSON schedules and a SHA-256 manifest. The application project is named **TDC1261 - estimated photo review**. Runtime artifacts and the source photograph stay under ignored private output storage and are excluded from the source archive.

The full test suite passed **108 tests, 1 skipped, 0 failed**, with 233 dependency warnings, in 53.98 seconds. [JUnit evidence](test-results.xml). Ten new cases verify solids, exact overall size and local coordinates, no positive-volume part intersections, invalid proportions, unverified provenance, legacy serialization, reopened exports, manifest hashes and the AI template guard.

Chrome checks passed at 1440 × 1080 and 390 × 844. The browser displayed actual GLB geometry and 80 file-browser entries (79 generated files plus the reference). The estimated-provenance report opened, and workbook worksheets showed four glass rows and 51 wood rows. No JavaScript errors or mobile horizontal overflow were observed. Runtime `ui_verification.json`, `model_view.png` and `workspace_preview.png` are retained alongside the downloaded study.

The deterministic study and all associated checks made **zero external OpenAI requests**. They do not establish manufacturing qualification, physical dimensions, supplier specifications or exact fidelity to hidden construction. The earlier AI analysis remains a separate custom-design review record.

To reproduce with the local API running:

```powershell
python -m scripts.generate_image_study --image C:\path\reference.jpg --output outputs/my-image-study
```

The script explicitly uses these study proportions; it does not infer dimensions from another image. It creates a new private project, uploads the reference, saves an estimated revision, submits background CAD, then downloads and hash-checks the package. Its output folder must not already exist.
