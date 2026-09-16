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
| IB authoritative runtime state | `hepta-ib-executiond` `heptatrader.ib-runtime-observation.v1`, installed `hepta_ib_runtime_report.py` | one adapter-lock recovery-audit view: connection/event-stream state; active/terminal/risk generations, completeness and bounded counts; gross position; exposure/post-fill reconciliation; terminal-drain state. No account/order/command labels. Callback-lag, callback-conflict and network-policy families have explicit presence `false`, not observed zero. |
| Gateway scheduling, results and writes | actual Unix Gateway observations | fixed result bins, pending/active/ready gauges, delivery failures and three latency histograms; application success is distinct from socket delivery; [contract](technical/gateway-runtime-observability.md) |
| Read-only OMS/Gateway report | installed `hepta_oms_report.py`, `--kind oms` or `gateway` | validated samples, epoch-bounded growth, advisory headroom, fixed alert classification and classic Prometheus text; no network listener |
| Read-only IB runtime report | installed `hepta_ib_runtime_report.py` | validates fixed IB runtime samples and emits fixed-cardinality Prometheus gauges/health alerts; it does not collect journald, mutate state or grant qualification |
| Atomic metrics textfile publication | `hepta_oms_report.py`, `--format prometheus --output-dir` | fixed names, writer exclusion, atomic replacement, collection/sample timestamps and explicit failure; [publication contract](technical/oms-operational-report.md). IB runtime report is not yet wired to this publisher. |
| Bounded journald collection | installed `hepta_telemetry_collect.py` | fixed OMS/Gateway service profiles, trusted journal unit/invocation identity, bounded command output/deadline and serialization of read plus publication; IB state observations are emitted by the daemon but not yet part of this collector. [collection contract](technical/telemetry-collection.md) |
| Scheduling, scrape and alert integration examples | `systemd/monitoring/` | inert observer timer, node_exporter/Prometheus/Alertmanager configuration and executable rules; packaging does not activate a host or supply a real receiver |
| Ordered execution events | execution event hub and HEV2 feed | service/stream identity, sequence and gap handling; [event contract](technical/execution-events.md) |
| Command and read status | Execution protocol via Gateway | stable command identity and typed results; capacity-only entry refusal is not a global cancel/flatten fence; [capacity guard](technical/oms-recovery-capacity.md) |
| Owner-scoped health | `owner_scoped_health_publisher.cpp` | owner-scoped event delivery, not complete portfolio state or a general exporter |
| Package and qualification receipts | release/qualification verifiers | exact source/artifact/harness/profile evidence; package success is never PAPER/LIVE authorization |

Presence and freshness must be checked before numerical values. Missing or
saturated histograms are omitted; unknown state must not become observed zero.
`collector_success=1` means input was parsed, not that the service is healthy.
A valid stale sample still has `telemetry_fresh=0` and a nonzero report exit code.
A surviving file with `telemetry_fresh=1` can outlive a dead collector, so the
supplied rules independently evaluate collection and source timestamps.

## Requirements not yet delivered as a complete interface

Complete per-reason execution lifecycle beyond coordinator call outcomes, full
Broker reconciliation duration/SLOs, callback lag and conflict counters, quote
and snapshot ages, portfolio notional/PnL/drawdown, connection/refresh duration,
network-policy state and atomic/journald publication of the new IB runtime stream
still need individually bounded producers and behavior tests. IB snapshot
**generations/completeness** are now exported; this is deliberately narrower than
snapshot age or callback latency. Existing C++ fields do not automatically
constitute exported metrics.

The source now supplies collection and integration examples. Deployment still
owns the trusted observer identity, actual installation/activation, target
mapping, retention, approved receiver and protected receiver credentials. The
current collector covers the canonical OMS/Gateway pair; the IB state reporter
is a separate read-only source path until collector/publication integration is
added. Examples and source CI cannot prove a trading host is configured.

## Behavioral and host acceptance

`test_telemetry_collection.py` exercises the real OMS/Gateway parser, publisher,
subprocess limits and failure behavior using synthetic journal envelopes. The
IB observation contract has an independent compiler-backed cross-language test
that executes the production C++ serializer and Python validator/exporter.
The process acceptance launches real node_exporter, Prometheus and Alertmanager
and requires loopback webhook firing, HTTP 503 retry, resolution and
 dead-collector detection with an unchanged old healthy file. Promtool tests
execute the same production rule file, including exact time and capacity
boundaries. No synthetic receipt is target-host evidence.

On the target host, demonstrate collection of actual daemon output, stopped
publication, stale/missing/malformed input, denied output writes, writer poison,
capacity warning and delivery to the actual operator. Preserve service/artifact
identity and measured timestamps. OMS, Gateway and the IB authoritative-state
stream must eventually be tested independently. The five-second observations,
15-second source age and 30-second collector age are explicit baseline choices,
not a universal host SLA or latency promise.

Native durability/queue tests, report and publication tests, Gateway tests and
installed-process acceptance remain required. Actual host notification,
multiday stability, IB collector publication and the missing callback/network
metric families remain open host/lifecycle work. Do not close those gaps merely
because the source-side state observation is tested.

## Alert and authority boundary

[Alert rules](ALERT-RULES-BASELINE.md) distinguish safety incidents, planning and
repository failures. An unrelated later main/PR CI failure affects that
revision's promotion, not an already qualified immutable artifact. Evidence of
a compromised running artifact or safety boundary remains a stop/fence issue.
No metrics file may disarm a kill switch, mint a session or authorize trading.
Never export credentials, tokens, encrypted-store plaintext or Broker secrets.
