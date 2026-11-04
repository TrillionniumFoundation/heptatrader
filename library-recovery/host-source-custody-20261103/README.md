# Desktop source custody — 2026-11-03 capture

This isolated archive preserves 58 source references and 40 distinct Git blobs for `TrillionniumFoundation/heptatrader`.

Source bytes are stored under `contexts/`, separated by capture scope and original worktree context. `SOURCE_MANIFEST.json` maps every archived file to its original path, captured revision, byte count, SHA-256, and Git blob SHA-1. The manifest also records the original frozen snapshot and code-only package hashes.

The archive preserves source files, captured source patches, and small explicitly synthetic fixtures. It does not reconstruct the original captured commit history or certify scientific replay, tests, or production readiness. Captured sources were hashed and checked for recognizable credential literals; they were not executed during this recovery.

The archive commit is based on `ffec655e69c610e0c4af41c00e3565e84edeb940` and adds only this custody directory. No existing development branch was modified.
