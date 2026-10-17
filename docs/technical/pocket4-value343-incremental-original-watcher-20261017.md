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

## Actual v4 Pocket4 installation and previous watcher retirement

The exact research commit `e7cf79202a3ce22fd01b3369640954bca98ecd6d`
was pushed to the dedicated stacked Draft PR #407. The program was
staged read-only at
`~/.local/share/heptatrader/value343-prospective/releases/value343-incremental-20261017-v4`.
Physical installed SHA256 identities:

- v4 observer source: `c22fa6a8ef0356e5c2557f3ae29ebacdfbae4bcec49fbe55a50485f55c15f442`;
- v4 incremental source verifier: `cf64fb78b82f8038d005df2ff52bb926dcb7da5edef67259cdbac38e41862bf8`.

An owner-private v4 only trial was create-once registered at
`~/.local/state/heptatrader/operator-evidence/value343-predecision-incremental-20261017/freeze.json`;
SHA256 `f1b2b0cc44b025e068e7d6b211e20f365581730ffaaddb9096ac1ed0a101c117`.
The model baseline remains exactly the previous historically unqualified
Value343 Huber fit, not a new trained model. The freeze verifies both
source SHA256 values and original historical model identity.

The real Pocket4 user unit
`heptatrader-value343-predecision-incremental.service` has been
started, accepted with physical Linux inotify FD open, stable PID,
zero restarts, about 145MB peak memory and original entire source
verification on startup. It is **enabled for restart and active**.
Existing CPUQuota 40%, MemoryMax 512MiB, MemorySwapMax=0,
original home/source read-only, no network and only the separate v4
research evidence directory writable are retained. Owner `alex`
user lingering was already enabled.

The v3 research observer was disabled/stopped **AFTER** actual v4
inotify, whole-source validation and stable process observation.
The earlier v2 persistent watcher and v1 polling timer remain inactive.
All older frozen receipts, code releases and private research evidence
were retained unchanged, including original failed forensic tests.
Original PAPER Execution, Gateway, EUR/USD/BTC Drivers, Laya Trainer,
Evaluator and Feed were physically rechecked active without research
component restarting their PIDs.

The original source contains 4,432 REQUESTs before v4 activation;
no old request was backfilled as a prospective forecast. After initial
v4 cutover, forward predictions, missed/censored requests, qualified
RESPONSE proofs, rejected receipts and future settled scores are all
**zero**. This is valid for the weekend/closed source and is NOT
qualification of the new Value model or a real live event latency.

Create-only physical Pocket4 deployment qualification:
`~/.local/state/heptatrader/operator-evidence/value343-incremental-v4-deployment-20261017/runtime-qualification-v1.json`.
SHA256:
`1aae04bbdc24c59f6f1c45c760e93ccea87c9d65d4c62b8c1d726f68b232e446`.
This receipt binds source program/cache SHA, v4 freeze, systemd unit,
real inotify FD, process restart count/memory peak, old research
watcher inactivity, live original peer PIDs and zero prospective
qualifications.

Research-only rollback (without interrupting any broker services):
stop+disable **only**
`heptatrader-value343-predecision-incremental.service`; re-enable
and start only the still-pinned
`heptatrader-value343-predecision-hardened.service`.
Never delete v4 partial/failed evidence or pretend a previous
missing source event was genuinely sealed. The actual market-return
trial remains OPEN until a NEW authentic original REQUEST is
observed and sealed before RESPONSE, later scored against the
original broker's full close plus actual USD commissions.

The exact Pocket4 worktree passed 47 focused Value/incremental tests,
116 Source tests, 705 Historical Training tests, documentation,
whitespace and user service validation. Neither external hosted CI
nor the full Core suite, live prospective alpha or policy improvement
is claimed.
