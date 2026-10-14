# Independent original FX prospective price receipt verifier — 2026-10-13

**State:** research-only / no broker I/O / NO_TRADE / no model-fit or
product promotion. This is a NEW, isolated **audit** worktree; it does
not modify the running `heptatrader-fx-original-future-20261012.service`
producer, its frozen code, any source file, risk policy, existing PAPER
orders or account balances.

## Actual pre-event custody and first failure

Pocket4 registered exactly four original-price 15/30/60/120-minute
windows for 2026-10-13 00:20, 03:35, 06:50, 10:05 UTC+08:00, as
part of research PR #236. The physical **qian-qi** workstation
received copies of `plan.json` and `sealed.json` at
**2026-10-12 23:59:46 UTC+08:00**, before the first target. This audit
hard-codes those **already pinned, externally retained** file SHA256s:

- plan SHA256:
  `02aa4a2662b743203cc925d09d3250bcc0e02155cf5d00bb319b0526448aaa04`
- seal SHA256:
  `1f13ed5c7a8fcdc29cc0881bf5c7854c263876f5f89bb6bc87633d5b2b33860b`
- original frozen producer code SHA256:
  `e08d952e80423a0fdd296f37c5d2d6efbdfb64ebd246ff03e00901f3465e2411`
- PAPER period:
  `61995fcedc2993d7ac33d2ed86c9613068c5a3ecd0d1c377e612639cd5a0958f`

At the 00:20:01 grid decision attempt, the producer durably recorded
`CENSORED` with original
`EXCEPTION_Hold_ORIGINAL_READINESS_HOLD_OR_STALE`. There was NO
original preaction acceptance, and this is NOT an alpha/price sample.
The original decision receipt SHA256:
`2fe06eeb07f61f64f2a71d7caa9f05b231ea3fc913c32e00053bad8220c85a81`.

Qian-qi, using its **prior copy** of the plan/seal, fetched this
decision and independently verified its original registration window,
`CENSORED` status, research-only scope and no order authority.
Separate qian-qi 00:26:44 (+08:00) crosshost receipt SHA256:
`50719f808ee5acc7aaa2b53a375b5c09a89ae6943d6eeafd81f57b95ba03a8b1`.
The private qian-qi file is under
`~/.local/state/heptatrader-fx-future-precommit-20261012/`.
This is independent **file custody**, NOT a signed IB source or native
Root/Owner/Witness admission.

During nearby **00:19 and 00:20:56** separate monitoring observations
reported READY 24/24, but the exact 00:20:01 local health snapshot
that led to the rejection was **not durably retained by the producer**.
Never deduce it was READY in the failed decision from adjacent samples,
rewrite CENSORED, or state a specific unproven internal HOLD reason.
This evidentiary gap should be corrected only in a **new, separately
precommitted** study by recording the exact health bytes and as-of
hash even on a rejected decision.

## Independent auditor

`research/train_v2/independent_fx_future_audit.py` uses Python
standard library only, no IB connector and no live trading libraries.

1. Verify the exact qian-qi pre-event pinned plan, seal and unchanged
   currently deployed producer code. Validate four chronological,
   fixed 195-minute-spaced anchors and 15/30/60/120-minute
   maturities, no training/trading privileges and producer worker-start.
2. Read ORIGINAL 0600 quote prefix receipts by O_NOFOLLOW with owner,
   inode and explicit bound checks, verify each prefix SHA256 and the
   entire strict local `previous`/sequence/SHA256 JSONL chain,
   PAPER instrument/period/scope, authoritative IB quote and expiry.
   Live append-only growth beyond a frozen prefix is permitted;
   rewriting that frozen prefix, conflicts and after-END events fail.
3. If/when an input passes, independently reconstruct **all 13
   causal 5-minute quote closes** and its 8D features with
   standard-Python arithmetic. This auditor additionally demands
   that *every* selected historical quote remain valid through its
   respective grid boundary. The old producer checked full expiry
   for the **last** quote, but not explicitly for all earlier 12
   closes. A synthetic adversarial fixture proves the stricter
   rejection; there is NO finding that the currently frozen real
   cohort actually contained such a stale older quote.
4. Independently recompute the original IB cost receipt p99
   spread, 2×p90 commissions, hypothetical 4bps slippage and
   2bps margin; never accept a more favorable cost value supplied
   solely by producer JSON. No execution slippage is attested.
5. For future maturities **only after genuine original receipts
   exist**, verify frozen 6.5–15.0 second completion window,
   original earliest-event choice (not a cherry-picked later
   quote), 0..5s availability bound, preaction source ID,
   immutable prefix hashes and exact Decimal midpoint,
   ask→bid and bid→ask returns. These are **quote-side hypotheticals**,
   NOT executable real fills.
6. For any CENSORED window/horizon, preserve original reason
   and forbid a parallel "qualified" replacement. If a receipt
   is not yet due, report PENDING; if overdue but missing, record
   missing without inventing success or new source data.

The independent auditor's output is an O_EXCL, 0600 file under a
0700 private research directory. It never deletes or edits originals.

### Reproduction

```sh
cd ~/work/heptatrader-fx-future-independent-20261013
python3 -B research/train_v2/independent_fx_future_audit.py \
  --root ~/.local/state/heptatrader/operator-evidence/fx-prospective-original-horizons-20261012-v1 \
  --frozen-code ~/work/heptatrader-fx-prospective-price-envelope-20261012/research/train_v2/fx_original_forward_price.py \
  --output ~/.local/state/heptatrader/operator-evidence/fx-future-independent-20261013/NEW-audit-output.json
```

Use **a fresh unused output filename** every time. Do not apply any
patch from this audit to the still-running code or to the original
plan/seal. Historical price labels, broker claims, current account NAV
and original risk/exit controls are unaffected.

## Remaining hard hold

This is still an original PAPER quote **price-only** study with
no prospectively fitted predictor or original behavior propensities.
Local prefix SHA-chains and qian-qi file custody are not cryptographic
IB market signatures. The native Train V2 120-minute FeatureSpec cap
continues to be 60 minutes; the new 120-minute research is NOT a native
admission. No independently proved real FX commissions/slippage/financing,
complete broker Activity/Flex NAV, Owner/Root/LedgerWriter/Witness
or verified positive after-cost edge exists.

**Production, model fit, broker orders, promotion: HOLD.**
