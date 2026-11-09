# Native CAD geometry optimization

The engineering generation path can refine an accepted `PhotoDesign` locally:

**Photos and quantitative evidence → locked parameter registry → native B-Rep
projections → bounded SciPy fitting → full engineering checks → visual review
and the existing STEP/drawing exports.**

The numerical loop makes **zero LLM requests**. It uses CPU, CadQuery, OpenCascade,
NumPy, SciPy and OpenCV. It does not reconstruct CAD from generated meshes.
The existing mesh-based reference-fit endpoint remains compatible and separate.

## Enable and configure

Backend environment controls:

```dotenv
CAD_OPTIMIZER_ENABLED=1
CAD_OPTIMIZER_MODE=balanced
CAD_OPTIMIZER_MAX_SECONDS=600
```

The last value is the server's maximum allowed job time. The selected mode supplies
the usual shorter budget. Set `CAD_OPTIMIZER_ENABLED=0` to disable the new engine,
or supply `"optimization": {"enabled": false}` for one generation. Neither requires
a database migration or additional dependencies.

| Mode | Objective evaluations | Wall time | Independent controls |
| --- | ---: | ---: | ---: |
| economy | 15 | 20 seconds | 3 |
| balanced | 40 | 60 seconds | 8 |
| quality | 100 | 180 seconds | 16 |

These are experimental resource defaults. API options can change them within the
schema's limits; the server time cap still applies. Finite-difference evaluations
and cached objective invocations count toward the objective budget. Accepted
solver steps have a separate iteration limit. A hidden child executable provides
a hard time limit, including startup, with a bounded cleanup allowance. It works
from local jobs and Celery workers. A stopped worker leaves the original or last
completed, fully validated candidate available.

## Numerical evidence API

Supply `optimization` to the existing
`POST /api/projects/{project_id}/generate-all` request. The ordinary `image_id`,
`known_values`, `notes` and `analysis_workflow_id` inputs retain their behavior.
For example, the following is an optimization field, not a complete request:

```json
{
  "optimization": {
    "mode": "balanced",
    "component_ids": ["F002"],
    "parameter_ids": ["F002.position.0"],
    "feature_components": {"V001": ["F002"]},
    "mismatch_feature_ids": ["V001"],
    "views": [{
      "image_id": "00000000-0000-0000-0000-000000000001",
      "camera": {
        "image_size": [800, 600],
        "projection": "perspective",
        "intrinsic_matrix": [[900, 0, 400], [0, 900, 300], [0, 0, 1]],
        "rotation_vector": [1.57079632679, 0, 0],
        "translation_mm": [0, 375, 1500],
        "reliable": true,
        "uncertainty": "Replace with your calibration evidence"
      },
      "landmarks": [{
        "part_id": "F002",
        "anchor": "center",
        "image_point": [250, 420],
        "confidence": 0.9,
        "evidence_id": "V001",
        "visible": true
      }]
    }],
    "max_structural_llm_calls": 0
  }
}
```

Replace the UUID, camera and pixels with observations for the uploaded reference.
Pixels and camera intrinsics must use the stored image's resolution, not a browser
thumbnail's dimensions. Images must belong to the project and selected reference
set. Input validation rejects mismatched image resolutions and foreign images.

Landmarks support native mass centers, top/bottom anchors, local points, normalized
size corners, and identified path/outline controls. Curves support existing native
Bezier paths, panel outlines and native B-Rep edges. `silhouette` contains sampled
boundary pixels and requires `silhouette_reviewed: true`; use
`silhouette_complete: false` for a partial observed boundary. Invisible observations
are excluded. Multiple views have independent cameras and confidence weights.

The existing vision output contains qualitative features, not calibrated pixel
observations. Consequently ordinary uploads **skip numerical fitting unless usable
evidence is supplied**. MATCH/MISMATCH text never becomes an invented numerical
target. A missing or uncertain camera is reported; uncertain cameras are excluded
unless `allow_uncertain_camera` is explicitly enabled.

Camera rotation uses OpenCV Rodrigues vectors in radians, with world-to-camera
translation in millimetres. Perspective intrinsics are in pixels; orthographic
focal entries are pixels per millimetre. Perspective distortion uses OpenCV's
supported coefficient formats. `align_cameras: true` estimates pose first from
at least six noncoplanar `fixed_world_point` correspondences. Moving CAD controls
are never used as fixed calibration points. A small reprojection residual is a
pose-consistency check, not independent certification of the supplied calibration.

## Parameters, constraints and matching

The registry includes existing named values and bounded component controls such as
`F002.position.0`, `F002.rotation_deg.1`, `F001.outline.2.0`,
`F003.path.1.2`, and loft section centers/width/depth. Each record includes component
ownership, value, bounds, unit, evidence IDs, uncertainty and lock/optimization flags.
Expression-backed local controls retain their named dependencies and receive bounded
arithmetic offsets; generated Python is never edited. Derived control bounds are
also checked after named values change. Overrides can narrow bounds or freeze controls. User-provided and matched-drawing
dimensions stay locked. Shared parameters that also drive unselected components
are excluded. Counts and topology are not numerical optimization variables.

Without an explicit component subset, visible numerical landmarks/curves select
their components; a complete silhouette permits whole-model fitting. The solver visits global proportions, component dimensions, positions/angles and
local curve controls separately. Only selected controls up to the mode limit are
searched. Linear `relations` support mirrored placement and other explicit
couplings. Existing smooth cubic-chain joins, including C1 joins, and closed outline
endpoints are preserved; an adjustment that would change a confirmed control is rejected. Existing lofts,
sweeps and outline panels retain their native construction methods. Free standalone
NURBS surface poles and arbitrary topology changes are not implemented.

Landmark residuals use pixel distances normalized by the image diagonal. Curves
and silhouettes use bidirectional nearest-boundary distances, with robust SciPy
loss and configurable landmark/silhouette/curve/regularization weights. Partial
boundaries use only observation-to-CAD distances. Hard geometry constraints use
an infeasible-candidate penalty and cannot be traded for a better image score.

Native projection uses `HLRBRep_Algo`, not its polygonal alternative. Visible edges
are sampled for pixel comparison. Native face-ray intersections distinguish actual
silhouette boundaries/holes from interior edges and reject occluded native-edge
samples. No triangle mesh is used for the objective. See the primary
[OpenCascade HLR documentation](https://github.com/Open-Cascade-SAS/OCCT/blob/master/dox/user_guides/modeling_algos/modeling_algos.md)
and [OpenCV camera projection reference](https://docs.opencv.org/4.x/d9/d0c/group__calib3d.html).
Silhouette sampling has finite pixel resolution and can be expensive on complex
assemblies; time and edge limits still apply.

Every candidate uses the existing builder and full solid/contact/interference and
supplied-envelope validation, plus `BRepCheck_Analyzer`. Baseline contacts must be
preserved. Native component caching reuses unchanged prefixes; changing an earlier
component invalidates later components because contact trimming depends on order.
This conservative approach favors validity over aggressive fitting speed.

## Revisions, exports and AI spending

The `engineering_optimization_0` workflow checkpoint saves the original/selected
plan hashes and optimization report. The report records bounded parameters,
attempts, accepted content-addressed candidate revisions, modified values, evidence,
errors, validation, elapsed CPU-worker wall time, evaluations and zero local LLM
calls. Candidate histories are audit entries; only the final selection creates an
ordinary immutable project revision. Original accepted plans remain checkpointed.

Reports appear under `engineering_cad.workflow.cad_optimization`, including in
`engineering_workflow.json`. Final engineering validation and the existing visual
review operate on the selected model before STEP and drawing exports. A rejected
optimization keeps the initial accepted model. Saved-plan re-export copies the
frozen reports and selected plan without optimization or paid requests.

Numerical improvement does not clear qualitative mismatches or establish physical
accuracy. The error metric is a defined normalized image residual, not a furniture
accuracy percentage. No manufacturing approval is implied.

For jobs with numerical evidence, optional structural repair defaults to zero
additional AI calls. Setting `max_structural_llm_calls: 1` permits one response only
when review reports a structural problem. Its allowance is reserved in a checkpoint
before submission, preventing automatic paid repeats after an uncertain failure.
Invalid repair input does not trigger another response. After that call the changed
CAD is marked for manual visual review; no additional paid review is requested.
AI usage remains in the existing invocation ledger, separate from local compute
reports. Legacy jobs without numerical evidence retain their existing repair flow.

## Verification and limitations

```powershell
.venv\Scripts\python.exe -m pytest tests/test_cad_optimizer.py tests/test_cad_optimizer_workflow.py -q
```

Tests cover measurement locks, bounds, calibrated projection, native HLR/rays,
dimension/leg-position recovery, native cubic sweep refinement, C1 continuity,
mirrored controls, invalid candidates, rollback, budgets, early stopping, zero AI
requests, multiple views, component caching, private API evidence, immutable
re-export, optional repair spending and genuine STEP round trips.

Synthetic tests project known CAD from known cameras and perturb parameters. They
prove numerical recovery for those fixtures, not accuracy on real furniture.
`tests/fixtures/cad_optimizer_real_photo.jpg` is a reduced copy of an existing catalog
photograph. Its JSON records manually annotated facade corners. A real-image test
reduces facade reprojection error with an explicitly assumed frontal camera and
scale; another verifies that the uncalibrated camera is skipped by default. This
does not validate depth, hidden construction, full-object reconstruction or physical
measurements. The UI does not yet provide a landmark/calibration editor; quantitative
evidence is supplied through the API.
