# IB PAPER external harness contract

Status: CURRENT  
Applies to: optional owner-operated IB PAPER qualification only

## Purpose

The external qualifier remains outside the repository because it owns host-specific Broker session control and may access credentials that repository code must never receive. Its behavior is nevertheless not an opaque source of truth. The repository defines the complete scenario, evidence, isolation and terminal-state contract that a qualifying harness must satisfy.

The canonical scenario list is [`../ib-paper-qualification-scenarios-v1.json`](../ib-paper-qualification-scenarios-v1.json). `tests/python/test_ib_paper_scenario_contract.py` requires that reviewable contract to agree exactly with the assertions and evidence kinds enforced by `scripts/verify_ib_paper_qualification.py`.

## Trust boundary

The qualification workflow has two distinct principals:

1. a no-secret builder produces and verifies an immutable candidate artifact from the exact `main` revision selected at owner-authorized workflow dispatch;
2. a PAPER host executes only that verified candidate through an independently pinned qualifier.

The requested candidate SHA must equal the dispatch event's immutable `github.sha` before either self-hosted runner is allocated. That converts a moving branch pointer into an immutable source/artifact identity at admission. Once the candidate artifact exists, later movement of `refs/heads/main` is intentionally irrelevant to that campaign: it cannot change the source SHA, executable digest, harness, profile, Broker account, host or evidence already bound to the qualification subject. A changed bound input requires a new campaign.

Candidate code must not inherit Actions credentials, repository write credentials or raw Broker credentials. The qualifier is responsible for Broker login/session custody and for constraining candidate networking to the approved PAPER path.

## Invocation contract

`scripts/run_ib_paper_artifact_qualification.sh` invokes the pinned qualifier from an empty environment except for the explicitly bounded qualification variables. The qualifier receives:

- the exact candidate executable and SHA-256;
- the exact source SHA;
- the required scenario/operation allowlist;
- an evidence directory that does not exist before the run;
- the required result path;
- `candidate-environment=cleared`;
- `candidate-network-policy=broker-proxy-only`;
- `credential-delivery=harness-only`;
- `mode=bounded-mutations`.

The harness may not silently expand the operation set, run a different executable, substitute another account/environment, or reuse a stale evidence directory.

## Scenario execution semantics

Every canonical scenario is an effect-bound experiment, not a text assertion. The harness must induce or observe the actual condition and retain the Broker/runtime evidence required by the scenario contract. Examples include a genuine partial fill, an actual disconnect/reconnect epoch transition, an outcome whose send result is initially uncertain, and process restart followed by journal replay and authoritative reconciliation.

A scenario is not satisfied by emitting its assertion names. The verifier requires evidence files with declared kinds, sizes and digests and binds the result to the exact candidate, source, harness, Broker session and timing envelope.

## Evidence contract

The harness writes `qualification-result.json` only after scenario execution. Evidence is immutable input to the repository verifier and must include the exact evidence kinds required by each scenario. The verifier rejects missing, duplicate, unexpected, oversized, changed or path-escaping evidence.

Raw secret values must never appear in evidence. Account and host identity use bounded fingerprints. Broker callbacks, authoritative snapshots, execution events and OMS journal extracts should retain the minimum fields needed to prove the asserted state transition while omitting credentials and session tokens.

## Terminal state

A qualifying run is incomplete until every possible mutation is resolved and the bounded PAPER account is authoritatively reconciled. Any active/unresolved order, uncertain send, unexplained execution, position divergence, incomplete refresh barrier, unsafe kill-switch state or changed source/artifact/harness identity makes the campaign fail.

The final state must satisfy the profile's flat/terminal requirements. A failed campaign is evidence of failure; it never grants partial authorization.

## Reproducibility and audit

A qualification record must make it possible to answer, without trusting narrative prose:

- exactly which source and executable ran;
- which qualifier bytes ran;
- which PAPER profile, account fingerprint and host fingerprint were used;
- which scenarios executed and in what order;
- which immutable evidence files prove each scenario;
- whether any mutation remained uncertain;
- whether final authoritative reconciliation was complete.

The repository deliberately does not claim that the external harness, credentials, TWS/IB Gateway or PAPER account exist merely because this interface is documented. Their presence and behavior require a real owner-operated campaign.

## Failure semantics

Any mismatch between the reviewable scenario contract and the executable verifier fails source CI. Any mismatch between the pinned harness, candidate identity, operation allowlist, Broker environment, evidence contract or terminal state fails qualification. Missing external infrastructure leaves `paper_authorized=false`; LIVE remains unavailable.

Branch-pointer movement after immutable candidate admission is not a failure condition. It is repository navigation state, not a mutation of the candidate bytes. See [`../adr/0003-immutable-artifact-paper-qualification.md`](../adr/0003-immutable-artifact-paper-qualification.md).
