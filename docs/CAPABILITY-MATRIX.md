# Capability matrix

Status: current  
Applies to: repository-wide  
Last verified commit: moving `main`

状态定义：

- **Implemented**：代码路径存在，并纳入核心 build/test。
- **Experimental**：代码路径存在，但依赖外部 SDK、目标宿主或尚未形成完整生产闭环。
- **Scaffold**：只保留接口或未来绑定点；必须 fail closed。
- **Unsupported**：当前版本不得宣称或启用。

## Runtime and authority

| Capability | Status | Authority / boundary |
|---|---|---|
| Tool discovery and schema binding | Implemented | Gateway session-visible catalog |
| Typed Unix framing and peer identity | Implemented | Gateway/Execution IPC |
| Session capability enforcement | Implemented | Gateway + session supervisor |
| Command-id idempotency | Implemented | Execution Coordinator |
| Journal-before-send | Implemented | Execution Coordinator / OMS |
| Execution fencing and service identity | Implemented | Execution Service |
| Uncertain outcome recovery | Implemented | OMS replay + authoritative reconciliation |
| Owner-scoped cancel | Implemented | Execution-owned order projection |
| Authoritative flatten / reduce-only | Implemented for supported compositions | Execution Service only |
| Multi-agent capital allocation | Unsupported | Future deterministic portfolio plane |
| Agent-selected raw broker session | Unsupported | Forbidden by architecture |

## Venue support

| Venue / mode | Read | Mutation | State |
|---|---:|---:|---|
| Deterministic simulator | Yes | Yes | Implemented for local contract/fault tests |
| IB PAPER | Yes | Yes | Experimental; requires IB SDK and target host |
| CTP | No | No | Scaffold; connection fails closed |
| XT / MiniQMT | No | No | Scaffold; no fake ACK/order IDs |
| IB LIVE | No | No | Unsupported |
| CTP/XT LIVE | No | No | Unsupported |

## Agent integration

| Integration | Status | Notes |
|---|---|---|
| Native C++ client | Implemented | Versioned tool discovery and typed request path |
| `heptactl` | Implemented | Operator/developer CLI |
| MCP bridge | Implemented | Local adapter; no credential or broker authority |
| Codex plugin metadata | Implemented | Exposes MCP tools only |
| OpenClaw adapter | Compatibility only | Not a second execution authority |
| Autonomous portfolio/rebalance agent | Unsupported | Requires deterministic portfolio and budget contracts first |

## Research and strategy

| Capability | Status | Notes |
|---|---|---|
| Versioned strategy JSON | Implemented | Current repository contains EURUSD SHADOW definition |
| Deterministic strategy evaluation | Experimental | Python implementation, strategy-specific |
| Replay evaluation | Experimental | Not yet a general event-driven backtest engine |
| Dataset registry / point-in-time data | Unsupported | Required for general research platform |
| Feature registry | Unsupported | Required before multi-strategy scale-out |
| Experiment/model registry | Unsupported | Future research plane |
| SHADOW-to-PAPER automatic promotion | Unsupported | Promotion is explicit and operator-controlled |

## Promotion rule

A capability may move from Scaffold/Experimental to Implemented only when all of the following are true:

1. It has a real implementation rather than synthetic success events.
2. It is included in a bounded automated test lane.
3. Its authority, failure and recovery semantics are documented.
4. The runtime can report the capability truthfully through health/discovery.
5. Missing dependencies fail closed rather than silently degrading.
