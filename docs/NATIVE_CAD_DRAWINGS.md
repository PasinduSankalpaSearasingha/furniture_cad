# Native CAD drawing exports

Whole-furniture jobs request only the assembly page and native document from
TechDraw. Individual source solids remain temporary validation inputs; per-part
pages described below apply to legacy `PARTS` exports.

CadQuery remains the solid modeller. The exported part STEP files are reopened
and checked before any drawing, GLB or STL is produced. Replacing the modelling
library does not resolve dimensions or hidden construction from one photograph.

## build123d

Assembly elevations, part sheets and the combined dimension sheet use build123d
hidden-line projections of the accepted solids. DXF keeps native LINE, CIRCLE,
ELLIPSE and SPLINE entities instead of replacing every curved edge with a fixed
48-point polyline. PDF uses vector paths, with visible and hidden edges. All DXF
geometry is in millimetres at 1:1 model scale; PDF/SVG views are fitted to paper.
The combined sheet's clipped detail uses requested 0.005 mm curve deflection;
that request is not a certified global error bound.

The pinned build123d 0.10.0 and ocp-gordon 0.1.17 share the existing OCCT 7.8
runtime with CadQuery 2.6.1. Upgrading build123d requires checking kernel
compatibility and rerunning curve, projection, STEP and edit tests.

An offline 1,000 mm round-top test measured about 1.071 mm chord deviation in
the previous sampled outline. Its new DXF contains native circles at the
specified 500 mm outer radius and 8 mm hole radius. This improves drawing
representation; it does not establish the photograph's real dimensions.

## FreeCAD TechDraw

TechDraw is optional and runs in a separate Docker process. The tested runtime
uses Debian Bookworm FreeCAD 0.20.2, its own OCCT library and an invisible Xvfb
display. `--init` is required for the virtual-display startup handshake.

From the repository root, build the image:

```powershell
docker build -f tools/techdraw/Dockerfile -t fiske-techdraw:0.20.2 .
```

Set these backend environment values and restart the idle local worker:

```dotenv
TECHDRAW_DOCKER_IMAGE=fiske-techdraw:0.20.2
TECHDRAW_TIMEOUT_SECONDS=180
```

The local worker needs the Docker CLI and access to its Linux engine. The shared
Compose app does not mount a Docker socket and therefore leaves this feature
disabled. Enabling it in a deployment needs a separately provisioned drawing
executor with access to the generated revision paths; the environment setting
alone does not provide that infrastructure.

For each newly generated or edited revision, the process:

1. Imports each verified part STEP and checks its hash, solid validity and volume.
2. Creates three assembly views and three local-axis views per part.
3. Adds native extent dimensions measured from the projected geometry. Values
   must agree with the STEP envelopes within 0.1 mm. This is an export comparison
   threshold, not a manufacturing tolerance or physical accuracy claim.
4. Fits each view to A3, checks its paper scale, exports PDF/SVG and saves a native
   FreeCAD document with embedded geometry and drawing template.
5. Reopens the native document and checks the saved dimension references.
6. Validates all outputs before adding them to the revision manifest and ZIP.

Additional files:

- `furniture_REVIEW.FCStd`: embedded STEP solids, TechDraw views and associative
  dimensions, editable in FreeCAD. Imported solids do not have the app's
  parametric feature history.
- `techdraw_review_drawings.pdf`: assembly and all part pages.
- `techdraw_assembly_REVIEW.pdf/.svg` and `part_Fxxx_techdraw_REVIEW.pdf/.svg`.
- `techdraw_verification.json`: source hashes, volumes, measured dimensions,
  paper scales and native-document reopening result.
- `drawing_generation.json`: completed, failed or unconfigured native export.

The container has no network, drops capabilities, has CPU/memory/process limits
and sees only the new generated revision. It receives no API key, database or
original uploads. Timeout recovery removes its uniquely named container.
If TechDraw is unavailable or verification fails, the build123d drawing set
remains available and the reason is recorded in `drawing_generation.json`.

Use the app's part editor to change geometry. Each accepted edit rebuilds STEP,
meshes, DXF/PDF/SVG, FreeCAD drawings, schedules and ZIPs in a new revision.
Changing a downloaded FreeCAD document does not synchronize changes back to the
app. Earlier saved files remain immutable.

No additional OpenAI requests are used by these drawing exporters.

## Verification

```powershell
.venv\Scripts\python.exe -m pytest tests/test_native_drawings.py tests/test_dimension_sheet.py tests/test_techdraw.py
$env:TECHDRAW_INTEGRATION_TESTS='1'
.venv\Scripts\python.exe -m pytest tests/test_techdraw.py
```

Integration checks require the built image. They use synthetic model responses
and real geometry, including a drilled round top, a tilted curved rail and
round-to-tapered chair-leg edits with private downloads and immutable revisions.
They do not make paid AI calls or certify photo reconstruction accuracy.

Official references: [build123d technical drawings](https://build123d.readthedocs.io/en/stable/tech_drawing_tutorial.html),
[FreeCAD TechDraw](https://github.com/FreeCAD/FreeCAD-documentation/blob/main/wiki/TechDraw_Workbench.md).
