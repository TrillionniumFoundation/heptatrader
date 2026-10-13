# EUR/USD original PAPER 4-window source-health as-of matrix

Date: **2026-10-13**. Mode: **read-only retrospective integrity audit**.
Status: **research source proof PASS / original four decisions HOLD / no model
or trade qualification**.

## Distinct issues: monitoring snapshot age ≠ original IB quote age

The *original* experimental study precommitted a 30-second maximum age
for its separate 24-probe source monitor JSON at an actual fixed 5-minute
grid decision. It also separately required a broker-reported 5-second
TTL for the original input quote, a valid 13-quote causal input, and
a 4.5-second decision validity window. These are different time axes;
one cannot substitute for the other.

**All four** originally frozen decisions 00:20, 03:35, 06:50, 10:05
UTC+08:00 exited `CENSORED/ORIGINAL_READINESS_HOLD_OR_STALE`.
The already observed 15/30/60/120min outcome attempts for rejected
decisions are not broker trading rewards, synthetic zero returns or
reusable new samples. Original experiment result files and read-only
workers were not edited by this audit.

The first (00:20) original source-monitor JSON was never preserved
contemporaneously. **Do not reconstruct it.** The second, independent
source-health custody stream had separately preregistered the
03:35/06:50/10:05 windows at 00:52, pinned against exact original
experiment plan SHA and seal. It saved each raw 24-probe health JSON
with an immutable per-window attempt receipt.

This audit independently reconciles these three *already saved* raw
status files with their exact original entry CENSORED receipts:

| Window (UTC+8) | Original 24 probes | Source-monitor age at entry | Last original quote age **at monitor as-of** | Result |
|---|---:|---:|---:|---|
| 03:35 | 24/24 | 37,938 ms | 435 ms | Original entry still CENSORED |
| 06:50 | 24/24 | 86,233 ms | 476 ms | Original entry still CENSORED |
| 10:05 | 24/24 | 85,031 ms | 1,065 ms | Original entry still CENSORED |

All three original monitor states were actually
`READY_FOR_REVIEW_NOT_AUTHORIZATION` *at their own earlier as-of
timestamp*, and each monitor's original quote age was below the
frozen 5-second source limit **at that old timestamp**. All three were
**too old at the experiment's fixed decision time**. This is enough to
identify the source-monitor **report freshness / publication cadence**
as the immediate input gate failure, **not** proof that a fresh IB quote
would have been available for a successful decision. The quote at the
actual original grid remains unproven, especially during sparse FX
market events.

The collector's earlier implementation used a 60-second sleep **after**
each 24-probe calculation. That scheduling design is not compatible
with reliable <=30-second snapshot freshness, even if 24/24 previous
probes are usable. A sandbox 24-cycle staged verified cache later
improved worst stale age to 22,802ms but kept three genuine
`LATEST_AUTHORITATIVE_QUOTE_NOT_FRESH` HOLD cycles. These later
measurements did not create a new original at-decision source proof.

## Exact code and on-pocket4 custody evidence

Independent auditor:
`research/train_v2/fx_forward_health_asof_matrix.py`

Original plan SHA:
`02aa4a2662b743203cc925d09d3250bcc0e02155cf5d00bb319b0526448aaa04`

Original seal SHA:
`1f13ed5c7a8fcdc29cc0881bf5c7854c263876f5f89bb6bc87633d5b2b33860b`

Independent **preobserved** health custody plan SHA:
`9bdb268359c2befeecb66ff11cddf45161f72543678875ed86428cb3a6420737`

The auditor:
- Opens 0600 owner-private original JSON bytes using `O_NOFOLLOW`;
  checks stable dev/inode/size/mtime while reading; rehashes all raw
  snapshots and all original decision receipts.
- Checks exact plan, seal, four frozen clocks, unique window identities,
  and the exact original non-retryable `CENSORED` decisions.
- Independently recomputes each source status' 24 individual probe
  counts, source-prefix digest, 30-second health as-of age, 5-second
  quote age as of the producer, monitor schema, frozen readiness
  algorithm and automatic-registration/trading flags.
- Distinguishes monitor-age failures from upstream real quote freshness;
  no trading approval or migration of original failures is possible.
- Uses immutable write-once result (`O_EXCL`, 0600 under 0700) and
  rejects changed timestamps, source bytes, probe counts, identities,
  original decision status, or production permission.
- Is unable to convert local SHA timestamps into cryptographic
  IB-signed custody, independently authorized native Owner/Witness, or
  actual broker execution/funding/PnL.

Private receipt:
`~/.local/state/heptatrader/operator-evidence/fx-future-health-asof-20261013/reconciliation-v2.json`

SHA256: `98e941d147e9da81103c8fc36ba1d50971b9500ead57155317b2247878ea0154`.

Release: **HOLD**. Do not rerun the original 00:20/03:35/06:50/10:05
entry cells. The final 10:05 +120-minute price-only attempt has its own
scheduled due; the original research worker must naturally close it,
and a separate original four-window final auditor should independently
validate all sixteen attempts when physically available.

## Next engineering qualification

1. Preserve the presently deployed source monitor and its original
   snapshots until the frozen study naturally closes. Do not edit
   its fixed TTL or old 30-second evidence contract.
2. New, separate source-health monitor candidate should prove a
   **sustained** full-source 24-probe freshness SLA under actual CPU,
   I/O contention, cold restart, source archive rollover and no
   broker quote events, without weakening `HOLD` when the latest
   original IB quote exceeds 5s validity.
3. Only after an independently verified read-only monitor cutover
   is authorized, design and precommit a new never-seen prospective
   source cohort; no existing historical or first four-window prices
   may be relabeled as new OOS predictor evidence.
4. Keep product native Train V2 >60-minute horizon, signed Owner,
   CandidateSet/LedgerWriter/Witness, full original behavioral
   propensity, actual after-cost broker PnL and complete IB Flex/NAV
   as independent hard blockers.

There was **no new broker request, execution, order, cancellation,
model fitting, automatic trading or model promotion** in this audit.
