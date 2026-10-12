# Train V2: original opaque LLM inference latency versus IB source freshness

Date: 2026-10-12 (Pocket4 UTC+08). An independent, ISOLATED,
READ-ONLY experimental branch stacked on the clean unified native
qualification source at dd0bfc9. No original PAPER trading driver, OMS,
Laya trainer, policy model, original quote collector, fixed-old trials,
product Root/Owner, native LedgerWriter or market execution is modified.

## Proven source-time architectural bottleneck

The frozen original heptatrader PAPER decision-attribution release roots
contain three exact real deployed production manifests (EUR/USD changed
and unchanged profiles, and BTC/USD economics). The original model uses
a remote opaque LLM that returns a single BUY/SELL/HOLD JSON and its
scalar confidence. Its true action-sampling policy is not observable.

The new original_model_timing.py uses the UNMODIFIED independently
validated original release manifests, pinned runtime files,
local hash-chained REQUEST/RESPONSE/DECISION source, and actual
original graph verifier. It requires every complete triple to retain
exact identity and causal ordering; duplicate requests, malformed
timestamps, future source timestamps or tampered original responses
fail closed. Incomplete graph parts remain UNKNOWN rather than HOLD.
It exports only aggregate latency counts and SHA256 source fingerprints,
never the model prompt, answer, account number or model rationale.

The measurement begins at the captured final authoritative quote
timestamp in the original model input, not at a reconstructed quote
arrival or invented broker snapshot. For an IB quote observed at T,
the theoretical *latest* possible validity is T+5000ms; the actual
IB stale_after_ms may be EARLIER. Independent producer Ed25519 signing,
Native LedgerWriter/Witness ACK, final source deadline and broker
PREPARED each consume MORE time, none of which this original journal
attests as included. Consequently a completed model response
within 5000ms of T is only an optimistic research timing candidate.

## Exact real Pocket4 final source snapshot

Actual write-once source/test-code-bound receipt:

~/.local/state/heptatrader/operator-evidence/train-v2-opaque-timing-20261012/observed-original-latency-sourcebound-v3.json

SHA256:
99d9972c485efe9e84a1aadca5e6105e70940c130c95520666c3dcfc2bb09616

The immutable prefix readback observed 5,135 completed original
request/response/decision model graphs:
- 1,835 EUR/USD and 3,300 BTC/USD model-return graphs.
- Model REQUEST to DECISION: P50 6,377ms, P95 12,322ms.
- Source quote timestamp to DECISION: P50 7,624ms,
  P95 13,567ms.
- Source input quote older than 5 seconds already at REQUEST: ZERO.
- Source quote over optimistic 5s bound by RESPONSE: 5,061.
- Source quote over optimistic 5s bound by DECISION: 5,061.
- At most 74 / 5,135 (about 1.44%) of observed model-return
  graphs could possibly fit inside that optimistic full quote lifetime,
  BEFORE allowing independent signing, native witness fsync or
  PREPARED admission. It is NOT a measured admission success rate.
- Historical original missing/unknown parts at this instant:
  17 missing DECISION and 2 missing RESPONSE.
- Original OPE eligible trade policy rows: 0; confidence is
  NOT a true per-action behavior selection propensity.
- Broker requests/orders, model inference from this audit: 0.

An earlier independent static/policy-gate census showed 262 request-
time possible static open candidates, and 120 same-original model
PREPARED bindings at an earlier snapshot. Those are DIFFERENT clocks
and populations and MUST NOT be divided into the 5,135 model timing
denominator as a fill or risk-veto rate.

The observed count may increase as the installed original journal
appends. Each immutable receipt pins its OWN complete segment prefix
fingerprint; previous receipts are retained and not rewritten.

## Architecture decision this supports

It is not viable to put the unchanged ~6 second P50 opaque LLM into
a fresh quote's at-most-5-second source trust window, then wait for
signing, fsync, risk and broker PREPARED. Prewarming source-specific
model calls cannot make the ORIGINAL future quote knowable earlier.

The recommended NEW-POLICY design should be a genuinely preregistered
asynchronous model forecast produced from EARLIER causally available
input. A separate fast Numeric/Value or deterministic no-trade/risk
gate receives a newly authoritative quote and MAY use the frozen
older forecast as an input only when its independently declared horizon,
age, feature/policy identity, confidence calibration and risk controls
are proven. A forecast from the old LLM is not its behavior propensity.
The new architecture needs a NEW policy identity, matched Train/Serve
features, independent source timestamps, signed original candidate
support and genuine time-before-PREPARED Native Witness ACK.

No provider logprobs or sampling probability is present for the
original opaque LLM, so the true original off-policy evaluation
population remains nonadmissible. Fixed NO_TRADE laboratory
ProbabilityQ32::ONE is not transferable to this model.

## Next hard acceptance

1. Pre-register the new offline-forecast/fast-action boundary and
   exact input/output, source freshness and expiration profiles.
2. Demonstrate with genuinely NEW PAPER source that signed forecast
   was precomputed from earlier inputs; the fast final decision and
   durable witness occur before the original source cutoff. Old source
   cannot be copied into a new prospective outcome.
3. Obtain a complete original action opportunity and veto/discard
   census; know the actual behavior-policy propensity or abstain.
4. Then independent financial outcomes, terminal costs, account
   reconciliation, reward completeness and after-cost advantage over
   no-trade. No model release before canonical Owner/withdrawal CURRENT.
5. Never modify the current autonomous PAPER trader, old Trial3,
   Native NO_TRADE original record or frozen failed windows to achieve
   a passing test.

## Reproduction

Run the focused original_model_timing tests, then the existing
historical and core Python suites on the exact branch source tree.
All tests use synthetic complete/incomplete/tampered original-graph
fixtures; real source inspection is performed separately against
private original RELEASE manifests with the official original hash
chain and runtime SHA check.

## Exact final source-bound verification

The source-bound v3 original readback includes EUR/USD 1,835 model
outputs, of which 38 complete within the optimistic 5000ms source
timestamp budget, and BTC/USD 3,300, of which 36 complete within it.
ZERO captured source quotes were already older than 5000ms when the
original model request began. 5,061 quotes crossed that bound by the
model RESPONSE itself. The primary bottleneck is during original
opaque model inference, not solely late original quote acquisition.

Every complete original REQUEST/RESPONSE/DECISION graph must match its
original manifest/source-hashed runtime and immutable journal prefix,
with strictly earlier original input timestamps. Missing model source
records remain UNKNOWN. Percentiles use integer-millisecond
nearest-rank, not a statistical SLO or provider reliability guarantee.

The final private receipt binds exact source code SHA256:
- Original timing source:
  3562f4a772a7be422f8030a6a35dc3c1418341f5bb1b9427257332bc9ee7513e
- Adversarial timing test source:
  b5cdeb8ad666a43f8c93fcf40528e97dfa1e01cf0ce4e561ee55c18c21dcb385

Final pocket4 exact-tree results:
- Focused original timing adversarial cases: 19 passing.
- Combined Python and historical research pytest:
  1,967 passing, 23 preexisting skips, 824 passing subtests.
- Source/build/ownership partition: 106 passing, zero failures.
- Zero source journal rewrites, model requests, new broker quote calls,
  orders, root enrollments, promotion or installed runtime changes.

Execution cumulative restarts increased from 294 to 296 while this
isolated read-only audit ran; no causal attribution or fix is claimed.
Final observed EUR/USD position was flat and strategy net PnL was
negative; account_complete stayed false. No account-wide complete
financial result, product policy propensity or canonical owner approval
is claimed from the latency evidence.
