# CAD preview and saved-plan recovery

A preview failure must not discard a validated furniture plan. The sculpted bench
workflow exposed two problems: its arm/support display meshes exceeded 500,000
triangles, and the final comparison renderer raised an unhandled exception before
the final AI request was reserved.

`app/cad_meshing.py` now reads absolute-unit OCCT triangulations directly. Calling
CadQuery's `Shape.tessellate` after absolute meshing can reject OCCT's recorded
deflection and invoke relative remeshing again. Face transforms and orientation
are retained when extracting the native triangles.

Dense display meshes use volume-preserving quadric reduction, bounded source and
output triangle counts, and bidirectional sampled mesh-deviation checks. Sharp
display edges are split again for shading. This changes display meshes only;
the CAD solids, parameter values and authoritative STEP geometry are unchanged.
Reduction details and sampled deviations are recorded in comparison-sheet mesh
metadata, CAD preview metadata and each GLB mesh's extras. These sampled checks
do not establish a full surface-error guarantee or physical manufacturing accuracy.

One prepared raster mesh is reused across all seven comparison views. The final
review receives actual CAD views and retains the existing one-request allowance.
If the comparison cannot be created, AI review is skipped and the CAD remains an
explicitly unreviewed draft. An unpreviewable proposed correction retains the
preceding model.

If a mesh export cannot finish, the workflow exposes its real saved CAD plan,
reference links and available measurements through the result-report download.
It does not claim that STEP/GLB/drawing downloads completed. Unexpected failures
after a validated plan exists also retain that report while keeping the detailed
exception in server logs.

The Studio exposes **Resume saved CAD** for these incomplete outputs. Resumption
starts from the latest accepted candidate, including candidate 1, so an interrupted
second candidate does not repeat earlier paid planning calls. Saved or reserved
final-review requests keep their existing recovery rules.

Verified on 2026-10-28: **85 regression checks passed**, including absolute
mesh extraction, display reduction without CAD-solid changes, reuse across views,
preview/export fallback, second-candidate recovery, existing drawing/STEP checks,
spending limits and component edits. See
[JUnit results](preview-recovery-regression-results.xml). The production frontend
build and submission-idempotency checks also passed. The failing arm's local
reproduction reduced 769,558 display triangles to 19,999, with approximately
0.102 mm maximum bidirectional sampled mesh deviation across 512 samples.
