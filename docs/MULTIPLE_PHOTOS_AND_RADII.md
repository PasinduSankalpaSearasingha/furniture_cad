# Furniture photos and curve dimensions

Upload up to six PNG, JPG or WebP photographs of the same furniture together,
or use **Add more angles** to extend the current selection. The first photo is
the primary reference. Optional angle labels include front, rear, left, right,
top, underside, perspective and detail. **Make primary** changes the reference
camera. Removing a view or changing its label requires analysis again before
generating CAD. Uploaded originals remain in the project; removing a selected
view does not delete historical design evidence.

All selected images are analysed together and reach dimension interpretation,
CAD planning, small correction requests and actual-CAD visual review. The
prompts explicitly treat the photos as views of one object, retain visible
asymmetry and use the first image for the comparison camera. Additional views
can clarify occluded shape; they do not establish physical measurements or
guarantee reconstruction accuracy. GPT-6.1 Sol remains configured for these
steps. More images contribute input tokens within the existing spending policy;
the upload does not start one separate analysis per photo.

The generation API retains `image_id` for the primary image. It additionally
accepts ordered `image_ids` (maximum six) and optional ordered
`reference_views: [{image_id, angle}]`. IDs must be unique and belong to the
same project. A saved analysis must match the exact set, order and angle labels.
Legacy callers supplying only the primary ID and a saved multi-photo analysis
inherit its complete reference set. Saved-plan re-exports and edits retain the
frozen reference inventory, and local re-exports make no new AI requests.

New whole-furniture drawings include R-value leaders for face-on circular CAD
edges, a circular-radius legend and an editable native DXF radius DIMENSION.
PDF and SVG show the same radius values. The DXF remains in true millimetres;
plotting scale does not change the measured radius. Curves and their radii are
derived from the actual CAD geometry used for the export, rather than guessed
from a raster preview. The STEP model retains its native geometric curves;
radius annotations are on the general-arrangement drawing, not STEP PMI.

Freeform and elliptical edges do not have a single circular radius. They are
labelled variable-radius curves. Drawing data and the **Curve radii from CAD**
table include sampled local-radius ranges; these samples are neither a
constant-radius replacement nor verified global extrema. Their native spline
or ellipse geometry stays editable in DXF and STEP. The complete circle-radius
inventory and variable-curve samples are retained in the drawing JSON, even
where a crowded sheet cannot place a leader for every distinct radius.

These radii describe the generated design. Photo-derived sizes and radii remain
estimates until verified. Existing saved deliverables are immutable; generate a
new version or locally re-export a saved plan to obtain the new annotations.

Validation includes real STEP round trips, PDF/SVG labels and DXF radius
measurements, multiple-photo propagation and ownership checks, six-photo limits,
analysis-set integrity, and synthetic browser checks without paid AI requests.
