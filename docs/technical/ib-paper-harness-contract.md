# IB PAPER external harness contract

Status: CURRENT  
Applies to: optional owner-operated IB PAPER qualification only

## Purpose

The external qualifier remains outside the repository because it owns host-specific Broker session control and may access credentials that repository code must never receive. Its behavior is nevertheless not an opaque source of truth. The repository defines the complete scenario, evidence, isolation and terminal-state contract that a qualifying harness must satisfy.

The canonical scenario list is [`../ib-paper-qualification-scenarios-v1.json`](../ib-paper-qualification-scenarios-v1.json). `tests/python/test_ib_paper_scenario_contract.py` requires that reviewable contract to agree exactly with the assertions and evidence kinds enforced by `scripts/verify_ib_paper_qualification.py`.

## Trust boundary

The qualification workflow has two distinct principals:

1. a no-secret builder produces and verifies an immutable candidate artifact from the exact `main` revision selected at owner-authorized workflow dispatch;
2. the dedicated `desktop-ib-paper` runner executes only that verified candidate through an independently pinned qualifier on the desktop.

A dispatch with `mutation_mode=false` runs only the exact-source, network-disabled
IB candidate build and synthetic probe tests. It does not allocate the PAPER
runner, enter the Broker environment, run a campaign or issue qualification.
`mutation_mode=true` adds the existing separately gated qualification job; a
successful build alone never approves orders. This keeps compiler feedback
independent from approval to exercise a real PAPER session.

The builder is `desktop-ib-builder`; the qualifier additionally requires the
`desktop-ib-paper` runner label and exact runner name. Both retain the
`trillionnium-ib-paper` group and their distinct existing role labels.
The generic desktop runner and X230 are not fallback qualification hosts.

The requested candidate SHA must equal the dispatch event's immutable `github.sha` before either self-hosted runner is allocated. That converts a moving branch pointer into an immutable source/artifact identity at admission. Once the candidate artifact exists, later movement of `refs/heads/main` is intentionally irrelevant to that campaign: it cannot change the source SHA, executable digest, harness, profile, Broker account, host or evidence already bound to the qualification subject. A changed bound input requires a new campaign.

Candidate code must not inherit Actions credentials, repository write credentials or raw Broker credentials. The qualifier is responsible for Broker login/session custody and for constraining candidate networking to the approved PAPER path.


Build/probe errors retain `build-status.json` and at most the final 1 MiB of
`candidate-build.log` in the run/attempt-bound builder diagnostics artifact,
before the private build workspace is removed. The status reports the phase,
exit code, full log byte count, truncation and the retained slice's digest.
Candidate log text is never replayed into the Actions command stream; source trees,
SDK directories and host environment dumps are not uploaded. These diagnostic files
are not a qualification receipt. Failures before the private workspace exists
remain explicit step errors and may have no diagnostic artifact.

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
- `broker-host=127.0.0.1` and `broker-port=4002`, forwarded explicitly by the controller;
- `mode=bounded-mutations`.

The harness may not silently expand the operation set, run a different executable, substitute another account/environment, or reuse a stale evidence directory.
The controller rejects missing, remote, hostname-based or different-port endpoint
bindings before reserving an attempt or spawning the harness. Its cleared child
environment repeats the binding in `HEPTA_QUALIFICATION_EXPECTED_BROKER_HOST` and
`HEPTA_QUALIFICATION_EXPECTED_BROKER_PORT`. The independently pinned desktop
harness must consume the new `--broker-host`/`--broker-port` arguments and reject
any mismatch with its protected host policy; an older harness is not implicitly
compatible. Port 4002 alone never establishes account mode or PAPER readiness.

## Scenario execution semantics

Every canonical scenario is an effect-bound experiment, not a text assertion. The harness must induce or observe the actual condition and retain the Broker/runtime evidence required by the scenario contract. Examples include a genuine partial fill, an actual disconnect/reconnect epoch transition, an outcome whose send result is initially uncertain, and process restart followed by journal replay and authoritative reconciliation.

A scenario is not satisfied by emitting its assertion names. The verifier requires evidence files with declared kinds, sizes and digests and binds the result to the exact candidate, source, harness, Broker session and timing envelope.

## Evidence contract

The harness writes `qualification-result.json` only after scenario execution. Evidence is immutable input to the repository verifier and must include the exact evidence kinds required by each scenario. The verifier rejects missing, duplicate, unexpected, oversized, changed or path-escaping evidence.

Raw secret values must never appear in evidence. Account and host identity use bounded fingerprints. Broker callbacks, authoritative snapshots, execution events and OMS journal extracts should retain the minimum fields needed to prove the asserted state transition while omitting credentials and session tokens.

### Broker endpoint in the result

New desktop campaigns use `hepta.ib-paper-attempt.v2` and
`hepta.ib-paper-qualification.v2`. The controller persists `broker_endpoint`
before launching the harness; the harness reports its actually observed
`broker.endpoint` as `{"host":"127.0.0.1","port":4002}`. The verifier compares
both with the explicit workflow endpoint and retains the binding in the V2
verification receipt. A missing endpoint, hostname, remote address, wrong port
or non-integer port cannot produce a desktop qualification receipt.

V1 results remain readable only as historical endpoint-unbound evidence. They
cannot satisfy a current controller attempt or explicit endpoint verification.
The harness must observe the connection it used, not merely copy the requested
arguments. Endpoint agreement does not replace the existing account-mode,
identity, scenario, terminal-state or independent host-policy checks.

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

## Attempt custody and verified publication

`run_ib_paper_artifact_qualification.sh` is the stable entry point; its isolated
Python controller is `scripts/run_ib_paper_campaign.py`. The controller reserves
an attempt directory before launching the separately digest-pinned harness.
Existing attempt paths, including failed attempts, cannot be reused. The trusted
attempt parent must already exist. Reservation fsyncs the parent directory as
well as the attempt metadata before any harness child can start.

```text
attempt/attempt.json                  # bounded source/binary/harness/status metadata
attempt/evidence/                    # retained private raw evidence; never recursively uploaded
attempt/evidence/qualification-result.json
attempt/evidence/qualification-verification.json  # only after full verifier acceptance
attempt/verified-evidence.tar         # verified explicit file allowlist only
sibling .hepta-paper-private-*/       # private HOME; outside every publication path
```

States are RESERVED, RUNNING, HARNESS_SUCCEEDED_AWAITING_VERIFICATION,
HARNESS_FAILED, TIMEOUT, INTERRUPTED or CONTROLLER_FAILED. None grants trading
authorization. The fixed timeout is 900 seconds plus at most 30 seconds for
termination; test-only calls may shorten, never enlarge, the bounds. The child
runs in a separate process group with a cleared environment. INT/TERM/HUP and
timeout stop the child group and retain attempt/raw evidence. A hard kill or
power loss can leave RESERVED/RUNNING: treat it as incomplete, not as safe to
resend. The separately pinned host harness remains responsible for cgroup,
network/proxy, credentials, durable bounded evidence and final Broker cleanup.

Private HOME is removed separately after child termination. stdout/stderr are
not replayed into Actions logs because they may contain Broker secrets; the
external harness must emit bounded sanitized machine evidence. Failures retain
raw files on the trusted host for operator investigation. Source code cannot
infer that arbitrary raw text is secret-free. Retention/quotas and secure host
cleanup require operational ownership, not a blanket `rm -rf` trap.

The final verifier receives `--attempt`, checks successful controller state and
matching immutable bindings, then validates the result and every referenced
scenario file. `--publication-archive` atomically creates a no-overwrite tar
containing only the result, verification receipt and the verified referenced
files. It rechecks file identity/digests while reading them. Extra files, links,
FIFOs, drift and failed results do not get published. The workflow uploads only
attempt metadata, that archive and the receipt attestation bundle. A failed
attempt normally has no verified archive; its private raw directory stays on
host and is not an Actions artifact.

Behavior is exercised without Broker I/O by `test_paper_campaign_evidence.py`,
`test_paper_evidence_publication.py` and executable workflow-block tests in
`test_ib_workflow_interfaces.py`. The structure checker is not a replacement
for those behaviors or external qualification.

The controller is included in both exact-index critical paths and the candidate
builder's trusted-file digest set. Changing it requires new provenance and a
new candidate/qualification binding; an old receipt lacking that file is not
silently accepted. `test_campaign_provenance.py` changes actual fixture bytes
and verifies digest drift/rejection rather than counting source tokens.
