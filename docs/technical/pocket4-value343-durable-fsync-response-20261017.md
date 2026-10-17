# Pocket4 Value343: durable post-fsync RESPONSE admission, v5

Research-only. The historical 17D Value343 model still did NOT beat
the original train-median MAE baseline and has NO trade permission.

## Concrete flaw in v4

The previously deployed source observer recorded sealed_at_ms BEFORE
writing the prediction file. A real original RESPONSE could become
available while the prediction was still being fsynced, despite the
old prewrite timestamp comparing earlier than RESPONSE. An eventual
original DECISION or fee-complete label does not make that earlier
timestamp a real pre-RESPONSE forecast commitment.

## v5 mechanism

V5 keeps that old timestamp as an attempt clock ONLY. After the original
prediction file bytes and directory have actually been fsynced and
atomically linked, v5 records its kernel final-file ctime_ns and a
separate time.time_ns sampled after successful write_once return.
It then writes an immutable, SHA-linked second durable_seals receipt.

Original RESPONSE admission now requires the genuine source producer
RESPONSE available_at_ms * 1000000 be STRICTLY later than the full
durable_fsync_returned_at_ns, and also enforces the fixed 90-second
original request deadline against this final durable timestamp.
The prediction, commit witness, original RESPONSE proof, prepared
command and later original OMS execution+fee timestamps are rechecked
by the future scoring code. An absent/corrupt durable receipt fails
closed; a real RESPONSE during fsync is CENSORED; an observer crash
after prediction fsync but before its proof cannot be fixed by later
backfilled timestamps. A response in the same millisecond as the
durable completion cannot pass a rounded millisecond comparison.
The previous no-authority BUY/SELL pre-response research scope remains.

The source version, immutable model freeze, proof schema and private
root are completely new: v5 cannot overwrite v1-v4 private
research receipts and the existing frozen historical model is never
refitted. Cache source SHA and watcher code SHA are both frozen.

## Isolated full-chain engineering evidence

On Pocket4, tests use genuine production research source parser,
Linux original journal inotify, source hash/sequence verification,
the existing VALUE original USD100k economic recomputation, and
synthetic original-format REQUEST, RESPONSE, Decision, OPEN/CLOSE,
broker execution and USD commissions. No real trading or new
economic label is generated from these synthetic fixtures.

Tests cover true fsync before response; response during slow write;
simulated crash after durable file without second witness; strict
sub-millisecond source RESPONSE denial; tampered witness SHA/kernel
ctime; 90-second expiration at actual fsync completion; no original
RESPONSE yet; duplicate final write refusal; original source ID and
counterfactual side constraints. Full original 13,646 source events
and 4,432 REQUESTs remain anchored to the same Pocket4 manifest.

## Pocket4-only deployment and rollback

Use the separate v5 source-only user systemd unit:
heptatrader-value343-predecision-durable.service

Resource limits: CPUQuota40%, MemoryMax512M, MemorySwapMax0,
network denied, NoNewPrivileges, production home read-only, and
write access only to a private, owner-only v5 evidence folder.
v4 must remain on duty until the new release, exact freeze,
source full replay, physical inotify FD and bounded runtime
are independently accepted, then stop/disable only v4.
Never restart Laya, Execution, Gateway, IB Gateway or FX/BTC
trading services as a precondition for this research.

Even a perfect engineering reproduction is not an actual
prospective prediction, does not prove economic alpha and does not
authorize model promotion or live Broker orders.

## Real Pocket4 v5 cutover and create-once verification

The source patch was pushed as commit
c8e8762f0a58cb4635d3bf7038c5be957ddd05be on the separate
stacked Draft research branch. The physical v5 source-only release
is installed read-only at
  ~/.local/share/heptatrader/value343-prospective/releases/value343-durable-20261017-v5

The v5 observer executable SHA256 is
  b218825f11625a1d71ad8cdcd35540ef308c8834b9013fd8478d185b0de5bcfc
and the unchanged v4 incremental source parser SHA256 is
  cf64fb78b82f8038d005df2ff52bb926dcb7da5edef67259cdbac38e41862bf8.

New independent v5 freeze path:
  ~/.local/state/heptatrader/operator-evidence/value343-predecision-durable-20261017/freeze.json
Freeze SHA256:
  dd847aaa46587ce6a27d4b04897fe816b17020833ba4a02881eb13ad4ce148db

The actual user unit heptatrader-value343-predecision-durable.service
was started in parallel with v4, successfully re-read all 13,646
original journal events/4,432 older REQUEST identities, established
its real Linux inotify descriptor and reached stable operation with
zero restarts and about 138 MiB observed memory peak. Only after this
was true did the operator enable v5 for reboot and disable/stop v4.
V1/v2/v3 research observers also remain inactive. Older model
releases, freeze files and failed or accepted evidence were NOT changed.

The original PAPER FX/BTC Drivers, Laya Trainer/Evaluator/Feed,
Execution and Gateway were all physically active with their prior
PIDs; none was restarted to deploy this read-only experiment.
The v5 service uses the same network-denied, 40% CPU, 512MiB memory,
zero-swap, read-only-original files and private v5-only writing path
as its predecessor.

Owner-private create-once physical deployment qualification:
  ~/.local/state/heptatrader/operator-evidence/value343-postfsync-v5-deployment-20261017/runtime-qualification-v1.json
SHA256:
  fac9b5b006e8a7da262f9bd80fbbca1924cb48b7395f18a09e03f1a5f582a68e

The qualification binds program SHA, original source-cache SHA,
model freeze SHA, actual systemd unit SHA, source original history,
process identity, inotify FD, memory peak, original peer PIDs and
zero Broker/model authority. Exactly 54 focused Value tests, 116
Source tests and 705 historical-training tests were green on the
same Pocket4 checkout, including seven new explicit fsync timing
cases, and the local docs/unit/whitespace checks also passed.

At original v5 activation all 4,432 source requests were historical;
real post-v5-frozen prospective predictions, durable_seals,
RESPONSE timing proofs, missed/censored and economic scores are
ALL ZERO. This is a valid closed-source/closed-market startup, not
proof of future predictive quality or profitability.

Research-only rollback without touching production services:
stop+disable heptatrader-value343-predecision-durable.service;
then enable+start the still-pinned
heptatrader-value343-predecision-incremental.service. Never delete
or modify any of the prior evidence or reclassify a missed request
as a successful future pre-RESPONSE forecast.

## First actual autonomous 180-second v5 independent full-source replay

After v5 replaced the v4 user research watcher and continued running,
the actual periodic independent whole-original source rehash completed
successfully **without operator invocation of the verifier**.
The logged result confirmed 13,646 authentic original events, 4,432
original REQUESTs, and zero unverified extra events, with
`full_source_consistent=true`. No original source history, original
Broker economics or model weights were changed. The v5 user service
remained enabled/active with zero restarts and a measured maximum
~231MiB during the complete replay (below its 512MiB hard limit).

The independent, create-only Pocket4 source-cycle receipt is:
  ~/.local/state/heptatrader/operator-evidence/value343-postfsync-v5-full-replay-20261017/autonomous-original-source-replay-v1.json
SHA256:
  c0400ed77c22fe7b37d493ff41d30dbeabb14c033aa2cd240c03258cc2917e94

The v5 research output still contains zero genuine new predictions,
post-fsync proof receipts, original RESPONSE proofs, missed/censored
requests or settled after-fee scores. That is an operational source
integrity pass during closed FX markets, NOT a statement about
prospective prediction efficacy or profitability. The original v4,
v3, v2 and v1 release paths and historical evidence remain intact.
