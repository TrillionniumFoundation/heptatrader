# Release package procedure

Status: CURRENT
Applies to: canonical core and IB PAPER candidate builds

## Objective

Build once, identify once, and carry the same immutable artifact through preflight, qualification, deployment and rollback. Rebuilding later from the same source SHA is not a substitute for using the same package digest.

## Core candidate

```bash
set -euo pipefail
sha="$(git rev-parse HEAD)"
epoch="$(git show -s --format=%ct "$sha")"

cmake -S . -B build/release-core -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_TESTING=ON \
  -DHEPTA_ENABLE_IBAPI=OFF \
  -DHEPTA_BUILD_LEGACY_MONOLITH=OFF \
  -DHEPTA_BUILD_LEGACY_SIMULATOR=OFF
cmake --build build/release-core --parallel 2
ctest --test-dir build/release-core --output-on-failure -L core

python3 scripts/build_release_package.py \
  --build-dir build/release-core \
  --output "dist/heptatrader-0.1.0-beta.1-core.tar.gz" \
  --version 0.1.0-beta.1 \
  --profile core \
  --source-sha "$sha" \
  --source-date-epoch "$epoch"
```

The install root is a stable snapshot boundary. The builder pins the no-follow root and every traversed directory, opens each leaf relative to those descriptors, and revalidates the full root/ancestor/leaf namespace after every copy and again before manifest construction. Concurrent leaf or ancestor replacement fails before any output is published.

The output path, `.sha256` sidecar and `.receipt.json` sidecar must not already exist. The builder refuses replacement rather than silently changing an artifact identity. `manifest.json` is a generated archive name and is forbidden in the install-root payload, including as a directory prefix. The destination parent is part of the security boundary: it must be owned by the invoking operator, must not be group/world writable, and its filesystem must support Linux anonymous `O_TMPFILE` staging plus descriptor-bound `/proc/self/fd` hard-link publication. The builder keeps the fsynced anonymous inode open, atomically links that exact inode to a previously absent destination, verifies destination inode/bytes/mode and the pinned parent identity, and never exposes or cleans up a mutable staging pathname. If a later sidecar publication fails, earlier immutable outputs remain and the incomplete set must be recovered under a fresh basename or removed only after operator verification.

## IB PAPER candidate

Use the separately controlled IB builder, pinned IB SDK tree and pinned Intel BID archive. Configure `HEPTA_ENABLE_IBAPI=ON` and complete the ABI probe before packaging. Then invoke the same builder with `--profile ib-paper`.

An IB PAPER package must contain the real `hepta-ib-executiond`; the installed build metadata must declare that IB API support was compiled. It must still declare PAPER and LIVE authorization false.

## Artifact admission

Before transfer or installation, verify the caller-approved SHA-256:

```bash
python3 scripts/hepta_preflight.py \
  --artifact dist/heptatrader-0.1.0-beta.1-core.tar.gz \
  --expected-sha256 "$(cut -d' ' -f1 dist/heptatrader-0.1.0-beta.1-core.tar.gz.sha256)" \
  --profile core \
  --policy docs/preflight-policy-v1.json \
  --artifact-only \
  --output dist/core-preflight-receipt.json
```

The release package, digest sidecar, package receipt and preflight receipt form one evidence set. Upload or sign them together. A receipt with another artifact digest does not apply.

## Prohibited shortcuts

- Do not rebuild on the PAPER or deployment host.
- Do not copy loose binaries around the package manifest.
- Do not add credentials, tokens, journal data or kill-switch markers to the archive.
- Do not edit an archive after its digest is approved.
- Do not promote an artifact because compilation or artifact-only preflight passed.
- Do not represent a PAPER candidate as LIVE-capable.
