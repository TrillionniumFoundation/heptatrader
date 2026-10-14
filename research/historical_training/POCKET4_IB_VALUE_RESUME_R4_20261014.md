# Pocket4 IB history + Value successor — R4, 2026-10-14

## Real operational changes

- The old trial3 Value one-second predictor calendar remains **disabled**;
  the original-request watcher from R3 remains enabled, without trade authority.
- The two formerly crashing Value evaluator timers were disabled **after**
  their separately versioned successor services successfully completed actual
  locally signed scoring and verified the original assessment files unchanged.
- Installed successor units:
  `heptatrader-value-successor-trial3-evaluator.{service,timer}`,
  `heptatrader-value-successor-original-evaluator.{service,timer}`.
  Calendars are finite through October 24, every five minutes, staggered.
- Each successor uses the same original plan, registrar signature, candidate
  and prediction witness. It reads the original, SHA256-pinned OMS byte prefix
  with a bounded streaming parser even when the append-only file exceeds
  32 MiB. Outcomes and local evaluator signatures are written to an **isolated
  successor directory** with append-only continuation receipts.
- This is explicitly **post-hoc continuation**, not the original precommitted
  frozen runtime, not canonical Agentd admission, and never order authority.
  Original trials, failed logs and original assessments were NOT rewritten.
- Trial3 observed 58 / 179 original commands in a later successful check.
  Original Value observed 0 / 180. Both PENDING, no candidate selected.
  Counts are snapshots and may increase with genuinely new broker fills.

## Real IB PAPER historical retrieval

- Separate, bounded read-only EUR.USD requests, client 8984, 127.0.0.1:4002,
  under a process-global lock. HEAD timestamps returning 162 did not establish
  that historical bars were inaccessible.
- Explicit BAR-first mode bypasses the HEAD *query*, not the safety checks:
  known 2105 disconnect blocks requests; an UNKNOWN farm permits at most one
  guarded bar probe; a successful full end callback with rows is required
  before subsequent same-session requests. Any 162 or unknown callback
  aborts without automatic replay.
- The 7-day October 13 window returned MIDPOINT/BID/ASK and actual schedule
  callbacks; 1,424 calendar-complete paired bars, 821 exactly equal overlap,
  603 previously absent bars.
- Older 90-day and 60-day chunks also completed: 12/12 and 8/8 history
  requests succeeded respectively, with zero broker orders. Their bounded
  source windows were checked independently against returned broker schedules.
- Exact-identity merge refuses all revisions and does not invent missing bars.
  Final separate research dataset:
  `~/.local/state/heptatrader/neuron-learning/historical-offline/eurusd-5m-joined-1yr-20261014-r4`.
  **73,169** aligned 5-minute timestamps, from **2025-10-16 00:00 UTC**
  through **2026-10-13 23:50 UTC**, no unresolved calendar omissions.
  Backward merges added **17,091** and **11,937** new bars after
  **569** and **20** byte-identical overlapping time points.
- Broker-retrieved historical bars are retrospective/revisable and are
  **not** proof of contemporaneous knowledge, actual fills, slippage or PnL.
  Raw downloads and prior failures are kept intact.

## Offline training result — not a trading release

- 15/30/60-minute four-fold rolling train/validation/test on the resulting
  one-year history with next-bar-open hypothetical entry/exit. Abstention
  is an explicit zero-USD baseline. Ridge selects no trade on every fold.
- Study scenario assumes roundtrip commission **0.4 bps** and extra slippage
  **0, 0.5, 2 bps** on top of bid/ask spread proxies; assumptions are not
  measured transaction costs.
- In the 0.5-bps slippage scenario: 15-minute mean reversion = -$367.76,
  30-minute = -$542.45, 60-minute = +$159.52 (168 simulated trades).
  The latter changes to **-$2,108.89 at 2 bps** and is therefore not
  cost-robust. No financial candidate is selected or registered.
- The 603 genuinely new-to-the-previous-dataset **historical** bars were
  also withheld from fitting in a separate incremental holdout. Those
  comparisons did not produce a robust positive selection. It is NOT a
  live prospective experiment.

## Verification and remaining distinct gates

- `unittest` historical-training regression: **647 passed**.
- Laya/Value non-native regression: **314 passed, 1 skipped**. Two
  native-conformance classes lacked their fixture-config environment and
  were excluded, not counted as passing.
- Execution, FX PAPER driver, BTC PAPER driver, original quote/learning
  services and request trigger were not replaced. No real-money order.
- Future shadow qualification still requires genuinely prospective
  timestamps, fresh original requests, a meaningful **cost-after-fees**
  advantage against abstention and independent economic scoring. No
  backdating/repurposing previously failed windows.
