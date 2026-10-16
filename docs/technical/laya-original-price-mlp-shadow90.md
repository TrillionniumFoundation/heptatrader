# Pocket4 original-source Tiny MLP 90-slot research

## Current state: source only; no new campaign deployed

The proposed new original-source Tiny MLP 90-slot campaign is NOT running.
Its real-model operator preflight was blocked by execution-channel safety,
so do not claim a completed prospective experiment or model promotion.

The *different*, existing original-quote dynamic-prior Shadow 90
experiment remains running in Pocket4 independently and untouched.
It compares a fixed prior to an as-of historical prior, not Tiny MLP.
Its genuine input coverage and observed outcomes must never be credited
to this proposed neural campaign.

A one-year revised-history GPU backtest had 40,767 distinct five-minute
test anchors across 14 validation-selected folds. The small 24-32-7
MLP improved Brier and NLL versus frequency prior in all 14 folds,
but source bars were revised, not available contemporaneously.
The first real original PAPER-quote Tiny MLP forward prediction
*missed*: Brier 0.7769793 versus frequency prior 0.2370791.
It is explicitly retained as negative evidence and cannot be
retrofitted into another successful trial.

## Fixed source-only protocol

The research module is research/neuron_learning/laya_cell/
price_original_campaign.py and the runner is
scripts/research_laya_price_original_shadow90.py.

- Register all 90 immutable original-quote anchors in advance.
  Each fixed input is an aligned five-minute closed bar; windows
  are exactly 300,000 ms apart with a 300,000 ms horizon.
  Missing input or outcome is CENSORED, never backfilled.
- Bind SHA256 of the frozen last-fold Tiny MLP weights, revised
  annual bars, original 14-fold plan, model feature-builder,
  original-tick bridge, research runner, research contract and
  read-only source config.
- Recompute fold-13 feature mean/std from only original historical
  training indices, then reproduce the frozen GPU fold-13 validation
  Brier, NLL and Top-1 with the *unchanged* checkpoint on CPU.
  Save a SHA-pinned standardizer and static training class prior.
- Use only original SOURCE-VERIFIED PAPER quote events known by
  the as-of timestamp. Build 13 strictly covered contiguous
  five-minute OHLC buckets, no interpolation or revised IB
  replacement, then compute 24 historic-compatible features.
- Verified recent-source reads are preferred. Full original archive
  fallback is permitted only on documented archival rotation or
  coverage limitations. Integrity mismatches or changed archive
  proof MUST fail closed, not trigger a favorable fallback.
- Every forecast must be written within 60 seconds of the
  PRECOMMITTED anchor and BEFORE the five-minute outcome.
  Keep the immutable quote-snapshot ID, actual quote receipt pins,
  original feature digest, model probability vector, frozen
  training-frequency prior and number of inputs exceeding
  the fixed historical feature clamp.
- Score only the next strictly completed original quote bar.
  Wait the full original 60-second endpoint grace for delayed
  receipts, then record a permanently unavailable outcome as
  CENSORED. No shifted clock, changed forecast, success-only
  window selection or recovery-time backfill.
- Report model/prior Brier, NLL and hit counts side by side.
  At least 88/90 actual observations establish only RESEARCH
  SOURCE COVERAGE, not the original Laya candidate qualification,
  a tradable edge or model promotion.

The runner is CPU inference only and cannot write Broker orders,
change the existing formal Laya trial or its historical evidence,
promote a candidate, or change the existing prequential-prior
Shadow campaign. No trading device other than Pocket4 is used.

## Important domain-shift and acceptance limits

The fold-13 original historical GPU weights have SHA256
7340d6429fbeb342ffd3d2038f4f3ea4b6f5252dcd6bc0efa7ef77fdd95c72e1.
The previously checked first real original-source sample included
a feature at 9.698 standard deviations pre-clamp. Exact feature
math parity with archived revised IB data does not establish
the original-to-revised input distribution identity.

Even 90/90 input success would be insufficient to promote a
new trading agent. The model needs prospective independent
price quality against the predeclared prior, followed by
separate, original, cost-aware economic/owner qualification.

## Review gate

The source-only contract now has 38 focused adversarial CPU
tests covering fixed slots, causal cutoffs, frozen priors, source
proofs, anti-forgery, grade receipts, full outcome grace, no
order authority, observed-only Brier/NLL averages and source
archive-rotation fallback. It refuses to score a missed original
outcome as OBSERVED after its endpoint grace, even if the source
has become complete in hindsight. A CENSORED outcome stays
in the 90-slot coverage denominator and cannot silently increase
observed-only model quality. Source receipts, experiment scope,
training-model pins and status identities fail closed. Full repository
regressions and any real standalone run must be verified
independently; the user has not been told it is already active.

An attempted full Pocket4 frozen-year-model preflight was
blocked by the execution channel, NOT an OS resource denial.
No long-lived campaign or new formal study was started.


## Pocket4 original quote source-only probe

At 2026-10-16T05:00:30Z a read-only original PAPER quote
probe independently verified 8,978 quotes, 23 source-prefix
proofs and 55 strictly complete five-minute buckets. The last
complete bucket was 30.555 seconds old, within the registered
60-second eligibility grace. The 24 model input features were
constructed in 1.115 seconds without loading neural weights,
writing a prediction, modifying the canonical trial or
sending orders. The largest unnormalized absolute feature
was 8.455 (not a standard-deviation measurement). This
proves original source inputs were ready at that instant,
not that the real Tiny MLP model was checked or launched.

The probe is retained on Pocket4 under
~/.local/state/heptatrader/operator-evidence/laya-year-original-shadow90-preflight-20261016/source-feature-readonly-v1.json.
The contract now also validates the exact 90th fixed deadline,
complete model/source pin names, research non-authority,
strict as-of feature identity, and no nonfinite features.
The research-only live status contains observed-only
mean Brier, NLL and top-1 counts for MLP and frozen
frequency prior; every CENSORED source window remains
visible and cannot be retrospectively repaired.

**No new Tiny MLP campaign was launched.** The separate
real-model/normalizer execution preflight remains blocked
by the execution channel. The already-running independent
prequential-prior Shadow experiment remains untouched.
