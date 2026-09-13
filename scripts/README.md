# Runtime, research, validation, and qualification scripts

Status: CURRENT

## Development and documentation

- `dev_core.sh` configures, builds, and runs the canonical core CTest label.
- `check_documentation.py` validates module/capability truth and documentation links.
- `check_component_coverage.py` derives the production file set from the exact Git index, assigns each path to the most-specific module implementation boundary, and compares repository CMake translation-unit owners with `docs/build-targets.json`.
- `verify_build_ownership.py` compares fresh CMake File API target and translation-unit models with `docs/build-targets.json`; additions fail closed until they have an explicit canonical owner.
- `check_gap_register.py` validates an extensible issue inventory; open work is allowed. `--release-profile core` or `ib-paper` additionally rejects explicitly scoped unresolved release blockers. It never grants Broker authority.
- `resolve_hepta_config.py`, `validate_sim_data.py`, and `verify_oms_journal_replay.py` support configuration and recovery checks where applicable.

## Release and preflight

- `build_release_package.py` installs or consumes a canonical staging tree and emits a deterministic archive, SHA-256 sidecar and private package receipt. It rejects links, special files, secret-like paths, size overflow and output replacement.
- `hepta_preflight.py` verifies the package without extraction and can inspect the installed static host boundary. It never grants PAPER/LIVE authority and never changes the host.
- `run_release_simulator_smoke.py` takes a preflight-admitted core archive through a new candidate slot, executes the installed Agent/Gateway/Execution/simulator E2E binary once, verifies atomic rollback and re-promotion pointer transitions, and writes one no-replace non-authorizing deployment record. The previous slot is seeded from the same verified artifact; N-1 compatibility requires a separately verified prior artifact.

The package digest is the identity carried into later qualification and deployment. Rebuilding from the same source is a new candidate unless the complete package bytes and digest are identical.

## Agent and host runtime

- `hepta_agent_mcp_launcher.py` launches the MCP bridge under the expected identity.
- `hepta_agent_trust_domain.py` validates the Agent trust-domain configuration.
- `hepta_broker_egress_policy.py` applies the fixed UID/port Broker boundary.

## SHADOW research

`hepta_market_*`, `hepta_official_source_capture.py`, `hepta_strategy_*`, `hepta_eurusd_confirmed_momentum_strategy.py`, and `validate_hepta_strategy_decision_receipt.py` implement read-only evidence and replay components. The repository does not currently contain a canonical bounded observer/controller and does not install these research scripts automatically.

## Trusted qualification

`check_qualification_trust_boundary.py`, `build_ib_candidate_artifact.sh`, `verify_ib_candidate_artifact.py`, `run_ib_paper_artifact_qualification.sh`, `verify_ib_paper_qualification.py`, and `verify_canonical_ib_paper_profile.py` are trusted-main qualification components. They do not make external runners, credentials, Broker sessions or receipts exist; the verifiers fail when those controls are absent.

Do not add developer-specific paths, untrusted `eval`/`source`, Broker secrets, encoded transfer payloads, an alternate order path, or a branch-mutating remediation carrier to this directory.
