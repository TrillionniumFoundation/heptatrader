# Legacy strategy-state persistence

`HEPTA_STRATEGY_STATE_PERSIST` and `HEPTA_STRATEGY_STATE_PATH` belong to the deprecated legacy monolith strategy engine. They are not authoritative state for the Agent OS Execution Service and must not be used to recover Broker orders, positions, command IDs or reconciliation readiness.

Legacy state may contain cooldown, signal and research-position fields and can support migration/replay experiments. It must be stored privately and atomically, but its successful load does not authorize a mutation. Canonical recovery uses OMS v4 journal, Execution service epochs, venue correlation and authoritative snapshots.

New strategy work should persist a versioned, hash-bound decision state/receipt and submit any later TradeIntent through Gateway/Execution. Do not extend this legacy file into a second OMS or Broker authority.
