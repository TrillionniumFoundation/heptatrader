# Runtime and research scripts

`scripts/` contains bounded entry points used by the deterministic runtime or research loop. Large reusable logic should migrate into typed C++ libraries or a Python package rather than growing additional orchestration layers.

## Development

- `dev_core.sh` — repository integrity, configure, core build, CTest and Python tests.
- `check_repository_integrity.py` — documentation/path, active/legacy, workflow, manifest and unsupported-venue truth checks.
- `resolve_hepta_config.py` — canonical config source and profile lock.
- `validate_sim_data.py` — simulator input validation.
- `verify_oms_journal_replay.py` — OMS journal replay verification.

## Agent runtime

- `hepta_agent_mcp_launcher.py` — launch the MCP bridge under a fixed identity/environment.
- `hepta_agent_trust_domain.py` — strict trust-domain config reader.
- `hepta_broker_egress_policy.py` — minimal UID/port nftables boundary.

## Research

- `hepta_market_*` and `hepta_official_source_capture.py` — market context and deterministic normalization.
- `hepta_strategy_*` — strategy contract, SHADOW runner and replay evaluation.
- `hepta_eurusd_confirmed_momentum_strategy.py` — experimental EURUSD implementation.
- `validate_hepta_strategy_decision_receipt.py` — bounded SHADOW decision validation.

The canonical machine-readable research contract is `research/manifest-v1.json`. This directory contains no release round, evidence-closure, dynamic PAPER campaign, repair/renew/finalizer, host attestation, self-merge or hard-coded personal-workspace workflow.
