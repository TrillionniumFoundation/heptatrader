# Independent original worker-final to 4×4 physical receipts binding

**2026-10-13** · Strict extra HOLD gate, no trade or model effect.

## Why this extra gate exists

The pinned `fx_all_four_final_crosshost.py` independently revalidates
all four frozen original EUR/USD PAPER decisions and sixteen distinct
original price/maturity attempt receipts. Its
`COMPLETE_4X4_CENSORED_RESEARCH_PROVEN_NO_TRADE` is **not by itself**
evidence that the original finite worker durably wrote its final summary
or that the OS unit exited naturally. An independent record of the
original terminal status must be reconciled rather than assumed.

A new **additive** strict validator, implemented in
`research/train_v2/fx_fourwindow_primary_terminal_reconcile.py`,
binds the original worker's actual 0600 `final.json` byte content
to the prior independent qian-qi archives. It never replays or modifies
the original experiment.

## Required roots / never override

The original frozen pretrial plan:
SHA256 `02aa4a2662b743203cc925d09d3250bcc0e02155cf5d00bb319b0526448aaa04`.

The original frozen seal:
SHA256 `1f13ed5c7a8fcdc29cc0881bf5c7854c263876f5f89bb6bc87633d5b2b33860b`.

The standalone additional check requires:

1. Existing physical cross-host `verify_4(require_complete=True)`
   itself pass, not just the partial 15/16 verifier. Missing a future
   receipt after the original 12:05:15 deadline is a hard failure.
2. Original worker `final.json` copied byte-for-byte from pocket4
   **after it exists**, to a NEW owner-private qian-qi research directory.
   It must have the exact original schema, sealed SHA256 and zero
   orders, rewards, promotion, model admission, source-data changes.
3. Exactly **four** original `CENSORED` entry receipt SHAs matched to
   the already archived independent first-three and 10:05 physical
   audits. Reordered, duplicated or invented entries fail.
4. Exactly **16** distinct 15/30/60/120-minute maturity receipt
   SHA256s matched to already archived original physical evidence.
   No duplicate, omitted, relabelled, prematurely timestamped or
   hindsight-filled maturity is eligible.
5. Original worker's `completed_at_ms` must be no earlier than
   the last real 10:05+120m original due+6.5s. An unobserved or forged
   natural `systemd` exit **cannot** be inferred merely from the
   presence of a final JSON file. The separate unit exit/journal must
   still be checked by the operator.

The independently captured original 03:35, 06:50 and 10:05
health snapshots were all HOLD due to **source monitor age >30s**;
the original 00:20 health JSON is irretrievably unproven. The old
four-window 15/16 qian-qi partial proof was:
`0b68be9371bb9f412e2f6e2d1b6bd12ca058ce9c8ac75e0e7715a94b25f0c8b8`.
This result is not modified or promoted.

## Exact final command only AFTER original future due

Copy the original three required `10:05 window-03` receipts plus
the **new 120-minute fourth receipt**, and the ORIGINAL finite worker's
`final.json` to a NEW qian-qi private 0700 directory with 0600 files.
Use authenticated existing `pocket4` SSH host identity, not TOFU.

From the qian-qi offline independent verifier bundle:

```sh
export PYTHONPATH=$HOME/.local/state/heptatrader-fx-final4-code-20261013
python3 -B -m train_v2.fx_all_four_final_crosshost \
  --firstthree ~/.local/state/heptatrader-fx-firstthree-crosshost-20261013 \
  --cache1005 ~/.local/state/heptatrader-fx-1005-physical-review-20261013 \
  --fourth ~/.local/state/heptatrader-fx-fourwindow-closeout-20261013/final/window-03 \
  --require-complete \
  --output ~/.local/state/heptatrader-fx-fourwindow-closeout-20261013/final/independent-final-v1.json

# Use the separately installed new audited reconciling module:
python3 -B -m train_v2.fx_fourwindow_primary_terminal_reconcile \
  --firstthree ~/.local/state/heptatrader-fx-firstthree-crosshost-20261013 \
  --cache1005 ~/.local/state/heptatrader-fx-1005-physical-review-20261013 \
  --fourth ~/.local/state/heptatrader-fx-fourwindow-closeout-20261013/final/window-03 \
  --primary-final ~/.local/state/heptatrader-fx-fourwindow-closeout-20261013/final/primary-final.json \
  --output ~/.local/state/heptatrader-fx-fourwindow-closeout-20261013/final/primary-to-physical-v1.json
```

Every `--output` must be a NEW path. Do not run these commands before
the actual final price-clock deadline, claim a final PASS from a
partial report, or overwrite previous physical-host source archives.
Both verifiers use Python standard library and have **no broker API**
or production training/strategy authority.

Thirteen new negative/content consistency tests prove the hold
conditions, exact SHA binding, missing 16th receipt, fake trading
reward, fake terminal summary, incorrect maturity timestamps,
mismatched decisions or duplicate original receipt identities.

**Research release: HOLD.** Even a complete CENSORED 16/16 closure
is evidence of correct fail-closed sampling, not evidence of profitable
trading or an eligible new Train V2 model. No automatic Owner,
LedgerWriter, Witness, IB Flex/NAV or execution-cost gate is bypassed.
