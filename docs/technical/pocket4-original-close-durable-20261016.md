# Pocket4 original CLOSE attribution: immutable local retention

**Source-only, 2026-10-16.** This is an additive extension of the existing
driver close-trigger evidence. It changes neither order type, source of trading
authority, model outputs, thresholds, quote cadence, quantity, native Execution
protocol nor the independent stop shield.

## Root problem and scope

The installed driver (and the recently merged initial CLOSE-attribution source)
holds at most 64 latest `state["close_command_attribution"]` entries. Although
these entries bind original HSR1 command IDs to accepted Broker execution IDs
and driver-observed causes, a long-running paper account would otherwise lose
older entries by routine state rotation. Future training/PnL attribution must
not turn old mutable `last_exit_reason` into presumed original intent.

On a **future newly qualified real CLOSE only**, after existing authoritative
command/position/terminal economic-fill reconciliation, the driver:

1. Creates an owner-private `~/.local/state/heptatrader/<paper-driver|crypto-driver>/close-command-receipts/` directory (0700), and a new SHA256(command-ID)-named `.json` record (0600), using `O_CREAT|O_EXCL|O_NOFOLLOW`. Names never contain unsanitized command strings.
2. Writes the already-bound original command, original trigger and Broker
   execution identity record once, then fsyncs both file and directory.
3. On a repeated replay, accepts an existing **strictly matching** receipt,
   ignoring only a fresh observational `recorded_at_ms`. Any other difference
   is an immutable-evidence conflict, not permission to overwrite.
4. On disk errors, symlinks, unexpected owner/mode, corrupted originals, or
   conflicting data, records a bounded diagnostic error without preventing an
   already independently broker-confirmed CLOSE or modifying its settlement.
   A partial failed write remains retained as failure evidence and is not
   falsely reported as committed.
5. Preserves the existing 64-entry state window for compatibility. The
   separate append-only per-command directory retains older actual future
   records for subsequent exact OMS/PnL joining.

The local files are **not Broker-signed stop proofs**, are mutable by the
machine owner, and do not demonstrate a retroactive cause for the previous
361 unclassified closes. An independent auditor must additionally match
accounting period, original command, all Broker execution IDs, Broker order,
position, amount and economic details. If any evidence is missing or changed,
the cause remains UNKNOWN. This output is never used as action labels or
trading authority without a newly frozen, genuinely prospective protocol.

## On-host verification

- Scoped Python tests use only disposable directories and synthetic fills.
- Verify private file and directory modes, command-hash naming, durable repeat
  idempotence, conflict refusal, malformed metadata, symlink refusal,
  truncated existing file preservation, and **70 separate receipts** coexisting
  with the normal 64-entry state cap.
- A deliberately injected receipt-write failure during a synthetic
  Broker-confirmed CLOSE still leaves the original flat position, original
  realized PnL, single close count and no pending command.
- The resident Pocket4 service and prior user worktree remain untouched.
  The existing `RefuseManualStop=yes` shield prevents an ordinary stop/restart.
  This is NOT a maintenance unlock, a Broker protective stop or a deployment.
- Pocket4 focused regression after source pin updates: **150 tests passed**;
  separate cost-aware native adapter regressions: **13 tests passed**.
- Full Pocket4 core lane: **3,803 tests passed**, no skips.
  Full source/build-contract lane: **116 tests passed**, no skips.
  `scripts/check_documentation.py` and Git diff whitespace checks passed.
- Test environment must use a **short, executable, private** `TMPDIR`;
  the initial long nested test path caused UNIX socket path limits and
  directory-ownership fixture rejections. Neither failure is concealed:
  logs are retained in
  `~/.local/state/heptatrader/operator-evidence/close-durable-20261016/core.log`
  and `close-durable-verified-20261016/core-shortpath.log`.
  Both BTC and cost-aware research adapters retain exact new base SHA256
  identity checks; they are NOT live-release updates.
- Exact owner-private FX wrapper candidate was staged and `load_manifest()`
  passed without an IB connection. Driver SHA256
  `af0762b0ec8cf6bade99f410fb8de1b5a34d96be0b6bffd0c64a39fc34cea2c1`;
  candidate manifest SHA256
  `638d9476d232eb7ba9d71e49b1a1a29b70dcd15c53470519875a1adfac85e9e9`.
  Candidate path:
  `~/.local/share/heptatrader/decision-attribution/candidates/fx-close-cause-durable-af0762-v1/EUR.USD/`.
- Existing running FX/BTC services still point to their prior immutable
  releases and are guarded with `RefuseManualStop=yes`. No active process
  was restarted, no Broker orders/cancels or model promotion were requested.

## Future controlled rollout requirements

Only after the Execution owner can atomically block new entries, verify fresh
same-account flat/no-active/no-pending/unknown orders and preserve the genuine
stop-request protection across a controlled rollback, may the original
attributed wrapper be rebuilt into a private SHA-pinned release and rolled out.
Do not replace the currently running release or weaken `RefuseManualStop` to
manufacture a new observation. Begin measuring genuine new exit-reason coverage
only after an actual original PAPER CLOSE under the new runtime.
