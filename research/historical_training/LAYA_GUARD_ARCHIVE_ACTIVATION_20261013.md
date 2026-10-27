# Laya archive-aware research service activation (2026-10-13)

Historical operation; the fixed-canary activation CLI, exact-job closeout,
stage/unit helpers and paired tests are retired from current source. The commands
below describe only the original operation. Its measurements, failed attempts,
receipts and local runtime data remain retained. Read the original code and tests
from Git history, for example:

```sh
git show 15853b2d04a933cea779897779e621adc8daecc9:research/historical_training/laya_guard_archive_activate.py
git show 15853b2d04a933cea779897779e621adc8daecc9:research/historical_training/tests/test_laya_guard_archive_activate.py
```

This operator extension is intentionally separate from the stage-only source
and immutable evidence in PR #253. The real deployed fix is possible only
after the precommitted 10:05 UTC+8 **original-versus-verified-cache** source
comparison is durably sealed and its sandbox monitor has finished.

## Actual guarded operation

The activation preflight requires all of:

- Original precommitted comparison plan SHA256
  10fea7101627dde050a0b6f6ec1cfa93d7dc746ad0fc57c13b4b7224f7659943
  unchanged, exactly one terminal 10:05:01–10:05:04.5 observation,
  plus two copied unmodified source monitor JSON snapshots whose raw
  SHA256 agree with the immutable comparison receipt. Unhealthy snapshots
  remain scientifically valid observations and are NOT rewritten as READY.
- No residual 25%-CPU cache canary/snapshot process or transient user
  systemd service. The default live source monitor is untouched.
- All 53 original runtime hashes, exactly 51 retained and two replaced
  source files, plus the immutable source_archive.py pin. Original Guard,
  original resident config, new Guard, new resident config and two staged
  exact systemd drop-in SHA256 identities must match the previously
  independently frozen Stage artifact and old unit text.
- Both research units active under their exact OLD effective configuration.
  No admitted/running Laya job, original control status sufficiently fresh,
  no known resident training/fusion/value heavy GPU child, no canonical
  owner or trading permissions.
- Both NEW original admission Guard --check-only executions pass without
  relaxing resource Guard / quote freshness or connecting to the broker.

Only after these hard prerequisites does the operator create two EXACT
new systemd user override files, each with O_EXCL+fsync. It calls one user
daemon-reload and restarts **only** the Laya independent Evaluator and
Trainer, never Execution, Paper driver, crypto driver, original quote source,
FX 10:05 cache monitor, old Trial3 or any model artifact.

If a stage/install/restart/effective unit check fails, the operator attempts
rollback by removing only the two newly created, still SHA-matching files,
performing user daemon-reload and restarting those same two research services
under the already retained old effective unit configuration. Any incomplete
rollback is explicitly marked UNCERTAIN; it is not a success receipt.

No-source-evidence deletion, reward editing, historical trial replay,
auto-promotion, model selection, trade authority or account mutation is
implemented. Even a successful source/runtime repair does not establish
profitable trading, accepted canonical Artifact Owner CURRENT, no missing
historic trials, hardware durability or full longitudinal training health.

## Reproducibility

Source: research/historical_training/laya_guard_archive_activate.py
Focused tests: research/historical_training/tests/test_laya_guard_archive_activate.py

Before activation, run the preflight entrypoint in the isolated worktree:
PYTHONPATH=research:research/neuron_learning:scripts python3 -B -m historical_training.laya_guard_archive_activate --mode preflight

The --mode activate operation must be used only with approved operator
maintenance and these hard gates satisfied, never as an automated timer.
The installation receipt (or rollback status) is kept under the new
private operator-evidence/train-v2-laya-activation-20261013 directory.
Tests mock systemd and never restart real services.

## Actual Pocket4 activation and independently sealed closeout

The separately preregistered 10:05:02 original-vs-cache source-health
experiment produced immutable status snapshots. The *unchanged original*
status had 24/24 USABLE probes but was 86,092ms old and therefore
causally STALE. The SHA-verified 15-second research cache had 24/24
USABLE and was 18,000ms old. The frozen fourth original future FX
decision remains CENSORED by ORIGINAL_READINESS_HOLD_OR_STALE. The
research cache did not replace it and did not create an order signal.

The independent cache service exited naturally with success before
archive Guard cutover. The post-canary preflight checked 51 unchanged
old release pins, both replaced source files and the archived reader,
exact two staged unit overrides, previously unchanged old effective
units, zero admitted/running research candidate jobs and shared GPU
lease free. A first preflight was correctly rejected across the 30min
source rotation because the cached source prefix had not yet observed
the new original segment. The second was correctly denied while the
existing 1Hz Value source-check process was present. The final
preflight used the actual shared GPU lease and original/value Guard
root equality instead of misclassifying a short process with no GPU
lease. Original resource and 5s source freshness thresholds were
never relaxed.

Two exact SHA-pinned create-only systemd user research drop-ins were
installed, user daemon reloaded and ONLY Laya Trainer/Evaluator
restarted. Post-restart real ExecCondition exit codes were BOTH zero
and actual systemd ExecStart/DropInPaths pinned the staged guard.json.
The old unit definitions and every original data source were retained.

Real GPU job after activation:
- The full archive-aware Laya trainer loaded its pinned release and
  executed 16 actual gfx1150 training steps.
- Original COMPLETED count increased 30 -> 31, historical FAILED
  stayed 1. ADMITTED and RUNNING returned to zero.
- New immutable candidate ID:
  1797b31505c498fa320e910a80426bccb083cf1ceeb9da8d7ad54ec9ad692124
- Candidate weights SHA256:
  29e135d201e4e450e698e7e9853279b79562483740a4f6830a45ca6bb38c2acd
- Candidate status: REQUIRES_INDEPENDENT_FUTURE_EVALUATION.
  No auto-cutover, no OWNER, no trading/promotion effect.
- Both earlier retained RESIDENT_SEGMENT_BOUND_NO_HISTORY_DELETION
  runtime errors were finally marked active=false with separately
  retained resolved_at_ms; earlier error bytes/reasons were not erased.
- Independent evaluator still shows 30 rejected old plans and
  promotion_performed=false; no historical failure retroactively passes.

Actual activation receipt:
~/.local/state/heptatrader/operator-evidence/train-v2-laya-activation-20261013/result.json
SHA256 93a4900aec8c25cccae241eded447250bf6d6e2301ba354a8c0373f615c2cc30

Independent full post-activation read-only source/process/weights audit:
~/.local/state/heptatrader/operator-evidence/train-v2-laya-activation-20261013/post-activation-audit.json
SHA256 773e0ec03f82c449585e1a6e6fc2b5c66dd0371c97aca3f08d9f18f0646e73eb

Execution was NOT restarted and remained at cumulative 298 restarts.
PAPER FX/BTC drivers remained active. The original strategy's actual
realized PnL may evolve independently (at last post-cutover read,
-$1,512.8376316525 USD); account_complete=false, so no full-account
all-in return is claimed. This research operation sent zero orders.

This fixes archive-runtime/Supervisor integration and verifies a real
new GPU candidate. It does NOT prove positive trading economics, a
successful independent new PRICE trial, canonical Agentd/Artifact
Owner CURRENT, model activation, or financial net-PnL promotion.


## Exact post-activation regression on final source tree

- Guard activation and cross-process GPU lease adversarial tests:
  12/12 passing. The prior stage-and-unit planner's 17 tests remain present.
- Historical research discovery: **429 tests passing, 7 pre-existing skips**.
- Core Python discovery: **2,487 tests passing, 23 pre-existing skips**.
- Source/build/ownership partition: **106 tests passing**.
- The independent source/weights/process closeout returns PASS with
  31 complete/1 retained failure, 16 applied GPU steps, 30 historic
  evaluator rejects and immutable candidate id.
- New candidate remains REQUIRES_INDEPENDENT_FUTURE_EVALUATION. Later
  Trainer status may correctly become HOLD_NEW_TRAINING_RESOURCE_OR_SOURCE
  on the strict original 5s quote/lease rules, without invalidating
  the previous successful candidate training or admitting new unsafe work.

Full final test transcripts:
~/.local/state/heptatrader/operator-evidence/train-v2-laya-activation-qa-20261013/
