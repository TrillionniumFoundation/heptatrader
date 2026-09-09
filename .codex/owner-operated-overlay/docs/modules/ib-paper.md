# IB PAPER runtime

Status: QUALIFICATION_REQUIRED  
Applies to: repository HEAD  
Implementation: `HeptaTrade/adapter_ib/`, `HeptaTrade/execution/hepta_ib_executiond.cpp`, `.github/workflows/ib-paper-qualification.yml`, `scripts/build_ib_candidate_artifact.sh`, `scripts/verify_ib_candidate_artifact.py`, `scripts/run_ib_paper_artifact_qualification.sh`, `scripts/verify_ib_paper_qualification.py`, `systemd/hepta-execution-ib-paper.service`, `docs/ib-paper-profile-policy-v1.json`  
Tests: `tests/ib_order_lifecycle_tests.cpp`, `tests/ib_live_terminal_reconciliation_tests.cpp`, `tests/ib_paper_kill_switch_tests.cpp`, `tests/execution_coordinator_tests.cpp`, `tests/python/test_canonical_ib_paper_profile.py`, `tests/python/test_ib_paper_qualification.py`, `tests/python/test_qualification_trust_boundary.py`, `tests/python/test_ib_workflow_interfaces.py`

## Scope

The IB runtime is a fixed PAPER-only Broker authority candidate. It may connect only to the configured loopback TWS/IB Gateway PAPER listener using a dedicated execution identity and a separately supplied, pinned IB C++ API source/runtime.

This is an owner-operated system. Repository teams, CODEOWNERS, pull-request approval counts, branch rules, Merge Queue, protected governance environments, and governance receipts are not qualification inputs. A qualifying workflow may run only for the exact current `main` SHA, and movement of `main` during the campaign invalidates the result.

## Composition

`hepta-ib-executiond` owns the IB API session and callbacks, authoritative quote subscriptions, account/position/order/execution refresh barriers, order identity and correlation, venue-specific validation, journaled send/cancel/flatten operations, reconnect, and terminal recovery. The Tool Gateway communicates through the typed Execution protocol and cannot link or call the IB API.

The SDK uses IEEE decimal64 BID quantities and the real Intel Decimal Floating-Point Math Library. Native builds require an explicit archive and pass the SDK/BID ABI probe. The SDK snapshot digest covers the Decimal headers and `libbid.a`.

## Fixed profile

The source-controlled policy is [`../ib-paper-profile-policy-v1.json`](../ib-paper-profile-policy-v1.json). It binds PAPER mode, `DU` account, loopback endpoint, client ID, state/control directories, profile credential, allowed security/order types, quantity/notional/rate limits, active-order limit, gross-position limit, and quote freshness.

Until aggregate pending-order notional is authoritative across contracts, qualification permits exactly one active order and one CASH quote contract. Multi-contract and STK profiles are not qualified.

## Owner-operated qualification boundary

Qualification keeps two distinct trust domains:

1. a no-secret builder receives the exact current `main` source, a digest-pinned OCI image, a read-only SDK/BID snapshot, and a bounded writable filesystem;
2. a PAPER execution identity receives only the verified artifact and runs it through a separately pinned external harness with PAPER-only Broker access.

The workflow confirms that the requested SHA equals the dispatching `main` SHA and independently reads `refs/heads/main` before and after the Broker campaign. It does not inspect pull requests, reviews, teams, branch rules, environments, or Merge Queue state.

The final receipt binds source SHA, artifact and executable digests, builder image/toolchain/resource policy, SDK/BID digest, harness digest, effective PAPER profile, account mode, required scenarios, runner/host identity, and reconciled terminal state. Any bound-input change requires a new qualification.

## Runtime invariants

- `nextValidId` and connection epoch must exist before admission.
- Quotes are authoritative only after exact subscription and freshness checks.
- Risk-increasing commands are durably journaled before Broker send.
- Filled terminal orders require execution evidence.
- Partial fills retain cumulative quantity; duplicate callbacks do not create a new generation.
- Disconnect/reconnect invalidates affected snapshots and correlations.
- A possibly sent command is reconciled by stable command and venue identity and is never blindly resent.
- Terminalization closes ingress, drains callbacks, freezes one recovery snapshot, and commits a durable witness.
- The root/operator kill switch cannot be disarmed by Execution; unsafe marker state is `Uncertain` and blocks risk increase.

## Failure semantics

Missing SDK, invalid profile, unsafe credential, Broker loss, stale quote, incomplete risk state, kill-switch uncertainty, callback conflict, journal failure, source movement, artifact mismatch, campaign failure, or absent qualification all fail closed for risk increase. Cancel and guarded flatten retain their explicit checks.

## Known limitations

The repository does not supply the IB SDK, external harness, PAPER credentials, TWS/IB Gateway, or Broker account. These are owner-controlled runtime inputs. LIVE is unavailable.
