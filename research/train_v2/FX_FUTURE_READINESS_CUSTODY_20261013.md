# Pocket4 FX future study: independently archived original source-health bytes

**2026-10-13, pocket4 local UTC+8.** This is a new **research-only
same-host corroboration**, stacked on the exact previously sealed
`heptatrader.fx-original-future-price-cost-calendar.v1` study. It
is not a source-authoritative IB quote, a production independent witness,
a canonical Owner root, a signed broker execution, a model-fit license,
or trading authorization.

## What the original experiment proved and did not prove

The future quote-only study at 00:20 / 03:35 / 06:50 / 10:05 local time
uses no model and permanent NO_TRADE. Its first 00:20 attempt was
correctly frozen with `CENSORED` reason
`ORIGINAL_READINESS_HOLD_OR_STALE`, and its first maturity was
`CENSORED / NO_ELIGIBLE_ORIGINAL_PREACTION`. An existing qian-qi
physical-host readback independently preserved exact hash copies of
those two attempts.

**Critical custody limitation:** the first original decision only
reported the reject code; its contemporaneous source-readiness JSON
bytes were **not durably retained**. Reconstructing a later health
snapshot can *never* prove the 00:20 state or relabel its outcome.

The new read-only custody stream therefore expressly excludes the
already-elapsed window 00. It only captures the three future 03:35,
06:50 and 10:05 status JSONs at the old study's original
`anchor +1.0s .. +4.5s` decision interval.

## Frozen source-health contract

The new code in
`research/train_v2/fx_original_readiness_custody.py`
pins the *previously* independently copied root:

- Original plan SHA256
  `02aa4a2662b743203cc925d09d3250bcc0e02155cf5d00bb319b0526448aaa04`.
- Original seal SHA256
  `1f13ed5c7a8fcdc29cc0881bf5c7854c263876f5f89bb6bc87633d5b2b33860b`.
- The same exact PAPER period and source readiness file path from
  those original, unmodified bytes.
- Exact `heptatrader.quote-readiness.v1` monitoring rule:
  24 original probes, 60s spacing, 5min horizon, 5s endpoint tolerance,
  30s path continuity, at least 20/24 usable and at most 4 missing,
  5s original quote TTL. We do **not** lower these limits.

On each future target, the physical monitor's original JSON bytes
are opened with `O_NOFOLLOW`, a private-file owner/mode/size check,
stable file identity and SHA-256. The module recomputes:

1. Exact PAPER period binding and monitor schema/version.
2. All 24 probe statuses vs stored disjoint counts, usable/unusable
   integers and 20/24 readiness threshold.
3. Local SHA256 of the original producer's `source_prefixes`
   metadata vs the reported source-prefix digest.
4. Monitor timestamp no older than 30s at the fixed capture time,
   the original quote event timestamp/age and its 5s freshness bound.
5. `automatic_trial_registration=false`, `trading_authority=false`.

A source snapshot is archived **even if monitor says HOLD**, with
`HOLD` and its explicit discrepancies. The original health JSON
and exact attempt receipt are both private 0600 and created with
`O_EXCL` + `fsync`; a duplicate target is never retried or overwritten.
If the read/custody process misses its fixed 3.5s decision window,
it records only `MISSED` and never fetches a later healthier snapshot.

If all these controls pass, `CORROBORATING_SOURCE_READY_NO_TRADE`
means **only** that a second, distinct research process observed the
same local monitor at the same time; the old primary worker is still
solely responsible for its own decision. The new receipt **cannot**
change, override, rescue, authorize or count the primary window.
This same-machine hash custody is not a time-attested or IB-signed
physical second-source witness. It does not reconstruct the source
monitor at window 00.

## Operations and tests

The isolated code branch is
`audit/fx-original-readiness-custody-20261013`.

Private witness evidence directory:
`~/.local/state/heptatrader/operator-evidence/fx-future-readiness-custody-20261013-v1/`

A one-shot user service may run after a genuinely future plan
registration. It has *no* IB Gateway or broker TCP socket, no orders,
no production/strategy/funding authority, no model training or
automatic registration and no systemd restart. It naturally ends after
the 10:05 window and must not be treated as a background trading agent.

New adversarial regressions explicitly reject: foreign-paper-period
health, change to minimum-usable rule, altered original source prefixes,
probe-count manipulation, stale/future monitor timestamps, quote-age
mismatch, private-file symlinks/public permissions, duplicate JSON,
nonfinite numerics, retimed/late witness receipt, repeated output,
automatic trial registration, and any attempt to recover the already
censored window 00.

**No mechanism in this audit can create a model predictor, relax the
native Train V2 60-minute FeatureSpec horizon cap, or repair the
original experiment's unresolved Owner/LedgerWriter/Witness/Flex
and net-PnL gates.** Every old failed/abstained source observation
is preserved unchanged.
