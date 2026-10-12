# HeptaTrader FX source-health cadence SLA: exact original publication clocks

**2026-10-13, pocket4 local UTC+8.** Research-only CPU-budget and
timestamp-custody audit. **No production monitoring deployment or automatic
trading/model admission.**

## Problem proven by the prior source-maturity failures

The fixed first future EUR/USD price observation at 00:20 was correctly
`CENSORED / ORIGINAL_READINESS_HOLD_OR_STALE`; its 15/30/60-minute
outcomes also remain CENSORED, despite the original producer remaining
active. The 120-minute outcome is due separately at 02:20 and **is not**
counted as done here.

The original producer `heptatrader-learning-source-health.service`
uses the unchanged `heptatrader.quote-readiness.v1` 24-probe classification
and CPUQuota=25%. Its default implementation sleeps 60 seconds **after**
each assessment. Observed deployed production inter-assessment intervals
previously reached 75.300 and 76.388 seconds. Fixed price-only entry
requires the producer snapshot's original `at_ms` to be <=30s old at
the specified decision; no path that regenerates a later snapshot may
requalify or relabel an old censored decision.

A separate prior isolated branch `fix/research-health-monitor-cadence-20261013`
already added opt-in sandbox-only `--refresh-seconds 15`, retaining
the 60-second production default and the exact original market-data
readiness rules. It previously measured 4 short cycles at CPUQuota50%
without production deployment. The present audit extends this evidence
with **a more representative resource-constrained run, and an independent
publisher-to-publisher as-of age computation**.

## Real bounded sandbox evidence

Two **new**, one-shot `systemd-run --user` experiments on pocket4
used the unchanged original source file and strict `--sandbox`,
`--max-cycles` (8 / 12), `--refresh-seconds 15`.
Both had AF_UNIX-only socket families, NoNewPrivileges, 384MiB caps,
`Restart=no` and hard runtime limits. Neither wrote to
the current production monitor's output directory. Both exited
naturally with code 0 and recorded no broker queries or orders.

| Requested resource/cadence | 25% CPU / 15s | 50% CPU / 15s |
| --- | ---: | ---: |
| Actual cycles, all READY 24/24 | 8 | 12 |
| Observed complete publish-to-publish intervals | 7 | 11 |
| Longest age when a new snapshot was published | 16,993 ms | 9,397 ms |
| Max age immediately before its replacement publication | **33,986 ms** | **23,977 ms** |
| Intervals violating frozen 30s as-of budget | **7 of 7** | **0 of 11** |
| Aggregate time with stale observable snapshot | **23,097 ms** | 0 ms |
| CPU accumulated | 32.356s | 50.340s |
| Wall time | 136.018s | 172.949s |
| Peak RSS reported by systemd | 116.7MiB | 117.3MiB |

### Why “refresh every 15s” alone is insufficient

Each source monitor captures its real `at_ms` **at the beginning of
assessment**, and publishes the result only after it finishes reading,
hash-verifying and evaluating the 24 source probes. At CPU25% those
calculations occupied 15.6–17.0s per cycle, overriding the nominal
15-second requested start-to-start cadence.

The stronger correctness condition is not merely that the 15-second
timer fired. If source snapshot `i` is published at `P_i`, with
authentic as-of timestamp `A_i`, then all future decisions during
`[P_i, P_(i+1))` are bounded by

```
max_real_snapshot_age_before_next_publish = P_(i+1) - A_i
```

The exact `P_i` and `A_i` were retained in the **original
unmodified bounded service journal**, not synthesized. A stale period is

```
max(0, P_(i+1) - max(P_i, A_i + 30,000))
```

At 25% this produced seven continuous-age budget failures; at 50%
it produced zero in the eleven observed complete publish intervals.
The 50% test has **only ~6.0s observed upper-envelope headroom** and
is still too short for a production SLA, cold boot, reboot recovery,
heavy backpressure, calendar holidays or pathological source activity.
This audit is also **not a newly registered original price prediction
or broker-issued market-data signature**.

## Immutable research artifacts and replay

New standalone verifier:
`research/train_v2/source_health_cadence_sla_audit.py`.

It obtains the two named units' original local `journalctl --user`
lines and accepts only complete, strictly ordered, fixed-count,
no-order/no-authority, finite timestamp and exact-schema events.
It refuses duplicate or corrupted JSON, wrong CPU labels/cadence,
missing or reordered cycles, inconsistency between source `at_ms`
and published age, unexplained clock reversal, unsafe output directory,
and any already-existing output. These are **publisher timing proofs
only**, not independent custody of the underlying IB tick.

Owner-private write-once evidence directory:
`~/.local/state/heptatrader/operator-evidence/fx-health-sla-budget-20261013-v1/`.

Original normalized JSONL and summary `*.review.v1.json` for each
sandbox unit are both `0600`; directory `0700`.
Summary SHA256:

- CPU25%: `926b86e5e60ae31dec68da528780f2ebc3cc7d515d3f86aff9011c15e55faccd`.
- CPU50%: `dd02b8f01063088bf76f6193c16499976fa0c42e36ac844d715506bfcc967a01`.

The first 60m original-quote CENSORED packet was additionally copied
and independently replayed on the physical qian-qi host, matching its
original BEFORE-FIRST-WINDOW plan/seal hashes and all 15/30/60
original attempt timestamps. The physical result SHA256:
`c4b6fa4c79774ca9be9f0d46389d539e5a5129376f16e43d5dbeb59412f4cb1c`.
That packet expressly says no contemporaneous raw 00:20 source health
snapshot had been preserved; **it does not pretend to recover one**.

## Admission constraints

1. **Source monitoring release remains HOLD.** An isolated
   CPUQuota50% result is a *candidate* for later durability work,
   not permission to replace the original 25% monitor. Do not change
   its production `status.json`, cadence, CPU quota or trained
   model's source identity during the already-frozen 03:35, 06:50
   and 10:05 original trial.
2. Re-run the same full-envelope checks under sustained CPU contention,
   first-start/reboot/closed-source conditions, source 24-probe
   continuous coverage, actual future independently recorded health
   snapshots and rollback to the baseline. Quantify the service's
   maximum as-of age **just before** each next publication and at
   actual fixed decision times, not simply as an average.
3. Verify no auto trial registration, original broker action/order,
   model/Agentd Owner/Witness/LedgerWriter, Flex/NAV account PnL or
   trade permission was introduced. Production training and
   economic release remain separate hard HOLD gates.

The current production health service remained active throughout both
bounded labs; research and production PAPER FX/BTC drivers were not
restarted. The prior CENSORED window and frozen source/price records
remain unchanged.
