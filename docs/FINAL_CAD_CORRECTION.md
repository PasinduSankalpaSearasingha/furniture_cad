# One final comparison and correction before export

New generations and part edits submitted by the Studio request
`final_ai_correction: true`. After native CAD construction, validation and any
available local numerical fitting, the backend creates **one labelled comparison
sheet** containing every reference photo and seven actual CAD views: reference
camera, front, rear, left, right, top and bottom.

The configured review model receives this single image and the CAD parameter plan.
Its one structured response contains a feature review and an optional small
`EngineeringRepairPatch`. This replaces the separate visual-review/repair loop for
this mode. It does not add a second paid review after a correction.

The backend merges proposed changes into the existing declarative plan, preserves
supplied measurements, rebuilds the solids and repeats the normal contact,
interference, dimension and edit-scope checks. Valid corrections receive new local
comparison renders, then the normal STEP round-trip checks and drawing exports.
Invalid corrections retain the preceding valid model and explicit review findings.
The correction cannot reclassify components as detached furniture to evade the
original contact and dimension scopes.

The AI reviewed the **input** sheet. A changed model has not been seen by the AI;
its new sheet is for human visual review. Numerical and solid validation do not
establish exact photographic identity or manufacturing approval.

## Configuration and API compatibility

```dotenv
CAD_FINAL_CORRECTION_ENABLED=1
```

This setting is forwarded to API and worker services in Compose. Set it to `0`
to disable the new mode. Existing API clients and saved workflows preserve their
previous behavior unless they explicitly submit `"final_ai_correction": true`
to `/api/projects/{id}/generate-all` or `/api/projects/{id}/edit-cad`. The Studio
submits that flag automatically. Rebuild the frontend and restart the backend
after updating application code. A valid configured API key/model is required
for the live AI stage.

The existing AI spending allowance still applies. A declined budget, provider
failure or invalid response leaves a clearly unreviewed draft. Local offline
reconstruction scripts retain their zero-API behavior; they do not fabricate a
completed AI review.

## Request limits and recovery

The final request uses no tool loop, SDK submission retries or implicit model
fallback. Its allowance is checkpointed before submission. Completed final stages
and saved structured responses are reused. An interruption after reservation with
no saved structured response is **not automatically resubmitted**; the stage is
reported as interrupted and the valid draft is retained. This conservative rule
can leave a received background response unused, but avoids duplicating a paid
request when its outcome is uncertain.

The successful whole-furniture route still has dimension planning, CAD planning
and one final review/correction AI stage. Numerical optimization, CAD rebuilding,
new comparison renders and export remain local. Initial geometry planning may
still need its pre-existing bounded geometry repair attempts.

Reports live at `engineering_cad.workflow.final_ai_correction` and in the exported
`engineering_workflow.json`. They record source/selected plan hashes, input and
selected tile inventories/hashes, the proposed patch, acceptance or rejection,
response receipt and outstanding visual review. Private comparison files follow
the project deletion lifecycle. Frozen-plan re-export copies the saved report
without new AI or new comparison renders.

Implementation uses the existing Responses client with image input and a strict
Pydantic response schema. See the [official OpenAI documentation on structured
outputs](https://developers.openai.com/api/docs/guides/structured-outputs).

## Tests

```powershell
.venv\Scripts\python.exe -m pytest tests/test_final_correction.py -q
```

These tests use synthetic provider replies and real CAD/STEP exports. They cover
one-image/one-response submission, valid and rejected corrections, locked sizes,
provider failure, interrupted reservations, saved-response recovery, re-export,
server opt-out and private artifact cleanup. No paid requests are made.
