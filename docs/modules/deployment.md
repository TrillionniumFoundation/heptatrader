# systemd deployment assets

Status: CURRENT  
Applies to: repository HEAD  
Implementation: `systemd/`, `tmpfiles.d/`  
Tests: `tests/python/test_documentation_control_plane.py`

## Scope

The repository contains source deployment units and examples for simulator execution, IB PAPER execution, the Tool Gateway, event sockets, session-supervisor sockets, service identities, trust-domain configuration, broker egress policy, and runtime directories.

These files are deployment inputs, not proof that a host is correctly installed. The current CMake project does not install a complete production tree.

## Identity separation

At minimum, deployment separates:

- Agent identity;
- Tool Gateway identity;
- simulator Execution identity;
- IB PAPER Execution identity;
- root/operator control path.

Only the IB PAPER Execution identity may receive broker credentials or broker-port egress. The Agent and Gateway must be unable to reach protected broker API ports.

## Filesystem contract

State, runtime sockets, session tokens, credentials, control directories, and exported read-only evidence use separate paths and ownership. Units must reject unsafe symlinks, hard links, modes, owners, or path substitution. `tmpfiles.d` creates only non-secret directories and must not synthesize credentials or authorization markers.

## Unit ordering

The IB PAPER service depends on the broker egress policy, runtime directory creation, socket ownership, credential delivery, and kill-switch control directory. Stopping or failing the network policy must tighten to deny-all. Gateway startup does not imply an active session, and an active session does not imply PAPER qualification.

## Configuration

Files ending in `.example` are templates. A deployment copies them to root-owned host paths, replaces placeholders, validates the resulting contract, and records a digest. Secrets use systemd credentials or an equivalent protected facility rather than world-readable environment files.

## Upgrade and rollback

Deploy immutable artifacts by version/digest, stop mutation admission, reconcile and close active sessions, engage the kill switch for broker-backed upgrades, install the candidate, run startup checks, and only then restore bounded authority. Rollback uses the prior artifact with the same persistent-schema compatibility; it must not roll back across an incompatible journal/lease schema without migration.

## Observability

Units should expose service state, restart count, readiness, socket ownership, credential availability without value disclosure, network-policy state, kill-switch state, and exact artifact digest. Host checks are operational evidence, not repository-source evidence.

## Known limitations

The repository does not provide a canonical package, container image, full install target, OS hardening profile, log rotation policy, or host qualification script for every supported distribution. Until those are supplied, deployment remains operator-managed and PAPER stays qualification-gated.
