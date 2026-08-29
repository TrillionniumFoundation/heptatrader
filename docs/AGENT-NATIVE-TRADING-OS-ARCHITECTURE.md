# HeptaTrader AI-Agent trading runtime architecture

Status: current
Applies to: `HeptaTrade/`, `adapters/mcp/`, `plugins/`, `research/`, `systemd/`
Verification: same-revision CI

## 1. Architectural statement

HeptaTrader is a deterministic trading runtime used by AI agents. Codex and other models are untrusted, replaceable clients. They may inspect bounded authoritative state and submit goals; they never own the broker session, account truth, OMS, final risk decision, reconciliation or kill switch.

## 2. Four planes

### Research plane

Point-in-time inputs, deterministic feature/strategy code, replay, costs and validation. Outputs forecasts or bounded target intents. Research has no runtime capability.

### Agent plane

MCP/native clients, identity-bound session and capability catalog. Ordinary Agents use decision snapshots and target-position intent. Raw orders are an operator-only surface.

### Portfolio and risk plane

Trusted target normalization, cross-strategy netting, capital budgets, authoritative valuation and deterministic risk. This plane converts desired state into an execution delta.

### Execution plane

OMS, journal-before-send, stable command IDs, fencing, venue lifecycle, callback projection, reconciliation, kill switch and safe exits.

## 3. Process boundary

```text
Agent UID                       Execution UID
---------                       -------------
Codex/model                     broker adapter + credential
MCP adapter                     authoritative venue state
Tool Gateway                    OMS journal / reconciliation
session token                   deterministic final risk
        |                               ^
        +--- typed local IPC ----------+
```

Gateway may validate identity, session, capability, schema and request bounds. It cannot link a broker adapter or send a venue mutation. Execution revalidates authority before every mutation.

## 4. Trusted state flow

```text
venue event
-> adapter normalization
-> authoritative state generation
-> OMS/reconciliation projection
-> decision snapshot
-> intent/portfolio/risk evaluation
-> durable command
-> venue send
```

Free-form model text never enters the trusted state machine. Only typed, bounded fields cross the Agent boundary.

## 5. Mutation lifecycle

```text
validate identity/session/capability
-> obtain generation-consistent snapshot
-> normalize target and derive plan
-> deterministic risk
-> issue/bind preview permit
-> apply revalidation
-> append durable command
-> send venue mutation
-> project callback/fill/reject
-> reconcile uncertain outcomes
```

Same-command retries return the durable outcome. A changed payload under the same ID is rejected. A lost/uncertain response is resolved through command status and authoritative reconciliation, not a new order.

## 6. Failure policy

Risk increase fails closed on unknown identity, missing config, stale quote, incomplete account/position/order state, generation change, persistence failure, disconnected authority, unsupported venue or reconciliation break. Safe cancel/strict reduction/flatten remains independently available when provable.

## 7. Deployment boundary

- minimal install contains simulator runtime, Gateway, MCP adapter, CLI and active service templates;
- IB PAPER is an explicit optional build/install path;
- CTP, XT/QMT and LIVE are not activated by examples or config;
- mutually untrusted Agents use separate OS identities, sockets, tokens and capabilities;
- only broker-owning Execution UID has broker network reachability.

## 8. Scale path

Scale proceeds in this order:

1. deterministic single-Agent Simulator closure;
2. generation-consistent intent/permit closure;
3. IB PAPER parity and soak;
4. portfolio compiler and multi-strategy budgets;
5. multi-Agent lifecycle/evaluation;
6. separately reviewed LIVE architecture, if ever pursued.
