# Photo reconstruction

The reconstruction target is the uploaded furniture's visible design: silhouette,
curves, support faces and orientations, taper, placement, negative spaces and
continuous junctions. Dimension entry remains a separate step. No manual-drawing
dimensions are inferred as constraints unless the user supplies them.

The existing dimension/vision stage also describes up to twelve defining visible
features. This adds no separate AI stage. The CAD planner receives that appearance
contract and the original photograph. It must preserve the observed identity and
use shared or mirrored geometry only where the photograph supports it. A valid
solid with the correct overall size does not establish a visual match.

The first review render uses the reference camera estimated during photo
analysis, or the camera estimated by the vision stage for direct API requests.
It preserves the source aspect ratio up to 1280 pixels on the longest side.
The remaining six views expose the front, rear, both sides, top and underside.
An explicit ordered view inventory keeps the reference view first across cache
and database round trips. The original photo and actual CAD preview are shown
together in the model workspace.

The reviewer checks every defining feature and supplies affected CAD component
IDs and concrete corrections. Local checks reject missing, duplicate or unknown
feature IDs, unknown component IDs, and critical mismatches or unverifiable
features, even if the model says accepted. One bounded correction remains
available. A remaining mismatch produces an editable draft marked
`PHOTO_MATCH_NEEDS_REVISION`; it is never presented as a completed photo match.
Previews and STEP/DXF exports still come from validated actual CAD geometry.
For partly occluded members, only the visible portion is a shape target. Unknown
hidden continuations and joints remain limitations rather than required matches.

## Model routing

The following backend variables override only CAD planning and visual review:

```dotenv
OPENAI_PHOTO_DESIGN_MODEL=gpt-6.1-sol
OPENAI_PHOTO_REVIEW_MODEL=gpt-6.1-sol
```

Photo analysis and dimension extraction retain the configured vision model.
These overrides apply after `OPENAI_REASONING_MODEL` routing. Each new workflow
freezes the selected models; saved-plan re-exports retain the source model record
and use no new AI requests. Existing source revisions remain unchanged.

Camera estimates and AI visual acceptance are approximations. Unseen surfaces,
precise physical construction and structural stability are not established by a
single photograph. No exact reconstruction or manufacturing approval is claimed.

The saved live sculptural-table trial in `outputs/astra-photo-match-live` completed
its CAD exports using Astra planning and review, with one bounded correction.
The final review still rejected the branch junctions, foot contours and relief.
That result remains an editable draft; this trial does not demonstrate exact
photo reconstruction from a model change alone.
