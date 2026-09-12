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

Core Runtime CI owns canonical CTest, full Python discovery, install/package/preflight
and simulator lifecycle smoke. Required GCC/Clang contexts execute the common
`run_runtime_resilience.sh` ASan/UBSan suite for any non-documentation change.
Unknown paths are conservative code changes; Gateway/Session/client changes are
not missed. Nightly runs use the same script and do not duplicate PR triggers.
Source-model checks parse actual workflow jobs and run steps, not comments or
command names embedded in prose. Native risk/OMS/venue/release source token gates
have been removed; their behavioral owners remain CTest and Python regression.

Server-side ruleset 22597364 still requires the redundant
`canonical-full-suite-core` and `exact-merge-candidate` context names. Only those
names remain temporary compatibility emitters; they are not additional evidence.
Delete them together with the server-side required-context inventory, not before.
The required sanitizer names retain actual test execution and failure propagation.

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
