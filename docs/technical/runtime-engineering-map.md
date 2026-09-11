# Runtime engineering map

Status: CURRENT  
Applies to: canonical Agent-native runtime  
Primary architecture: [`../AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md`](../AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md)

This document is a developer navigation map, not an additional approval or authorization gate. It connects the subsystem contracts that already own runtime behavior so a change can be traced from caller input to durable state, external effect, recovery, tests, and operations without inventing another control plane.

## End-to-end data and authority flow

```text
Agent / MCP / CLI
  |  discovery + typed request + stable command_id
  v
Tool Gateway
  |  peer/session/capability/domain/schema/rate checks
  v
Execution Service                         <--- Session Supervisor fence/lease
  |  authoritative state + deterministic risk
  |  durable intent + durable send-attempt
  v
Venue adapter / deterministic simulator
  |  callback / event / refresh barrier
  v
Execution projection -> Authoritative State -> Reconciliation
         |                    |                    |
         +---- OMS journal ---+--------------------+
```

Authority is deliberately asymmetric. The Agent may express bounded intent. The Gateway authenticates and forwards. The Execution Service alone may create an external mutation after risk and durability checks. Venue callbacks provide observed Broker or simulator facts. The OMS journal proves local intent/send history. Reconciliation requires both durable local history and complete authoritative venue state.

## Mutation invariants

Risk-increasing mutation code must preserve all of these invariants:

1. one normalized mutation has one stable `command_id`;
2. intent and send-attempt state are durable before external Broker I/O;
3. a possibly sent command is never blindly resent after transport loss;
4. authoritative quote, position, active-order, account, kill-switch and venue state are revalidated at final admission;
5. callbacks are correlated by owner/session/domain plus venue identity and connection epoch;
6. unresolved send, callback conflict, reconnect or incomplete refresh blocks new risk;
7. cancel, reduce-only and authoritative flatten are explicit guarded exit paths rather than blanket bypasses;
8. terminalization drains callbacks, freezes recovery state and commits a durable terminal witness.

The detailed owners are [`../modules/execution-service.md`](../modules/execution-service.md), [`../modules/oms-journal.md`](../modules/oms-journal.md), [`../modules/risk-engine.md`](../modules/risk-engine.md), and [`reconciliation-engine.md`](reconciliation-engine.md).

## Concurrency and lock order

The maintained runtime follows these boundaries:

- command identity and durable command state are serialized by the Execution coordinator;
- final simulator reservation/activation evaluates risk while holding the venue mutex so concurrent admissions observe prior pending exposure;
- the simulator lock order is **coordinator then venue** for reservation/activation;
- event sinks run after the venue lock is released;
- Gateway/session shared state is synchronized locally and must not hold its locks across an unbounded external call;
- Broker callbacks may arrive concurrently or out of order and must enter monotonic/explicitly-incomplete projections;
- terminal recovery closes callback admission, drains in-flight callbacks, and only then freezes the terminal snapshot.

A change that introduces a second lock acquisition order or waits on external I/O while holding a shared runtime lock needs an explicit concurrency test; it must not be hidden behind a broader timeout.

## Persistence and migration map

| State | Authority | Durability / migration rule |
|---|---|---|
| mutation identity and send history | OMS journal | append-only critical records; replay before admission; schema migration must preserve uncertain sends |
| session generation/fence | Session Supervisor lease store | atomic durable generation; malformed/newer format fails closed |
| Broker/simulator snapshots | Authoritative State | re-established after restart; epoch/generation completeness required |
| release identity | package manifest + digest receipt | immutable evidence, not runtime authority |
| PAPER rollout / qualification | exact artifact + verifier receipts | external evidence bound to source/artifact/harness/profile/account/host/stage or scenarios |
| strategy research history | SHADOW records | read-only to execution authority until a separately reviewed promotion boundary exists |

Rollback is permitted only across compatible journal and lease schemas or through a tested migration. See [`service-lifecycle.md`](service-lifecycle.md).

## Protocol and schema evolution

The Agent/Gateway tool catalog, Execution protocol, event feed, OMS journal, session protocol and release evidence are independently versioned. A protocol change should follow this order:

1. update the owning wire/schema contract;
2. add old/new compatibility or explicit rejection tests;
3. update producer and consumer together where the change is not backward compatible;
4. preserve command identity and uncertain-outcome semantics across the transition;
5. update the owning module document in the same change.

Do not infer compatibility from C++ struct layout or JSON field coincidence. Unknown fields/versions on authority-bearing protocols fail closed unless the owning contract explicitly defines forward compatibility.

## Configuration ownership and precedence

Configuration is accepted only from its owning trust domain.

- source-controlled defaults/policies define supported bounds;
- release artifact metadata binds source/profile identity;
- root-owned deployment files and systemd credentials provide host-specific identities/secrets;
- Session Supervisor state grants bounded Agent session capabilities;
- Execution runtime owns effective venue profile and authoritative risk inputs;
- Agent/strategy request fields cannot override Broker account, contract identity, quote source, FX source, kill switch, credentials or execution-domain binding.

Files ending in `.example` are templates, never authority. Runtime code should expose the effective configuration digest and reason codes without logging secrets.

## Test ownership

Behavior-bearing evidence is intentionally assigned once:

- **Core Runtime CI** owns the canonical build/CTest, full Python discovery, install inventory, deterministic package/preflight, and installed simulator lifecycle rollback/re-promotion.
- **Periodic Runtime Resilience** owns the actual GCC/Clang ASan+UBSan executions. It runs daily and on pull requests that touch execution, risk, OMS, authoritative state/reconciliation, IB, simulator, test or build surfaces.
- **Documentation Control Plane** owns documentation depth, component/build ownership, capability truth and source-gap contracts without rerunning the behavior suite.
- **Qualification Source Audit** owns only the source-side PAPER artifact/rollout/certification trust boundary and is path-scoped to that surface.
- **IB PAPER progressive rollout / certification** is explicit owner-dispatched external Broker work and is never a routine merge gate.

The live repository ruleset `22597364` still names four historical contexts that no longer own behavior: `canonical-full-suite-core`, `canonical-full-suite-reliability (g++)`, `canonical-full-suite-reliability (clang++)`, and `exact-merge-candidate`. While that server-side rule remains active, those job names are retained only as **compatibility shims**. They intentionally do not checkout, install dependencies, build, package, run sanitizer binaries, rerun documentation, or assert Broker authority. Their only purpose is to stop obsolete repository-administration context names from deadlocking Merge Queue. They should be deleted when the live ruleset is updated. A green compatibility shim is not engineering evidence and never changes PAPER/LIVE authorization.

The current gap validator still searches the historical sanitizer command tokens in `canonical-full-suite.yml`; those tokens are retained there only as comments during this ruleset/source-policy transition. `tests/python/test_resilience_workflow.py` separately proves that the comments are not executable and that the real sanitizer commands live in `resilience-periodic.yml`. The comments should disappear together with the old validator expectation, not become a permanent control mechanism.

A new invariant belongs in the smallest behavior test that can falsify it. Repeating the same deterministic suite in another workflow is not independent evidence.

## PAPER deployment and certification ownership

PAPER deployment follows one artifact identity:

```text
single candidate build
  -> lightweight host preflight
  -> P1 canary
  -> P1 pilot
  -> P1 extended
  -> optional V5 heavy certification
```

Canary/pilot/extended increase only the number of independently terminal flat PAPER-V4 cycles; they do not increase the one-unit instantaneous P1 exposure envelope. Heavy V5 fault experiments execute only when `certify` is explicitly selected. See [`../modules/ib-paper.md`](../modules/ib-paper.md) and [`ib-paper-harness-contract.md`](ib-paper-harness-contract.md).

## Performance budget and measurement

Safety correctness is primary, but the runtime should make latency cost visible rather than accumulating unmeasured defensive layers. Benchmark at least:

- Gateway request p50/p95/p99/p99.9 and queue saturation;
- Execution admission and final-risk latency;
- OMS append/fsync latency;
- quote callback to authoritative snapshot latency;
- accepted intent to venue-send latency;
- venue callback to projection/event latency;
- reconnect/reconciliation time;
- simulator throughput and jitter under concurrent admission.

Performance changes must preserve the safety invariants above. Host-specific low-latency tuning remains measurement-driven and reversible rather than a universal release gate.

## Change-impact guide

| Change | Minimum evidence |
|---|---|
| tool schema / Gateway policy | registry + framing + permission + IPC tests |
| mutation lifecycle / journal | restart at each durable boundary + idempotency + uncertain recovery |
| risk calculation | table/hostile boundary tests + concurrent final-admission test |
| venue adapter | callback correlation + partial fill + reconnect + reconciliation fixtures |
| persistent schema | old fixture + migration + rollback compatibility test |
| release/preflight | package producer/consumer parity + installed simulator lifecycle smoke |
| PAPER profile / harness contract | source trust-boundary tests + progressive P1 rollout; V5 certification when resilience contract changes |
| strategy/research | deterministic replay + no-lookahead + cost/capacity evidence; no authority promotion |

The objective is to preserve behavior-bearing defenses while keeping source, CI and deployment evidence as small and direct as the invariant allows.
