# IB Advanced Scheduler (P2) Rollout Readiness

## Scope
This document covers production-safe rollout of `HEPTA_IB_ADV_SCHEDULER=1` and new P2 tuning knobs.

## Safety and determinism changes
When `HEPTA_IB_ADV_SCHEDULER=1`:
- deterministic sort tie-break chain: score -> qty -> strategy -> side -> instrument -> referencePrice -> reason
- strict budget gate: intents are dropped if `usedRisk + riskCost > riskBudget`
- optional per-loop enqueue cap (`HEPTA_IB_ADV_SCHED_ENQUEUE_BUDGET_PER_LOOP`)
- configurable per-loop dequeue allocation (`min/max/queue_pressure`)

When `HEPTA_IB_ADV_SCHEDULER=0`:
- default path behavior is unchanged (legacy queueing/drain flow)

## New tuning knobs
All knobs are env vars and optional.

### Score balance
- `HEPTA_IB_ADV_SCHED_SIGNAL_WEIGHT` (default `1.0`): increase to prioritize signal strength.
- `HEPTA_IB_ADV_SCHED_RISK_WEIGHT` (default `1.0`): increase to penalize risk cost more.

Score formula:
`score = (signal_weight * signal_strength) / (1 + risk_weight * risk_cost)`

### Budget allocation
- `HEPTA_IB_ADV_SCHED_RISK_BUDGET_QTY` (default: `risk.maxOrderQuantity`)
- `HEPTA_IB_ADV_SCHED_ENQUEUE_BUDGET_PER_LOOP` (default `0`, unlimited)
- `HEPTA_IB_ASYNC_PLACE_BUDGET` (existing hard upper bound)
- `HEPTA_IB_ADV_SCHED_MIN_PLACE_BUDGET` (default `1`)
- `HEPTA_IB_ADV_SCHED_MAX_PLACE_BUDGET` (default `HEPTA_IB_ASYNC_PLACE_BUDGET`)
- `HEPTA_IB_ADV_SCHED_QUEUE_PRESSURE` (default `0.5`, range `0..1`)

Effective per-loop place budget (advanced scheduler only):
`clamp(ceil(queue_depth * queue_pressure), min_place_budget, max_place_budget)`
then clamped by `HEPTA_IB_ASYNC_PLACE_BUDGET`.

## Recommended starting values (paper/stress)
- signal/risk: `1.2 / 1.8`
- risk budget: `3000`
- enqueue budget per loop: `8`
- place budget: min `2`, max `5`, queue pressure `0.7`
- queue capacity: `512`

## 30-minute stress profile
Files:
- `scripts/ib_adv_scheduler_stress_30m.env`
- `scripts/run_ib_adv_scheduler_stress_30m.ps1`

Run:
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_ib_adv_scheduler_stress_30m.ps1
```

Allow paper orders explicitly:
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_ib_adv_scheduler_stress_30m.ps1 -AllowPaperOrders
```

## Safe on/off procedure
### Enable (paper first)
1. set `HEPTA_IB_ADV_SCHEDULER=1`
2. keep safety gates: `HEPTA_ALLOW_IB_ORDERS=0`, `HEPTA_ALLOW_IB_LIVE=0`, `HEPTA_IB_LIVE_KILL_SWITCH=1`
3. run 30-minute stress profile and verify logs:
   - `[IB-ADV] ...`
   - `[IB-SCHED] selected/dropped ...`
   - `[IB-ASYNC] queue_depth=...`
4. if stable, enable paper orders only (`HEPTA_ALLOW_IB_ORDERS=1`)

### Disable / rollback
Immediate rollback (no restart policy change needed):
- set `HEPTA_IB_ADV_SCHEDULER=0`
- optionally reset tuning vars (or leave; ignored when scheduler is OFF)

Emergency stop:
- set `HEPTA_GLOBAL_KILL_SWITCH=1` (or `HEPTA_IB_LIVE_KILL_SWITCH=1` for live gate)
