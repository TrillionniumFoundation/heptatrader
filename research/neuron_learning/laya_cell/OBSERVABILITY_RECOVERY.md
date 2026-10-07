# Laya observability recovery and execution-reward separation

## Scope

This change restores only the three Laya services. It does not restart, wrap or
modify the FX/BTC drivers, change confidence or cost admission, submit orders,
loosen quote TTL, interpolate missing outcomes, or activate model promotion.

## Diagnosed failures

The installed learning configuration belonged to a previous accounting period.
Restarting it against the current period would fail its existing period checks.
The old services had been cleanly stopped, not crash-restarted. Recovery uses a
new period-scoped directory and an empty current-period seed; prior datasets,
three candidates and window records remain intact, never relabelled as new data.
Old expired plans receive explicit administrative rejection/closure, not a moved
window or fabricated missing prediction.

Sixteen old worker logs ended in NO_FRESH_INPUT_IN_RESERVED_SLOT. An input-only
replay using the original recorded arrival timestamps found eight of these slots
had a sufficiently long fresh contiguous suffix, while the old fixed last-24
history still crossed a previous gap. This is evidence about input availability,
not a reconstructed model prediction or proof of profitable OOS behavior.
The other slots remain genuinely unsupported by those recorded inputs.

The worker also loaded the full model only after the slot began, and the evaluator
blocked on its child. It could later classify an endpoint using a quote snapshot
captured before that wait. The patch prepares workers 90 seconds before a fixed
input slot, polls them without blocking other outcomes, and rereads the actual
quote prefix immediately before deciding outcome availability. The 60-second
input window, five-minute horizon, five-second source TTL and endpoint tolerance,
and 30-second maximum path gap are unchanged.

## Input and inference evidence

`read_quotes` validates source prefixes without rebuilding a full training
dataset for every prediction/evaluation poll. `contiguous_history` stops at the
most recent real gap and still requires six observations, the same fixed feature
scales and a source quote no older than five seconds. The original raw sources
are retained. Numeric bid/ask normalization changes representation, not price.

Before inference, a new immutable input record binds candidate, fixed slot,
source prefixes and exact input. Phase records expose process start, model ready,
input acquisition, quote age, quote-read duration and prediction completion.
The model remains in eval/no-grad mode; weights, feature definitions and trained
adapters are not retuned to pass a slot. Failures preserve phase and root message,
rather than collapsing every cause into PREDICTION_PROCESS_FAILED alone.

The evaluator remains independently responsible for future labels and screening.
A slow or failed worker cannot prevent unrelated endpoints from being observed.
True stale/unready quotes or gaps are still failures/censoring, not usable prices.
A few successful slots are operational evidence, not a complete stability or
predictive-skill qualification.

## Economic outcome versus model-learning credit

`resident_rewards.current` now independently replays EUR/USD and BTC/USD from one
committed OMS prefix through the existing accounting implementation. Each
executed flat-to-flat interval retains original commands, unique execution IDs,
actual USD commissions, gross and net amounts. Short FX intervals are handled
with signed quantities. Remaining positions, missing commission, invalid
identities and unsupported cross-zero allocations are never fabricated as closed
rewards. Both gains and losses remain represented.

Two durable tables have different meanings:

- `execution_rewards`: observed financial outcomes, even when original model
  input is unavailable. Unconfirmed original-command settlement is explicitly
  recorded. This is not automatically a trainable or canonical ledger fact.
- `rewards`: only independently settled, exact-input-bound value targets.
  The existing prohibition on treating remote behavior profit as on-policy Laya
  policy gradient remains in place.

Missing commission leaves net value null. Missing input leaves input/model
identity null and learning_eligible=false. Missing status for one episode does
not suppress unrelated known financial outcomes. Active IDs are rederived from
the current snapshot; corrections produce new immutable versions with the same
episode identity, not cumulative duplicate rewards. Journal entries, actual fees,
accounting period and trader pending state are never changed by the bridge.

The current historical trading runtimes no longer emit the exact old learning
export contract. Consequently existing economic outcomes can be audited without
pretending their lost model inputs can be reconstructed. No timestamp proximity,
confidence match, current quote history or reused order number is accepted as an
exact historical input binding. A financial critic or actor is not trained on
these unbound observations. A future producer change must separately preserve
that exact input/command link; this recovery does not secretly alter traders.

## Verification boundaries

Regression cases cover contiguous histories, refusal to bridge real gaps,
source timestamps, quote TTL, missing endpoints, fresh endpoint rereads,
nonblocking prewarming, structured worker failures, independent financial and
credit records, missing fees, corrections, residual positions and duplicate
executions. Existing Laya/native, core, source and driver suites are retained.
Deployment checks actual process state and prospective predictions/outcomes;
status=running alone is not an end-to-end acceptance claim. Canonical Agentd
owner admission and automatic execution cutover remain separate and disabled.
