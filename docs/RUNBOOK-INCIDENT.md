# Incident response runbook

Status: current
Applies to: Simulator and IB PAPER runtime incidents
Verification: same-revision CI

## Severity

- **P1** — possible unintended exposure, journal/fencing failure, authoritative state break, kill-switch uncertainty or unresolved mutation.
- **P2** — degraded broker/data/reconciliation capability with no known unintended exposure.
- **P3** — bounded development or observability issue with no runtime mutation impact.

## First actions

1. Engage the deployment-owned kill switch or disable new-risk capability.
2. Preserve cancel/authoritative flatten capability when its proof remains safe.
3. Do not restart repeatedly or submit replacement command IDs for uncertain mutations.
4. Capture service epoch/fencing generation, command IDs, journal path/health, broker connection epoch, active orders and positions.
5. Query health and exact command status; reconcile before reopening risk.

## P1 examples

- durable journal write failure or send without durable command evidence;
- broker/OMS order or position disagreement while risk remains open;
- stale/incomplete quote or state accepted for new exposure;
- execution epoch/fence mismatch;
- kill-switch state cannot be read/enforced;
- uncertain command exceeds reconciliation deadline.

## Commands

```bash
systemctl status hepta-tool-gateway.service hepta-execution-simulator.service
journalctl -u hepta-tool-gateway.service -u hepta-execution-simulator.service --since -30min
heptactl system.get_health
heptactl orders.list
heptactl portfolio.list_positions
```

For IB PAPER use the matching service/socket units and operator-controlled broker console. Never expose broker credentials or kill-switch mutation to an Agent session.

## Recovery authorization

Reopen risk only after the current execution authority reports complete authoritative state, no unresolved exposure-bearing command, reconciled owner/order/position projections, valid config/credential/kill state and stable epoch/fencing/generation.

## Post-incident record

Record timeline, owner, environment/venue, affected command/order IDs, exposure impact, root cause, safety mechanism behavior, code/config fix, regression test and whether any runbook or metric failed to make the state obvious. This is an ordinary incident record, not a release/campaign finalization artifact.
