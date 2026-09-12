# Runtime, research, validation, and qualification scripts

Status: CURRENT

## Development and documentation

- `dev_core.sh` configures, builds, and runs the canonical core CTest label.
- `check_documentation.py` validates module/capability truth and documentation links.
- `check_component_coverage.py` derives the production file set from the exact Git index, assigns each path to the most-specific module implementation boundary, and compares repository CMake translation-unit owners with `docs/build-targets.json`.
- `verify_build_ownership.py` compares fresh CMake File API target and translation-unit models with `docs/build-targets.json`; additions fail closed until they have an explicit canonical owner.
- `verify_source_gap_closures.py` checks the gap registry and supplemental static risk/venue/OMS contracts, and executes documentation, fresh build-ownership, qualification-boundary and profile validators. It does not execute C++ behavioral tests or prove Broker-send ordering; those are checked by the separately built core suites. Its result cannot close external Broker qualification.
- `resolve_hepta_config.py`, `validate_sim_data.py`, and `verify_oms_journal_replay.py` support configuration and recovery checks where applicable.

## Release and preflight

- `build_release_package.py` installs or consumes a canonical staging tree and emits a deterministic archive, SHA-256 sidecar and private package receipt. It rejects links, special files, secret-like paths, size overflow and output replacement.
- `hepta_preflight.py` verifies the package without extraction and can inspect the installed static host boundary. It never grants PAPER/LIVE authority and never changes the host.
- `run_release_simulator_smoke.py` takes a preflight-admitted core archive through a new candidate slot, executes the installed Agent/Gateway/Execution/simulator E2E binary, switches to a previous slot, executes rollback validation, re-promotes the candidate, and writes one no-replace non-authorizing deployment record.

The package digest is the identity carried into later qualification and deployment. Rebuilding from the same source is a new candidate unless the complete package bytes and digest are identical.

## Agent and host runtime

- `hepta_agent_mcp_launcher.py` launches the MCP bridge under the expected identity.
- `hepta_agent_trust_domain.py` validates the Agent trust-domain configuration.
- `hepta_broker_egress_policy.py` applies the fixed UID/port Broker boundary.

## SHADOW research

`hepta_market_*`, `hepta_official_source_capture.py`, `hepta_strategy_*`, `hepta_eurusd_confirmed_momentum_strategy.py`, and `validate_hepta_strategy_decision_receipt.py` implement read-only evidence and replay components. The repository does not currently contain a canonical bounded observer/controller and does not install these research scripts automatically.

## Trusted PAPER rollout and certification

`check_qualification_trust_boundary.py`, `build_ib_candidate_artifact.sh`, and `verify_ib_candidate_artifact.py` establish the build-once immutable-artifact boundary.

`run_ib_paper_artifact_rollout.sh` and `verify_ib_paper_rollout.py` own the small PAPER-V4 progressive rollout interface. They permit canary/pilot/extended to increase only the number of independently terminal flat round trips; they never widen the existing one-unit instantaneous P1 exposure envelope and their receipts have no authorization effect.

`run_ib_paper_artifact_qualification.sh` and `verify_ib_paper_qualification.py` retain the separate qualification-only PAPER-V5 twelve-scenario resilience/certification path. `verify_canonical_ib_paper_profile.py` keeps the source-controlled profile contract canonical.

These trusted-main components do not make external runners, credentials, Broker sessions, host policy, external harness support or receipts exist; the verifiers fail when those controls are absent. The external harness remains an owner-controlled input and must implement the documented progressive-rollout mode before real P1 stages can execute.

Do not add developer-specific paths, untrusted `eval`/`source`, Broker secrets, encoded transfer payloads, an alternate order path, or a branch-mutating remediation carrier to this directory.

## PAPER continuation and operational diagnostics

The source-controlled P1 orchestrator is `hepta_ib_paper_harness.py`; versioned,
root-owned host custody is defined by `docs/technical/ib-paper-host-driver.md`.
`hepta_paper_campaign.py` reuses completed stages without resending and blocks
unresolved attempts; `hepta_paper_rollout_host.py` binds it to the admitted artifact
and exports retained success/failure evidence. `resolve_ib_artifact.py` selects
an exact prior artifact ID, not latest/main. `hepta_evidence_io.py` centralizes
bounded descriptor-based I/O; `hepta_campaign_evidence.sh` retains failure data.

`run_runtime_resilience.sh` is the single real GCC/Clang sanitizer implementation.
`ci_workflow_contract.py` reads YAML job structure, not inert comments.
`hepta_runtime_diagnostics.py` provides bounded read-only journal capacity metrics;
its output never substitutes for Execution reconciliation.
