# Source-health cache capacity thrash — 2026-10-15

Scope: Pocket4 read-only EUR/USD original-PAPER source-health publication. This
change has no order, broker client, model promotion, owner, or risk authority.
The original 24-probe / >=20 usable / 5-second quote TTL / 30-second status
freshness rules and the full archive-integrity check are unchanged.

## Defect and bounded fix

The archived sealed inventory now exceeds both the 320-segment and 180,000-record
cache caps. In ascending file order the first cache misses evicted the following
still-cached records on **every** sweep. A complete original 24-cycle test at
50% CPU showed **0 cached hits and 9,216 sealed strict reparses**.

The cache now retains the verified recent suffix and strictly parses older
uncached segments without inserting them ahead of that suffix. It still runs
canonical `inventory()` on EVERY call (full original-file SHA-256 and ledger),
rehashes every cache hit with `verify_prefix()` at use, strictly reparses all
unsealed sources, checks period and clock, and performs the same cross-source
price-conflict and availability selection. No record is accepted by metadata
alone; memory limits are unchanged.

## Pocket4 separate real-source results

Both releases were run as distinct private, order-incapable transient user
services with AF_UNIX-only, 384 MiB memory, and no active product source
replacement. The patched release changed just the cache Python module among
56 installed files (SHA-256 `a125a88765f3b212b709ee817fce07d8dded504d93139b0ab6da4db3833f0bc7`).

| Original 24-cycle at 50% CPU | Repaired 24-cycle at 25% CPU |
| --- | --- |
| 0 sealed cache hits | **7,337 verified cache hits** |
| 9,216 sealed strict reparses | **1,879 sealed strict reparses** |
| full canonical inventory each cycle | full canonical inventory **24/24** |
| 384 MiB observed memory peak | **218.8 MiB** observed peak |
| research/no-order only | research/no-order only |

Repaired **warm** evaluations took at most **8,095 ms**. For all 22 warm
publisher-to-publisher intervals, the prior source as-of age before the next
publication was at most **23,095 ms**, with **zero** breaches of the existing
30,000 ms requirement. The first COLD interval was **34,006 ms** and remains
an actual breach, never reclassified. The original price-source probe
population was genuinely **17/24 usable** throughout: 24/24 correct
`HOLD_NEW_FUSION_WINDOW` statuses, **no readiness or model promotion**.

Pocket4 operator-private write-once receipt:
`~/.local/state/heptatrader/operator-evidence/fx-health-suffix25-20261015/verified-summary.json`
SHA-256 `bd1e11556ee887f574ece0291459995d938357da7a5cfe2e7cd3a1dd3b8186fd`.
Exact raw journal SHA-256:
`f3aa402afffe1df173a93fa67d341a07592b383608aaa08b6bbae9d1b6ccc552`.
These local host logs are not independent broker signatures.

## Development versus live source

The patch fixes the observed capacity regression without increasing CPU or
loosening any source integrity threshold. The original on-host health service
and its running PID were left unchanged. The finite original-source campaign
timer was still active at readback, so integration into main must **not**
quietly switch the input of precommitted future decisions. A later controlled
monitor-only release should separately handle the cold-start stale interval,
preserve genuine source HOLDS, compare exact service/release identity and
perform tested rollback. Code merge is not permission for new orders.

## Same-day 50%-CPU cold-to-warm proof (no live cutover)

A **new**, separate finite Pocket4 user service
`heptatrader-health-cold50-20261015-r1` tested the *identical* current main
source monitor SHA-256 `8cbedb6a5f792891f0141f7b2ae0da91a7738080cd14c45995436ed55473c309`
and suffix cache SHA-256 `a125a88765f3b212b709ee817fce07d8dded504d93139b0ab6da4db3833f0bc7`.
The transient unit had `CPUQuota=50%`, `MemoryMax=384M`,
`RestrictAddressFamilies=AF_UNIX`, `NoNewPrivileges=yes`, a private distinct
output directory, `--sandbox --verified-sealed-cache --refresh-seconds 15
--max-cycles 24`, and `Restart=no`. It did not write the original product
monitor status or call Broker APIs.

The original 24 cycles naturally exited with `Result=success`, code 0,
over ~5m49s and observed memory peak **220.3 MiB**. Measured from actual
journal event clocks, `next_published_ms - previous_source_asof_ms` was
**18,607ms** across the cold first interval and at most **18,820ms** across
subsequent intervals: **0/23** exceeded the unchanged 30,000ms limit.
The maximum age at a status's own publication was 12,219ms. All **24**
complete source archive inventories were SHA-checked; 7,337 sealed decoder
hits were individually reverified, 1,879 sealed sources and 264 unsealed/extra
sources were strictly re-parsed. Original genuine usable probes remained
**17–18/24**; every decision was correctly `HOLD_NEW_FUSION_WINDOW`.
Zero Broker orders, zero new trial admissions, no altered original labels.

Pocket4 operator-private exact evidence (write-once):
`~/.local/state/heptatrader/operator-evidence/fx-health-cold50-20261015-r1/`
- `cycle-journal.jsonl` SHA-256:
  `e8ff19926d1e39e9d1e5e791c8719f0586403ab725402e70d25d1263d262169f`
- `final-summary.json` SHA-256:
  `36a5ec8415282af378b4044032907f8e97e4989e913c7181bcf567261160087d`

**Operational decision:** finite source-health publisher cadence at CPU50 is
qualified by this actual cold/warm proof, **not** indefinite operational
uptime, broker-signature authenticity, source readiness, model profitability,
or trading authorization. At readback the original 24-window frozen-price
campaign timer remained active and scheduled further windows. Its input must
NOT change mid-cohort. Once that original cohort naturally reaches terminal,
a bounded **monitor-only** cutover may check current config/release identity,
original order/owner/driver process continuity, the same 30s original-source
age budget, cold/restart and rollback; a local publication change by itself
must never flip an original source HOLD into READY.
