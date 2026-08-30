# Module ownership and extraction discipline

Status: current
Applies to: active C++ runtime, Python runtime/research utilities and tests
Verification: `python3 scripts/check_module_discipline.py`

Authority follows dependency direction rather than file size. Execution owns venue
mutation; broker adapters own transport only; risk and portfolio modules own
deterministic policy; Gateway and clients remain unprivileged. The machine-readable
map is `module-ownership-v1.json`.

The portfolio module is deliberately a pure policy boundary: `PortfolioCompiler`
accepts only typed strategy intents, an authoritative generation-tagged
snapshot and a capital policy. It must not include or call Execution, venue,
session, credential, socket or permit code. Trusted Simulator orchestration may
consume its bounded net target and then hand the result to Execution's existing
risk/journal/permit path. The ordinary Agent target-position path is a
single-intent Execution flow and is not a substitute for that orchestration or
for a multi-Agent allocator.

New active C++ files may not exceed 2,500 lines and new Python libraries may not
exceed 1,200 lines. Existing exceptions are frozen at their audited line count and
may not grow. The active `research/` package is checked by the same rule;
`research/run_protocol.py` is a temporary frozen exception owned by the research
team while its evaluator/CLI split is planned. New behavior must first extract a
bounded state machine, codec or library. This branch already performs targeted
extraction for bounded JSON, runtime telemetry, target-position intent and the
portfolio compiler without moving broker authority out of Execution Service.
