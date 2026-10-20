# IB PAPER runtime

Status: QUALIFICATION_REQUIRED  
Applies to: repository HEAD  
Implementation: `HeptaTrade/adapter_ib`, `HeptaTrade/execution/hepta_ib_executiond.cpp`, `.github/retired-workflows/ib-paper-qualification.yml`, `.github/retired-workflows/self-hosted-ib-availability.yml`, `scripts/build_ib_candidate_artifact.sh`, `scripts/verify_ib_candidate_artifact.py`, `scripts/run_ib_paper_artifact_qualification.sh`, `scripts/verify_ib_paper_qualification.py`, `scripts/check_qualification_trust_boundary.py`, `scripts/hepta_broker_egress_policy.py`, `systemd/hepta-execution-ib-paper.service`, `systemd/hepta-broker-network-policy-v1.json`, `systemd/hepta-x230-paper-host-identity-map-v1.json`, `docs/ib-paper-profile-policy-v1.json`, `scripts/verify_canonical_ib_paper_profile.py`, `scripts/run_ib_paper_campaign.py`, `scripts/hepta_ib_runtime_report.py`, `HeptaTrade/cli/hepta_ib_profile.cpp`
Tests: `tests/ib_order_lifecycle_tests.cpp`, `tests/ib_paper_kill_switch_tests.cpp`, `tests/ib_paper_execution_profile_tests.cpp`, `tests/ib_live_terminal_reconciliation_tests.cpp`, `tests/execution_coordinator_tests.cpp`, `tests/python/test_canonical_ib_paper_profile.py`, `tests/python/test_ib_paper_qualification.py`, `tests/python/test_qualification_trust_boundary.py`, `tests/python/test_ib_workflow_interfaces.py`, `tests/python/test_hepta_broker_egress_policy.py`, `tests/python/test_self_hosted_ib_availability.py`, `tests/python/test_hepta_broker_egress_policy_atomic.py`, `tests/python/test_paper_campaign_evidence.py`, `tests/python/test_paper_evidence_publication.py`, `tests/python/test_campaign_provenance.py`, `tests/python/test_exported_source_configure.py`, `tests/python/test_ib_runtime_observation.py`, `tests/ib_connection_probe_behavior.py`, `tests/user_profile_cli_tests.py`

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

The Desktop Actions entry points were retired on 2026-10-20. Their exact
workflow bytes remain in `.github/retired-workflows/` as qualification and
host-isolation reference contracts, with the existing executable source tests.
They are not current Pocket4 deployment or GitHub Actions dispatch entry points.
The requirements below describe that retained qualification protocol; source
checks and the current user-session deployment do not establish its Broker
qualification. An old artifact remains bound to its original source revision
and verifier. New artifacts bind the archived workflow's actual source path.

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

The IB daemon now emits `heptatrader.ib-runtime-observation.v1` beside the existing OMS observation on the same bounded cadence. `ib_runtime_observation.h` captures one adapter-lock recovery-audit view instead of joining independently sampled state: connection/event-stream state; active and terminal snapshot generations/completeness/counts; account, position, FX and aggregate risk generations/completeness; gross absolute position; exposure-generation absorption; post-fill reconciliation; recovery barrier state; terminal transport/drain state; and callback-in-flight count. The observation carries no account, order, instrument or command identifiers and has `authorization_effect=NONE`, `paper_authorized=false`, and `live_authorized=false`.

The installed `hepta_ib_runtime_report.py` validates the fixed schema and exports fixed-cardinality Prometheus gauges without reason strings as labels. It exports real callback queue-lag histograms and callback-conflict counters from the adapter, primary-quote and complete authoritative-snapshot ages from the runtime, a native successful post-fill reconciliation-duration histogram, and process-local reconnect plus reconnect authoritative-refresh duration histograms. Missing/invalid ages or absent older-producer fields are represented explicitly rather than rewritten as numerical zero. Network-policy state is intentionally not claimed by the unprivileged daemon; the separate root/host-owned `hepta_broker_egress_policy.py --observe-json` path performs exact nftables readback and accepts only canonical allow or compiled deny-all. Actual target-host collection/notification is separate external evidence; source metrics do not authorize PAPER.

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

## Campaign evidence implementation

The attempt controller, separated private HOME, failure retention and verified publication contract are specified in [`ib-paper-harness-contract.md`](../technical/ib-paper-harness-contract.md). The stable shell entry delegates to `scripts/run_ib_paper_campaign.py`; publication remains part of the existing result verifier. Source-only subprocess regressions do not make an IB account qualified or authorized.

The no-secret builder exports the already-verified candidate commit with `git archive`. Because that immutable source tree intentionally contains no `.git` metadata, the builder passes the exact admitted SHA as `HEPTA_DOCUMENTATION_SOURCE_SHA` during CMake configuration. The broker-disabled `test_exported_source_configure.py` exercises the same top-level configure from a real Git archive and proves installed documentation generation does not depend on an accidental repository worktree.

## Cancellation result boundary

The adapter returns [typed cancellation results](../technical/venue-cancellation-contract.md) under its API mutex. Post-send exceptions remain uncertain. A deferred attempt is removed before external invocation so repeated acknowledgements cannot resend an exception-interrupted cancellation. This source change does not grant PAPER authority.

## Authoritative-flatten result boundary

[Typed flatten results](../technical/venue-flatten-contract.md) retain the existing position/quote/kill-switch checks while removing the unlocked mutable error read. SDK-entry false returns and exceptions remain uncertain; allocation failure in post-send duplicate-signature construction no longer silently reports success. Synthetic adapter regressions are not real SDK/Broker qualification.

## Read-only connectivity diagnostic

The optional `ib_connection_probe` is a diagnostic, not an execution or
qualification entry point. Build it from the root with `BUILD_IB_PROBE=ON`,
`HEPTA_ENABLE_IBAPI=ON` and the same explicit SDK/BID inputs as the candidate.
It reuses `hepta_ibapi_client`; no alternate SDK source inventory is maintained.

```sh
cmake --build build/ib --target ib_connection_probe --parallel 2
build/ib/docs/ib_probe/ib_connection_probe 127.0.0.1 4002 101 8000
ctest --test-dir build/ib -R '^hepta_ib_connection_probe_behavior$' --output-on-failure --no-tests=error
```

The positional arguments are loopback host, port, positive client ID and total
milliseconds (default 8000, maximum 30000). Client ID zero, non-loopback hosts,
malformed numeric input and conventional LIVE ports 4001/7496 reject before
connecting. An alternate local port may serve a synthetic fixture or a separately
configured PAPER endpoint; its number does not verify the account mode.

One monotonic process deadline starts before `eConnect` and covers negotiation,
startup callbacks, the read request and SDK cleanup. A stalled SDK terminates
this isolated read-only process; the same design must not be copied into a
possibly-effectful order daemon. Normal success requires a nonnegative next ID,
nonempty managed-account callback and a response to the subsequent current-time
request. No order, cancel, account mutation or subscription is sent. This is a
small connectivity observation, not a proof of account-wide economic state.

Output is one `heptatrader.ib-connectivity.v1` JSON object. It contains fixed
reason/status vocabulary, callback-presence flags and a numeric SDK error code,
not account IDs, order IDs, raw Broker error text or credentials. Exit status is
0 for a completed read-only round trip, 2 for deadline expiry, 64 for arguments,
and another nonzero value for connection/disconnection/SDK failure. Every record
retains `broker_mode_verified=false`, `paper_authorized=false`,
`live_authorized=false` and `broker_mutations=0`. A port listener or READY result
cannot replace the independently pinned external PAPER campaign.

The real SDK-linked executable is tested against a synthetic fragmented local
peer, stalled negotiation, partial startup, missing read response, disconnects,
invalid arguments and secret-canary callback text. The optional CTest entry is
registered only in the SDK-enabled probe build; the no-secret candidate builder
runs the same behavior test inside its existing network-isolated container before
packaging. Ordinary SDK-free core builds are unchanged. Deployment must run the
diagnostic under an already authorized host identity; it does not change UID,
firewall policy, kill-switch state or Broker credentials to gain access.

## Explicit user scope

The [single-operator user profile](../technical/user-paper-runtime.md) reuses
this runtime under the current user with its own bounded credential and pinned
control directory. It is not the system-service or external qualification profile.

## User-session mutation readiness

User PAPER `system.get_health` includes the authenticated Execution owner's
fence/recovery-only state and authoritative-state mutation readiness, nested under
`execution_authoritative_health` at the Gateway. It is not an order permit: current
quote freshness, risk bounds and a single-use preview remain mandatory. External
qualification health retains its existing strict wire contract.

## Broker order lifetime and callback identity

A numeric Broker order ID is only a current lookup hint, not durable execution
identity. Owned execution callbacks must match the active observation epoch,
Broker client, account, durable service correlation, direction, contract and
nonempty execution ID. Historical queries return old order lifetimes on a new
connection: a different valid correlation is retained as unowned historical
evidence, not routed into the latest owner of the same integer. A same-correlation
identity contradiction remains a failure; the direction check is not bypassed.

Native order statuses without an order reference require the adapter's admitted
current send binding. Unscoped historical statuses cannot retire a newly reused
number. Completed/execution evidence is paired by correlation; old and new
correlations may share an integer without supplying each other's fill or cancel
proof. The legacy numeric execution summary is not used by the PAPER cancellation
recovery path. Cancel targets persist their original correlation through intent,
send, uncertainty and durable resolution.

Recent order rows are keyed by owner scope and correlation, and expose original
`command_id`, `venue_correlation_id`, `broker_client_id`, execution evidence and
observation epochs. Replay recovers command bindings from the original OMS
intent/receipt, without rewriting historical records. Missing/ambiguous execution
evidence is not permission to invent a fill price.

The recent-order display is additionally limited to 24 KiB inside the existing
32 KiB Execution reply envelope. `recent_orders_total`, `recent_orders_returned`
and `recent_orders_truncated` distinguish a bounded view from complete history.
The authoritative active-order snapshot is separate; no ledger bytes or command
records are removed when the display is truncated. Pending reconciliation never
falls back to a same-number row if its exact command is absent from this view.

## FX restart catch-up: accepted is not filled

The checkpoint catch-up evaluator classifies the original order lifecycle; the
runtime supplies all post-checkpoint owner/source-matched records instead of
filtering out cancellation callbacks. A transport-accepted order with a durable
zero-fill `Cancelled` or `ApiCancelled` orderStatus has zero currency delta. This
requires the exact original command and correlation, session/account/domain,
client, service/connection epoch, direction and instrument, and zero cumulative
filled quantity/price. The remainder may be the original unfilled size or zero.
The acceptance and cancellation must agree on the original command/client.

An error code (including 10328), missing callbacks, PendingCancel, a same-number
historical order, a partial fill, or contradictory execution/orderStatus evidence
cannot substitute for that proof. Ambiguous input fails without modifying any
baseline. A cancellation does not advance the observed-cash watermark, invent
a fill, or add PnL. The existing current Broker account/position/cash attestation
still gates startup and mutation readiness after journal catch-up.

## Opt-in BTC/PAXOS user PAPER contract

The mixed user profile accepts exactly `BTC.USD|BTC|CRYPTO|PAXOS|USD;EUR.USD|EUR|CASH|IDEALPRO|USD`.
Its profile credential binds this contract set and the crypto envelope; an old FX-only
credential cannot authorize the extension. Default/system/external-qualification
profiles retain their original contract and limit semantics.

BTC fresh exposure is long-only and flat-before-open. Every new opening is
BTC/PAXOS/USD BUY MKT/IOC with an exact USD 25,000 cash quantity; smaller cash
orders are rejected rather than accepted as canaries. A 1 BTC protection ceiling
bounds the authoritative ask conversion, and 0.00000001 BTC remains the execution
size grid. A SELL is permitted only through the existing exact authoritative
reduce-only MKT/IOC flatten of the positive BTC position using the actual owned
quantity. The API wrapper independently enforces BTC/PAXOS/USD, MKT/IOC, fixed
cash sizing for entry and zero-cash exact-quantity exit. Stale quote, mismatched
contract, unresolved command, non-flat entry state, fractional-grid error, active
order or incomplete risk evidence rejects before Broker dispatch.

The risk gate validates EUR and BTC quantities separately instead of adding
incomparable asset units into one exposure limit. The FX cash baseline remains
unchanged and applies only to CASH contracts; crypto uses Broker security positions.
Post-fill position barriers reconcile BTC at crypto precision before another send.
Crypto events are not replayed as FX cash movement.

Mixed-profile market data is instrument-local: a missing/closed FX quote does not
invalidate a fresh BTC quote. The subscription set keeps whole-set completeness
false until every instrument is priced; operational readiness and per-instrument
quote admission are separate. Only the explicitly mixed user profile opts into
independent publication. Each order still requires its own current-epoch positive
bid/ask and freshness check. PAXOS crypto uses the configured currency-qualified
identity; other venues cannot alias the same inventory.

The FX/NAV candidate and its non-authorizing probe/assurance components are tracked in
[PR #178 consolidation](../technical/pr178-candidate-consolidation-20261008.md).
Source ownership does not approve Broker deployment or strict NAV.
