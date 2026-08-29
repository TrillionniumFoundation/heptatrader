# HeptaTrader Production Upgrade — Issue Templates

> Use this file as a ready-to-run execution board. One issue = one deliverable.

## Status Legend
- `TODO` not started
- `DOING` in progress
- `BLOCKED` waiting dependency
- `DONE` accepted

---

## P0 Issues (Blocking for production)

## ISSUE-P0-001 — Canonical Config Source + Profile Lock
- **Priority**: P0
- **Status**: DONE (2026-02-27)
- **Owner**: 
- **ETA**: 3 days
- **Depends on**: none
- **Scope**:
  1. Define single canonical config path.
  2. Introduce profile model: `sim/paper/live`.
  3. Startup hard-fails when conflicting config files are present.
  4. Log config fingerprint (`path`, `profile`, `sha256`).
- **Acceptance Criteria**:
  - Startup logs exact config source and hash.
  - Invalid profile or duplicate config causes startup fail.
- **Deliverables**:
  - Config loader update
  - Validation script in CI

## ISSUE-P0-002 — OMS Journal (Append-only) + Replay
- **Priority**: P0
- **Status**: DOING (Phase-1 skeleton done: append/replay/probes/scripts)
- **Owner**: 
- **ETA**: 8 days
- **Depends on**: ISSUE-P0-001
- **Scope**:
  1. Add append-only journal for order lifecycle events.
  2. Startup replay reconstructs in-memory order state.
  3. Add idempotency key to prevent duplicate post-restart orders.
- **Acceptance Criteria**:
  - Kill/restart mid-order loop recovers to correct final state.
  - No duplicate live orders after restart.
- **Deliverables**:
  - `oms_journal` module
  - Replay + reconciliation tests
  - Phase-1 note: `docs/OMS-JOURNAL-PHASE1.md`

## ISSUE-P0-003 — Broker Reconciliation on Startup
- **Priority**: P0
- **Status**: TODO
- **Owner**: 
- **ETA**: 4 days
- **Depends on**: ISSUE-P0-002
- **Scope**:
  1. Pull open orders / positions / balances at startup.
  2. Compare with journal state and classify mismatches.
  3. Emit reconciliation report artifact.
- **Acceptance Criteria**:
  - Reconciliation report generated at every startup.
  - Mismatch severity levels defined and logged.

## ISSUE-P0-004 — Hard Risk Gate Module (Pre-trade)
- **Priority**: P0
- **Status**: TODO
- **Owner**: 
- **ETA**: 7 days
- **Depends on**: ISSUE-P0-001
- **Scope**:
  1. Move risk checks into independent pre-trade gate.
  2. Add account-level limits (position/notional/daily loss/order rate).
  3. Add clear reason codes (`RISK_XXX`).
- **Acceptance Criteria**:
  - Every blocked order carries a deterministic risk reason code.
  - Risk checks are unit-tested and independent of strategy module.

## ISSUE-P0-005 — Emergency Kill Switch (Global)
- **Priority**: P0
- **Status**: TODO
- **Owner**: 
- **ETA**: 2 days
- **Depends on**: ISSUE-P0-004
- **Scope**:
  1. Runtime global switch to block all order submission.
  2. Optional flatten-only mode.
- **Acceptance Criteria**:
  - Switch activation takes effect within 1 second.
  - Action is audit-logged with operator identity.

## ISSUE-P0-006 — Audit Event Model + Trace IDs
- **Priority**: P0
- **Status**: TODO
- **Owner**: 
- **ETA**: 5 days
- **Depends on**: ISSUE-P0-002
- **Scope**:
  1. Define immutable event schema with `trace_id/order_req_id`.
  2. Ensure all key actions emit structured events.
  3. Provide one-command lifecycle query by order id.
- **Acceptance Criteria**:
  - Full order lifecycle trace retrievable from audit logs.

---

## P1 Issues (Strongly recommended before first live month)

## ISSUE-P1-001 — CI/CD Release Gate Pipeline
- **Priority**: P1
- **Status**: TODO
- **Owner**: 
- **ETA**: 5 days
- **Depends on**: ISSUE-P0-001
- **Scope**:
  1. Build + lint + tests + release gates in CI.
  2. Block release on failed gate.
- **Acceptance Criteria**:
  - Pipeline required for merge/release.

## ISSUE-P1-002 — Unit/Integration Test Matrix
- **Priority**: P1
- **Status**: TODO
- **Owner**: 
- **ETA**: 8 days
- **Depends on**: ISSUE-P0-004
- **Scope**:
  1. Risk/config parser unit tests.
  2. Integration tests with mocked broker events.
  3. Failure-injection scenarios.
- **Acceptance Criteria**:
  - Risk/config module coverage >= 80%.

## ISSUE-P1-003 — Metrics + Alerting
- **Priority**: P1
- **Status**: TODO
- **Owner**: 
- **ETA**: 6 days
- **Depends on**: ISSUE-P0-006
- **Scope**:
  1. Export latency/reject/reconnect metrics.
  2. Add dashboard + threshold alerts.
- **Acceptance Criteria**:
  - Alerts trigger for disconnect, reject spike, stuck order.

## ISSUE-P1-004 — Operations Runbooks
- **Priority**: P1
- **Status**: TODO
- **Owner**: 
- **ETA**: 4 days
- **Depends on**: ISSUE-P1-003
- **Scope**:
  1. Startup/shutdown/recovery/runbook docs.
  2. Incident response flow (P1/P2/P3).
- **Acceptance Criteria**:
  - Operator can execute incident procedure end-to-end.

---

## Tracking Board (Quick View)

| Issue | Priority | Status | Owner | ETA | Dependency |
|---|---|---|---|---|---|
| ISSUE-P0-001 | P0 | DONE |  | 3d | - |
| ISSUE-P0-002 | P0 | TODO |  | 8d | P0-001 |
| ISSUE-P0-003 | P0 | TODO |  | 4d | P0-002 |
| ISSUE-P0-004 | P0 | TODO |  | 7d | P0-001 |
| ISSUE-P0-005 | P0 | TODO |  | 2d | P0-004 |
| ISSUE-P0-006 | P0 | TODO |  | 5d | P0-002 |
| ISSUE-P1-001 | P1 | TODO |  | 5d | P0-001 |
| ISSUE-P1-002 | P1 | TODO |  | 8d | P0-004 |
| ISSUE-P1-003 | P1 | TODO |  | 6d | P0-006 |
| ISSUE-P1-004 | P1 | TODO |  | 4d | P1-003 |

---

## Suggested Execution Sequence
1. P0-001
2. P0-002 + P0-004 (parallel)
3. P0-003 + P0-005 + P0-006
4. P1-001
5. P1-002 + P1-003
6. P1-004
