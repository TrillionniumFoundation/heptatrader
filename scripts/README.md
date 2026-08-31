# Runtime, research and validation scripts

Status: current
Applies to: `scripts/`
Verification: `./scripts/dev_core.sh` and Python contract tests
Authority: script inventory

Reusable behavior belongs in typed C++ libraries or bounded Python packages. Scripts are thin entry points and validators.

## Development and architecture

- `dev_core.sh` — canonical local core loop.
- `check_repository_integrity.py` — repository, capability, workflow and active/legacy boundaries.
- `check_documentation_control_plane.py` — V2 document registry, aliases, module/capability/contract/program cross-references and DAG.
- `check_schema_catalog.py` — checked-in protocol/schema drift.
- `check_module_discipline.py` — compatibility ownership/no-growth guard during ModuleManifest V2 migration.
- `check_install_tree.py` — staged runtime allowlist.
- `reliability_core.sh` — sanitizer, crash/replay, malformed-protocol and performance fixtures.
- `resolve_hepta_config.py` — configuration authority and supported-profile lock.

## Runtime support

- Agent/MCP launcher and trust-domain scripts remain unprivileged.
- Broker egress policy is a target-host security boundary.
- OMS replay and observability utilities never replace durable journal or authoritative state.

## Research

The current research protocol is documented in [`../docs/research/RESEARCH-PROTOCOL.md`](../docs/research/RESEARCH-PROTOCOL.md). Historical campaign/finalizer scripts are not extension points; new reusable research behavior must move into the current package and registries.
