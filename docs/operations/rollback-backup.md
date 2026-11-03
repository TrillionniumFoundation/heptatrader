# Rollback, backup, and restore

Status: CURRENT  
Applies to: persistent runtime state and immutable deployment artifacts

## Back up

Retain, with restricted access and integrity digests:

- exact source and binary artifact identity;
- OMS journal and durable command/fence state;
- session lease store and schema version;
- Gateway audit file **and all** of its `.segments/` predecessors;
- terminal recovery witnesses;
- non-secret profile and policy files;
- configuration and installed-file manifests;
- qualification receipts and broker evidence where applicable.

Do not back up live token or broker credentials into ordinary artifact bundles. Use the deployment secret-management system.

## Rollback preconditions

A binary rollback is allowed only when the older binary can read the current journal, lease, protocol, and configuration schemas. If compatibility is not explicitly tested, keep mutation closed and perform a reviewed migration or forward fix.

## Rollback procedure

1. engage the kill switch and stop new risk;
2. fence/revoke sessions and reconcile unresolved commands;
3. stop Gateway then Execution;
4. snapshot persistent state without modifying it;
5. install the prior immutable artifact and matching non-secret configuration;
6. run schema and ownership validation;
7. replay and reconcile under recovery-only mode;
8. run core and deployment smoke tests;
9. restore bounded authority only after review/qualification.

## Restore failure

A missing, corrupt, incompatible, or partially restored journal/lease store is a fail-closed condition. Never synthesize an empty state to make startup pass when a broker account may still contain positions or orders.

## Compressed OMS downgrade

See [lossless stopped-state maintenance](../technical/oms-archive-lifecycle.md) for optional gzip storage,
writer exclusion, decoded recovery budgets, crash handling and explicit
expansion before downgrade. It preserves all event bytes and command identities;
it is not online truncation or a general N-1 compatibility claim.

## Audit maintenance

After fencing/reconciliation, stop Gateway before running the installed
`hepta-sessionctl --audit-seal-stopped /absolute/audit-path` as its audit-file
owner. Run `hepta-sessionctl --audit-verify /absolute/audit-path` before restart.
Sealing retains every old byte, reserves no new trading authority and never
truncates a live descriptor. Preserve orphan staging files after an interrupted
operation for investigation; do not restore a prior active file over a newer
anchor. See the [audit persistence contract](../modules/tool-gateway.md#state-and-persistence).

HJA3 segmentation requires the current verifier. Old binaries accepting the
anchor as arbitrary legacy text do not verify the retained predecessor chain;
that is not supported downgrade evidence.

### Gateway-only maintenance plan

`scripts/activate_prepared_gateway.py` supports an optional `audit_seal` step in
its existing Gateway maintenance transaction. Omit the field to retain the
existing behavior. The step requires the original hold, fenced owners, stopped
controllers, an inactive Gateway with no queued job, and disappearance of the
original Gateway process. It does not authorize stopping Broker or Execution,
changing the PAPER producer, or extending the original recovery deadline. A plan
with `native_execution` cannot also request audit sealing.

Use a new reviewed plan and the existing hold workflow. A completed plan remains
immutable; repeating it returns `ALREADY_COMPLETE`, and a new plan uses the
controller's existing completed-checkpoint archive. Never edit a checkpoint or
deliberately interrupt a deployment to obtain an offline sealing window.

#### Qualify the actual readers before planning maintenance

Keep the original immutable release manifests and
`heptatrader.gateway-recovery-source-evidence.v2` build receipt unchanged. The
recovery receipt binds the original source, binary and recovery tests, but does
not by itself establish HJA3 reader compatibility. Create a separate private
qualification input artifact containing these pins:

```json
{
  "schema": "heptatrader.gateway-audit-reader-qualification-inputs.v1",
  "candidate": {"manifest": "/absolute/current-release.json", "sha256": "MANIFEST_SHA256"},
  "fallback": {"manifest": "/absolute/current-release.json", "sha256": "MANIFEST_SHA256"},
  "recovery_evidence": {"path": "/absolute/original-recovery-evidence.json", "sha256": "EVIDENCE_SHA256"},
  "sessionctl": {"path": "/absolute/hepta-sessionctl", "sha256": "SESSIONCTL_SHA256"},
  "simulator": {"path": "/absolute/hepta-executiond", "sha256": "SIMULATOR_SHA256"}
}
```

The capitalized digest values above are placeholders. Hash the actual reviewed
bytes and pin the input artifact itself. For audit-only maintenance, candidate
and fallback can both reference the same current Gateway manifest and binary;
this avoids introducing a different reader during recovery. Distinct binaries
must each qualify. Each selected source/binary pair must already appear in the
unchanged recovery build receipt; a new logical fallback may reuse its original
candidate. An untested capability flag does not qualify a reader.

Run the explicit fixture entry on the authorized host as UID/GID 1000, with a
new output path in a private directory:

```sh
python3 tests/python/test_gateway_audit_qualification.py \
  --inputs /absolute/audit-reader-inputs.json \
  --inputs-sha256 INPUT_SHA256 \
  --output /absolute/audit-reader-qualification.json
```

This entry runs the pinned binaries with private sockets, a private synthetic
audit directory, cleared inherited environment, a SIMULATOR Execution daemon,
and a WATCH-only Gateway. It does not use a production socket, production
service, owner or persistent lease store. The legitimate no-persistent-lease
profile exercises the same audit reader while avoiding the production lease
cleanup interlock; the original build receipt remains the evidence for durable
lease recovery. Ordinary unit-test discovery runs only the Python validators.

For every distinct Gateway binary the fixture uses the pinned sessionctl to
seal a nonempty synthetic legacy file into a real HJA3 anchor. It requires a
successful Gateway startup and clean shutdown with the intact predecessor. It
then independently removes and corrupts that predecessor: native verification
must reject with `SUPERVISOR_AUDIT_SEGMENT_INVALID`, and Gateway startup must exit
7 with `SUPERVISOR_AUDIT_INIT_FAILED:SUPERVISOR_AUDIT_SEGMENT_INVALID`. An unrelated
startup error does not pass. The new receipt binds original manifest/build pins,
actual binary identities, input and fixture-tool hashes, exact outcomes, command
log hashes and completed child cleanup. Review the receipt and retained logs;
only `PASS` is eligible. The fixture never overwrites an existing receipt.

#### Add the opt-in step and use the existing transaction

Add the following fragment to the new plan, retaining all existing plan fields
and controls. The path must be the canonical, private journal configured in the
original Gateway environment. The qualification's candidate, fallback and
sessionctl pins must exactly match this plan:

```json
{
  "audit_seal": {
    "path": "/absolute/configured/session-audit.jsonl",
    "qualification": {
      "path": "/absolute/audit-reader-qualification.json",
      "sha256": "QUALIFICATION_SHA256"
    }
  }
}
```

The existing preflight reads qualification/build evidence and journal metadata;
it does not hash or verify the live journal. Use the original CLI entry points:

```sh
python3 scripts/activate_prepared_gateway.py --plan /absolute/new-plan.json --plan-sha256 PLAN_SHA256
python3 scripts/activate_prepared_gateway.py --plan /absolute/new-plan.json --plan-sha256 PLAN_SHA256 --activate
```

The first command performs the existing preflight without control actions. The
second performs the reviewed maintenance transaction. A `HELD` outcome requires
inspection of the original checkpoint and then an explicit same-plan resume,
without replacing its hold, owner set, evidence or deadline:

```sh
python3 scripts/activate_prepared_gateway.py --plan /absolute/new-plan.json --plan-sha256 PLAN_SHA256 --activate --resume
```

After the original writer has stopped, the controller captures the final active
file's device/inode, ownership, permissions, size, timestamps, parent-directory
identity and complete byte SHA256 `H`. It durably records an immutable sealing
intent before invoking the pinned offline CLI. For a nonempty file that is not
already a pure anchor, the intent leaves the cumulative record count `N`
undetermined. Native sealing verifies the original chain before publishing;
the resulting canonical `HJA3\tH\tN\n` must name the captured complete byte hash.
The final native verifier must confirm that exact cumulative `N` and retained
chain before `N` enters the verified receipt. The original intent is not changed
or backfilled. A nonempty legacy file with zero HJA2 records still preserves all
of its bytes in a segment.

Interrupted outcomes are reconciled only while the same writer remains stopped:

| Observed state | Meaning and permitted continuation |
| --- | --- |
| Exact original active file remains | No active replacement is observed. A segment or staging file may already exist; preserve it and resume the original native operation under the same intent. |
| Expected HJA3 anchor and retained predecessor exist | Publication is visible, but durability is unconfirmed. Invoke the native sealer again; its pure-anchor branch completes the parent-directory fsync, then verify the complete retained chain. |
| Any other identity, byte, count or history mismatch | Keep the transaction `HELD` and both Gateway startup paths closed. Preserve original files and evidence. |

An empty file and a pre-existing pure anchor also require a successful native
sealer invocation; they are not permission to skip its validation and directory
fsync. A readable anchor or successful verification alone does not prove that a
lost sealing reply completed the final fsync. The original CLI's exact success,
post-verification, stable identities and a successfully saved receipt are all
required before a new writer is requested. A failed receipt save leaves the
operation unknown in memory as well, so exception fallback cannot restart the
Gateway. If a saved receipt is visible after an interrupted checkpoint fsync,
the subsequent writer-start intent must itself save successfully before start.

Both the normal candidate startup and compatible fallback startup use this
gate. Once a writer-start intent has been saved, normal Gateway appends are
allowed; resume validates the recorded receipt and existing release/process
identity instead of sealing an active writer or requiring its journal to remain
a bare anchor. Successful maintenance follows the original owner/controller
recovery and retains the native hold for the existing explicit release workflow.

The operation performs one Python scan to bind `H`, the native sealer's required
validation/copy, and one final native full-chain verification. It avoids a
redundant pre-count verification and a second Python scan of the retained
segment. Same-plan recovery may need to read original bytes again to establish
which publication state exists. Measure elapsed time and free-copy space before
planning near a recovery deadline; no step extends that deadline. Each native
offline invocation is limited to five minutes and the remaining maintenance
budget.

Sealing restores room in the active append file. It **does not reclaim disk
space**, delete history, or remove the cost of validating retained history. Keep
the complete `.segments/` chain, qualification/build evidence, checkpoint
receipts and interrupted staging files under the existing restricted backup
policy.
