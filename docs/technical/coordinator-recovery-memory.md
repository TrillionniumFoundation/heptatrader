# Coordinator recovery memory and exception boundary

Status: CURRENT
Applies to: ExecutionCoordinator::RecoverFromJournal; not a checkpoint format
Implementation: `HeptaTrade/execution/execution_coordinator.cpp`, `HeptaTrade/oms_journal.cpp`
Tests: `tests/recovery_projection_faults.cpp`, `tests/python/test_recovery_projection.py`, `tests/execution_coordinator_tests.cpp`

## One validated event sequence

The journal first validates and materializes the complete pinned snapshot,
including gzip members, decoded budgets, record validity and final file identity.
It invokes no callback on a validation failure. Only then does it unlock its
mutex and invoke callbacks against the frozen in-memory event sequence.

The coordinator consumes that sequence directly while holding its existing
coordinator mutex. Its former second vector copied every event and its strings
before projecting them. Removing that duplicate reduces temporary recovery
storage without changing event order, durable identities, file formats or wire
versions. Projection callbacks update internal state only; they never submit to
a venue. Public reads and dispatch cannot interleave with partial projection.

## Allocation and projection failures

A thrown projection/allocation exception clears all rebuilt command, owner,
fence and rate-index projections before the coordinator lock is released. The
coordinator sets its boolean mutation fence before allocating diagnostics and
reports `OMS_RECOVERY_PROJECTION_FAILED`. Under sustained process-wide memory
exhaustion even a diagnostic string may fail to allocate; the coordinator must
remain fenced, and process supervision/recovery remains required.

A negative journal replay result retains `OMS_REPLAY_FAILED` and a fenced,
empty projection. Neither failure path edits the journal. After the underlying
failure is removed, retry complete recovery using the same bytes and command
identities; never manufacture a new order identity to work around recovery.

Validly parsed uncertain commands are different from exceptions. They remain
available for authoritative reconciliation even though recovery returns false.
Do not clear them merely because `ValidateRecoveredProjectionLocked` rejects
new risk. Existing terminal evidence, owner scopes and correlation rules still
apply. Successful recovery never resends an admitted command.

## Executable failure evidence

The Python core partition builds a separate test executable from the actual
coordinator/journal sources. A test-only replacement allocator injects one-shot
failures at allocation positions across bounded repeated recovery. Public
command, owner, owner-fence and rate-index reads must show either the complete
successful projection or an empty fenced failure. The test retries recovery,
checks byte-identical journal contents and proves the venue send count remains
two. Late corruption and valid uncertain outcomes have separate controls.

The standalone allocator replacement is not linked into installed programs.
The ordinary coordinator suite still runs in both existing sanitizer lanes.
No new workflow, approval gate, production fault flag or dependency is added.

## Remaining lifecycle work

This is not constant-memory replay: the journal still materializes one bounded
sequence, and durable command and send-attempt identities still grow with
history. It does not deliver checkpoints, incremental replay, a disk-backed
identity index or multi-day host capacity qualification. Gzip and the early
entry pause do not change that fact. `OMS-LIFECYCLE-002` remains open.

A general cap in `RejectLocked` is unsafe: that helper also handles outcomes
already written to the ledger, including flatten rejection. A future bounded
pre-admission refusal cache must explicitly separate no-intent refusals from
admitted identities; it must never evict durable/recovered commands. No such
retention change is hidden in this recovery optimization.

See [recovery budgets](oms-recovery-capacity.md),
[storage maintenance](oms-archive-lifecycle.md) and
[persistence support](persistence-support-window.md).
