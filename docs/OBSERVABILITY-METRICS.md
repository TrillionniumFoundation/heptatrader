# Observability contract

Status: CURRENT  
Applies to: canonical runtime

## Required metric groups

### Gateway

- requests and results by tool, effect, status, reason code, and execution domain;
- authentication, session, capability, schema, frame, timeout, and transport failures;
- request latency and worker/queue saturation;
- active, recovery-only, fenced, expiring, and uncertain sessions.

### Execution and journal

- commands by lifecycle state, duplicate/conflict/uncertain outcome, and venue;
- journal append/fsync latency, replay duration, last durable sequence, corruption, and unresolved send attempts;
- send, cancel, flatten, reconciliation, recovery, reconnect, and terminalization duration;
- event-feed sequence, subscriber lag, dropped/disconnected consumers.

### Authoritative state and risk

- snapshot epoch/generation/completeness/age and invalidation reason;
- quote subscription identity, quote age, spread, and callback lag;
- active/terminal order correlations and unresolved execution IDs;
- order notional, pending buy/sell notional, worst-case gross notional, PnL/drawdown, rate-window utilization, and risk decision reason;
- kill-switch state and broker-egress-policy state.

### IB PAPER qualification

- candidate/source/binary/harness digests;
- pre/post admission identity;
- builder, runner, protected-environment, broker-session, and receipt verification outcomes;
- no metric may represent source-only success as PAPER authorization.

## Output requirements

Runtime metrics should be structured and monotonic where possible. Logs must contain UTC time, service identity, execution epoch, session/command identity where relevant, typed reason code, and correlation fields without secret/token values. A future Prometheus exporter may expose these metrics, but plain log-grep counters are not the canonical contract.

## Alerts

See [`ALERT-RULES-BASELINE.md`](ALERT-RULES-BASELINE.md) and [`operations/incident.md`](operations/incident.md). Missing telemetry on a safety-critical state is itself an alert; it does not imply a healthy zero value.
