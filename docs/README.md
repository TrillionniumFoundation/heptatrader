# HeptaTrader documentation map

Status: current
Applies to: repository documentation
Last verified commit: moving-main

Documentation is an executable engineering contract. “Current” means the described path, command and capability exist in the same revision. Historical process narratives are preserved by Git history rather than kept as misleading active files.

## Canonical current contracts

| Document | Authority |
|---|---|
| [`development/PLAN.md`](development/PLAN.md) | single gap registry, priorities, acceptance evidence and definition of done |
| [`AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md`](AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md) | runtime components, dependency direction and authority boundary |
| [`CAPABILITY-MATRIX.md`](CAPABILITY-MATRIX.md) | implemented/experimental/unsupported capability truth |
| [`development/AGENT-INTENT-CONTRACT.md`](development/AGENT-INTENT-CONTRACT.md) | ordinary Agent snapshot and target-position interface |
| [`RISK-MODEL.md`](RISK-MODEL.md) | venue-neutral deterministic risk semantics |
| [`OMS-EVENT-SCHEMA.md`](OMS-EVENT-SCHEMA.md) | durable command/event semantics |
| [`RECONCILE-RULES.md`](RECONCILE-RULES.md) | authoritative recovery and uncertain-command handling |
| [`CONFIGURATION.md`](CONFIGURATION.md) | config source precedence, profile lock and secret handling |
| [`DEPLOYMENT.md`](DEPLOYMENT.md) | minimal runtime installation and fixed service assembly |
| [`SECURITY.md`](SECURITY.md) | threat model, identities, credentials and failure policy |
| [`OBSERVABILITY.md`](OBSERVABILITY.md) | runtime metrics, SLOs and operational alerts |
| [`ITERATION.md`](ITERATION.md) | bounded local/PR development loop |
| [`development/TEST-STRATEGY.md`](development/TEST-STRATEGY.md) | test layers, fault cases and exact-head evidence |
| [`RUNBOOK-INCIDENT.md`](RUNBOOK-INCIDENT.md) | incident classification and safe response |
| [`RUNBOOK-KILLSWITCH.md`](RUNBOOK-KILLSWITCH.md) | kill switch, flatten and terminal control |
| [`BROKER-NETWORK-ISOLATION.md`](BROKER-NETWORK-ISOLATION.md) | broker network egress boundary |

## Research

| Document | State |
|---|---|
| [`../research/README.md`](../research/README.md) | current research/replay contract |
| [`STRATEGY-VALIDATION-PLAN.md`](STRATEGY-VALIDATION-PLAN.md) | current validation gates for any strategy candidate |
| [`EURUSD-CONFIRMED-MOMENTUM-SHADOW-V2.md`](EURUSD-CONFIRMED-MOMENTUM-SHADOW-V2.md) | experimental EURUSD strategy contract; SHADOW only |

## Proposals

QMT/XT and other unimplemented venue plans belong under `docs/proposals/`. A proposal may describe an intended API, but must not appear in runtime capability lists until transport, lifecycle, reconciliation and negative-path tests exist.

## Required document header

Every current, experimental or proposal document starts with:

```text
Status: current | experimental | proposal | deprecated
Applies to: <paths/components>
Last verified commit: <sha or moving-main>
```

## Prohibited current-document content

Current documents must not refer to deleted PowerShell gates, round/P1 campaigns, evidence closure, renew/repair/finalizer orchestration, attestation bundles, self-merging workflows, hard-coded personal workspaces or nonexistent runbooks.
