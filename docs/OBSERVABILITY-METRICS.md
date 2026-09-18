# Observability contract and implementation inventory

Status: CURRENT
Applies to: canonical runtime; source implementation is distinct from deployed collection

This table is the current inventory. CURRENT means this contract is maintained;
it does not mean every requested metric or host integration has been delivered.

## Implemented outputs and collection

| Output | Producer / access | Meaning and limits |
|---|---|---|
| OMS append, data-sync and replay-validation timing | `OmsJournal::GetHealthSnapshot`, five-second Execution observations | counts, nanoseconds and bounded histograms, including failed attempts; not complete recovery SLOs; [measurement boundaries](technical/runtime-cost-observations.md) |
| OMS capacity and pending queues | same snapshot/observation stream | decoded bytes, records, pending count/bytes, budgets, poison/unknown state; physical gzip size is separate; [online capacity](technical/oms-live-capacity.md), [pending queue](technical/oms-pending-queue.md) |
| Coordinator operations, local recovery and state size | `ExecutionCoordinator::RuntimeObservation`, additive `execution_metrics` in Execution observations and the installed report | fixed outcome counts, lock-wait/inclusive/lock-held operation and replay-projection histograms and O(1) identity/owner/index sizes; no per-ID labels; [exact scopes](technical/runtime-cost-observations.md) |
| Coordinator bounded reason counters | same additive `execution_metrics` stream and installed report | 41 fixed reason bins for each of place/cancel/flatten; unknown maps to OTHER, no arbitrary labels; explicit presence and saturation; not all upstream risk/profile decisions |
| IB authoritative runtime state | `hepta-ib-executiond` `heptatrader.ib-runtime-observation.v1`, installed `hepta_ib_runtime_report.py` | one adapter-lock recovery-audit view: connection/event-stream state; active/terminal/risk generations, completeness and bounded counts; gross position; exposure/post-fill reconciliation; terminal-drain state. The reporter additionally derives conservative continuous observed-duration lower bounds for post-fill reconciliation pending, authoritative snapshot incompleteness and terminal callback drain pending, reset at service/connection epoch boundaries. No account/order/command labels. Callback queue-ingress-to-routing lag and bounded callback-conflict counters are native producers with fixed cardinality. Network-policy state remains explicitly absent until a host readback producer supplies it. |
| Gateway scheduling, results and writes | actual Unix Gateway observations | fixed result bins, pending/active/ready gauges, delivery failures and three latency histograms; application success is distinct from socket delivery; [contract](technical/gateway-runtime-observability.md) |
| Read-only reports | installed `hepta_oms_report.py` and `hepta_ib_runtime_report.py` | strict validated OMS/Gateway/IB samples, fixed-cardinality metrics and health classification; no listener and no trading authority |
| Atomic metrics textfile publication | OMS/Gateway publisher plus IB reporter publisher | fixed names (`hepta_oms.prom`, `hepta_gateway.prom`, `hepta_ib.prom`), nonblocking writer locks, descriptor/namespace validation, fsync + atomic replacement and explicit failure; no old healthy series is retained as a synthetic success after invalid input |
| Bounded journald collection | installed `hepta_telemetry_collect.py` | fixed service profiles, trusted journal unit/invocation identity, bounded command output/deadline and serialized read/publication. `simulator` publishes OMS+Gateway; `ib-paper` independently selects OMS+IB schemas from the Execution unit and Gateway from its unit, so one invalid stream does not suppress the other valid files. [collection contract](technical/telemetry-collection.md) |
| Scheduling, scrape and alert integration examples | `systemd/monitoring/` | inert observer timer, node_exporter/Prometheus/Alertmanager configuration and executable rules; packaging does not activate a host or supply a real receiver |
| Ordered execution events | execution event hub and HEV2 feed | service/stream identity, sequence and gap handling; [event contract](technical/execution-events.md) |
| Command and read status | Execution protocol via Gateway | stable command identity and typed results; capacity-only entry refusal is not a global cancel/flatten fence; [capacity guard](technical/oms-recovery-capacity.md) |
| Owner-scoped health | `owner_scoped_health_publisher.cpp` | owner-scoped event delivery, not complete portfolio state or a general exporter |
| Package and qualification receipts | release/qualification verifiers | exact source/artifact/harness/profile evidence; package success is never PAPER/LIVE authorization |

The new fixed-cardinality duration gauges are:

- `hepta_ib_post_fill_reconciliation_pending_observed_ms`;
- `hepta_ib_authoritative_snapshot_incomplete_observed_ms`;
- `hepta_ib_terminal_callback_drain_pending_observed_ms`.

They are **observed lower bounds**, not hidden internal start timestamps. The reporter walks only the retained recent sample window and stops at a service/connection epoch boundary, a false condition or monotonic-clock regression. A value of zero therefore means “not continuously observed across two retained samples”, not “the operation consumed zero milliseconds”.

Presence and freshness must be checked before numerical values. Missing or
saturated histograms are omitted; unknown state must not become observed zero.
`collector_success=1` means input was parsed, not that the service is healthy.
A valid stale sample still has `telemetry_fresh=0` and a nonzero report exit code.
A surviving file with `telemetry_fresh=1` can outlive a dead collector, so the
supplied rules independently evaluate collection and source timestamps.

## Requirements not yet delivered as a complete interface

Complete per-reason execution lifecycle beyond coordinator call outcomes, exact
Broker reconciliation start-to-finish duration/SLOs, quote and snapshot ages,
portfolio notional/PnL/drawdown, connection/refresh duration and network-policy
state still need individually bounded producers and behavior tests. Callback
queue lag and conflict counters are now produced and exported; they do not by
themselves prove Broker or host health. IB snapshot **generations/completeness**
and the three continuous observed-stall duration lower bounds are produced,
reported and collected; this remains deliberately narrower than a native Broker
reconciliation timer, snapshot age or callback latency. Existing C++ fields do
not automatically constitute exported metrics.

Deployment still owns trusted observer identity, actual installation/activation,
target mapping, retention, approved receiver and protected receiver credentials.
Source CI can execute collection/publication behavior but cannot prove that a
trading host runs the collector or that a real operator received an alert.

## Behavioral and host acceptance

`test_telemetry_collection.py` exercises the OMS/Gateway parser, publisher,
subprocess limits and failure behavior using synthetic journal envelopes.
`test_ib_telemetry_collection.py` exercises the IB PAPER three-stream collector,
including independent publication and replacement of an invalid IB stream with
failure-only health while preserving valid OMS/Gateway files. The IB observation
contract also has a compiler-backed cross-language test executing the production
C++ serializer and Python validator/exporter; that test covers the continuous
observed-duration calculations and epoch reset behavior. The process acceptance
launches real node_exporter, Prometheus and Alertmanager and requires loopback
webhook firing, HTTP 503 retry, resolution and dead-collector detection. No
synthetic or loopback receipt is target-host evidence.

On the target host, demonstrate collection of actual daemon output, stopped
publication, stale/missing/malformed input, denied output writes, writer poison,
capacity warning and delivery to the actual operator. Preserve service/artifact
identity and measured timestamps. Test OMS, Gateway and IB authoritative-state
streams independently. The five-second observations, 15-second source age and
30-second collector age are explicit baseline choices, not a universal host SLA.

Actual host notification, multiday stability and the remaining network-policy,
quote/snapshot-age and exact Broker-duration metric families remain open. Do not close those gaps merely
because the source-side chain is executable.

## Alert and authority boundary

[Alert rules](ALERT-RULES-BASELINE.md) distinguish safety incidents, planning and
repository failures. An unrelated later main/PR CI failure affects that
revision's promotion, not an already qualified immutable artifact. Evidence of
a compromised running artifact or safety boundary remains a stop/fence issue.
No metrics file may disarm a kill switch, mint a session or authorize trading.
Never export credentials, tokens, encrypted-store plaintext or Broker secrets.
