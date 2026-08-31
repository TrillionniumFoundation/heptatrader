# Runtime, research and validation scripts

Status: current
Applies to: `scripts/`
Verification: `./scripts/dev_core.sh` and Python contract tests
Authority: script inventory

Reusable behavior belongs in typed C++ libraries or bounded Python packages. Scripts remain deterministic entry points and validators.

## Documentation and architecture

- `generate_documentation_views.py` — renders Capability Matrix, Contract Index, Module Map and Roadmap from registries.
- `check_documentation_control_plane.py` — enforces one active document graph, zero aliases/history, generated-view parity and full registry traceability.
- `check_repository_integrity.py` — validates immutable CI, active/legacy boundaries, current links and capability truth.
- `check_module_discipline.py` — validates ModuleManifest V2, current target/source coverage, shared-migration gaps and source-size budgets.
- `check_schema_catalog.py` — validates checked-in runtime protocol/schema drift.

## Development and reliability

- `dev_core.sh` — canonical local loop; generated docs and architecture checks run before build.
- `check_install_tree.py` — verifies the installed canonical docs and rejects old filenames.
- `reliability_core.sh` — sanitizer, crash/replay, malformed-protocol and performance fixtures.
- `resolve_hepta_config.py` — configuration authority and supported-profile lock.

## Runtime and research

Agent/MCP launchers remain unprivileged; Broker egress, OMS replay and observability tools never replace Execution authority. The current research protocol is [`../docs/research/RESEARCH-PROTOCOL.md`](../docs/research/RESEARCH-PROTOCOL.md). Historical campaign/finalizer scripts are migration debt, not extension points.
