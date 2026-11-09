# Furniture templates and AI estimates

**Historical workflow:** the studio template picker has been removed. The current upload-first flow builds photo-specific component geometry; see [PHOTO_RECONSTRUCTION.md](PHOTO_RECONSTRUCTION.md). The catalog implementation and older APIs are retained for existing saved designs and exports. Instructions below describe that earlier flow.

Forma Studio includes **43 common furniture templates across nine families**, in addition to the original cabinet and decorative cabinet study. These are editable review models. The library does not reconstruct every possible furniture design or establish manufacturing dimensions from an unscaled photograph.

| Family | Starting templates |
| --- | --- |
| Chairs | Dining, wooden armchair, lounge, office study, accent, rocking study, child study |
| Stools | Bar, counter, footstool |
| Benches | Dining, garden |
| Tables | Dining, round dining, coffee, side, console, writing desk, office desk, standing desk study, dressing table |
| Storage | Bookcase, wardrobe, kitchen base/wall, sideboard, TV unit, shoe cabinet, bedside cabinet, chest of drawers, filing study, bathroom vanity |
| Beds | Single, double, queen, king, bunk study |
| Sofas | Two seat, three seat, loveseat, ottoman |
| Screens | Room divider |
| Racks | Coat rack |

Dimensions in `app/furniture_catalog.py` are common starting **estimates chosen for this application**, not certified ergonomic standards, measured furniture sizes or mattress/supplier specifications. Users can adjust them. Office mechanisms, desk adjustment mechanisms, bunk safety systems, rocker stability and upholstery construction are not engineered by these templates. Sofa cushion volumes use the selected study material as a proxy; they are not fabrication patterns or a foam/fabric BOM.

## Using the UI

Open **Design studio → Furniture templates**, choose a category and template, select a project, and edit dimensions/design details. **Generate template CAD** saves an estimated revision and queues its actual CAD package without calling OpenAI.

For photo interpretation, choose **From a reference**, select a furniture type or let AI choose, upload the reference, and add notes. Optional overall dimensions override the model's size estimates. The default **AI estimates + automatic review CAD** mode runs image observations, bounded parameter estimation, deterministic CAD/document generation, then an advisory review of the real generated geometry. It uses the existing configured vision/design/review models and backend key. No image-generation call is required.

An existing unsupported photograph workflow has a **Build with AI estimates** action. This reuses its hash-checked observation checkpoint and image references, avoiding another vision call. It creates a new estimated workflow; the earlier observation record remains available.

All outputs and individual components appear in the project's **Preview gallery**. The overview shows actual GLB geometry after CAD completion. **AI design estimates** shows the size/design rationale and assumptions. Full per-field attribution is in the downloadable workflow report. For revisions, use the studio to save another editable template model; files from earlier revisions remain available.

## Curves and construction

Chair, stool and bench parameters cover seat height/thickness, rectangular/rounded/elliptical/sculpted seats, leg width/taper/splay/curvature, back uprights and armrests. Back height follows overall height minus seat height. Back rails can be straight, bowed, wraparound or a simplified wave. Curved members use actual bounded Bezier sweep solids; curved legs use a three-section loft. The sculpted seat has an actual shallow spherical dish. Arm height controls the front ends of wraparound rails or separate arm rails.

Storage supports dividers, shelves, doors and drawer trays/sides/fronts. Drawer layouts use one compartment without doors or shelves. Bed frames include headboards, footboards, rails and slats; the bunk template is a two-deck spatial study without a qualified ladder or guard system. Detailed carvings, precise photo silhouettes, hidden joints, complex mechanisms, arbitrary surfaces and mixed drawer/door layouts remain outside these generators. AI must list important unrepresented features.

Template contacts are trimmed to flush mating faces. Curved profiles are bounded to the overall envelope when needed. These trims are recorded in package limitations and **do not implement engineered joinery**. Per-part blank values are local enclosing envelopes without qualified machining or bending allowances.

STEP keeps the actual B-rep solids in millimetres. GLB is derived from those solids with 0.25 mm tessellation in source units before conversion to metres/Y-up. Tessellating before scaling avoids slow OCCT meshing of scaled spline geometry. STL is a union mesh in millimetres; DXF/PDF drawings project actual CAD edges. Legacy filenames `cabinet.step`, `cabinet.glb` and `cabinet.stl` are retained for viewer/API compatibility across families; the manifest and specification identify the actual furniture template.

## API and provenance

`GET /api/v1/furniture/templates` returns the allowlisted catalog and defaults. Authentication/private project access uses the existing local or shared deployment policy.

```json
POST /api/v1/ai/estimate-furniture
{
  "project_id": "PROJECT_UUID",
  "image_ids": ["IMAGE_UUID"],
  "template_id": "armchair",
  "dimensions": {"width_mm": 650},
  "notes": "Estimate other sizes. Curved wraparound back and sculpted seat.",
  "idempotency_key": "chair-estimate-001"
}
```

`template_id`, images and supplied dimensions are optional when sufficient notes or a template are provided. Use `source_workflow_id` to reuse an observation checkpoint in the same project. Poll the returned `status_url`. The normal progression is `QUEUED → RUNNING → CAD_QUEUED → QUEUED → RUNNING → REVIEW_REQUIRED`. CAD or AI errors are explicit. A failed post-CAD advisory review does not remove a completed CAD package. An idempotency key prevents duplicate submissions; a different design needs a different key.

Attribution distinguishes `COMMON_DEFAULT_ESTIMATE`, `AI_ESTIMATED` and `USER_PROVIDED`. User-supplied sizes are preserved but are not automatically physically verified. A model can label a value as a common default only if it exactly matches the catalog. Estimated workflow inputs, model output checkpoints, revision specification and package hashes are checked. The estimated CAD validator is separate from the original designer-verified cabinet path. Estimated revisions cannot claim physical verification, verified stock or manufacturing approval.

The AI supplies strict structured parameters; it never supplies executable Python or arbitrary CAD commands. This follows OpenAI's [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses) interface. Geometry uses CadQuery's [documented solid and curve operations](https://cadquery.readthedocs.io/en/stable/classreference.html).

The optional local CLI `scripts/generate_furniture_estimate.py` submits one workflow or resumes an existing workflow with `--workflow-id`, polls it, verifies the archive's hashes and saves its outputs. It obtains no API key itself; the running server uses its configured key.

Physical measurements, stock, joins, hardware, strength, stability, ergonomics, clearances and applicable safety requirements still need qualified review. Every library package remains **REVIEW / not approved for manufacturing**.
