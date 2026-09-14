# Journald collection and executable alert delivery

Status: CURRENT
Applies to: optional, operator-enabled monitoring for one canonical service pair
Implementation: `scripts/hepta_telemetry_collect.py`, `scripts/hepta_oms_report.py`, `systemd/monitoring/`
Tests: `tests/python/test_telemetry_collection.py`, `tests/monitoring/rules.test.yml`, `tests/monitoring/process_smoke.py`

## What is delivered

The installed collector closes the source-side path from journald observations
through atomic textfiles to node_exporter, Prometheus rules and Alertmanager.
The templates are installed as **examples**, not activated host configuration.
The collector never reads a trading journal, token, lease or Broker credential,
opens a listener, connects to a Broker, changes admission, or sends a trading
command. Alertmanager, not the collector, routes notifications.

`--profile simulator` selects `hepta-execution-simulator.service` and
`hepta-tool-gateway.service`. `--profile ib-paper` selects the IB PAPER Execution
unit and the same Gateway. The latter only observes logs; it neither enables nor
qualifies PAPER. Arbitrary units, executable paths, command fragments and fake
clock overrides are not CLI inputs. Multi-instance deployments must specify and
review their own collection mapping; this version does not silently collect all
`@` instances into one healthy output. Use one output directory and exporter
target per monitored pair.

## Input, identity, time and resource bounds

The collector executes absolute `/usr/bin/journalctl` without a shell or inherited
secret-bearing environment. It requests the current boot and at most 4,096 rows.
Each command has a four-second deadline and an eight-MiB stdout limit. Exceeding a
bound, nonzero exit or timeout rejects the whole result; no valid prefix is
published as successful. The child is reaped and failed collection is visible.
Rows are parsed incrementally with a 128-KiB bound, never split into an unbounded
list. At most the latest 120 supported observations are retained per source.

Only envelopes whose trusted journald `_SYSTEMD_UNIT` equals the selected unit
contribute. `_BOOT_ID` and `_SYSTEMD_INVOCATION_ID` bind the incarnation. Manager
messages included by `--unit` cannot impersonate daemon telemetry. A new
invocation, even one with only a startup message, discards the prior healthy
window. Mixed service epochs inside one invocation fail. Duplicate JSON keys,
invalid UTF-8, non-finite/underflowed numbers, partial records and invalid native
accounting also fail. Parent namespace trust remains a host responsibility.

Wall time is read **after** collection and parsing. Slow collection cannot make
an expired sample look fresh using its start time. The existing reporter keeps
units, histogram semantics and same-epoch trend rules. See
[the report contract](oms-operational-report.md). No latency SLO is invented.

A caller-owned, non-group/world-writable output directory is required. One
nonblocking collection lock covers observation and publication, preventing an
older overlapping read from winning the final rename. Directory/lock identities
are rechecked before output; existing per-file publisher locks remain intact.
Failure of one source does not prevent attempting the other source. Outputs are
`hepta_oms.prom` and `hepta_gateway.prom`; no raw log payload is printed.

Exit 0 means both reports have no alerts; 1 means valid reports with alerts;
2 means at least one collection/publication failed. A valid warning is not a
collector crash. On input failure the publisher replaces old healthy series with
explicit collector failure. If publication itself fails, the old timestamp is
left detectable by external monitoring. No rename/fsync failure is called a
successful publication or silently rolled back.

## Deployment procedure

First verify the immutable package and static host as described in
[installation](../operations/install.md). CMake installs the collector beside its
reporting module. Provision a dedicated, locked-down `hepta-observer` system
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

These are explicit operator actions, not package-install hooks. The service's
StateDirectory creates `/var/lib/hepta-telemetry-simulator`, privately writable
by the observer. It has a private network, no effective capabilities, a 128-MiB
memory ceiling, 16 tasks and a 15-second service deadline. The timer runs five
seconds after the last collection finishes; it does not overlap the service.
`SuccessExitStatus=1` distinguishes runtime alerts from collection failure.

Configure a trusted node_exporter with:

```text
--web.listen-address=127.0.0.1:19100
--collector.disable-defaults
--collector.textfile
--collector.textfile.directory=/var/lib/hepta-telemetry-simulator
```

Merge the example scrape/rule configuration into the host's reviewed Prometheus
configuration; bind the server and Alertmanager to loopback unless separately
reviewed TLS/auth controls exist. Copy `hepta.rules.yml.example` to the selected
rule path without changing its units. Configure TSDB retention explicitly for
that host. Replace the example loopback webhook with the operator's approved
receiver and protected credentials. The example receiver is deliberately not a
real notification destination. Validate with `promtool check config`,
`promtool check rules` and Alertmanager's `amtool check-config` before activation.

## Alert behavior and acceptance

The supplied rules cover missing/unreachable scrape targets, absent/failed
collectors, dead collectors retaining old healthy files (30 seconds), expired
source observations (15 seconds), future clocks, poisoned OMS writes, unknown or
low recovery headroom, pending-queue pressure, Gateway delivery/backpressure and
metric saturation. They never infer that missing metrics mean observed zero.
They do not express complete portfolio, callback lag or Broker health coverage.
No notification performs a cancel, retry, restart or authority change.

`promtool` tests execute the exact production rule file, including inclusive
30/15-second boundaries, missing signals, future clocks and capacity boundaries.
The process acceptance runs the real collector/publisher, node_exporter,
Prometheus and Alertmanager against synthetic journal envelopes. It observes a
firing webhook, an actual HTTP 503 and successful retry, a resolved webhook, and
a dead-collector alert while the old healthy `.prom` bytes remain unchanged. It
uses loopback only and never shortens the production staleness thresholds.
The existing documentation job runs this check; no new approval/check context
is added, and the downloaded Debian tools are extracted without starting host
services. The evidence reports binary and rule hashes and its synthetic scope.

On the real deployment, repeat the receiver drill, disable the timer to verify
dead-collector detection, interrupt one source, and test recovery with the
approved receiver. Preserve measured timestamps and actual receiver acceptance.
A source CI PASS is **not** proof that this host, its service permissions,
retention, paging destination, network path, on-call response or multiday runtime
has passed. Never substitute this synthetic receipt for target-host acceptance.

## Primary interface references

Prometheus rule testing and Alertmanager routing use their official contracts:
https://prometheus.io/docs/prometheus/latest/configuration/unit_testing_rules/
and https://prometheus.io/docs/alerting/latest/configuration/ . Journald JSON
producer fields are defined by systemd's journalctl and journal-fields manuals.
