# Release publication and input pinning

Status: CURRENT
Applies to: deterministic release packages and machine-readable host preflight receipts
Implementation: `scripts/build_release_package.py`, `scripts/hepta_preflight.py`, `scripts/hepta_preflight_core.py`
Tests: `tests/python/test_release_package.py`, `tests/python/test_hepta_preflight.py`, `tests/python/test_preflight_complete_namespace.py`, `tests/python/test_cmake_install_integration.py`

## Security objective

A release digest, manifest, inspected archive and emitted receipt must refer to one exact byte sequence. A concurrent process must never be able to replace an existing package, checksum sidecar or receipt between a preliminary existence check and publication.

## Input contract

The release builder opens the install root component by component with no-follow directory descriptors and keeps that root pinned for the complete payload snapshot. Every child directory is opened relative to its pinned parent; every leaf is opened nonblocking and no-follow relative to that directory. The implementation binds root, directory and leaf objects to device, inode, mode, link count, size, modification time and change time, copies each leaf once into an anonymous immutable snapshot, and computes the digest while copying.

After every copy, the still-open leaf descriptor, leaf name under the held parent, freshly reopened install-root path, complete logical directory chain and leaf name under that fresh chain must all resolve to the pinned objects. A final full-tree namespace pass repeats the directory and leaf checks before the payload snapshot is committed. Leaf replacement, ancestor-directory replacement, root replacement, in-place mutation, link-count change, disappearance or metadata drift fails closed. The policy and archive inspection paths use the same descriptor/path identity discipline; hashing one pathname open and parsing another is forbidden.

## Publication contract

Package, checksum and receipt outputs are staged as private same-directory files, synchronously flushed, and published with an atomic no-replace link operation relative to a pinned directory descriptor. Existing destinations—including destinations created by a competing publisher after staging began—are never overwritten.

After successful publication, the temporary name is removed and the containing directory is synchronized. Publication is atomic per output, not transactional across the package and two sidecars. If a later no-replace publication fails, already published immutable outputs are deliberately retained; pathname-based rollback is not attempted because it could delete another writer's file. The evidence set is complete only when the package, checksum and receipt all exist and cross-bind. Recovery uses a fresh output basename or an operator-verified removal of the incomplete set.

## Bounded archive parser

The builder reserves `manifest.json`, rejects generated-name and file-prefix collisions before payload snapshots, emits canonical USTAR without GNU/PAX extension records, and asserts global member-name uniqueness. The public preflight entry point loads the unchanged bounded parser core through one stable regular-file descriptor and adds a complete namespace admission check. Preflight validates the generated-plus-payload file namespace and rejects exact, ancestor, or descendant collisions before consuming any payload body. The check enumerates every slash-delimited ancestor rather than relying on adjacent sorted names, so `a`, `a-legal`, `a/child` is rejected while `a` plus `a-legal` remains valid. Its compiled member-count, member-size and total-unpacked ceilings equal the installed preflight policy; generated manifest bytes and its archive member are included in those budgets, so a successfully built package is not rejected merely because producer and consumer count different objects. Preflight checks the compressed file-size ceiling before hashing, then parses the gzip stream incrementally. Member count and per-member/total sizes are checked before body consumption; total decompressed tar bytes are bounded; GNU/PAX long-name and extended-header metadata are rejected immediately with a compiled zero-byte extension-metadata allowance. Regular-file archive names are exact canonical POSIX paths and cannot use trailing-slash aliases. Producer and consumer share one path-byte policy: backslashes, bytes below `0x20`, and byte `0x7f` are rejected in every leaf and directory component, while other valid UTF-8 names remain supported. No eager `getmembers()` materialization is used.

## Deterministic hostile tests

`tests/python/test_release_package.py`, `tests/python/test_hepta_preflight.py` and `tests/python/test_preflight_complete_namespace.py` inject the following deterministic interleavings and namespace attacks:

- a competing package publisher creates the destination after staging and before publication;
- a release payload source changes after it has been snapshotted and hashed;
- a payload leaf pathname is replaced after its bytes are copied but before namespace commitment;
- an ancestor directory is renamed and replaced after a descendant leaf is copied;
- the archive pathname is replaced after preflight pins and hashes it but before archive parsing completes;
- a competing preflight receipt publisher creates the destination before publication;
- an otherwise valid artifact declares non-adjacent payload names with an ancestor/descendant collision;
- install roots contain a backslash, control character, or DEL in either a leaf or directory component, which must fail before package, digest, or receipt publication.

The expected result is fail-closed rejection, preservation of competitor bytes, explicit retention of any earlier per-file publication after a later failure, archive bytes equal to the bytes hashed into the manifest, receipt evidence bound to the exact archive bytes inspected, and rejection of a colliding manifest before any payload body is consumed.

## Authorization boundary

Passing these checks proves only byte identity, deterministic packaging and local preflight integrity. Repository admission settings remain an optional engineering control and do not grant Broker authority. These checks create neither credentials nor PAPER/LIVE authorization; optional PAPER remains qualification-required and LIVE remains unavailable.
