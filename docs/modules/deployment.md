# systemd deployment assets

Status: CURRENT  
Applies to: repository HEAD  
Implementation: `systemd/`, `tmpfiles.d/`, `scripts/check_systemd_units.py`
Tests: `tests/python/test_systemd_units.py`, `tests/python/test_documentation_control_plane.py`, `tests/python/test_hepta_broker_egress_policy.py`, `tests/python/test_hepta_broker_egress_policy_atomic.py`

## Scope

The repository contains source deployment units and examples for simulator execution, IB PAPER execution, the Tool Gateway, event sockets, session-supervisor sockets, service identities, trust-domain configuration, broker egress policy and runtime directories.

`scripts/check_systemd_units.py` is the unprivileged deployment gate. It parses
every checked-in unit and verifies executable paths, identity separation,
socket associations, simulator network isolation, IB PAPER policy ordering,
credential declarations, and deny-all stop behavior. It never contacts PID 1
or changes host state. A target host should additionally run
`systemd-analyze verify` and a disposable startup/readiness probe after
installing the exact artifact; those checks are host evidence and cannot be
replaced by a source-only green build.

The top-level CMake project installs the maintained runtime into a canonical tree. [`release-package.md`](../operations/release-package.md) builds a deterministic archive from that tree and validates it with a read-only machine-readable preflight. These assets still do not prove that a particular host, GitHub environment or Broker account is qualified.

## Identity separation

At minimum, deployment separates:

- Agent identity;
- Tool Gateway identity;
- simulator Execution identity;
- IB PAPER Execution identity;
- root/operator control path.

Only the IB PAPER Execution identity may receive Broker credentials or broker-port egress. The Agent and Gateway must be unable to reach protected Broker API ports.

## Filesystem contract

State, runtime sockets, session tokens, credentials, control directories, installed package files and exported read-only evidence use separate paths and ownership. Units must reject unsafe symlinks, hard links, modes, owners or path substitution. `tmpfiles.d` creates only non-secret directories and must not synthesize credentials or authorization markers.

Release package files are root-owned immutable deployment inputs. Journals, lease stores, tokens, credentials, kill-switch markers and qualification receipts are host state and must never be placed in the release archive.

An IB-enabled canonical install includes the Broker egress policy at `/usr/share/heptatrader/hepta-broker-network-policy-v1.json`; the IB preflight package inventory requires that exact leaf. The root policy service accepts only that logical path, a root/root mode-`0644` regular single-link file under non-writable root-owned ancestors, the compiled SHA-256 `5eddd44a588ac3269804cb62adb19c3879febce8569df30ab86886028e969e6b`, and the compiled canonical semantics. Descriptor, final path and parent namespace identities are revalidated after the bounded read. A different host-specific qualification policy belongs to an external root-owned deployment mechanism and cannot be substituted into the canonical unit by changing its command line.

## Unit ordering

The IB PAPER service depends on the Broker egress policy, runtime directory creation, socket ownership, credential delivery and kill-switch control directory. Stopping or failing the network policy must tighten to deny-all. Policy read, identity, digest, semantic or nft application failure independently attempts the compiled deny-all ruleset and leaves the unit failed; explicit stop-time deny-all does not depend on reading the policy file. nftables replacement uses JSON table inventory, bounded present/absent race retries and exact structural JSON readback; localized diagnostics are never control flow. Gateway startup does not imply an active session, and an active session does not imply PAPER qualification.

## Configuration

Files ending in `.example` are templates. A deployment copies them to root-owned host paths, replaces placeholders, validates the resulting contract and records a digest. Secrets use systemd credentials or an equivalent protected facility rather than world-readable environment files.

Run `hepta-preflight` twice: artifact-only before privileged transfer and static-host mode after installing the exact approved archive. Then run the unprivileged unit lint, `systemd-analyze verify`, and a disposable service startup/readiness probe. The receipt always has `authorization_effect=NONE`; the protected Broker campaign remains a separate step.

## Upgrade and rollback

Deploy immutable artifacts by version and digest, stop mutation admission, reconcile and close active sessions, engage the kill switch for Broker-backed upgrades, install the candidate, run static preflight and startup checks, and only then restore bounded authority after the applicable qualification. Rollback uses the prior artifact with the same persistent-schema compatibility; it must not roll back across an incompatible journal/lease schema without migration.

## Observability

Units should expose service state, restart count, readiness, socket ownership, credential availability without value disclosure, network-policy state, kill-switch state and exact artifact digest. Preflight and qualification receipts should be indexed by the same package SHA-256. Host checks are operational evidence, not repository-source evidence.

## Known limitations

The repository still does not create organization teams, branch rulesets, protected environments, trusted runner assignments, Broker credentials, TWS/IB Gateway or PAPER accounts. It also lacks a distribution-specific post-install transaction and log-rotation policy for every supported distribution. Until those external controls and Broker-observed qualification are supplied, PAPER remains qualification-gated and LIVE remains unavailable.

## Detailed development reference

See [`installed-process-acceptance.md`](../technical/installed-process-acceptance.md) for concrete contracts, executable acceptance and the limits of that evidence.

## Artifact-to-manager integration

The canonical `/usr/bin` daemon paths and packaged private network-policy helper
are validated against fresh CMake installation, including template units. Core
installs no IB authority units or IB tmpfiles rule. The
[systemd simulator acceptance](../technical/systemd-simulator-acceptance.md)
uses real PID 1 on a disposable host, without copying loose build binaries,
patching unit paths or weakening daemon sandboxing. Target-host and Broker
acceptance remain distinct from this test.
