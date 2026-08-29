# Capability matrix

Status: current
Applies to: repository-wide
Verification: same-revision CI

State definitions:

- **Implemented** — active code path included in the bounded core build/tests.
- **Experimental** — code exists but external dependencies, host validation or full operational closure is incomplete.
- **Planned** — target contract exists; runtime must not advertise it as available.
- **Unsupported** — the current version rejects or omits the capability.

## Runtime and authority

| Capability | State | Authority / boundary |
|---|---|---|
| Tool discovery, schema hash and typed local framing | Implemented | Gateway session-visible catalog |
| Session identity and capability enforcement | Implemented | Gateway + session supervisor |
| Stable command-id idempotency and journal-before-send | Implemented | Execution Coordinator / OMS |
| Execution service epoch and fencing | Implemented | Execution Service |
| Uncertain command recovery and owner-scoped control | Implemented | OMS + authoritative reconciliation |
| Authoritative flatten/reduce-only | Implemented for supported compositions | Execution Service only |
| Generation-consistent decision snapshot | Experimental | Execution-owned state authority |
| Target-position preview/apply permit path | Experimental | Execution Service; ordinary Agent target |
| Portfolio netting and strategy capital budgets | Planned | future deterministic portfolio compiler |
| Agent-selected broker session or venue truth | Unsupported | forbidden by architecture |

## Venue support

| Venue / mode | Read | Mutation | State |
|---|---:|---:|---|
| Deterministic simulator | Yes | Yes | Implemented for local contract/fault tests |
| IB PAPER | Yes | Yes | Experimental; pinned IB SDK and reviewed host required |
| CTP | No | No | Unsupported scaffold; typed fail-closed result |
| XT / MiniQMT | No | No | Unsupported scaffold; no synthetic ACK/order IDs |
| Any LIVE environment | No | No | Unsupported and absent from accepted configuration |

## Agent integration

| Integration | State | Notes |
|---|---|---|
| Native C++ client | Implemented | versioned discovery and typed request path |
| `heptactl` | Implemented | operator/developer CLI |
| MCP bridge | Implemented | local adapter; no broker credential or venue authority |
| Codex plugin metadata | Implemented | launches MCP adapter only; not a complete Agent OS |
| Ordinary target-position Agent profile | Experimental | raw place authority must be absent |
| Multi-Agent portfolio/capital allocator | Planned | requires deterministic portfolio compiler first |

## Research

| Capability | State | Notes |
|---|---|---|
| Versioned strategy definition | Implemented | current EURUSD SHADOW definition |
| Deterministic strategy evaluation | Experimental | strategy-specific Python path |
| Compact manifest/replay/summary protocol | In progress | replaces campaign/finalizer machinery |
| Purged walk-forward, costs, capacity and regime evaluation | In progress | required before PAPER proposal |
| Point-in-time dataset registry and feature registry | Planned | required for multi-strategy scale |
| Automatic SHADOW-to-PAPER/LIVE promotion | Unsupported | explicit reviewed runtime change only |

A capability advances only when implementation, negative tests, current documentation, runtime discovery and same-head CI agree. Missing dependencies always fail closed.
