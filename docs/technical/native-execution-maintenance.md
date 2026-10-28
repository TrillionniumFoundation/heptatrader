# Native Execution replacement within the existing maintenance transaction

The prepared Gateway controller can optionally replace the native Execution
binary while its original owners and Gateway are already held and stopped.
The default Gateway-only path is unchanged. This does not create a second
owner store, a reusable stop permit, or a new Broker connection alongside the
original Execution owner.

Add a `native_execution` member to the existing, hash-pinned Gateway plan:

```json
{
  "native_execution": {
    "source_sha": "REVIEWED_40_CHARACTER_SOURCE_COMMIT",
    "binary": {"path": "/absolute/immutable/bin/hepta-ib-executiond", "sha256": "BINARY_SHA256"},
    "validation_receipt": {"path": "/absolute/actual-sdk/final-result.json", "sha256": "RECEIPT_SHA256"}
  }
}
```

The existing SDK acceptance receipt must identify the same source and binary,
have status `ACTUAL_SDK_BUILD_AND_OFFLINE_EXITS_ACCEPTED`, and retain successful
build/test exits. This is artifact evidence; activation additionally consumes
fresh original-owner audits under the existing operator lock and native hold.
Use an immutable extracted release for the binary, not a mutable build target.

Preflight still performs no native control RPC. Activation performs the
existing owner RecoveryQuery and normal controller/Gateway drain first. It
checks the final authenticated catalog, then obtains fresh, complete, same-epoch
whole-account zero observations from the original owner audits. These audits
can reconcile OMS and request normal Broker recovery; they are not pure reads.
An active fixed forward-evaluation window must finish before this transaction
can interrupt its original quote source.

Execution has two socket activators. The controller therefore stops the service
and both original sockets in one systemd transaction. Stopping the service alone
could allow a queued connection to restart the former image during replacement.
Forced killing is disabled. A pending stop retains that policy and is observed
on resume; it is never converted into a successful drain by a timeout.

The candidate starts with the same configuration and native hold. Acceptance
requires a distinct native service epoch, the exact running image, unchanged
Broker process/listener, and new complete zero-owner audits. Only then does the
existing Gateway recovery path resume the original owners and previously active
controller. An originally disabled controller stays disabled. No order, cancel,
flatten, native hold release or account fence release is sent by the new phase.

The same private Gateway checkpoint records the native phase. Lost stop/start
submission replies are reconciled against current jobs and process identity;
the reviewed release bytes can be adopted after a rename whose later persistence
reply was lost. An unknown process or another writer's third file version is
never stopped or overwritten.

If candidate startup fails without a remaining process or systemd job, resume
with the existing `--activate --resume` flags plus `--native-fallback`. This
selects the exact former binary retained in the same checkpoint. A live or
unidentified candidate cannot be forced down through this option. Until a native
image is accepted, Gateway and controllers remain held, and automatic forced
shutdown is not restored. Original stop policies are restored after the normal
owner recovery completes.

The independent native risk marker remains engaged after success or rollback.
The source change does not prove production continuity: actual original-window
completion, fixed-package activation, Broker recovery and subsequent owner
observations must be recorded separately on Pocket4.
