# Native hold recovery with an inactive crypto owner

The mixed user PAPER runtime has an active FX lane and an independently owned
crypto lane. A crypto driver may already be disabled when its last bearer expires
and the Supervisor removes that lease. The old native owner fence remains. In
that state, requiring discovery through the deleted bearer prevents the active
FX lane from releasing an independent native startup hold. Provisioning another
24-hour bearer without a running custodian only postpones the same problem.

The explicit `paper-on --resume-native-hold` path can now establish
`INACTIVE_FENCED_ZERO_NO_LEASE` for that exact disabled crypto owner. It preserves
the crypto driver, its original state and token file, and its native owner fence.
It creates no lease, performs no resume or renewal, and sends no order. The
ordinary owner-token checks still apply to an active crypto driver. An operator
release without the new, reviewed runtime binding also keeps the old path.

## Current evidence, before and after native release

The existing operator-maintenance lock covers the local transaction. The helper
requires the loaded crypto unit to be `loaded`, `inactive`, `dead`, `disabled`,
PID 0 and `RefuseManualStop=yes`, with no pending manager job. It binds the loaded
definition, fragment and drop-in files, original driver-state bytes and original
token-file metadata. A nonzero local position, pending command or uncertain lease
blocks the path; these are local consistency checks, not Broker zero proofs.

The current Gateway and Execution processes must agree on the configured crypto
agent, session, account and PAPER scope. Configuration comes from those running
processes, rather than the caller's environment. The helper binds manager
invocations, process start ticks and executable identities. The native client's
connections additionally check the exact Execution peer PID and start ticks,
and all native replies must share one service epoch and fencing generation.

`hepta-sessionctl inactive-owner-audit` reads the existing encrypted catalog with
the ordinary store reader. Any record for the exact owner rejects the proof,
including recovery, predecessor or finalization-bearing records; expiry is not
a reason to ignore a record. The complete catalog is sampled again after the
native reads and must have unchanged identity and bytes. The store's shared
cleanup lock does **not** serialize ordinary provisioning or renewal, and the
operator lock does **not** claim to lock the Supervisor. Safety depends on the
separate native crypto fence remaining installed. A concurrent explicit owner
resume, catalog change, service restart or driver-policy change invalidates the
observations and causes the existing recovery path to retain or reengage the
native startup hold.

The `held` phase performs the existing owner audit, requiring the exact crypto
scope's authoritative zero result, complete Broker recovery barrier, no owner
commands or orders, and no global active orders. Its native health, order and
position reads prove the current crypto fence and native startup hold. The FX
owner retains its original health/orders/positions checks. A known retained FX
position is not relabeled as zero.

The existing audit can persist its normal OMS reconciliation evidence. Its
`audit_performed` field records that audit; the zero mutation counters describe
lease, fence and order authority, not an absence of all internal persistence.

After unlinking the independent startup marker, the `released` phase repeats the
configured crypto proof and requires its native owner fence to remain installed
while the independent new-risk guard is released. This phase does not repeat the
global zero-order recovery audit: a valid FX request can begin immediately after
the account guard lifts and invalidate that audit's recovery barrier. The proof
instead requires complete current owner-order and explicit crypto-position
evidence, with no unattributed or uncertain work. The active FX postcheck retains
its original behavior and can report legitimate in-flight work. This distinction
does not relax an owner fence, submit permission, or finalization condition.

Any failure after unlink uses the existing native-hold reengagement path. A
precheck receipt, previous process epoch or historical `session_owner_fenced`
event cannot substitute for either current phase. `--check` leaves the native
hold installed.

## Explicit zero positions and durable native fences

The ordinary portfolio array omits zero positions. Missing an entry is therefore
insufficient evidence for an inactive owner's zero position. The existing
`portfolio.list_positions` operation additionally returns
`configured_crypto_position_observation` for the mixed user PAPER profile. This
read-only observation binds the configured physical crypto contract, account,
connection and position generations, and complete active/terminal/risk evidence.
An explicit BTC quantity of zero is distinct from a missing, stale or incomplete
observation. The existing settlement observer retains its original stricter
contract. No new wire operation or trading capability is added.

The runtime binding also covers the existing synchronous critical-OMS contract:
owner-fence events are synchronously persisted and a failed append blocks native
mutation; replay restores the fence. Current native `owner_fenced=true` is
accepted only with `mutation_blocked=false`, the reviewed runtime image and
complete current native evidence. The historical journal event alone is not a
release permission, and an ordinary owner fence is not whole-account terminal
finalization.

## Deployment binding and entry points

The prepared operator `MANIFEST.json` may contain `inactive_owner_runtime` with
schema `heptatrader.inactive-fenced-owner-runtime.v1`. It binds:

- `execution_binary`: the actual Execution image path and SHA256;
- `build_receipt`: the existing build evidence path and SHA256;
- `source_commit` and `source_leaf_sha256`: the reviewed source and compiled
  inputs for the OMS, owner replay, user-PAPER audit, native health and explicit
  crypto-position contracts.

Preparation must compare those source leaves with the inputs used to build the
selected image. A release label or an unverified whole-tree claim is insufficient.
The helper verifies the receipt bytes and matches the actual process image to
the selected executable on every proof. An older runtime without the explicit
crypto observation cannot satisfy this path. Existing active-lane deployments do
not need this optional binding.

The native diagnostic entry is:

```text
heptatrader session inactive-owner-audit --owner crypto --phase held|released \
  --execution-pid PID --execution-start-ticks TICKS
```

It accepts no token, session, account, domain or TTL override. Normal native-hold
recovery obtains its process/configuration bindings automatically through
`hepta_maintenance_latch.py` and the pinned `hepta_inactive_owner.py`; callers
normally use the existing `paper-on --resume-native-hold [--check]` command.
This diagnostic does not provide authority to stop or start a service.

Implementations are in `scripts/hepta_inactive_owner.py`,
`scripts/hepta_maintenance_latch.py`, `scripts/hepta_operator_clients.py`, and
`HeptaTrade/cli/hepta_sessionctl_inactive_owner_audit.cpp`. The native client
reuses the existing Execution read/control operations and catalog reader. Native
socket fixtures and private-directory Python tests cover stale identity,
catalog changes, nonzero/incomplete crypto evidence, preserved original state,
phase boundaries, and the unchanged active-owner path.
