# Current100k fixed outcome event cutoff — 2026-11-03

The final report's 120-second publication allowance does not extend the registered outcome window. The evaluator now supplies `event_cutoff_ms=min(now, plan['outcome_end_ms'])` to the existing native reward replay.

`resident_rewards.current` keeps its default behavior when the optional argument is absent. With the argument, it retains the complete original OMS prefix and hash, and constructs an in-memory event view using original `ts_ms` values. Normal events after the cutoff and no later than the observation clock are excluded. The report records the cutoff, exclusion count, retained count, and timestamp basis. Original records are never edited or redated.

Malformed in-scope evidence and timestamps later than the observation clock still reach the unchanged native validator. Fees, executions, and settlement evidence in the bounded view are checked by the existing accounting replay. The six statistical functions, `score_case`, heads, encoder, registered plan, and all window times remain unchanged.

## Concrete regression cases

- A close completed before the cutoff whose commission arrives after it contributes no observed target, even though unrestricted later replay would produce a reward.
- A close initiated and filled after the cutoff remains outside the settled label view.
- A complete eligible episode whose last fee is exactly at the inclusive cutoff retains its original reward and monetary value.
- Invalid execution economics within the cutoff remain a source replay failure.
- A close sent before the cutoff but first filled after it remains incomplete at the cutoff. The native reason is `SUBMITTED_COMMAND_WITHOUT_FILL_OR_ZERO_FILL_PROOF`; the evaluator must preserve this source-unavailable result and publish `FINAL_UNAVAILABLE_NO_SELECTION` through its existing finalization path. It must not invent zero-fill evidence or borrow the later fill.

Tests use synthetic private OMS files, actual native `hepta_pnl_accounting.replay`, actual reward assembly, and the evaluator snapshot. Only original-input attribution is supplied as a fixture. No model, GPU, order API, or remote service is involved.

## Continuation protection

The only additional runtime path is `research/neuron_learning/laya_cell/resident_rewards.py`. Its permitted transition is fixed by exact full-file SHA-256 values in `current100k_weight_continuation.py`, alongside unchanged whole-module head protection and statistical AST checks. This does not authorize arbitrary reward implementation changes.
