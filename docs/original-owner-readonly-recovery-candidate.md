# Original-owner evidence recovery candidate

Status: current-main candidate, locally compiled and synthetically runtime-tested,
not deployed. It is based on HeptaTrader commit
`ffec655e69c610e0c4af41c00e3565e84edeb940`. It preserves the current deferred
observation scopes, core-test registration extensions, and observation-flush
race fixes. The change does not edit model weights, qualify a trading model, or
claim to recover a real broker outcome.

The separate release-base candidate at
`9682ccade08f936c1ffe48952e3036d06244a684` remains preserved. All validation results
below were repeated with this current-main candidate; no historical test credit
was transferred.

## Problem and scope

The deployed startup rejects an accepted original order with neither a complete
execution nor proven zero-fill cancellation during cash restart catch-up, before
rebuilding the coordinator and starting its callback receiver. A later existing
read-only startup fallback is consequently unreachable for that condition.
Weakening the original cash or order guards would conflate missing evidence
with economic finality. This candidate instead preserves that legacy rejection
and adds a separate, explicitly selected observation path.

## Two independently bounded entry points

1. `hepta_fx_restart_diagnose` is an optional, noninstalled, stdin-only executable.
   It links the production catch-up classifier and OpenSSL hashing, without an
   OMS reader/writer, runtime composition, broker adapter, or service startup.
   It accepts at most 1 MiB, 64 baseline records, and 4096 projected events. Its
   output is an inspection plus input SHA256, never a permit. The caller must
   independently pin the source journal/checkpoint provenance of that projection.
   It cannot retrieve missing broker outcomes.
2. `originalOrderRecoveryReadOnly` is an opt-in runtime mode, default `false`.
   The selector accepts only empty/`0` or `1`, and requires the existing explicit
   user-owned PAPER scope. This document is not a deployment instruction.
   Before any foundation or connection, it sets the existing sticky read-only
   latch. Catch-up inspection runs on a baseline copy; even `Complete` cannot
   advance cash baselines in this mode. Exact coordinator ownership must recover
   before an adapter is created with `readOnly=true` and order submission disabled.

## Classification and required evidence

| Condition | Candidate behavior | Economic or trading authority |
| --- | --- | --- |
| Selector off, accepted order unresolved | Original failure and original reason before connection | None |
| Invalid identity, cross-epoch conflicting record, nonfinite evidence | Invalid; pending list discarded | None |
| Valid pending original identity, coordinator not recovered | Reject before connection | None |
| Original identity recovered, authoritative active snapshot does not bind it | Reject startup | None |
| Exact original active binding, terminal refresh still pending | Permit only the read-only receiver | None |
| Inspection `Complete` with selector on | Keep sticky read-only latch; do not advance baseline | None |
| Late callback arrives for original owner | Retain original ownership and the new observed service epoch in the observation journal | None |
| Reconnect would persist a checkpoint | Internal checkpoint guard rejects; runtime fails closed | None |

`Complete` retains the historical catch-up meaning. It does not assert universal
journal validity, cross-epoch settlement, portfolio reconciliation, predictive
value, or net benefit. Invalid `Filled` and malformed non-cancellation statuses
claiming a pending owner are explicitly rejected; a valid `Filled` status without
execution evidence remains pending.

## Guards and rollback boundary

Risk increase, cancellation, flatten preview/dispatch, and checkpoint writes are
blocked by the existing read-only latch and the new internal checkpoint guard.
The original owner is not retired by a late callback in this mode. No code clears
the latch. Health reports `startup_recovery_read_only=true` and
`mutation_ready=false`; there is no model-promotion interface.

This recovery mode may append observed callbacks to its configured observation
journal when eventually deployed under a separately reviewed procedure. The
current test uses only a new synthetic journal. It never wrote a real account
ledger. Reconnect continuity is not implemented: it fails closed. Do not relabel
new observation epochs as the original acceptance epoch or synthesize a zero-fill
cancellation to unblock cash attestation.

The current patch has not been deployed, so no production rollback is needed.
Discarding this current-main source candidate restores its pinned main source. A future
live procedure must pin the original binary/config/journal/checkpoint, stop on
unbound evidence, and use a reviewed rollback protocol; it cannot be inferred
from the synthetic result.

## Current-main validation receipt

- All 139 pinned source/build files match their fetched exact-commit Git blob SHA and
  SHA256. The build compiles 64 actual production source objects, with the broker
  SDK and production executable main disabled. Compiler dependency output for
  67 translation units bound all 136 actual project source/header inputs to the
  exact main tree or this candidate.
- 35 typed evidence/config/snapshot cases passed, including unchanged legacy
  rejection, partial fill, full fill, proven zero-fill cancellation, conflicting
  execution/status, wrong owner/client/epoch, malformed quantities, and immutable
  input baselines.
- The original current-main native profile suite passed, including cancel and
  flatten progress under concurrent policy operations. Its fake-wrapper operations
  are separate from the four read-only scenarios and are not broker operations.
- Five offline CLI parser/projection cases passed; undefined-symbol inspection
  found no network or runtime symbols in that executable.
- Four full-runtime scenarios passed on Pocket4 at 2026-11-05 17:10:59 UTC as a
  nonroot user, using actual Start/coordinator/adapter/poller/IPC/OMS classes and a
  socket-free fake broker. Private AF_UNIX execution/event IPC was actually used.
  A kernel seccomp filter denied IPv4/IPv6 sockets and subsequent exec calls. The test kept its nonroot gate and used a disarmed
  in-memory kill reader so that an unrelated kill gate could not mask the dedicated
  latch. All fixture credentials and journals were synthetic private files.
- Full runtime result: 3.780258 seconds wall, 0.048283 seconds CPU; the process-tree maximum
  RSS upper bound was 25,284 KiB; 25% CPU/512 MiB process limits; zero place/cancel wrapper calls, zero cash
  checkpoint writes, zero model forwards, zero production changes. Direct field
  comparisons verified that all original cash baseline fields stayed unchanged
  before and after the late callback. The expected
  readiness-timeout log belongs to the negative unbound-snapshot scenario.
- Tested stripped binary SHA256:
  `7663a63b6a4cd3829d0a88291300ed3979140c08028230017d934a47fb09eeda`.
  Unstripped build SHA256:
  `9e8ee58160c3281ce6aed48f0be43edf6c7a99b204628c5e2a7555721277a851`.
- CMake was unavailable in the local build environment. The recorded direct g++
  build passed; CMake target integration still requires ordinary repository CI.
  No SDK-enabled build or live broker recovery was claimed or tested.

## Reproduction

In a complete checkout at the pinned current-main commit, review and apply the patch in a
separate candidate branch. With the broker SDK disabled, build the new evidence
unit target and existing full-runtime test target. Run the full-runtime binary
as an unprivileged user with `--original-pending-readonly-only`, private TMPDIR,
no real credentials/environment, and CPU/memory/time limits. Its own seccomp
filter and nonroot check must remain enabled. The CTest registration includes the
four new cases with a 300-second timeout.

The optional diagnostic CLI target is `hepta_fx_restart_diagnose`. Its five
synthetic stdin tests are in `tests/fx_restart_diagnose_cli_tests.py`; pass the
built executable as the script's only argument. The `HEPTA_FX_DIAGNOSTIC_V1`
projection example in that script documents field order and quoting. Do not
substitute the synthetic fixture for an original ledger.

## External conditions still required

The actual pending original order needs authenticated owner-bound execution or
proven zero-fill terminal evidence. A cross-epoch evidence protocol and independent
cash reconciliation must be reviewed before any baseline advancement. The original
owner receiver must have access to the proper broker session/terminal download;
its availability has not been demonstrated by this test. Frozen market/forward
windows and model-quality/net-benefit evaluation remain separate incomplete gates.
The next safe action is repository review/CI of this candidate. Live deployment,
broker access, and actual economic reconciliation remain separate reviewable work.
