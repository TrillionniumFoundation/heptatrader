# Observability contract and implementation inventory

Status: CURRENT
Applies to: canonical runtime; source implementation is distinct from deployed collection

This table is the current inventory. CURRENT means this contract is maintained;
it does not mean every requested metric or host integration has been delivered.

## Implemented outputs

| Output | Producer / access | Meaning and limits |
|---|---|---|
| OMS append, data-sync and replay-validation timing | `OmsJournal::GetHealthSnapshot`, five-second Execution observations | counts, nanoseconds and bounded histograms, including failed attempts; not complete recovery SLOs; [measurement boundaries](technical/runtime-cost-observations.md) |
| OMS capacity and pending queues | same snapshot/observation stream | decoded bytes, records, pending count/bytes, budgets, poison/unknown state; physical gzip size is separate; [online capacity](technical/oms-live-capacity.md), [pending queue](technical/oms-pending-queue.md) |
| Gateway scheduling, results and writes | actual Unix Gateway observations | fixed result bins, pending/active/ready gauges, delivery failures and three latency histograms; application success is distinct from socket delivery; [contract](technical/gateway-runtime-observability.md) |
| Read-only OMS/Gateway report | installed `hepta_oms_report.py`, `--kind oms` or `gateway` | validated samples, epoch-bounded growth, advisory headroom, fixed alert classification and classic Prometheus text; no network listener |
| Atomic metrics textfile publication | same helper, `--format prometheus --output-dir` | fixed output names, writer exclusion, atomic replacement, collection/sample timestamps, explicit failure on missing/invalid input; not a scheduler, log follower or notification service; [publication contract](technical/oms-operational-report.md) |
| Ordered execution events | execution event hub and HEV2 feed | service/stream identity, sequence and gap handling; [event contract](technical/execution-events.md) |
| Command and read status | Execution protocol via Gateway | stable command identity and typed results; capacity-only new-entry refusal is not a global cancel/flatten fence; [capacity guard](technical/oms-recovery-capacity.md) |
| Owner-scoped health | `owner_scoped_health_publisher.cpp` | owner-scoped event delivery, not complete portfolio state or a general exporter |
| Package and qualification receipts | release/qualification verifiers | exact source/artifact/harness/profile evidence; package success is never PAPER/LIVE authorization |

Telemetry presence and freshness must be checked before numerical values. A
valid explicit zero is different from a missing series. Missing/saturated
histograms are omitted; unknown positions or capacity must not be synthesized.
`collector_success=1` means input was parsed, not that the service is healthy.
A valid stale sample still has `telemetry_fresh=0` and a nonzero report exit code.

## Requirements not yet delivered as a complete interface

Per-reason execution lifecycle counters, full recovery duration/SLOs, callback
lag, all snapshot ages/generations, portfolio notional/PnL/drawdown, connection/
refresh duration and network-policy state still need individually specified
names, types, units, cardinality bounds, collection points and behavior tests.
Existing C++ fields do not automatically constitute exported metrics.

The reporter now owns safe textfile replacement; deployment still owns the
service-log snapshot producer, scheduling, scrape configuration, retention and
notification routing. No source test proves these are installed on a trading
host. Each expected service/host needs missing-series and timestamp-age checks;
a surviving file with `telemetry_fresh=1` can outlive a dead collector.

## Host acceptance

Use one existing monitoring stack rather than adding a second control plane.
On the target host, demonstrate collection of real daemon output, a stopped
publisher, stale/missing/malformed input, a denied output write, writer poison,
capacity warning and notification delivery to the actual operator. Preserve
service/artifact identity and measured timestamps. Test OMS and Gateway
independently; the default baseline emits observations every five seconds and
uses a 15-second input-age budget, not a universal host SLA.

The native durability and queue tests, report tests,
`test_metrics_publication.py`, Gateway tests and installed-process acceptance
cover their stated source behavior. Multi-day stability and real alert delivery
remain external evidence under the existing lifecycle/host gaps. Do not close
those gaps merely because textfile publication is implemented.

## Alert and authority boundary

[Alert rules](ALERT-RULES-BASELINE.md) distinguish safety incidents, planning and
repository failures. An unrelated later main/PR CI failure affects that
revision's promotion, not an already qualified immutable artifact. Evidence of
a compromised running artifact or safety boundary remains a stop/fence issue.
No metrics file may disarm a kill switch, mint a session or authorize trading.
Never export credentials, tokens, encrypted-store plaintext or Broker secrets.
