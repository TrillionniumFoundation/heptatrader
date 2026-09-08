# systemd deployment assets

Status: CURRENT  
Applies to: repository HEAD  
Implementation: `systemd/`, `tmpfiles.d/`  
Tests: `tests/python/test_documentation_control_plane.py`

## Scope

The repository contains source deployment units and examples for simulator execution, IB PAPER execution, the Tool Gateway, event sockets, session-supervisor sockets, service identities, trust-domain configuration, broker egress policy and runtime directories.

The top-level CMake project now installs the maintained runtime into a canonical tree. [`release-engineering.md`](release-engineering.md) builds a deterministic archive from that tree and validates it with a read-only machine-readable preflight. These assets still do not prove that a particular host, GitHub environment or Broker account is qualified.

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

## Unit ordering

The IB PAPER service depends on the Broker egress policy, runtime directory creation, socket ownership, credential delivery and kill-switch control directory. Stopping or failing the network policy must tighten to deny-all. Gateway startup does not imply an active session, and an active session does not imply PAPER qualification.

## Configuration

Files ending in `.example` are templates. A deployment copies them to root-owned host paths, replaces placeholders, validates the resulting contract and records a digest. Secrets use systemd credentials or an equivalent protected facility rather than world-readable environment files.

Run `hepta-preflight` twice: artifact-only before privileged transfer and static-host mode after installing the exact approved archive. The receipt always has `authorization_effect=NONE`; the protected Broker campaign remains a separate step.

## Upgrade and rollback

Deploy immutable artifacts by version and digest, stop mutation admission, reconcile and close active sessions, engage the kill switch for Broker-backed upgrades, install the candidate, run static preflight and startup checks, and only then restore bounded authority after the applicable qualification. Rollback uses the prior artifact with the same persistent-schema compatibility; it must not roll back across an incompatible journal/lease schema without migration.

## Observability

Units should expose service state, restart count, readiness, socket ownership, credential availability without value disclosure, network-policy state, kill-switch state and exact artifact digest. Preflight and qualification receipts should be indexed by the same package SHA-256. Host checks are operational evidence, not repository-source evidence.

## Known limitations

The repository still does not create organization teams, branch rulesets, protected environments, trusted runner assignments, Broker credentials, TWS/IB Gateway or PAPER accounts. It also lacks a distribution-specific post-install transaction and log-rotation policy for every supported distribution. Until those external controls and Broker-observed qualification are supplied, PAPER remains qualification-gated and LIVE remains unavailable.
