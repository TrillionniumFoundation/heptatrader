# Runtime, research, validation, and qualification scripts

Status: CURRENT

## Development and documentation

- `dev_core.sh` configures, builds, and runs the canonical core CTest label.
- `check_documentation.py` validates module/capability truth and documentation links.
- `verify_build_ownership.py` compares fresh CMake file-api target and translation-unit models with `docs/build-targets.json`; additions fail closed until they have an explicit canonical owner.
- `verify_source_gap_closures.py` checks the gap registry and supplemental static risk/venue/OMS contracts, and executes documentation, fresh build-ownership, qualification-boundary and profile validators. It does not execute C++ behavioral tests or prove broker-send ordering; those are checked by the separately built core suites. Its result cannot close external governance or broker qualification.
- `resolve_hepta_config.py`, `validate_sim_data.py`, and `verify_oms_journal_replay.py` support legacy/configuration checks where still applicable.

## Agent and host runtime

- `hepta_agent_mcp_launcher.py` launches the MCP bridge under the expected identity.
- `hepta_agent_trust_domain.py` validates the Agent trust-domain configuration.
- `hepta_broker_egress_policy.py` applies the fixed UID/port broker boundary.

## SHADOW research

`hepta_market_*`, `hepta_official_source_capture.py`, `hepta_strategy_*`, `hepta_eurusd_confirmed_momentum_strategy.py`, and `validate_hepta_strategy_decision_receipt.py` implement read-only evidence and replay components. The repository does not currently contain a canonical bounded observer/controller and does not install these scripts automatically.

## Trusted qualification

`github_qualification_evidence.py`, `verify_github_governance*.py`, `verify_qualification_candidate.py`, `build_ib_candidate_artifact.sh`, `verify_ib_candidate_artifact.py`, `run_ib_paper_artifact_qualification.sh`, and `verify_ib_paper_qualification.py` are trusted-main qualification components. They do not make external teams, rulesets, runners, credentials, broker sessions, or receipts exist; the verifiers fail when those controls are absent.

Do not add developer-specific paths, untrusted `eval`/`source`, broker secrets, encoded transfer payloads, or an alternate order path to this directory.
