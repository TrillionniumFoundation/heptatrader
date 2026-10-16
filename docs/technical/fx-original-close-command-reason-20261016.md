# Original PAPER FX close reason bound to original Broker command

Issue: [P0 #331](https://github.com/TrillionniumFoundation/heptatrader/issues/331)

## What changed

The FX resident Driver originally assigned `state["last_exit_reason"]` immediately
before `prepare_send("CLOSE")`. That state is mutable and overwritten on the
next close; assigning it retroactively to a historical execution is not
valid original-command evidence.

In the new **additive Driver state** extension, a CLOSE trigger is frozen
*before* contacting the HSR1 preparation service. After the original
`prepare-close` response returns its authoritative `command_id`, the pending
command records a `heptatrader.driver-close-trigger.v1` containing:

- Original HSR1 command ID, exact observed reason and source classification.
- Observation timestamp and position at decision time.
- Original authoritative quote timestamp, bid and ask where the
  strategy itself evaluated a market-based stop or model reversal.

Allowed runtime origins: `MAX_ADVERSE_MOVE`, `MODEL_REVERSAL`,
`HALT_REDUCE_ONLY`, `OWNER_RECOVERY_REDUCE_ONLY` and
`CRYPTO_CANARY_EXIT`. Arbitrary reasons and malformed, inverted or
future-timestamped quotes fail **before preparing any original HSR1 command**.
The existing one-and-only transport attempt, risk policy, quote-evaluation
cadence, broker order type and maximum quantity are unchanged.

When `inspect_pending` matches an *accepted* original CLOSE command to its
correlated Broker execution and authoritative position (including
terminal partial reductions), it adds a bounded record to
`state["close_command_attribution"]`. It contains the original command,
broker order ID, exact execution and venue correlation identities, previously
frozen trigger, quantity/position boundaries and whether the fill is
terminal/partial. Duplicate command/execution pairs are idempotent; a changed
execution identity for the same command fails closed.

Legacy CLOSE commands without a captured reason produce
`LEGACY_UNATTRIBUTED`, **never** a value copied from mutable
`last_exit_reason`. Rejected, pending and unknown sends do not acquire a
fabricated filled-close receipt.

## Critical evidence limits

This is the **original Driver's causal-intent attribution**, not a Broker-
signed proof that the strategy's stop was executable or that a 20 bp fill
could have occurred. The local Driver state remains mutable and is not
the authoritative OMS. The original HSR1 and Execution receipts still
own the command, order and execution identity; this feature cannot
mint/replay/reissue an order, change the risk exit or promote a model.

The 2026-10-14 original EUR/USD loss and service stop cannot be
retroactively reclassified from these newly added fields. Its
historical trigger remains unproven unless a contemporary original
immutable receipt already contains it.

This code is **research-only until the pinned resident Driver release is
explicitly rebuilt and qualified**. The current Pocket4 service and
PAPER position must not be changed as part of these source tests.

## Tests / validation

On Pocket4, the original driver, 100k USD FX notional, PAPER profile and
new original CLOSE attribution tests run together: **78/78 PASS**.
Additional adversarial coverage includes quote inversion/malformed numbers,
malicious trigger labels, OPEN mislabeled as an exit, uncertain submit,
accepted full CLOSE, original correlated execution IDs, terminal partial
close, legacy unattributed history, bounded receipts and conflicting
re-execution identities. No real Broker orders were sent by tests.

**Still separate P0:** systemd manual-stop shield is installed and active;
independent Broker-native stop/parent+child/OCA working protection across
Driver outage is not yet proven. Keep [#331](https://github.com/TrillionniumFoundation/heptatrader/issues/331) OPEN.
