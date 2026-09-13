# Alert rules baseline

Status: CURRENT  
Applies to: canonical runtime

## P1 — stop risk increase

Trigger on any of:

- kill switch `Uncertain`, unsafe control path, or inability to observe the marker;
- journal durability failure after a possible send;
- unresolved/duplicate broker order, execution correlation conflict, or position mismatch;
- stale/incomplete authoritative risk state while mutation admission is open;
- broker API access observed from an Agent or Gateway identity;
- credential/token disclosure;
- terminal recovery or owner-fence ambiguity;
- the running artifact digest, credential/network boundary, or evidence bound to that running artifact is invalid or compromised.

Action: engage the operator kill switch, fence sessions, preserve evidence, use read-only status/reconciliation, and follow [`operations/incident.md`](operations/incident.md).

## P2 — degraded but bounded

- repeated reconnect or refresh timeout;
- callback/event-feed lag above the declared SLO;
- rapidly rising risk rejects or order/cancel errors;
- session stuck in recovery-only, expiring, or terminalizing state;
- disk/journal latency or space approaching a safety threshold;
- protected qualification runner unavailable;
- structured OMS capacity WARNING (inclusive 80%) or EXCEEDED, requiring a planned
  stopped-state checkpoint and measured recovery budget, not journal truncation;
- missing expected OMS capacity heartbeat (deployment baseline: 15 seconds), or
  UNKNOWN after readiness; inspect writer safety and file identity before assuming
  zero usage. A poisoned writer remains P1, not merely a capacity warning.

## P3 — investigate trend

- isolated recoverable read or market-data error;
- non-critical observability exporter failure when local safety state remains visible;
- deterministic simulator or research fixture drift.

## Rule requirements

Each alert has a stable rule ID, severity, service/venue, observed value, threshold, first/last time, exact reason code, and runbook link. Missing or unknown values are distinct from zero. Thresholds must be configured in deployment policy and tested; this file does not invent universal latency or loss limits.

## Repository admission is not a runtime kill signal

A missing or failed CI check for a new PR, merge candidate, tag or later main
revision blocks that revision's admission/promotion. It does not automatically
stop an already qualified immutable artifact. Investigate whether the running
artifact or its bound evidence is actually affected before escalating to a
runtime action. No repository event alone grants or revokes Broker authority.

This file specifies alert policy; deployment must install and test a collector
and delivery path. The [metric inventory](OBSERVABILITY-METRICS.md) identifies
which structured producers exist and which metrics/exporters remain requirements.
