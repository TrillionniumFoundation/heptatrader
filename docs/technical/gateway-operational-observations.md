# Gateway runtime observations

Status: CURRENT
Applies to: local Gateway process; no wire, session, or execution authority change

`UnixToolServer` records actual ingress/framing, selected queue waiting,
dispatch, and response-write durations using the steady clock. Its existing
queue, worker, backpressure, deadline and cancellation counts are exported by
`hepta-tool-gatewayd` every five seconds in
`heptatrader.gateway-observation.v1` JSON. The process's generated Gateway epoch
separates restarts. Wall-clock milliseconds establish collection freshness;
monotonic milliseconds establish interval deltas. No token, account, tool-call
ID, arbitrary tool name, request body or reason text is a metric label.

## Exact boundaries

| Field | Meaning |
|---|---|
| ingress_latency | peer credential acquisition + bounded framing + typed decoding, including rejected input |
| queue_latency | successful queue-entry selection until a worker begins dispatch; cancelled/never-dispatched requests do not contribute |
| dispatch_latency | worker's expiry decision or host invocation, excluding result audit and reply encoding/write |
| reply_latency | result JSON encoding and attempted complete framed write, including failed writes |
| response_attempts/result_counts | attempted result envelopes by the fixed seven statuses plus unknown; not Broker outcomes |
| response_writes / response_write_failures | complete WriteFrame success or failure; a successful syscall does not prove client receipt |
| pending_connections / active_requests / ready_owners | local sampled scheduler gauges, not a transactionally coherent portfolio view |
| worker_limit / pending_limit | configured server bounds |

Counters use fixed memory. New activity counters saturate rather than wrap;
a saturated series is explicitly unusable for rate/quantile inference. The
histogram primitive is shared with OMS but introduces no journal writer or
Broker dependency in the Gateway. The four histogram definitions have fixed
11-bucket bounds; exported quantiles are bucket upper bounds, never exact
percentiles. Queue and dispatch samples are updated together. The telemetry
mutex is held only for observation/copy; it is never held while reading or
writing a socket, invoking Execution, or formatting/emitting log output.

## Read-only operational output

The installed `libexec/heptatrader/hepta_gateway_report.py` shares the bounded,
no-follow, stable-snapshot log reader with the existing OMS reporter. For an
operator-created regular log export:

```bash
python3 /usr/libexec/heptatrader/hepta_gateway_report.py \
  --input /private/gateway-export.jsonl --format prometheus
```

The reporter validates status/count/histogram accounting. Fixed Prometheus
metric names and status labels prevent caller-controlled cardinality and secret
leakage. A new epoch, reset, absent adjacent sample, excessive collection gap or
changed policy removes interval evidence. Missing data is exit 2, not zero.
Observational alerts are exit 1: stale/future collection, stopped service,
sampled worker/queue saturation, new reply failures/backpressure and saturation
of metric storage. Instantaneous saturation is a diagnostic observation, not an
SLO breach or permission to change trading policy. Alert delivery and incident
response require real host integration; this command opens no network service.

Native Unix-server tests inspect real path counters; Python tests cover actual
C++ serialization, accounting, bounds, privacy, restart/gaps and exports.
Installed-process tests use actual daemon traffic and wrong-UID rejection,
then execute the installed reporter across a real restart.
