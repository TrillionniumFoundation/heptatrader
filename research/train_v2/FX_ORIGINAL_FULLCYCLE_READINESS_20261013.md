# Full-byte original PAPER source health: 24 fixed future cycles

2026-10-13 | pocket4 isolated research. **NO_TRADE / HOLD PRODUCT CUTOVER**

## Motivation

The actual four-window / four-horizon original EUR/USD study completed with
4/4 original preaction decisions CENSORED, 16/16 future price samples CENSORED
and zero new trading orders. The exact unchanged 30-second source-health age
gate refused all four. Three originally captured 24/24 READY statuses were
37,938 ms, 86,233 ms and 85,031 ms old at their respective decisions, and
the 00:20 full status was never captured. Those old failures are permanent.

Earlier isolated source monitor cadence work on a genuine original PAPER feed
measured 24 summary events at 15-second start-to-start cadence and 50% CPU
quota, with worst source age just before next publication 27,241ms. But 7/24
events reported genuine original source HOLD. Crucially, the old experiment
preserved only its final complete original source status JSON, so no
independent verifier could fully recompute every previous status/24 probes
from the summary events.

This branch closes **that observability gap**, not the market-availability or
trading efficacy gap. It does not replace the installed
`heptatrader-learning-source-health.service`, restart FX/BTC trading drivers,
change source rules, broaden quote TTL, repair retrospectively censored
windows, write to IB, fit a model, or approve source promotion.

## Frozen independent future cohort

A new research-only plan is created once before a fresh observation.
Code: `research/train_v2/fx_original_health_fullcycle.py`.

- **24** future consecutive source assessments exactly **15,000ms**
  apart on a fixed UTC epoch grid.
- Original **PAPER** period, source feed directory and config SHA-256
  bound to the existing trusted quote-only cell. Both the installed
  active producer source SHA and opt-in sandbox candidate source SHA are
  frozen before any new study observations.
- Unchanged `heptatrader.quote-readiness.v1`: 24 subprobes,
  20/24+ usable, <=4 missing, 5s original IB quote TTL, 5s
  endpoint tolerance, 30s source path continuity. Readiness permits
  operator review **only**, not automatic study registration.
- One original full JSON document per cycle, 0600 and O_EXCL, with
  its own source `at_ms`, 24 probes and original source-prefix digest;
  separate fsynced SHA256-chained receipt per cycle. A missing or
  delayed source result stays CENSORED/HOLD, not an interpolated READY.
- The original source `at_ms` is *not* updated to publication time.
  Publication age is calculated from immutable local timestamps.
  Worst age *immediately before the next actual publication* is
  `next.published_at_ms - previous.original_at_ms`; merely passing
  age at the instant of publication is insufficient.
- A genuine original IB quote HOLD remains a HOLD even if the cadence
  timing succeeds. Snapshots are locally hashed, not IB digitally signed.
- Single transient user service, 50% CPU, 384 MiB memory cap,
  AF_UNIX-only networking, NoNewPrivileges, no restart, finite runtime.
  No production timer installed and no unattended code/monitor cutover.

Original future plan on pocket4:

`~/.local/state/heptatrader/operator-evidence/fx-health-fullcycle-original-20261013-v1/plan.json`

Plan SHA256 (sealed before all 24 observation starts):

`91495e9f5aa9007df67982adbf0be6bb9026873c81c83242415f663c8e09f7cd`

The full-cycle archive source builds from the opt-in 15-second sandbox
cadence code, but archives **each** original assessment rather than
only status at the end. Original production source remains 60s
post-assessment and has not been altered.

## Independent complete-byte verification

A separate Python-standard-library analyzer,
`research/train_v2/fx_original_health_fullcycle_audit.py`,
does not import the source producer, model, IB trader or original market
decoder. It verifies owner-only evidence, exact first plan and worker
identity, all 24 source status bytes and 24 original probes each, the
recomputed native `source_prefix_digest`, original source age/time,
snapshot SHA, receipt chain, terminal manifest and missing/censored counts.

It reports independently:
- Exact full-byte source snapshots counted vs precommitted 24.
- Original READY vs original HOLD (never a new execution reward).
- Maximum age at publication, and maximum age before next publication.
- Whether the previous unchanged 30s timing gate is met on this
  short research cohort.
- Hard limits: `prod_monitor_cadence_switch_approved=false`,
  `trade_authority=false`, `model_promotion_allowed=false`,
  `source_signed_by_ib=false`.

A physical second-host readback, if performed, only replays the same
captured pocket4 original bytes. That is a useful byte-integrity check,
NOT a second independently sampled IB market feed or a hardware clock
signature.

Adversarial tests cover altered source IDs, duplicate JSON keys,
nonfinite data, altered rule/period, 24-probe count spoofing,
source-prefix mutation, false READY quote older than 5s, delayed
publication, unavailable cycles, 0600 evidence permissions, fail-closed
missing receipts, chain changes and report overwrites.

## Interpretation / original product gates

Passing archive completeness means the original source diagnostic can
actually be independently audited over all 24 cycles. Passing 30s
cadence timing would support **further** controlled testing under
load, restart, cold-start and CPU contention, not unattended
replacement of the deployed production read-only monitor.

Actual original market quote gaps must be evaluated separately;
financially executable bid/ask quotes, 2-sided commissions, realized
slippage, financing, cash NAV/Flex/Activity and statistically credible
independent forward model efficacy remain unavailable.

Native Train V2 continues to cap FeatureSpec horizons at 60 minutes.
Canonical Root/Owner/LedgerWriter/Witness/behavior propensities and
automatic trading/model promotion all remain HOLD.

**The previous price-only 4x4 cohort is never replayed or upgraded;
no trading service restarts, IB requests or orders occur in this work.**


## Actual new 24-cycle pocket4 result (verified, 2026-10-13)

The isolated full-byte worker completed naturally with systemd
`Result=success`, original 24 source evaluations,
**24/24 complete status JSON files and 24/24 SHA256-chained receipts**.
It consumed 1min56.381s CPU over 6min52.837s wall time and reached
130.5MiB peak memory under a research-only 50%-CPU / 384MiB limit.
No broker order, product owner action or production service restart.

A second stdlib-only audit checked **all 24 exact original 0600 JSON bytes**,
recomputed the 24-probe and source-prefix metrics per cycle, exact source
period/rule and all 24 independent receipts, and compared the fixed
15-second scheduling with the originals' true `at_ms` timestamps:

| Measured and independently replayed property | Result |
| --- | ---: |
| Complete future snapshots and receipts | 24 / 24 |
| Original IB PAPER source READY | 24 / 24 |
| Genuine source HOLD in this small cohort | 0 / 24 |
| Maximum status age at *publication* | 10,951 ms |
| Maximum status age *before the next publication* | **25,952 ms** |
| Declared local source age budget | 30,000 ms |
| Independent price/commission/slippage execution samples | 0 |
| Broker-source digital signature / native Owner admitted | NO |

Pocket4 private frozen plan SHA256:
`91495e9f5aa9007df67982adbf0be6bb9026873c81c83242415f663c8e09f7cd`

Pocket4 natural-exit immutable final SHA256:
`38342d80fd64b33619df394fcfe662cb228bda13154b29179fa01177099777b1`

Pocket4 independent stdlib research audit:
`~/.local/state/heptatrader/operator-evidence/fx-health-fullcycle-independent-20261013/original-fullcycle-24-source-byte-audit-v1.json`

Audit SHA256:
`6c56b7193e6fad46c70dcb388912636bc324a85afb11c8e200a6adf774cea9af`

A physical qian-qi readback received only the exact 52 private JSON
files, not the upstream IB network feed or account secrets, and
independently recomputed the exact same READY/HOLD count, 24 raw
snapshots, receipt-chain head and **25,952ms** worst-between-publish
age. Its output SHA256 differs because its `verified_at_utc` is a later
time; same metrics and source SHA roots match.

qian-qi report:
`~/.local/state/heptatrader-fx-original-health-fullcycle-20261013-original-copy/crosshost-verified-v1.json`
SHA256:
`e794cf57460d4901de6a45f14ab0bea18f68fb7e7d145cd7c7f92e5eeae37d8a`

**Audit verdict:** `PASS_FULLBYTE_ORIGINAL_SOURCE_CUSTODY_TIMING_ONLY`.
This is a short research window on one quote feed, not a cold-start,
clock-drift, CPU-throttle, day-boundary, broker disconnect/reconnect,
physical independent IB quote stream or sustained production SLA proof.
The candidate research producer is *not* installed as the default
monitor. Existing FX/BTC PAPER strategy and past 4x4 failures remain
unchanged, production cutover/HFT claims and all model/trading
promotion gates remain HOLD.
