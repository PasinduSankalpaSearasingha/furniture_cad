# Available output when corrections stop

Furniture generation retains every complete CAD candidate that passes closed-solid,
collision, supplied-dimension and edit-scope checks. Connection gaps can remain in
a draft, but are listed as unresolved issues. Nothing is silently omitted from a
candidate to make it pass.

When the per-furniture spending allowance, provider credit limit, or bounded CAD
repair allowance stops further corrections, the workflow selects the latest
connected candidate if one exists; otherwise it selects the latest exportable
candidate. STEP, GLB, STL and review drawings remain downloadable. The workflow
finishes as `REVIEW_REQUIRED`, with `error: null` and an explicitly unreviewed
visual assessment. A disconnected assembly remains a draft with listed blockers.

The retained-output policy also applies when the final comparison-sheet review
cannot run because of a credit or spending limit. No replacement paid review is
submitted. Existing saved review responses and export checkpoints remain reusable.

If no exportable CAD has been built, the result contains `available_output` with
the saved reference links, supplied measurements, and any completed dimension
extraction. The UI provides a downloadable JSON report and keeps an earlier saved
CAD model visible when available. It explicitly identifies that model as a previous
version. It does not fabricate CAD or present failed geometry as a valid model.

Credit/spending stops before CAD use `BUDGET_REACHED` with no error; exhausted
geometry repairs without a usable candidate return the evidence report as
`REVIEW_REQUIRED`. These are terminal states and do not trigger automatic retries.
The original cause remains in `available_output.stop_reason`. Integrity, access,
invalid credentials and unrelated system failures remain errors.

Billing-related 429 responses are classified as `AI_CREDIT_LIMIT`, including
`insufficient_quota`, `credit_balance_exhausted`, organization/project spend limits
and organization usage limits. They stop model fallback. This follows the
[official OpenAI error documentation](https://developers.openai.com/api/docs/guides/error-codes):
billing limits and request-rate limits require different recovery actions.

## Recover an older stopped workflow locally

```powershell
.venv\Scripts\python.exe scripts/recover_available_cad.py --workflow WORKFLOW_ID
```

An optional `--patch path/to/repair_patch.json` applies a locally reviewed
`EngineeringRepairPatch` before the normal whole-model checks. Source input and
checkpoint hashes are verified. Recovery creates a new same-project draft revision
and preserves the source workflow. Repeating the same recovery reuses its receipt.
It never initializes an AI client or submits a final AI review; visual review stays
explicitly unaccepted.

## Verification

The focused suite covers provider credit limits without fallback, budget checks,
exhausted repairs with usable disconnected geometry, final-review stops, report-only
output, previous-model retention, and checkpoint integrity. Provider behavior uses
synthetic HTTP responses; these tests spend no API credits.

Verified on 2026-10-28: 42 focused checks, 55 regression checks, and two additional
checks passed (99 total). Three intercepted production-browser scenarios passed:
report-only downloads, disconnected CAD draft downloads, and retention of a
previous saved model. All three also passed the mobile-width check. Results:
[focused](available-output-test-results.xml),
[regression](available-output-regression-results.xml),
[additional](available-output-additional-results.xml).

```powershell
.venv\Scripts\python.exe -m pytest tests/test_furniture_cost_control.py tests/test_final_correction.py -q
```
