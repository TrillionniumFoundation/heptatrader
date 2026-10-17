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
