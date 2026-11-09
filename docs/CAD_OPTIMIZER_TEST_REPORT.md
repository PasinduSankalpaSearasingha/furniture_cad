# Native CAD optimizer verification

Verified locally on Windows with Python 3.11. No paid AI requests were made.

- **33 focused tests passed** in 46.54 seconds. See
  [focused JUnit results](cad-optimizer-test-results.xml).
- **124 regression tests passed** in 598.66 seconds. See
  [regression JUnit results](cad-optimizer-regression-results.xml).
- These runs overlap in workflow tests; their counts are not a unique combined total.

Run the focused suite:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_cad_optimizer.py tests/test_cad_optimizer_workflow.py -q
```

The regression run covered engineering CAD, whole-furniture delivery, photo matching,
engineering edits, local repairs, existing reference fitting, photo reconstruction,
photo fidelity, panel/sweep frames, curve dimensions, multiple photos, furniture
items and custom dimensions. Existing dependency deprecation warnings remain.

Synthetic fixtures verify recovery of dimensions, leg positions and native cubic
curves, fixed measurements, bounds, independent camera calibration, multiview
handling, native HLR and ray visibility, caching, invalid-candidate rollback,
iteration/evaluation/time limits and real STEP export agreement. API tests verify
selected geometry in exported STEP, private/resolution-bound image evidence,
frozen controls, disabled behavior, re-export reuse and the optional AI call cap.

The real catalog-photo fixture has manually annotated facade corners. With an
explicitly assumed frontal camera and scale, fitting reduces its 2D landmark
residual. Without that opt-in assumption, the uncertain camera is skipped. This
is a facade fitting test, not calibrated full-furniture or physical accuracy
verification. No real-photo reconstruction accuracy percentage is claimed.

## Changed files

| Purpose | Files |
| --- | --- |
| New numerical engine | `app/cad_optimizer.py`, `app/cad_parameters.py`, `app/cad_projection.py`, `app/cad_optimizer_schema.py`, `app/cad_optimizer_worker.py` |
| Existing workflow integration | `app/agents/engineering_agent.py`, `app/api/image_to_cad.py` |
| Native component cache | `app/photo_cad.py` |
| Configuration | `app/config.py`, `.env.example`, `compose.yaml` |
| Tests and photograph annotations | `tests/test_cad_optimizer.py`, `tests/test_cad_optimizer_workflow.py`, `tests/fixtures/cad_optimizer_real_photo.jpg`, `tests/fixtures/cad_optimizer_real_photo.json` |
| Documentation and results | `README.md`, `docs/CAD_OPTIMIZER.md`, this report, and the two JUnit files linked above |

Docker/Celery deployment was not executed. The isolated executable works locally;
it is designed to avoid multiprocessing's prohibition on children inside daemonic
Celery workers. Existing frontend files and database schema required no changes.
