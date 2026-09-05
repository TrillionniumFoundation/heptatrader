# Global Optimization Contract V1

Status: current normative; implementation corrections require same-revision tests and review
Applies to: proposal aggregation and Global Decision solver
Verification: `tests/global_allocator_tests.cpp`, exact enumeration, independent oracle, malformed-input and digest tests
Authority: global-allocation semantics

## Current bounded model

`GlobalAllocator::Allocate` selects at most one candidate per strategy module, or rejects that module. `ProposalSetBuilder::Build` establishes canonical module/proposal order; each member's `StrategyProposalContract::ValidateAndSeal` establishes canonical candidate and instrument order.

Let x_i be reject or a candidate of module i; u_i(x_i) its integer utility and q_i,k(x_i) its signed target contribution for instrument k. Reject contributes zero to both. The implemented problem is:

```text
maximize U = sum_i u_i(x_i)
subject to T_k = sum_i q_i,k(x_i)
           abs(T_k) <= instrumentAbsoluteLimits[k]
           sum_k abs(T_k) <= maximumGrossTarget
           count(k with T_k != 0) <= maximumInstruments
           every selected target instrument is registered in the policy
```

The quantities and utilities are `DecisionMicrounits` integers. Each sealed utility and target is within +/-9,000,000,000,000,000 raw units. The gross constraint sums absolute **net targets**, not absolute per-strategy contributions. It is a quantity-space budget, not an implemented multi-currency cash, margin, notional, covariance or market-impact model. Producers must agree on utility units and comparison horizon; the allocator does not estimate or calibrate utility.

Policy requires a canonical revision ID, positive gross and instrument limits within the fixed numeric range, active-instrument bound 1..4096 and exact-combination cap 1..1,000,000. Unregistered instruments make a candidate inadmissible even when its target is zero or another candidate would cancel it.

## Input validation and immutable solving copy

A `ProposalSet` is a mutable value, not an unforgeable issuer capability. Its digest contains claimed member digests; comparing only that outer digest cannot prove that the current candidate bodies still match those claims.

Before solving, the allocator checks the outer envelope and 1..256 member bound, then rebuilds through `ProposalSetBuilder::Build` using the original `capturedAtMs` and `snapshotValidUntilMs`. Rebuilding validates actual member bodies, identity, numeric bounds, duplicate candidates/targets, member digests, book/snapshot consistency and effective lifetime. The rebuilt digest must equal the supplied set digest. The solver consumes only the rebuilt canonical copy, not the caller's mutable ordering.

Revalidation does not renew horizons: each expiry remains the minimum of snapshot expiry, proposal expiry and `capturedAtMs + horizonMs`. Use at `createdAtMs >= validUntilMs` is refused. Missing or malformed members, inconsistent set headers and changed bodies cannot be repaired by recomputing only the outer digest. Failure returns `ALLOCATION_PROPOSAL_SET_INVALID` or the existing time/policy reason with an invalid receipt.

Deriving the member names for this consistency check does not independently prove that the caller included every module expected by an external admission policy. The caller must still enforce the original expected-module set and issuer/snapshot authority. SHA-256 integrity is not a signature. Callers must not concurrently mutate an input while passing it by reference.

## Exact enumeration: limits apply to complete net portfolios

For candidate counts m_i, the Cartesian search size is product_i(1 + m_i), including reject. When it fits the policy cap, exact mode examines complete assignments. `ProjectCandidate` admits only registered targets and checked integer accumulation; `TargetsFeasible` applies hard portfolio limits at complete leaves.

An intermediate signed sum exceeding a policy limit is **not** a valid pruning criterion: a later module can offset it. Gross and active-instrument constraints also need final-net evaluation. For validated inputs, per-instrument prefix sums and objective sums fit int64: at most 256 contributions, each at most 9e15 in magnitude. Checked arithmetic remains in place. An overflowing final gross sum is infeasible against the much smaller positive gross limit, not a fabricated zero or a wrapped valid value.

```text
Module A: utility +10, target +4
Module B: utility  -5, target -1
Instrument limit: 3; gross limit: 10

reject/reject: target  0, utility  0, feasible
reject/B:      target -1, utility -5, feasible
A/reject:      target +4, utility 10, infeasible
A/B:           target +3, utility  5, feasible and optimal
```

Pruning A before B would incorrectly report objective 0 and a zero optimality gap. The regression fixture checks this case in both signs, separate gross/count repairs, and large but representable intermediate targets.

Ties compare the canonical choice vector: reject first, then candidate IDs in lexical order for each canonical module. Length-prefixed hash serialization must not be used to order these economic ties. Thus an all-zero-utility set defaults to all-reject regardless of ID length. A zero- or negative-utility hedge can still be selected when needed to increase the **total** objective or meet a final constraint. Zero-utility candidates are not globally deleted before solving.

A successful exact run reports `optimal`, `exact=true`, primal=objective=upper bound and gap=0. `combinationsExplored` counts complete assignments assessed, including policy-infeasible leaves; structurally impossible candidate branches can be rejected before a leaf. It is not a count of broker actions or solver wall-clock iterations.

## Bounded fallback

When the Cartesian size exceeds the cap, fallback visits modules in canonical order and candidates by descending utility, then lexical ID. It selects the first positive-utility candidate whose resulting incumbent remains feasible, otherwise rejects the module. Unlike exact search, this conservative incremental choice is permitted because fallback never asserts optimality.

The incumbent objective is a feasible lower bound. A separate relaxation ignores joint constraints:

```text
upperBound = sum_i max(0, max_j u_i,j)
absoluteGap = upperBound - incumbentObjective
```

The implementation checks bound arithmetic and reports `feasible_not_proven`, `exact=false`, even when that gap happens to be zero. Negative-utility hedges or cross-module interactions can make fallback miss the optimum; its bound and status must remain truthful. The all-reject assignment is feasible for a valid policy.

The combination cap is an algorithmic work bound, not an enforced wall-clock deadline. Large target lists, hashing, copying and allocations still consume time. This implementation does not claim a target-host hard-real-time SLO or timeout cancellation. Those require separately implemented admission, cancellation and host qualification.

## Output, proof scope and compatibility

The result includes canonical nonzero net targets, accepted candidate identifiers, rejected proposal identifiers, solver metadata, bound/gap, input and snapshot digests, policy revision, epoch and lifetime. `SolverDigest` and `PlanDigest` bind their declared fields; the construction-restricted same-process `GlobalDecisionReceipt` is issued only after validation and solving.

Correcting invalid prefix pruning, stale nested-body acceptance and length-based tie ordering changes some choices, explored counts and digests. Old results and exact-head CI do not automatically qualify the corrected implementation. Logical contract/schema identifiers remain v1; the module patch version records the behavior correction. Never reuse old receipts or rewrite old evidence to appear produced by the new solver.

Execution independently revalidates its authority context, snapshot, policy, freshness and risk before any mutation. A feasible final target is not proof that every intermediate venue order is safe, that hedges will fill atomically, or that a strategy is profitable. No solver test grants PAPER, LIVE, merge or deployment authority.

## Requirement-to-assertion map

| Requirement | Direct C++ regression |
|---|---|
| Correct complete-net instrument limits, including a negative-utility hedge | `TestOffsettingInstrumentLimitAndNegativeUtility` |
| Correct complete-net gross and active-instrument limits | `TestOffsettingGrossAndInstrumentCountLimits` |
| Large intermediate arithmetic without incorrect policy pruning | `TestLargeIntermediateTargetsRemainRepresentable` |
| Reject changed nested bodies despite intact/rehashed outer digest | `TestNestedMutationCannotReuseMemberDigests` |
| Self-consistent hashes cannot replace schema/range/book checks | `TestSelfConsistentDigestsDoNotReplaceSemanticValidation` |
| Canonical solving and no horizon renewal | `TestCanonicalRebuildDoesNotRenewHorizonsOrDependOnCandidateOrder` |
| Exact cap boundary, unknown instruments and repeated ties | `TestExactBudgetBoundaryUnknownInstrumentsAndDeterministicTies` |
| Reject-first/lexical ties without discarding useful zero-utility hedges | `TestZeroUtilityRejectsUnlessNeededAndTiesUseLexicalIds` |
| Independent optimum, selected plan, fallback bound and permutation invariance | `TestSeededCompleteAssignmentOracle` |

The seeded oracle enumerates complete assignments using separate test code, not the production projection/feasibility functions. It covers 512 deterministic fixtures and 35,837 complete combinations with small integer values, checking exact objective/targets/choices, fallback feasibility/bounds and normalized input permutations. This is finite regression evidence, not proof of every possible input or a replacement for full repository integration and independent review. All assertions remain active with `NDEBUG`.
