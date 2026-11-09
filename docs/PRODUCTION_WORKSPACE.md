# Focused photo-to-CAD workspace

The supported browser flow is upload → analysis → CAD generation → download and
edit. `/` and `/studio` serve the same built React entry point. The sidebar has
three destinations: Upload photo, Model & edit, CAD files. Unused classic
dashboard assets and reference-fitting controls were removed. The standalone
rendering worker and Blender runtime mount were removed from the default
Compose stack. Stored projects, revisions, CAD files and backend compatibility
routes were retained.

## Generation behavior

Uploading a valid photo starts analysis and then displays a measurement review
form based on that photo. Estimated sizes are prefilled and labelled. The user
can change dimensions before selecting **Generate CAD files**. Untouched
suggestions retain their estimated provenance, while changed sizes become user
targets. Saved user measurements remain targets when creating another version.

CAD never starts automatically after analysis or a page reload. Old automatic
continuation intents are discarded. Manual submissions retain their idempotency
keys after uncertain responses so retrying cannot create a duplicate paid job.
Only fields identified for the photo are editable. Cleared inputs are omitted
from the submitted measurements. Invalid sizes, fractional counts and
nonfinite values are rejected before submission; backend validation remains
authoritative. Known values override matched drawings and image estimates.

The browser supports JPG, PNG and WebP up to 25 MB, resizing large images to the
server's bounded raster limits. Unsupported, damaged and excessive uploads
produce errors. Native file selection and drag-and-drop use the same path.
Uploading to an existing project retains previous revisions; **New design**
creates a separate project automatically on upload.

## Editing and files

Editing retains the accepted source model and downloads until the whole edited
package is complete. A failed edit is visible and resumable. A completed edit
changes the displayed mesh, drawing and download targets together. The source
revision and package remain immutable. Inspecting old downloads does not incur
provider charges. Generation and AI edits do.

Primary downloads are ZIP and the assembly STEP, STL and GLB; drawings and part
files are in the CAD files destination. Geometry reports and dimension provenance
are available under details rather than blocking the user flow. The model view
retains front, rear, sides, top, bottom, rotation and component selection.

## Access and deployment

Local workspace mode remains restricted to direct localhost access. Shared
deployments use the same interface with a sign-in gate. Browser session cookies
are HttpOnly, SameSite=Strict, expire with the existing server-side token, and are
Secure when the request scheme is HTTPS. Cookie-authenticated requests reject
foreign Origin and Sec-Fetch-Site values. Logout revokes the stored token and
clears the cookie. Explicit bearer authentication remains available to clients.
Native links, PDFs and 3D asset loaders authenticate with the same cookie.

The Docker build compiles React from its npm lock file. The API process runs as
a nonroot user. Compose uses PostgreSQL, Redis, migrations, API and one CAD
worker. The default stack does not need Blender. The artifact volume must remain
private. Configure HTTPS with trusted proxy forwarding, rate limits, deployment
secrets, persistent backups and a tested restore before external deployment.
The single worker listens to both `celery` and `renders`: the dispatcher also
uses `renders` for resumed CAD workflows that already have a saved job ID.
This queue name does not require a separate rendering process for photo-to-CAD.

The UI shows missing backend configuration before accepting a paid upload.
A configured key can still be expired or invalid; worker errors report rejected
authentication. No paid provider probe runs on page load.
`uv run --no-sync --env-file .env python scripts/check_generation_access.py`
checks the configured models with metadata GET requests only, without printing
the credential. It does not verify generation quota or billing.

## OpenAI background responses

Responses API requests use `background=True` by default. Set
`OPENAI_BACKGROUND_MODE=0` to use foreground requests; `OPENAI_POLL_INTERVAL_SECONDS`
defaults to 2 seconds (allowed range 0.25..10). These settings are forwarded to
both the API and worker by Compose. The direct Image API has no background flag.
CadQuery modelling and exports still execute in the existing backend workers.

The worker persists the provider response ID as soon as submission returns, then
polls that ID until completion. Each HTTP submission/status check has at most a
30 second timeout. `OPENAI_TIMEOUT_SECONDS`, or the stage-specific photo timeout,
bounds the polling period. Three consecutive transient status-check failures or
an exhausted polling period leave a resumable workflow. **Resume saved workflow**
retrieves the same response instead of submitting another generation.
Completed response payloads are stored in private, hashed workflow checkpoints,
so a restarted tool loop can replay its earlier model responses and continue
polling the pending response. These checkpoints are excluded from the browser's
completed-stage list and provider output is not copied into public event logs.
Token usage is updated on the original invocation, avoiding duplicate totals.
An already accepted fallback response takes priority over a new primary request,
and changing the polling interval or foreground setting does not resubmit it.
Workflow retries are explicit; this is not an automatic process-restart service.

`store=False` remains set. OpenAI temporarily retains background response data
for polling, roughly 10 minutes under its documented policy; a locally saved
completed result does not depend on that retrieval window. An expired pending
response stops with an explicit error. If submission loses its connection before
returning an ID, acceptance is unknown and the same workflow refuses to submit
a duplicate. Failed, cancelled and incomplete responses never proceed as
completed CAD input. See the [official background mode documentation](https://developers.openai.com/api/docs/guides/background).

Vite is pinned to 7.3.7 after a Linux audit reported vulnerabilities in the old
build dependency. Unused Tailwind packages were removed. See the
[maintainer advisory](https://github.com/vitejs/vite/security/advisories/GHSA-fx2h-pf6j-xcff).
The container copies the pinned uv binary using the
[official Docker installation method](https://docs.astral.sh/uv/guides/integration/docker/)
and caches locked Python downloads to support retried builds.

## Validation boundaries

Local API tests cover session isolation, cookie protections, token expiry,
logout, unchanged bearer access and real CAD downloads. Browser tests cover
measurement review after reload, a single explicit CAD request, editable estimated
dimensions, invalid counts, native ZIP downloads, mobile layout, provider
authentication failures and shared sign-in. Existing CAD edit integration tests
check real STEP solids, immutable source geometry and rebuilt delivery artifacts.

Browser provider responses are synthetic. No live paid generation or edit is
part of cleanup verification. The full Docker CAD-runtime build could not finish
the external dependency downloads; the frontend stage passed. Docker deployment
must be validated on its target host. See the
[executed test report](PRODUCTION_CLEANUP_TEST_REPORT.md).
This cleanup does not establish physical photo accuracy, structural
safety, manufacturing approval or CAM readiness.
