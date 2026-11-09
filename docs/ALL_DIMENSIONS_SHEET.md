# All dimensions on one sheet

New whole-furniture jobs use a general arrangement sheet with top, front, side
and isometric views. They omit the detail inset, part-size table and individual
part labels described below for legacy jobs. See
[whole-furniture outputs](WHOLE_FURNITURE_OUTPUTS.md) for formats and AI usage.

Studio now shows a combined drawing in **3D workspace** and **Files & drawings**.
The card includes a zoom control, a vector preview, a one-page PDF, an editable
DXF and a ZIP containing PDF/DXF/SVG/JSON. Expand **Read all dimensions as a table**
to read the design values and component sizes without zooming the drawing.

The A3 landscape sheet contains aligned top/front/side views at a shared scale,
visible and hidden CAD edges, overall width/depth/height, broad front component
widths and height intervals, an enlarged top-corner detail, a parameter schedule,
component dimensions and a title block. The detail has its own enlargement.
Source letters distinguish estimates, entered dimensions and matched drawings.
All existing design values are listed, including sizes, radii, gaps, counts and
ratios. Component sizes are local X/Y/Z envelopes rather than material blank
allowances. The top-corner detail projects actual CAD edges; it is not an inferred
section through hidden construction.

Measurements and linework come from the saved exported STEP parts. Creating the
sheet for a completed job uses no AI calls and does not change its CAD or original
archive. Existing jobs receive private, source-linked cached drawing files.
New engineering packages include the combined sheet automatically. Downloading
PDF/DXF/SVG/JSON from the card is available for both old and new jobs. Print the
PDF on A3 at 100% to retain its stated assembly scale.

Current example: job `c3d3d035-6ad3-4b6f-8842-c8b1f6beb585`, 15 components,
43 design values and 13 dimensional callouts on one page. Its current CAD overall
size is **2200 x 450 x 800 mm**; the supplied screenshot is a layout reference,
not a request to replace these dimensions with another cabinet's measurements.
Files are under `outputs/dimension_sheet_c3d3d035-6ad3-4b6f-8842-c8b1f6beb585_v2`.

Checks executed on 23 October 2026: 24 drawing/CAD/export/artifact regression tests
passed, and a separate matched-sample saved-plan reexport check passed. The final
drawing tests, production frontend build, native DXF dimension measurements,
single-page PDF, private cached file access, desktop/mobile layout and zoom
checks passed. Browser screenshots and results are under
`outputs/dimension-sheet-verification`.
