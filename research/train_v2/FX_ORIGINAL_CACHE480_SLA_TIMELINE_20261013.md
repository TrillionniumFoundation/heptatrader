# 480-cycle original PAPER source-health timeline — 2026-10-13

Status: **HOLD / research-only / NO_TRADE**.

## Why this audit exists

The original four-window FX price experiment naturally ended with
0/4 qualified preactions and 0/16 qualified price maturities. The source
health custody checks for later windows were stale under the frozen
30-second rule. The 480-cycle original-source verified-cache study
is separate: it was precommitted at 15-second cadence, with the
original 25% CPU quota, without production monitor cutover.

This new read-only independent auditor checks the *real* original source
status and receipt timeline without altering or replacing any cycle.
It does not query IB, modify any trade, perform training, or call a broker.

## Exact original verification

Source: `research/train_v2/fx_cache480_sla_timeline.py`.

- Pin the original 480-cycle plan SHA-256:
  `8a77da0fcdd3d251a19d53209fa7a836386848380f92e3681590b33ce2bf98d5`.
- Require the original EUR.USD PAPER period, 24 probes, 20 usable minimum,
  at most four missing, 5-second quote TTL and 30-second path threshold.
- Verify owner-private O_NOFOLLOW source files, every original receipt
  SHA chain, exact status hash, 24-probe counts, original quote age,
  source prefix digest, as-of and durable publication timestamps.
- Keep real READY, real HOLD, missing/failed capture and failed source
  qualification disjoint; fail on false READY or a missing interior cycle.
- Compute both own-publication age and *prior original as-of age before the
  next actual publication*. The second metric catches periods where a
  status remained stale even though its own publication had been timely.
- Include every HOLD run and its frozen cycle clock, no rewriting, no
  retrospective relabeling and no claims about production eligibility.
- Refuse premature 480/480 success: an active prefix is NOT final.

This is an independent status/receipt auditor, NOT a second full reparse
of every original raw IB quote source. Separate immutable original-quote
SHA-chain verification remains necessary. Broker quotes are not signed
by IB in this local custody.

## Actual observed prefix

On pocket4 the new **371/480** original precommitted cycles had:
- Genuine READY: **185**.
- Genuine original HOLD: **185**.
- Missing/failed capture: **1**.
- Maximum status age at its own publication: **19,152ms**.
- Maximum prior as-of age at the next publication: **31,654ms**.
- Source freshness SLA violations >30 seconds: **1**, across
  original cycles 0 to 2 while cycle 1 was missing.
- Longest genuine continuous HOLD: **88 cycles, 22 minutes**
  (index 180–267); another source HOLD ran 86 cycles
  (index 2–87, 21.5 minutes).

The original source-gap forensic experiment separately recorded a
123,095ms quote event gap, 61 original failure records and two
IB 10197 errors nearby. This is a correlation; because the quote
gap began before the errors, 10197 alone is not an established root cause.

Private write-once result on pocket4:
`~/.local/state/heptatrader/operator-evidence/fx-cache480-sla-timeline-20261013/prefix-v2.json`.

SHA-256:
`368c6c4105e148f056015a87ae0a1f94795e4e575a36c757908d28b43a4c44a5`.

## Regression and next hard gates

The 18 adversarial tests cover forged READY, tampered SHA chain,
incorrect period, false probe denominator, private-file permissions,
JSON duplicates/nonfinite values, missing cycle, premature terminal,
unmodified original HOLD runs, and the inter-publication SLA.

Next: finish all 480 original cycles, separately reparse exact original
quote bytes and recover the full native source gap/epoch timeline.
Any transport correction must first pass isolated mock fault tests,
then fresh future source continuity, real CPU throttling, cold-start,
failure rollback and true 30-second status age. Do not loosen the
original source eligibility thresholds to rehabilitate old failures.

**Release remains HOLD.** No automatic trading, model fit/promotion,
native Owner/Witness/LedgerWriter, signed broker fill prices, or complete
Account/Flex/NAV qualification follows from this report.
