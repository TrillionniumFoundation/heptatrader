# Train V2: captured original MODEL REQUEST denominator, not trading OPE

Date: 2026-10-12 (+08). Isolated research-only branch stacked on
the read-only original post-model re-quote PR #230 (1fd5db6).
No production PAPER, IB, Execution, Laya, original Trial3, model inference,
future trial, trading order, Root or Artifact Owner was changed.

## The actual inferential error being closed

The original post-model quote audit evaluated **completed verified LLM
returns only**. A sample restricted to completed model responses is not
the denominator of even the originally dispatched model REQUESTS:
timeouts, failed/unfinished responses and missing original DECISION
would silently disappear. Nor does the set of all logged model REQUESTS
establish the original executable risk-policy opportunity population,
unlogged gate vetoes, deterministic actor support or a true logged
BUY/SELL/HOLD action-selection propensity.

Train V2 must never call an incomplete model graph a successful HOLD,
NO_TRADE, zero-profit label or positive candidate. The old 1,675
fresh-quote count was only a time upper bound conditional on returned
models, not a brokerage execution or policy admission statistic.

## Implementation

- New request_denominator.py reuses the unchanged original
  hepta_decision_attribution local hash-chain scanner, pinned exact
  release manifests, and the original model REQUEST/RESPONSE/DECISION
  verifier. It enumerates every captured REQUEST identity, plus
  separately observed RESPONSE/DECISION orphan groups.
- A complete triple is accepted only if the original _graph_decision
  verifies exact source, sequence, response and model schema.
  Missing/invalid triples retain NO_RESPONSE_NO_DECISION, NO_RESPONSE,
  NO_DECISION or FULL_GRAPH_UNQUALIFIED; none becomes an action.
- The original-model attempts and the post-model as-of quote analysis
  use the SAME in-process, original-source-prefix scanner cache. This
  avoids mixing a captured REQUEST snapshot with later appended DECISION
  bytes inside one result. Original quote sources are read as a distinct
  locally authenticated sealed prefix and the latest unsealed segment
  remains excluded.
- Original release SHA identities, request/return counts, all missing
  reasons, quote-source coverage and original returned action subset
  must reconcile exactly. Missing manifests, duplicate requests across
  releases, overlapping count tricks (including negative/boolean
  counters), fake positive policy authority and spurious action counts
  fail closed.
- The report explicitly distinguishes the number of captured model
  REQUESTS (a lower bound), valid returned model results, missing
  original DECISION, fresh-quote upper bounds, quote unavailable and
  quote-archive UNKNOWN. It never claims all policy opportunities,
  canonical model selection propensity, economic alpha or a fully
  captured risk/OMS admission population.

## Two actual immutable Pocket4 snapshots

All counts are read-only inspection results from PRIVATE original EUR/USD
PAPER decision-attribution release manifests and original Laya quote
segments. The source is actively growing, so these snapshots must not
be interchanged or overwritten.

Initial v1:
- Captured original model REQUEST: 1,876
- Fully verified model return: 1,873
- REQUEST with RESPONSE but no DECISION: 3
- Qualifying fresh quote at model return, retrospective upper bound: 1,690
- No fresh quote at model return: 181
- Model return outside complete sealed quote coverage: 2

Final code-bound v2:
- Captured original model REQUEST: **1,879**
- Fully verified model return: **1,876**
- REQUEST with RESPONSE but no DECISION: **3**
- Qualifying fresh quote at model return, retrospective upper bound: **1,690**
- No fresh quote at model return: **181**
- Model return outside sealed quote coverage (UNKNOWN): **5**
- Valid original EUR/USD releases: 2 (REQUEST counts 1,859 and 20,
  complete model return counts 1,856 and 20).
- Original sealed numeric source segments: 253; newest incomplete
  segment excluded; 186,512,850 original locally authenticated bytes.

The additional 3 verified returned models between v1 and v2 are
OUTSIDE the source archive window, so the fresh-quote count does not
change. Those 5 observations are UNKNOWN, not source failure/negative
financial outcomes and not evidence of a repaired future window.

Final immutable private evidence:
~/.local/state/heptatrader/operator-evidence/train-v2-request-denominator-20261012/captured-original-request-envelope-codefinal-v2.json

Final v2 SHA256:
ee8e4ecc5f2268399cd52e995a793b74970180e8d27700314299b99fe9313e3f

Both existing v1 and v2 are append-free, create-once receipts; no older
file was modified. A re-run must choose a new receipt name, preserving
the exact original source/decision prefix identity.

## Gates still HOLD

The recorded original REQUEST is not the entire trading opportunity.
Every risk veto, no-model dispatch, absent quote, OMS reject, timing
failure, original PREPARED identity and original broker fill must be
included by a FUTURE newly authorized producer/evaluator before
calling the data a complete decision population. Model output
confidence is not a selection propensity.

A post-model newer quote is NOT an input to the old opaque LLM model,
so no old BUY/SELL can be retroactively promoted as a decision of
a new quote-as-of policy. No actual after-cost earnings, slippage,
DatasetSnapshotV3, root-registered independent actor or signed
CURRENT Artifact Owner selection has been demonstrated by this audit.
No original test or historical negative outcome can be rewritten.

Next product work: freeze a distinct NEW forward policy with complete
original action/veto/no-model enumeration, an explicit ABSTAIN arm and
truthful deterministic/stochastic selection; enroll independent
product-root principals; sign decision and exact native witness ACK
before original quote and PREPARED deadlines. Evaluate future outcomes
with original commissions, execution slippage and no-trade benchmark.
