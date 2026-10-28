# Native Execution replacement within the existing maintenance transaction

Native Execution replacement is currently unavailable. A plan containing
`native_execution` is refused during preflight with
`NATIVE_EXECUTION_STOP_AUTHORITY_UNSUPPORTED`, before any controller or Gateway
is stopped. The current runtime reports no maintenance-stop permission and no
complete external/manual writer universe. Its owner-zero observations, native
kill marker, operator hold and authenticated Gateway catalog do not supply that
missing account-wide stop authority. A plan or checkpoint boolean cannot grant
it. The default Gateway-only path is unchanged.

The retained artifact fields describe a prepared binary, not activation
permission:

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
build/test exits. This is artifact evidence, not a stop permit.
Use an immutable extracted release for the binary, not a mutable build target.

Preflight performs no native control RPC. Owner audits can reconcile OMS and
request normal Broker recovery; they are not pure reads. Their per-owner zero
and observed Broker-global zero values do not exclude another account writer
or prove that independent risk protection survives stopping Execution.

All new stop-policy openings, stop submissions and resubmissions, and fallback
selection that would require another stop are refused, including when resuming
an old checkpoint. Execution's two socket activators cannot be stopped merely
because the checkpoint recorded a previous intent.

Existing recovery obligations remain: a previously submitted stop may still be
observed without issuing another one; a completely stopped group may use its
retained controlled start; an already accepted native phase may continue the
original Gateway/owner recovery. Protection and preference restoration retain
their original checks. Forced killing stays disabled while a stop is pending.
No timeout is treated as a successful drain. Retained start acceptance
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

`--native-fallback` is unavailable because its retained path requires another
group stop. A failed or unidentified candidate is never forced down. Until a
native image is accepted, Gateway and controllers remain held, and automatic
forced shutdown is not restored. Original stop policies are restored only after
the existing verified recovery completes.

The independent native risk marker remains engaged after success or rollback.
The source change does not prove production continuity: actual original-window
completion, fixed-package activation, Broker recovery and subsequent owner
observations must be recorded separately on Pocket4.
