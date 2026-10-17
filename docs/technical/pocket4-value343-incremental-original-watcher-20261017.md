# Pocket4 Value343 original-source incremental SHA-chain watcher (v4)

## Why this is a product throughput fix, not a new model

The previous v3 source-only inotify watcher, pinned by PR #405, has
**zero genuine post-freeze prospective outcomes** while EUR/USD is closed.
Its frozen 17D Value model was learned from 217 real prior USD100k
EUR/USD rewards and **failed** to improve on the train median in the
67-row historical holdout. No trading/policy permission can be inferred.

Physical Pocket4 original attribution source has 13,646 hash-chain-verified
original records from 4,432 REQUESTs. All IDs and original economic
manifests remain fixed. Original-request journal writes have median
0ms (maximum 2ms) vs model RESPONSE availability; the real median
available lead time after source write is **6,296ms**, p01 about 4,294ms,
minimum 718ms. Prior v3 full archive scan took ~0.38-0.45 seconds
each event wake, and would grow with retained history.

## V4 cached ORIGINAL incremental reader

New code: research/neuron_learning/laya_cell/value343_journal_cache.py
and research/neuron_learning/laya_cell/value343_prospective.py.

- On initial v4 activation, fully verify the registered, owner-private,
  original EUR/USD PAPER source; inspect manifest/period; follow exact
  SHA256 of every canonical journal event, producer sequence and prior SHA.
- Preserve the source's complete, authentic historical event/request
  objects and each original REQUEST prefix hash. One separate, pinned
  source-only inotify event after startup only reads newly appended
  complete \`*.jsonl\` lines, recomputing the SHA state for that suffix.
  An incomplete appended line is never treated as a model input.
- Reject any source file inode rotation, truncation, unexpected
  same-size rewrite, dropped producer record, chain mismatch, invalid
  private owner/mode, changed manifest, wrong original request identity
  or exceeded file/total source cap. Extra foreign producer files are
  neither scored nor mistaken for the pinned original behavior.
- A concurrent legitimate append AFTER the observer's stat but BEFORE
  its file open is handled by reading exactly the already-observed
  size-bounded prefix, then authenticating the additional record on the
  next inotify event; no artificial source write timestamp or backfill.
- Existing original inotify wakeups still filter unrelated health-file
  changes; fallback check runs every 20s. Every 180s it also performs
  the independent **full** original history rehash and compares original
  event/REQUEST identity with the updated incremental source. Genuine
  new records arriving between the full scan and incremental update are
  accepted only as a verified additive suffix; historical differences
  are refused.
- V4 program AND cache source code SHA256 pins are frozen in an
  immutable, create-only owner-private v4 trial root, distinct from v1,
  v2 and v3 output roots. Production original source is read-only.

The existing research protocol keeps both BUY/SELL forecasts strictly
before original RESPONSE, independent SHA-bound first-RESPONSE proof
before any score, real original prepared command/side and exact
execution + USD commission finality, immutable missed/rejected counts,
and NO Broker operations, training or self-promotion.

## Actual Pocket4 numerical and source verification

Actual read-only original source benchmark from the same live Pocket4:
first full incremental validation ~0.42-0.50s; unchanged subsequent
stat/integrity sweep **0.19-0.25ms** versus full original re-read
~0.38-0.45s. All 13,646 original source events and 4,432 REQUEST IDs
match the independent full reader by original producer/sequence SHA and
original request digest.

This is a reduction in **unchanged archival scan overhead**, not a
promise of a particular model capture rate or measured trading edge.
Proof of actual pre-RESPONSE capture must still come from the next
true producer REQUEST after the new v4 freeze.

Isolated original-wire fixtures test complete and partial native
REQUEST append, RESPONSE append, source hash/previous tamper,
same-size rewrite, truncation, cross-file segment continuity, a
concurrent original append exactly between stat and open, and the
separate full-reader comparison across an active append race.
An isolated original REQUEST is genuinely sealed and then
independently proven preceding a later original RESPONSE in the v4
same-source reader. No synthetic fixture result is presented as
actual EUR/USD market evidence.

## Host boundary

Dedicated owner-only Pocket4 v4 release and independent user systemd
source-only watcher; same 512MiB/no-swap/40%-CPU no-network limits as v3,
with original data read-only and only the separate private evidence root
writable. v3 remains running until v4 is physically installed, checks
the entire source and inotify FD successfully, and has stable bounded
RSS. On successful acceptance disable ONLY v3; retain all frozen v3,
v2, v1 code/receipts for reversible research-only rollback.

Original Laya Trainer/Evaluator/Feed, PAPER Execution, Gateway and
EUR/USD/BTC Drivers must never be stopped or reconfigured for this
read-only source optimization.

**Still OPEN:** first actually new original REQUEST captured before
RESPONSE; independent original response receipt; real economic
settlement with actual commissions; a forward MAE/Brier evidence
against the already-frozen non-neural baselines. This cannot be closed
by an already-seen historical or synthetic record.
