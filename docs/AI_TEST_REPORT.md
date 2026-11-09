# Collaborative-agent verification — 18 October 2026

Agent-extension milestone command: `.venv/Scripts/python.exe -m pytest -q --disable-warnings --tb=short --junitxml=docs/test-results.xml`. Later complete-suite results are maintained in [TEST_REPORT.md](TEST_REPORT.md).

At that milestone: **69 passed, 1 skipped, 0 failed, 35.01 seconds.** The skipped test is the explicit live Vision integration test. The 219 warnings originate from dependency deprecations/configuration; no warnings are reported as successful engineering checks. All 41 earlier deterministic/API tests remain in the suite. JUnit evidence in test-results.xml reflects the latest complete run.

Provider tests use the actual OpenAI 3.12.0 SDK with `httpx2.MockTransport`, never live network calls. They verify strict Responses JSON schemas, vision-input packaging, Pydantic parsing, refusals, incomplete/malformed responses, account-checked model fallback, timeout behavior, strict read-only function calling, unknown-tool and argument injection rejection, tool-round limits, Responses image_generation request/result handling, unsupported template handling, required-measurement restoration, approved/compatible joinery lookup and retained deterministic blockers. No generated code execution or release tool is present.

The later [joinery request fix](AI_REQUEST_FIX.md) adds a real-SDK regression for two successive stateless tool rounds with reasoning items and commentary phases. It verifies API field aliases and omission of unset SDK defaults. Three transport cases check safe HTTP 400/422/500 diagnostics without retaining free-text provider errors; a workflow case verifies diagnostic persistence and retry using the completed Vision/Design checkpoints. These additional tests make no live requests.

Integration tests use temporary SQLite databases, authenticated FastAPI TestClient requests, explicit eager execution, synthetic upstream responses and real CadQuery exports. They exercise private image/workflow access, invalid raster rejection, image integrity, analysis reuse, frozen/idempotent requests even after catalog changes, checkpoint resume/corruption, bounded correction/retry loops, AI provenance rejection, complete measured input requirements, designer-only verification, specification/hash binding, legacy-route verification enforcement, CAD queue failure and retry through the existing job API, real generated CAD/documents, immutable validation findings, blocked approval/release, designer rejection, image-tool failure, concept independence, revision-linked STEP previews/presentations and versioned configured price estimates. Package generation rejects cross-revision context mismatches before writing outputs.

The tests deliberately submit a human fixture width of 900 mm after a mocked AI estimate of 700 mm and verify a top-panel length of 864 mm. This demonstrates that supplied designer-verified input controls the actual template. Both values are synthetic test dimensions, not a qualified design.

## Executed offline cabinet demonstration

Command: `python -m scripts.demo_agents --output examples/generated/collaborative_cabinet_demo`.

The reference is a rasterization of the earlier review STEP: a plywood carcass, timber frame and visual glass pane. It is explicitly labelled CAD-derived, not a photograph. Vision/Design/Joinery/Review responses are mocked. The designer account and measurement/evidence submissions are synthetic software fixtures; **no physical measurement or real engineering verification occurred**. The mock provider is not selectable through production HTTP or an environment flag.

Observed workflow: WAITING_FOR_DESIGNER → simulated specification verification → real CAD generation → deterministic validation → mocked post-CAD review preserving blockers → REVIEW_REQUIRED. CAD before verification returned HTTP 409. Approval and release returned HTTP 409. Designer CHANGES_REQUESTED returned HTTP 201. No production package was released. No real OpenAI request occurred.

The delivered demonstration's actual files were independently reopened and checked: **10 valid STEP solids**, named GLB component geometry with metre extents approximately 0.700 × 1.800 × 0.475 (Y-up), a watertight union STL, **13 audited DXFs**, **11 PDF pages**, four XLSX schedule sheets and 30 manifest-hashed package files. Its package manifest and all archived/local file hashes were verified.

Review package SHA-256: `798c225ebfbbd3debc8348e1f164f8949515b807cb69f212844f27ebfc6b473f`.

Manifest SHA-256: `cd0f3fc03b0eadd1947f5aa88c797baa3a40f0a77ca991d94be0ee4ad69e66a3`.

The CAD-derived reference preview was rendered and visually inspected. It is a review visualization with mesh edges, not a photorealistic output. No generated concept/presentation bitmap is offered as evidence of visual fidelity or manufacturing geometry.

## Deployment and limits

SQLite migrations upgraded and passed Alembic metadata checks; the complete migration upgrade/downgrade path is exercised by tests. PostgreSQL offline migration SQL was regenerated successfully. Compose configuration validation passed. The Docker Desktop Linux engine remained stopped, so no Linux container build, live PostgreSQL/Redis/Celery workflow or remote CI execution is claimed.

No backend OpenAI key or live-test flag was configured. Model availability checks are implemented in the worker but could not be performed against the user's account. Live image analysis quality, cross-image consistency, refusal behavior, cost accuracy, image-tool model compatibility, presentation fidelity and production deployment require configured integration evaluation. The separate OpenAI Agents SDK is not integrated; deterministic Python orchestration is used as permitted by the requirements.

Factory profiles, retained glazing, hardware mounting, door motion, structural/stability/glass/machining qualification, complete shop drawings and a real approved benchmark are still missing. Existing blockers deliberately prevent manufacturing approval and release. Tests establish software behavior, not factory readiness.
