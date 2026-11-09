# Edit generated furniture and rebuild its complete package

Open **Model & edit → Edit generated furniture** after generating a model.
Select components in the 3D view or in the part list. **Select all legs** selects
every named leg, making it easy to change a chair's four legs together. Choose
**Round / oval**, **Tapered**, **Curved**, or **Describe a change**, optionally add
details, then press **Apply change & rebuild all files**.

The edit uses the saved component plan as its source. Only the selected part
definitions can change. Existing parameter values, component IDs/count and the
actual overall envelope are retained. The AI returns a bounded component patch;
it does not execute generated Python. New profile parameters are estimates.
For other dimensions or a different component count, use the image/dimension
generation flow to create a new design.

The complete edited assembly is rebuilt and checked for closed solids, contacts
and interference. Actual unselected solids are compared by bounds, volume and
bidirectional solid differences, since contact trimming can otherwise change
an unselected part. A shape edit that changes no selected solid is rejected.
Critic and seven-view visual reviews receive the user's change request so they
do not mistakenly restore the original photographed leg shape.

The new revision regenerates the assembly and individual STEP/GLB/STL files,
seven assembly preview images, individual part previews, part and assembly
DXF/PDF drawings, the combined dimension sheet, schedules/BOM and the full ZIP.
All previews come from the newly reopened STEP geometry. The package records
the source workflow/job, selected IDs, requested shape and checks in
`engineering_workflow.json` and `geometry_verification.json`.

The previous model and downloads stay visible while the edit runs. The project
switches to the edited model and downloads only when the full package completes.
A failed edit or interrupted packaging keeps the saved revision available.
**Resume saved workflow** retries the edit; completed geometry and review stages
are reused. Editing does not modify or remove the source revision or archives.

The endpoint is `POST /api/projects/{id}/edit-cad`, with
`source_workflow_id`, `part_ids`, `style`, optional `instructions`, and a required
`idempotency_key`. Supported styles are `round`, `tapered`, `curved` and `custom`.
Custom edits require instructions. The API authenticates project access, checks
the exact source revision, manifests and frozen hashes, and rejects duplicate,
unknown or cross-project selections. Shape editing requires configured design
and review models; it reuses source dimension/assembly checkpoints instead of
performing another image analysis.

Edits remain review designs. Software checks do not verify structural strength,
manufacturing joints, hardware, tolerances or physical photo dimensions, and do
not grant manufacturing approval.

Verification uses `tests/test_engineering_edits.py`: real six-part chair CAD,
round and tapered leg exports, curved drawing geometry, complete archives,
unchanged source hashes, source/scope integrity, no-op and contact-trimming
rejection, idempotency and packaging retry. AI responses are synthetic. Browser
verification uses `scripts/check_engineering_edits_browser.py` with real CAD
fixture meshes and intercepted API responses, without paid requests or edits
to existing user projects.

Executed on 24 October 2026: the five edit tests and 32 CAD, custom-dimension,
storage-completion and project-scope regressions passed. The final round/tapered
chair export test was rerun after source-integrity checks were added and passed.
The structured-output schema, production frontend build, synthetic edit/retry
browser lifecycle and existing upload/download browser regression passed.
Evidence: `outputs/engineering-edit-tests.xml`, `engineering-edit-final.xml`,
`engineering-edit-regression.xml` and `outputs/engineering-edit-browser/audit.json`.
Use `scripts/check_engineering_edits_browser.py --project PROJECT_ID` for a
read-only check of the editor in a saved live project.
