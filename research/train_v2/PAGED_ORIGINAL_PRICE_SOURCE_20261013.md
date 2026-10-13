# Train V2: paged original PAPER price proofs, 2026-10-13

Research-only branch feat/train-v2-paged-price-source-20261013.
This is a new opt-in READ-ONLY source view, not a replacement of any frozen
Native outcome verifier, deployed source writer, existing trial, trading
strategy, canonical LedgerWriter, Artifact Owner or model.

## Real capacity blocker and safety design

The historical postmodel_requote loader intentionally stops at 320 numeric
original source segments or 256 MiB full-source bytes. On Pocket4 at the
initial review, the existing feed held 283 numeric segments, 282 naturally
ended, 8 original hash-linked archive pages and 212,297,546 source bytes.
Expanding that old bound or trimming old rows changes its original admission
protocol and is not acceptable.

The existing Laya source_archive module already seals original sources
32 per page, independently checkpoints every page and retains all old bytes.
New paged_price_window verifies EVERY original numeric segment and every
archived original prefix/page/checkpoint before retaining only an explicit
bounded 2-hour quote-availability window in memory. Selected files get a
second exact-hash, broker-reported-TTL check. Empty originals and source
identity remain part of the complete source hash, never silently skipped.

The limits are explicit and fail closed: 128 verified archive pages,
128 unpaged suffix segments, 64 MiB per original source file, 2 million
global duplicate-checked quote identities, at most 18,000 returned quotes
and two hours per selected window. Missing historical source, incorrect
period, malformed/duplicate segment, source tampering, deleted early
page, truncated checkpoint, earlier unsealed original, mutable source
between reads, archive rotation during verification and bound overflow
all reject. Active numeric suffix and recovery-live source alias are
never misrepresented as naturally sealed outcome evidence.

Source-wide fingerprinting uses the original legacy algorithm over all
original sealed numeric segments. New append-only segments extend the
current fingerprint but may not change the first N-segment FROZEN prefix
fingerprint of an earlier original trial. Both are independently verified,
and a change to an old segment rejects. The result is read-only local
hash custody, not an IB or Canonical host signature and not hostile-owner
filesystem proof.

## Real source compatibility and independent old-price proof

Initial one-hour source scan from 12:45–13:45 (UTC+8) reverified
283 numeric original segments (282 ended, newest unsealed), 8 archive
pages, 212,297,546 original bytes and 135,162 original quote receipts.
Only 1,192 target window quotes were retained. Original entire-source
fingerprint matched the independently sealed Native price pair:
bf5847eddedf53895f3d3a6b8701f1df020fa452a96855404591110ce034803e

When the original source naturally rotated, an initial strict current
full-fingerprint equality check correctly rejected. The new v2
frozen-prefix fence repaired the *verification method*, not the old
plan or original price. The first 282 sealed source segments still have
their exact prior fingerprint. The later complete 283-segment source
fingerprint is:
d9b8db2128a5b964b126d6dae3161156fdf1143cd65bd0b2030bc5e83ce8c570

PAGED reader verification of the two existing immutable Native price
outcome receipts recomputed both original 300-second PAPER MID prices
EXACTLY, with separate 132/139-quote availability windows:
- First original +0.626541179419017314913020000 bps, exactly matched.
- Second original -0.1789957443761774563809750000 bps, exactly matched.
Both previously sealed outcome receipts and old future forecast/commit
files remain immutable. There were 0 new forecasts, 0 attributed fills,
0 economic trade samples and 0 broker orders.

Create-only private receipt:
~/.local/state/heptatrader/operator-evidence/train-v2-paged-original-source-20261013/prior-native-price-pair-paged-crosscheck.json
SHA256:
d850fa69de37834a408347f44524984274909ce340067631cb7a889be441deb5

The old original strictly failed float/f64 replay remains failed. A
post-hoc paged price readout cannot itself count as a newly signed
forecast, independent observer, actual net PnL or successful model
promotion. The two original forecasts were correct direction 1/2 and
neither observed move cleared the preexisting hypothetical cost hurdle.

## Adversarial and scale tests

The dedicated tests synthesize >320 distinct authentic-format original
PAPER segments and seal them via the existing 32-segment archive writer.
At 321 fully sealed source segments, the old full-archive reader MUST
return its original numeric-source-budget failure, while the paged reader
verifies all ten archived pages and all 321 original segments yet
returns only two selected time-window quotes.

Additional negative tests cover tampered old page, removed original
segment, duplicate start, forged original price outcome, conflicting
old-vs-new source frontier, wrong original quote/horizon/period, unsealed
source, altered record during scan, count/byte/segment caps, and
post-freeze append-only growth that must preserve the original prefix.

The new code does not change existing source_archive, postmodel_requote,
Native price forecast/strict verifier, native_price_ticks_outcome,
Native price cohort tests or the production collector systemd units.

## Remaining actual acceptance barriers

No real new SOURCE/REQUEST/PREPARED forecast or traded economic reward was
created by this reader. Before production expansion a future predeclared
price-only trial must pin its own paged-source reader version and exact
original prefix identity. Native Canonical producer/observer/owner
enrollment, broker execution cost/slippage, profitable independent
prospective trades, current FX valuations and account-wide PnL remain
independent HOLD criteria. No promotion or trader authority is granted.
