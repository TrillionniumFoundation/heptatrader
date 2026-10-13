# Original FX PAPER 4×4 frozen future: independent physical-host closeout

**2026-10-13, pocket4 local time UTC+08:00**. Research integrity only;
no order, broker query, model training, owner enrollment, IB market-data
signature, independent exchange clock or model-profit qualification.

## Frozen study (do not edit)

The original PAPER EUR.USD original quote-only plan was registered before
2026-10-13 **00:20 / 03:35 / 06:50 / 10:05** (+08:00). Four fixed
horizons per anchor: 15 / 30 / 60 / 120 minutes, 16 possible maturation
attempts in total. The action is always `NO_TRADE`; a 120-minute
price-only observation is **not** a native Train V2 120-minute FeatureSpec
admission.

Physical qian-qi preserved the original plan and seal before the first
window. The hard-pinned SHA-256 roots are:

- Plan `02aa4a2662b743203cc925d09d3250bcc0e02155cf5d00bb319b0526448aaa04`
- Seal `1f13ed5c7a8fcdc29cc0881bf5c7854c263876f5f89bb6bc87633d5b2b33860b`.

Every original decision so far was **CENSORED** by the original source
monitor's 30-second freshness requirement; the 00:20 full monitor
JSON was never retained and is **not** recreated. The 03:35, 06:50
and 10:05 original-monitor health JSON bytes were retained locally
and explicitly reported as stale even when their 24 probes were usable.

At 10:05 a separately predeclared sandbox SHA-verified cache comparison
proved **original monitor age 86.092s** vs **sandbox cache age 18.000s**
at the same frozen +2s observation. The original PAPER decision was
and remains CENSORED. Cached READY is *not* acceptable original source
or proof of trading permission.

## Independent post-event validation

New independent, standard-library-only validator:
`research/train_v2/fx_all_four_final_crosshost.py`. It reuses the
**already separately validated and source-SHA-pinned**
`fx_first_three_original_crosshost.py` and
`fx_1005_cache_crosshost.py` contracts. It runs from qian-qi on
private byte-exact retained copies, not the live IB Gateway.

The validator asserts:

1. Pre-event physical qian-qi original plan/seal and immutable calendar
   exactly match the original recorded SHA-256, period and fixed rights.
2. **First three original** decisions and 12/12 original mature
   attempts are always `CENSORED`, with exact immutable age/quote source
   tests, original timing and authentic, unmodified local health bytes.
3. The 10:05 original entry attempt is *byte-for-byte identical*
   (SHA-256) to the original decision already independently checked
   by the physical-host 10:05 cache-vs-baseline verifier. It must stay
   `CENSORED`, no quote preaction or broker order.
4. Each fourth-window maturity receipt must occur inside its original
   due+6.5s to due+15s frozen acceptance interval, remain
   `CENSORED / NO_ELIGIBLE_ORIGINAL_PREACTION`, contain no trade
   authority, no model promotion, no backfill and no positive price
   label. Synthetic `maturity-XYZ.json` or `preaction.json` is a hard
   rejection.
5. A missing **future** receipt may be listed only as `PARTIAL` if its
   frozen due+15s deadline is still in the future. Once past, a missing
   receipt is a hard **HOLD/FAIL**, not an optimistic completed zero.
   `--require-complete` refuses anything less than four decisions and
   all sixteen original maturity receipts.

Original research data, native model, cost receipt, source readiness
producer and currently running PAPER execution are never mutated.
This audit independently verifies the bytes copied *after* the relevant
outcome; it cannot prove original broker signing, hardware timing,
cross-host market-data acquisition, PnL or model predictive alpha.

## Actual qian-qi physical proof while final horizons are future

The original sources were copied from pocket4 to a **new** qian-qi
private 0700 research directory, retaining 0600 original files.
No retained 00:20 health JSON was fabricated. The physical validator
successfully reproduced the first-three archive and the exact
10:05 precommitted cache comparison, then validated the fourth entry,
the 15-minute and 30-minute censored maturity receipts.

Result at **2026-10-13 10:46:11 +08:00**:

- `PARTIAL_AWAIT_ORIGINAL_FUTURE_MATURITIES`;
- **4/4** frozen decisions verified as CENSORED;
- **14/16** original maturity attempts verified as CENSORED;
- last fourth-window 60 and 120-minute outcomes explicitly **PENDING**;
- eligible price labels, broker queries/orders, actual execution rewards
  and authorized model promotions all **zero**.
- Source qian-qi original 10:05 monitor age **85,031ms** at the original
  preaction, unchanged from previously audited paper receipt.

Private exact physical-host partial report:

`/home/qian-qi/.local/state/heptatrader-fx-fourwindow-closeout-20261013/partial/crosshost-partial-v1.json`

SHA256:
`a477286571face76ee15e18dacf2ae055752d19687e178255120f2fc8a132dde`.

New adversarial tests: **16/16**, including forged completion,
missing after deadline, counterfeited price label, retimed maturity,
altered product/trading scope, manipulated cache acceptance, changed
original decision SHA and fabricated preaction. Existing standalone
physical verifier tests remain authoritative and separately retained.

## Final natural-exit gate

The remaining **10:05 +60min** and **+120min** receipts are due at
**11:05:06.5 and 12:05:06.5** (+08:00). The original finite worker
must naturally exit; no forced restart, old-window replay or modified
readiness TTL is permitted.

After both receipts are available, prepare a NEW qian-qi original-byte
archive without overwriting the existing partial one. Run:

```sh
# On qian-qi; copies must first be made from the existing, complete
# pocket4 original window-03 directory into a NEW private 0700 folder.
PYTHONPATH=/home/qian-qi/.local/state/heptatrader-fx-final4-code-20261013 \
python3 -B -m train_v2.fx_all_four_final_crosshost \
  --firstthree ~/.local/state/heptatrader-fx-firstthree-crosshost-20261013 \
  --cache1005 ~/.local/state/heptatrader-fx-1005-physical-review-20261013 \
  --fourth ~/.local/state/heptatrader-fx-fourwindow-closeout-20261013/final/window-03 \
  --require-complete \
  --output ~/.local/state/heptatrader-fx-fourwindow-closeout-20261013/final/independent-final-v1.json
```

Final successful **research-integrity** result is a complete audited
`4×4 CENSORED` cohort, not successful economic price predictions.
Only after this may the product source-health monitor candidate be
considered for a separate controlled cutover with independent rollback
proof. Even then, model/Owner/Witness/LedgerWriter, IB Flex, real
execution cost and account NAV remain separate hard HOLD gates.
