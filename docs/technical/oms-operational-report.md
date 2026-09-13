# OMS operational report and histogram export

Status: CURRENT
Applies to: read-only reporting of one Execution service's capacity observations
Implementation: `scripts/hepta_oms_report.py`, `HeptaTrade/oms_capacity_observation.h`, `HeptaTrade/oms_latency_observation.h`
Tests: `tests/python/test_oms_operational_report.py`, `tests/python/test_installed_runtime_processes.py`

## Producer and collection boundary

Both Execution daemons publish capacity and actual append/data-sync/replay
validation measurements through the existing five-second observation stream.
Messages include the immutable service_epoch and monotonic_ms from the running
process. This epoch is a process identity, not a token, account, command or new
trading permission. It resets on service restart. Existing capacity/latency
units and exclusions are in [runtime costs](runtime-cost-observations.md).

CMake installs hepta_oms_report.py in libexec/heptatrader. It reads a consistent
regular-file export of ONE service's JSON messages and writes stdout only.
It never opens the OMS journal, changes admission, contacts PID 1, sends a
notification, opens a listener, or calls a Broker. Deployment owns collection
from service-manager stdout, routing, retention and atomic publication into its
existing monitoring system. This component does not invent another daemon.

Example against an already collected, stable service-log snapshot:

```sh
/usr/libexec/heptatrader/hepta_oms_report.py --input /var/tmp/oms-observations.jsonl
/usr/libexec/heptatrader/hepta_oms_report.py --input /var/tmp/oms-observations.jsonl --format prometheus
```

These commands use current wall time. `--now-ms` is for a deliberately historical
audit/test and must not be used to make a stale operational sample look fresh.
The input is at most 64 MiB, each line at most 65,536 bytes, at most 100,000 lines;
only the latest 120 relevant observations are held. Leaf symlinks, hard links,
FIFOs, duplicate JSON keys, numeric overflow/underflow and file replacement are
rejected. Non-JSON log lines and unrelated JSON schemas are ignored. Parent
namespace custody belongs to the caller. No raw input or pathname is echoed in
errors; unknown extra fields, even secrets, are not copied into output.

## Growth and operational decisions

Capacity presence, pending counts, written bytes/records, thresholds and
headroom are revalidated. Unknown data is not a healthy zero. Freshness defaults
to 15,000 ms; the inclusive boundary is accepted, later samples are stale and
future wall timestamps report a clock anomaly. A caller may specify a positive
`--max-age-ms` for its explicit collection budget; this is not a quote TTL.

Growth uses the oldest contiguous valid observation in the bounded same-epoch,
same-capacity-policy suffix. Inter-sample monotonic time must increase and be
within the freshness budget; bytes/records cannot regress. Restarts, unknowns,
gaps, policy changes and counter decreases reset the trend. The output has
window_ms, bytes_per_second, records_per_second and an advisory minimum
headroom-seconds estimate. No growth gives null ETA, not proof of infinite
capacity. Single samples or missing legacy epoch evidence give null trend.
The estimate is not a safe-to-restart decision or a prediction of future load.

`--planning-seconds N` enables a planning warning for the explicitly selected
horizon. Default 0 disables prediction-based alerts. Exit 0 means a valid report
with no alerts, 1 means a valid report containing alerts, 2 means invalid input.
The command emits stable rule IDs, not executable trade or shell commands:

| Rule | Severity | Operator meaning |
|---|---|---|
| OMS_WRITER_POISONED | P1 | durability is untrustworthy; preserve state and follow fenced recovery |
| OMS_CAPACITY_WARNING / EXCEEDED | P2 | plan capacity before restart; exit evidence must remain writable |
| OMS_HEADROOM_PLANNING | P2 | measured recent growth crosses the explicit planning horizon |
| OMS_CAPACITY_UNKNOWN | P2 | inspect missing/invalid capacity; do not substitute zero |
| OMS_TELEMETRY_STALE / CLOCK | P2 | verify collection/time; no healthy-silence interpretation |
| OMS_METRIC_SATURATED | P2 | do not use saturated counters for ratios/distributions |

Alert delivery and response drills remain host acceptance, not a consequence
of generating JSON. A collector must treat tool failure and missing exports as
failures; retaining a last successful .prom file without freshness monitoring
would conceal an outage. Protect exported files, publish atomically using the
existing collector, and keep numeric scrape timestamps/freshness visible.

## Histogram semantics

Each real latency observation increments one bounded internal bucket. Finite
inclusive upper bounds in nanoseconds are 1,000; 10,000; 100,000; 1,000,000;
5,000,000; 10,000,000; 50,000,000; 100,000,000; 1,000,000,000; 10,000,000,000.
The eleventh bucket is unbounded. Internal bucket_counts are noncumulative;
bucket_upper_ns includes null for infinity. All increments saturate, never wrap.
The JSON reporter provides nearest-rank p99 and (with >=1,000 samples) p99.9
BUCKET UPPER BOUNDS, not exact or interpolated latency percentiles. An unbounded
bucket, missing distribution or saturated metric yields null.

The Prometheus text mode emits cumulative inclusive le buckets in seconds,
with +Inf equal to _count and _sum in seconds. Names begin
hepta_oms_append_latency_seconds, hepta_oms_data_sync_latency_seconds and
hepta_oms_replay_validation_latency_seconds. No high-cardinality command,
account, reason or epoch labels are created. Absent/saturated histograms are
omitted, not manufactured as zeros. The numeric capacity/freshness gauges use
fixed names and explicit known/fresh signals. This implements classic histogram
text, not an OpenMetrics/native-histogram server. See Prometheus's official
[metric-types contract](https://prometheus.io/docs/concepts/metric_types/) for
cumulative bucket/count/sum semantics (checked 2026-09-13).

## Journal lifecycle and evidence limits

Capacity warning is preventive observation, not compaction. Before restarting,
preserve the stopped-state journal/lease/key as one trusted consistent unit,
measure memory and full recovery duration on a copy with a proposed supported
budget, and verify the chosen previous/candidate artifact pair. Raising a
budget does not repair corruption, prove terminal economic state or resolve an
uncertain external send. At maximum supported budget, plan a reviewed schema/
checkpoint transition before it is reached; there is no automatic safe ledger
reset. Never logrotate/copytruncate the authority journal or expire command IDs.

The installed-process test invokes the actual installed helper using actual
capacity messages before/after a persisted-state restart, checks histogram
counters and verifies that the restarted epoch resets growth. Other fixtures
exercise the real C++ histogram formatter and hostile report input, actual
fsync failure, SIGKILL after persistence, scoped rate histories and bounded
recovery cycles. They do not establish multi-day stability, real notification
delivery, unlimited ledger retention, all-runtime metrics or IB qualification.
