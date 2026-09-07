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
- required Core/Documentation/Merge Candidate check missing or failed on an admitted revision.

Action: engage the operator kill switch, fence sessions, preserve evidence, use read-only status/reconciliation, and follow [`operations/incident.md`](operations/incident.md).

## P2 — degraded but bounded

- repeated reconnect or refresh timeout;
- callback/event-feed lag above the declared SLO;
- rapidly rising risk rejects or order/cancel errors;
- session stuck in recovery-only, expiring, or terminalizing state;
- disk/journal latency or space approaching a safety threshold;
- protected qualification runner unavailable.

## P3 — investigate trend

- isolated recoverable read or market-data error;
- non-critical observability exporter failure when local safety state remains visible;
- deterministic simulator or research fixture drift.

## Rule requirements

Each alert has a stable rule ID, severity, service/venue, observed value, threshold, first/last time, exact reason code, and runbook link. Missing or unknown values are distinct from zero. Thresholds must be configured in deployment policy and tested; this file does not invent universal latency or loss limits.
