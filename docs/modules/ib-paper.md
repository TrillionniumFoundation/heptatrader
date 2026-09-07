# IB PAPER runtime

Status: QUALIFICATION_REQUIRED  
Applies to: repository HEAD  
Implementation: `HeptaTrade/adapter_ib/`, `HeptaTrade/execution/hepta_ib_executiond.cpp`, `systemd/hepta-execution-ib-paper.service`, `docs/ib-paper-profile-policy-v1.json`  
Tests: `tests/ib_order_lifecycle_tests.cpp`, `tests/ib_paper_kill_switch_tests.cpp`, `tests/execution_coordinator_tests.cpp`, `tests/python/test_canonical_ib_paper_profile.py`

## Scope

The IB runtime is a fixed PAPER-only broker authority candidate. It may connect only to the configured loopback TWS/IB Gateway PAPER listener using the dedicated execution identity and a separately supplied IB C++ API source/runtime.

Repository source does not by itself authorize a real PAPER campaign. The protected environment, builder, runner, broker account, credentials, host controls, human approval, and verifier-issued receipt are external requirements.

## Composition

`hepta-ib-executiond` owns:

- the IB API session and callback lifecycle;
- authoritative quote subscriptions;
- account, position, active-order, terminal-order, and execution refresh barriers;
- order ID and correlation state;
- venue-specific risk and order-field validation;
- journaled send/cancel/flatten operations;
- reconnect and terminal recovery.

The Tool Gateway communicates over the typed Execution protocol and cannot link or call the IB API.

## Fixed profile

The profile binds PAPER mode, `DU` account, loopback host, allowed port, client ID, state directory, control directory, authorization credential, allowed security/order types, order quantity/notional limits, order rate, active-order limit, gross-position limit, and quote freshness. The authorization credential is a digest of the reviewed profile.

The source-controlled canonical policy is [`../ib-paper-profile-policy-v1.json`](../ib-paper-profile-policy-v1.json). Until the authoritative snapshot exposes aggregate pending-order notional across contracts, the canonical profile permits exactly **one active order** and exactly **one CASH quote contract**. This prevents a second candidate order from stacking behind an unaccounted live order. Multi-contract or STK profiles are not qualified by the current source policy even though lower-level adapter types remain extensible.

`python3 scripts/verify_canonical_ib_paper_profile.py` enforces the source template and is covered by hostile mutation tests. A deployment must verify the effective runtime environment against the same constraints; copying the example is not evidence by itself.

The external canary mode is separately bounded to a small LMT/DAY order and an authoritative quote-age limit. It is not LIVE.

## Kill switch and network boundary

The canonical kill switch is a root/operator-owned marker under the fixed control directory. Unsafe owner, mode, inode, link, directory, or I/O state is `Uncertain` and blocks risk increase. The Execution process cannot disarm it.

Broker API destination ports are restricted to the dedicated IB execution UID by the nftables policy. Agent and Gateway identities must be denied those ports even when they do not possess credentials.

## Callback and recovery invariants

- `nextValidId` and connection epoch must be established before order admission.
- A quote is authoritative only after the exact subscription and freshness checks pass.
- Filled terminal orders require execution evidence.
- Reconnect invalidates affected snapshots and correlations.
- A possibly sent command is reconciled by stable command and venue identities; it is not blindly resent.
- Terminalization closes event ingress, drains callbacks, freezes one recovery snapshot, and commits a durable witness.

## Failure semantics

Missing SDK, invalid profile, unsafe credential, broker connection loss, stale quote, incomplete risk state, kill-switch uncertainty, correlation conflict, callback drain failure, journal failure, non-canonical multi-order/multi-contract profile, or qualification absence all fail closed for risk increase. Cancel and guarded flatten retain their own checks and may remain available.

## Observability

Track connection epoch, next-valid-order-ID state, subscription IDs, quote age, callback lag, active and terminal correlations, execution IDs, position/account refresh completeness, risk reason codes, send attempts, uncertain commands, reconnect duration, kill-switch state, effective profile digest, active-order limit, and quote-contract count.

## Qualification

A qualifying run must build an immutable candidate without broker secrets, verify the artifact with trusted code, validate the effective profile against the canonical source policy, run a bounded PAPER campaign through an independently pinned harness, re-admit the unchanged reviewed candidate, and issue a final receipt only after post-campaign state and evidence verify. Failure or missing evidence leaves `production_authorized=false`.

## Known limitations

The current repository does not prove that organization teams, protected environments, runner-group restrictions, the IB SDK, credentials, TWS/Gateway, or a PAPER account are present. Aggregate pending-order notional and general multi-asset base-currency exposure remain prerequisites for relaxing the single-active-order/single-CASH-contract policy. LIVE is unavailable.
