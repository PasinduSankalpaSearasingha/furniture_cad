> Historical verification record: this report includes retired provider workflows.
> Current Fiske Studio generation and validation are documented in
> [ENGINEERING_CAD.md](ENGINEERING_CAD.md).

# Integrated studio verification — 19 October 2026

## Automated results

- Current full suite (20 October 2026): **268 passed, 4 opt-in tests skipped**, 417.80 seconds. See [test-results.xml](test-results.xml). This covers real CAD/export geometry, schemas, deterministic dimension propagation, profiles, joint volumes/fit, immutable revisions, access control, hashes, bounded tools and workflows, SDK HTTP request contracts, explicit failure handling and release refusal. Provider responses in ordinary tests are synthetic. Final multipart-upload, polling and resumption changes were then covered by the focused Neural4D suite: **9 passed, 1 skipped**, 29.51 seconds.
- Focused photo reconstruction, reference-fidelity and optional-dimension suites: **46 passed**, 226.21 seconds. Includes blank inputs through both APIs, parameter-only preservation, original estimate provenance, invalid correction rejection/recovery, retaining valid independent component changes, optional provider failure, private comparison previews, rounded loft/rail solids, shallow seat hollows, intermediate arithmetic limits and GLB normals. This focused run includes the final unused-estimate pruning change made after the full suite started.
- Actual installed Blender integration: **1 passed**, 22.76 seconds, with `BLENDER_INTEGRATION_TESTS=1`. Real synthetic CadQuery assembly → STEP/GLB with normals → Blender CPU Cycles → two valid PNGs and an editable `.blend`. Seven component names, parents, bounds and placements were checked. This test does not call OpenAI.
- React production build: passed, Vite 7.1.12. Bundle approximately 1.19 MB JavaScript / 333 kB gzip; Vite reports a large-chunk advisory because Three.js/R3F are included.
- `alembic check`: no new upgrade operations. Existing workflow/image/revision tables are reused; no schema migration was needed.
- Chrome desktop/mobile checks: passed on the compiled interface with actual private API data and real GLB/PNG artifacts. No JavaScript errors or page horizontal overflow. Verified component selection, visibility, exploded assembly, 12 chair components, five Blender views, reference image, blank optional dimensions, manufacturing table, and switching to the seven-component desk without mixing project reviews. No password controls exist in the React UI.

## Live OpenAI and Blender execution

These were actual calls, not mocked test responses. The configured model IDs were `gpt-6-astra` and `gpt-image-2.5-sunburst`.

### Text concept to real CAD and Cycles

Project: `b0565477-7b97-4d2f-a84b-9947035bd57d` — Astra + Sunburst + Blender · Oak desk.

1. Astra forced the native Responses image-generation tool. Sunburst returned a valid concept image, persisted and displayed in the concept gallery. Workflow: `f8762cb6-06a4-46bc-96b6-e3584419d15e`.
2. The concept was selected as design intent. Overall dimensions were supplied as 1200 × 600 × 750 mm.
3. Astra analyzed the image, called the strict `cad_generation` function, and the handler built real CadQuery/OpenCascade solids before accepting the final plan.
4. CAD job `ab12b26d-454b-4b66-87aa-d96bd87ee85c` exported seven named parts, STEP/GLB/STL and review manufacturing documents. Revision: `7229d09e-3937-4487-8209-98d7bea8ca86`.
5. Blender 5.2.2 imported the actual GLB, validated every component against the registry, and rendered front/side/top/isometric/hero PNGs with Cycles using **NVIDIA OptiX**. Editable scene, render report, hashes and archive were saved.
6. Astra compared the selected concept with the real render. It proposed appearance changes and preserved the supplied overall dimensions. It did not invent a numerical geometry correction unsupported by the evidence.
7. Workflow `eac5ea3b-1742-4f24-8162-8859542f251c` completed as `REVIEW_REQUIRED`. A final-result merge error found during this test was fixed; checkpoint reuse completed the workflow without repeating the paid concept/planning/review stages.

Independent STEP reopening found **7 valid positive solids** and bounds **1200.0 × 600.0000001 × 750.0000001 mm**. CAD package SHA-256: `d31a155914d4bdee44d559555236a5cca6faabb6ebe709c624ffbd480f97609e`.

The generated `.blend` was subsequently reopened by Blender. The audit found **7 separate components, 5 cameras and 3 area lights**, retained parents, node-based materials, metric units and the Cycles engine. It did not merely check that a filename existed.

### Natural-language modification

Workflow `32b175dd-01ab-4791-9f44-947a14610dc0` requested a 30 mm tabletop, retaining the 1200 × 600 × 750 mm envelope and existing components. Astra called the CAD tool again. The new CAD revision/job were:

- Revision `5b190b23-9899-4345-a03d-8c950e96f71a`.
- Job `86fb2749-9d60-4c96-805e-dd9e82e660e0`.

The tabletop changed from **25 to 30 mm**; leg heights and apron elevations changed accordingly. Independently reopened STEP retained seven valid solids and the same overall bounds. The first revision remained unchanged. Blender generated two preview renders and Astra performed a new visual review. CAD package SHA-256: `a7aa31c3405c6cd38a94706089c1d9c98626d3822099de8f9e397fe69e30f134`.

### Direct Sunburst Image API

Workflow `70c93b36-e2d2-49be-ae9a-a6508686f2a4` made a real `images.edit` request to show the desk concept in walnut. It returned a valid **1024 × 1024** `AI_CONCEPT_IMAGE`, privately linked to the same project. This appearance concept was not substituted for a geometry render or measurement evidence. Direct image generation and editing request shapes are also covered with the actual SDK using mock HTTP.

### Existing uploaded chair to Blender

The existing photo-derived chair revision `05168022-1e2f-4941-b1e7-7dca0e6fb6d7`, CAD job `15204465-473d-4397-ab22-bca141a2f635`, was rendered without a new design/image-generation call. Final render workflow `88ece553-45ce-480f-b8c0-cc07f7b01cfd` succeeded: **12 components**, all names/hierarchy/bounds/positions checked, **5 PNG views**, **960 × 540**, **32 samples**, **OptiX**, procedural walnut PBR and editable `.blend` output. A CPU smoke render and the separate CPU integration test also executed successfully.

Earlier chair render `a1e63a88-6b2b-4548-80f4-a287907dbd33` is preserved in history. Lighting and wood grain were refined for the final render; geometry and the original CAD revision were not altered by a material preview.

## Blank-dimension chair recovery

The previously failed photo workflow `213755a8-0564-498a-aab9-6459b31ba7c6` had no supplied dimensions. After the fix, retry reused its cached first photo plan, resolved unprovided overall estimates to the generated CAD envelope and completed CAD plus the final AI review as `REVIEW_REQUIRED`. No additional design request was needed. The previously attempted second plan is retained in the checkpoints.

Revision `c4320268-6df4-42c8-bdbf-4a6c05d168ed`, job `7a43b677-a68e-4e60-8f6c-979d5456708f`: independently reopened STEP contains **12 valid positive solids**, measuring **590.699292 × 540.200556 × 809.878568 mm**. Saved dimensions match the CAD envelope. Frozen input/plan/specification checks and all CAD-package hashes pass. Every parameter remains an unverified estimate. STEP, GLB, CAD preview, drawings, schedules and the review archive are available through the existing UI. Private read-only audit: `outputs/optional_dimensions_recovery_audit.json`.

## User WebP reference and bounded geometry correction

The user-selected test file was `C:\Users\HP\Desktop\three-leg-chair-1.webp` (1000 × 1000 pixels, 373,504 bytes; SHA-256 `16f1be9a72bcd3fcc5b2683c07f016c6ea90f1aa92cb5b2e92e9b0d2ce98c359`). The API accepted the WebP and deduplicated its normalized pixels to reference `45caa3c1-1ccf-4039-ae96-807e81c1d832` in project `c5429a31-1e0f-438f-9c6c-23018da0310a` (`chair11`). The WebP is pixel-identical to the earlier PNG reference. Component counts come from visible evidence, not the filename.

Workflow `4a950811-42d8-40f9-9d5c-21c38c13df31` used **no supplied dimensions** and reused the saved 16-component proposal. Actual Astra comparisons and two accepted field corrections changed the CAD seat hollows, seat contour, rail path/end treatment and member profiles before freezing revision `20fac329-6918-4a42-9b83-35343650eb66`, CAD job `23fe0400-5a2a-4a93-8ef2-02b99bd44acb`. The estimated depth was reconciled from the prior 528.797282 mm envelope to 513 mm without resizing the generated components.

The final comparison is **NEEDS_VISUAL_REVIEW**, not a verified match: sculpted seat/perimeter transitions, continuous rail shoulders/tapered tips and some member edge details still differ. Unsupported global fillets were explicitly deferred. The software retains valid geometric changes and displays actual comparison images with that review status. No exact reconstruction, true physical measurement or manufacturing approval is claimed.

Independent STEP reopening found **16 valid positive solids**, with an actual envelope of **619.851473 × 513.0000002 × 800.034822 mm**, within the existing estimate tolerance of the saved 620 × 513 × 800 mm values. Frozen revision/workflow/input/plan validation and CAD package hashes passed. STEP SHA-256: `9e969a1bd0329ceb24789f2ddeabc2a8ac2619b581ab45d69d5f257a5996b142`.

A local edit of this exact final plan changed `seat_thickness_mm` from **28 to 33 mm** with no provider calls. All other parameter values and all component definitions stayed identical; dependent seat/contact geometry changed and retained 16 valid solids. This check did not persist a new revision; the original saved revision remained unchanged. Separate API regression fixtures cover immutable revision creation through both public edit paths.

Blender workflow `6132f7de-1606-4983-a311-1c4635bcdfdf` succeeded on the actual exported GLB: **16 components**, **155,204 triangles**, two **960 × 540**, **32-sample** Cycles views using **OptiX**, procedural walnut and an editable `.blend`. Component names, parent relationships, dimensions/placement and render archive hashes passed. It used no image-generation or reasoning call. Browser inspection of this project passed for the two private comparison images, actual 16-part WebGL model, blank image-input fields, desktop/mobile layout, and absence of JavaScript errors/password controls. Saved output, render records, screenshots and `audit.json` are under private `outputs/three-leg-chair-test/`.

Earlier diagnostic attempts hit provider output limits and are retained as failures; they are not counted as successful geometry. Retrying this workflow reused valid saved stages. The correction validator was extended to handle bounded intermediate arithmetic and omitted simple aliases, and to reject geometry no-ops. The source image, private workflow records and exported evidence are excluded from the source ZIP.

## Stored evidence

Private workspace evidence is under `outputs/integrated-studio-demo/`: recorded workflow JSON, original and modified revision IDs, independent STEP/archive/hash audit, reopened-scene audit, CAD/render packages and chair render records. Run `scripts/verify_studio_demo.py` for the read-only independent CAD/archive audit.

Browser screenshots are under `outputs/integrated-studio-qa/`: `react_concepts_desktop.png`, `react_cad_desktop.png`, `react_blender_desktop.png`, and `react_blender_mobile.png`. User photographs, live outputs, databases and the real `.env` are excluded from the source ZIP.

## Local reference fitting, 20 October 2026

Added and tested OpenCV foreground extraction/corrective masks, SciPy perspective-camera fitting, bounded Bezier/loft/placement proposals, actual B-Rep acceptance checks, optional component landmarks, and fixed-camera multi-view nonregression. The explicit local action constructs no AI gateway. Added CAD support for continuous B-spline panel sculpting and connected rails with varying oval sections. Synthetic tests build these as valid positive single solids; they also verify supplied-dimension locking, unchanged source plans, actual-CAD rejection of misleading proxy proposals, ownership, artifact hashes and zero AI calls with AI disabled.

The complete ordinary suite passed **251 tests, 2 skipped** in **491.57 seconds**. The focused photo/optional/fidelity suite passed **46 tests** and the new fitting suite passed **9 tests**. The production Vite build passed. Live OpenAI tests remained skipped; no new paid request was made for this work.

The user's chair photo was fitted offline and through the real `/studio` local action in project `chair11`. Workflow `ea69375a-be39-4d54-889d-7c8e80eb1f18` saved review revision `61b880b1-16fe-47a3-94de-dfa2698f102b`, CAD job `6b3555ab-5879-4061-8be9-94164dfa6496`. At fitting resolution 160 pixels, silhouette IoU was **34.91%** at the initial camera, **49.88%** after camera fitting, and **54.52%** after validated geometry fitting using the same camera and mask. Geometry comparison loss fell from **0.421363** to **0.383246**. One full-displacement candidate failed actual CAD validation; the half-displacement candidate rebuilt successfully and improved the measured fit. The real workflow recorded **zero input/output tokens and zero generated images**. The foreground mask was inspected and marked reviewed through the UI. These measurements show a limited projection improvement, not an exact reconstruction.

Independent STEP reopening verified **16 valid positive solids**, with an actual envelope of **619.936393 × 509.5000002 × 800.026770 mm**, within estimate tolerance. The source revision stayed unchanged; frozen plan/specification/input, STEP, CAD package and fitting artifact checks passed. STEP SHA-256: `eb98d43ad714ffc4b535ed9e19445e2387f24a932ec0baebade9811a04457539`. Headless Chrome checks passed for mask painting, adding/removing landmarks, comparison previews, the actual 16-component WebGL model, mobile overflow and absence of password controls or JavaScript errors. Adding photographs to a saved photo design now stays local until the user explicitly requests AI generation/review.

Private artifacts, automatic mask, offline plan/report, backend logs and the independent audit are in `outputs/reference-fit-demo/`. Browser receipts, final workflow records and screenshots are in `outputs/reference-fit-browser/`. `scripts/verify_reference_fit.py WORKFLOW_ID` independently checks the frozen source/revision/input/plan hashes, STEP solids/envelope, CAD archive and fitting artifact hashes, and absence of AI invocations. Private photographs, outputs, database and `.env` remain excluded from the source ZIP.

## Native Sol and Blender modeling, 20 October 2026

Added a native modeling path pinned to `gpt-6.1-sol`. The model must call a bounded build tool; the trusted headless Blender script constructs geometry rather than importing CAD. Actual Cycles renders and objective measurements return to Sol through multimodal Responses function outputs. Measured component corrections retain supplied values, freeze comparison cameras/masks and reject photo/CAD regressions. The independent CadQuery path remains responsible for engineering solids; exported STEP is reopened and compared with native Blender surface samples. Native GLB, editable scene, overlays and comparison packages are exposed through private, hash-checked UI downloads.

The ordinary full suite passed **259 tests, 3 skipped**. After the final material-slot, numerical-distance and diagnostics fixes, the focused Blender-modeling/studio suite passed **29 tests, 1 skipped** in **235.44 seconds**. The opt-in actual Blender modeling test passed in **38.21 seconds** with CPU Cycles, a sculpted surface-grid seat, variable-section rail and loft members. It verifies positive closed components, camera projection error below 0.25 pixels and exported per-part material colors after Boolean operations. This test makes no OpenAI requests. The final React production build passed (607 modules, 17.81 seconds; Three.js bundle-size advisory only).

The real local rebuild was queued through the browser for `chair11`, workflow `b68b09bf-5948-40ce-841b-360b37fb9760`, revision `1a8576a3-6205-430f-bb23-e2d42a8be24e`, CAD job `39231131-1374-465b-b3b7-61573e3b92d3`. It created **16 native closed components**, **89,666 triangles**, two real Cycles views using **OptiX**, an editable `.blend` and GLB. Camera projection audit error was **0.000178 pixels**. The workflow used **zero AI calls/tokens/images**; source revision `61b880b1-16fe-47a3-94de-dfa2698f102b` stayed unchanged. Independently reopened STEP contained **16 valid positive solids**, with bounds **619.936393 x 509.5000002 x 800.026770 mm**. Archive CRCs, member hashes, frozen revision/input/plan validation and STEP/native correspondence hashes passed.

The measured native silhouette IoU was **49.44%** with an automatically estimated, unreviewed mask. The maximum bidirectional sampled native/STEP surface gap was **6.32457 mm**, above the **2 mm** review threshold; the UI correctly displays `CAD_MATCH_NEEDS_REVIEW`. This is a limited projection measurement and sampled geometry comparison, not a full-surface bound or an accurate physical-copy claim. Chrome checks passed for real image loading, native/CAD WebGL switching, component lists, mobile overflow, no password form and no JavaScript errors. A material-slot issue observed in these images was subsequently fixed and checked with actual Blender; a fresh local review revision preserves the earlier evidence.

Private receipts, workflow records, independent audits and backend logs are in `outputs/sol-blender-live/`; browser evidence is in `outputs/sol-blender-browser/`. `scripts/verify_blender_modeling.py WORKFLOW_ID` checks frozen revision/source integrity, all native artifact and package hashes, positive exported STEP solids, dimensions and AI invocation records without new provider calls.

The first two live Sol attempts returned HTTP 429 `insufficient_quota` / `credit_balance_exhausted`. After the user added credit and the backend configuration was reloaded, the third attempt completed real Sol requests and exported a review revision, `f6dae2e9-1739-4634-9d84-0e3ccf8a0caf`, job `0ad4bc3a-15bd-4883-b0df-48988abe4f14`. Recorded completed usage was **155,305 input / 28,888 output tokens**. This run was **not an accurate reconstruction**: silhouette IoU was only **15.39%**, and the maximum sampled native/STEP surface difference was **277.44 mm**. Its attempted correction exhausted the bounded tool budget; the existing model was retained with `AI_TOOL_LIMIT` and review blockers. Independently reopened STEP contains 16 valid positive solids, but this does not establish shape fidelity. The saved audit records these failures explicitly; no exact-match claim is made.

## Neural4D integration and actual chair generation, 20 October 2026

The user's Neural4D credential was configured only in the backend `.env`, and the local studio now selects Neural4D for photo-to-3D. No password or API-key entry form was added. The client handles matting, one-model generation, bounded status polling, safe CDN downloads, self-contained GLB validation and actual local Blender preparation. Paid submission intents/receipts prevent automatic duplicate generation after uncertain responses; known task IDs and downloads survive retries. A separate optional Sol/native-Blender action uses the generated mesh views and reports a whole-model surface comparison against reconstructed CAD and the reopened STEP. Mesh objects are explicitly unsegmented visual assets, not certified manufactured parts.

Live workflow **`74671c59-cb2b-4160-b90a-b6ece285058f`** completed as `REVIEW_REQUIRED` in project `chair11`, using the same normalized user photograph (SHA-256 `1d3ee3b11d2fc72fbefd300b39dbd31e0a252acfa884774751c68a8f95cb4deb`). The audit found **one accepted generation receipt**, provider model `bfdb3524-4868-468b-9727-329edaa9ce3d`, **one mesh**, **999,695 triangles**, two embedded texture images, three actual **512 x 512 OptiX Cycles views**, original/scaled GLB and an editable `.blend`. With no supplied dimensions, the unverified display envelope is **658.593774 x 578.125 x 800.000012 mm**, using an estimated 800 mm longest span. Source GLB SHA-256: `0012bab6d00a4564a9d2a17887846e60ab52ebed4ee8cf28204253a37eed655e`. Model package SHA-256: `48dd6f11199e1ae196d0865fbf52b6052249fb4049ce5dec8d1cb4e6a17ed9b6`.

The provider balance was **40 before / 10 after** this run, an observed difference of **30 Neural4D credits**. No USD invoice total is inferred. The initial full-size matting upload timed out; a smaller image-preparation upload recovered a real receipt with no generation submitted. The live API then rejected the older PDF's JSON `fileKey` request with HTTP 400, explicitly requiring an `image` file. After implementing the multipart contract, one generation was accepted. A transient read-only status-query disconnect was resumed using the same task ID. The original provider GLB, normalized model, all render hashes, mesh bounds, frozen inputs/source image and ZIP CRC/member hashes passed the independent audit. These events and account snapshots remain private under `outputs/neural4d-live/`.

The provider/studio contract tests cover current multipart and legacy JSON request shapes, credential redaction, rejecting external GLB resources/private download addresses, no bearer forwarding to CDN hosts, project ownership, idempotency, unknown-submission refusal, repeated free-poll recovery without billing again, actual STEP comparison with the generated reference, and fixed alignment across shape comparisons. The ordinary full suite passed **268 tests, 4 skipped**. The actual Blender provider-preparation integration passed **1 test** in **23.13 seconds** using CPU Cycles and verified 700 mm width / 900 mm height overrides while retaining the source GLB hash. The final React build passed. Full live Sol reconstruction from the new Neural4D reference was not run; the CAD bridge uses synthetic provider/model responses with actual CAD in its tests. No exact photo match or manufacturing approval is established.

Chrome desktop/mobile checks passed for the actual Neural4D model, all three rendered previews, private download links, service selection, mobile overflow, and absence of password controls or JavaScript errors. Browser evidence is in `outputs/neural4d-browser/`. The React GLB loader also needs local `blob:` image-buffer fetches: the studio policy now permits these while external connections remain excluded, and the viewer preserves original emissive materials when components are not selected.

## Limits of this evidence

- These examples verify service connectivity and specific geometry/scene behavior, not unrestricted reconstruction accuracy or all furniture categories.
- The chair still differs from its single reference photograph in some sculpted details. An exact copy or unseen construction cannot be established from that photo.
- All CAD/manufacturing packages remain review-only with explicit engineering blockers. Valid solids, matching envelopes and visual similarity are not manufacturing certification.
- Materials use procedural PBR graphs, not scanned licensed texture sets. Local HDRI support is implemented but was not live-tested because no HDRI asset was configured.
- Forced GPU-device failure fallback is implemented; the observed live paths were successful OptiX rendering and explicitly selected CPU rendering. Subprocess failure/timeouts and missing Blender are covered by synthetic failure tests.
- Container/PostgreSQL/Redis/Celery deployment was not run: the Docker Linux engine is stopped. Compose configuration and separate render-queue wiring are provided; a compatible Linux Blender installation and GPU runtime need deployment validation.
- Real image generation and planning incur usage. No billing total is asserted without configured rates/invoice reconciliation. Completed provider usage is recorded; crash/timeout uncertainty remains explicit.
- The implementation includes controlled component features and limited paired engineering joint geometry. Sheet nesting, optimized kerf layouts, complete tolerance/section shop drawings, structural calculations and approved CNC/CAM remain outstanding as documented in [INTEGRATED_STUDIO.md](INTEGRATED_STUDIO.md).
# Automatic complete chair delivery (2026-10-20)

The actual chair reference completed the full `IMAGE_TO_CAD` workflow `476ea67f-2a43-48d8-946b-0db19f7697a4`, reusing Neural4D workflow `74671c59-cb2b-4160-b90a-b6ece285058f`. No second Neural4D model was submitted. Sol geometry trials exposed unsupported loft fillets and invalid contact/Blender rebuilds; valid geometry was subsequently retained and the CAD worker and final rendering completed.

- Revision: `406261b6-2b31-4747-acdb-10165debef23`; CAD job: `032c21b3-1ed0-4dad-bda4-ae2eb91e7733`.
- Complete archive: **66 files**, **127,082,161 bytes**; SHA-256 `cf15a8a6ab8e392bd53e82f13d966b2cb616c32c58a6f2c7dd7c79d39448ecc6`.
- Original/scaled Neural4D models, STEP/GLB/STL, DXF/PDF drawings, schedules, assembly/validation documents, native Blender reconstruction, CAD-rendered Blender scene, PNG renders, accuracy report and delivery manifest are included.
- Independent local audit reopened **16 valid positive-volume STEP solids**, verified archive CRC/member hashes, manifest/source bindings, actual PNG renders and the no-manufacturing-approval flags. The actual HTTP complete-package download matched the archive's SHA-256.
- File generation completed as `REVIEW_REQUIRED`, progress 100. The design remains `VALIDATION_FAILED` due review findings; output completion is not manufacturing approval.
- **Accuracy limitations are measured:** original-photo silhouette IoU **54%** for the sampled comparison view; largest sampled native-Blender-to-STEP surface gap **10.33 mm**. Generated-Neural4D-to-STEP samples have mean gap **33.06 mm**, 95th percentile **117.40 mm**, maximum **157.91 mm** after estimated scale/yaw/translation alignment. This is not an exact reconstruction. These samples do not establish a full-surface bound, physical scale or engineering tolerances.

Private evidence is in `outputs/image-to-cad-live/{receipt,workflow,audit}.json`; those files and generated artifacts are excluded from the source deliverable. The separate automated tests use synthetic provider/AI/Blender fixtures and real CAD exports. Browser contract checks simulate upload/generation APIs and verify automatic dispatch, fixed measurements, stage progress, complete downloads and CSV/DXF previews without starting paid jobs.

## Neural4D model visibility

The 3D workspace now places its interactive viewport above reports and galleries. Direct links preserve `project`, `tab` and `model` query parameters, including after reload. Auto selection prefers an existing Neural4D model in the selected project. Demand-rendered camera/material changes explicitly invalidate the canvas; the generated image is visible while interactive geometry loads. The classic workspace shows Neural4D thumbnails and a project-specific link to the interactive model.

Read-only browser verification in `scripts/check_model_visibility.py` opened the actual `chair11` link without manually choosing a project, verified **19,089 brown chair pixels** in the ready WebGL frame, checked camera redraw and reload persistence, and followed the classic workspace link. Desktop/mobile checks passed with no JavaScript errors or paid generations. Evidence is in `outputs/model-visibility-browser/`, excluded from the source archive. The workspace/local-access regression checks passed: **14 tests**.
