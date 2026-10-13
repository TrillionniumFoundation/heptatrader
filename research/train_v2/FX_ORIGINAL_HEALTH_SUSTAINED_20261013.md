# Two-hour original IB PAPER source-health durability audit

2026-10-13. RESEARCH ONLY. NO_TRADE. Product and monitoring cutover HOLD.

## Scope

The frozen four-entry / 16-horizon original EUR/USD price experiment has now
finished: four preaction entries were CENSORED, 16/16 dependent prices were
CENSORED and zero new broker orders were sent. For the three original future
readiness snapshots, source status was nominally READY/24 but respectively
37,938 / 86,233 / 85,031 milliseconds old. The original 30-second age gate
correctly rejected these. The first window's full health bytes were not
captured at decision time, so their exact content is forever unknown.

Original source-health producer uses a 60-second sleep AFTER full assessment.
Two short 15-second isolated pilots showed improved freshness, including a
24/24 fully retained and independently audited pilot (worst between-publication
source age 25,952 ms). Neither is a multi-hour SLA or production approval.

This new module runs 480 individually future-precommitted health assessments
at EXACTLY 15-second intended start intervals. That is two hours of prospective
research. It creates a separate immutable receipt and exact full original
status JSON for every cycle (private 0600 O_EXCL and fsync) and a SHA256-chained
original cycle record. It records genuine source HOLDs, assess CPU overruns,
unavailable observations, missing events, and publication lateness WITHOUT
retrying or creating synthetic READY.

All original PAPER source config SHA, period, original production monitor
code SHA, candidate sandbox monitor code SHA, research runner SHA, 24-probe
readiness rules, market quote TTL=5s, path-gap threshold=30s and source-health
decision age=30s are pinned BEFORE observing the first cycle.

The new standalone research source runner is
research/train_v2/fx_original_health_sustained.py and the
stdlib-only independent verification is
research/train_v2/fx_original_health_sustained_audit.py.
Earlier fullcycle code and the original installed service stay unchanged.

## Source and timing hard gates

For each target, record the true original assessment start and status at_ms,
the producer's own 24 source probes, actual SOURCE prefixes and exact SHA,
original as-of quote age, publication timestamp, operator-review-only
READY or genuine original market-data HOLD, and the prior receipt SHA.

Worst source age just before the NEXT actual publication is the essential
metric; checking only freshness immediately after publication would falsely
pass. Any missing status, changed source, later reported original health
clock, 30-second age violation, or non-qualifying original quote must remain
HOLD/CENSORED. Never relax source quote TTL, redefine the old fixed windows,
change model/trader behavior, fill absent broker reports or rewrite history.

READINESS is not trading authorization and 480 overlapping operational probes
are NOT 480 independent alpha samples. A local hash chain is NOT IB-signed
market data or a canonical Root/Owner/Witness.

The research service uses a transient user unit: 50 percent of one CPU core,
384MiB limit, low scheduling priority, AF_UNIX-only sockets, NoNewPrivileges,
and Restart=no. It cannot reach IB Gateway over TCP or submit orders.

## What would count as PASS

The standalone test requires 480/480 complete 24-probe source records,
480/480 SHA256-chained private receipts, stable original timing and 30-second
source-as-of age BETWEEN EVERY PAIR OF PUBLICATIONS. Genuine market HOLD
remains HOLD and source-state availability is reported separately.
The independent reader must verify the entire original bytes and timestamps.
Unattended production monitor cutover remains on HOLD even after a short
two-hour success, pending restart/rollback, resource contention, broker
disconnect/recovery, day boundary, cold boot and multi-session proof.

Current strategies, Laya training, paper account PnL, IB Flex/Activity/NAV
reconciliation, original 60-minute native Train V2 FeatureSpec cap,
actual after-cost fills, independent behavior propensities, canonical
Owner/LedgerWriter/Witness and model promotion are unchanged and on HOLD.
