# Release publication and input pinning

Status: CURRENT
Applies to: deterministic release packages and machine-readable host preflight receipts
Implementation: `scripts/build_release_package.py`, `scripts/hepta_preflight.py`
Tests: `tests/python/test_release_package.py`, `tests/python/test_hepta_preflight.py`, `tests/python/test_cmake_install_integration.py`

## Security objective

A release digest, manifest, inspected archive and emitted receipt must refer to one exact byte sequence. A concurrent process must never be able to replace an existing package, checksum sidecar or receipt between a preliminary existence check and publication.

## Input contract

Release payload files, the preflight policy and the release archive are opened through a no-follow parent directory descriptor. The implementation binds the opened descriptor to its device, inode, mode, link count, size, modification time and change time, copies the bytes once into an anonymous immutable snapshot, computes the digest while copying, and consumes that same snapshot for manifest construction, archive generation or archive inspection.

The original descriptor and path identity are checked again after the copy or inspection. Path replacement, in-place mutation, link-count change, metadata change, disappearance or size drift fails closed. Hashing one pathname open and parsing another is forbidden.

## Publication contract

Package, checksum and receipt outputs are staged as private same-directory files, synchronously flushed, and published with an atomic no-replace link operation relative to a pinned directory descriptor. Existing destinations—including destinations created by a competing publisher after staging began—are never overwritten.

After successful publication, the temporary name is removed and the containing directory is synchronized. Publication is atomic per output, not transactional across the package and two sidecars. If a later no-replace publication fails, already published immutable outputs are deliberately retained; pathname-based rollback is not attempted because it could delete another writer's file. The evidence set is complete only when the package, checksum and receipt all exist and cross-bind. Recovery uses a fresh output basename or an operator-verified removal of the incomplete set.

## Bounded archive parser

The builder reserves `manifest.json`, rejects generated-name and file-prefix collisions before payload snapshots, emits canonical USTAR without GNU/PAX extension records, and asserts global member-name uniqueness. Preflight checks the compressed file-size ceiling before hashing, then parses the gzip stream incrementally. Member count and per-member/total sizes are checked before body consumption; total decompressed tar bytes are bounded; GNU/PAX long-name and extended-header metadata are rejected immediately with a compiled zero-byte extension-metadata allowance. No eager `getmembers()` materialization is used.

## Deterministic hostile tests

`tests/python/test_release_package.py` and `tests/python/test_hepta_preflight.py` inject the following deterministic interleavings:

- a competing package publisher creates the destination after staging and before publication;
- a release payload source changes after it has been snapshotted and hashed;
- the archive pathname is replaced after preflight pins and hashes it but before archive parsing completes;
- a competing preflight receipt publisher creates the destination before publication.

The expected result is fail-closed rejection, preservation of competitor bytes, explicit retention of any earlier per-file publication after a later failure, archive bytes equal to the bytes hashed into the manifest, and receipt evidence bound to the exact archive bytes inspected.

## Authorization boundary

Passing these checks proves only byte identity, deterministic packaging and local preflight integrity. Repository admission settings remain an optional engineering control and do not grant Broker authority. These checks create neither credentials nor PAPER/LIVE authorization; optional PAPER remains qualification-required and LIVE remains unavailable.
