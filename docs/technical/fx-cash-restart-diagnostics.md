# FX CASH restart catch-up diagnostics

The stable startup reason `IB_FX_CASH_RESTART_CATCHUP_EXECUTION_INVALID` covers several different failure paths. Restarting with that reason alone does not identify which path failed. The runtime now emits one additional diagnostic line containing fixed categories only:

```text
IB FX CASH restart catch-up rejected: IB_FX_CASH_RESTART_CATCHUP_EXECUTION_INVALID detail=UNRESOLVED_ACCEPTED_ORDER ignored_execution_mask=3 economic_evidence_observed=0
```

The example is synthetic. The diagnostic does not contain account, order, execution, command, session or correlation identifiers, timestamps, prices, quantities, or journal contents. It does not authorize a retry, baseline adjustment, ledger repair or order submission.

## Fixed detail codes

| Detail | Meaning |
| --- | --- |
| `EXECUTION_CORRELATION_MISMATCH` | An otherwise eligible execution supplied a nonempty correlation different from its submitted order. |
| `EXECUTION_SIDE_MISMATCH` | The normalized side differs from the submission, or is neither BUY nor SELL. |
| `EXECUTION_BASELINE_MISSING` | A complete eligible execution has no instrument baseline. |
| `EXECUTION_DELTA_NONFINITE` | Adding the signed execution quantity produces a nonfinite cumulative delta. |
| `EXECUTION_GROSS_LIMIT` | The resulting cumulative delta exceeds the existing gross-position bound. |
| `EXECUTION_BALANCE_NONFINITE` | Adding the resulting delta to the baseline balance is nonfinite. |
| `UNRESOLVED_ACCEPTED_ORDER` | A submitted and accepted order has neither complete eligible execution evidence nor the existing exact zero-fill cancellation evidence. |
| `ADVANCED_BASELINE_INVALID` | The complete catch-up calculation returned successfully, but the runtime's subsequent baseline validation rejected the proposed state. |

The original four-argument API and top-level reason strings are retained. The diagnostic overload reports the same result and leaves the original baselines unchanged on failure. Success still publishes the advanced baseline only after all existing validations pass.

## Ignored execution mask

Some execution records are non-authoritative for a submitted and accepted order. The evaluator continues to ignore them under the original predicates. If that order later fails to close, the mask records which predicates rejected its execution records. Several bits may be set across several records; this is not an assertion that one specific record failed every listed check.

| Bit value | Rejection category |
| ---: | --- |
| 1 | Different broker service epoch |
| 2 | Different broker connection epoch |
| 4 | Different session |
| 8 | Different account |
| 16 | Different strategy |
| 32 | Different execution domain |
| 64 | Different instrument |
| 128 | Missing broker execution ID |
| 256 | Nonfinite quantity |
| 512 | Nonpositive quantity |
| 1024 | Quantity greater than the submitted quantity |
| 2048 | Nonfinite price |
| 4096 | Nonpositive price |

The mask belongs to the order responsible for the reported failure; records for unrelated order IDs do not contribute. For `UNRESOLVED_ACCEPTED_ORDER`, `economic_evidence_observed=1` means the existing eligible-economic-evidence set contains that order. This includes partial execution evidence and does not establish a complete fill. A zero mask and zero flag can mean that no eligible terminal evidence was observed. Neither value establishes a zero-fill cancellation.

These fields classify existing evidence. They do not weaken correlation, epoch, financial, cancellation or baseline checks. The diagnostic state is reset at the beginning of each call and is clear after a successful call.

## Verification and operational limit

The pure C++ diagnostic target exercises all detail branches in the catch-up evaluator, each of the thirteen ignored-execution predicates, accumulated flags, unrelated-order isolation, unchanged baselines on rejection, legacy reason compatibility and diagnostic reset. The post-validation detail formatter is covered separately; the runtime's existing baseline validation remains authoritative.

The diagnostic target constructs synthetic journal events in memory and calls no broker API, service manager or live ledger. A passing test or completed code change establishes that diagnostics are available in the source. It does not establish that a particular deployed service has adopted the change or that its failed lifecycle evidence has been repaired. Actual failure classification requires a diagnostic from the corresponding runtime and original evidence.
