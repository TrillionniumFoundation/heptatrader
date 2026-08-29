# Runtime and research scripts

Status: current
Applies to: `scripts/`
Verification: same-revision CI

`scripts/` contains bounded entry points used by the deterministic runtime, development loop or experimental research. Reusable logic belongs in typed C++ libraries or a Python package; new orchestration layers are not added.

## Development/runtime

- `dev_core.sh` — repository integrity, configure, core build, CTest and Python tests.
- `check_repository_integrity.py` — documentation/capability/build/install truth checks.
- `resolve_hepta_config.py` — canonical config source and supported-profile lock.
- `hepta_agent_mcp_launcher.py` / `hepta_agent_trust_domain.py` — identity-bound MCP launch/config.
- `hepta_broker_egress_policy.py` — target-host broker-port UID boundary.
- `verify_oms_journal_replay.py` — bounded OMS replay utility.

## Research transition

The canonical target is the compact `RunManifest -> EventLog -> RunSummary` protocol in `docs/RESEARCH-PROTOCOL.md`. Existing `hepta_market_*`, `hepta_strategy_*` and EURUSD scripts are experimental implementation inputs while G-009/G-010 remain open. Campaign, WATCH-lease, root-custodian and final-audit concepts are legacy debt to be removed from the current path, not extension points for new work.
