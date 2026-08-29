# HeptaTrader canonical development plan

Status: current
Applies to: active runtime, Agent contracts, research/replay and repository development loop
Last verified base: `4e62a27ba1d2ba1cdadd810ae6533af90352a2b1`

## 1. Product truth

HeptaTrader is a model-agnostic, local-first trading control and execution runtime for AI Agents. Codex, OpenClaw and other clients may inspect authoritative state and submit bounded intents. They are never the broker authority, portfolio truth, final risk authority or OMS owner.

The project optimizes for two outcomes:

1. safe and deterministic conversion of an Agent intent into an execution result;
2. fast, reproducible strategy research whose output can be replayed and independently evaluated.

The project does **not** optimize for release ceremony, round/evidence closure, campaign finalizers, self-merging automation or documentation volume.

## 2. Non-negotiable runtime invariants

1. Only Execution Service may perform venue mutations.
2. Agent, MCP bridge and Tool Gateway hold no broker credentials and link no broker adapter.
3. Every new mutation is durably journaled before venue send.
4. Retries reuse the exact command ID; changed payload under the same ID is rejected.
5. Session, decision lease, execution epoch and fencing generation are checked at the authority boundary.
6. Orders, positions, cash and account state come from authoritative venue/Execution projections.
7. Unknown identity, configuration, quote, persistence or reconciliation state fails closed.
8. Cancel, reduce-only and authoritative flatten remain available when safe exit is possible.
9. CTP and XT/QMT report `VENUE_NOT_IMPLEMENTED` until real transport and lifecycle tests exist.
10. LIVE is unsupported until a separately reviewed activation change explicitly enables it.

## 3. Architecture target

```text
Agent model / operator
        |
        | bounded forecast or target-position intent
        v
Agent adapter (MCP/native)
        |
        | typed schema + stable command id
        v
Tool Gateway
        |
        | identity/session/capability enforcement
        v
Execution Service
        |\
        | +-- decision snapshot / portfolio netting / deterministic risk
        | +-- OMS journal / idempotency / recovery / reconciliation
        v
Simulator or implemented broker adapter
```

The dependency direction is one-way:

```text
agent adapters -> intent contracts -> portfolio/risk -> execution -> venue
venue events -> OMS/reconciliation -> authoritative state -> read tools
```

No active target may depend on `legacy/`.

## 4. Gap registry

The table is the only canonical gap list. A gap closes only when its acceptance evidence is present at the same commit.

| ID | Gap | Priority | Acceptance evidence | State |
|---|---|---:|---|---|
| G-001 | Root/current documentation can contradict code | P0 | canonical docs index, link/path integrity test, current/experimental/deprecated labels | in progress |
| G-002 | Active root still exposes historical build switches or source surfaces | P0 | active CMake has no legacy build switch; inactive monolith assets live under `legacy/`; dependency check | in progress |
| G-003 | CTP/XT scaffolds can be mistaken for usable venues | P0 | fail-closed implementations, capability matrix and tests | foundation complete |
| G-004 | Risk core lacks complete reduce-only, freshness and portfolio-budget semantics | P0 | deterministic risk policy and table-driven/property tests | in progress |
| G-005 | Ordinary Agent API is raw-order-centric | P0 | decision snapshot plus target-position preview/apply tools; raw place authority reserved for operator profile | in progress |
| G-006 | State reads used by decisions are not explicitly generation-consistent | P0 | one decision snapshot binds execution epoch, fencing generation and collection/event watermarks | in progress |
| G-007 | Research pipeline is encoded as campaign/receipt ceremony | P1 | one research manifest, deterministic replay command and compact strategy contract | in progress |
| G-008 | Minimal runtime build and install are not continuously verified | P0 | bounded CI runs build, CTest, Python tests and install smoke; no write permission or self-merge | foundation complete |
| G-009 | Observability is based on obsolete scripts rather than execution semantics | P1 | runtime metric/SLO contract covering decision, journal, send, callback and reconcile paths | in progress |
| G-010 | Large modules and duplicate schemas slow development | P1 | generated/schema-single-source proposal plus bounded module ownership map; incremental refactors only | planned |
| G-011 | Strategy validation lacks leakage, walk-forward, cost and capacity gates | P1 | research validation contract and machine-readable manifest fields | in progress |
| G-012 | LIVE states appear more mature than actual capability | P0 | capability matrix, configuration and tool exposure all mark LIVE unsupported | in progress |

Allowed states are `planned`, `in progress`, `blocked` and `closed`. Avoid invented closure grades, round numbers or evidence bundles.

## 5. Workstreams

### W1 — Repository and documentation truth

Deliverables:

- one root README describing actual capability;
- one canonical docs index;
- this plan and a compact architecture/contract/test set;
- stale command/path checker;
- historical and proposal documents separated from current contracts.

Exit criteria:

- every current document references existing paths;
- no current document mentions deleted release gates, PowerShell scripts, campaign finalizers or nonexistent runbooks;
- capability claims match build targets and adapters.

### W2 — Deterministic risk and portfolio boundary

Deliverables:

- finite-value validation;
- per-order quantity/notional and price-band controls;
- rolling order-rate and active-order limits;
- quote freshness and state-generation checks;
- gross/net/strategy budget inputs;
- strict reduce-only that cannot cross through zero;
- deterministic reason codes and explainable decision output.

Exit criteria:

- Simulator and IB PAPER use the same policy object;
- every reject branch has a test;
- safe reduction remains available above a limit, but cannot flip exposure.

### W3 — Agent intent contract

Ordinary Agent sessions receive:

- `decision.get_snapshot`;
- `intent.preview_target_position`;
- `intent.apply_target_position`;
- read, event, cancel and authoritative flatten tools allowed by capability.

Raw order placement is reserved for an explicitly separate operator capability and is never exposed by ordinary Agent deployment examples.

Exit criteria:

- target delta, side, quantity and order shape are derived in trusted code from an authoritative snapshot;
- preview permit binds target, snapshot generations, risk-policy version and expiry;
- apply consumes the permit or replays the same durable command;
- stale or changed generations fail closed.

### W4 — Authoritative decision snapshot

A decision snapshot is one bounded JSON object containing:

- account summary and positions;
- active/recent orders;
- normalized quote for the requested instrument;
- risk limits;
- health/recovery state;
- execution service epoch and fencing generation;
- collection, event and snapshot watermarks;
- observed/completed timestamps and freshness status.

Exit criteria:

- all components are collected from one Execution authority;
- generation or epoch changes during collection reject the snapshot;
- Agent cannot provide or widen any authoritative field.

### W5 — Research and replay

Deliverables:

- `research/manifest-v1.json` declaring datasets, strategy implementation, parameters, costs and unsupported promotion modes;
- deterministic replay output with input digest and code revision;
- leakage, walk-forward, cost, capacity and regime checks;
- SHADOW output separated from PAPER/LIVE authorization.

Exit criteria:

- research can run without root custody/campaign/finalizer machinery;
- the same manifest reproduces the same decision stream within declared numeric tolerance;
- no research artifact grants runtime capability.

### W6 — Bounded development loop

Required PR checks:

1. syntax/config validation;
2. Release core build with IB disabled;
3. core CTest;
4. Python contract tests;
5. minimal runtime install smoke;
6. documentation/path and active-to-legacy dependency checks.

Nightly or optional checks may add sanitizers, fuzzing, long replay and fault injection. They must not be disguised as ordinary source-development prerequisites.

CI has read-only repository permission. It never approves or merges its own PR.

## 6. Performance budgets

Correctness comes first, but every hot path must record a monotonic duration for:

- market event -> authoritative quote projection;
- intent receipt -> decision snapshot complete;
- snapshot -> deterministic risk decision;
- accepted command -> journal durable;
- journal durable -> venue send;
- venue callback -> OMS projection;
- reconnect -> authoritative reconciliation complete.

Initial regression budgets are expressed as relative baselines, not unsupported universal latency claims. A change fails when p99 regresses by more than 20% on the same fixture without an accepted explanation.

## 7. Definition of done

A work item is done only when all applicable statements are true:

- implementation and documentation agree;
- negative and failure-path tests exist;
- no unsupported venue or LIVE capability is advertised;
- no Agent-controlled field is treated as authoritative market/account state;
- idempotency and crash/retry behavior are defined;
- observability exposes a stable reason code;
- the exact branch head passes bounded CI;
- no temporary self-removing or self-merging workflow remains.

## 8. Change discipline

Prefer small deterministic contracts over orchestration frameworks. Add a state, receipt or process only when it prevents a concrete trading failure that cannot be prevented more simply. Every new component must name its authority, inputs, durable state, failure mode, timeout and owner.

Documentation is an executable contract, not a certification narrative. When code changes, update the nearest canonical document and tests in the same commit.
