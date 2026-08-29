# IB P2 Advanced Flags (Guarded, Default OFF)

These controls are **opt-in**. If not set, legacy behavior is unchanged.

## New env flags

- `HEPTA_IB_STEADY_SIGNAL_CLOCK` (default `0`)
  - `1`: strategy signal/sample timing uses `steady_clock` (ms-level, monotonic), preventing wall-clock jumps from affecting cadence.
  - `0`: legacy timing path (system/wall clock + existing sec/ms gates).

- `HEPTA_IB_ADV_SCHEDULER` (default `0`)
  - `1`: enable lightweight multi-strategy scheduler before async placement queue.
  - Scheduler ranks intents by `signalStrength / (1 + riskCost)` and admits intents under per-loop risk budget.

- `HEPTA_IB_ADV_SCHED_RISK_BUDGET_QTY` (default `HEPTA_IB_MAX_ORDER_QTY` effective risk max)
  - Per-loop aggregate risk budget (qty-like units) used when advanced scheduler is enabled.

- `HEPTA_IB_ADV_OBS` (default `0`)
  - `1`: emit additional observability logs:
    - `[IB-SCHED]` selected/dropped decisions
    - `[IB-STRAT-TIMING]` per-strategy eval/cycle timing
    - `[IB-LOOP-TIMING]` main loop cycle timing snapshots

- `HEPTA_IB_ADV_OBS_INTERVAL_SEC` (default `30`, min practical `5`)
  - Interval for timing/obs snapshots when `HEPTA_IB_ADV_OBS=1`.

## Safe rollout (recommended)

1. **Stage 0 (baseline)**: keep all flags OFF.
2. **Stage 1 (timing only)**: set `HEPTA_IB_STEADY_SIGNAL_CLOCK=1` only; verify no strategy behavior drift except better cadence stability.
3. **Stage 2 (observability)**: add `HEPTA_IB_ADV_OBS=1`; confirm scheduler/timing logs are healthy.
4. **Stage 3 (scheduler canary)**: set `HEPTA_IB_ADV_SCHEDULER=1` with conservative `HEPTA_IB_ADV_SCHED_RISK_BUDGET_QTY`.
5. Increase budget gradually after confirming order quality and no unexpected drops.

## Fast rollback

Unset (or set to `0`) these flags:

- `HEPTA_IB_STEADY_SIGNAL_CLOCK`
- `HEPTA_IB_ADV_SCHEDULER`
- `HEPTA_IB_ADV_OBS`

Optional: remove `HEPTA_IB_ADV_SCHED_RISK_BUDGET_QTY` and `HEPTA_IB_ADV_OBS_INTERVAL_SEC`.

This immediately returns runtime to legacy behavior paths.
