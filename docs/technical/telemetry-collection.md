# Journald collection and executable alert delivery

Status: CURRENT
Applies to: optional, operator-enabled monitoring for canonical simulator and IB PAPER profiles
Implementation: `scripts/hepta_telemetry_collect.py`, `scripts/hepta_oms_report.py`, `scripts/hepta_ib_runtime_report.py`, `systemd/monitoring/`
Tests: `tests/python/test_telemetry_collection.py`, `tests/python/test_ib_telemetry_collection.py`, `tests/monitoring/rules.test.yml`, `tests/monitoring/process_smoke.py`

## What is delivered

The installed collector closes the source-side path from journald observations
through atomic textfiles to node_exporter, Prometheus rules and Alertmanager.
The templates are installed as **examples**, not activated host configuration.
The collector never reads a trading journal, token, lease or Broker credential,
opens a listener, connects to a Broker, changes admission, or sends a trading
command. Alertmanager, not the collector, routes notifications.

`--profile simulator` selects `hepta-execution-simulator.service` and
`hepta-tool-gateway.service` and publishes the OMS and Gateway streams.
`--profile ib-paper` selects the IB PAPER Execution unit and the same Gateway;
from the Execution unit it independently selects the ordinary OMS observation
and `heptatrader.ib-runtime-observation.v1`, publishing `hepta_oms.prom`,
`hepta_ib.prom` and `hepta_gateway.prom`. The profile only observes logs; it
neither enables nor qualifies PAPER. Arbitrary units, executable paths, command
fragments and fake clock overrides are not CLI inputs. Multi-instance deployments
must specify and review their own mapping; this version does not silently merge
all `@` instances into one healthy output.

## Input, identity, time and resource bounds

The collector executes absolute `/usr/bin/journalctl` without a shell or inherited
secret-bearing environment. It requests the current boot and at most 4,096 rows.
Each command has a four-second deadline and an eight-MiB stdout limit. Exceeding a
bound, nonzero exit or timeout rejects the whole result; no valid prefix is
published as successful. The child is reaped and failed collection is visible.
Rows are parsed incrementally with a 128-KiB bound. At most the latest 120
supported observations are retained per selected schema.

Only envelopes whose trusted journald `_SYSTEMD_UNIT` equals the selected unit
contribute. `_BOOT_ID` and `_SYSTEMD_INVOCATION_ID` bind the incarnation. Manager
messages included by `--unit` cannot impersonate daemon telemetry. A new
invocation, even one with only a startup message, discards the prior healthy
window. Mixed service epochs inside one invocation fail. Duplicate JSON keys,
invalid UTF-8, non-finite/underflowed numbers, partial records and invalid native
accounting also fail. Parent namespace trust remains a host responsibility.

For IB PAPER, the OMS and IB parsers consume the same approved Execution unit but
accept only their exact schemas. An unrelated JSON message never becomes an IB
state sample. The IB validator additionally requires `authorization_effect=NONE`,
false PAPER/LIVE authorization flags, bounded reasons, coherent completeness,
valid generation/count relationships and terminal-drain ordering. Missing callback
lag/conflict/network producers are explicit presence `false`, never numerical zero.

Wall time is read **after** collection and parsing. Slow collection cannot make
an expired sample look fresh using its start time. Reporters retain same-epoch
freshness and accounting rules; no latency SLO is invented.

A caller-owned, non-group/world-writable output directory is required. One
nonblocking collection lock covers observation and publication, preventing an
older overlapping read from winning the final rename. Directory/lock identities
are rechecked before output; per-file writer locks remain independent. Each file
uses a private temporary, fsync, namespace recheck, atomic replacement and
directory fsync. Failure of one source does not prevent attempting the remaining
sources. No raw log payload is printed.

On input failure the publisher replaces that stream's old healthy series with
explicit collector failure only. Therefore malformed/missing IB state cannot
leave a stale `hepta_ib_connection_epoch` looking current, while valid OMS and
Gateway publication can still succeed. If publication itself fails, the old
file/timestamp remains detectable externally; no rename/fsync failure is called
a successful publication or silently rolled back.

Exit 0 means all selected reports have no alerts; 1 means valid reports with at
least one runtime alert; 2 means at least one collection/publication failed. A
valid warning is not a collector crash.

## Deployment procedure

First verify the immutable package and static host as described in
[installation](../operations/install.md). CMake installs the collector and both
reporting modules. Provision a dedicated, locked-down `hepta-observer` system
identity with `systemd-journal` read membership; do not reuse Agent, Gateway or
Execution users. This group can read broader host logs, so the observer is a
trusted monitoring identity, not an untrusted Agent sandbox. Review journal
access and log redaction on the actual host.

Review and copy `hepta-telemetry@.service.example` and its timer from
`/usr/share/heptatrader/examples/systemd/monitoring` to the administrator's systemd
unit directory **without** the `.example` suffix. Run `systemd-analyze verify`
against the installed executable/module pair before enabling the chosen timer:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now hepta-telemetry@simulator.timer
systemctl status hepta-telemetry@simulator.service
```

For a qualified PAPER host use the separately reviewed `ib-paper` instance only
after confirming its observer can read the intended unit and cannot access
Broker credentials or execution control paths. Observer setup itself keeps
PAPER/LIVE authorization unchanged.

These are explicit operator actions, not package-install hooks. The example
service has a private network, no effective capabilities, bounded memory/tasks
and a service deadline. The timer does not overlap the service.
`SuccessExitStatus=1` distinguishes runtime alerts from collection failure.

Configure a trusted node_exporter textfile collector against the selected output
directory. Merge the example scrape/rule configuration into the host's reviewed
Prometheus configuration; bind monitoring endpoints to loopback unless separately
reviewed TLS/auth controls exist. Configure TSDB retention and replace the example
loopback webhook with the approved operator receiver and protected credentials.
Validate with `promtool check config`, `promtool check rules` and Alertmanager's
`amtool check-config` before activation.

## Alert behavior and acceptance

The supplied existing rules cover missing/unreachable scrape targets,
absent/failed collectors, dead collectors retaining old healthy files, expired
source observations, future clocks, poisoned OMS writes, recovery headroom,
pending queues, Gateway delivery/backpressure and metric saturation. The new IB
textfile exposes fixed state gauges and reporter alert count, but production rule
promotion for each IB-specific condition should accompany target-host acceptance
rather than inventing unmeasured SLOs. Missing metrics never mean observed zero.
No notification performs a cancel, retry, restart or authority change.

`test_telemetry_collection.py` executes OMS/Gateway parser/publication behavior.
`test_ib_telemetry_collection.py` executes the `ib-paper` three-stream path and
requires independent atomic files; an invalid IB stream becomes failure-only
IB health without suppressing valid OMS/Gateway output. The process acceptance
runs the real collector/publisher, node_exporter, Prometheus and Alertmanager
against synthetic journal envelopes, including receiver HTTP 503 retry,
resolution and dead-collector detection. Loopback evidence is not target-host
or Broker qualification evidence.

On the real deployment, repeat the receiver drill, disable the timer to verify
dead-collector detection, interrupt each source independently and test recovery
with the approved receiver. Preserve actual invocation IDs, source/collection
timestamps, artifact digest and receiver acceptance. A source CI PASS is **not**
proof that host permissions, retention, notification path, on-call response or
multiday runtime passed.

## Target-host acceptance sequence (not executed by source CI)

A real deployment must bind the exact artifact, host configuration and service
invocation before collecting evidence. Keep PAPER/LIVE disabled for observer
setup and perform destructive fault drills only on a disposable clone with
stopped-state copies, never on a trading ledger.

| Acceptance | Evidence to retain | Rejection condition |
|---|---|---|
| Actual service collection | OMS/Gateway and, on IB hosts, IB invocation IDs, source/collection timestamps, artifact digest and successful scrapes | Any selected stream absent, stale, future-dated or mapped to the wrong unit |
| Collector failure | Stop/publication failure for each stream and independent timestamp alert | Old health remains trusted after the specified age |
| Publication failure | Denied output or writer contention on a disposable clone; failure status and bounded exit | A success receipt or stale health is substituted for failure |
| Operator delivery | Approved actual receiver, firing, delivery failure/retry and resolved notification | Only a local log or unapproved test receiver was observed |
| Sustained load and restart | Measured CPU/RSS, event/byte growth, lock wait, local-operation tails, recovery duration, IB generations and retained identities | Budget breach, unexplained growth, duplicate send, stale ownership or missing failure detection |

Choose the observation period and numeric acceptance budgets from the target host
and expected command/callback load before starting; record actual elapsed coverage,
gaps and failures. Do not label a short accelerated fixture as multiday evidence.
The source defaults remain baselines, not verified host SLOs. A monitoring drill
must not disarm a kill switch, rotate an Agent session or authorize Broker
mutations. `HOST-OPERATIONS-003` remains OPEN until the actual host and actual
operator delivery are observed and reviewed.
