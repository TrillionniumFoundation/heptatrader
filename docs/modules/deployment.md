# systemd deployment assets

Status: CURRENT  
Applies to: repository HEAD  
Implementation: `systemd/`, `tmpfiles.d/`, `cmake/HeptaInstall.cmake`  
Tests: `tests/python/test_documentation_control_plane.py`, `.github/workflows/install-package-smoke.yml`

## Scope

The repository contains source deployment units and examples for simulator execution, IB PAPER execution, the Tool Gateway, event sockets, session-supervisor sockets, service identities, trust-domain configuration, broker egress policy, and runtime directories.

The canonical CMake install manifest packages the SDK-free runtime, documentation, policy/schema files and deployment inputs. It does not silently materialize host identities, credentials, authorization markers, network policy or service enablement. Those remain explicit, independently verified host controls.

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

The core install profile places deployment assets under the HeptaTrader data tree rather than activating them. A host integrator copies or maps reviewed units and tmpfiles entries into distribution-specific root-owned locations, then records exact content digests and effective ownership. Installed examples are never interpreted as live secrets.

## Unit ordering

The IB PAPER service depends on the broker egress policy, runtime directory creation, socket ownership, credential delivery, and kill-switch control directory. Stopping or failing the network policy must tighten to deny-all. Gateway startup does not imply an active session, and an active session does not imply PAPER qualification.

## Configuration

Files ending in `.example` are templates. A deployment copies them to root-owned host paths, replaces placeholders, validates the resulting contract, and records a digest. Secrets use systemd credentials or an equivalent protected facility rather than world-readable environment files.

The package's non-secret policy and schema files are versioned inputs. Effective host configuration must bind its account, execution domain, socket paths, service UIDs, profile digest and installed artifact digest to those inputs. Unknown keys, conflicting sources or an IB executable appearing in an SDK-free install fail validation.

## Package and installation verification

`cmake/HeptaInstall.cmake` defines runtime, documentation and deployment components. `install-package-smoke` configures an SDK-free Release build, installs into an empty root, verifies the expected core binaries and policy files, proves that no IB execution binary entered the core profile, rejects installed symlinks and scans for obvious private-key or real-account material. CPack opens every generated archive before the job succeeds.

IB PAPER packaging is a separate component available only when `HEPTA_ENABLE_IBAPI=ON` has already passed the pinned SDK and Decimal ABI checks. Packaging that component still does not prove a PAPER account, protected runner, credential, network boundary or campaign receipt.

## Upgrade and rollback

Deploy immutable artifacts by version/digest, stop mutation admission, reconcile and close active sessions, engage the kill switch for broker-backed upgrades, install the candidate, run startup checks, and only then restore bounded authority. Rollback uses the prior artifact with the same persistent-schema compatibility; it must not roll back across an incompatible journal/lease schema without migration.

## Observability

Units should expose service state, restart count, readiness, socket ownership, credential availability without value disclosure, network-policy state, kill-switch state, and exact artifact digest. Host checks are operational evidence, not repository-source evidence.

Package verification records the exact source SHA, installed paths, archive checksums and component set. Runtime logs and metrics must identify the installed artifact digest so incident evidence can be joined to source and qualification receipts.

## Known limitations

The repository now provides a canonical core install/package path, but host provisioning remains distribution- and environment-specific. It does not yet create OS users/groups, activate units, install a universal hardening profile, configure log rotation, deliver credentials or prove nftables state on every supported distribution. Those controls must be implemented by a reviewed host profile and exercised in an isolated VM before PAPER qualification. Container images and RPM packaging are not yet canonical.
