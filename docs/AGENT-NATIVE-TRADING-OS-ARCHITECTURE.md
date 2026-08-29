# HeptaTrader AI-Agent Trading Runtime Architecture

Status: current  
Applies to: `HeptaTrade/`, `adapters/mcp/`, `plugins/`, `systemd/`  
Last verified commit: moving `main`

## 1. Product boundary

HeptaTrader is a model-agnostic deterministic trading control and execution runtime. Codex, OpenClaw and other agents are replaceable clients. They may query state and submit bounded intent, but they are not broker, order, portfolio or risk authorities.

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
          |
          | deterministic venue contract
          v
Simulator / IB PAPER
```

CTP and XT are scaffolds and must fail closed until a real transport, authoritative state projection and recovery contract exist. LIVE is unsupported.

## 2. Control-plane responsibilities

### Agent adapters

- `adapters/mcp/hepta_mcp_server.py`
- `plugins/heptatrader-agent-os/`
- `HeptaTrade/client/`
- `HeptaTrade/cli/heptactl*`

Responsibilities: discovery, request encoding, response parsing and bounded orchestration. They do not hold broker credentials, create venue order IDs or infer PAPER/LIVE authority.

### Tool Gateway

- `HeptaTrade/tool_host/`
- `HeptaTrade/tools/`

Responsibilities: peer identity, session token, capability, environment, schema and parameter validation; forwarding to the selected Execution Service; relaying owner-scoped events. It must not link broker SDK or credential symbols.

### Execution Service

- `HeptaTrade/execution/`
- `HeptaTrade/oms_journal*`
- `HeptaTrade/state/`
- `HeptaTrade/risk/`

The Execution Service is the only order authority. It owns:

- command-id idempotency and payload conflict detection;
- journal-before-send and durable receipts;
- service/session/decision fencing;
- deterministic pre-trade risk;
- venue order lifecycle and correlation;
- authoritative quote/order/position/account state;
- uncertain recovery and reconciliation;
- kill switch, cancel, reduce-only and flatten paths.

### Venue adapters

A venue adapter translates deterministic commands to venue protocol and venue events back to authoritative projections. It must not decide strategy, capital allocation or policy. A missing transport must return a stable unsupported error and may never synthesize a successful connection or broker ACK.

## 3. Data and decision planes

The runtime currently has a narrow experimental EURUSD SHADOW research path. A general AI-native quantitative system still requires explicit deterministic contracts for:

```text
point-in-time data
 -> features
 -> forecast / target exposure
 -> portfolio netting and capital allocation
 -> risk sizing
 -> order plan
 -> execution
```

Ordinary strategy agents should converge on forecast/target/TradeIntent APIs. Raw `trade.place_order` remains an execution-level capability for explicitly authorized execution/operator sessions; it is not the preferred long-term strategy API.

## 4. Mutation invariant

```text
Agent intent
 -> peer/session/capability validation
 -> normalized request and snapshot binding
 -> deterministic risk
 -> execution permit and command ID
 -> durable journal
 -> venue send
 -> execution event projection
 -> authoritative reconciliation
```

Required invariants:

1. Only Execution Service sends to a venue.
2. Agent/Gateway never owns broker credential or broker session.
3. Every risk-increasing mutation is durable before external send.
4. An uncertain retry reuses the exact command ID and payload.
5. Expired/fenced session, owner or lease cannot increase risk.
6. Venue/Execution projection is authoritative for orders and positions.
7. Unknown identity, protocol, quote, config, persistence or kill-switch state fails closed.
8. Cancel, reduce-only and flatten remain available whenever they can safely reduce risk.

## 5. Snapshot and event model

Agent decisions should bind to a single Execution-owned snapshot identity containing service epoch, fencing generation and collection watermark. Tool discovery and descriptor metadata are session control-plane data and should be cached rather than rebuilt for every market sample. Incremental state changes should use the bounded event feed instead of repeated full polling.

## 6. Supported modes

### Simulator

`hepta-executiond` provides a deterministic process-local venue for contract, recovery and fault tests. It is not proof of IB/CTP/XT venue parity.

### IB PAPER

`hepta-ib-executiond` is the broker-owning PAPER authority when built with the IB SDK. It runs under a separate OS identity with credential isolation, journal, quote/state projection, hard risk limits, kill switch and reconciliation.

### LIVE

LIVE is unsupported. A future LIVE path must reuse the same Execution authority, journal, fencing, risk and reconciliation contracts; no Agent or legacy monolith bypass is allowed.

## 7. Development and release boundary

`./scripts/dev_core.sh` and the minimal GitHub workflow protect runtime correctness and authority boundaries. Packaging signatures, SBOM, host compliance and formal release evidence are separate on-demand concerns and must not become ordinary strategy iteration gates.
