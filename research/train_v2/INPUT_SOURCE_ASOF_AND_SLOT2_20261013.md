# Train V2: input/timer as-of cutoff and frozen 3x4 cohort follow-through

Date: 2026-10-13 UTC+8. Independent research-only branch based on
fix/train-v2-independent-unit-20261013 at 58675c2.

## Correctness issue

The existing cohort reviewer correctly checked every persisted price outcome
observed_at_ms <= the audit's as_of_ms. It did not check that a persisted
pre-outcome input and its real systemd one-shot completion ALSO occurred
no later than as_of_ms. After an original price source file is appended,
a retrospective review with a cutoff before that capture could incorrectly
report that the future original input had already been captured.

## Change

After the original source input/timer receipt is independently validated,
price_only_cohort_audit requires both the original decision timestamp and the
recorded worker-finished timestamp to be integers with

    decision_at_ms <= worker_finished_at_ms <= audit_as_of_ms

A purported future input fails with COHORT_AUDIT_INPUT_SEALED_IN_FUTURE.
No downstream cost, causal history, source TTL, model, account or trade
policy is changed.

Two new adversarial tests cover future original input and future worker
completion with an as-of cutoff earlier than the receipt. Existing tests
preserve the original per-slot receipt semantics. These are local time fences,
not independent broker clock signatures.

## Authentic observed research progression

Frozen campaign ID:
0b0f40d0013199eb43fdb97886744b80e29f1479955c695f7d239d07bebf1ece

This is the unchanged 3 decision x 4 correlated horizon PRICE-ONLY cohort.
The prior separate 00:20 manually missed campaign remains separate and missed.

1. The 02:10:05 pre-registered slot 1 original input/timer had been sealed
   with no model inference or broker orders. Its 30-minute outcome was
   previously WAIT pending natural original source-segment sealing.
2. At approximately 03:24 on October 13, exact strict read-only preflight
   for slot 1 / 30m returned READY_ONLY_NO_WRITE. A single invocation of
   the existing original strict observer committed the immutable 30m price
   receipt at 03:24:54. Its original MID move was
   -1.926670042073096965270652000 bps. SHA256:
   2772f901a80ef0d395d959cffa5ec0c2b9f21fd673959d5a13103856a83006a4
3. Slot 1 / 60m is already mature but remains
   WAIT_END_OF_ORIGINAL_SEALED_COVERAGE. No rotation, interpolation,
   backfill or synthetic target is allowed.
4. The third scheduled slot, anchored 03:30, was NOT fired early.
   Actual pre-existing systemd one-shot launched at 03:30:05 UTC+8, exited
   successfully and sealed a NEW genuine original pre-outcome source receipt.
   Original source available at ms timestamp 1791833404620; decision
   at 1791833405106, latency 486ms, original valid until 1791833409042.
   No model inference, order or financial reward is claimed.
   Immutable slot-02 input SHA256:
   3d1fd5515abcd6c7b92f5a81eb40c481a5b9352a58db4168a31f2e8a65775ec9
5. Independent byte-level replay reconstructed the exact 658,523-byte
   original source prefix, verified original chain head, six causal receipts,
   decision and local TTL. Replay SHA256:
   143bead9a1dc49762152959bc19539be47c4267bd65f1b9d80a3b70305c4fd36
6. The full immutable 12-cell audit after the third input captured all three
   original source slots: 7 VERIFIED_OBSERVED_ORIGINAL_PRICE_ONLY, 1
   WAIT_ORIGINAL_SEALED_ENDPOINT_COVERAGE and 4 PENDING_NOT_MATURE.
   Audit SHA256:
   4020f69ff89c9e4089ba85c28f44502b730ff42dbb6b4a6198a30972894b4b41

A price-only horizon is never an independent financial trade sample.
The economic evidence, prediction/trading authority, Owner and strategy
promotion remain 0/HOLD. The current frozen 10.9133557 bps stressed
hypothetical cost-plus-margin hurdle is NOT a claimed executable net PnL.

## Exact source/test evidence locations

Private root:
~/.local/state/heptatrader/operator-evidence/train-v2-input-asof-fix-20261013

Frozen, untouched source campaign:
~/.local/state/heptatrader/operator-evidence/train-v2-fixedslot-20261013/timed-campaign-v1

No existing evidence was overwritten, no active quote service or user
systemd timer was modified, and this branch is NOT deployed as a producer
or evaluator. This is a research-only input as-of and original-price
custody update.

## Remaining actual gates

- Slot 1 60m price: wait for naturally sealed original source and
  only then perform exactly one READY-gated original strict observer.
- Slot 2 5/15/30/60m: never score before their original due time plus
  grace and naturally sealed source coverage. Retain CENSORED where needed.
- Independently tie an ACTUAL original model prediction and native
  LedgerWriter proposal to REQUEST/PREPARED, genuine behavior propensity,
  independent signatures and durable witness before treating it as trading.
- Real broker execution/fees, defensible slippage, unseen after-cost
  evaluation, and separate Owner/CURRENT must gate every model promotion.


## Exact full regression on pinned Phase 4 research tree

- New focused input/correlation price-cohort suite: 21 tests passing.
- Historical training: 400 tests passed (7 pre-existing skips).
- Core Python: 2,444 tests passed (23 pre-existing skips).
- Source/build/ownership/documentation: 106 tests passed.
- No installation of this patch into any original scheduled price timer.
- All input/output and replay hashes above derive from the previously
  frozen original PAPER source and immutable prior campaign.


## Additional real 03:38 UTC+8 post-maturity as-of review

After slot 2's genuine 5-minute deadline, the strict observer returned
WAIT_INPUT_ORIGINAL_SEGMENT_SEAL (not a label). Slot 1's mature 60m
remained WAIT_END_OF_ORIGINAL_SEALED_COVERAGE. No forced source
rotation, pending outcome write, or failure-to-zero accounting occurred.

A NEW immutable read-only 3x4 as-of receipt confirmed 7 VERIFIED
price-only labels, 1 matured WAIT original slot 1 endpoint,
1 matured WAIT original slot 2 input segment and 3 PENDING future horizons.
All THREE distinct original source decisions were captured, but
independently executable trading samples and actual trade rewards stayed 0.
The cohort did not become a statistical sample size of 12 independent trades.

SHA256 of the 03:38 as-of report:
ab693fdef4afc803888985abb10bcc8465f9d6fd3e701d07a12624a622638f6a

Private retained report:
~/.local/state/heptatrader/operator-evidence/train-v2-input-asof-fix-20261013/post-slot2-h5-asof.json

No production quote source or original frozen cohort file was overwritten.
