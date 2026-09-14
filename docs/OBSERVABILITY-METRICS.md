# Observability contract and implementation inventory

Status: CURRENT
Applies to: canonical runtime; implemented outputs are distinguished from requirements

## Implemented outputs

| Output | Producer / access | Semantics and tests |
|---|---|---|
| OMS latency summaries | `OmsJournal::GetHealthSnapshot` and the existing daemon capacity messages | append, file-data-sync and replay-validation sample counts, sum/max/last nanoseconds and bounded histograms; [measurement boundaries](technical/runtime-cost-observations.md), native observation and injected sync-failure tests; bucket upper-bound quantiles, not exact percentiles or full recovery SLOs |
| Installed OMS report/export | `libexec/heptatrader/hepta_oms_report.py` | same-epoch capacity growth, advisory headroom, executable alert classification and classic Prometheus histogram text; [report contract](technical/oms-operational-report.md), hostile and installed-process tests |
| Structured OMS capacity | `OmsJournal::GetHealthSnapshot`, both Execution daemons; service-manager logs | byte/record budgets, headroom, pending records, poisoned/unknown state; [online capacity](technical/oms-live-capacity.md), native durability and installed process tests |
| Ordered execution events | Execution event hub and HEV2 feed | service/stream identity, sequence and gap handling; `tests/execution_event_feed_tests.cpp`, [event contract](technical/execution-events.md) |
| Command and read status | Execution protocol via Gateway | typed reasons, stable command identity and authoritative read state; coordinator, protocol and simulator tests |
| Owner-scoped health publication | `HeptaTrade/events/owner_scoped_health_publisher.cpp` | owner-scoped event delivery; not a substitute for complete portfolio state or an exporter |
| Immutable package/qualification receipts | release and qualification verifiers | exact source/artifact/harness/profile scope; package success never authorizes PAPER/LIVE |

OMS JSON fields have explicit units, presence and cadence in the linked contract.
Do not infer that all previously requested metrics are implemented because this
document is CURRENT. Existing C++ health fields are not automatically exported.

## Requirements not yet delivered as a complete metric interface

Gateway request/worker saturation and latency histograms; per-reason lifecycle
counters; measured complete recovery SLOs;
callback lag; all snapshot ages/generations; portfolio notional/PnL/drawdown series;
connection/refresh duration; and network-policy state still need individually
specified names, types, units, cardinality bounds, collection points and tests
before a deployment can claim the complete monitoring contract.

The installed one-shot OMS helper exports Prometheus text and classifies alerts.
It is not a network exporter, log follower or notification service. Deployment
must configure and test actual collection, retention, atomic metric publication
and alert delivery; no source-only PASS proves those host controls.
Do not turn missing telemetry into a healthy zero. Never emit credentials,
session tokens, encrypted-store plaintext or Broker secret material.

## Alert and authority distinction

[Alert rules](ALERT-RULES-BASELINE.md) distinguish runtime safety incidents,
capacity planning and repository admission failures. A later unrelated main/PR
CI failure blocks that revision's promotion; it does not revoke an already
qualified immutable artifact. Evidence that the running artifact or its actual
safety boundary is compromised remains an operational stop/fence condition.

## Gateway and pending queue implementation update

The actual Gateway now publishes fixed result bins, local response-write
outcomes, scheduling gauges and three latency histograms. The existing installed
reporter selects them with `--kind gateway`; see
[the measured boundaries](technical/gateway-runtime-observability.md). OMS also
exports [pending buffer limits and occupancy](technical/oms-pending-queue.md).
Other portfolio, quote, callback and host-notification requirements above are
not implied complete by these additions.

OMS `bytes` and headroom charge decoded history even in gzip mode; the added
`storage_bytes` and `gzip_storage` fields distinguish physical use from replay
capacity. See [storage maintenance](technical/oms-archive-lifecycle.md).
