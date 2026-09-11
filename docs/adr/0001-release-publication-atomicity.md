# ADR 0001: Atomic release publication and pinned input bytes

Status: Accepted
Date: 2026-09-09

## Context

The release builder and preflight verifier cross a local filesystem trust boundary. A pathname can be replaced after a preliminary existence or metadata check, and an output can be created by another publisher after a check but before publication. A digest computed from one pathname open cannot safely describe bytes parsed from a later pathname open.

## Decision

Release payloads, policies and package archives are consumed from one no-follow descriptor-backed snapshot. Digest calculation and parsing or archive construction use the same snapshotted bytes. The original descriptor and pathname identity are checked for replacement or metadata drift.

Package, checksum and receipt outputs are flushed to private same-directory files and published with an atomic no-replace operation relative to a pinned directory descriptor. A pre-existing or concurrently created destination causes a fail-closed result and is never overwritten. Publication is per-file atomic rather than a multi-file transaction; if a later sidecar loses a race, earlier immutable outputs remain and recovery requires a fresh basename or operator-verified cleanup.

## Consequences

The package manifest is bound to the exact payload bytes archived, and a preflight receipt is bound to the exact package bytes inspected. Concurrent publishers can race safely: at most one publishes each destination, losing publishers fail, independently created bytes remain intact, and partial evidence sets are never described as complete.

The implementation remains Linux-oriented, matching the canonical deployment target. These integrity guarantees have no PAPER or LIVE authorization effect. Repository admission settings are optional engineering controls; optional PAPER qualification remains a separate fail-closed Broker boundary and LIVE remains unavailable.

The archive format is canonical USTAR. Generated manifest names and file-prefix collisions are rejected before packaging. Preflight uses a streaming parser with compressed, decompressed, member-count, member-size and total-payload ceilings; GNU/PAX extension metadata is forbidden and rejected before its body is consumed.

## Verification

`tests/python/test_release_package.py` and `tests/python/test_hepta_preflight.py` deterministically cover competing publishers, payload mutation after snapshot, and archive pathname replacement during preflight. The ordinary release, preflight, CMake install, documentation, ownership, Python and C++ suites continue to run on the exact pull-request head.
