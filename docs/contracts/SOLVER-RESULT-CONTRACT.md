# SolverResult V1

Status: current bounded runtime contract; additional solver statuses are future scope
Applies to: global allocator, AllocationPlan and decision evidence
Verification: schema plus `tests/global_allocator_tests.cpp` independent objective, constraint, bound and negative-input assertions
Authority: solver-result semantics

## Implemented statuses and fields

The current `AllocationSolverResult` and `schemas/solver-result-v1.json` support exactly two lowercase statuses. They do not implement a tolerance-optimal, best-known nonlinear or timeout-specific solver.

| Runtime status | Required interpretation |
|---|---|
| `optimal` | `exact=true`; complete feasible-net assignment search completed within the combination cap; objective=primal bound=upper bound; absolute gap=0. |
| `feasible_not_proven` | `exact=false`; a conservative feasible incumbent and independently computed relaxed upper bound; no claim of exact enumeration or optimality. A zero gap alone does not change this status. |

On invalid policy, stale/inconsistent input, failed arithmetic or failed digest creation, `GlobalAllocationResult.accepted=false`, `reasonCode` describes rejection, and the decision receipt remains invalid. There is no successful SolverResult carrying `NO_FEASIBLE_PLAN`, `STALE_INPUT` or `NUMERIC_FAILURE` in the current wire schema.

| C++ member | Schema field / semantics |
|---|---|
| `objective` | `objective_raw`; selected integer utility, not measured P&L. |
| `primalBound` | `primal_bound_raw`; equals the feasible incumbent objective for this maximization problem. |
| `upperBound` | `upper_bound_raw`; exact optimum on exhaustive completion, otherwise sum of per-module nonnegative best utilities with joint constraints relaxed. |
| `absoluteGap` | `absolute_gap_raw`; upper bound minus objective, checked and nonnegative. |
| `combinationsExplored` | `combinations_explored`; complete assignments assessed in exact mode; candidate attempts in fallback. These two counts must not be compared as identical work units. |
| `exact`, `status`, `digest` | Exactness flag, supported status and canonical SHA-256 digest. |

The allocation plan separately binds the result to proposal-set and snapshot digests, policy revision, allocator epoch, creation time and validity. Build/binary identity belongs in external exact-artifact evidence; this C++ result does not carry a binary digest, seed, primal/dual residuals or a wall-clock termination record.

## Proof and validation boundaries

The mathematical model, final-net constraints, canonical reject-first tie rule, lifetime revalidation and bounded fallback are specified in [Global Optimization](GLOBAL-OPTIMIZATION-CONTRACT.md). An infeasible prefix may become a feasible full portfolio; it must not be pruned while claiming an exact optimum. A claimed input digest must be checked against rebuilt member contents before solving.

Schema validation checks shape and supported status/flag combinations; it cannot prove optimum, recompute cross-field arithmetic, authenticate an issuer or establish safe execution. The independent complete-assignment CTest oracle checks optimum, selected choices, net targets and relaxed bounds for deterministic fixtures. Execution risk and external qualification remain separate.

## Future extensions, not current guarantees

`OPTIMAL_WITHIN_TOLERANCE`, `BEST_KNOWN`, `FEASIBLE_FALLBACK`, nonlinear bounds, explicit cancellation/deadline outcomes, solver seeds and residuals require a versioned schema and a new implementation/verification contract before use. Do not emit the former uppercase design vocabulary under today's lowercase v1 schema, fill absent bounds with zero, or describe an iteration cap as a proven target-host deadline.

Any new solver must retain independent constraint validation and downstream Execution risk; no future status may create Broker, PAPER or LIVE authority by itself.
