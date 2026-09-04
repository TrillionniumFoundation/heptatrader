# Module Lifecycle Contract V1

Status: current normative; bounded native controllers are specified separately below
Applies to: Management Control and simulator strategy modules
Verification: lifecycle-faults, rollout-rollback and transaction fault-injection tests
Authority: generation-fenced lifecycle state

每个模块版本绑定 module/version、artifact/config/model digest 与单调 generation。允许状态为 REGISTERED → WARMING → SHADOW → ACTIVE → DRAINING → STOPPED；任意非 stopped 状态可被 fail-closed QUARANTINED。所有 transition 使用 expected generation，旧 generation、时间回退、过期/不健康 evidence 和非法状态跳转均拒绝。

ACTIVE 升级会保存 previous-active identity 并进入 WARMING；若 shadow diverges 或运行故障，Management 可 quarantine 新版本，再以新 generation 恢复经健康验证的 previous active。Management 不持有 broker credential，不参与 tick hot path，也不能绕过 Execution。机器 schema 为 `schemas/module-lifecycle-v1.json`。

## Three distinct state mechanisms

`ModuleLifecycleRegistry` owns the seven-state in-process lifecycle above.
`StrategyRuntimeControl` owns four-state strategy metadata, not OS processes.
`DurableRolloutStore` persists desired state, not observed deployment success.
Neither a stored desired state of `active` nor a controller phase of `Running`
starts a process, proves health or grants Execution authority.

The inter-module contract remains `hepta.module-lifecycle.v1`. The native
controller identifiers are independently versioned:
`hepta.strategy-runtime-control.v2` and `hepta.durable-rollout-store.v2`.
Both module manifests are version 2.0.0 because existing callers must review the
stricter admission rules and recompile native types. Wire schema V1 is unchanged;
no ABI, automatic migration or deployment qualification is implied.

## Strategy metadata transaction contract

The implementation is `HeptaTrade/strategy_runtime/strategy_runtime_control.h`.
A controller holds one mutex for its bounded module map. Its constructor defaults
to 256 modules; the trusted composition selects the count. There is no per-key
parallelism inside that controller. Budget fields are validated declarations,
not enforced operating-system quotas. Caller time is a trusted composition input,
not an independent clock authority.

| Operation | Preconditions | State change |
|---|---|---|
| Admit | Valid complete descriptor; nonzero time; capacity available | Create Admitted at generation 1 |
| Duplicate Admit | Identical descriptor, Admitted, time not before committed update | Return unchanged snapshot with duplicate=true |
| Start | Exact expected generation, non-regressing time, Admitted, matching artifact digest | Running; generation + 1 |
| Checkpoint | Running, generation/time valid, nonzero sequence and byte count within declared budget | Record sequence/digest/bytes together; generation + 1 |
| Duplicate Checkpoint | Current generation; same sequence, digest **and byte count** | Return unchanged snapshot; conflicting bytes or digest are rejected |
| Quarantine | Valid bounded reason; generation/time valid; not Stopped | Quarantined; generation + 1 |
| Replace | Quarantined or Stopped; new valid descriptor; generation/time valid | Admitted with new descriptor; clear checkpoint sequence, digest and byte count; generation + 1 |
| Stop | Generation/time valid | Stopped; generation + 1, or unchanged duplicate when already stopped |

A skipped checkpoint sequence is permitted; replay of an older sequence is not.
There is no inference that an absent checkpoint payload can be reconstructed.
`checkpointBytes` binds metadata identity only: the controller does not load,
hash, sign, persist or deserialize a payload. Snapshot copies do not convey
permission to mutate controller state. `Get` supports an input key that aliases
its output snapshot and clears the output on a miss.

All modifying operations construct the complete proposed snapshot and accepted
result before publication. `Commit` uses a compile-time checked no-throw move
while the mutex is held. Admit prepares its result before map insertion. If an
allocation fails, the exception propagates to the trusted caller and the prior
record, phase, generation and checkpoint identity remain unchanged. No success
is returned and no admission slot is consumed. A caller must catch failures at
its supervisor boundary; this API does not claim it can allocate an error result
under sustained memory exhaustion. Duplicate observations do not advance the
committed timestamp, and failed observations do not create a clock-fault epoch.

## Durable local desired-state contract

The implementation is `HeptaTrade/management/durable_rollout_store.h`; private
Linux file operations are in `rollout_file_boundary.h`. All objects sharing a
path must cooperate through its companion `.lock` file. The final parent belongs
to the effective management UID and must not be group/world writable. Ancestors
and the leaf are traversed with no-follow descriptor operations; `..`, empty
leaf names, embedded NUL and leaf names longer than 128 bytes are rejected.
Linux is the implemented persistence platform; unsupported platforms fail closed.

`Load` is mandatory before Put/Get/List/Reconcile. It can create missing private
parent directories and the private companion lock; it does not fabricate a
nonempty store. The store and lock must be regular files, effective-UID owned,
mode 0600, single-linked and still bound to their named inode. Existing V1 files
with looser permissions are rejected, not silently repaired. Preserve a backup,
review ownership/permissions offline under the trusted UID, and validate the
file before starting V2. Never weaken permissions to make a migration pass.

Defaults remain 1,024 records and 4 MiB; hard admissible ceilings are 4,096 records
and 16 MiB, with a minimum file budget of 64 bytes. Reads use the descriptor's
size, bounded pread, an extra-byte probe and pre/post identity/size/mtime/ctime
checks. A corrupt, replaced, oversized, symlink, hard-linked or nonregular file
cannot populate usable state. The physical encoding remains canonical
`HEPTA_ROLLOUT_STORE_V1`, sorted length-prefixed rows and an FNV checksum. V2 also
rejects noncanonical spellings and unknown lifecycle state strings. FNV is
accidental-corruption detection, **not authentication or anti-rollback proof**.

| Stage | Required behavior |
|---|---|
| Acquire | Retain parent descriptor; open/validate companion lock; acquire nonblocking exclusive flock. Contention is a typed failure, not an unbounded wait. |
| Revalidate | Read current bounded bytes while locked and compare with the exact bytes last loaded/committed by this handle. Even duplicate Put must pass this check. |
| Prepare | Validate generation/time/capacity, construct complete proposed map, serialized document and accepted result without altering current state. |
| Persist | Create a fresh O_EXCL private temp in the same directory; retry interrupted/short writes; fsync the temp; validate named identity; renameat; fsync parent. Never truncate a predictable `.tmp` file. |
| Publish | Recheck file/directory/lock binding and close the file; only then swap memory and cached bytes and return success. |
| Fail | On I/O, concurrency, parse or load failure, mark the handle not ready. Preserve the disk evidence; require a successful Load before further use. |

A failure before rename leaves the prior record file intact. A failure after
rename, including directory fsync or close failure, may leave the new file on
disk while memory still holds the old snapshot. This is an **uncertain commit**:
Put is blocked until Load resolves the actual state. Do not retry blindly from
the old generation. Successfully created private temporary files are removed
only when still bound to the owned inode; renamed records are never deleted as
error cleanup. Companion lock files remain part of the store's operating state.

Load on a live handle rejects disappearance, removal of a known module, lower
generations/timestamps and same-generation identity changes. A new process has
no independent monotonic history: replaying an older valid complete file across
a fresh restart remains outside this local mechanism and requires a separately
trusted epoch/backup/admission policy. Get/List are snapshots of the last
successful load/commit, not continuous filesystem attestation.

Reconcile returns proposed actions only. Unready state, malformed/oversized
observations or duplicate module observations produce one `blocked` action with
an empty module ID, meaning the whole reconciliation is blocked. No caller may
interpret it as convergence or permission to deploy. Valid actions compare the
existing observed artifact/config/state fields; the current observed structure
has no version, model digest or generation attestation, so `noop` does not prove
those dimensions. No distributed rollout executor is implemented here.

## Executable regression and fault evidence

`tests/strategy_runtime_transaction_tests.cpp`, invoked by
`tests/python/test_strategy_runtime_transactions.py`, checks regressed duplicate
admission, byte-conflicting checkpoints, stale generations, alias-safe reads,
checkpoint reset, capacity, snapshot isolation and twelve simultaneous writers
with one accepted generation. Its allocator interposer walks every allocation
ordinal until success for Admit/Start/Checkpoint/Quarantine/Replace/Stop and
asserts unchanged state on every injected failure. No test depends on sleep.

`tests/rollout_store_transaction_tests.cpp`, invoked by
`tests/python/test_rollout_store_transactions.py`, operates on real local files.
Linker interposers fail selected fsync/rename calls and exercise interrupted and
short writes. It verifies pre-/post-rename recovery, symlink and hardlink
rejection, FIFO rejection, private modes, path rebinding, lock contention,
cooperating-writer conflicts, in-process rollback detection, capacity, duplicate
observations and corruption closing readiness. Interposers exist only in the
test binary. They do not change production permission or durability checks.

Run these from the repository root with `python3 -m unittest discover -s
tests/python -p '*transactions.py'`. The canonical Python suite discovers both
wrappers; existing bounded-runtime CTests continue to exercise the public
composition. Passing these fixtures is not real power-loss, network-filesystem,
malicious same-UID, independent-review, production-SLO or Broker qualification.
The current plan's remaining payload persistence, sandbox execution, deployment
executor and external governance/PAPER gates remain separate open work.
