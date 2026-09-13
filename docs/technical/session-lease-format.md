# Session lease protocol, persistence and migration

Status: CURRENT
Applies to: Session Supervisor and its operator-only Unix control path

## APIs and trust

`session_supervisor_protocol.h` defines `SessionSupervisorRequest` and
`SessionSupervisorResult`; its `.cpp` owns numeric field IDs and operation
validation. The wire magic is `HSS1`, followed by uint16/uint32 big-endian TLVs.
It is a different protocol from Agent `HTT1` and Execution `HEX1`. The
`unix_session_supervisor_server` authenticates the operator peer before an
operation reaches the lease store. Agent token possession alone does not grant
access to this control plane.

| Operation family | Key request bindings | Result interpretation |
|---|---|---|
| Provision | template, token, Agent/session identity, peer UID, TTL | accepted plus durable generation; not Broker qualification |
| Renew / Rotate | existing token, expected generation, TTL; replacement token for rotation | stale generation cannot extend authority |
| Revoke | existing token and operation-specific scope | durable fencing/cleanup, not assumed terminal Broker state |
| RecoveryQuery | token, target command ID and recovery context | authoritative status and owner-audit completeness are separate fields |
| PaperFinalize / PaperFinalizeAck | recovery/finalization IDs, exact owner-set hash/count, receipt bindings | monotonic tombstone/ack transitions |
| PaperTerminalizeAck / PaperTerminalWitnessPrepare / PaperTerminalWitnessAck | exact current terminal evidence and owner/service identity | terminal proof cannot be inferred from an earlier audit alone |

Consult the serializer for the exact required/forbidden fields of each
operation. Result booleans such as `accepted`, `ownerAuditAuthoritative`,
`ownerAuditComplete`, `terminalLatchDurable` and `terminalCurrentEvidenceVerified`
are not interchangeable success flags. Diagnostic strings are stored through
the named accessors on `SessionSupervisorResult`; array position is not a wire
contract for other clients.

## Durable record identity

A lease binds template/issuer, secret token, Agent/session identity, exact owner
account/execution domain, peer UID, expiry and generation. Rotation tracks a
predecessor token/generation. Fencing and recovery have explicit pending,
complete and recovery-only fields. External PAPER finalization additionally
binds recovery/finalization IDs, expected owner-set hash/count and receipt hash.
A policy edit after restart must not substitute a different account/domain for
the durable owner.

The finalization enum is exactly `None=0`, `FencePending=1`, `FenceComplete=2`,
`AuditSealed=3`. Generic `Replace` does not advance PAPER tombstones. Dedicated
`AdvancePaperFinalization`, `SealPaperFinalizationGroup` and
`AcknowledgeAndPurgePaperFinalizationGroup` enforce one-way transitions and
persist an acknowledgement ledger. A tombstone or ledger row can never be
provisioned as a live lease.

## Version table

| Layer/version | Current handling |
|---|---|
| Outer encrypted HSL2 envelope | nonce, tag and ciphertext encoded on separate lines; nonce 12 bytes, tag 16 bytes; bounded encrypted store |
| Outer unencrypted HSL1 | rejected by the encrypted-envelope loader |
| Plaintext HSL1–HSL6 | explicitly parsed historical layouts with version-specific presence/owner checks; not generic permission to recreate authority |
| Pre-owner PAPER HSL4/HSL5 | special cleanup-only migration requires matching fixed identity, trusted source/key/lock, expiry and no predecessor/fence/recovery state |
| Plaintext HSL7 | tagged lease/tombstone records; obsolete HSL7 acknowledgement rows are rejected |
| Plaintext HSL8 | current writer: `R` lease/tombstone rows and `A` acknowledgement rows binding preliminary audit and Execution terminal receipt |

`SerializePlaintext` in `session_supervisor_lease_store.cpp` is the exact field
ordering reference. New writers must not reuse an older tag for a changed
layout. Unknown/malformed versions fail closed. The store is bounded to 2 MiB;
key input is bounded to 65 bytes. Encryption does not remove file identity,
permissions, durable replacement or rollback requirements.

## Failure and restart matrix

| Interruption | Required recovery behavior |
|---|---|
| Before durable lease commit | do not publish a usable token or successful generation |
| Durable generation exists but token publication is incomplete | inspect durable generation; never guess a more permissive state from the token |
| Fencing RPC is unavailable or ambiguous | retain fence/recovery-only state; no risk increase |
| Restart with a finalization tombstone | continue the one-way finalization; do not rotate or reprovision |
| Preliminary audit exists but terminal witness is absent | do not acknowledge final cleanup as terminal proof |
| Store/key/inode identity changes during persistence | fail rather than overwrite unrelated state |
| Older executable cannot interpret current HSL8 state | do not roll back the binary across that schema without a tested migration |

`tests/session_supervisor_lease_store_migration_tests.cpp` and
`tests/unix_session_supervisor_server_tests.cpp` contain actual persistence,
peer, rotation, stale-generation, tombstone and terminal-ack fixtures. Use their
old-format fixtures to add a migration case; do not manufacture a new fixture
by serializing only the current format and calling it an old-version test.
