# Train V2 FX Ridge: independently prove earliest mature quote across original numeric segments

Date: 2026-10-12. Research-only candidate stacked on
fx-ridge-prefix-replay d6c4f70 / PR #220. Neither the precommitted
four-window original producer nor historical model, live FX/BTC driver,
OMS, broker, Laya trainer, old trial, Root or Artifact Owner is modified.

## Actual independent audit gap closed

The preceding independent replay verified all original hash-chained market
quotes in the **producer-submitted** mature-source receipt prefixes and
selected the earliest eligible quote WITHIN those prefixes. A malicious or
defective producer could omit another original numeric writer segment
containing a better or EARLIER eligible quote, then provide internally
consistent but incomplete mature witness prefixes. This could corrupt
price-label firstness without forging any previously submitted prefix.

New fx_ridge_global_earliest.py separately enumerates every currently
present original numeric writer segment in the source directory as of each
frozen maturity attempt. It excludes the nonnumeric legacy recovery-live
lane, rejects malformed numeric-lookalike paths, symlinks, incorrect
private file modes, changing hash chains, malformed duplicate JSON keys,
inconsistent timestamps, crossed/expired/non-authoritative IB quotes,
same-timestamp contradictory prices and missing original segment files.

The verifier replays full hash-chain records and their original event and
available-at timestamps without importing the original experiment producer.
For each precommitted due+5-second outcome tolerance it derives the
globally earliest locally recorded eligible original quote over ALL
currently available numeric writer segments with at_ms no later than the
original maturity attempt, then independently requires the frozen mature
receipt to name exactly that price event/receipt/availability time.

The scanner has fixed 512-segment, 4MiB-per-segment and 256MiB-total
I/O bounds. These bounds fail closed. All checks are read-only: no
strategy, IB API, model fitting, timestamp repair, order or automatic
promotion action exists.

## Precise limits

The global word means global over the **currently accessible original
numeric writer filesystem**; it is NOT a broker-signed complete archive
or an independent proof that the same-UID system administrator never
deleted other segments before this review. Current source bytes can
also grow after the frozen maturity event; the eligibility calculation
always truncates by original received_at <= original maturity attempt.
The recovery-live nonnumeric legacy lane is deliberately excluded.
The independent original-first test is therefore a stronger local
mathematical check, not an externally-rooted custody proof.

## Actual 2026-10-12 finite research campaign

Original 4-window campaign registered 17:55 UTC+8 for 18:10, 18:30,
18:50 and 19:10 (20-minute spacing) with a 15-minute fixed horizon.
One sample was appropriately censored, three prospective PRICE-ONLY
outputs became mature, and all four had independently revalidated
original precommit identities; no failed window was rescheduled.

Final independent scanning at 19:28 UTC+8:
- 18:10: CENSORED LAST_ORIGINAL_QUOTE_TOO_OLD.
- 18:30: predicted -0.007295119795 bps; original mature -1.832967484945 bps;
  earliest among 245 present numeric source segments.
- 18:50: predicted +0.059121451357 bps; original mature -3.577321569908 bps;
  earliest among 245 present numeric source segments.
- 19:10: predicted +0.082602329000 bps; original mature -0.805311476583 bps;
  earliest among 246 present numeric source segments.

Same-three-observation absolute price-error sums:
- Frozen Ridge: 6.350029191998... basis points.
- Always-zero-price-change reference: 6.215600531436... basis points.

These are purely DESCRIPTIVE PRICE FORECAST errors on 3 outcomes, not
risk adjusted, independently powered or executable economic PnL. The
frozen cost stress hurdle was around 10.9133557 bps, far above the
forecast magnitudes. Historical backtest training selected NO_TRADE in
all 3 folds. The actual finite experiment also selected NO_TRADE every
time and sent zero broker orders. No positive alpha, new model selection,
financial-return qualification or real on-policy trade label is claimed.

Final frozen new independent local receipt:
~/.local/state/heptatrader/operator-evidence/fx-ridge-finite-independent-20261012/four-final-global-original-check-1928.json
SHA256: 46353f866604805fd3238e525cbaf8b30656ee97b4183be79a8220fe1766d4be

A prior in-progress original-prefix review from 19:15 and global check
from 19:23 remain separately preserved; they are NOT overwritten or
misreported as completed. After 19:25 the original finite producer wrote
its own campaign-final.json; this checker never touches it.

## Required subsequent Product gates

- Trust distribution and independent Root/Governance enrollment in the
  product host (not operator-controlled lab keys).
- Original opaque LLM selected action support, exact logged propensities
  and all missing/veto/no-model opportunities recorded before PREPARED.
- Signed original Native LedgerWriter/Witness, independent terminal
  broker execution cost and source correction/withdrawal, current V3
  dataset/Artifact Owner, future economic and statistical qualification.
- Independent off-host source inventory sealing or broker-provided
  custody/coverage proof for a *global* earliest-quote claim beyond
  this one local host's currently retained original writer files.
- NO trade/promotion until genuinely unseen after-cost margin and the
  canonical owner path are independently verified.


## Exact pocket4 regression and remaining evidence boundary

- New cross-segment independent earliest-quote adversarial suite: 14 passed,
  including a deliberately omitted earlier eligible quote from an entirely
  different original writer segment, same-time conflicting prices, modified
  original hash chain, broken/missing quote source, symlink and file mode,
  invalid JSON/nonfinite, and cutoff-time non-backfill.
- Existing historical-training suite: 400 tests passed, 7 preexisting skipped.
- Full core Python suite: 2,247 tests passed, 24 preexisting skipped.
- Source/build/ownership suite: 106 passed.
- Source code still never opens a market/broker socket, order pathway,
  trainer or product artifact selection. The frozen producer root and its
  campaign-final receipt are never written by this branch.

Original immutable complete four-window global audit report SHA256:
46353f866604805fd3238e525cbaf8b30656ee97b4183be79a8220fe1766d4be.
Its three mature target prices are verified against independently enumerated
245, 245 and 246 actual numeric writer segments, respectively. The fourth
original sample remains a CENSORED FIRST window in the fixed denominator.

Statistical promotion is prohibited even when a price direction happens
to match: zero executed trades, no actual fill/slippage/financing return,
one censored sample, only three retained price labels, no independently
retained original source catalog and no true original LLM propensity.
