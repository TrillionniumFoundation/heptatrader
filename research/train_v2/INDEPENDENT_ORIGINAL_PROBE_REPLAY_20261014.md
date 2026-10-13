# Train V2: Independent exact original Source Health replay (2026-10-14)

**Scope:** Isolated, read-only source audit stacked on PR #291 / commit 803335343b5df696c3aef8d5a1a9f1c2e08fd544. Neither production Laya Guard nor its trained models, original source segments, quote timestamp, Broker gateway, Execution, PAPER strategy, scheduled trials or GPU consumers are modified.

## Previously unsupported claim

Previous source-gap forensics independently checked whether each fixed 60-second probe contained at least one actual original quote. That is necessary but not sufficient: an original quote can exist while the historical input is discontinuous, the exact 300-second outcome is missing, or an intermediate quote path exceeds 30 seconds. A forged status with 24 USABLE probes and one quote per minute could pass the former mere-anchor audit. Counting quote anchors does not establish 24/24 causal source readiness.

## Change

The new replay_original_fixed_grid recomputes all 24 fixed minute probes using the ORIGINAL pinned laya_cell.source_readiness.probe implementation and the already-verified original chained PAPER quotes frozen to the exact original status as-of timestamp. It compares each complete original probe dict (including anchor, horizon due, original input failures, history and endpoint/path details) to the independent result. It also independently reconstructs the full status counts, usable/unusable totals, quote age, missing-history and insufficient-coverage reasons and HOLD/READY state. Any discrepancy is a hard refusal. The receipt includes the original source-probe implementation SHA256, original status/source prefix evidence and the exact revised diagnostic code SHA256.

This adds **verification only**, never source inference, interpolation, a training adapter, GPU dispatch, owner enrollment or a replacement original Source Health decision. The existing CLI keeps intentional non-admission exit code 2 even when the independent result is READY_FOR_REVIEW_NOT_AUTHORIZATION.

## New physical Pocket4 proof

On October 14, after the post-reboot original feed naturally recovered, the actual original 24-minute status was READY_FOR_REVIEW_NOT_AUTHORIZATION with all 24 USABLE and no HOLD reason. Independent full original hash-chain and 24-slot causal re-evaluation matched the saved original slot dicts byte-equivalently. No empty or rejected input minute was found. The most recent original contiguous source suffix was approximately 71 minutes; prior failed and missing windows were NOT backfilled. The result remains UNCHANGED_REVIEW_ONLY_NO_TRAINING.

Create-only private proof:
/home/alex/.local/state/heptatrader/operator-evidence/train-v2-cpu-original-20261014/source-ready-full-24-probe-replay-v1.json
SHA256: bfdcee5d82c520b26cbc66ab13f934bc5d1508f202e93c4cd17050daeec229d5

The original GPU owner-census proof was independent: at least ten real busy DRM/KFD holders and incomplete PID permission coverage. Exact source READY does not imply an exclusive ROCm device lease. The current economic models and independent Canonical Owner also remain unqualified.

## Negative tests

Focused suite now includes seven new cases: no quote, quote exists but lacks contiguous causal history and mature endpoint, forged READY, counts forgery, missing TTL-stale reason, altered original probe input lineage, and wrong source period. These intentionally fail closed. The original previous 19 tests and their failure evidence are retained.

## Next acceptance

Use the recovered original source only for new preregistered future research, never to repair old trials. Actual model/GPU admission remains behind the unchanged existing product Guard, physically exclusive GPU lease, and independently acknowledged Canonical Owner. Actual future model prediction and after-cost returns are distinct from Source Health, which measures coverage only.

## Exact Pocket4 source and regression closeout

- Independent real original-source recomputation: 24/24 complete native
  probe dicts independently matched, original operational source state
  READY_FOR_REVIEW_NOT_AUTHORIZATION, 0 missing causal source windows,
  no training/GPU/owner authority.
- Focused tests: 26 passing (19 retained + 7 added).
- Full historical research: 539 passing, 7 existing skips.
- Full core Python: 2,760 passing, 23 existing skips.
- Source/build/ownership partition: 106 passing.
- The existing source_gap_forensics module returns intentional exit code
  2 even for READY because the result is an immutable research diagnostic,
  not production Source Health admission.
- The separate new CPU-only Native two-window research sample is NOT a
  GPU/Agentd promotion or statistically independent financial return.
  Its immutable model results and original source deadlines are retained
  separately, without modifying either original cohort.
