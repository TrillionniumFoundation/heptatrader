# Laya Trainer/Evaluator: archive-aware pinned Guard repair (2026-10-13)

## Verified root cause

Both original resident units use historical_training.resident_resource_guard.
Their systemd PYTHONPATH overrides point at the verified c17f338 source-archive
release, but the original resource Guard's main function prepends the OLD
original_config.runtime_directory to sys.path before importing the actual
Supervisor and Evaluator. The unchanged older training-repair runtime's
resident_data.py rejects more than 128 segments, causing both active Guard
error receipts to report RESIDENT_SEGMENT_BOUND_NO_HISTORY_DELETION.

Pocket4 actually had >275 original PAPER quote segments; 8 existing
immutable archive pages cover 256 originals and preserve full source SHA.
The remaining unarchived segments were below the existing 128 bound.
The separately running original quote-health test was 24/24 usable.
Do not delete original source to hide a runtime mismatch.

## Stage-only exact-pin migration

laya_guard_archive_cutover.py verifies the original Guard and Config hashes,
all 53 original runtime pins, the real pinned c17f338 Git commit and exact
installed source bytes. The candidate runtime keeps 51 files unchanged,
replaces only dataset.py and resident_data.py and adds one pin for
source_archive.py. Any other changed/missing file fails closed.
The new config retains period, existing candidate/job/evaluation state,
original OMS/BTC/FX attribution, worker sandbox, resource policy,
source health, GPU single-worker guard, promotion protocol and no-trade
authority, byte-identically outside the two runtime fields.

New staged config SHA256:
a32913e63611d428aa168131b787b598c77e5c4969d75b2e9e2fceff0ef01d9c

New Guard config SHA256:
71bf38e969fec90a2a7afb7c77d3bcc2dcc2f9e42fb0b1b702e8d5702e1f16b8

Pinned source Git commit:
c17f33846bde974bf1c89c7f040a8b206981172d

Archive module SHA256:
de45ecdb4e7b4894f5563b2f6190d3d757cd718fa431023cdf0882772f574b03

## Actual independent evidence

Without importing trading code, sending broker requests, creating a
candidate or restarting units, the staged original Guard verify_config
and new resident config_check succeeded. The actual archive-aware
resident_data.collect read 276 original source files and 8 archived
pages, saw 129494 verified original quotes, and formed 919 matured
price-only training rows in 4.39 seconds. Exact original source input,
period and model identity remain unmodified. Both independent resource
Guard.check calls reported that the current source and resource evidence
would permit a new worker.

Private test receipt:
~/.local/state/heptatrader/operator-evidence/train-v2-laya-guard-cutover-20261013/source-preflight.json

## Allowed cutover and rollback boundary

Before modifying research services, verify no active GPU child and no
RUNNING or ADMITTED candidate, preserve the complete old unit text
and verify the new Guard, runtime and staged receipt SHA. Add a single
new drop-in to each of Trainer and independent Evaluator to override
only resource Guard config paths, preserving all unit isolation controls.
Daemon reload then restart ONLY the Laya research services, never
PAPER or Execution. Check the actual post-restart original source
read, original job counts, new training/evaluation status and both
old Guard error records. Do not call active/running alone acceptance.

If the preflight fails, remove only this new drop-in, restore original
effective systemd configuration, daemon reload and restart under the
previous Guard. Never delete original evidence, rewrite old jobs or
infer canonical Artifact Owner authority from a healthy source reader.
Current and historical signal/economic promotion remains HOLD.


## Actual staged unit and cutover timing HOLD

The create-only exact unit planner generated both inert drop-in files:

- Trainer SHA256:
  31218b5e40f3b9a4e7589be87e499faebd389b3c2ad24ebbbe87c96949d9542b
- Independent evaluator SHA256:
  8bea5b65740b99be4f33ed624b5f8a5336b3e5db3b4998cc8aeced037725f5d1

The drop-ins override only ExecStart, ExecCondition, PYTHONPATH and
WorkingDirectory for the two RESEARCH units; the existing root unit
resource caps, GPU single-worker Guard, NoNewPrivileges,
RestrictAddressFamilies=AF_UNIX and trade-deny rules remain inherited.
They are NOT installed by the planner.

Current live candidate jobs before cutover: 30 COMPLETED, 1 historical
FAILED, no RUNNING or ADMITTED jobs. Trainer and Evaluator original
systemd cat text was separately sealed before any service modification.
The exact historical errors remain retained; healthy unit state alone
would not satisfy closure.

The genuine PREEXISTING, separately precommitted FX original-vs-cache
canary starts at 2026-10-13 09:59:40 UTC+8 and captures at 10:05:02.
A model reload or newly admitted GPU job before that comparator could
contaminate its 25%-CPU/cadence experiment. Therefore the final
operator activation state is an explicit
HOLD_NO_UNIT_CHANGE_BEFORE_FROZEN_FX_CACHE_CANARY, not a silently
completed service migration. No user unit was modified or restarted.
The immutable private hold and original unit snapshots are in the
same operator evidence directory under activation-hold.json.

After this real-world canary completes, any subsequent cutover must
first independently validate its terminal receipt, verify effective
pre-cutover jobs again and preserve fixed-trial outcomes, then apply
the presealed two exact drop-ins with a write-once/rollback operation.
This document does not create any scheduled future cutover.

## Exact final isolated test evidence

- New migration + systemd planner adversarial tests: 17 passed.
- Historical research discovery: 417 passed, 7 pre-existing skipped.
- Core Python discovery: 2475 passed, 23 pre-existing skipped.
- Source/build/documentation lane: 106 passed.
- Original actual source read-only: 276 source segments, 8 sealed pages,
  129494 original locally verified quotes, 919 matured price rows.
- No model training, signal selection, broker request/order, systemd
  restart, original source/prior receipts modification or canonical
  owner acceptance by this branch.
