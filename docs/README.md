# HeptaTrader documentation map

Status: current
Applies to: repository documentation
Verification: same-revision CI

“Current” means the described path, command and capability exist or is explicitly labelled as a target contract whose implementation state is tracked in the canonical plan. Historical process narratives are preserved under `docs/legacy/` or Git history, not presented as active operations.

## Product and architecture

| Document | Authority |
|---|---|
| [`PRODUCT-SCOPE.md`](PRODUCT-SCOPE.md) | product definition, maturity and naming rules |
| [`CAPABILITY-MATRIX.md`](CAPABILITY-MATRIX.md) | implemented/experimental/planned/unsupported truth |
| [`AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md`](AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md) | components, dependencies and trust boundaries |
| [`development/PLAN.md`](development/PLAN.md) | single gap registry, sequence and definition of done |

## Runtime contracts

| Document | Authority |
|---|---|
| [`development/AGENT-INTENT-CONTRACT.md`](development/AGENT-INTENT-CONTRACT.md) | ordinary Agent target-position path and operator separation |
| [`STATE-AND-SNAPSHOT.md`](STATE-AND-SNAPSHOT.md) | authoritative state, generation and atomic snapshot semantics |
| [`PORTFOLIO-AND-CAPITAL.md`](PORTFOLIO-AND-CAPITAL.md) | portfolio compiler, netting and budget target contract |
| [`RISK-MODEL.md`](RISK-MODEL.md) | deterministic risk rules and source requirements |
| [`OMS-EVENT-SCHEMA.md`](OMS-EVENT-SCHEMA.md) | durable command/event model |
| [`RECONCILE-RULES.md`](RECONCILE-RULES.md) | uncertain outcomes and authoritative reconciliation |
| [`CONFIGURATION.md`](CONFIGURATION.md) | supported profiles and configuration authority |
| [`SECURITY.md`](SECURITY.md) | threat model, identities and credentials |
| [`OBSERVABILITY.md`](OBSERVABILITY.md) | metrics, SLOs and alerts |

## Research and validation

| Document | Authority |
|---|---|
| [`RESEARCH-PROTOCOL.md`](RESEARCH-PROTOCOL.md) | RunManifest, EventLog and RunSummary contracts |
| [`../research/README.md`](../research/README.md) | current research entry points and boundary |
| [`STRATEGY-VALIDATION-PLAN.md`](STRATEGY-VALIDATION-PLAN.md) | executable leakage/cost/capacity/promotion gates |
| [`EURUSD-CONFIRMED-MOMENTUM-SHADOW-V2.md`](EURUSD-CONFIRMED-MOMENTUM-SHADOW-V2.md) | experimental SHADOW strategy |

## Development and operations

| Document | Authority |
|---|---|
| [`development/TEST-STRATEGY.md`](development/TEST-STRATEGY.md) | test layers and exact-head evidence |
| [`ITERATION.md`](ITERATION.md) | bounded local/PR loop |
| [`DEPLOYMENT.md`](DEPLOYMENT.md) | minimal install and service assembly |
| [`RUNBOOK-INCIDENT.md`](RUNBOOK-INCIDENT.md) | incident response |
| [`RUNBOOK-KILLSWITCH.md`](RUNBOOK-KILLSWITCH.md) | kill switch and safe exit |
| [`BROKER-NETWORK-ISOLATION.md`](BROKER-NETWORK-ISOLATION.md) | broker egress boundary |

## Non-current material

- [`proposals/`](proposals/) describes unimplemented future work and makes no capability claim.
- [`legacy/`](legacy/) contains deprecated documentation and is not an active dependency.

Every current or target-contract document must start with `Status`, `Applies to` and `Verification`. `Verification: same-revision CI` means repository checks and tests execute against the exact commit under review; mutable labels such as `moving-main` are forbidden.
