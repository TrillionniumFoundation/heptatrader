# HeptaTrader canonical development plan

Status: current
Authority: this is the single canonical gap registry and implementation sequence
Applies to: active runtime, Agent contracts, portfolio/risk, research/replay, deployment and CI
Verification: `canonical-full-suite` on the exact revision; baseline audited at `e3cb6afe018024af543031c2d6c83322a1237300`

## 1. Product truth

HeptaTrader is a **model-agnostic deterministic trading control and execution runtime for AI agents**, plus a reproducible quantitative-research plane. Codex is the first supported Agent client, not the broker authority or a required runtime dependency.

The product has four planes:

1. **Research plane** — point-in-time data, deterministic features, experiments, costs, replay and validation.
2. **Agent plane** — bounded forecasts and target-position intents through MCP/native clients.
3. **Portfolio and risk plane** — netting, capital budgets, authoritative snapshots and deterministic policy.
4. **Execution plane** — OMS, journal-before-send, idempotency, reconciliation, venue adapters and safe exits.

Current implementation is strongest in the execution plane. The work below closes the remaining gaps without advertising unsupported LIVE, CTP or XT/QMT capability.

## 2. Explicit non-goals

The repository does not optimize for:

- release rounds, closure grades or evidence-bundle ceremony;
- campaign opener/renewer/finalizer workflows;
- self-approving or self-merging automation;
- Agent possession of broker credentials or raw venue state;
- automatic SHADOW-to-PAPER/LIVE promotion;
- universal latency claims without same-fixture measurements.

A new process, receipt or durable state is justified only when it prevents a named trading failure that cannot be prevented more simply.

## 3. Non-negotiable runtime invariants

1. Only Execution Service may perform venue mutations.
2. Agent, MCP bridge and Tool Gateway hold no broker credentials and link no broker adapter.
3. Every new mutation is durably journaled before venue send.
4. A retry reuses the exact command ID and normalized payload; changed payload is an idempotency conflict.
5. Session, decision lease, execution epoch and fencing generation are checked at authority boundaries.
6. Orders, positions, cash, PnL, risk usage and account state come from authoritative Execution/venue projections.
7. Unknown identity, configuration, quote, persistence, generation or reconciliation state fails closed.
8. Cancel, strict reduce-only and authoritative flatten remain available whenever a safe exit can be proved.
9. Unsupported adapters return a typed unsupported result and never synthetic connection, ACK, order or fill success.
10. LIVE configuration, capability and mutation remain absent until a separate reviewed activation change.

## 4. Target architecture

```text
Point-in-time data -> research/replay -> forecast/target intent
                                           |
Codex / Agent / operator ------------------+
        |
        | MCP/native typed contract
        v
Tool Gateway: identity, session, capability, schema and bounds
        |
        v
Execution Service
        |-- authoritative state + generation-consistent decision snapshot
        |-- portfolio netting + capital/risk budget
        |-- deterministic risk + preview permit authority
        |-- OMS journal + idempotency + uncertain recovery
        |-- metrics + reason codes + reconciliation
        v
Simulator or explicitly supported PAPER adapter
```

Dependency direction is one-way:

```text
agent adapters -> intent -> portfolio/risk -> execution -> venue
venue events -> OMS/reconcile -> authoritative state -> reads/snapshots
research artifacts -> reviewed intent inputs only; never runtime capability
```

No active target may depend on `legacy/`.

## 5. Canonical gap registry

Allowed states are `planned`, `in progress`, `blocked` and `closed`. A gap closes only when implementation, negative tests, documentation and same-head CI evidence agree.

| ID | Gap | P | Required closure evidence | State |
|---|---|---:|---|---|
| G-001 | Repository and current documentation can contradict code | P0 | canonical index; automated metadata/link checks for current Markdown; commands and exact capability claims checked | in progress |
| G-002 | Historical monolith surfaces remain discoverable as active product paths | P0 | inactive sources under `legacy/`; active dependency check; no legacy build switch | in progress |
| G-003 | CTP/XT scaffolds can be mistaken for usable venues | P0 | typed fail-closed adapters and negative tests; proposals separated from current docs | in progress |
| G-004 | LIVE/profile truth is inconsistent across config, tools and examples | P0 | only `sim` and `paper` accepted; no account-string mode inference; no LIVE tool environment; clean examples | in progress |
| G-005 | Risk rules are ahead of their authoritative data sources | P0 | each enabled rule maps to a snapshot field/source/generation; missing fields fail closed; Simulator and IB PAPER use common policy | in progress |
| G-006 | Ordinary Agent API remains raw-order-centric | P0 | ordinary profile exposes snapshot and target-position preview/apply; raw place is operator-only and absent from Agent examples | in progress |
| G-007 | Decision state is assembled from loosely parsed JSON instead of one typed generation | P0 | typed snapshot; epoch/fence/generation/watermark consistency; stale/incomplete negative tests | in progress |
| G-008 | Preview permits lack a complete authoritative lifecycle | P0 | server-issued opaque permit; expiry/generation binding; atomic consume; same-command replay; cross-command rejection | planned |
| G-009 | Research runtime still contains campaign/custodian/finalizer ceremony | P0 | canonical `RunManifest`, append-only `EventLog` and `RunSummary`; no campaign/lease/finalizer dependency in current path; installed runner fails closed when source assets are absent | in progress |
| G-010 | Strategy validation is mostly narrative rather than executable | P1 | deterministic replay command; data digests; purged walk-forward; explicit costs/capacity/regime output; parity tests; static-manifest verification names its source checkout | in progress |
| G-011 | No portfolio compiler, cross-strategy netting or capital budget authority | P1 | typed strategy intents -> netted portfolio target; deterministic budget decisions and tests | planned |
| G-012 | Observability contract is not fully implemented in runtime | P1 | bounded counters/gauges/latencies at risk, journal, send, callback and reconcile transitions; no sensitive labels | planned |
| G-013 | Tool, protocol and result schemas are duplicated in C++ and Python | P1 | one canonical schema catalog; validated bindings and digest/drift test | planned |
| G-014 | Large modules and script-shaped libraries slow safe iteration | P1 | ownership map; thin CLI entry points; targeted extraction without changing authority | planned |
| G-015 | CI lacks complete fault, replay and optional reliability lanes | P1 | fast PR lane plus permanent `canonical-full-suite` sanitizers/fuzz/crash/replay/performance jobs and optional scheduled diagnostics; read-only permissions | in progress |
| G-016 | Install tree can expose stale or unsupported deployment surfaces | P0 | minimal simulator/Agent install; PAPER only when explicitly built; no stale XML/CTP/LIVE examples; install smoke | in progress |

### Current implementation checkpoint

The following checked-in surfaces provide the implementation and negative-test
evidence currently available on this branch. They do not, by themselves, close
a gap: each row still requires a successful `canonical-full-suite` result on
the exact head before its state may become `closed`.

| Gap family | Implementation surface | Deterministic check |
|---|---|---|
| Repository, configuration and install truth (G-001/G-002/G-004/G-016) | `scripts/check_repository_integrity.py`, `scripts/resolve_hepta_config.py`, `cmake/RuntimeInstall.cmake` | repository/config Python tests and install-tree smoke |
| Venue and authority boundaries (G-003/G-005/G-006/G-007) | typed CTP/XT rejection, Execution-owned snapshots, Tool Registry/Gateway and risk policy | core C++ contract and negative tests |
| Preview/apply lifecycle (G-008) | Execution-issued opaque permit, generation/fence binding and journal-before-send path | `hepta_execution_preview_permit_tests`, target-position/tool-host tests |
| Research/replay (G-009/G-010) | `research/run_protocol.py`, manifest and schema catalog | research protocol unit tests, deterministic `verify` fixture and fail-closed installed-runner/source-root check |
| Portfolio, telemetry, schemas and module discipline (G-011–G-014) | `HeptaTrade/portfolio/`, `HeptaTrade/observability/`, `schemas/`, ownership map | portfolio/telemetry C++ tests plus schema/module checkers |
| Reliability and performance (G-015) | `scripts/reliability_core.sh`, crash/replay, malformed-protocol and latency fixtures | permanent `canonical-full-suite` reliability matrix |

## 6. Workstreams and acceptance contracts

### W1 — Repository and documentation truth

Deliverables:

- root README and capability matrix describe the same product truth;
- every current document has `Status`, `Applies to` and `Verification` metadata;
- proposals live under `docs/proposals/`; historical material lives under `docs/legacy/` or `legacy/`;
- repository integrity checks all current documents, local links, commands and forbidden capability tokens.

Exit command:

```bash
python3 scripts/check_repository_integrity.py
```

### W2 — Configuration and deployment truth

Deliverables:

- runtime profiles: `sim`, `paper`; LIVE is rejected as unsupported;
- no profile is inferred from account text;
- simulator and PAPER examples are separate and minimal;
- credentials are injected only by deployment authority;
- unsupported venue examples are not installed.

### W3 — Authoritative state, portfolio and deterministic risk

Each enabled risk dimension must document and implement:

```text
field -> authority -> generation -> freshness -> missing behavior -> reason code
```

Required dimensions are order quantity/notional, rolling rate, active orders, gross/net position, strategy budget, daily loss, drawdown, quote freshness, snapshot completeness and strict no-cross-zero reduction.

### W4 — Agent intent and permit authority

Ordinary Agent tools:

```text
decision.get_snapshot
intent.preview_target_position
intent.apply_target_position
account/portfolio/orders/risk reads
events.wait
trade.cancel_order
risk.preview_flatten / trade.flatten_position when supported
```

Raw order placement belongs to a separately provisioned operator profile. Preview/apply must use server-derived position, price, quantity, risk and generation fields.

### W5 — Research and strategy validation

The current research path is capability-free and uses only:

```text
RunManifest -> deterministic EventLog -> RunSummary
```

A run records source revision, strategy/config/data digests, calendar/session semantics, fold boundaries, costs, capacity assumptions, decisions, metrics, failures and output digest. No research artifact contains a runtime token, preview permit or promotion grant.

The runtime install carries the capability-free runner and static contract but
not experimental strategy source assets.  Installed `self-test`/full replay
remain available; static-manifest verification must receive an explicit source
checkout via `verify --root` and fails closed when required assets are missing.

### W6 — Runtime observability and reliability

Required transition timing:

- market event -> authoritative projection;
- intent receipt -> decision snapshot;
- snapshot -> risk decision;
- accepted command -> journal durable;
- journal durable -> venue send;
- venue callback -> OMS projection;
- reconnect -> reconciliation complete.

Same-fixture p99 regression budget is initially 20%; correctness and fail-closed behavior take precedence.

### W7 — Schema and module discipline

- canonical wire/schema definitions have one source of truth;
- C++/Python/MCP bindings are generated or mechanically validated;
- command-line scripts remain thin;
- large-file extraction is incremental and covered by parity tests.

## 7. Implementation sequence

1. Close W1/W2 truth gaps before adding capability.
2. Close state/risk/intent/permit gaps on Simulator with full negative tests.
3. Apply the same contracts to IB PAPER without weakening them.
4. Replace current research campaign machinery with the compact run protocol.
5. Add portfolio netting/capital budgets and executable validation.
6. Instrument observability, fault lanes and schema drift checks.
7. Mark the PR ready only after the exact head passes the bounded core workflow and temporary diagnostics are removed.

## 8. Definition of done

A gap is `closed` only when all applicable statements are true:

- implementation and current documentation agree at the same revision;
- negative and failure-path tests exist;
- unsupported venue/LIVE capability is neither accepted nor advertised;
- Agent-controlled fields are never treated as authoritative market/account/risk state;
- idempotency, crash/retry, expiry and reconciliation behavior are defined;
- stable machine reason codes and bounded observability exist;
- no active dependency points to `legacy/`;
- exact-head PR CI passes with read-only repository permission;
- no temporary source snapshot, finalizer, self-approval or self-merge workflow remains.

## 9. Change discipline

Prefer a small deterministic state machine over orchestration. Every new component must name its authority, inputs, durable state, failure mode, timeout and owner. Every removed component must have no remaining runtime, test, documentation or install dependency.
