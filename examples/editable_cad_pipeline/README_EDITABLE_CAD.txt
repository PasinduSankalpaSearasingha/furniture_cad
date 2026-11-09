DRAFT - MANUFACTURING REVIEW REQUIRED

assembly.step and per-part STEP contain native B-Rep geometry, not mesh facets.
Part STEP/drawings use the local stock frame. local_to_world matrices restore the assembly frame.
Plain untrimmed axis-aligned boxes use native Sketcher + PartDesign Pad histories.
Complex curves and contact-trimmed parts use packaged FeaturePython objects.
Edit shared Parameters in FreeCAD, or ConstructionJSON on complex parts for curves/profiles/control points.
The proxy rebuilds the same PhotoDesign using the installed project's .venv.
Keep the source project and .venv available; set Parameters.ProjectRoot/PythonExecutable after moving them.
Place cad/furniture_cad_proxy.py on FreeCAD's Python path before opening assembly.FCStd.
Alternatively run cad/open_project.FCMacro. Recompute must finish without LastError before export.
Confirmed dimensions are read-only in the property editor; unlocking requires deliberate designer action.
No model weights are bundled. Local segmentation code AND weights require a license review.
Drawing curves use exact native B-Rep HLR. Dimensions are measured envelopes, not cutting instructions.
Unseen geometry, materials, joints, loads, tolerances, grain and stock allowances remain unverified.
Six-side drawing PDFs/SVGs/DXFs use the stated first/third angle convention; DXF model space is 1:1 mm.
