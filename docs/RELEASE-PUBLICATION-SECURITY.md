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

After successful publication, the temporary name is removed and the containing directory is synchronized. On a later transaction failure, rollback removes only an output whose device and inode still match the output created by that transaction; independently replaced or independently created bytes are preserved.

## Deterministic hostile tests

`tests/python/test_release_package.py` and `tests/python/test_hepta_preflight.py` inject the following deterministic interleavings:

- a competing package publisher creates the destination after staging and before publication;
- a release payload source changes after it has been snapshotted and hashed;
- the archive pathname is replaced after preflight pins and hashes it but before archive parsing completes;
- a competing preflight receipt publisher creates the destination before publication.

The expected result is fail-closed rejection, preservation of competitor bytes, no orphaned sidecars, archive bytes equal to the bytes hashed into the manifest, and receipt evidence bound to the exact archive bytes inspected.

## Authorization boundary

Passing these checks proves only byte identity, deterministic packaging and local preflight integrity. Repository admission settings remain an optional engineering control and do not grant Broker authority. These checks create neither credentials nor PAPER/LIVE authorization; optional PAPER remains qualification-required and LIVE remains unavailable.
