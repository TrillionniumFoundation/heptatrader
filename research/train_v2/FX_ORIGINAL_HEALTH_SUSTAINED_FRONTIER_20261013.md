# FX source-health sustained 480-cycle study — independent first-32 frontier

**2026-10-13**. Status: **FIRST 32 CYCLES VERIFIED; FULL 480 HOLD**.

The old 2026-10-13 four-window forward price-only trial naturally exited:
4/4 original entry decisions were `CENSORED / ORIGINAL_READINESS_HOLD_OR_STALE`,
with all sixteen predeclared future 15/30/60/120-minute results also
`CENSORED / NO_ELIGIBLE_ORIGINAL_PREACTION`. All 16 attempts were
written and the finite service exited normally. **Zero eligible price
outcomes or broker orders.** Neither the old failed windows nor those
original files were changed or relabeled here.

Original completed forward-study SHA256:
`857914d7222cc6e85058d9a19c3bf8765690e172a91a4de9cd2d22ebecc3890a`.

## Root cause and already-running prospective correction study

The installed read-only source-health monitor sleeps 60 seconds after
its calculation. Assessment/publication can consume an additional
~8–16 seconds, exceeding the separate 30-second health snapshot age
budget at an arbitrary decision instant, even when the underlying
quote is fresh. A separately isolated faster assessment feasibility
test is not proof of sustained acceptance.

A **480-cycle, 15-second, two-hour bounded source-health observation**
was separately preregistered and physically running on pocket4 when
this audit began. It uses the same original IB PAPER read-only quote
source, original 24 probes and their unchanged READY/HOLD thresholds.
Its source and period are pinned to:
- Original complete frozen plan SHA256:
  `490375e6cfc4cea69b0aeb8ece2bd836228d1b3d086793555ed35d920bc8f6be`.
- Original worker start SHA256:
  `3b880a8bd59b515b128a5566e713767ac822862d695f1f8c4237bb018fbc0d59`.
- Original sandbox receipt SHA256:
  `b18abaf07255b4183f96dcdad8ed5059cbbd8e2f82616c81cc5597fef513acaa`.

**Do not start a second producer, stop this transient service, change
the original installed health service, or declare 480/480 passed.**
The fixed 480-cycle producer independently records each source snapshot
and an append-only receipt hash chain. It has no IB broker request,
order or automatic training/promotion interface.

## New incremental verifier

`research/train_v2/fx_original_health_sustained_frontier.py`
checks an **exact contiguous 32-cycle prefix**, against the untouched
480-cycle frozen calendar:

- Absolute, private owner-only source paths; only write-once 0600
  output, never mutate the producer or its original JSONL/source files.
- Frozen PAPER period, 480-cycle target, immutable 15s targets,
  unchanged 20-of-24 readiness/5s IB quote rules, original sandbox
  Landlock/seccomp/socket-denial flags and no trading authority.
- Every original receipt, original byte SHA256, predecessor receipt
  hash and exact selected receipt status.
- Exact 24 probe count reconciliation and source-prefix metadata SHA.
- Per-cycle assessment start time, original observation time,
  publication time and quote freshness.
- Compute the worst observed state age at publication and the
  worst age immediately before the next observed 15-second publication.
- Never count any of the other **448 expected future cycles** as
  completed. When the producer has ended, use the separate complete
  480-cycle verifier and immutable `final.json`, not this prefix tool.

Actual pocket4 first-32 result:
- **32/32** captured, with **32 READY / 0 HOLD**.
- Maximum publication age **11,385ms**, maximum time until next
  published observation **26,384ms**, below 30,000ms on this prefix.
- Exact original first-32 receipt SHA chain head:
  `3933a569e518a632830ce711c3e9329883901111535274fcf8c28bcba5505d84`.

Private pocket4 write-once receipt:
`~/.local/state/heptatrader/operator-evidence/fx-original-health-frontier-20261013/original-first32-frontier-v1.json`
SHA256 `46f45a5dd739c0d9a61b7d94d9aa82977a091c1d1022162cf1f2380883cd6663`.

## Physical qian-qi independent offline reparse

A physical **qian-qi** host used SSH solely to copy the original
read-only **first 32** plan, worker-start, sandbox, status and receipt
bytes into a private 0600 archive (without broker credentials/orders).
Archive SHA256:
`f8d5ab1b81f9fff67f471443e48cd5e528cd373dbff4b79e3564f13256a0f586`.

An independent, **standard-library-only** implementation in
`research/train_v2/fx_original_health_portable_prefix_verify.py`
parses the original archive without extraction, rejects tar path escape,
duplicate members and symlinks, recomputes all original byte hashes and
SHA-256 receipt chain, and independently recalculates the 24-probe and
15-second timing statistics. It **hard-pins the original study and
Pocket4 first-32 audit SHA256 values**. The caller cannot supply a
replacement hash as a production acceptance argument.

The physical qian-qi verifier independently reproduced **32 READY,
0 HOLD, 11.385s worst published age and 26.384s worst before next
publication**.

Qian receipt (user-owned, not published):
`~/.local/state/heptatrader-fx-sustained-32-crosshost-20261013/qian-crosshost-first32-v2.json`

SHA256:
`fa6592c0fea588a8deb93418882f6c69c9de44e5fece6701a30920d2f040cd00`

Portable verifier SHA256:
`1d4a02a8ed6931c60ca93e5cb28eb099a962f5123d581433a158fa42004a6ce8`

**This is a second physical host independently reparsing a single
original capture, NOT independently broker-signed quotes, independent
Root/Owner/LedgerWriter/Witness or an independent IB subscription.**

## Next release gates

1. Let the existing two-hour study terminate naturally and inspect
   **all 480** original full byte snapshots, immutable chain and final
   counters. A missing/censored/late cycle must remain failed.
2. Check actual 30-second publication age at **every** required
   decision under ordinary production CPU contention and transient
   failures, and exercise cold start and sustained service isolation.
   Do not accept a prefix as complete.
3. Only after complete evidence and safe rollback, consider a
   controlled *monitor-only* cadence change; preserve the installed
   trade gateway, model, source quota, original quote TTL and
   production PAPER Driver.
4. Original action-behavior propensities, true broker execution and
   financing costs, account Flex/Activity and NAV, product-native
   owner/witness signatures and model OOS positive economics remain
   independently blocked.

**Release state: HOLD for production monitor cutover and for any new
model/trading promotion. No live services or orders were changed.**

## Additional later pocket4 verification (not cross-host)

While the same 480-cycle producer remained active, the original-data verifier also checked the continuous **first 96 cycles**: 96 READY, zero HOLD, worst publication age 11,385ms, and worst age before next publication 26,384ms. The remaining 384 cycles were not validated. Only the first 32 were separately reparsed on qian-qi.

Write-once result: `fx-original-health-frontier-20261013/original-first96-frontier-v1.json`; SHA256 `a0eb3393c437f80a0d5d89d530cba6f7488bc0fce3384e35bd3fe7e44032c748`. No new producer was started.
