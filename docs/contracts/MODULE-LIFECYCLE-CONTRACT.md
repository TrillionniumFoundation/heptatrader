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
Strategy Runtime is module version 2.2.0; Management Control is 2.0.1
with the seven-state exception-safety correction below. Existing V2 native callers
must still review the stricter admission rules and recompile native types. Wire
schema V1 is unchanged; no ABI, automatic migration or deployment qualification
is implied.

## Seven-state registry transaction contract

`ModuleLifecycleRegistry` is implemented in
`HeptaTrade/management/module_lifecycle.cpp`. It is separate from the four-state
strategy metadata controller, even though both use generation-fenced updates.
The registry holds one mutex and an in-memory map; no persistent journal,
per-key parallelism, built-in record-count ceiling or OS resource enforcement is
provided by this class. The trusted composition must bound module admission.

| Operation | Required conditions | Atomic publication |
|---|---|---|
| Register | Valid identity and nonzero time; unused module ID | Complete Registered record at generation 1, or no record on failure |
| Duplicate Register | Same identity; time at least the latest committed update | Unchanged snapshot in any current phase; no generation/time advance |
| StageUpgrade | Current generation, non-regressing time, Active or Stopped, different identity | Warming/new identity, generation + 1; save previous-active only when staging from Active |
| Transition | Current generation/time and one of the seven allowed directed edges | State, generation, timestamp, health and reason change together |
| Quarantine | Current generation/time, valid bounded reason, not Stopped | Quarantined with cleared health and generation + 1 |
| Rollback | Current generation/time, saved previous-active, Warming/Shadow/Quarantined, valid health | Restore Active identity at a new generation and consume saved rollback state together |

The allowed directed edges remain Registered-to-Warming, Warming-to-Shadow,
Shadow-to-Active, Active-to-Draining, Draining-to-Stopped,
Quarantined-to-Stopped and Stopped-to-Warming. Quarantine and Rollback are
separate operations, not additional arbitrary Transition edges. Repeating a
Transition to the same state is not an idempotent success. Shadow/Active entry
and rollback require healthy evidence with a canonical digest, nonzero evidence
time not in the future, and age at most 30,000 ms inclusive. Caller observations
are trusted composition inputs; freshness and a digest string do not authenticate
an issuer or bind evidence to a particular process/artifact. Such admission
belongs outside this in-process registry.

Every existing-record mutation prepares a complete private `Record`, including
both the current and previous-active snapshots, before calling `Commit`.
`Commit` constructs the accepted acknowledgement first and only then publishes
the record via a compile-time checked no-throw move. Register prepares its
complete result before single-element map insertion; a failed allocation cannot
leave a default/partial entry that consumes the module ID. All generation
exhaustion checks happen before preparing modified rollback history.

An allocation exception propagates with the existing record and saved rollback
state unchanged. Failed upgrade staging cannot manufacture a rollback target;
failed rollback cannot consume one. No error object is promised under sustained
memory exhaustion: the supervising caller must handle the exception without
inferring a successful transition. The accepted result can be returned without
throwing even when constructor elision is disabled. This is an in-process
exception guarantee, not a process-crash or persistent transaction guarantee.

`Get` looks up its input before modifying the output so an input key may alias
`out.identity.moduleId`. A miss clears the output. On success, the complete
snapshot copy is prepared before a no-throw move to the caller's output; a copy
exception leaves that output unchanged. `ListActive` also returns copies, not
mutable references or deployment capabilities.

`tests/module_lifecycle_transaction_tests.cpp`, discovered through
`tests/python/test_module_lifecycle_transactions.py`, supplies six regression
functions. It checks all 49 state-pair combinations, the inclusive health-age
boundary, invalid health/generation/time/enum inputs, duplicate registration,
alias-safe reads, independent snapshots and twelve barrier-synchronized writers
with exactly one accepted generation. Its thread-local allocation interposer
walks each allocation ordinal until success across registration, upgrade,
rollback, quarantine and five transition paths. Every thrown allocation must
preserve observable state; additional rollback probes check the private history.
The test uses always-active assertions with `NDEBUG` and disables constructor
elision. Interposition is confined to the test executable and introduces no
production fault-injection or authority bypass.

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

## Signed artifact bytes and verified metadata entry

Strategy Runtime module **2.2.0** adds `StrategyArtifactVerifier` and immutable
`VerifiedStrategyArtifact` in `strategy_runtime/strategy_artifact_verifier.*`.
The private read-only Linux file boundary is `strategy_artifact_files.h`. They
are compiled into `hepta_strategy_runtime`. The native verifier identifier is
`hepta.strategy-artifact-verifier.v1`; the controller identifier remains V2.

This implementation loads and verifies artifact, configuration and optional
model **bytes**. It never maps them as executable memory, dynamically links,
extracts archives, interprets configuration/model formats, executes a process or
creates a sandbox. A valid signature authenticates the bounded authorization
message under the selected public key; it does not prove that the program is
benign, profitable, compatible, independently reviewed or qualified for trading.

### Trust policy and canonical signed message

`StrategyArtifactTrustPolicy` is copied at verifier construction and immutable.
The trusted supervisor independently chooses its revision, exact audience,
module, key ID and 32-byte raw Ed25519 public key, revocation flag, key-validity
window, minimum release sequence, maximum signed lifetime and byte limits. No
key is accepted from the manifest or fetched from a URL/environment variable.
One verifier admits one exact module/key/audience tuple. Key provisioning,
rotation, policy authentication, active-policy selection and persisted monotonic
release state are outside this object. Do not let candidate code construct or
choose the verifier which will authorize its own admission.

`SignedStrategyArtifact` is a typed input, not a JSON/package parser. Callers
must bound and validate any transport before constructing it, and must not
concurrently mutate arguments during validation. `SigningMessage` returns the
following unambiguous, domain-separated bytes, or an empty string for an invalid
manifest. Production code contains verification and message construction only;
private keys and signing in the repository are confined to public test fixtures.

```text
ASCII "HEPTA_STRATEGY_ARTIFACT_AUTHORIZATION_V1\n"
8 length-prefixed strings, in order:
  policyRevision, audience, keyId,
  moduleId, version, artifactDigest, configDigest, modelDigest
7 unsigned integers, in order:
  maxThreads, maxFileDescriptors, maxMemoryBytes, maxCheckpointBytes,
  releaseSequence, issuedAtMs, expiresAtMs
```

Every length/integer is eight-byte unsigned big-endian. Digests are canonical
lowercase `sha256:` strings. An empty model digest requires no model path;
otherwise the model is mandatory. Module/version IDs and budgets obey the
controller's existing validation limits. Policy/audience/key IDs are nonempty
bounded identifiers. Signature is exactly 64 raw bytes and is not part of its
own signed message. OpenSSL Pure Ed25519 verification uses one-shot
`EVP_DigestVerify` with a null digest algorithm, and every initialization and
verification result must succeed; no algorithm fallback exists. API reference:
`https://docs.openssl.org/3.0/man7/EVP_SIGNATURE-ED25519/`.

Load rejects mismatched scope, revoked keys, releases below the independent
minimum, malformed signatures and invalid times **before file reads**. Valid
signed intervals satisfy `issuedAtMs <= observedAtMs < expiresAtMs`, fit wholly
within the pinned key window, and do not exceed the configured maximum lifetime
(default/hard maximum 86,400,000 ms). Timestamp arithmetic uses ordered unsigned
subtraction, not potentially overflowing expiry addition. Observation time is
provided by a trusted supervisor; there is no trusted clock or live revocation
service inside this library.

The verifier's policy digest covers its domain, all four identity strings, raw
public key, revocation flag, both key times, minimum sequence, maximum lifetime
and all four byte limits using the same field/integer encoding. The immutable
verified result retains this digest, signed manifest, copied bytes and validation
time. A policy with the same revision name but changed key/limits has a different
fingerprint and cannot authorize an old result without fresh verification.

### Read-only bounded file admission

The three files must have distinct simple leaf names of at most 96 characters
from letters/digits/dot/underscore/hyphen; `.` and `..` are refused. The absolute
existing directory is at most 4,096 bytes and 64 components, with no empty,
dot, parent or NUL components and no trailing separator. Its effective-UID
ownership and exact 0700 mode are checked. All components are opened with
no-follow directory descriptors. Files must be effective-UID-owned, exact 0600,
single-linked regular files; symlinks, executable/set-ID modes, FIFOs, devices,
directories, missing and zero-length required files are refused.

Artifact/configuration/model default byte caps are respectively 16/1/32 MiB;
total default and hard maximum is 64 MiB. Each individual cap must fit the total;
a model cap of zero explicitly forbids model-bearing bundles. Each read is also
limited by remaining aggregate capacity and the descriptor's declared memory-byte
ceiling. These bound input bytes and per-call allocations, not allocator overhead,
OpenSSL internals, caller-retained result copies, filesystem caches or total
process memory. They do not enforce CPU/FD/OS sandbox budgets or wall-clock I/O
deadlines. Depth bounds permit at most root plus 64 component FDs and 3 files.

The reader retains every file descriptor until all files are read and all digests
match. It handles short/interrupted reads, probes one byte beyond the captured
size, and revalidates every file's device/inode/size/mtime/ctime/mode/UID/link count
and named bindings after the complete bundle has been read. Directory bindings
are rechecked as well. This rejects even identical-byte inode substitution of an
earlier file while a later one is read. File/directory close errors also prevent
success. No persistent file, permission or lock is created or changed. Failures
release all owned descriptors, including when a C++ allocation throws; successful
output is published only after all checks and closes complete.

These checks assume a trusted local directory and cooperating deployment actor;
they do not prove resistance to arbitrary malicious same-UID code, all network
filesystem behavior or privileged directory manipulation. The result owns a
historical immutable copy, not a promise that the path will remain current.
Subsequent execution must consume the verified bytes, not reopen a path and assume
it still denotes the same artifact. Non-Linux loading fails closed. Platform
support outside Linux has not been qualified by this implementation.

### Controller composition and policy changes

`Authorizes(result, now)` rechecks the exact current verifier fingerprint,
revocation, release minimum, key/manifest time windows and non-regression from the
result's validation time. `AdmitVerified` uses this check before ordinary complete
descriptor admission. `StartVerified` repeats it at use time and under the
controller mutex requires the exact expected generation, Admitted phase and
**full descriptor equality**, not only an equal executable hash. Publication uses
the existing prepared-state/prepared-acknowledgement no-throw commit.

A tested recovery sequence is: verify bundle; AdmitVerified; Load an independently
selected checkpoint for that descriptor; RestoreCheckpoint under the new local
generation; StartVerified with the resulting generation and fresh observation time.
The latter updates metadata only. It neither deserializes checkpoint/model/config
bytes nor executes the artifact. An expired or revoked selection cannot pass the
verified Start path, and a different config/model/budget cannot be substituted.

The older raw `Admit`/`Start` APIs remain metadata-only compatibility operations.
They do not perform signature verification and must not be treated as authorization
for a future process launcher. This additive change does **not** turn the existing
metadata controller into a globally enforced execution gate. A real supervisor
must choose the current trusted verifier, use the verified paths, revalidate at
launch, and independently enforce isolation and health/release policy. Keeping an
old verifier after revocation can retain its old policy view; this library does
not authenticate or automatically distribute policy updates. No result grants
Broker credentials, PAPER/LIVE operation, merge or deployment permission.

### Requirement-to-assertion mapping

`tests/strategy_artifact_verifier_tests.cpp`, invoked by the canonical Python
wrapper `tests/python/test_strategy_artifact_verifier.py`, contains eight functions:

| Requirement | Direct regression |
|---|---|
| Exact bytes, optional model, immutable result and independently encoded golden signature | `TestRoundTripAndIndependentSignatureVector` |
| All signed fields, every signature byte, length, wrong key and crypto initialization failure | `TestSignedFieldTamperingAndInvalidCrypto` |
| Scope, revoked policy, key window, release minimum, lifetime and use-time fingerprint | `TestTrustPolicyRevocationWindowsAndSequence` |
| All payload domains and inclusive individual/aggregate bounds | `TestAllPayloadsAndExactResourceCaps` |
| Unsafe paths, modes, symlink/hardlink/FIFO/directory rejection | `TestUnsafePathsPermissionsAndSpecialFiles` |
| Retained-file replacement, short/EINTR/read/close faults, descriptor cleanup | `TestRetainedFileRevalidationAndIoFailures` |
| Verified admission/start, full identity and actual checkpoint restore composition | `TestVerifiedControllerEntryAndCheckpointComposition` |
| Every observed C++ allocation-failure ordinal in Load/Start and unchanged state | `TestAllocationFailurePublicationAndFdCleanup` |

The golden signing message/signature is independently encoded with Python
`struct`/`hashlib` and `cryptography`, then fixed in the C++ test. This validates
application-level interoperability, not an independent cryptographic proof or a
second implementation of Ed25519. Public test seeds are not production signing
credentials. Interposers exist only in test binaries. Always-active assertions,
NDEBUG and disabled constructor elision keep failure-path checks effective in
Release-like builds. Full repository CI and independent security review are still
required on the exact changed source; these fixtures do not qualify a target host.

## Bounded checkpoint payload persistence and explicit restore

Strategy Runtime module **2.1.0** adds `StrategyCheckpointStore` and
`VerifiedStrategyCheckpoint` in `HeptaTrade/strategy_runtime/strategy_checkpoint_store.*`.
The library target is `hepta_strategy_runtime`; the native store identifier is
`hepta.strategy-checkpoint-store.v1`. The four-state controller identifier remains
V2 and gains an additive `RestoreCheckpoint` operation. This is Linux local
payload persistence plus metadata restoration, not automatic process recovery,
strategy code execution or an extension of trading authority.

### Ownership and admission

The trusted composition owns the store, private path, descriptor and independently
retained checkpoint digest. An untrusted strategy may supply bounded opaque bytes;
it must not own the filesystem directory, the store/controller objects or the
selection of recovery evidence. The API's source-generation and timestamp inputs
are provenance supplied by that composition, not authenticated worker evidence.
The storage implementation does not itself establish this OS separation.

A store is bound to the complete module/version/artifact/config/model identity
and all four resource-budget fields. It requires an existing absolute directory
owned by the effective UID with exact mode 0700. Files and the companion lock
are effective-UID-owned, single-linked regular files with exact mode 0600; the
lock is empty. Path components are opened without following symlinks, retained
through each transaction and rechecked. The directory and lock inode identities
are also retained logically across successful operations on the same handle.
Replacement of either prevents further use of that handle, even with equal data.
Relative paths, `..`, embedded NUL, more than 64 directory components, filenames
outside the bounded identifier alphabet or over 96 bytes, symlinks, hardlinks,
FIFOs and unsafe permissions are rejected. No permissions or directories are
silently repaired. A first Load can create the private companion lock.

`Load(expectedRecordDigest)` is mandatory before Save. An empty digest requires
that the record is absent; it never means trust the first file found. Existing
bytes require an exact canonical SHA-256 digest independently selected by the
supervisor. Do not compute the expected digest from the same untrusted file and
then describe the comparison as independent validation. Load reads only a bounded
regular descriptor, checks extra bytes, size/time/inode/permission stability and
named bindings, compares the record digest, and parses the entire envelope before
publishing a verified immutable copy. Failed Load closes readiness.

### Physical encoding and bounds

The canonical V1 file has no JSON, native-endian integers or optional trailing
fields. Its order is:

```text
ASCII "HEPTA_STRATEGY_CHECKPOINT_V1\n"
five length-prefixed strings: moduleId, version, artifactDigest, configDigest, modelDigest
four unsigned integers: maxThreads, maxFileDescriptors, maxMemoryBytes, maxCheckpointBytes
four unsigned integers: checkpointSequence, sourceGeneration, savedAtMs, payloadLength
exactly payloadLength opaque bytes, including arbitrary NUL/non-text bytes
```

Every integer and every string length is eight-byte unsigned big-endian. Strings
must byte-match the canonical bound descriptor; a noncanonical or reinterpreted
identity cannot be accepted. Sequence, source generation, saved time and payload
length must be nonzero. Trailing bytes and all truncated prefixes are invalid.
The externally retained `RecordDigest` covers the **whole** encoded record;
`PayloadDigest` is separately computed over payload bytes and is the digest used
by the controller's existing checkpoint-metadata API. They are different names
for different byte domains, not interchangeable attestations.

The default payload ceiling is 1 MiB; a constructor limit may be at most 16 MiB.
The descriptor's checkpoint-byte budget is an additional upper bound. The whole
file read is bounded by payload ceiling plus 1,024 envelope bytes; validated
identity lengths fit that envelope. There is one latest record, one empty lock
and at most one owned staging slot `.<filename>.pending` per store path. The
staging file is created with O_EXCL and no-follow: an existing file or symlink
is never truncated, reused or automatically deleted. A crashed writer's leftover
therefore blocks new saves rather than accumulating unlimited random temporary
files. Preserve and review that evidence offline before cleanup. Repeated writes
do not append historical payloads or perform implicit rotation/replication.

The implementation bounds its own per-operation buffers and one retained current
receipt, not arbitrary copies retained by callers, the caller's input allocation,
filesystem caches or total process memory. Up to 64 directory components plus
root, lock and an active record/staging descriptor are admitted. This is not a
hard latency deadline or an OS-level resource sandbox.

### Save transaction and failure semantics

A nonblocking private flock serializes cooperating local writers. Each Save reads
and verifies the exact latest record previously loaded by that handle, including
on a duplicate request. The next sequence must equal the loaded sequence plus
one. A duplicate requires identical sequence, source generation, saved time and
payload; it returns unchanged data. Older timestamps, gaps, conflicts and exhausted
sequence arithmetic are rejected. All encoded bytes, digests, immutable payload
and successful acknowledgement allocations are prepared before persistent mutation.

```text
revalidate directory/lock/current bytes
  -> create exclusive staging slot
  -> complete short/interrupted writes
  -> fsync staging file and validate identity
  -> recheck old target and renameat
  -> fsync parent directory
  -> validate resulting file/directory/lock, close file
  -> publish immutable receipt and readiness
```

Before rename, failure preserves the prior record. After rename, directory-sync,
close or binding failure can leave new bytes on disk without a successful
acknowledgement. The result is rejected with `uncertain=true` and no verified
checkpoint. `attemptedRecordDigest` identifies the attempted bytes for explicit
reconciliation; it is not a success receipt. An exception after persistence
starts also requires reconciliation, not blind retry. Save is disabled after
I/O/rebinding/concurrent-write failure until a successful Load resolves the actual
selected file. Only a staging inode created and still owned by that transaction
is removed by normal error cleanup; a published record or prior orphan is never
removed as cleanup. The filesystem must honor the requested synchronization;
these tests do not establish actual power-cut durability for a deployment device.

On a live handle, Load rejects missing history, a lower sequence/time and changed
same-sequence bytes even when an older digest is supplied. A fresh process cannot
know a global latest sequence: explicitly selecting an old valid digest can load
an old checkpoint. Authentication, cross-restart anti-rollback and recovery
selection therefore remain separate supervisor policies. SHA-256 is integrity
binding, not a digital signature or proof of producer identity. Same-UID malicious
writers, network filesystems, encrypted backups and distributed consensus are
outside this cooperating-local-store guarantee.

### Controller integration and recovery

Normal checkpointing calls Save first, then the existing controller Checkpoint
with the returned sequence, payload digest and byte count under the expected
controller generation. No controller mutex is held during file I/O. The two
operations are **not** one atomic transaction: a quarantine, replacement or crash
between them can leave a durable checkpoint ahead of acknowledged metadata.
Treat a rejected metadata handoff as a supervision/reconciliation event, not as
permission to run, reuse a stale generation or erase the durable evidence.

For recovery, the supervisor loads the independently selected exact record and
admits the same full descriptor to a new controller. `RestoreCheckpoint` requires
a valid construction-restricted checkpoint, exact local generation, non-regressing
current time, a saved time not in the future and phase Admitted. It restores only
sequence/payload-digest/byte-count metadata, incrementing the **new local** generation;
it never transplants the old process generation. Duplicate restores must match
all metadata and do not advance time/generation. Conflicting restores or a
Running/Quarantined/Stopped phase are rejected. Complete proposed metadata and
acknowledgement are prepared before no-throw publication, retaining exception
atomicity. The controller remains Admitted: ordinary Start checks still apply.

An issued checkpoint owns immutable verified bytes. Later file changes or saves
do not mutate it; it denotes that historical content, not continuously attested
latest state. The caller must independently validate payload-specific semantics
before any deserialization and must establish a real process sandbox/rollout
executor before running strategy code. No serialized copy of this C++ object is
network authority; no checkpoint grants Broker, PAPER or LIVE access.

### Direct executable evidence

`tests/strategy_checkpoint_store_tests.cpp` contains thirteen test functions;
`tests/python/test_strategy_checkpoint_store.py` builds and executes the real
store/controller/OpenSSL/filesystem chain with always-active assertions. Tests
cover binary round trips and all descriptor bindings; explicit pins and no
trust-on-first-use; every truncated prefix and every single-byte corruption of
the reference envelope; bounds and sequence exhaustion; unsafe files/paths/locks;
stale writers and live-handle rollback; directory/lock replacement; short and
interrupted I/O; failure before and after rename; committed bytes surviving
`_exit` without destructor flush; a crash leaving a single blocking staging file;
twelve simultaneous cooperating writers; and exhaustive allocation-failure
ordinals for Save/Restore. The handoff test explicitly exercises durable bytes
surviving a rejected metadata update. Fault interposers exist only in the test
binary. This is finite component evidence, not a deployment or broker receipt.

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
tests/python -p '*transactions.py'`. The canonical Python suite discovers all three
transaction wrappers; existing bounded-runtime CTests continue to exercise the public
composition. Passing these fixtures is not real power-loss, network-filesystem,
malicious same-UID, independent-review, production-SLO or Broker qualification.
The current plan's remaining automatic checkpoint selection/process recovery,
sandbox execution, deployment executor and external governance/PAPER gates remain
separate open work.
