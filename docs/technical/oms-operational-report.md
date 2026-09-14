# OMS operational report and histogram export

Status: CURRENT
Applies to: read-only OMS/Gateway observations and optional metrics textfile publication
Implementation: `scripts/hepta_oms_report.py`, `HeptaTrade/oms_capacity_observation.h`, `HeptaTrade/oms_latency_observation.h`
Tests: `tests/python/test_oms_operational_report.py`, `tests/python/test_metrics_publication.py`, `tests/python/test_installed_runtime_processes.py`

## Producer and collection boundary

Both Execution daemons emit real capacity and append/data-sync/replay-validation
measurements every five seconds. Gateway emits its own result/write/queue
observations. Messages carry process `service_epoch`, `observed_at_ms` and
`monotonic_ms`; epoch changes on restart and is not a credential or permission.
[Runtime costs](runtime-cost-observations.md) and [Gateway observations](gateway-runtime-observability.md)
define each measurement's scope and exclusions.

The installed `/usr/libexec/heptatrader/hepta_oms_report.py` reads a consistent
regular-file export of ONE service's JSON messages. It does not read the OMS
journal, follow logs, contact PID 1, open a listener, send notifications or call
a Broker. Deployment supplies the log snapshot and scheduling. The same helper
can print a report or safely replace one metrics textfile; no extra daemon is
introduced.

```sh
/usr/libexec/heptatrader/hepta_oms_report.py --input /var/tmp/oms-observations.jsonl
/usr/libexec/heptatrader/hepta_oms_report.py --input /var/tmp/oms-observations.jsonl --format prometheus
```

Without `--output-dir`, stdout behavior is unchanged. `--now-ms` is available
only for deliberate historical audits/tests, never for operational publication.
Input bounds are 64 MiB, 65,536 bytes/line and 100,000 lines; only the latest 120
matching observations are held. Symlinks, hard links, FIFOs, duplicate keys,
unrepresentable numbers and changed file identity fail. Unrelated schemas and
non-JSON lines are ignored. The caller owns the input parent namespace. Errors
never echo raw data or paths; extra fields are not copied into metrics.

## Atomic publication and failure semantics

Prepare an existing publisher-owned output directory, separate from journals,
credentials and tokens. A read-only monitoring identity may traverse/read it.
For example, after a host administrator creates and assigns such a directory:

```sh
/usr/libexec/heptatrader/hepta_oms_report.py \
  --input /var/tmp/oms-observations.jsonl --kind oms --format prometheus \
  --output-dir /var/lib/hepta-metrics
/usr/libexec/heptatrader/hepta_oms_report.py \
  --input /var/tmp/gateway-observations.jsonl --kind gateway --format prometheus \
  --output-dir /var/lib/hepta-metrics
```

Outputs are exactly `hepta_oms.prom` and `hepta_gateway.prom`. Publication requires
Prometheus format and the real wall clock; `--now-ms` is rejected before writes.
The helper never creates/chowns directories or grants the publisher journal or
Broker access. Output parents are opened descriptor-wise, no symlinks; root or
publisher ownership and no other-writer permission are required. Root-owned
sticky intermediates support disposable `/tmp` tests. The final directory must
belong to the publisher. This is a trusted same-UID namespace, not isolation
from malicious code already running as that UID.

A fixed private mode-0600 lock file and nonblocking exclusive flock serialize
writers per kind. The lock is not unlinked. New numeric output is bounded to
1 MiB, written to an exclusive same-directory temporary, set to mode 0644 and
fsynced. Pinned temporary/target/lock/directory identities are checked before
atomic replacement and directory fsync. Existing links, special files, wrong
owners/modes and namespace substitutions fail. Failure before replace preserves
the previous file; failure after replace/directory-sync is uncertain and never
claims old bytes survived. Process interruption may leave a private temporary,
not a partially published `.prom` file.

| Situation | Output / exit |
|---|---|
| Valid fresh input, no alerts | numeric report and collection timestamps; exit 0 |
| Valid input with alerts, including stale/future data | report with `telemetry_fresh=0` where applicable; exit 1 |
| Missing/malformed input | atomically replace old series with collection failure and `telemetry_fresh=0`; no invented capacity/latency zeros; exit 2 |
| Unsafe/unwritable output or another writer holds lock | no claimed successful publication; exit 2; retain last file if replace did not occur |
| Publisher stops running | no rewrite; consumers MUST detect old timestamps or absent expected series |

`hepta_<kind>_collector_success` means parsing completed, not service health.
`collector_timestamp_seconds` is the invocation's current wall time;
`sample_timestamp_seconds` is the source observation time and is omitted on
invalid input. A valid stale sample has collector success 1 but telemetry fresh
0. Timestamps are gauge values, not Prometheus exposition sample timestamps.
They contain no unbounded service/account/order labels.

## Host alert and delivery acceptance

A last successful file can outlive the publisher. For EACH expected host/service,
monitor collector timestamp age, source timestamp age, missing series, collector
failure, telemetry freshness and writer poison/capacity status. Merely checking
that a node exporter is reachable, or that a frozen `telemetry_fresh` equals 1,
is insufficient. Use an expected-target inventory so disappearance of one host
is not masked by another host still emitting the same metric name.

The 15-second source-age default is inclusive and corresponds to the current
five-second source cadence; collection and scrape budgets must be chosen and
measured on the actual host. Alert evaluation must also reject future source or
collector timestamps according to the host's clock policy. No default here is
a certified production SLA. Configure the existing monitoring system, not a new
trading-authority gate.

The target-host drill must stop/restart the publisher, feed missing, malformed
and stale snapshots, deny an output write, exercise a writer-poison observation
and capacity warning, and verify actual operator notification and recovery.
Capture artifact, service and timestamp identities. Source tests demonstrate
safe file behavior, not installed scheduling, scraping, retention or delivery.

## Growth and alert classification

Known/pending counts, decoded bytes, thresholds and headroom are revalidated.
Unknown is never zero. Growth uses the oldest contiguous valid same-epoch,
same-policy suffix. Monotonic time must increase within the freshness budget;
counts/bytes must not regress. Restart, gaps, policy changes and unknown values
reset the trend. No growth yields null ETA, not infinite capacity. The estimate
is neither permission to restart nor a prediction of future load.

`--planning-seconds N` enables an explicit planning horizon; zero disables it.
This option applies only to OMS. Stable rules include OMS_WRITER_POISONED (P1),
OMS_CAPACITY_WARNING/EXCEEDED, OMS_HEADROOM_PLANNING, OMS_CAPACITY_UNKNOWN,
OMS_TELEMETRY_STALE/CLOCK, OMS_METRIC_SATURATED and pending-queue alerts (P2).
Gateway has separate clock/staleness, saturation, backpressure and write-failure
rules. Exact alert classification remains in the existing tested report code.

## Histogram semantics

Internal buckets are noncumulative, with inclusive nanosecond upper bounds
1,000; 10,000; 100,000; 1,000,000; 5,000,000; 10,000,000; 50,000,000;
100,000,000; 1,000,000,000; 10,000,000,000; then infinity. Counters saturate.
Reported p99 and p99.9 (only with at least 1,000 samples) are nearest-rank BUCKET
UPPER BOUNDS, not exact/interpolated percentiles. Infinity, missing distributions
and saturated metrics yield null bounds, not invented samples.

Prometheus text emits cumulative `le` buckets in seconds, with `+Inf` equal to
`_count`, and `_sum` in seconds. OMS names begin `hepta_oms_append_latency_seconds`,
`hepta_oms_data_sync_latency_seconds` and `hepta_oms_replay_validation_latency_seconds`.
Gateway names and fixed labels are in its owning contract. Absent/saturated
histograms are omitted. This is classic histogram text, not an OpenMetrics or
native-histogram server.

## Journal lifecycle and evidence limits

Capacity warning/publication is not compaction. Follow [recovery capacity](oms-recovery-capacity.md)
for the new-entry pause, guarded exits and measured recovery. Preserve stopped
journal/lease/key identities and verify the actual artifact pair. Never apply
logrotate/copytruncate to the authority journal or expire command IDs. Raising
a budget does not prove integrity, terminal flatness or a resolved send.

Existing installed-process acceptance invokes the installed helper on real
before/after-restart daemon observations. New publication tests execute real
file/CLI behavior, writer contention, malicious paths, failure-only replacement,
clock restrictions and injected sync/substitution faults. Native journal tests
retain crash/replay/durability coverage. These do not prove multi-day stability,
all-runtime metric coverage, target-host alert delivery, unlimited retention or
IB PAPER qualification. Existing host/lifecycle gaps remain open.
