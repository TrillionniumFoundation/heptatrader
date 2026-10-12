# Phase 4K — real original PAPER model decisions: response vs opening gates

2026-10-12 on Pocket4, stacked on Phase 4J, no execution/model/broker action.
The previous phase closed one actual EUR/USD PAPER research-only shadow
prediction and 5-minute price label. Phase 4K audits the DISTINCT
ORIGINAL running LLM trading policy, not that newly defined CSPRNG policy.

## Exact original runtime and source provenance

The three original PAPER attribution manifests, original fully
pinned Python runtime files and two original journal roots are read
ONLY. Manifest accounting period, PAPER scope, original runtime file
SHA256, original request/response/decision hash chains and exact
model/PREPARED command identity are verified before reporting counts.

The earlier all-action original model graph parser already exists in
sibling Draft PR #200 and was imported byte-for-byte here, leaving
the original PR unmerged and its source unchanged. Its hashes:

- original_decision_population.py:
  101eca70773d78e1cdc67284869bc7ca6a839ee4e57f97b1e629db61995a583c
- test_train_v2_original_decision_population.py:
  bb4f013df48291c651abf86811a9221f81fc46c7481c908880b185644d6c770d

The new original_model_action_gate.py adds an independently tested
original runtime source-pinned STATIC legacy opening precondition
audit and exact same-returned-object to PREPARED identity verification.
It also refuses original request/command identity duplication across
producer processes and historical releases. The exact installed BTC
economics wrapper + its declared policy source must match the
previous audited version; unknown variants fail closed.

## What is actually observed

The INSTALLED original LLM request returns exactly one JSON-selected
BUY, SELL or HOLD with confidence and rationale. It does not disclose
a three-way selection probability law from the actual sampler.
Confidence threshold 0.62 is a directional/predicate condition, NOT
a selected-action probability, not expected PnL, not a confidence
interval and not an OPE importance weight.

At model request time the observable input also contains the model
worker's initial position. This is NOT a final authoritative position
at the end of the model call. The original Python first-stage entry
predicate allows EUR.USD BUY or SELL, but BTC.USD only BUY, above
confidence 0.62. HOLD is never a directional opening request.

The actual execution route also has model-age and changed-position
discards, authoritative fresh quotes, pending/active orders, owner
mutation readiness, session safety, postfilter FX hysteresis/economics
and risk vetoes. Their complete per-opportunity at-dispatch states
are NOT captured in this original model journal. No non-PREPARED
candidate may be relabelled a confirmed veto just because a command
binding is absent.

## Actual source snapshot v4 at 2026-10-12 09:00:14 China (+08)

Three distinct original RELEASE manifests, two EUR.USD profiles and
one BTC.USD original economic profile, were verified.

- Original REQUEST rows: 4,391.
- Original RESPONSE rows: 4,390.
- Fully verified original REQUEST -> RESPONSE -> DECISION graphs: 4,375.
- Missing DECISION: 15; missing RESPONSE: 1. Neither becomes HOLD.
- Exact original emitted action counts: BUY 943; SELL 864; HOLD 2,568.
- Literal recorded model confidences >=0.62: exactly 2,043.
- High-confidence HOLDs: exactly 1,190, an actual counterexample
  to any attempted mapping from confidence to action probability.
- Request-time flat-position plus directional high-confidence model
  outputs and source-pinned instrument grammar: 210 possible STATIC
  first-stage entries: 82 changed FX, 2 unchanged FX, 126 BTC.
- Original model identity linked to a *real original durable PREPARED
  OPEN* across the very same captured journal: 84 (81 changed FX,
  2 unchanged FX, 1 BTC), comprising 39 BUY and 45 SELL.
- 631 original BTC returned outputs were SELL; the base
  BTC opening grammar supports BUY only. Some BTC economics filters
  are separately applied at actual opening time.

**The difference 210 - 84 = 126 is an UNKNOWN source-time execution
gate effect population, NOT 126 proven risk vetoes.** A request-time
model candidate may have become stale, lost position relevance, hit
active/pending order or quote fences, encountered economics admission
or never been considered at the actual branch. Original journal does
not establish which of these happened.

Also, even a perfectly complete count of original model returned
actions is NOT the original policy's entire decision-opportunity
population. Some driver ticks may not dispatch a model at all;
existing asynchronous capture can fail or be dropped. Observed
frequencies do not recover stochastic per-state behavior propensity.

## Security and fail-closed regression

The new test-only adversarial suite rejects changed runtime source,
wrong BTC decorator version, duplicate request identity, response
hash and prepared identity tampering, wrong command side, misplaced
journal ordering, Boolean or nonfinite model request position,
nonflat open request, high-confidence HOLD treated as a trade,
BTC short signal promotion, and confidence immediately below 0.62.

All original prompt text, raw model stdout, rationale, session tokens
and broker account values stay in owner-private source journals. The
summary exposes only hashes and aggregated counts. It creates
neither products nor new trading permissions.

## Unclosed product admission: HOLD

To make the original strategy usable for real on-policy training,
a future authorized original Generator must log the COMPLETE actual
action support and its EXACT sampling law and selected
ProbabilityQ32 before original action is possible. It must include
explicit no-trade/abstain and all filters, truncation and model
selection provenance. Original scalar LLM confidence and an
empirical tally of returned BUY/SELL/HOLD cannot provide this.

Every actual decision opportunity must also be reliably accounted
for in source time, including no-model ticks, discarded slow results,
active orders, risk veto and rejection. Even then, the result must
be signed by product-host-approved independent Generator/Observer
administrators under an independently pinned root, appended and
acknowledged by the native LedgerWriter + separate durable Witness,
and later matched to genuine broker terminal fills/cost-complete PnL
or correctly censored no-trade outcomes.

Only after real signed source facts and valid correction/withdrawal
would a product DatasetSnapshotV3 be frozen, independently
revalidated before artifact use, evaluated unseen after costs and
eligible for Artifact Owner/CURRENT review.

No installed trading service, true behavior policy or native product
trust was changed in this phase. Original OPE-eligible product
DecisionV2 count remains zero.

## Immutable original local evidence

Private Pocket4 evidence root:

~/.local/state/heptatrader/operator-evidence/train-v2-original-action-gate-20261012/

Initial original source readback SHA256:
e5b23b261cc6ca2471e0291f9820897b9087da2cfcc383619ecbaa0ed4de9568

Second original-source receipt SHA256:
760f51f52e527de61f50b7c4f3b5fe4290d1c26ce83006d73727b2e336efd8b5

Pre-final-gate-change source v3 SHA256:
40963baa357a41a3efc6d5b5ff3dc2b1cea0fb5b8703fd9a734be0edad4499bd

Final original-source v4 with exact BTC post-model adapter pin:
a67cb210db0d999ac2be556f6f30563f381b516040e4d6ebff40ef4649528c93

These source counts are snapshots of a currently running append-only
local observer journal; exact observed counts may grow later. All
output audits remain read-only and mark product acceptance HOLD.

## Final original-PAPER source qualification and regression evidence

A separate immutable, create-only private composite receipt checks the
exact original Git/Cargo pinned source, both source parser/test file
hashes reused from sibling Draft PR #200, all three original
release-scoped request/response/model/command observations, static
gate and true PREPARED counts, source audit failures, six live unit
liveness statuses and the exact final Python regression.

Final private composite receipt:
original-policy-static-gate-final-qualification-v1.json

SHA256:
bfe05ee2693b261dcfd8c6c9f6703eb2fd5547bca8c73b70ade73910155d05e2

Full HeptaTrader core + original/historical research Python regression
for this final source revision: 1,703 passed, 21 skipped, 741 subtests
passed, zero failures. This includes the earlier sibling all-action
source parser tests and the new source-bound gate/policy adversarial
tests. The actual test output is immutable private evidence in
all-core-historical-python-final-v2.log; no original model or broker
was invoked by test.

This qualification is observability/source lineage ONLY and retains
zero original OPE eligible rows. It cannot grant production
LearningLedger, DatasetSnapshotV3, trusted independent Observer,
true sampled original action probabilities, order execution or
Artifact Owner.
