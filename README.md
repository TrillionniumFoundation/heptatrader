# HeptaTrader

HeptaTrader is a **model-agnostic deterministic trading control and execution runtime for AI agents**, with an experimental reproducible quantitative-research plane. Codex is the first supported Agent client through MCP; it never owns broker credentials, portfolio truth, final risk decisions, OMS state or venue sessions.

> HeptaTrader is not an “LLM directly calls a broker API” framework. Models are replaceable clients; the trusted runtime is deterministic.

## Capability truth

| Capability | State |
|---|---|
| Typed local Tool Gateway, native client and MCP bridge | Implemented |
| Session/capability enforcement and bounded framing | Implemented |
| OMS journal, stable command IDs, replay/recovery contracts | Implemented |
| Deterministic simulator | Implemented for contract and failure-path tests |
| Shared deterministic risk core | Implemented; authoritative portfolio wiring is being completed |
| Decision snapshot and target-position intent | In progress |
| IB PAPER | Experimental; external SDK/host required |
| Research/replay | Experimental; SHADOW only |
| CTP and XT/QMT transport | Unsupported / fail-closed |
| LIVE mutation | Unsupported |

The authoritative matrix is [`docs/CAPABILITY-MATRIX.md`](docs/CAPABILITY-MATRIX.md). The single gap registry is [`docs/development/PLAN.md`](docs/development/PLAN.md).

## Authority boundary

```text
Codex / Agent / operator
        |
        | bounded MCP/native tools
        v
Tool Gateway
        |
        | authenticated typed Unix protocol
        v
Execution Service
        |-- authoritative state / portfolio / deterministic risk
        |-- OMS journal / idempotency / reconciliation
        v
Simulator or explicitly supported PAPER adapter
```

Non-negotiable invariants:

1. only Execution Service performs venue mutation;
2. every new mutation is durable before send;
3. retries use the same command ID and normalized payload;
4. unknown identity, state, quote, generation, persistence or reconciliation fails closed;
5. safe cancel/reduce-only/flatten paths remain available when provable;
6. research artifacts never grant runtime capability.

## Development loop

```bash
./scripts/dev_core.sh
```

This runs repository-truth checks, Release core build, core CTest and Python contract tests. The matching GitHub Actions workflow is read-only and never approves or merges its own PR.

## Minimal simulator install

```bash
cmake --preset core-release
cmake --build --preset core-release --target hepta_runtime_binaries
cmake --install build/core-release --component runtime
```

## Repository map

```text
HeptaTrade/       active C++ Gateway, Execution, OMS, risk, state and simulator/PAPER runtime
adapters/mcp/     MCP adapter
plugins/          Agent client metadata only
schemas/          canonical protocol/schema catalog (planned/being introduced)
strategies/       versioned strategy definitions
research/         compact research run contract
scripts/          bounded development and runtime utilities
systemd/          active service/socket templates and examples
tests/            unit, contract, integration and failure-path tests
docs/             current contracts, proposals and deprecated documentation
legacy/           inactive historical source; active targets may not depend on it
```

Start with [`docs/PRODUCT-SCOPE.md`](docs/PRODUCT-SCOPE.md), [`docs/README.md`](docs/README.md) and the canonical plan.
