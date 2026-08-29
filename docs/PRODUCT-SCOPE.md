# Product scope and maturity

Status: current
Applies to: repository-wide
Verification: same-revision CI

## Product statement

HeptaTrader is a model-agnostic deterministic trading control and execution runtime for AI agents, with an experimental reproducible research plane. Codex is a supported Agent client through MCP; it is not embedded into the trusted execution core.

## Implemented core

- typed local Tool Gateway and native/MCP clients;
- identity/session/capability enforcement;
- deterministic simulator;
- OMS journal, stable command IDs and replay/recovery contracts;
- owner-scoped order control and authoritative flatten where supported;
- shared deterministic pre-trade risk core;
- bounded local/PR build and test loop.

## Experimental

- IB PAPER adapter and target host integration;
- generation-consistent decision snapshot and target-position intent path;
- EURUSD SHADOW research strategy and replay;
- deployment hardening outside the deterministic simulator fixture.

## Unsupported

- all LIVE mutation;
- CTP and XT/QMT transport or order lifecycle;
- automatic strategy promotion;
- multi-Agent capital allocation and production portfolio optimization;
- model-owned broker sessions or credentials.

## Product naming rule

Use **Agent-compatible deterministic trading runtime** for the current product. Use **AI-native quantitative trading system** only after research, portfolio and execution planes form a tested closed loop. Use **Agent OS** only after multi-Agent lifecycle, evaluation, memory/workspace and capital-allocation contracts exist.
