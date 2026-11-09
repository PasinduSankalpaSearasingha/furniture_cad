# Editable furniture CAD, part recognition and six-view delivery

Validated 28 October 2026. Target: `D:\Enfection\furniture_planner\furniture_manufacturing_starter`. Application dependencies and validation use `.venv`. No paid API generation requests were made during implementation, testing or benchmarking.

## Implemented behavior

The existing whole-surface reconstruction/optimization path is preserved. Its delivery now includes native assembly/part STEP, assembly and per-part FCStd, shared parameters and regeneration source, stable component records and local/world transforms, six orthographic SVG/DXF views for every assembly/part, drawing PDFs, manufacturing review PDF, geometry/evidence PDF, BOM and review cutting envelopes, and an immutable package manifest.

The original photograph is input to optional part recognition and the existing local photo validation. The original mesh is retained. Region/face/vertex records distinguish geometric evidence from manufactured-part hypotheses. Cleanup/welding mappings preserve source scene IDs; `mesh_analysis.json` supplies node ranges and `reference/mesh_index_map.npz` maps working indices to loaded original triangles/vertices. OBJ polygon triangulation follows the loader. Unassigned faces and uncalibrated semantics remain explicit.

Authenticated project-scoped artifacts support safe nested paths and hash verification. Designer revisions preserve IDs, labels, dimension locks, boundary decisions and optional face/construction edits. Each revision reuses the saved mesh, regenerates native CAD/drawings and keeps a new immutable job. Altering a confirmed dimension requires a new confirmed value. Unchanged exact projection curves are reused after geometry, basis and artifact hash checks; annotations and title blocks are regenerated with the new revision. Assembly validation and outputs are regenerated because they describe the whole revision.

The React workspace provides assembly/individual WebGL previews, selection/hide/explode controls, colored reference-region inspection, six-vector-view switching, embedded PDFs, parameter and boundary editing, advanced declarative construction edits, and an export center. Explode is an interactive preview; no assembly sequence or joints are invented.

## Fixed API generation limits

**At most two Sol 6.1 generation requests plus one Neural4D generation job per job**, as explicitly requested. Model ID: `gpt-6.1-sol`. Strict structured recognition combines native CAD planning and semantic hypotheses, with at most one corrective request. SDK retries are disabled. SQLite submission reservations persist before HTTP submission; failed/ambiguous submissions still consume a slot and cannot silently resubmit. Completed identical requests replay from cache.

Legacy engineering workflows combine dimensions and initial native planning into one response and use local critic checks. Their persistent submission-event guard includes linked image analysis, retries, tool continuations and fallbacks in the same two-request limit. A limit stop retains available local CAD for review. Provider status polling and model-access checks are distinct read operations; numerical optimization, drawings, exports and manual revisions make zero AI generation requests. The existing Neural4D persisted UUID/binding safety remains intact.

The selected model's Responses, image-input and structured-output capabilities were checked against the [official Sol 6.1 documentation](https://developers.openai.com/api/docs/models/gpt-6.1-sol).

## Native editability and drawings

Actual FreeCAD **1.1.4** was exercised, using its isolated embedded Python. FreeCAD and CadQuery/OCP are never loaded into one process. Untrimmed axis-aligned plain boxes use constrained native Sketcher rectangles and PartDesign Pads driven by shared parameter expressions. Curved, rotated and contact-trimmed members use the packaged FeaturePython proxy, which invokes the same authoritative PhotoDesign/CadQuery builder through the project's `.venv`. Complex geometry is not a static STEP-import feature.

The proxy implementation and loader macro are bundled. Keep the source project/.venv available and install the proxy on FreeCAD's Python path before opening, or use the loader macro. After moving the project, update Parameters.ProjectRoot/PythonExecutable. These are editable local application documents, not standalone embedded copies of the whole CAD engine. Generated `assembly.py` reads `cad_parameters.json`; each part includes its construction/value/transform data and regeneration entry point.

Validation changes an unlocked envelope parameter, checks changed native geometry and relative placements against rebuilt B-Reps, regenerates and reopens edited STEP, generates six new native views, restores the source and reopens the assembly and every individual FCStd. The curved chair also changes a Bezier control, checks valid changed geometry and repeats independent native export/view generation.

Six canonical bases are explicit: front (0,-1,0), back (0,1,0), left (-1,0,0), right (1,0,0), top (0,0,1), bottom (0,0,-1), with right/up vectors and a right-handedness check. First/third angle affects sheet layout without mirroring physical views. Final HLR comes from native B-Rep curves through the existing OCCT/build123d projection engine. This route does not require FreeCAD GUI/TechDraw; a separate real FreeCAD capability probe also exported native TechDraw PDF/SVG/DXF successfully.

Default paper is A3, with A4/A2/custom support. SVG is paper space with explicit scale; DXF is 1:1 model space in mm. Visible/hidden curves are retained; VISIBLE/HIDDEN/CENTERLINES/DIMENSIONS/ANNOTATIONS layers are present. Unneeded centerlines are left empty. Measured native envelope dimensions identify confirmed versus derived sizes, and estimated physical provenance. PDF line weights stay readable when views are scaled, and larger furniture gets additional detail pages. No tolerances, cutting allowances or hidden manufacturing detail are fabricated.

Statuses record draft, geometry validation, supplied-dimension verification where complete, and manufacturing review. Supplied dimensions matching CAD do not establish physical measurements. No output is automatically approved for manufacturing. Materials, grain, allowances, joinery, tooling, structural/stability checks and physical accuracy remain review items.

## Local segmentation model evaluation

- [PartSAM source/setup](https://github.com/czvvd/PartSAM) specifies Python 3.11, PyTorch 2.4.1/CUDA 12.4 and custom CUDA extensions. Its [license file](https://github.com/czvvd/PartSAM/blob/main/LICENSE.md) distinguishes MIT project code from restricted NVIDIA PartField inference dependencies; the current dependency path is not treated as commercially cleared. Its README advertises a Czvvd/PartSAM checkpoint. The checkpoint page could not be independently retrieved in this session, so no checkpoint-license approval is claimed.
- [Point-SAM source/setup](https://github.com/zyc00/Point-SAM) uses PyTorch plus custom CUDA/torkit3d/apex dependencies and promptable point masks. [Repository code is MIT](https://github.com/zyc00/Point-SAM/blob/main/LICENSE). The [published checkpoint card](https://huggingface.co/yuchen0187/Point-SAM) labels the weights Apache-2.0 and reports 0.3B/F32 parameters. This is distinct from the repository's code license. Runtime VRAM and full dependency clearance were not measured; an 8 GiB GPU performance guarantee is not made.

Neither heavy model was installed, downloaded or run by default. An administrator can provide a local adapter command, but must explicitly acknowledge reviewed code/checkpoint/dependency licenses. Its connected face proposals are validated for bounds, overlap, connectivity and categories before use. Open/curved regions remain uncertain when no reliable native construction exists. There is no claim of tested PartSAM/Point-SAM segmentation accuracy; the functional fallback is bounded Sol/photo recognition plus local geometry, or conservative geometry when AI is disabled.

## Measured validation

- Focused real CAD, FreeCAD, drawings, source regeneration, mock Sol/provider cap/recovery and API checks: **32 passed** ([JUnit](editable-cad-tests.xml)).
- Existing fitting, geometry, native drawings, photo checks and engineering workflow compatibility: **71 passed** ([JUnit](editable-cad-compatibility.xml)). **103 tests passed in these two runs.** The whole historical suite was not rerun; previously documented unrelated failures are not presented as fixed.
- A final repeat of local-STEP export/reopening and artifact/revision API checks passed **6 tests** ([JUnit](editable-cad-final-export-tests.xml)). These overlap the focused suite and are not added to the distinct test total.
- Production frontend build, existing measurement/submission JavaScript checks, and actual headless Chrome checks passed. The browser exercised WebGL, five selectable parts, all six SVG views, a part PDF, review controls, mobile overflow, and an R2 revision using zero Sol requests and zero provider submissions. The revision reused all six unchanged projection sets and regenerated its title blocks. [Browser evidence](editable-cad-browser-validation.json).

| Synthetic fixture | Parts | Six-view sheets | Export pipeline time | Verification points |
|---|---:|---:|---:|---:|
| table | 5 | 6 | 17.46 s | 2,500 |
| cabinet | 6 | 7 | 17.19 s | 2,500 |
| curved_chair | 6 | 7 | 37.04 s | 2,500 |

Peak aggregate process-tree RSS: **679.9 MiB**. These are single local runs with warmed regeneration caches on a shared 8 GiB host, not controlled performance claims. Their meshes come from known native fixtures, not live Neural4D. Fitting is disabled and verification is limited to 2,500 points to isolate output/reopening behavior; they do not replace the previous high-density optimizer benchmarks. All three assemblies and all separate native/STEP parts were generated and reopened. [Raw measurements](editable-cad-benchmarks.json).

Downloads: [table package](../examples/editable_cad_pipeline/table_package.zip), [cabinet package](../examples/editable_cad_pipeline/cabinet_package.zip), [curved chair package](../examples/editable_cad_pipeline/curved_chair_package.zip), [sample FCStd](../examples/editable_cad_pipeline/assembly.FCStd), [sample manufacturing PDF](../examples/editable_cad_pipeline/manufacturing_drawings.pdf).

## Running and limits

Use the original project's `.venv`, then restart the backend and workers to load the updated source. Existing `.env`, credentials and databases are preserved. Configure `FREECAD_PYTHON` if FreeCAD is installed elsewhere. If FreeCAD is missing, FCStd is explicitly unavailable; no placeholder file is emitted. The full test on this host used the installed FreeCAD.

```powershell
.\.venv\Scripts\python.exe scripts/benchmark_editable_delivery.py
.\.venv\Scripts\python.exe -m pytest tests/test_editable_cad_delivery.py
```

This is a tested development implementation requiring designer/manufacturing review. It does not establish exact photo reconstruction, hidden construction, joint design, material identity, load capacity, arbitrary sculpted/upholstered part recognition or automatic multi-patch CAD reconstruction. Drawing dimensions currently emphasize verified native envelopes rather than fully engineered hole/joint/feature manufacturing dimensions. Live AI/provider generation and specialized model inference were not exercised, so no paid requests were spent to assert their real-image accuracy.

The complete changed-source inventory is in [editable-cad-source-inventory.txt](editable-cad-source-inventory.txt). The hash-checked deployment manifest is `editable-cad-upgrade-manifest.json`; applying it verifies original hashes and preserves backups before copying.
