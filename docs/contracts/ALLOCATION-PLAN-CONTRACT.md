# AllocationPlan V1

Status: current normative
Applies to: Global Decision output and Execution shadow intake
Verification: plan integrity, expiry, snapshot binding and Execution revalidation CTests
Authority: global-decision output contract

`AllocationPlan` 是 immutable、bounded、可重放的目标计划，不是 broker command，也不授予 mutation。Plan 绑定 allocator epoch、proposal-set digest、snapshot digest、SolverResult、fixed-point targets、created/valid-until 和 plan digest。

Execution 依次验证 plan/solver digest、exact/heuristic 状态与 bound/gap 一致性、时间窗口、authoritative snapshot digest、target 排序/范围，再把 targets 交给现有 `PortfolioCompiler` 重算strategy/global capital budget 和 authoritative generation delta。失败保持 typed reject；当前集成为shadow revalidation，尚不直接发送 broker mutation。机器 schema 为 `schemas/allocation-plan-v1.json`。

## Sealed provenance and Execution context binding

An `AllocationPlan` binds allocator epoch, capital pool, account book, policy revision, ProposalSet digest, authoritative snapshot digest, ProposalSet capture/expiry and snapshot expiry. These fields, solver evidence and ordered targets are covered by the plan digest. Plan validity is derived from the ProposalSet/snapshot intersection and cannot be extended by a caller.

Execution accepts only a `GlobalDecisionReceipt` issued by Global Decision plus an independently supplied authoritative execution context. Default, forged or client-reconstructed receipts are rejected. Execution rechecks receipt integrity, solver bounds and gap, allocator epoch, pool, book, policy revision, ProposalSet identity, snapshot identity and lifetime, then recompiles targets against authoritative portfolio state and current execution budgets. Any mismatch yields no venue mutation.

## Portfolio compiler admission and complete-target semantics

Portfolio module **1.1.1** hardens `PortfolioCompiler::Compile` in
`HeptaTrade/portfolio/portfolio_compiler.*`. The native `portfolio-compiler-v1`
identifier and public struct layout remain unchanged. This is bounded, pure
netting/budget/delta compilation, not allocation acceptance, a broker command or
an issuer of authoritative snapshots. In particular, a caller-supplied
`AuthoritativePortfolioInput.complete` boolean and generation are checked fields,
not independent proof of source authority. Execution must supply that authority
and perform its own receipt, lifetime and risk checks.

### Complete preflight before normalization

The implementation first checks snapshot completeness/generation, policy scalars
and every container size. It then validates **all** registered strategy budgets,
including unused entries, all current positions, and every intent's identity,
generation, representable magnitude and registered budget. Malformed unused
policy entries are rejected even when there are no intents. Each budget-map key
must equal its value's strategyId, both must be canonical 1..64-byte IDs and the
budget must be positive. Instrument IDs remain canonical 1..128-byte IDs. The
ASCII alphabet is letters/digits/dot/dash/underscore/colon, independent of locale.

No normalization index or body-sized copy is allocated until this preflight
succeeds. Canonical ordering uses a bounded vector of pointers to stable input
records instead of copying all strings. Callers must not mutate referenced inputs
concurrently. Adjacent equal strategy/instrument pairs after sorting are rejected;
zero targets are still real records and do not avoid duplicate or count checks.
Typed failures contain only a bounded reason string and no partial target map,
gross totals or deltas. The reason string itself can allocate; the API does not
promise allocation-free reporting under sustained memory exhaustion. Other
allocation exceptions propagate with caller inputs untouched.

| Hard per-call ceiling | Value / meaning |
|---|---|
| `kMaximumIntents` | 16,384 physical intents, including duplicates and zero targets. |
| `kMaximumStrategyBudgets` | 256 registered policy entries and at most 256 participating strategies. |
| `kMaximumTargetInstruments` | 4,096 distinct target instruments; policy.maximumInstruments may narrow this. |
| `kMaximumSnapshotPositions` | 4,096 supplied current-position records, including zero positions. |
| `kMaximumDeltaInstruments` | 8,192 possible entries in the union of targets and current positions. |

A policy may register more strategies than may participate in one compilation,
but cannot exceed the hard registered-entry limit. The policy's target-instrument
limit is **not** repurposed to truncate held positions. At the hard bounds a
4,096-instrument target plus 4,096 different holdings can produce 8,192 deltas;
all such reductions are retained. No configuration can enlarge these ceilings,
and the compiler never truncates input or splits one global plan into independently
budgeted pieces. Inputs beyond the supported envelope require explicit upstream
policy/review, not a retry that silently discards positions.

New capacity failures are `PORTFOLIO_INTENT_CAPACITY_EXCEEDED`,
`PORTFOLIO_POLICY_CAPACITY_EXCEEDED` and `PORTFOLIO_SNAPSHOT_CAPACITY_EXCEEDED`.
Malformed declared entries are `PORTFOLIO_STRATEGY_BUDGET_INVALID`; an otherwise
valid intent referring to an absent entry retains
`PORTFOLIO_STRATEGY_BUDGET_MISSING`. Invalid scalar ceilings are
`PORTFOLIO_POLICY_INVALID`. When several independent fields are malformed,
preflight may report an earlier rejection than the former sort-first path;
callers must not rely on the old precedence of multiple invalid fields.

### Existing financial and delta meaning is not reinterpreted

For nonempty input, strategy gross is the sum of absolute per-strategy targets;
per-instrument net targets sum contributions across strategies. Portfolio gross
is the sum of absolute **net** targets, not cash, notional, margin or marked equity.
No price/lot normalization, currency conversion or fee estimation occurs here.
The legacy helper continues checked signed-int64 arithmetic; INT64_MIN magnitudes
and any unrepresentable accumulation/subtraction fail. It is not the optimizer:
an intermediate int64 overflow is rejected rather than certified as an optimal
or feasible final cancellation. Narrower domain numeric limits remain the
responsibility of the validated upstream contracts.

An empty input is a validated no-op (`PORTFOLIO_NO_INTENTS`) and emits no deltas;
it is **not** a flatten command. A nonempty input retains the existing complete
replacement-target behavior: a held instrument absent from the target map has
target zero, and its reduction/close delta is included. Callers sending a partial
update must not treat that as a complete target plan. Missing current positions
are treated as zero only within the caller's already complete trusted snapshot.
Zero deltas are omitted, but zero net targets remain in the returned map.

Hardening these checks rejects previously accepted malformed/unbounded inputs;
existing within-envelope valid fixtures keep their outputs. It does not activate
a new venue, increase order authority, modify the simulator cash ledger or change
PAPER/LIVE qualifications. Count/work bounds are not a process-wide RSS, allocator
or latency guarantee; all policy/position/intent maps still belong to the caller.

### Direct requirement-to-assertion coverage

`tests/portfolio_admission_tests.cpp`, invoked by
`tests/python/test_portfolio_admission.py`, contains seven regression functions:

| Invariant | Regression |
|---|---|
| Unused budgets validate on empty and active paths; registered vs participating sets stay distinct | `TestCompletePolicyIncludingUnusedEntries` |
| All cardinalities and every body validate before normalization allocations, including a malformed suffix | `TestCapacityAndFieldsPrecedeNormalization` |
| Inclusive 16,384/256/4,096 ceilings and all 8,192 delta-union entries; no silent held-position truncation | `TestInclusiveHardBoundsAndCompleteDeltaUnion` |
| Identifier boundaries, NUL/non-ASCII rejection and initial admission order | `TestIdEndpointsAndAdmissionOrder` |
| Checked integer behavior, duplicate suffixes and no partial output | `TestPrefixFailuresAndIntegerSemanticsRemainClosed` |
| Separate small-integer netting/gross/delta model and shuffled-input invariance across 500 seeded fixtures | `TestIndependentSmallValueModelAndPermutation` |
| Every observed allocation-failure ordinal, unchanged inputs and sixteen concurrent pure compilations | `TestAllocationFailureAtomicityAndPureParallelCalls` |

The existing four functions in `tests/portfolio_compiler_tests.cpp` remain
unchanged. Assertions are always active under NDEBUG; the new wrapper disables
constructor elision. A test-only allocator interposer measures rejection behavior
without changing the production allocator. The independent model covers bounded
small integer cases and is finite regression evidence, not proof of every possible
input or a substitute for exact-head integration and independent review.
