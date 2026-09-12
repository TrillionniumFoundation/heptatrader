# Deployment preflight

Status: CURRENT
Applies to: immutable core and IB PAPER release packages

## Evidence boundary

`hepta-preflight` is read-only. It validates the package and selected static host properties, writes a machine-readable receipt, and exits non-zero on failure. It does not install files, change firewall rules, create users, deliver credentials, open sessions, disarm the kill switch, place orders, or authorize PAPER/LIVE.

`hepta-preflight` is the only supported command-line entry point. The parser and verifier implementation is installed as the private regular file `libexec/heptatrader/hepta-preflight-core.py`, outside the public binary namespace. Direct execution of either the source or installed core exits with status `2` before argument parsing, package inspection, receipt publication, or optional network probing. The public wrapper loads that core through a stable no-follow descriptor. Archive namespace, release-label and exact root/version/profile rules are defined in the core itself rather than patched by the wrapper.

## Artifact-only check

Run before an artifact reaches a privileged host:

```bash
hepta-preflight \
  --artifact /srv/releases/heptatrader-0.1.0-beta.1-core.tar.gz \
  --expected-sha256 <approved-package-sha256> \
  --profile core \
  --policy /usr/share/heptatrader/preflight-policy-v1.json \
  --artifact-only \
  --output /srv/releases/core-preflight.json
```

This checks the compressed size before hashing, incrementally parses canonical USTAR under decompressed/member/total ceilings, rejects extension metadata before reading its body, and validates the archive root, manifest-first ordering, paths, types, every payload digest/mode/timestamp, profile-required files, installed build metadata, and prohibition on authorization claims.

## Static host check

After installing the exact artifact into the canonical `/usr` tree, rerun without `--artifact-only`:

```bash
sudo hepta-preflight \
  --artifact /srv/releases/heptatrader-0.1.0-beta.1-core.tar.gz \
  --expected-sha256 <approved-package-sha256> \
  --profile core \
  --policy /usr/share/heptatrader/preflight-policy-v1.json \
  --output /var/lib/heptatrader/evidence/core-host-preflight.json
```

The static check validates Linux, required commands and the installed regular files represented by the package profile. It does not start services.

## IB PAPER static check

Provide the deployment-assigned numeric UIDs and the already engaged, root-owned kill-switch marker:

```bash
sudo hepta-preflight \
  --artifact /srv/releases/heptatrader-0.1.0-beta.1-ib-paper.tar.gz \
  --expected-sha256 <approved-package-sha256> \
  --profile ib-paper \
  --policy /usr/share/heptatrader/preflight-policy-v1.json \
  --execution-uid "$(id -u hepta-ib-exec)" \
  --gateway-uid "$(id -u hepta-gateway)" \
  --kill-switch-path /run/hepta/ib-paper-control/kill-switch \
  --output /var/lib/heptatrader/evidence/ib-paper-host-preflight.json
```

The logical kill-switch path is non-relaxable: `/run/hepta/ib-paper-control/kill-switch`. Under a test or offline `--host-root`, that exact logical path is resolved below the pinned host root. The control directory must be root-owned, bound to the execution primary group, mode `0750`, and contain no subdirectories. The marker must be a stable root/execution-group regular single-link file, mode `0440`, with exact bytes `engaged`, matching `tmpfiles.d/heptatrader-ib-paper.conf`. Every ancestor and the final marker are opened through no-follow descriptors and revalidated after the content read; an unrelated root-owned file, alternate path, symlink, metadata mismatch, content mismatch or identity change fails the static check.

A bounded TCP reachability check can be requested with `--probe-broker --broker-host 127.0.0.1 --broker-port 4002`. The package policy may narrow access, but it cannot widen the compiled literal-address ceiling of `127.0.0.1`/`::1` and PAPER ports `4002`/`7497`; hostnames and DNS resolution are not admitted. Policy and package identity must succeed before any connection attempt. TCP success is not evidence that the session is PAPER, the account is correct, callbacks are healthy, or mutations are qualified.

## Required follow-on checks

A PASS static receipt must be followed by the protected IB qualification workflow, which verifies the effective profile, credential boundary, broker session/account mode, authoritative refresh barriers, journal ordering, idempotency, cancel/recovery behavior and final reconciliation against the same artifact digest.

Any digest change, package replacement, host identity change, policy change, failed check, missing evidence or uncertain broker state invalidates the preflight for promotion. LIVE remains unavailable.

## Exact binding guarantees

The caller-supplied policy must be byte-identical to the policy inside the approved package. Archive limits remain below compiled, non-relaxable ceilings. The compressed-file bound is checked before hashing; a streaming USTAR parser stops at the member-count boundary before body reads, bounds total decompressed bytes, and gives GNU/PAX extension metadata a compiled allowance of zero. Artifact and policy paths are traversed component by component with no-follow directory descriptors, and the artifact is hashed and parsed through one pinned descriptor.

Static-host mode compares the complete HeptaTrader-managed installed inventory with the package manifest. Every managed file must have the approved bytes, size and mode and must share the deployment-root owner/group; stale extra HeptaTrader binaries, units, helpers, policies or documentation fail the check. The installed build metadata is parsed again and must match the package profile and release identity.

Receipt publication requires an operator-owned parent directory that is not group/world writable and a filesystem supporting anonymous `O_TMPFILE` staging. The receipt is fsynced through an unnamed descriptor, linked no-replace from that descriptor, and verified by inode, bytes, mode and pinned parent identity. No mutable staging pathname exists for another publisher to replace or for cleanup to unlink. A competing writer that first claims the final output name wins without being overwritten; the preflight fails instead of replacing existing evidence.
