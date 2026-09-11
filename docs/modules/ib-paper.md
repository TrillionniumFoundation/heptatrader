# IB PAPER runtime

Status: QUALIFICATION_REQUIRED  
Applies to: repository HEAD  
Implementation: `HeptaTrade/adapter_ib/`, `HeptaTrade/execution/hepta_ib_executiond.cpp`, `HeptaTrade/execution/ib_paper_execution_profile.cpp`, `HeptaTrade/execution/ib_paper_execution_runtime_config.cpp`, `.github/workflows/ib-paper-qualification.yml`, `.github/workflows/self-hosted-ib-availability.yml`, `scripts/build_ib_candidate_artifact.sh`, `scripts/verify_ib_candidate_artifact.py`, `scripts/run_ib_paper_artifact_rollout.sh`, `scripts/verify_ib_paper_rollout.py`, `scripts/run_ib_paper_artifact_qualification.sh`, `scripts/verify_ib_paper_qualification.py`, `scripts/hepta_broker_egress_policy.py`, `systemd/hepta-execution-ib-paper.service`, `systemd/hepta-x230-paper-host-identity-map-v1.json`, `docs/ib-paper-profile-policy-v1.json`, `docs/ib-paper-rollout-policy-v1.json`  
Tests: `tests/ib_order_lifecycle_tests.cpp`, `tests/ib_live_terminal_reconciliation_tests.cpp`, `tests/ib_paper_kill_switch_tests.cpp`, `tests/ib_paper_execution_profile_tests.cpp`, `tests/execution_coordinator_tests.cpp`, `tests/python/test_canonical_ib_paper_profile.py`, `tests/python/test_ib_paper_rollout.py`, `tests/python/test_ib_paper_qualification.py`, `tests/python/test_qualification_trust_boundary.py`, `tests/python/test_ib_workflow_interfaces.py`, `tests/python/test_hepta_broker_egress_policy.py`, `tests/python/test_hepta_broker_egress_policy_atomic.py`, `tests/python/test_self_hosted_ib_availability.py`

## Scope

The IB runtime is a fixed PAPER-only Broker authority candidate. It may connect only to a configured loopback TWS/IB Gateway PAPER listener through a dedicated execution identity and a separately supplied, pinned IB C++ API source/runtime.

Repository source does not authorize a real PAPER campaign. `paper_authorized=false` remains the default, and LIVE is unavailable. A successful build, simulator run, TCP probe, host-map test, pull-request approval, rollout receipt, or issue closure is not Broker authorization evidence by itself.

This is an owner-operated system. Repository teams, review counts, branch rules, Merge Queue, workflow contexts, and CI receipts are engineering controls only; they neither grant nor revoke Broker mutation authority.

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

### P1 external canary and progressive rollout

The external canary uses a distinct `PAPER-V4` credential, LMT/DAY only, a bounded quote-age policy, at most one active order, at most one unit of order quantity and gross position, and at most 5,000 units of configured order notional. It cannot reuse or select V5.

Progressive rollout is defined by [`../ib-paper-rollout-policy-v1.json`](../ib-paper-rollout-policy-v1.json). Promotion never widens those instantaneous PAPER-V4 limits. It only increases the number of independently terminal round trips permitted for the same immutable artifact:

- `canary`: at most one mutation cycle;
- `pilot`: at most three mutation cycles;
- `extended`: at most ten mutation cycles.

Every cycle must return to an authoritative flat state before another cycle is admitted. A stage fails if any active order, uncertain command, non-zero position, or incomplete authoritative reconciliation remains. The rollout verifier emits `authorization_effect=NONE`, `paper_authorized=false`, and `live_authorized=false`; a successful rollout is operational evidence, not a source-controlled authorization bit.

### Qualification-only PAPER-V5

The protected certification mode is selected only by the explicit `HEPTA_EXECUTION_EXTERNAL_QUALIFICATION_LMT_DAY=1` configuration and uses a distinct `PAPER-V5` credential. It is never selected by the canonical service template or by P1 progressive rollout.

V5 retains one active order, LMT/DAY at the authoritative touch, quote age between 100 and 5,000 milliseconds, at most six sends per minute, at most 1,000,000 units, at most 1,500,000 notional, and at most 1,000,000 gross PAPER position. The configured maximum order quantity must equal the configured gross-position ceiling, so every reachable V5 position remains within one permissible exact atomic flatten order.

The credential digest includes the exact canonical single CASH contract string and primary quote instrument. Runtime configuration reparses the sole contract and rejects any mismatch between the effective contract map, primary instrument, and credential-bound values. A changed contract universe therefore requires a different credential and a new certification.

The expanded V5 envelope exists only to let an independently pinned harness force broker-observed resilience cases such as a genuine partial fill and then return to an authoritative flat state. It does not authorize market orders, multiple active orders, another contract, another account, ordinary unattended operation, or LIVE.

## Kill switch and network boundary

The canonical kill switch is a root/operator-owned marker under the fixed control directory. Unsafe owner, mode, inode, link, directory, or I/O state becomes `Uncertain` and blocks risk increase. Execution cannot disarm it.

Canonical deployment uses logical `hepta-ib-exec:2003` and the source-controlled loopback nftables policy. The bounded x230 qualification host uses the reviewed mapping `systemd/hepta-x230-paper-host-identity-map-v1.json`, which binds logical UID `2003` to host execution UID `995`, keeps the Actions runner identity distinct, limits scope to IB PAPER qualification, and declares `live_authorized=false`.

The lightweight preflight pins the root-owned host probe and identity map, proves the Actions runner itself cannot reach the protected PAPER port, verifies the immutable candidate and pinned external harness, and checks the non-secret host boundary before any rollout mutation job is eligible. Canonical nftables replacement queries JSON machine state, retries only within a compiled bound and requires exact structural rule readback before either allow or deny-all is accepted. Any host-specific policy permitting the execution UID remains a root-owned deployment input; source tests or preflight do not install it or authorize a campaign. See [`../BROKER-NETWORK-ISOLATION.md`](../BROKER-NETWORK-ISOLATION.md).

## Build-once artifact boundary

The owner-operated workflow deliberately separates artifact construction from every Broker experiment:

1. a no-secret builder receives the exact dispatch-time `main` revision, a digest-pinned OCI image, a read-only SDK/BID snapshot, and bounded writable storage;
2. it builds **one** immutable candidate artifact and uploads that artifact once;
3. host preflight, canary, pilot, extended rollout, and optional certification each download and verify that same artifact identity rather than rebuilding source;
4. the PAPER execution identity receives only the verified executable and runs it through a separately pinned external harness with PAPER-only Broker access.

The dispatch actor and rerun triggering actor must match the configured immutable owner identity before self-hosted allocation. The requested SHA must equal the dispatch-time `main` SHA, so an arbitrary branch cannot select qualification code or candidate bytes.

After exact source has been converted into the immutable artifact, later movement of `refs/heads/main` is intentionally irrelevant to that rollout. Branch movement cannot change the source SHA, executable digest, SDK digest, builder provenance, harness, profile, account, host, or evidence already bound to the candidate. Any change to a bound input requires a new artifact or campaign as appropriate. See [`../technical/ib-paper-harness-contract.md`](../technical/ib-paper-harness-contract.md) and ADR [`../adr/0003-immutable-artifact-paper-qualification.md`](../adr/0003-immutable-artifact-paper-qualification.md).

## Progressive PAPER workflow

The canonical owner-operated workflow is:

```text
exact dispatch-main source
  -> single no-secret immutable candidate build
  -> lightweight target-host preflight
  -> PAPER-V4 canary (1 flat round trip)
  -> PAPER-V4 pilot (<=3 flat round trips)
  -> PAPER-V4 extended (<=10 flat round trips)
  -> optional PAPER-V5 full certification
```

`canary`, `pilot`, `extended`, and `qualify` use the protected GitHub environment `ib-paper`; build and non-mutating host preflight do not. Environment reviewers, deployment protection and external credential access remain server-side controls and must be configured independently. Merely naming the environment in source is not evidence that it exists or approved a campaign.

A user may stop at any requested rollout stage. `certify` is the only selection that executes the heavy twelve-scenario V5 campaign, and it is reachable only after the same artifact passed extended P1 rollout. Ordinary application releases therefore do not need to rerun destructive resilience experiments merely to prove that an unchanged execution/risk/journal path still deploys.

## Runtime and recovery invariants

The rollout refactor does not change Execution, Risk, or Journal semantics. The following invariants remain mandatory:

- `nextValidId` and a current connection epoch exist before admission;
- a quote is authoritative only for the exact subscription/contract and freshness window;
- risk-increasing commands have stable command identity and durable intent/send-attempt records before Broker I/O;
- Filled terminal orders require execution evidence; Filled text alone is insufficient;
- validated execution and terminal callbacks match Broker client, account, correlation, connection epoch, and contract binding;
- partial fills retain cumulative quantity and duplicate/out-of-order callbacks do not create new economic fills;
- a cancel queued before acknowledgement remains uncertain until authoritative terminal evidence resolves it;
- reconnect invalidates affected snapshots and correlations;
- a possibly sent command is reconciled by stable command and venue identity and is never blindly resent;
- P1 progression never increases the existing one-unit instantaneous exposure envelope;
- V5 flatten is LMT/DAY at the authoritative touch, exactly equals the authoritative position, and remains absolutely bounded to 1,000,000 units;
- terminalization closes ingress, drains callbacks, freezes one recovery snapshot, and commits a durable witness.

## Failure semantics

Missing SDK, invalid or conflicting profile mode, unsafe credential, changed contract binding, multiple quote contracts, unsupported STK scope, Broker loss, stale quote, incomplete risk state, kill-switch uncertainty, identity-map mismatch, runner broker reachability, callback conflict, journal failure, artifact/source mismatch, rollout failure, certification failure, or absent required external infrastructure all fail closed for risk increase. Cancel and guarded authoritative flatten retain their own owner, fencing, order-state, quote, and venue checks.

Promotion is monotonic only within one immutable artifact identity. Failure at `pilot` cannot be bypassed by invoking `extended`; failure at `extended` cannot be bypassed by invoking `certify`. A new artifact starts again at preflight/canary unless an independently reviewed operational policy explicitly changes that rule.

## Observability

Track connection epoch, next-valid-order-ID state, subscription IDs, exact contract identity, quote age, callback lag, active/terminal correlations, execution IDs, account/position refresh completeness, risk reason codes, send attempts, uncertain commands, reconnect duration, kill-switch state, logical/runtime/runner identity, exact artifact digest, effective profile digest, credential version, active-order limit, rollout stage, completed terminal cycles, and final reconciliation state.

For promotion, the minimum terminal SLI set is `active_orders=0`, `uncertain_commands=0`, `position_quantity=0`, and `authoritative_reconciliation_complete=true`. Any deviation blocks the next stage. No metric, receipt, issue label, or CI status may represent source-only success as PAPER authorization.

## Heavy resilience certification

Full certification remains available and deliberately separate from normal progressive rollout. A certification run must:

1. consume the same immutable artifact that passed preflight and extended P1 rollout, without rebuilding it;
2. verify executable, SDK/BID, builder, harness, profile, contract, and host identities;
3. establish a PAPER-only account/session and fresh authoritative barriers;
4. execute rejection, accepted-order, partial-fill, duplicate/out-of-order callback, cancel-race, disconnect/reconnect, uncertain-outcome, restart/journal replay, fencing, kill-switch and divergence scenarios;
5. cancel/flatten and perform final authoritative order, execution, position, and account reconciliation;
6. issue a digest-bound receipt only after the final state is flat and every possible send is resolved.

The canonical scenario contract remains [`../ib-paper-qualification-scenarios-v1.json`](../ib-paper-qualification-scenarios-v1.json). Heavy certification exists to validate broker-specific resilience and recovery behavior; it is not duplicated by ordinary PR CI or every deployment attempt.

## Test expectations

Source tests must prove that the workflow builds exactly one candidate, every later stage names the same artifact identity, host preflight precedes mutation, P1 stages cannot widen the one-unit instantaneous limits, any non-flat or uncertain terminal result blocks promotion, and full certification is explicitly selected and sequenced after extended rollout.

The existing runtime suites continue to own order lifecycle, kill switch, atomic flatten, reconciliation, idempotency, callback ordering, and journal-before-send behavior. Sanitizer and fault-heavy runtime testing runs in its dedicated periodic/high-risk-change lane rather than through duplicated compatibility workflow contexts.

## Known limitations

The repository does not supply the IB SDK, external harness, PAPER credentials, TWS/IB Gateway, root-owned x230 host policy, or Broker account. Those are owner-controlled runtime inputs and require separate evidence. The external pinned harness must implement the documented `p1-progressive-rollout` mode before canary/pilot/extended can execute on a real host. Aggregate pending-order notional and general multi-asset base-currency exposure remain prerequisites for relaxing the one-active-order/one-CASH-contract policy. LIVE is unavailable.

## Gap-register relationship

IB PAPER is optional and disabled by default. Real Broker rollout/certification is an activation prerequisite, not an unresolved supported-scope source gap. Closing repository gaps never asserts that a campaign happened. Progressive rollout receipts intentionally keep `paper_authorized=false`; full PAPER authorization remains an external operational decision bound to a valid current artifact/profile/environment evidence set. `live_authorized=false` remains invariant.
