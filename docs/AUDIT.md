# Source audit

All original application modules, the example and both tests were inspected before changes.

Working: rectangular cabinet and conceptual glazed frame, stable part labels, explicit blank dimensions, matched demonstration bores, STEP/GLB exports, CSV and front DXF. The two original tests are retained.

Problems: blocking CAD in HTTP requests; public UUID downloads; no persistent jobs or exception logging; front DXF duplicates dimension formulas; no finite-number validation; no material master, hardware, pricing, revision or approval identity. Back attachment, frame joints, glass retention and hardware geometry are absent. Demo bores have no approved engineering basis. Tests do not reopen exports, check dimensions, access control or release blocking. Dependencies were ranges and CAD/test packages were absent from the initial interpreter.

Implementation scope: one cabinet template, registry-driven CAD and review documents, explicit material/blank provenance, configurable paired dowel features, validation, database-backed private revision/job workflow, and deployment scaffolding. Production release is deliberately blocked while manufacturing drawings, real benchmark evidence and engineering systems remain incomplete. No factory data was supplied; none is represented as verified.
