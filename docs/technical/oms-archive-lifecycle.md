# Stopped-state, lossless OMS storage maintenance

Status: CURRENT
Applies to: archive-aware OMS reader/writer and installed offline maintenance helper
Implementation: `HeptaTrade/oms_archive_codec.h`, `HeptaTrade/oms_journal.cpp`, `scripts/hepta_oms_archive.py`, `scripts/oms_archive_codec.py`
Tests: `tests/oms_archive_cases.h`, `tests/python/test_oms_archive.py`, `tests/python/test_installed_runtime_processes.py`

## What this changes and what it does not

The journal may use ordinary JSONL (unchanged default) or standard gzip members.
Offline `compact` losslessly packs the entire validated history into one member.
Subsequent archive-aware appends write one complete gzip member per JSON record,
including its newline, before the existing critical fdatasync. No event is
removed, reordered, deduplicated, converted, or given a new command identity.
There is no automatic online rotation, implicit conversion, remote backup API,
new credential, or authority-bearing RPC. This is optional stopped-state disk
maintenance, not an unlimited retention/memory solution.

OMS event schema remains 4 with the existing historical readers. Gzip is a
separate storage-encoding compatibility axis. New binaries read both encodings;
old binaries do not. A prior artifact must reject compressed bytes rather than
skip history. Before a downgrade, stop services and explicitly expand with the
archive-aware helper. Test that exact artifact pair and its lease/key/journal
state. No promise of arbitrary historical or unresolved-Broker-order rollback
is made by this document.

## Complete validation and bounded resources

The C++ reader uses `inflate` with gzip checks, not permissive `gzread`. Every
member must reach its checksum-validated end. Trailing garbage, padding,
truncation, unknown compression, CRC failure, and more than 1,000,000 members
reject the complete replay. No execution recovery callback runs until the entire
snapshot, including all later members and metadata, has validated. Bad data
never applies a valid prefix. The Python diagnostic and maintenance readers
independently enforce the same stream boundaries.

`HEPTA_OMS_REPLAY_MAX_BYTES` charges **decoded bytes including newlines**;
record count and per-record size limits still apply to every original record.
Compressed storage cannot evade a replay budget. Physical compressed input is
also bounded to `2 * max_decoded_bytes + 1 MiB`; ordinary JSONL retains its
existing physical/decoded ceiling. Chunks are 8 KiB. Parsed events remain bounded
materialization, not a constant-memory recovery checkpoint. Repacking does NOT
lower the historical record count, decoded replay memory, or identity count.
If those budgets are exhausted, preserve the ledger and follow the existing
measured budget/recovery procedure; do not truncate to obtain a green startup.

The existing capacity observation's `bytes`, `max_bytes` and byte headroom refer
to decoded history. Added `storage_bytes` reports actual file size (or null when
unknown) and `gzip_storage` identifies encoding. Append latency includes optional
record compression. Gzip adds CPU and member overhead to each append: measure
that mode on the intended host before adoption. CRC checks corruption; they are
not cryptographic authentication or protection from a malicious service UID.

## Writer exclusion and filesystem trust

Archive-aware runtimes hold a shared advisory flock on their pinned journal
inode throughout its lifetime. This lock excludes the offline rewriter, which
requires an exclusive nonblocking lock. Shared runtime locks intentionally do not
serialize multiple runtime writers; deployment's existing single-service owner
contract remains required. Older runtimes do not participate in this protocol.
`--stopped-state` acknowledges an independently performed service stop; it is NOT
proof that every old/non-cooperating writer has stopped. Never run maintenance
against a live old runtime merely because the lock was obtainable.

The helper runs as the journal's service UID. The file must be private mode 0600,
regular, single-link, and owned by that UID. It opens ancestors descriptor-wise
without following symlinks. The direct state directory must be owned by that UID,
mode 0700; earlier ancestors must be owned by root/service and not writable by
others (root-owned sticky intermediate directories allow disposable /tmp
fixtures). That private directory is a trusted, exclusively controlled namespace.
A process sharing the service UID is not an independent untrusted security domain.

A rewrite creates a fresh mode-0600 same-directory temporary, locks its inode,
streams all validated raw records into it, fsyncs it, and independently reads it
back. Decoded SHA-256 and record count must equal the input. Source and temporary
identity are revalidated before one atomic replace. Locks on BOTH old and new
inodes are held through directory fsync. A runtime opening either inode during
replacement cannot acquire its shared lock; an old-path opener must also pass
the runtime's existing pinned-path revalidation.

## Failure/restart matrix

| Interruption | Meaning / action |
|---|---|
| Active cooperating writer | Exclusive lock fails immediately; no rewrite. |
| Bad input, budget, checksum, source or temporary substitution | No published replacement; original evidence is not edited. |
| Crash before replace | Original remains authoritative; a private orphan temporary may remain. It is never adopted automatically. |
| Crash after replace | Either complete encoding is recoverable by the new reader; no partially built file is published. Independently inspect before resuming. |
| Directory fsync fails after replace | Report `OMS_MAINTENANCE_DURABILITY_UNCERTAIN_INSPECT_CURRENT_FILE`; do not assume old bytes remain and do not automatically roll back. |
| Old binary sees gzip | Refuse startup before admitting mutations; explicitly expand using candidate helper, then retry the approved old artifact. |

Do not remove a failed temporary blindly on a production host. Confirm stopped
state, ownership and which inode is authoritative, then retain or dispose of it
under the same protected-state policy. SIGKILL tests cover process crash, not
hardware power loss or every filesystem's durability implementation.

## Operator commands

First stop all relevant writers using the actual deployment manager, retain a
consistent protected lease/key/journal checkpoint, and record the previous and
candidate artifact identities. Run as the actual service UID; the example path
must refer to its private stopped-state directory.

```sh
python3 /usr/libexec/heptatrader/hepta_oms_archive.py \
  --journal /var/lib/hepta-execution/oms-journal.jsonl --operation inspect
python3 /usr/libexec/heptatrader/hepta_oms_archive.py \
  --journal /var/lib/hepta-execution/oms-journal.jsonl --operation compact --stopped-state
# Before a downgrade to a binary without this storage reader:
python3 /usr/libexec/heptatrader/hepta_oms_archive.py \
  --journal /var/lib/hepta-execution/oms-journal.jsonl --operation expand --stopped-state
```

Non-default native recovery budgets require corresponding `--max-bytes`,
`--max-records`, and `--max-record-bytes`. Output contains only counts, sizes and
SHA-256; no order/account/token values. A PASS means byte-preserving maintenance,
not economic flatness, compatible leases, successful startup or permission to
trade. Gzip CRC and decoded SHA-256 are integrity observations, not authorization.

## Implementation references

The upstream zlib manual defines complete gzip member handling and the difference
between `inflate` and permissive gzip file readers: https://zlib.net/manual.html .
Python's fcntl/os interfaces supply nonblocking advisory locks, descriptor-relative
opens, atomic replace and fsync: https://docs.python.org/3/library/fcntl.html and
https://docs.python.org/3/library/os.html . Build environments now need zlib headers
(`zlib1g-dev` on the existing Ubuntu lanes) and runtime libz in addition to existing
dependencies. No downloaded SDK or vendored compressor is added to the repository.
