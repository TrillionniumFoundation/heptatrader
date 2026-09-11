# IB PAPER runtime

Status: QUALIFICATION_REQUIRED  
Applies to: repository HEAD  
Implementation: `HeptaTrade/adapter_ib/`, `HeptaTrade/execution/hepta_ib_executiond.cpp`, `HeptaTrade/execution/ib_paper_execution_profile.cpp`, `HeptaTrade/execution/ib_paper_execution_runtime_config.cpp`, `.github/workflows/ib-paper-qualification.yml`, `.github/workflows/self-hosted-ib-availability.yml`, `scripts/build_ib_candidate_artifact.sh`, `scripts/verify_ib_candidate_artifact.py`, `scripts/run_ib_paper_artifact_qualification.sh`, `scripts/verify_ib_paper_qualification.py`, `scripts/hepta_broker_egress_policy.py`, `systemd/hepta-execution-ib-paper.service`, `systemd/hepta-x230-paper-host-identity-map-v1.json`, `docs/ib-paper-profile-policy-v1.json`  
Tests: `tests/ib_order_lifecycle_tests.cpp`, `tests/ib_live_terminal_reconciliation_tests.cpp`, `tests/ib_paper_kill_switch_tests.cpp`, `tests/ib_paper_execution_profile_tests.cpp`, `tests/execution_coordinator_tests.cpp`, `tests/python/test_canonical_ib_paper_profile.py`, `tests/python/test_ib_paper_qualification.py`, `tests/python/test_qualification_trust_boundary.py`, `tests/python/test_ib_workflow_interfaces.py`, `tests/python/test_hepta_broker_egress_policy.py`, `tests/python/test_hepta_broker_egress_policy_atomic.py`, `tests/python/test_self_hosted_ib_availability.py`

## Scope

The IB runtime is a fixed PAPER-only Broker authority candidate. It may connect only to a configured loopback TWS/IB Gateway PAPER listener through a dedicated execution identity and a separately supplied, pinned IB C++ API source/runtime.

Repository source does not authorize a real PAPER campaign. `paper_authorized=false` remains the default, and LIVE is unavailable. A successful build, simulator run, TCP probe, host-map test, pull-request approval, or issue closure is not Broker qualification evidence.

This is an owner-operated system. Repository teams, CODEOWNERS, pull-request approval counts, branch rules, Merge Queue, governance environments, and governance receipts are engineering controls only; they neither grant nor revoke Broker mutation authority.

## Composition

`hepta-ib-executiond` owns:

- the IB API session and callback lifecycle;
- authoritative quote subscriptions;
- account, position, active-order, terminal-order, and execution refresh barriers;
- order ID and venue-correlation state;
- venue-specific risk and order-field validation;
- journaled send, cancel, and authoritative flatten operations;
- reconnect, uncertain-outcome recovery, and terminalization.

The Tool Gateway communicates through the typed Execution protocol and cannot link or call the IB API. Agent, Gateway, and Actions runner identities do not receive Broker credentials or direct broker-port access.

The SDK uses IEEE decimal64 BID quantities and the real Intel Decimal Floating-Point Math Library. Native builds require an explicit archive and must pass the SDK/BID ABI probe. The qualifying read-only SDK snapshot digest covers both source and `libbid.a`.

## Fixed profile families

The source-controlled baseline is [`../ib-paper-profile-policy-v1.json`](../ib-paper-profile-policy-v1.json). Every enabled profile binds PAPER mode, a `DU` account, loopback endpoint, client ID, state/control directories, authorization credential, security/order types, quantity/notional/rate limits, active-order limit, gross-position limit, quote freshness, and the execution domain.

The canonical service template remains the ordinary fixed PAPER profile. Until aggregate pending-order notional is authoritative across contracts, the supported qualification scope permits exactly one active order and one CASH quote contract. Multi-contract and STK profiles are not qualified.

### Ordinary local profile

The local profile uses its own `PAPER-V3` credential and conservative hard limits. It does not inherit any external qualification mode or V5 limit.

### P1 external canary

The existing external canary uses a distinct `PAPER-V4` credential, LMT/DAY only, a small absolute order/position envelope, a bounded quote-age policy, and one active order. It cannot reuse or select V5.

### Qualification-only PAPER-V5

The protected qualification mode is selected only by the explicit `HEPTA_EXECUTION_EXTERNAL_QUALIFICATION_LMT_DAY=1` configuration and uses a distinct `PAPER-V5` credential. It is never selected by the canonical service template.

V5 retains one active order, LMT/DAY at the authoritative touch, quote age between 100 and 5,000 milliseconds, at most six sends per minute, at most 1,000,000 units, at most 1,500,000 notional, and at most 1,000,000 gross PAPER position. The configured maximum order quantity must equal the configured gross-position ceiling, so every reachable V5 position remains within one permissible exact atomic flatten order.

The credential digest includes the exact canonical single CASH contract string and primary quote instrument. Runtime configuration reparses the sole contract and rejects any mismatch between the effective contract map, primary instrument, and credential-bound values. A changed contract universe therefore requires a different credential and a new qualification.

The expanded envelope exists only to let an independently pinned harness place a touch-price order above displayed top-of-book liquidity, observe a genuine partial fill, cancel any remainder, and return to an authoritative flat state. It does not authorize market orders, multiple active orders, another contract, another account, ordinary unattended operation, or LIVE.

## Kill switch and network boundary

The canonical kill switch is a root/operator-owned marker under the fixed control directory. Unsafe owner, mode, inode, link, directory, or I/O state becomes `Uncertain` and blocks risk increase. Execution cannot disarm it.

Canonical deployment uses logical `hepta-ib-exec:2003` and the source-controlled loopback nftables policy. The bounded x230 qualification host uses the reviewed mapping `systemd/hepta-x230-paper-host-identity-map-v1.json`, which binds logical UID `2003` to host execution UID `995`, keeps Actions runner UID `994` distinct, limits scope to IB PAPER qualification, and declares `live_authorized=false`.

The checkout-free runner probe pins the map digest, proves the runner cannot reach port 4002, and delegates non-secret host-boundary inspection to a separately pinned root-owned helper. Canonical nftables replacement queries JSON machine state, retries only within a compiled bound and requires exact structural rule readback before either allow or deny-all is accepted. Any host-specific policy permitting UID `995` remains a root-owned deployment input bound to that map; source tests or the probe do not install it or authorize a campaign. See [`../BROKER-NETWORK-ISOLATION.md`](../BROKER-NETWORK-ISOLATION.md).

## Owner-operated qualification boundary

Qualification keeps two trust domains:

1. a no-secret builder receives the exact dispatch-time `main` revision, a digest-pinned OCI image, a read-only SDK/BID snapshot, and bounded writable storage;
2. a PAPER execution identity receives only the verified immutable artifact and runs it through a separately pinned external harness with PAPER-only Broker access.

The dispatch actor and rerun triggering actor must match the configured immutable owner identity before self-hosted allocation and are reasserted at runtime. The requested SHA must equal the dispatch-time `main` SHA, so an arbitrary branch cannot select qualification code or candidate bytes.

After that exact source has been built and verified, the Broker campaign is bound to the immutable artifact/source digest rather than to the mutable `refs/heads/main` pointer. Ordinary development may advance `main` while the campaign runs; that does not alter the candidate binary and does not invalidate otherwise valid Broker-observed evidence. Any change to the candidate source SHA, artifact/executable digest, builder inputs, harness, effective profile, contract binding, account/host identity, or required scenario evidence still requires a new campaign. See [`../technical/ib-paper-harness-contract.md`](../technical/ib-paper-harness-contract.md) and ADR [`../adr/0003-immutable-artifact-paper-qualification.md`](../adr/0003-immutable-artifact-paper-qualification.md).

The mutation-capable `qualify` job also declares the protected GitHub environment `ib-paper`; the no-secret builder does not. Environment reviewers, deployment protection and credential access remain server-side controls and must be configured independently. Merely naming the environment in source is not evidence that it exists, is protected, or approved a campaign.

Trusted and candidate checkouts are cleaned and checked against HEAD, stage-zero index, tracked bytes, modes, link identity, and untracked/ignored paths. Candidate and trusted trees are rechecked after build; the trusted harness is rechecked after the Broker campaign.

The final receipt binds source SHA, artifact/executable digest, builder image/toolchain/resource policy, SDK/BID digest, harness digest, effective PAPER profile and contract binding, account mode, required scenarios, runner/host identity, kill-switch observations, and reconciled terminal state. Any bound-input change requires a new campaign.

## Runtime and recovery invariants

- `nextValidId` and a current connection epoch exist before admission.
- A quote is authoritative only for the exact subscription/contract and freshness window.
- Risk-increasing commands have stable command identity and durable intent/send-attempt records before Broker I/O.
- Filled terminal orders require execution evidence; Filled text alone is insufficient.
- Validated live execution and terminal callbacks must match Broker client, account, correlation, connection epoch, and contract binding.
- Partial fills retain cumulative quantity; duplicate or out-of-order callbacks do not create new economic fills.
- A cancel queued before acknowledgement remains uncertain until authoritative terminal evidence resolves it.
- Reconnect invalidates affected snapshots and correlations.
- A possibly sent command is reconciled by stable command and venue identity and is never blindly resent.
- V5 flatten is LMT/DAY at the authoritative touch, exactly equals the authoritative position, and is absolutely bounded to 1,000,000 units.
- Terminalization closes ingress, drains callbacks, freezes one recovery snapshot, and commits a durable witness.

## Failure semantics

Missing SDK, invalid or conflicting profile mode, unsafe credential, changed contract binding, multiple quote contracts, unsupported STK scope, Broker loss, stale quote, incomplete risk state, kill-switch uncertainty, identity-map mismatch, runner broker reachability, callback conflict, journal failure, artifact/source mismatch, campaign failure, or absent qualification all fail closed for risk increase. Cancel and guarded authoritative flatten retain their own owner, fencing, order-state, quote, and venue checks.

## Observability

Track connection epoch, next-valid-order-ID state, subscription IDs, exact contract identity, quote age, callback lag, active/terminal correlations, execution IDs, account/position refresh completeness, risk reason codes, send attempts, uncertain commands, reconnect duration, kill-switch state, logical/runtime/runner identity, effective profile digest, credential version, active-order limit, and quote-contract count.

No metric, receipt, issue label, or CI status may represent source-only success as PAPER authorization.

## Qualification

A qualifying run must:

1. build an immutable no-secret candidate from the exact dispatch-time `main` revision;
2. verify package, executable, SDK/BID, builder, harness, profile, contract, and host identities;
3. establish a PAPER-only account/session and fresh authoritative barriers;
4. execute rejection, accepted-order, partial-fill, duplicate/out-of-order callback, cancel-race, disconnect/reconnect, uncertain-outcome, restart/journal replay, fencing, and kill-switch scenarios;
5. cancel/flatten and perform final authoritative order, execution, position, and account reconciliation;
6. issue a digest-bound receipt only after the final state is flat and every possible send is resolved.

A later change to the `main` branch pointer is not a candidate change. Failure, bound-input drift, or absent effect-bound evidence leaves `production_authorized=false` and `paper_authorized=false`.

## Known limitations

The repository does not supply the IB SDK, external harness, PAPER credentials, TWS/IB Gateway, root-owned x230 host policy, or Broker account. Those are owner-controlled runtime inputs and require separate evidence. Aggregate pending-order notional and general multi-asset base-currency exposure remain prerequisites for relaxing the one-active-order/one-CASH-contract policy. LIVE is unavailable.

## Gap-register relationship

IB PAPER is optional and disabled by default. Its real Broker campaign is an activation prerequisite, not an unresolved supported-scope source gap. Closing repository gaps never asserts that the campaign happened. Until a valid current receipt exists for the selected artifact/profile/environment tuple, `paper_authorized=false`; `live_authorized=false` remains invariant.
