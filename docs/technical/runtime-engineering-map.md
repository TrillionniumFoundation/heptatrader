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
- risk-increasing place/flatten admission remains serialized, but after durable send-attempt persistence the coordinator releases its mutex while the venue callback executes; another new risk mutation fails closed until that dispatch returns;
- cancellation also releases the coordinator mutex around the venue callback, so status/reconciliation/control reads are not serialized behind a slow provider; terminal evidence is refused while any venue dispatch is still in flight;
- final simulator reservation/activation evaluates risk while holding the venue mutex so concurrent admissions observe prior pending exposure;
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
| PAPER qualification | exact artifact + verification receipt | external evidence bound to source/artifact/harness/profile/account/host/scenarios |
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

Routine pull request validation uses the current `core-ci.yml` workflow. Native,
Python, and learning validation provide separate feedback. Within Python
validation, documentation, core behavior, and source structure checks are reported
independently so an earlier failed check does not hide the remaining results.

`scripts/ci_scope.py` selects the runtime lanes. Prose-only changes retain
documentation feedback. Verified Python operators run the Python checks; source
monitoring, the shared change watcher, PnL watchers, and price baselines also retain
learning integration checks. Changes to an unknown or other shared component,
CI logic, or a deleted, missing, or retyped required file restore full validation.
The verified loader and attribution installer additionally check their known
imports and required support files. Changing a support file does not inherit a
helper's scope exemption.

`scripts/run_python_tests.py` owns the disjoint Python test partitions. Behavior
tests, including source-proof cache behavior, remain in the core partition;
source structure checks belong to the source partition. A test should have one
runner owner. Components retain the integration checks needed by their consumers.

Release-tag package and install checks and manual sanitizer, SDK, and broker
acceptance workflows serve their stated release or operator acceptance purposes.
They are not additional prerequisites for every routine helper edit. The retained
Windows Desktop IB workflow profiles have the legacy scope described in
[Deployment](../modules/deployment.md); current Pocket4 operating instructions are
owned by that module.

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
| PAPER profile / harness contract | scenario contract + exact-artifact qualification campaign |
| strategy/research | deterministic replay + no-lookahead + cost/capacity evidence; no authority promotion |

The objective is to preserve behavior-bearing defenses while keeping source, CI and deployment evidence as small and direct as the invariant allows.

Historical HeptaDLL inventory consistency uses the archived Python partition
when its metadata or validators change, or on manual dispatch. It is not repeated
in routine source/native/sanitizer feedback and no longer requires
particular English explanation phrases. Canonical old-record decoding, retired
interface rejection and unprivileged-client boundary tests remain native behavior.

## 0DTE options development

The optional native options observer, risk/economic evidence core, MKT proposal
codec and acceptance boundaries are documented in [Native 0DTE](native-zero-dte.md).
This is shadow delivery only; it does not enable OPT in the current PAPER profile.

### Options source correction: Snowball / IB authority split

See [native options source and acceptance contract](native-zero-dte.md).
SnowballOptionFeed is the native normalized input/cache layer, not an implemented
Snowball network transport. Default shadow selection is SNOWBALL and blocks
before broker I/O until an official authorized quote transport exists.
`hepta-options-observer --qualify-occ` is a separate actual IB contract-only
route with no market-data subscription. Feed and Broker epochs are independent;
H0D2 records their separate normalized provenance. No source migration enables
OPT, bypasses native Execution, or changes FX/BTC OMS state.

### Options read-only audit and normalized trace

The native OptionsAudit component contains `hepta-options-oms-audit` and
`hepta-snowball-trace-replay`. Both produce diagnostics only; neither calls a
Broker mutation or supplies an Execution permit. The first joins copied native
OMS individual executions and unbound fees through original command identities;
the second records/replays all normalized feed context, clocks, transitions and
decisions. See native-zero-dte.md for completeness bounds, tests, unresolved
Snowball access and the independent cash mismatch that keeps drivers paused.
