# HeptaTrader Production Readiness Plan (v1)

> Scope: bring current HeptaTrader from research/paper-trading level to production-grade quant trading platform.

## 1) Prioritized Backlog (P0/P1/P2)

## P0 — Must complete before live trading

### P0-1 Order Journal + Recovery (OMS durability)
- **Goal**: survive process crash/restart without losing order lifecycle state.
- **Tasks**:
  1. Add append-only local journal (JSONL or binary WAL): `order_intent`, `place_sent`, `ack`, `status`, `fill`, `cancel_sent`, `cancel_ack`, `reject`.
  2. Add startup replay: rebuild in-memory state from journal.
  3. Add broker reconciliation at startup (open orders / positions / balances).
  4. Add idempotency key and duplicate suppression across restarts.
- **Acceptance**:
  - Kill process during order loop, restart within 30s, system reconciles to correct final state.
  - No duplicated live orders after restart.

### P0-2 Multi-layer Risk Engine
- **Goal**: enforce hard risk boundaries independent of strategy logic.
- **Tasks**:
  1. Split risk checks into pre-trade hard gate module.
  2. Add account-level limits: max position, max notional, max daily loss, max order rate.
  3. Add session/time-window guardrails.
  4. Add emergency global kill switch (runtime-configurable).
- **Acceptance**:
  - Violating any risk rule blocks order with explicit reason code.
  - Kill switch blocks all order submissions within 1 second.

### P0-3 Config Single Source of Truth
- **Goal**: eliminate config drift and wrong-file execution.
- **Tasks**:
  1. Define canonical config path + profile system (paper/live/sim).
  2. Reject startup when multiple conflicting config files detected.
  3. Validate config schema at startup; fail fast with clear error.
- **Acceptance**:
  - Startup prints exact config source path + profile.
  - CI config lint fails on invalid/missing critical fields.

### P0-4 Audit-grade Event Model
- **Goal**: trace every order from signal to broker response.
- **Tasks**:
  1. Add global `trace_id` / `order_req_id` propagation.
  2. Normalize structured event schema.
  3. Persist immutable audit stream with timestamp + source + decision reason.
- **Acceptance**:
  - Given any orderId, one command retrieves full lifecycle chain.

---

## P1 — Strongly recommended before first production month

### P1-1 CI/CD Release Gates
- **Tasks**:
  1. Add CI pipeline: build, static checks, unit tests, integration tests, regression scripts.
  2. Add artifact signing/checksum.
  3. Add release promotion policy (paper soak → canary → production).
- **Acceptance**:
  - No direct manual binary deployment to production.

### P1-2 Test Matrix Expansion
- **Tasks**:
  1. Unit tests for risk, config parser, adapter edge cases.
  2. Integration tests with mocked IB/CTP responses.
  3. Failure injection: disconnect/reconnect, duplicate callbacks, delayed acks.
- **Acceptance**:
  - Minimum target: 80% coverage for risk/config modules.

### P1-3 Observability + Alerting
- **Tasks**:
  1. Expose metrics: submit latency, cancel latency, reject ratio, reconnect count, heartbeat lag.
  2. Build Grafana dashboard and alert rules.
  3. Add severity levels (P1/P2/P3) and on-call runbook links.
- **Acceptance**:
  - Alert fires automatically for: gateway disconnected > N sec, reject spike, order stuck.

### P1-4 Runbook + Ops Controls
- **Tasks**:
  1. Standardize runbooks for startup/shutdown/recovery.
  2. Add operator commands: pause strategy, flatten positions, switch read-only.
  3. Add protected confirmation for dangerous actions.
- **Acceptance**:
  - New operator can execute incident playbook without code changes.

---

## P2 — Scale and institutional quality

### P2-1 Portfolio/Exposure Analytics
- VaR, concentration, correlation exposure limits.

### P2-2 Multi-broker abstraction
- Broker failover and routing policy.

### P2-3 Time sync + latency SLO
- NTP/PTP discipline + end-to-end latency budget with SLOs.

### P2-4 Compliance hardening
- Retention policy, immutable storage, sensitive field masking, access audit.

---

## 2) Work Packages (estimated)

- **WP-A (2 weeks)**: P0-3 + config schema validation + startup source lock
- **WP-B (3 weeks)**: P0-1 journal/replay/reconcile
- **WP-C (2 weeks)**: P0-2 risk hard gates + kill switch
- **WP-D (2 weeks)**: P0-4 audit event model
- **WP-E (2 weeks)**: P1-1 CI/CD baseline
- **WP-F (2 weeks)**: P1-3 metrics + alerts + dashboards

Total baseline: ~13 weeks (single small team). Can be parallelized.

---

## 3) Definition of Done (Production Entry)

A release is considered production-ready only when:
1. All P0 items pass acceptance tests.
2. 7-day paper soak has zero unresolved P1 incidents.
3. Release gate pipeline is green and artifacts are traceable.
4. Incident drills (disconnect/restart/reconcile) are successfully completed.

---

## 4) Immediate Next Actions (this week)

1. Freeze canonical config path and profile naming.
2. Create `oms_journal` module skeleton and event schema.
3. Add startup config fingerprint log (`config_path`, `sha256`, `profile`).
4. Define risk reason codes (`RISK_XXX`) and map to logs.
5. Add CI job to run unified gate entry:
   - `gate-local.ps1` (local one-click)
   - `scripts/ci_gate.ps1` (CI entry, with exit-code contract)
   - Workflow draft reference: `docs/GITHUB-ACTIONS-WORKFLOW-DRAFT.md`

---

## 5) W10-W12 Baseline Delivery Status (Phase E/F/G)

### W10 / Phase E — CI Gate
- Implemented workflow: `.github/workflows/ci-gate.yml`
- CI entrypoint: `scripts/ci_gate.ps1`
- Artifact upload: `runtime-logs/ci-gate-*/`

### W11 / Phase F — Observability Scaffold
- Metrics baseline: `docs/OBSERVABILITY-METRICS.md`
- Alert baseline: `docs/ALERT-RULES-BASELINE.md`
- Script enhancement: `scripts/summarize_ib_logs.ps1`
  - Added machine-readable `summary.json`
  - Added `alerts.json` with P1/P2 rule outputs
  - Added configurable thresholds for nextValidId / tickPrice / critical error codes

### W12 / Phase G — Runbooks & Go-Live Ops
- Startup runbook: `docs/RUNBOOK-STARTUP.md`
- Incident runbook: `docs/RUNBOOK-INCIDENT.md`
- Go-live checklist: `docs/PROD-GO-LIVE-CHECKLIST.md`

### Verification (baseline)
1. Local gate:
   - `powershell -ExecutionPolicy Bypass -File .\gate-local.ps1 -NoLaunch -SkipHealthcheck`
2. Direct CI gate script:
   - `powershell -ExecutionPolicy Bypass -File .\scripts\ci_gate.ps1 -ProjectRoot "D:\quant\HeptaTrader-master" -NoLaunch -SkipHealthcheck`
3. Observability summary:
   - `powershell -ExecutionPolicy Bypass -File .\scripts\summarize_ib_logs.ps1`
   - Verify generated `summary.md`, `summary.json`, `alerts.json`
