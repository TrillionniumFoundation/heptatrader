# Pocket4 Value/FX training continuation — R5 (2026-10-15)

## Real operational root causes

- Read-only verified producer replay of the 12h preceding 2026-10-15 early
  UTC: 81 mature original **flat-position** requests, **11** cryptographically
  validated and early-signed commits, **69** immutable deadline misses and
  **1** unresolved expired case. Retained mean/median and denominator are
  research observations, *not* per-trade profitability samples.
- Original resource refusals included 32 request-level cases bearing
  `HOST_SWAP_RESERVE_BELOW_1GIB`, 20 involving incomplete source continuity,
  13 involving another heavy research worker, six memory PSI holds and three
  transient quote-freshness holds. Reasons may overlap within a request.
- The old event watcher started the frozen original one-shot predictor only
  once per fresh request. On resource denial it did not retry before the
  original **60-second** deadline; the next two-minute request arrived too late.

## Completed repairs

- A separate temporary **4GiB kernel zram** swap device was activated:
  `/dev/zram0`, priority 10, without changing existing `/swap.img`,
  resident training guard thresholds, original Value code or trading services.
  Actual frozen Value guard returned `admit_new_worker=true` with 24/24
  source probes, sufficient memory and no pressure at a measured instant.
  Host capacity is boot-ephemeral: recheck after reboot; never assume it was
  provisioned permanently.
- R5 watcher retains original request identity and source path, but may
  **re-trigger the same frozen worker** at most four times per request,
  with 14-second spacing, only while still fresh (max 55-second age).
  It avoids starting a second worker while systemd reports the first active,
  submits at most one new worker start per observation tick, and stops when
  a signed commit or terminal failure appears. It does not produce inference
  or write missing original predictions.
- A watcher restart now preserves partial append records and recovers only
  truly recent original requests. Original one-second timer remains disabled.
  R5 is a separate immutable source release; the previous unit is retained
  for rollback. All original Value signing/publication checks remain frozen.
- A read-only research coverage observer validates original REQUEST provenance,
  existing prediction signatures, signed publication times, historical missed
  requests and bounded resource denial causes. It reports both a rolling 12h
  denominator and a **strictly post-restoration** denominator.
- The first *post-zram* matured request was still unscored: its specific guard
  refusal was `HOST_FULL_MEMORY_PSI_PRESSURE` at avg60=0.16. The first
  *post-R5 final cutover* flat-position request arrived 2026-10-15 09:13
  device-local time. The new watcher invoked the unchanged frozen worker
  at request ages 0.8, 14.8, 28.9 and 42.9 seconds. The request still
  reached an immutable deadline miss because the original guard saw
  `HOST_FULL_MEMORY_PSI_PRESSURE` (avg60=0.19). Source and signed-prediction
  audit found 1 mature post-cutover opportunity, 0 valid commits.
  No improvement can yet be claimed.

## Offline cost-aware training

- A separate CPU-only nonlinear feature/Ridge study compared a fixed family
  of engineered linear/nonlinear features on the previously validated
  73,169-point year-long EUR/USD BID/ASK/MIDPOINT 5m dataset.
- Three horizons (15/30/60 minutes), four chronological rolling folds,
  nonoverlapping test positions, frozen validation-selected regularization
  and abstention thresholds, and executable-side simulated BID/ASK prices.
- Assumed 0.4bps roundtrip commission plus 0/0.5/2bps extra slippage.
  All six arms failed the predeclared +2bps robust-return screen and abstained
  from trading. No autonomous candidate promotion; no actual IB execution
  inference or real slippage qualification is claimed.
- A separate bounded IB completed-day incremental request for October 14
  did not dispatch: the named `cashhmds` service was not ready. The failed
  session is preserved, not silently retried or presented as new training
  data. The last proven-complete one-year dataset remains unchanged.

## Further real qualification

1. Observe new original flat-position requests after R5 cutover, measuring
   verified pre-deadline commitments over all mature opportunities; old
   missed/held requests are never backdated.
2. Keep the existing minimum swap, memory and source-continuity safeguards;
   only admit research work when genuinely safe.
3. Require a separately pre-registered prospective shadow window and a
   material cost-after-fees improvement over abstention before any candidate
   could be considered for human owner review. No PAPER strategy routing
   or real-money trade change is made here.

## Evidence locations

- `~/.local/state/heptatrader/operator-evidence/value-host-zram-capacity-20261015-r5/`
- `~/.local/state/heptatrader/operator-evidence/value-original-request-trigger-r5-cutover-20261015/`
- `~/.local/state/heptatrader/operator-evidence/value-original-request-trigger-r5-fix-20261015/`
- `~/.local/state/heptatrader/operator-evidence/value-coverage-audit-20261015-r5/`
- `~/.local/state/heptatrader/operator-evidence/nonlinear-fx-cost-study-20261015-r5/`
- `~/.local/state/heptatrader/operator-evidence/ib-incremental-20261015-r5/`

No historical archives, predictor keys, authoritative broker journal, or
Desktop Commander service were modified.

## Final R5 validation / deployment

- The final installed R5 watcher source matches SHA256
  `8cd03e17189b6dbc14c4706c5f91c6bd59ab3b4fa6f8479ddd5dcd50848e57aa`.
  The user-level watcher unit is active with a new PID; its previous unit
  and both earlier code releases remain available for rollback.
- Historical-training regression: **659 tests pass**. Laya/Value non-native
  regression: **314 pass, 1 skipped**; two tests classes needing separately
  supplied native fixture configurations were not claimed as passing.
- Additional final deployment receipt:
  `~/.local/state/heptatrader/operator-evidence/value-original-request-trigger-r5-final-20261015/`.
- The initial R5 cutover checkpoint had no mature post-cutover eligible
  original requests. Its first later request was **1/1 unsuccessful** despite
  all four bounded retries; the new control worked, but sustained host memory
  pressure prevented the original prediction. Keep the old signed-trial
  validity and `60s` publication deadline unchanged rather than backfill.
- Independent read-only first-request receipt:
  `~/.local/state/heptatrader/operator-evidence/value-coverage-audit-20261015-r5/post-r5-first-mature.json`.
