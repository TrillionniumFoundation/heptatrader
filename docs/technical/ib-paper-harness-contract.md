# IB PAPER external harness contract

Status: CURRENT  
Applies to: optional owner-operated IB PAPER progressive rollout and certification

## Purpose

The external qualifier remains outside the repository because it owns host-specific Broker session control and may access credentials that repository code must never receive. Its behavior is nevertheless not an opaque source of truth. The repository defines the artifact identity, isolation, operation envelope, evidence and terminal-state contracts that the harness must satisfy.

There are now two deliberately separate Broker-facing modes:

1. **`p1-progressive-rollout`** uses the existing `PAPER-V4` / `EXTERNAL_P1_CANARY_LMT_DAY` profile and proves tiny deployability through one or more independently flat round trips without widening instantaneous risk;
2. **`bounded-mutations`** uses the qualification-only V5 profile and executes the complete broker-observed resilience/certification scenario set.

The canonical heavy scenario list remains [`../ib-paper-qualification-scenarios-v1.json`](../ib-paper-qualification-scenarios-v1.json). Progressive stage limits are defined separately by [`../ib-paper-rollout-policy-v1.json`](../ib-paper-rollout-policy-v1.json).

## Trust boundary and build-once identity

The workflow has distinct principals:

1. a no-secret builder produces and verifies one immutable candidate artifact from the exact `main` revision selected at owner-authorized workflow dispatch;
2. a non-mutating target-host preflight verifies the same artifact, the pinned harness, runner isolation and the root-owned PAPER host boundary;
3. mutation-capable PAPER jobs execute only that verified candidate through the independently pinned qualifier.

The requested candidate SHA must equal the dispatch event's immutable `github.sha` before any self-hosted job is allocated. Candidate source is checked out only for the single build. Preflight, canary, pilot, extended rollout and certification download the same artifact identity and never rebuild it.

Once source has been converted into that immutable artifact, later movement of `refs/heads/main` is intentionally irrelevant: branch navigation cannot mutate the source SHA, executable digest, SDK/BID snapshot, builder provenance, harness, profile, Broker account, host or evidence already bound to the candidate. A changed bound input requires a new artifact or campaign as appropriate.

Candidate code must not inherit Actions credentials, repository write credentials or raw Broker credentials. The qualifier remains responsible for Broker login/session custody and constraining candidate networking to the approved PAPER path.

## Progressive P1 rollout mode

`scripts/run_ib_paper_artifact_rollout.sh` invokes the pinned qualifier from an empty environment except for explicit bounded inputs. The qualifier receives:

- exact candidate executable and SHA-256;
- exact source SHA;
- rollout stage (`canary`, `pilot` or `extended`);
- maximum mutation-cycle count for that stage;
- existing P1 instantaneous limits: quantity `1`, order notional `5000`, one active order and gross position `1`;
- profile order mode `EXTERNAL_P1_CANARY_LMT_DAY`;
- `require-flat-between-cycles`;
- a new evidence directory and exact result path;
- `candidate-environment=cleared`;
- `candidate-network-policy=broker-proxy-only`;
- `credential-delivery=harness-only`;
- `mode=p1-progressive-rollout`.

The progressive policy currently permits at most 1/3/10 terminal cycles for canary/pilot/extended respectively. Promotion changes only the number of independent cycles; it must not raise the PAPER-V4 per-order or instantaneous position envelope.

Each mutation cycle must start from an authoritative reconciled state, place only a bounded P1 operation, converge to authoritative terminal order state, return the PAPER account to zero position, and resolve every possibly-sent command before another cycle starts. The harness may not count a new cycle while the previous cycle retains an active order, an unresolved/uncertain command, a non-zero position or an incomplete reconciliation barrier.

The progressive result schema is `heptatrader.ib-paper-rollout-result.v1`. `scripts/verify_ib_paper_rollout.py` binds it to the exact source, binary and harness; enforces the stage limits; requires final `active_orders=0`, `uncertain_commands=0`, `position_quantity=0`, and complete authoritative reconciliation; and verifies digest-bound `authoritative-snapshot`, `broker-callbacks`, and `oms-journal` evidence.

A rollout verification receipt always declares `authorization_effect=NONE`, `paper_authorized=false`, and `live_authorized=false`. It is deployment/promotion evidence, not an authorization credential.

## Heavy certification mode

`scripts/run_ib_paper_artifact_qualification.sh` continues to invoke the same independently pinned qualifier with `mode=bounded-mutations`, a cleared candidate environment, broker-proxy-only networking and harness-only credential delivery.

The heavy campaign is explicitly selected only through workflow stage `certify` and is reachable only after the same immutable artifact has passed extended P1 rollout. It uses the qualification-only V5 profile because some required broker experiments, such as a genuine partial fill above displayed top-of-book liquidity, cannot be reliably induced inside the one-unit P1 envelope.

Every canonical certification scenario is an effect-bound experiment, not a text assertion. The harness must induce or observe the actual condition and retain the Broker/runtime evidence required by the scenario contract. Examples include genuine partial fill, disconnect/reconnect epoch transition, a possibly-sent outcome entering durable uncertainty, callback duplication/reordering, kill-switch behavior, lease fencing, divergence reconciliation and process restart followed by journal replay.

`scripts/verify_ib_paper_qualification.py` continues to require all canonical scenarios and their exact assertion/evidence contracts. Full certification is intentionally heavy and must not be silently substituted for ordinary rollout or duplicated by every PR/deployment.

## Evidence rules

Both modes write their result only after the requested Broker experiment completes. Evidence is immutable verifier input and must be bounded, digest-addressed, path-safe and free of raw secret values. Account and host identity use bounded fingerprints. Broker callbacks, authoritative snapshots, execution events and OMS journal extracts retain only the fields necessary to prove the asserted transition while omitting credentials and session tokens.

A harness may not silently expand the operation set, run a different executable, substitute another account/environment, increase stage limits, switch P1 to V5, reuse a stale evidence directory, or claim a terminal state while unresolved mutations remain.

## Terminal state and promotion

A progressive stage or heavy certification is incomplete until every possible mutation from that run is resolved and the bounded PAPER account is authoritatively reconciled. Any active/unresolved order, uncertain send, unexplained execution, position divergence, incomplete refresh barrier, unsafe kill-switch state or changed source/artifact/harness identity fails the run.

For progressive rollout, each cycle must be flat before the next cycle and the final state must be flat. Failure of `canary` blocks `pilot`; failure of `pilot` blocks `extended`; failure of `extended` blocks `certify`. A failed campaign is evidence of failure; it never grants partial authority.

## Reproducibility and audit

A record must make it possible to answer, without trusting narrative prose:

- exactly which source and executable ran;
- which builder/SDK identity produced the artifact;
- which qualifier bytes ran;
- which PAPER profile, account fingerprint and host fingerprint were used;
- for rollout, which stage and how many terminal cycles completed;
- for certification, which canonical scenarios executed and in what order;
- which immutable evidence files prove the result;
- whether any mutation remained uncertain;
- whether final authoritative reconciliation was complete.

The repository deliberately does not claim that the external harness, credentials, TWS/IB Gateway or PAPER account exist merely because these interfaces are documented. Their presence and behavior require an owner-operated host run.

## Failure semantics

Any mismatch between source policy and executable verifier fails source CI. Any mismatch between pinned harness, candidate identity, stage/operation envelope, Broker environment, evidence contract or terminal state fails rollout/certification. Missing external infrastructure leaves `paper_authorized=false`; LIVE remains unavailable.

Branch-pointer movement after immutable candidate admission is not a failure condition. It is repository navigation state, not a mutation of candidate bytes. See [`../adr/0003-immutable-artifact-paper-qualification.md`](../adr/0003-immutable-artifact-paper-qualification.md).
