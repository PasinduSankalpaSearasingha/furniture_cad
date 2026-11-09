# STEP surface verification correction

The export failure `STEP surface round-trip exceeds 0.001 mm` on workflow
`772e2fe4-c628-41bf-b400-1a5552465c0c` was a false rejection in the surface
comparison. The saved F012 rear corner member is a valid Bezier outline with
a stationary end tangent. Its tessellation contains points off its exact trimmed
B-Rep surface. Comparing those mesh points against the original solid itself
reported the same **0.843365987 mm** maximum gap as comparing against its STEP
export. Source and STEP volumes were identical.

The verifier now evaluates points directly on each trimmed B-Rep surface in UV
coordinates, checks their membership in the face, and samples exact edges and
vertices. It compares both directions against the other shape's boundary faces.
That boundary comparison also prevents points inside the other solid from being
treated as zero surface distance. The 0.001 mm surface limit, validity/closed-shell
checks, volume checks and existing envelope limits remain enforced.

Verification reports record `EXACT_BREP_TRIMMED_UV_EDGES_AND_VERTICES` and the
surface tolerance. Failures identify the part, STEP filename, measured gap and
comparison direction.

On the saved failing design, all 15 individual STEP exports passed. The formerly
failing F012 gap was **3.7521e-12 mm**; the largest part gap was **4.6115e-10 mm**.
The design geometry and dimensions were retained. A regression with a moved hole
but unchanged volume and outer bounds still fails the surface check.

Executed verification on 23 October 2026:

- 11 engineering CAD tests passed, including the original Bezier regression and
  the altered-hole rejection.
- 44 sample/dimension/image-to-CAD/export regression tests passed.
- Direct sphere and cylinder STEP round trips passed.

The diagnosis and complete-part checks are saved under
`outputs/step-export-recovery`. These are geometry/export checks; the existing
physical measurement and engineering review requirements still apply.

The local backend was restarted with the correction and the original failed
workflow was retried successfully to `REVIEW_REQUIRED`. The same input hash,
revision, job and all prior checkpoint hashes were retained. AI invocations
remained 9 before and after recovery. The combined 12,604,290-byte download was
retrieved through the API; its ZIP CRC and every part STEP hash were verified.
Recovery evidence is in `outputs/step-export-recovery/recovery_audit.json` and
the download audit in `outputs/engineering-delivery-audit/772e2fe4-c628-41bf-b400-1a5552465c0c/audit.json`.
