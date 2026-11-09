# Furniture photo to 3D

The studio is now image-first. It has no furniture template selector. Upload the furniture, enter only the values you know, then generate its 3D reconstruction. Existing saved designs and their downloads remain readable.

## What the user sees

1. Upload one or more furniture photos. The studio can create a project automatically, or save to an existing project under **Save location**.
2. AI analyses the visible furniture and supplies relevant optional input fields. A chair might show seat height, seat thickness, leg width and arm height. A cabinet might show shelf count or glass thickness. Overall width, depth and height are always offered.
3. Fields start **blank**. Their placeholders contain estimated suggestions rather than supplied measurements. Enter any subset; leave all blank if no sizes are known. Notes and a known material are optional.
4. **Generate 3D from photo** uses the supplied values as constraints, predicts missing values from them and the photo, and constructs the furniture's component geometry.
5. Open the project to inspect actual 3D geometry, the uploaded reference, the advisory comparison, individual parts, drawings and CAD downloads.

The configured backend vision/design/review models and key are retained. There is no model or credential entry form in the studio. Browser verification uses mocked AI responses for repeated UI checks, avoiding duplicate paid generations.

## How the generator follows the photo

The current path does not choose an entry from the furniture catalog. The AI returns a bounded component plan: each part has its photographed evidence, material, colour, local shape, dimensions, placement and rotation. It can describe individual curved/tapered legs, seat outlines, shaped panels, oval or rectangular curved rails, supports, aprons and soft volumes.

Supported geometry includes boxes with optional corner rounding, elliptical cylinders, ellipsoids, arbitrary closed panel contours, multi-section lofts and round/oval/rectangular section sweeps. Cubic Bezier chains supply custom contours and 3D paths. A shallow dish can be cut into a seat. Colour is inferred separately per component, including metal, plastic and upholstery categories.

Named parameters drive coordinate expressions. A supplied width can appear as `width_mm / 2` in positions and as `width_mm` in a panel contour, for example. The evaluator supports only bounded arithmetic and parameter names; it never executes model-generated Python. The server refuses changed or ignored supplied inputs. Actual CAD envelopes are checked against the supplied overall dimensions; missing sizes remain `AI_ESTIMATED`. Count inputs must match the labelled component inventory.

The plan receives a CAD preflight. One bounded correction attempt may fix invalid solids or conflicting geometry while preserving supplied values. Valid plans become immutable estimated revisions and queued CAD jobs. The same source/checkpoint/specification integrity checks run again in the worker. Failures remain explicit; the system does not silently substitute a stock model.

After generation, the advisory review receives both the reference images and an image rendered from the **actual generated STEP**. It is instructed to report missing parts and differences in outlines, curves, proportions and arrangement. All deterministic manufacturing blockers are retained.

## API

`POST /api/v1/ai/analyze-furniture-photo` accepts a private project and uploaded image IDs. Poll its workflow status to obtain `vision`, `summary` and `matching_inputs`.

```json
{
  "project_id": "PROJECT_UUID",
  "image_ids": ["IMAGE_UUID"],
  "notes": "Keep the curved arms and the photographed leg shape."
}
```

`POST /api/v1/ai/reconstruct-furniture` accepts the image references or a completed analysis workflow and any known values:

```json
{
  "project_id": "PROJECT_UUID",
  "source_workflow_id": "ANALYSIS_WORKFLOW_UUID",
  "known_values": [
    {"key": "width_mm", "value": 700},
    {"key": "seat_height_mm", "value": 450}
  ],
  "notes": "Follow the uploaded design closely.",
  "idempotency_key": "photo-design-001"
}
```

For a fully estimated model, send `"known_values": []` or omit the field. `template_id` is not accepted by the reconstruction request. A direct reconstruction request with image IDs can perform its own analysis. Reusing an analysis workflow avoids repeating its vision call. Sources must belong to the same accessible project and their stored hashes must match.

The photo plan uses the existing OpenAI [Structured Outputs interface](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses). Its stage budget defaults to `AI_PHOTO_MAX_OUTPUT_TOKENS=12000` and `AI_PHOTO_TIMEOUT_SECONDS=300`, independently of the smaller general response budget. Both are bounded in server settings. Photo planning uses configurable `AI_PHOTO_REASONING_EFFORT=low`; the configured [GPT-6.1 Sol model supports this level](https://developers.openai.com/api/docs/models/gpt-6.1-sol). Leave the effort variable empty for a model without reasoning controls. There is no automatic repeat after a timeout that may already have been billed.

The CLI `scripts/generate_photo_reconstruction.py` can submit or resume one local workflow and save its hash-verified package. `--known width_mm=700` supplies a constraint; omitting `--known` uses estimates. `--workflow-id` resumes polling without another paid submission.

## Fidelity and engineering limits

A single photo does not determine exact scale, back surfaces, hidden joints or all depth relationships. The application produces a photo-guided approximation and reports remaining differences; it never marks `exact_reconstruction_verified` true. Additional views and measured dimensions improve the constraints but do not constitute automatic physical or manufacturing verification.

Intricate carving, fabric seams, mechanisms, textures and complex freeform surfaces may require further modelling. Contacts may be trimmed for visual fit. Blank values are enclosing envelopes without qualified allowances. STEP is the solid geometry source; GLB/STL are derived review meshes; drawings and schedules remain review documents. All models still require dimensional, joint, material, structural and safety review before manufacture.


## Verification

The complete suite passes **195 tests, with one optional live test skipped** (test-results.xml). Twenty-six focused photo tests cover optional/supplied inputs, image-specific geometry, all eight shape primitives, arithmetic sandboxing, private image requirements, source hash validation and actual STEP generation. Desktop/mobile browser checks verify automatic relevant fields, blank defaults, partial input submission and no template controls; AI responses in those repeated browser checks are mocked. Existing saved-design/export tests also pass.


The live chair check produced a photo-derived 12-part model and a completed private CAD package. An initial provider timeout was explicitly retried using the saved analysis and bounded lower-effort reasoning. The CAD kernel then failed to emit a union STL; export now retains the actual component meshes with an explicit COMPONENT_SHELLS report when that happens. STEP/GLB remain available and no new design-model call was required to repair the export. This STL fallback does not certify a watertight printable union. The original photo and generated CAD are compared in the UI, with differences reported honestly.
