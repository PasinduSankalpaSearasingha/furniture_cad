# Furniture AI spending

New whole-furniture generations default to a USD 2.00 AI spending allowance,
configured by `AI_FURNITURE_BUDGET_USD`. The linked photo analysis counts toward
that allowance. A separately requested component edit starts its own allowance;
retries of the same workflow keep its existing spend and reservations. Older
workflow inputs and their price history are not rewritten.

New generations use GPT-6.1 Sol for furniture planning, actual-CAD visual review,
photo analysis and dimension interpretation. Historical workflows retain their
frozen model records and prices. A complete
plan is requested once. Geometry and visual repairs request
`EngineeringRepairPatch`: only replacement components, changed estimates and
explicit inventory corrections. The server merges these into the saved plan,
retains omitted declarations, protects supplied dimensions, and validates the
entire resulting CAD assembly. Terminal tool requests carry one strict function
schema, without duplicating it in an unused final-text schema.

Existing deterministic geometry repairs and saved-candidate revalidation run
before another paid plan. Accepted tool arguments are used directly, without an
extra model transcription. Saved provider responses/checkpoints are reused;
STEP, DXF, GLB, STL, PDF and SVG exports remain local operations.

Before each new generation request, the provider input-token count endpoint
counts its actual instructions, history, images and schemas. The application
reserves the highest applicable Standard input/cache-write price, 1,024 tokens
of formatting headroom and the entire output allowance. Counts do not generate
a model completion. The database reservation and budget check occur in the
active workflow transaction. Linked analysis must belong to the same project.

New requests explicitly select Standard processing and 30-minute cache writes.
Prices are frozen in workflow inputs from official OpenAI pricing checked on
2026-10-27. Ordinary input, cached reads, cache writes and output are counted
once each; reasoning tokens are already included in output. Twelve-hour writes
and long-context rates are handled when reading reported usage. Unknown model
prices or uncertain usage block new paid calls. A lost submission keeps its
reservation across process restarts; polling/replaying a saved response is
allowed without reserving another generation. A definitively rejected request
without a response ID releases its reservation.

The limit is an application control using this verified rate snapshot, not an
OpenAI account billing limit. Update the verified rates when provider prices
change. Hosting, tax, other services and independently requested new generations
are outside this allowance. A response whose usage exceeds its reservation or
omits required accounting blocks subsequent calls.

When the next request cannot fit, the available CAD is exported as a draft with
the spending-stop note. Every complete plan that builds valid closed solids,
passes interference checks and retains supplied dimensions is saved in a
hash-checked `engineering_exportable_draft_*` checkpoint before AI acceptance.
If spending stops during planning/repair before a final accepted plan exists,
the latest connected saved design is preferred; otherwise the latest built
design is exported with disconnected groups listed as unresolved blockers.
All its components and their positions remain unchanged. Invalid solids are
never substituted, silently omitted or labelled valid.

A visual review that could not run is explicitly marked unverified, never
accepted. `engineering_budget_draft` freezes the retained plan identity and
remaining problems. The UI keeps all export formats available, lists known
layout problems, and the whole-furniture drawing says `STATUS: BUDGET DRAFT`.
The same findings are retained in the geometry and validation metadata. If no
exportable geometry exists yet, the workflow ends as `BUDGET_REACHED` with an
explanatory message and no fabricated files.
Budget exhaustion is not automatically retried. A temporary token-count outage
can be resumed explicitly without repeating completed stages. The workflow API
exposes estimated spend, allowance, outstanding reservations and whether linked
analysis is included; the files view displays the cost and any draft-stop note.

Validation uses the real OpenAI SDK with mocked HTTP and real CadQuery exports;
it does not spend API credits. It covers one full plan followed by one small
patch, input/schema/image counting, saved-response reuse, interrupted submissions,
cache accounting, dimension retention, draft export at a spending stop, and a
clear stopped state when no valid model exists.

References: [OpenAI pricing](https://developers.openai.com/api/docs/pricing),
[per-run spending control](https://developers.openai.com/cookbook/articles/per_run_spending_controller_responses_api),
[cost optimization](https://developers.openai.com/api/docs/guides/cost-optimization).
