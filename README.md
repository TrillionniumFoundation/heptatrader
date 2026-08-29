# HeptaTrader

HeptaTrader is a **model-agnostic deterministic trading control and execution runtime for AI Agents**. Codex, OpenClaw and other clients may inspect authoritative state and submit bounded intents through MCP or the native client. They never own the broker session, OMS, portfolio truth, final risk decision, reconciliation or kill switch.

> HeptaTrader is not an “LLM directly calls a broker API” framework. The model is replaceable; the trusted runtime is not.

## Runtime truth

| Capability | State | Runtime truth |
|---|---|---|
| Tool Gateway / typed Unix protocol | implemented | peer identity, session, capability, schema hash and bounded framing |
| Execution Service / OMS journal | implemented | journal-before-send, stable command IDs, fencing and uncertain recovery |
| Deterministic simulator | implemented | local end-to-end execution and failure-path testing |
| IB PAPER | experimental | separately owned broker authority, deterministic risk, kill switch and reconciliation |
| CTP | unsupported / fail-closed | no real transport; must return `VENUE_NOT_IMPLEMENTED` |
| XT/QMT | unsupported / fail-closed | no real transport; no synthetic connection, ACK or local order success |
| LIVE | unsupported | no default or certified LIVE authority |
| Codex / OpenClaw | client adapters | no broker credential or authoritative state ownership |
| EURUSD SHADOW | experimental research | deterministic replay/decision output only; grants no PAPER/LIVE capability |

The canonical capability table is [`docs/CAPABILITY-MATRIX.md`](docs/CAPABILITY-MATRIX.md). The canonical work plan and gap registry are [`docs/development/PLAN.md`](docs/development/PLAN.md).

## Authority boundary

```text
Codex / Agent / Operator
          |
          | MCP / heptactl / native client
          v
Tool Gateway
          |
          | authenticated typed Unix protocol
          v
Execution Service
          |\
          | +-- decision snapshot / deterministic risk
          | +-- OMS journal / idempotency / reconciliation
          v
Simulator or implemented broker adapter
```

Non-negotiable invariants:

1. only Execution Service may send venue mutations;
2. Agent and Gateway hold no broker credential and link no broker adapter;
3. every new mutation is durable before send;
4. an uncertain retry reuses the exact command ID and payload;
5. session, lease, epoch and fencing state are rechecked at authority boundaries;
6. account, position and order truth comes from venue/Execution projections;
7. unknown identity, quote, configuration, persistence or reconciliation state fails closed;
8. cancel, strict reduce-only and authoritative flatten remain available when safe exit is possible.

## Development loop

```bash
./scripts/dev_core.sh
```

Or:

```bash
cmake --preset core-release
cmake --build --preset core-release
ctest --preset core-release
```

The loop builds the IB-disabled deterministic runtime, runs core CTest and Python contract tests, and is also exercised by a read-only GitHub Actions job. It does not generate round manifests, evidence-closure bundles, host attestations or self-merging automation.

## Minimal runtime install

```bash
cmake --preset core-release
cmake --build --preset core-release
cmake --install build/core-release --component runtime
```

The runtime component contains only binaries, MCP bridge/launcher, systemd templates, sysusers/tmpfiles and example configuration. Signing, SBOM and distribution assembly are separate, on-demand release concerns.

## Repository map

```text
HeptaTrade/       active C++ Gateway, Execution, OMS, risk, state and venue runtime
adapters/mcp/     MCP bridge
plugins/          Agent client metadata
strategies/       versioned strategy definitions
research/         machine-readable research contract and reproducibility manifest
scripts/          bounded development/config/research utilities
systemd/          fixed Gateway, simulator and IB PAPER service definitions
tests/            core contract, integration and failure-path tests
docs/             current, experimental and proposal documentation
legacy/           inactive historical source; active targets may not depend on it
```

Start at [`docs/README.md`](docs/README.md). A current document must describe paths and commands that exist at the same revision; future design is labelled experimental/proposal and never advertised as implemented capability.
