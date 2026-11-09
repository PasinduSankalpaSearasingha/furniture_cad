# Complete furniture models for all-angle review

Engineering CAD plans now include rear, side and underside construction instead
of stopping at the photographed facade. Closed storage receives a local
completion pass for missing rear panels. The saved sideboard's named facade zones
also support estimated partitions, shelves, drawer boxes and rear base members.
Added parts have separate identifiers and `ASSUMED_360_COMPLETION` evidence.
Intentional open-back furniture remains open.

New uploads use the `COMPLETE_360_V1` reconstruction policy. Before packaging,
the workflow checks separate closed solids, assembly contacts, interference,
supplied overall dimensions and sampled rear coverage. AI visual review receives
the reference plus seven actual CAD views: isometric, front, rear, left, right,
top and bottom. The check for a closed rear is a 35-point occupancy sample,
not an exhaustive proof of every exterior surface or internal component.
Its vertical sampling range uses the actual broad cabinet floor and underside
of the top. Open space between raised legs is excluded from the enclosure;
missing or partial backs above the floor still fail. The recorded
`rear_sample_z_range_mm` makes that range inspectable.

For an existing cabinet, select **3D workspace → Complete 360° model**. The
`POST /api/projects/{id}/complete-360` endpoint takes `source_workflow_id`, checks
the frozen source hashes and creates a new workflow and immutable revision.
It preserves the source exterior plan and dimension constraints and exports a
new package locally, without initializing an AI client. Repeated submissions
for the same source return the existing completion workflow. Local completion
keeps visual approval false; it does not claim a new AI review.

The viewer supports front/back/left/right/top/bottom/isometric views and a
**360° rotation** switch. **Model views** displays all seven exported images.
Each image and interactive mesh comes from reopened STEP solids. Original
revisions and downloads remain available.

Hidden construction is an estimate, rather than a recovered measurement or
verified manufacturing design. Locally added dimensions are labeled
`ASSUMED_COMPLETION` / **Construction estimate**. The existing manufacturing
review blockers remain in force.

Regression coverage is in `tests/test_storage_completion.py`: preserving the
13-part sideboard exterior while adding 24 solids, complete rear coverage,
connected assembly, idempotency, intentional open-back furniture, source hash
integrity, zero-AI local completion, inherited evidence and seven-view review
for new uploads. Read-only browser checks use
`scripts/check_full_360_browser.py --project PROJECT_ID`.

Verified on 23 October 2026: the `test1` sideboard now uses job
`8471d56d-31c8-436a-9ee6-8696ff313a82`, with 37 separate closed solids at
**3000 × 500 × 700 mm**. All 13 original parts passed independent STEP surface,
volume and envelope comparisons against the new exports. Original workflow
data, package hashes and the protected `chair11` project are unchanged.
The completion used zero input/output tokens and generated no AI images.

The relevant regression suites passed 66 distinct tests (61 CAD, upload,
dimension and project-scope checks plus five storage-completion checks).
The production frontend build and desktop/mobile browser checks passed,
including all camera views, automatic rotation, seven exported images and no
horizontal overflow or JavaScript errors. Download archive CRC and all part
STEP hashes passed. Evidence: `outputs/full-360-repair/verification.json`,
`outputs/full-360-repair/browser/audit.json`, the test XML files in that directory,
and `outputs/engineering-delivery-audit/5e557683-0cd9-4046-aa68-570ab769cd23/audit.json`.
