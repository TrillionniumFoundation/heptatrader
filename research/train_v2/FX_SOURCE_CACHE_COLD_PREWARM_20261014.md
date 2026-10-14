# Original IB PAPER source-health: preregistered cold-cache prewarm probe

**2026-10-14. Isolated research, NOT production monitor cutover, NO_TRADE.**

## Previously observed incident, exact scope

The separate 480-cycle original PAPER source experiment at the existing
25% CPU budget completed with **293 genuine READY**, **187 non-READY**,
and **479/480** actual full-source status snapshots. Cycle 1 missed its
15-second fixed starting time after cold cycle 0 took around **19.15
seconds**. The original as-of source timestamp was **31,654 ms** old
before the next actual publication. This is a genuine >30-second SLA
breach, not a reason to raise the threshold, erase a failure, or
claim that all original IB source HOLD periods are recoverable.

The first cycle belongs to the immutable original campaign and is never
replayed or changed. The source also experienced true IB transport
errors, source gaps and partial outages; cache warmup does not repair
an IB subscription or missing original quote.

Original preserved campaign:
`~/.local/state/heptatrader/operator-evidence/fx-cache480-25pct-20261013-v1/`
SHA-256 final:
`8ac37d9c202505e00bcc90daf7ede67dabdc7167eafef526ee17457cc1745353`.

The read-only original 480 independent final receipt also exists
under `fx-cache480-independent-prefix-20261013/full480-v1.json`,
recording exact 480/480 cycle chain, **one failed observation** and the
real 31,654ms cross-publication stale interval. No successful model
training or broker execution authorization is inferred.

## First 12-slot implementation failure retained (v1)

Original first prewarm experiment at pocket4:
\`operator-evidence/fx-source-cache-prewarm-20261014-v1/\`.
It successfully completed an original read-only cache prewarm *before* all
fixed slots, but **all 12 health verdicts failed with
\`ASSESSMENT_KeyError\`**. The production PAPER period had been written as
\`original_period_sha256\` in the frozen new plan while the existing
read-only health validator expects \`period_sha256\`. The assessment
itself saved 12 original 0600 source-health JSONs, but the wrapper
erroneously passed the new plan directly into the period checker.
This is a real **candidate implementation bug**, not evidence of a
broker quote failure, and **0/12 v1 are qualified**. The original plan,
status/attempt SHA chains and full v1 final receipt must remain preserved.
The fix explicitly verifies and maps the frozen original PAPER period into
the established monitor checker; a missing or forged period is rejected.
This change is regression-tested before a **distinct, future-registered v2**
pilot. No earlier v1 slot may be replayed, relabeled or overwritten.

## New design (predeclared, separate real observation)

Code: `research/train_v2/fx_source_cache_prewarm_probe.py`.

New future **12 x 15-second fixed grid** / 25% CPU / 384MiB RAM
one-shot study. At pre-registration time all 12 cycle timestamps
are frozen and the exact repository code SHA, original installed source
monitor SHA, selected original verified-sealed cache SHA, immutable
PAPER period and read-only config are pinned.

The worker performs one **full original SHA256 archive validation and
strict decode** BEFORE the first cycle's deadline. That prewarm has
its own owner-private write-once original source JSON and receipt,
never becomes a fake experimental cycle or a prospective model label.
The warmed cache is then used for the 12 immutable status assessments.
Its original archive inventory and every sealed source page SHA256
continue to be re-verified at **every** assessment; unsealed source
segments are always strictly reparsed. A source timeout, original HOLD,
late cache warmup, late fixed observation, stale >30-second status or
source/probe tampering becomes HOLD or CENSORED, without retry.

Each status and receipt is stored as `0600` with fsync under a
`0700` private research directory; receipts are SHA256-chain-linked.
The report separately computes:
- Original as-of source age at its own durable publication.
- Age of prior original as-of just **before the next actual publication**.
- Genuine original READY vs genuine original HOLD vs missed assessment.
- Verified-cache counters, preserved old original failure, and zero
  broker queries/orders.

This is NOT a replacement or downgrade for the live
`heptatrader-learning-source-health.service`. The exact original
20/24/4-missing/5s quote TTL/30s path rules do not change, nor do
the live FX/BTC PAPER driver, IB Gateway, Exec, strategy, account NAV,
canonical Owner, Witness or LedgerWriter.

The 12-slot pilot is **not equivalent to 480-slot sustained stress**.
If this pilot passes, the separate later full duration, cold startup,
CPU-contention/recovery, physical-host original source checks and
authorized reversible read-only canary would be further hard gates
before even contemplating a production health-monitor cutover.

## Scope safeguards

The opt-in prewarm pilot runs a *single* transient user service with
`NoNewPrivileges=yes`, `RestrictAddressFamilies=AF_UNIX`,
`CPUQuota=25%`, `MemoryMax=384M`, `Restart=no`, fixed runtime,
original source read-only sandbox, no broker client, no persistent
timer, and no trading authority.

Unit tests verify:
- Exact new 12-slot 15-second prospective grid.
- Correct old source/code/cache SHA and quote-readiness rule pins.
- A cold warmup is not allowed to create a fake earlier observation.
- Source HOLD and false READY remain rejected.
- Wrong PAPER period, quote age, modified original-prefix digest
  and stale >30-second status are fail-closed.
- No duplicate/overwritten original price or status receipt.
- Any production cutover or trade/model promotion is prohibited.

**Product release remains HOLD** regardless of the pilot outcome:
IB PAPER accounting lacks Flex/Activity and all-in NAV, original
trader behavior propensity and canonical Product Root/Owner/Witness
are incomplete, broker-signed price and actual fill-time economic
performance are not proved, and 15-minute Ridge financial edge failed.
