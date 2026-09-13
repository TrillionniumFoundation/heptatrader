# Lossless offline core-state archives

Status: CURRENT
Applies to: stopped deterministic-simulator Execution and Gateway state only

`hepta_core_state_archive.py` is an installed root-custodian command for a
lossless gzip copy of the complete core OMS journal and the encrypted lease
store. It does not rotate, truncate or compact the active ledger; no command ID,
fence generation, fill or terminal record expires. It stores no plaintext key,
Agent token file, HFC1 credential or Broker credential. The matching lease key
and execution fence inputs remain separately protected deployment inputs.

## Consistency and admission

Snapshot acquires the real root-owned lease cleanup lock at
`/run/hepta-agent/session-lease-terminal-cleanup.lock` exclusively and the
Execution directory's actual `execution-runtime.lock`. The live Gateway holds
a shared lease lock; live Execution holds the exclusive state lock. A busy,
missing, replaced, linked or unsafe lock aborts before state reads/output
creation. The command never creates/chmods a lock to manufacture custody.
Root stops Gateway then Execution using the normal service procedure first.

Input directories and file descriptors are pinned without symlink traversal.
Journal/lease owner, group, private mode, single link, size and stable metadata
are checked; all source identities and the separate key digest are rechecked.
The operator must control ancestor namespaces and supply the exact deployment
paths/UIDs; arbitrary configurations are not auto-discovered or adopted.

The archive is a new mode-0700 directory containing exactly a gzip journal,
encrypted lease bytes and a bounded manifest. The manifest contains SHA-256
and uncompressed byte counts, custody IDs and the separate key's digest. It is
not a signature or a proof of valid journal semantics. Maximum journal bytes
are 1 GiB; encrypted lease bytes are 2 MiB. Copy/verification stream in 64 KiB
chunks; gzip expansion is bounded to the validated manifest length. Arbitrary
filenames, extra payloads and credentials are not accepted archive members.

## Commands

Prepare a root-owned mode-0700 backup parent outside the runtime directories.
The following placeholders must be replaced by the current host's approved
core paths/identities; the commands do not grant or restore trading authority:

```bash
python3 /usr/libexec/heptatrader/hepta_core_state_archive.py snapshot \
  --execution-state /approved/core-execution-state \
  --lease /approved/gateway-state/leases --key /approved/secret/lease-key \
  --execution-uid 2002 --gateway-uid 2001 --gid 2000 \
  --output /private/backups/new-checkpoint

python3 /usr/libexec/heptatrader/hepta_core_state_archive.py verify \
  --archive /private/backups/new-checkpoint --key /approved/secret/lease-key
```

Use `restore` with `--archive`, `--key`, the same explicitly approved identities
and a never-existing `--output`. Every archive member is verified before output
creation, then independently checked while copying into fresh `execution/` and
`gateway/` directories. Existing directories are never overwritten or adopted.
The helper does not install them, copy the key, republish tokens, or start any
service. Rebind paths only through the existing deployment process, validate
native replay and schema compatibility, and reconcile the selected simulator
state. Never place an old lease over newer fence state or run two deployments
against one authoritative venue. IB and arbitrary N-1 migrations are not
supported by this helper.

## Failure, retention and limits

File and directory fsync are required before successful completion. A failed
attempt remains private and incomplete for inspection; it is not reused. A
sync failure after writes is an uncertain durability result, not permission to
assume old output state. No success is printed on an exception. A manifest or
RESTORED marker alone is not deployment permission; verification checks all
bytes/custody, and native replay still owns semantic acceptance.

Cold archives may be retained on protected storage under the host's retention
policy, with the matching key in separate secret custody. This feature does
not solve indefinite active-ledger growth: replay budgets, prospective online
segmentation/compaction, actual-host restore SLO and retention deletion policy
remain separate work. Keep history; do not use this archive as an excuse to
reset the running ledger. Actual target-host acceptance remains OPEN.

Unit tests exercise locks, exact byte restoration, missing/reused outputs,
wrong keys/UIDs, corruption, expansion bounds, special files, symlink ancestors,
source replacement and failed fsync. Installed multi-UID tests prove live-lock
rejection and recover the original persisted 25-unit position, encrypted lease
and command identity with no duplicate send, then exit to flatness. Archive
bytes and keys are fixture-private and never included in CI artifact uploads.

A state directory containing `ib-paper-runtime.lock` is rejected even if a
core lock also exists: mixed deployment state is not a core-only archive.
Corrupt DEFLATE streams produce the same bounded, content-free CLI failure as
other invalid archives rather than leaking a Python traceback.

## Packaging compatibility

The candidate install test checks these helper bytes, permissions and runnable
entry points. The shared v1 preflight inventory is unchanged: existing admitted
previous artifacts are not required to contain a newly added optional helper.
This preserves the explicitly tested rollback pair instead of making diagnostic
packaging changes into an implicit unsupported-format retirement.
