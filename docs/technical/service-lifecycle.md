# Service lifecycle technical reference

Status: CURRENT  
Applies to: systemd units, Unix sockets, session supervisor, Gateway, simulator Execution, and IB PAPER Execution

## Ordered startup

Canonical startup is:

1. create non-secret runtime directories with `tmpfiles.d`;
2. engage and validate the Broker kill switch for Broker-backed profiles;
3. install and verify the exact release artifact;
4. apply and structurally verify the Broker egress policy where applicable;
5. start Execution sockets and Execution service;
6. complete journal replay and authoritative recovery;
7. start session supervisor and Tool Gateway;
8. expose readiness only after identity, socket ownership, and snapshot barriers pass.

Starting a process is not readiness. An active session is not PAPER qualification.

## Readiness and health

Readiness requires the expected artifact digest, healthy journal, current configuration digest, correct peer identities, no unresolved send that permits risk increase, and profile-specific authoritative state. Health reports degraded versus mutation-blocked conditions separately. Secrets are represented only by availability and identity metadata, never values.

## Shutdown and upgrade

Shutdown first closes new mutation admission, drains accepted work, reconciles active orders, and confirms final state before terminating sockets. Broker-backed upgrades keep the kill switch engaged. A candidate is installed in a new immutable slot; the `current` pointer changes atomically only after verification.

## Rollback

Rollback selects the prior compatible slot, restarts the same service graph, replays journals, and reconciles again. It is forbidden across an incompatible journal or lease schema without a tested migration. The release simulator smoke executes the candidate through the installed E2E binary and separately verifies atomic rollback/re-promotion pointer transitions without Broker access; it does not claim N-1 compatibility for an identical-artifact seed slot.

## Failure handling

Any failed artifact check, socket identity, egress-policy readback, replay, snapshot barrier, readiness probe, simulator smoke, or rollback smoke leaves risk increase closed. Operator diagnostics must identify the failed lifecycle phase and the last known artifact/configuration digest.
