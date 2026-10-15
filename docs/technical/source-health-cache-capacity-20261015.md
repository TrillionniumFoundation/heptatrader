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
