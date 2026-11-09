# Generated table comparison sample

`scripts/create_table_platform_sample.py` creates new table solids through the
application's `PhotoDesign` schema, `create_photo_furniture` builder and
`build_engineering_package` exporter. It does not read or copy the original PDF.

The profile is manually specified from the supplied photo. This demonstrates
the CAD and drawing pipeline; it is **not** an end-to-end OpenAI photo-analysis
test. The configured key was rejected, and no provider requests are made by the
sample script.

Reference target dimensions are 2800 x 1100 x 750 mm overall, a 45 mm top and
2100 x 766 mm base footprint. Base curves, 55 mm panel section, R100 top corners,
materials and component segmentation are estimates. The construction contains
one tabletop, one continuous diagonal base and two contact-trimmed cross-base
wings. This is a four-solid review assembly with unverified physical joinery.

Build the optional native drawing image first, then run:

```powershell
docker build -f tools/techdraw/Dockerfile -t fiske-techdraw:0.20.2 .
uv run --no-sync --with pypdfium2==4.30.0 python scripts/create_table_platform_sample.py
```

Each execution uses a new `outputs/table-platform-generated-*` directory.
The pipeline reopens STEP, verifies every solid and produces model meshes,
dimensioned drawings, schedules, native FreeCAD files and a complete ZIP.
The additional `platform_table_all_views.pdf/.svg/.dxf/.png` shows top, front,
side and isometric views at 1:20 on one A3 sheet. The isometric view is projected
from the same reopened solids. Dimension chains are calculated from their
actual CAD envelopes, rather than independent typed labels.

The script verifies native DXF measurements, PDF page count and source STEP
hashes before updating the fresh sample manifest and archive. It does not
modify saved projects or the previous reference sample. This additional
four-view layout is used by this comparison runner; the Studio's default
combined-dimension sheet remains its existing three views plus detail layout.

Rendering uses a temporary pypdfium2 overlay; no new application dependency is
added. `app/drawing_projection.py` accepts an explicit camera for isometric
projections and discards only zero-length HLR edges (at most 1e-9 mm). This
prevents the SVG Bezier converter rejecting empty projected splines while
retaining all nonempty native curves and leaving the 3D solids unchanged.
