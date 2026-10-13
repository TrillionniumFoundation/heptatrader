# Four-window original PAPER primary final: strict readback path fix (2026-10-13)

**Scope:** One-line verifier API fix and focused actual-file integration tests.
No new trading, model training, original market data, broker request or promotion.

## Failing gate diagnosed from actual physical qian-qi readback

At the original 10:05 +120-minute due time, the pocket4
`heptatrader-fx-original-future-20261012.service` naturally exited
with `Result=success` and `ExecMainStatus=0`.
Its final original 0600 file SHA256 is:

`857914d7222cc6e85058d9a19c3bf8765690e172a91a4de9cd2d22ebecc3890a`.

The independently captured original 4/4 decisions + 16/16 maturity
attempts all remained `CENSORED`. A qian-qi physical verifier
`fx_all_four_final_crosshost.py --require-complete` passed with
`COMPLETE_4X4_CENSORED_RESEARCH_PROVEN_NO_TRADE`.
Its new result SHA256:

`d910c92afc4347d32971cc33b301f6e6a4e1276549d4694c124a1d9d54cb844d`.

**Real bug:** the extra primary-final-to-physical verifier called

```python
final, fdigest = evidence(p.parent, p.name)
```

but `fx_first_three_original_crosshost.evidence` has signature
`evidence(path, size=65536)`. The directory was passed as the file path
and the filename string as its byte-size cap. It therefore rejected a
genuine, owner-private, single-link 0600 `primary-final.json` with
`UNSAFE_OR_OVERSIZE_SOURCE`.

The fix is **only** `evidence(p)`. Its original private-file
`O_NOFOLLOW`, regular-file, owner, nlink, 0600, stable file-read,
size bound, source SHA, exact 16-maturity and no-trade checks remain
unchanged.

## Verified on two independent research processes

New `test_fx_primary_final_path_integration.py` covers actual
0600 file-byte loading, correct content SHA, refusal of public
0644 receipt and refusal of symlink lookalike. A test against the
original broken call fails, proving regression relevance.

Pocket4 source regressions:
- 3/3 new actual-file path tests passed.
- 13/13 existing strict primary-manifest adversarial tests passed.
- 17/17 previous complete-four-window physical adversarial tests passed.
- Total **33/33** focused tests.

The **corrected source bytes** were copied to a separately named
`train_v2.fx_fourwindow_primary_terminal_reconcile_fixed` in the
existing qian-qi *research-only* verifier bundle. The original
failing module and original archived trial bytes were left unchanged.

Rerunning the full, strict binding of original worker `final.json`
to all original 4 entry and 16 maturity SHA receipts passed:

`ORIGINAL_4X4_RESEARCH_PRIMARY_FINAL_AND_CROSSHOST_RECONCILED`

Private corrected qian-qi result SHA256:
`bb553ae5c553c9896d7bc5b03241cf9d99888986b496b56fada73b7d1bbb25ee`.

This still does **not** prove IB-signed quote custody, source freshness
at failed entry time, a nonzero executed trading reward, profitable
model efficacy, original behavior propensity, native Owner/Witness,
LedgerWriter permission or complete IB Flex/NAV account PnL. The
original four rejected windows remain CENSORED and future model
training and promotion remain **HOLD**.

No production service, source monitor, quote TTL, broker driver,
original frozen artifact or original order was modified.
