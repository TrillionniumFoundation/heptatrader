# HeptaTrader change gates: scope and release authority

Status: CURRENT — development and operator separation, 2026-10-15.

## Do not confuse independent decisions

1. **Merge source**: source correctness, changed-file review, appropriate tests, exact commit and preserved compatibility. Read-only tools, documentation and historical evidence fixes do **not** require profitable strategy evidence, a flat broker account, Flex/NAV acceptance, a product Witness, or a new trading session. A merge does not authorize installation or orders.
2. **Run read-only research**: verify data integrity and resource/credential isolation. Original censored/missing labels stay censored. Model economic promotion criteria do not block collector bug fixes, source-health repairs or offline training.
3. **Change production observability**: verify output identity, resources, freshness, reversible rollout and no order-opcode authority. Do not require economic alpha or product model promotion for a monitoring-only repair.
4. **Change a PAPER trading executable or existing driver**: prove running release identity, original pending-command and broker-terminal reconciliation, owner/lease continuity, positions, kill switch, and rollback. A non-flat deployment must use an explicitly tested non-flat continuity path; NEVER manufacture a flat state or require closing a position for unrelated observability.
5. **Allow NEW risk / promote a model / go LIVE**: requires a separately scoped authorized operator or strategy decision. A green CI run, `mutation_ready=true`, a read-only source READY, or a successful restart alone can never disarm the native kill switch or prove profit.

## Operational disposition of holds

Record the concrete blocking condition and its owner. Keep separate: native kill switch / operator hold, broker recovery, order or owner uncertainty, invalid fresh market quote, research source-publisher SLA, and strategy economic eligibility. An observation-only failure does not silently revoke reduce-only risk exits. Expiring maintenance artifacts cannot become undocumented permanent operator policy.

## Branches, tests and evidence

Close or supersede finished research Draft PRs based on their actual delta against current main; an old stacked parent chain is not a prerequisite for integrating an independent valid change. Archive original receipts and the reason for an immutable failed trial; do not repeat cross-host custody checks without a concrete new failure mode. Ordinary changes use existing exact-head native and Python CI. Separate full sanitizer, installed release qualification, IB broker interaction and live-host deployment tests apply **only** to changes that actually need those proofs. Keep failure diagnostics and historical evidence.

## Known operational mismatch to resolve separately

As observed 2026-10-15, main retired the optional FX opening hysteresis. The Pocket4 resident FX release still reported an older `fx-opening-hysteresis.v1` in its state while holding nonzero FX exposure. This document does not approve replacement of that release, state editing, flattening, broker orders or disarming the native operator hold; an actual preserved-position continuity release is separate.
