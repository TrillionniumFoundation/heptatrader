# Gateway request and response observations

Status: CURRENT
Applies to: local Unix Tool Gateway; no authority or protocol change

## Actual producer and collection

`UnixToolServer::GetHealth` returns existing queue gauges/backpressure counters
plus fixed result counts and three measured latency histograms. The owning
`hepta-tool-gatewayd` writes `heptatrader.gateway-metrics.v1` JSON every five
seconds after startup through the existing service log. No new socket, exporter
process, credential access or Broker call is introduced. Counter lifetime is the
server object; the daemon's generated `service_epoch` separates restarts.

The scheduling gauges and measurement counters are acquired under separate
short locks and are NOT a transactionally consistent view of all service state.
Neither lock is held during host invocation or socket writes. Measured latency
includes system scheduling and serialization costs inside its stated boundary.

| Field | Unit / meaning |
|---|---|
| pending_connections, active_requests, ready_owners | instantaneous scheduling counts, not portfolio/order state |
| max_pending_connections | configured queue bound |
| queue_backpressure_rejections, owner_backpressure_rejections | existing global/owner admission failures |
| deadline_rejections, cancelled_requests | existing scheduling outcomes |
| results | seven fixed bins: ok, permission_denied, invalid_tool, rejected, duplicate, uncertain, error |
| responses_delivered | complete local frame write succeeded; NOT proof of client consumption, order fill or durable commit |
| response_write_failures | attempted local frame write failed; cannot imply a safe mutation retry |
| queue_wait_latency | nanoseconds from decoded request handoff to execution worker, including queued audit work but excluding socket ingress |
| execution_latency | nanoseconds from execution worker admission through host result, excluding post-result audit and response write |
| response_write_latency | nanoseconds spent encoding and writing the result frame, including failed writes |

The three summaries reuse the existing pure numeric latency accumulator. They
contain samples, total/max/last nanoseconds, fixed **non-cumulative** buckets and
a saturation flag. At saturation the operational report omits unreliable
Prometheus counters/histograms; it does not wrap to a healthy zero. Global
backpressure that closes a socket before constructing a response is counted by
the dedicated backpressure counter, not by the response bins. Dropped responses
remain distinct from application-level rejection. Unknown statuses map to the
fixed error bin rather than adding an unbounded label.

No token, account, order ID, tool arguments, caller-selected tool name or free
text reason becomes a metric label. The epoch is runtime-generated. An invalid
epoch supplied to the serialization helper produces no observation.

## Installed report

The already installed `hepta_oms_report.py` accepts `--kind gateway`; default
`--kind oms` retains its previous interface. Use a stable export from ONE
service, not a concurrently growing log file:

```sh
python3 /usr/libexec/heptatrader/hepta_oms_report.py \
  --kind gateway --input /private/export/gateway.jsonl --format prometheus
```

The parser pins a bounded nonblocking/no-follow regular file, rejects duplicate
keys and invalid numeric/accounting values, and rechecks file identity. The
report distinguishes stale/future observations, current queue saturation,
metric saturation and **new** write/backpressure failures within a same-epoch
interval. It does not keep paging forever because a historical counter is
nonzero. A restart, counter regression, non-increasing monotonic time or a gap
larger than the freshness budget suppresses interval deltas rather than
inventing continuity. A single sample has no interval-failure rate.

Exit codes: 0 means the selected report has no detected alerts, 1 means alerts,
2 means invalid/missing evidence. None means trading readiness. Exact p99 is not
reported; histogram quantiles are bucket upper bounds. p99.9 is withheld below
1,000 samples. Prometheus emits cumulative buckets and `+Inf = count`.

## Executed coverage and limits

`tests/unix_tool_server_tests.cpp` checks measurements after real accepted,
permission-rejected and malformed local requests, with concurrent clients and
queue pressure. `test_gateway_observability.py` executes CLI, parser, reset/gap,
accounting and histogram behavior. The existing installed-process partition
runs the actual packaged Gateway and packaged reporter with distinct UIDs.

This does not implement all portfolio/PnL/quote/connection metric series or
prove host collection, notification delivery or an operational SLO. Those
remain external or unimplemented requirements in the observability inventory.

## Optional Supervisor lease capacity

Current Gateway producers append `lease_store.schema_version=1` without changing
the gateway-metrics v1 authority model. The native serializer and installed
reporter validate distinct active/fence/recovery/finalizing counts, acknowledgement
history bytes/groups and exact canonical envelope/headroom arithmetic. The source
of these counters is the lease store's cached serialization observation. Old
producers omit this object and export `hepta_supervisor_lease_capacity_present 0`.

Known capacity exports fixed `hepta_supervisor_*` gauges. Unknown/indeterminate
capacity omits numeric gauges instead of publishing apparently healthy zeroes.
`hepta_supervisor_lease_persist_seconds_count`, `_sum` and `_max` describe complete
persist attempts (including failure), not committed operations. They reset on
restart and are omitted when saturated. The report distinguishes indeterminate
persistence, unavailable capacity and paused admission. None authorizes trading.

The lease history fixture emits `heptatrader.lease-history-cost.v1` observations
for small and near-reader-limit synthetic HSL8 histories. It exercises real
reopen, fence, removal and oldest-token rejection; it is not a qualified Broker
receipt producer or evidence of physical power-loss survival.
