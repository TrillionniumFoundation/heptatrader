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
| Gateway scheduling, results and writes | actual Unix Gateway observations | fixed result bins, pending/active/ready gauges, delivery failures and three latency histograms; application success is distinct from socket delivery; [contract](technical/gateway-runtime-observability.md) |
| Read-only OMS/Gateway report | installed `hepta_oms_report.py`, `--kind oms` or `gateway` | validated samples, epoch-bounded growth, advisory headroom, fixed alert classification and classic Prometheus text; no network listener |
| Atomic metrics textfile publication | same helper, `--format prometheus --output-dir` | fixed names, writer exclusion, atomic replacement, collection/sample timestamps and explicit failure; [publication contract](technical/oms-operational-report.md) |
| Bounded journald collection | installed `hepta_telemetry_collect.py` | fixed service profiles, trusted journal unit/invocation identity, bounded command output/deadline and serialization of read plus publication; [collection contract](technical/telemetry-collection.md) |
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

Per-reason execution lifecycle counters, full recovery duration/SLOs, callback
lag, all snapshot ages/generations, portfolio notional/PnL/drawdown, connection/
refresh duration and network-policy state still need individually specified
names, types, units, cardinality bounds, collection points and behavior tests.
Existing C++ fields do not automatically constitute exported metrics.

The source now supplies collection and integration examples. Deployment still
owns the trusted observer identity, actual installation/activation, target
mapping, retention, approved receiver and protected receiver credentials. The
current collector covers one named canonical pair, not arbitrary multi-instance
services. Examples and source CI cannot prove a trading host is configured.

## Behavioral and host acceptance

`test_telemetry_collection.py` exercises the real parser, publisher, subprocess
limits and failure behavior using synthetic journal envelopes. The process
acceptance launches real node_exporter, Prometheus and Alertmanager and requires
loopback webhook firing, HTTP 503 retry, resolution and dead-collector detection
with an unchanged old healthy file. Promtool tests execute the same production
rule file, including exact time and capacity boundaries. See the collection
contract for the evidence scope; no synthetic receipt is target-host evidence.

On the target host, demonstrate collection of actual daemon output, stopped
publication, stale/missing/malformed input, denied output writes, writer poison,
capacity warning and delivery to the actual operator. Preserve service/artifact
identity and measured timestamps. Test OMS and Gateway independently. The
five-second observations, 15-second source age and 30-second collector age are
explicit baseline choices, not a universal host SLA or latency promise.

Native durability/queue tests, report and publication tests, Gateway tests and
installed-process acceptance remain required. Actual host notification,
multiday stability and complete runtime telemetry remain open host/lifecycle
work. Do not close those gaps merely because the source-side chain is tested.

## Alert and authority boundary

[Alert rules](ALERT-RULES-BASELINE.md) distinguish safety incidents, planning and
repository failures. An unrelated later main/PR CI failure affects that
revision's promotion, not an already qualified immutable artifact. Evidence of
a compromised running artifact or safety boundary remains a stop/fence issue.
No metrics file may disarm a kill switch, mint a session or authorize trading.
Never export credentials, tokens, encrypted-store plaintext or Broker secrets.
