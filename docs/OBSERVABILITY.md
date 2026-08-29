# Runtime observability and SLO contract

Status: current
Applies to: Gateway, Execution Service, OMS, risk, simulator and IB PAPER
Last verified commit: moving-main

Observability is organized around trading state transitions, not obsolete build scripts. Every metric carries `environment`, `venue` and a bounded `reason_code`; account, token, credential and full strategy prompt values are never labels.

## 1. Required counters

| Metric | Meaning |
|---|---|
| `hepta_tool_calls_total{tool,status,reason_code}` | Gateway-visible calls and outcomes |
| `hepta_session_rejections_total{reason_code}` | identity/session/capability failures |
| `hepta_risk_decisions_total{decision,reason_code}` | deterministic allow/reject outcomes |
| `hepta_execution_commands_total{status,reason_code}` | accepted/rejected/duplicate/uncertain commands |
| `hepta_oms_journal_failures_total{reason_code}` | durability failures; any increase blocks new mutation |
| `hepta_venue_sends_total{operation,status}` | broker mutation attempts after durable journal |
| `hepta_execution_events_total{event_type}` | normalized ACK/fill/cancel/reject/correction events |
| `hepta_reconcile_runs_total{outcome,reason_code}` | authoritative reconciliation outcomes |
| `hepta_state_breaks_total{kind}` | order/position/account disagreement with venue |
| `hepta_kill_switch_transitions_total{state}` | kill-switch changes |

## 2. Required gauges

- active sessions by environment;
- active orders and uncertain commands;
- authoritative snapshot age;
- quote age for the evaluated instrument;
- execution epoch/fencing generation as info values, not high-cardinality labels;
- broker connection/recovery state;
- journal backlog and event-relay backlog;
- gross/net exposure and remaining risk budget using bounded account/strategy identifiers.

## 3. Required latency histograms

Use a monotonic clock and record:

```text
market event -> quote projection
intent receipt -> decision snapshot complete
snapshot complete -> risk decision
accepted command -> journal durable
journal durable -> venue send
venue callback -> OMS projection
reconnect start -> reconciliation complete
```

Initial release gates use same-fixture relative regression: p99 may not regress more than 20% without an accepted explanation and updated baseline. Do not claim universal “low latency” from host tuning alone.

## 4. SLOs

For Simulator/core fixtures:

- 100% accepted mutations are journaled before send;
- 100% duplicate command retries resolve to the same durable outcome;
- zero scaffold venue success events;
- zero state-break leakage into new-risk authorization;
- zero session/capability bypass;
- deterministic replay produces the declared tolerance.

For IB PAPER soak:

- no unresolved uncertain command beyond the configured reconciliation deadline;
- no new-risk order using stale quote or incomplete account/position state;
- broker disconnect/reconnect transitions are observable and reconcile before reopening risk;
- safe cancellation/flatten outcomes are tracked independently from new-risk availability.

## 5. Alert policy

### P1 — immediate risk response

- journal cannot persist a new command;
- risk-increasing send occurred without a durable command record;
- authoritative position/order break while new-risk gate remains open;
- kill switch cannot be read or enforced;
- stale/unknown quote or generation accepted for new exposure;
- execution fencing/epoch mismatch;
- uncertain command exceeds reconciliation deadline with possible exposure.

### P2 — degraded trading capability

- broker disconnected with no known open risk;
- event/reconcile backlog approaching limit;
- repeated venue rejects or callback delay;
- decision/risk p99 regression above budget.

CI failure is a development block, not a production P1 trading alert.

## 6. Logging

Use structured records containing timestamp, service epoch, fencing generation, session/command/tool IDs, status and canonical reason code. Redact credentials, tokens, raw account secrets and unbounded model text. A log message is diagnostic; the durable journal and authoritative projection remain the state truth.
