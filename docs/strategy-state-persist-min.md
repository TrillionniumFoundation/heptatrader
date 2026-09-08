# Legacy monolith strategy-state persistence

Status: LEGACY  
Applies to: default-disabled `ib_fx_multi_strategy` path

The historical monolith periodically wrote strategy cooldown and position-intent fields to a JSON file. This is not authoritative broker position, order, fill, or Execution command state and must not be used to reopen risk after a restart.

Canonical recovery uses the Execution journal plus fresh venue account, position, active-order, terminal-order, execution, quote, epoch, and generation barriers. Research strategy state may be restored only as non-authoritative input and must be reconciled or reset before producing new intent.
