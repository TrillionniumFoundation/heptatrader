# Portfolio compiler and capital budget contract

Status: current deterministic Simulator implementation; venue integration remains separately reviewed
Applies to: `HeptaTrade/portfolio/`, `HeptaTrade/risk/`, Agent target-position path
Verification: `canonical-full-suite` on the exact revision; external PAPER host checks are separate

## Purpose

Agents and strategies express desired portfolio state. They do not independently create broker orders. A trusted portfolio compiler nets compatible intents, applies deterministic capital/risk budgets and emits one bounded execution plan.

## Inputs

- owner/session/strategy identity;
- forecast or target-position intent;
- authoritative current positions and active orders;
- strategy and portfolio budgets;
- normalized quote/liquidity state;
- execution epoch, fencing and state generation;
- expiry and urgency bounds.

## Processing

```text
validate intent
-> normalize target
-> aggregate by instrument/horizon
-> cross-strategy netting
-> apply strategy capital budget
-> apply portfolio gross/net/concentration budgets
-> derive execution delta
-> deterministic risk
-> execution plan or typed rejection
```

## Invariants

- opposite intents are netted before venue order creation;
- no strategy can widen its assigned budget;
- reduction remains available when new risk is blocked;
- crossing through zero is not treated as reduce-only;
- unknown PnL, margin, FX conversion, liquidity or generation fails closed for risk increase;
- every budget decision has a stable reason code and authoritative source.

## Initial scope

The checked-in implementation is deterministic single-account, multi-strategy
netting for Simulator. `PortfolioCompiler::Compile` requires a complete,
generation-tagged authoritative snapshot, sorts inputs canonically, checks
per-strategy and portfolio gross budgets with checked fixed-point arithmetic,
and emits stable rejection reason codes plus non-zero target deltas. It never
creates a venue order or grants a runtime capability. Dynamic optimization,
leverage allocation, learned sizing and multi-account allocation remain out of
scope until separately reviewed.

## Runtime authority boundary

`PortfolioCompiler::Compile` is a pure, typed policy service. Its caller must
be trusted Simulator orchestration that owns the complete strategy-intent
vector, authoritative position snapshot and capital policy. The compiler has
no session, credential, permit, socket or venue dependency; its result is a
bounded policy decision, not a broker command. A caller must pass the returned
target/delta through the existing Execution risk, journal and permit path
before any venue mutation.

The ordinary Agent target-position path is intentionally narrower: one Agent
target is compiled against one Execution-owned decision snapshot and then
revalidated by Execution's risk preview/apply authority. That path does not
aggregate intents from other strategies and does not infer a portfolio budget
from Agent-supplied fields, so it must not be described as a multi-strategy
allocator. Until a separately reviewed orchestration supplies the compiler's
full intent vector and policy at runtime, the production/PAPER capability
matrix keeps multi-Agent portfolio allocation planned; compiler unit tests do
not by themselves grant that capability.

The contract fixture is `tests/portfolio_compiler_tests.cpp`; it covers
cross-strategy netting, deterministic order independence, missing snapshots,
generation mismatch, duplicate intents, budget limits and arithmetic overflow.
