# FX Ridge four-window: Mac + X230 physical-source readback (2026-10-12)

## Verdict

**PASS for a frozen, historical/price-only research integrity check; HOLD
for profitable trading, native product dataset admission, and promotion.**

The already completed four-window original IB PAPER quote experiment is fixed,
and no later observation is imported into the decision record. The old
four-fold historical model selected abstention everywhere; it remains the
same frozen model for all four prospective windows.

| Beijing time | Decision | 15-minute original midpoint move | Result |
|---|---|---:|---|
| 18:10 | CENSORED: original quote aged out | none | no label, no backfill |
| 18:30 | NO_TRADE / -0.00729512 bps | -1.83296748 bps | direction correct |
| 18:50 | NO_TRADE / +0.05912145 bps | -3.57732157 bps | direction wrong |
| 19:10 | NO_TRADE / +0.08260233 bps | -0.80531148 bps | direction wrong |

**3/4 eligible, 1/4 censored, 1/3 direction correct, mean absolute
forecast error 2.116676397332736232396993 bps, 0/3 forecasts above
the frozen 10.9133557 bps cost-plus-margin hurdle.** Zero independent
actual after-cost broker execution PnL samples. Statistical and product
qualification remain HOLD; these samples are not evidence of alpha.

## Immutable source anchors

An independent verifier now has **hard-coded**, previously observed SHA256
pins for all four immutable research roots and the exact verifier code:

| Artifact | Frozen SHA256 |
|---|---|
| Pre-window campaign seal | `e5da7854edfe2af4ab00a32ba0b7dc3cf1a41891e1010455de25ddf07c1c28dc` |
| Natural-exit campaign final | `448a043e1904192882d1a846132a627893cb3b5e8003d4909ca86a6ca224351e` |
| Existing independent pocket4 audit v2 | `af46b3c9f3028cac98e790e75a67c0919f4ba0ad192ab9477abb770580ce7a4c` |
| Inspected standard-library original-quote verifier | `ad29a9086226c547a9bbef7e474b08c5b39a9bb12bfc04a9408dd804c3365e3f` |
| Exact four-window metadata ZIP | `dc2d3c416385dccb0561e63b9c2538f5ba6f5947a3c078ef3ec37e85caa5c5f7` |

This frozen ZIP contains the campaign final, pre-registered original plans,
read-only execution attempts, 3 already matured forecast/price records, the
original local audit receipt, and the censored window-00 attempt. The original
source segment bytes are **not** taken from current revised history: each of
the three separate source ZIPs is restricted to the exact original immutable
source prefixes recorded for that window. ZIP SHA256:

- Window 01: `05017cf04d0f964abc118cdac76b84c11cd10b9b5d4d7c3e94a5a55422d5c06f`
- Window 02: `53d26c1b2e00eb9e08ad30257ee01f126f3f0284cc39c9b4cbc3a9937d23e6c6`
- Window 03: `cc24cb58adebff3188535d259398b94240998a41785d10be30e471ec98c0e1ec`

All input files are private 0600; work directories 0700. Exported material
deliberately excludes broker credentials, original OMS journals, secret tokens,
and private keys. **Source quotation chains are locally hashed, not signed
by IB.** A physical verifier recomputes source hash sequence, the 13 causal
five-minute quote pseudo-closes, Ridge numerical result, previously frozen
costs, first originally matured future quote and reported price error.

Window 00 **must** retain a CENSORED decision and CENSORED maturity, and
must have no source episode ZIP or hypothetical future trading reward. The
end-to-end verifier fails closed if a future result is attached to it.

## Actual Mac and X230 host verification

Routes from `qian-qi`: `ssh -o StrictHostKeyChecking=yes mac-ts` and
`ssh -o StrictHostKeyChecking=yes x230-ts`. These used existing
`known_hosts` identities, **not trust-on-first-use or bypass**. Mac used
Python 3.9 standard library; X230 used Python 3.12. Their clocks and machine
administration were not promoted to independent trust roots.

Both hosts independently reran all three source/model/price ZIPs, then a
separate complete *four-window* audit, pinning all five source hashes above,
preserving window-00 censorship, matching the four calendar windows, fees,
forecast/price numbers, and no-trade status.

- **MacBook-Pro.local:** 4-window audit v3 SHA256
  `fbc7799d86d029ae026e672e1ad8f031190b245d5bc018888c72fe5fc123653f`.
- **qian-ThinkPad-X230:** 4-window audit v3 SHA256
  `0e22c4d6800b17adee7cc5d1e2e28fad8fd3fd63d37962e509b13be6d1305ab5`.

The two hosts disagree only in floating rounding at ~1e-13 bps and their
own host/time metadata; all original price labels, denominator, censorship,
gross direction statistics and cost hurdle agree. Their source is a shared
frozen pocket4 capture, **not** two separately acquired IB quotations.

Private receipts on pocket4:

`~/.local/state/heptatrader/operator-evidence/fx-ridge-four-window-physical-20261012/`

Private source and physical-host reports also remain on qian-qi, Mac and
X230; they are not published to GitHub.

## Reproduction (research only)

From the pocket4 worktree:

```sh
cd ~/work/heptatrader-fx-ridge-finite-campaign-20261012
P=$HOME/.local/state/heptatrader/operator-evidence/fx-ridge-four-window-physical-20261012

python3 -B research/train_v2/fx_ridge_four_window_physical_verify.py \
  --mode verify --episodes "$P" --original-verifier "$P/verifier.py" \
  --campaign-zip "$P/four-window-campaign-metadata-v1.zip" \
  --output "$P/recheck-NEW-UNUSED-NAME.json"

export PYTHONPATH=research:research/neuron_learning:scripts
export HT_FX_PHYSICAL_BUNDLE="$P/four-window-campaign-metadata-v1.zip"
export HT_FX_PHYSICAL_EPISODES="$P"
export HT_FX_PHYSICAL_VERIFIER="$P/verifier.py"
python3 -B -m unittest discover -s tests/python \
  -p 'test_fx_ridge_four_window_physical_verify.py' -v
```

The physical-host portable validator has no IB client or API dependency.
Exports and results are written exclusively (O_EXCL) and cannot overwrite
prior evidence. Actual immutable campaign archives are never rewritten.
Focused tests include source tampering, swapped windows, missing episode,
self-reissued verifier, duplicate JSON, nonfinite numerics, unsafe source
permissions, symlinks, and non-overwriting reports.

This implementation intentionally pins the **one previously completed
campaign**, not a generic facility that can automatically accept future
campaigns by presenting new self-signed hashes. A future genuine campaign
must be precommitted separately before any real observations.

## Remaining blockers

1. Unstable quote continuity caused window-00 original quote expiration.
   Keep that failure; diagnose the original source/collector endpoint or
   gaps without widening time limits and without retroactive relabeling.
2. The model returned 1/3 correct directions and 0/3 above true declared
   stress costs; no prospective alpha or independent execution net profit.
3. Original trader's true behavior propensities, production Root/Owner,
   native LedgerWriter, independent authorized Witness and currently
   signed approvals are still absent.
4. IB Activity/Flex, reset-opening account NAV, outside liabilities,
   financing and taxes are not complete. Strategy PAPER net PnL is not
   the account's fully reconciled net profit.

**No live driver restart, model training/promotion, broker request,
order/cancel, new trade authority, past sample re-selection or
CENSORED backfill was performed in this research.**
