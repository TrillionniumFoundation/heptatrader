# Authoritative reconciliation rules

Status: current
Applies to: `HeptaTrade/execution/`, `HeptaTrade/state/`, Simulator and IB PAPER recovery
Verification: `canonical-full-suite` on the exact revision; external PAPER host checks are separate

## Authority

Reconciliation compares durable OMS command/owner state with an authoritative venue snapshot. New risk remains blocked whenever the runtime cannot prove that open orders, terminal results, positions and service identity belong to one current connection epoch/generation.

The inactive CSV `HeptaTrade/reconcile/reconcile_engine.*` utility is not the active runtime authority and must not be used as proof of broker consistency.

## Startup/reconnect sequence

```text
replay durable journal
-> enter recovery-only / mutation-blocked state
-> establish new execution service identity and broker connection epoch
-> capture authoritative active and terminal order/correlation state
-> resolve uncertain place/cancel commands
-> reconcile owner projections
-> refresh positions/account/FX cash and quote subscriptions
-> audit owner active orders and unresolved commands
-> reopen risk only when all required state is complete
```

## Fail-closed conditions

- command may have reached venue but no authoritative result exists;
- active or terminal correlation conflict;
- broker order lacks an Execution owner mapping;
- owner projection references a non-authoritative active order;
- position/account/cash/quote snapshot is incomplete or from another generation;
- execution epoch or fencing generation changed;
- journal append required to record reconciliation fails;
- connection lifecycle requires a new epoch.

Stable `RECOVERY_*`, `OMS_*`, `IB_*` or `SIM_*` reason codes identify the exact block.

## Safe exit

A recovery/new-risk block does not automatically forbid owner-scoped cancel or authoritative flatten. The action proceeds only if its own current venue/order/position proof is complete and cannot increase absolute exposure.

## Uncertain commands

A caller queries `execution.get_command_status` or retries the exact command ID. Reconciliation binds venue correlations and terminal evidence to that command. A caller never creates a replacement command ID merely because a response was lost.

## Exit criteria

Risk increase may reopen only when:

- no unresolved uncertain command can carry exposure;
- owner mapping and authoritative active orders agree;
- required account/position/cash/quote projections are complete and current;
- epoch/fencing/generation are stable;
- the mutation block reason is durably cleared by the owning authority.

## Tests

Coordinator, Simulator E2E, state projection and IB PAPER recovery tests cover duplicate, uncertain, reconnect, correction and owner-audit paths in the bounded/optional lanes.
