# Dimensions from the Momentro samples

The Studio upload flow now checks the seven paired examples from `Momentro.zip`.
Uploading a reference image, including a resized or recompressed copy, automatically
uses the paired shop drawing's overall dimensions. The dimension card identifies
the sample, shows width/depth/height and their sources, and opens the original PDF.
Your entered dimensions override individual sample values. When there is no
reliable image match, the existing AI workflow estimates missing dimensions and
shows them as **AI estimate** as soon as image analysis completes.

| Sample | Width (mm) | Depth (mm) | Height (mm) |
| --- | ---: | ---: | ---: |
| Bed | 2500 | 2010 | 1200 |
| Bench | 1350 | 430 | 450 |
| Shoe cabinet | 2100 | 545 | 858 |
| Sideboard | 3000 | 500 | 700 |
| Sofa | 3000 | 1450 | 880 |
| Dining table A | 2800 | 1100 | 750 |
| Dining table B | 2200 | 950 | 750 |

Bed length maps to depth. Shoe cabinet height defaults to 858 mm including the
18 mm client top; the card documents the 840 mm alternative without that top.
Enter 840 mm to choose that variant. Table B's drawing labels the base separately
as 2100 x 850 x 732 mm; the table above uses the overall dimension callouts with
the stone top. The sofa reference image includes product sizes that differ from
the paired shop drawing; the drawing supplies the target dimensions.

Matching is deliberately conservative: normalized-image hashes and agreement in
image structure, colour and aspect ratio support the same reference photograph.
A different view, substantial crop, or merely similar furniture falls back to
estimation. Matching does not establish the physical size of a new object. All
sample sizes remain unverified for the uploaded furniture.

Large PNG/JPEG/WebP files (up to 25 MB) are resized in the browser before upload,
so the original shoe cabinet photos work within the backend's 1.5 MB image limit.
The backend continues to validate raster dimensions, format and byte limits.

The private bundled catalogue is in `app/reference_data/momentro/catalogue.json`.
Its references and drawings are included with application/source packaging and
Docker's existing `COPY app`. It has no dependency on the user's Downloads folder.
Sample PDFs require authentication (or the configured local workspace identity).
The importer reads archive entries as data; it never executes DWG/3DS/documents:

```powershell
.venv\Scripts\python.exe -m scripts.import_momentro_samples 'C:\path\Momentro.zip'
```

Matched constraints and their provenance are frozen in each workflow's input
hash, passed through deterministic CAD envelope checks, and retained in saved
revisions, drawing/CAD packages and the combined `accuracy_report.json`.
Saved-plan reexports retain the original values and source metadata. Sample
sizes are marked `SAMPLE_DRAWING`, entered values `USER_PROVIDED`, and remaining
dimensions `AI_ESTIMATED`; none are marked physically verified.

Verification commands (no paid model calls):

```powershell
.venv\Scripts\python.exe -m pytest tests/test_sample_dimensions.py tests/test_optional_dimensions.py tests/test_engineering_cad.py tests/test_image_to_cad.py -q
.venv\Scripts\python.exe -m scripts.verify_sample_upload
```

The browser check uses a temporary database and output directory, disables CAD
dispatch, and saves screenshots/results under `outputs/sample-dimensions-verification`.
Its intermediate AI values are simulated; the regression tests use mocked AI
with real CadQuery solids and export validation.

Verified on 23 October 2026: 50 targeted regression tests passed. The production
frontend build passed. Desktop and mobile browser checks passed with no page
errors or horizontal overflow, including a 50 MP original shoe cabinet upload,
user overrides, reload persistence and display of intermediate estimates.
