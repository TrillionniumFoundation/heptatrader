# Release engineering and host preflight

Status: CURRENT  
Applies to: repository HEAD  
Implementation: `CMakeLists.txt`, `cmake/HeptaInstall.cmake`, `scripts/build_release_package.py`, `scripts/hepta_preflight.py`, `docs/preflight-policy-v1.json`  
Tests: `tests/python/test_release_package.py`, `tests/python/test_hepta_preflight.py`, `tests/python/test_cmake_install_integration.py`

## Responsibilities

Release engineering turns a reviewed build directory into one canonical install tree and one deterministic, content-addressed archive. Host preflight verifies the archive, its manifest, the installed static boundary, and optionally bounded PAPER-port reachability. Neither component creates or changes trading authority.

The package boundary exists to prevent source SHA, rebuild, staging directory, PAPER-tested binary, and deployed binary from silently becoming different objects. All later qualification evidence must bind the exact package digest or an extracted binary digest from that package.

## Install contract

The top-level CMake project owns and explicitly registers the canonical install rules after every referenced runtime target exists. A core install contains the simulator Execution daemon, Tool Gateway, session control, CLI, preflight command, runtime helpers, systemd and tmpfiles assets, capability policy, build metadata, and current documentation. An IB PAPER install additionally contains the actual IB-linked Execution daemon and fixed PAPER policy.

Files ending in `.example` remain non-secret templates. Broker credentials, Agent session tokens, authorization markers, private keys, live environment files, journal state, and host-generated receipts are never package inputs.

## Deterministic package contract

`scripts/build_release_package.py` accepts either a CMake build directory or an already installed staging root. It requires:

- an exact 40-character source SHA;
- a positive `SOURCE_DATE_EPOCH`;
- an explicit release label and `core` or `ib-paper` profile;
- a new output path that is not replaced in place;
- regular, single-link payload files with bounded sizes and no setuid/setgid bits;
- no symlinks, hard links, private-key suffixes, or non-example `.env` files.

The archive normalizes path order, owner, group, mode, timestamp, tar format, and gzip timestamp. Its manifest binds every payload path, size, mode and SHA-256. The sidecar receipt records the source identity, manifest digest and package digest. Rebuilding the same installed bytes with the same identity inputs must produce the same archive bytes.

The manifest and receipt always declare `authorization_effect=NONE`, `paper_authorized=false`, and `live_authorized=false`. Artifact construction is not qualification.

## Preflight contract

`scripts/hepta_preflight.py`, installed as `hepta-preflight`, validates without extracting the package:

1. artifact file identity and caller-supplied SHA-256;
2. bounded member count and unpacked size;
3. canonical paths and a single package root;
4. rejection of links, devices and duplicate members;
5. strict JSON without duplicate keys or non-finite numbers;
6. manifest-to-payload size, mode, timestamp and digest equality;
7. profile-required files and installed build metadata;
8. prohibition on PAPER/LIVE authorization claims.

Artifact-only mode is suitable for CI and admission. Static-host mode additionally checks installed files, Linux commands and, for IB PAPER, distinct host UIDs and a safe root-owned kill-switch marker. An explicitly requested TCP probe is restricted to policy-approved loopback PAPER ports and proves reachability only; it does not prove account mode, credentials, order behavior, or broker qualification.

## State and persistence

Package outputs are created atomically and are never silently overwritten. The package receipt is mode `0600`. The preflight receipt is also atomically created with mode `0600` and includes a host fingerprint that hashes, rather than exposes, machine identity.

Build and preflight receipts are evidence inputs. They are not mutable runtime state and must not be used as session tokens, Broker credentials, kill-switch state, or execution commands.

## Failure semantics

Any path escape, symlink, hard link, special file, unexpected authorization claim, invalid digest, manifest mismatch, missing required file, unsafe kill-switch marker, missing host identity, unsupported Broker endpoint, malformed JSON, non-finite number, size overflow, output replacement attempt, unregistered install module, or incomplete installed inventory fails closed.

A failed preflight leaves the host and package unchanged. A successful artifact-only preflight does not imply the host is installed. A successful static-host preflight does not imply PAPER qualification. LIVE remains unavailable.

## Observability

Receipts expose release label, source SHA, source epoch, package and manifest digests, profile, file count, check IDs, typed PASS/FAIL/SKIP results, bounded details, host platform and hashed machine identity. They never include credential values or session tokens.

## Test expectations

Unit tests cover reproducible archive bytes, sorted manifests, authorization non-escalation, overwrite refusal, private-key paths, symlinks, hard links, invalid source identity, digest mismatch, required-file absence, path traversal, archive symlinks, duplicate JSON keys and forged LIVE claims.

The install integration test consumes the same already-built canonical `build/core` directory, verifies its exact CMake profile, executes `cmake --install` under a fresh `DESTDIR`, asserts every policy-required regular file, rejects an IB-enabled daemon in the core profile, and checks installed build metadata. This prevents an install module that exists in source but is never registered from passing CI.

A release workflow must build the canonical CMake targets, install into an empty staging root, package that root, run artifact-only preflight, and retain the package, digest and receipts as one immutable evidence set.

## Known limitations

Repository admission settings are optional engineering controls for this owner-operated system and are not Broker authorization. Static preflight deliberately stops before credentials, TWS/IB Gateway sessions and PAPER account effects. If the owner later enables optional IB PAPER, the exact-current-main qualification workflow must bind Broker-observed evidence to the unchanged package digest. Until then `paper_authorized=false`; LIVE remains unavailable.
