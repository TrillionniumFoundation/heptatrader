# Oct 13 11:23 (+08:00) interim: all four FX source preactions censored, 15/16 maturities independently checked

This is an **append-only research audit update** to
`FX_ALL_FOUR_FINAL_CROSSHOST_20261013.md`. It does not replace an
original pre-registered execution, retrospectively change a timestamp,
substitute a quote, or assert a model/production PASS.

## Cross-host physical receipt

On qian-qi, a new private `0700` directory
`~/.local/state/heptatrader-fx-fourwindow-closeout-20261013/partial-1105/`
contains *byte-exact* strict-host-identity SSH copies of Pocket4 original
10:05 `decision-attempt.json` and 15/30/60-minute maturity receipts.
All four are 0600. Original Pocket4 input files remain unmodified.

Reusing the previously SHA-pinned standard-library-independent
`fx_first_three_original_crosshost.py`,
`fx_1005_cache_crosshost.py`, and
`fx_all_four_final_crosshost.py`, the actual independent tool output was:

```
result: PARTIAL_AWAIT_ORIGINAL_FUTURE_MATURITIES
all_original_decision_windows: 4
original_decision_censored_count: 4
original_complete_matured_censored_count: 15
original_expected_maturity_count: 16
fourth_future_horizons_pending: [120]
price_labels_proven: 0
all_original_failures_preserved: true
trade_authority: false
```

The **new** write-once partial receipt:
`~/.local/state/heptatrader-fx-fourwindow-closeout-20261013/partial-1105/independent-partial-v2.json`

SHA-256:
`0b68be9371bb9f412e2f6e2d1b6bd12ca058ce9c8ac75e0e7715a94b25f0c8b8`.

The old 14/16 partial and all other failed/negative records remain
retained. Final full 16/16 acceptance MUST NOT be claimed until the
original last window's **12:05:06.5** (+08:00) 120-minute deadline,
the original service's natural exit and exact last original source
receipt have been observed and independently rechecked. A missing
receipt after deadline is an explicit FAILURE, not a zero return.

## Actual source-health blocker

All four original study decisions were `CENSORED /
ORIGINAL_READINESS_HOLD_OR_STALE`. The independent local source-health
custody captured the exact 03:35, 06:50 and 10:05 original JSONs and
preserved `READINESS_SNAPSHOT_STALE_OR_FUTURE` for each; the
00:20 original full status bytes are unavailable, and MUST NOT
be recreated from later monitor readings.

Even with all 24 past probes marked USABLE, the three original local
monitor ages at preaction were approximately **37.9s, 86.2s and
85.0s** (respectively), above the frozen 30s requirement.
The 10:05 pinned sandbox cache had a different 18s source age,
but that shadow counterfactual was **not an original input**.

An independently staged 24-cycle cache experiment also remains
**HOLD_STAGE_SOURCE_READINESS_FAILURES**:
3/24 cycles failed despite 24/24 *past* probes being usable. Separate
read-only post-event diagnosis found their latest original market event
was **5.087s, 9.892s and 24.892s** old, respectively, against the
unchanged 5s authoritative quote TTL. These are not production
monitor arithmetic discrepancies; late/absent genuinely fresh quotes
must NOT be retimestamped or credited as READY.

The staged cache reduced measured worst pre-next-publication age to
22.802s and warm evaluation to 4.281s, but a quick cache cannot
fabricate valid source events. `production_cutover_approved=false`.

## Training and financial state

As of 11:14 +08:00, existing PAPER Execution/FX/BTC/PnL/NAV/Laya
services were active. PAPER strategy after-fee observed PnL was
**-$1,520.3533456525** with $903.893880 in already-accounted fees;
`account_complete=false`. These values are from the existing PAPER
PnL snapshot, **not** from this zero-trade research experiment.
A more recent Laya trainer log reported 31 completed and 1 failed,
automatic trading false and canonical Owner still disconnected.

Release verdict: **HOLD**. No automatic retraining, live economic
candidate, original owner/witness/ledger authority, after-cost broker
alpha, Flex/Activity/NAV closure, or product source-health cutover.
