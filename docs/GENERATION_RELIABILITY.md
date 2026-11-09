# Furniture scope and robust curved geometry

Whole-furniture output may contain a main item and genuinely detached companion
furniture, such as a sofa and ottoman. `PhotoDesign.separate_items` assigns each
companion its component IDs and its own named width, depth and height parameters.
All remaining components form the primary furniture, measured against
`width_mm`, `depth_mm` and `height_mm`. Membership is validated; unknown,
duplicate or overlapping assignments and an empty primary item are rejected.

Each item must be internally connected. A detached companion is allowed to have
a placement gap, but cannot bridge disconnected parts of the primary furniture.
Interference is still checked across all items. Companions are not trimmed
against the primary item to conceal an overlap. Their own dimension checks use
the same 0.01 mm limit as supplied overall constraints.

The one general-arrangement sheet retains all furniture in the layout and
dimensions the primary item and companions separately. Metadata records both
`overall_dimensions_mm` for the primary item and `layout_dimensions_mm` for the
complete scene. Optional native assembly projections use the full layout size.
STEP, GLB and STL remain combined whole-furniture outputs.

A narrow migration for saved failed plans can declare an ottoman or footstool
only when the source photo analysis identifies it as separate and the saved plan
explicitly excludes it from the main furniture dimensions. It also requires
named companion dimensions and matching component labels. Geometry and supplied
parameters are retained during this scope migration. Saved-plan re-exports and
component edits retain their frozen membership and construction interpretation.

New whole-furniture workflows freeze `sweep_frame_policy=WORLD_UP_V2`. Oval and
rectangular sections use a horizontal section axis and a vertical section axis.
Planar sweeps maintain a fixed vertical binormal, avoiding unwanted cushion
section rotation. New lofts use `loft_mode=BOUNDED`: small interpolation overshoot
is constrained to the declared section envelope. Automatic clipping is limited
to two percent of each section-envelope span and two percent of volume; larger
departures trigger a local reconstruction ladder before any further AI plan.
The builder first tries native centripetal cubic/quadratic interpolation, then
shape-preserving intermediate profiles with native smooth loft surfaces. It
does not replace the model with a faceted or ruled solid. At most five local
constructions are attempted, with a 20-second check between attempts. An
individual native kernel operation is not interrupted mid-build.

Every alternative keeps original profile positions, widths, depths and corner
radii. Intermediates are derived from shape-preserving interpolation of profile
bounds and normalized corner radii. The permitted envelope always comes from
the original sections. Original two-percent envelope and volume guards remain
unchanged; no overall dimensions or source parameter values are scaled. A
repaired component must be one valid closed positive-volume solid. Actual
horizontal plane cuts are compared bidirectionally with samples of every
original section, including the caps, within 0.005 mm. Alternatives that move
the declared profiles are rejected.

Successful replacements are recorded as `LOCAL_LOFT_REBUILD`, including method,
local attempt count, measured profile agreement and zero extra AI requests.
The files view identifies locally repaired curves for photo comparison. These
checks establish geometry and parameter retention, not photo identity. If the
local ladder cannot produce a safe solid, the existing bounded AI correction
and spending policies still apply. Saved rejected candidates can be revalidated
with this constructor without paying for another full CAD plan. Completed valid
lofts retain their original construction; legacy `SMOOTH` lofts are unchanged.
Clipping and reconstruction are both recorded for visual review.

Contact cuts can expose a CAD kernel bounding-box defect: the box includes an
extremum of a spline's underlying surface that was already trimmed away. For
envelope-clipped lofts, component metadata and strict dimension checks measure
the remaining B-Rep material using distances to six exterior planes. They do
not substitute requested dimensions or a preview mesh. The original 0.01 mm
supplied-dimension tolerance remains in force after contact trimming.

Legacy omitted frame and loft fields keep their original interpretation, so old
saved geometry does not silently change on re-export. CAD kernel construction
failures are converted to component-specific validation errors that the bounded
planner can repair, rather than unhandled workflow exceptions. Validated drafts
remain available if a later visual correction cannot be built.

For `panel_frame=BOUNDS`, a crown/dish surface grid spans the actual panel's local
XY bounds, and a global `dish_depth` is centered on that panel. Offset outline
coordinates therefore remain valid; the surface is not incorrectly centered on
the coordinate origin. Explicit individual dish centers retain their supplied
local coordinates. Legacy saved panels retain their previous interpretation.
The new crown construction extrudes the top surface vertically before clipping
to the panel, rather than offsetting along its changing normals. Deep cushion
crowns therefore avoid the self-intersections of a large normal offset.

Envelope measurement starts with 0.02 mm requested tessellation. If a component
exceeds the one-million-triangle budget, it tries 0.05, 0.1 and 0.2 mm meshes,
recording the actual requested tolerance. The independent 0.005 mm STEP envelope
comparison, 0.001 mm sampled surface comparison and adaptive volume acceptance
limits remain unchanged. Meshing settings do not certify physical accuracy.

Regression cases cover the saved sofa's 3000 × 1450 × 880 mm primary envelope
and 1050 × 780 × 420 mm ottoman, connection scopes, invalid memberships, a false
connection through a companion, GA dimension scopes, section orientation,
bounded lofts, legacy compatibility and contextual kernel failures. Solid
validation does not establish exact photo identity or manufacturing approval.
