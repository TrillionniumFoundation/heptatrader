# Legacy CSV reconciliation notes

Status: LEGACY  
Applies to: deprecated monolith-only `HeptaTrade/reconcile/`

The historical reconciliation engine reads CSV snapshots and an OMS v2 replay. It is retained for compatibility with the default-disabled legacy monolith and is **not** an authoritative recovery or PAPER/LIVE admission mechanism.

Known limitations include comparing open-order counts rather than full stable identities and deriving position-like quantities from order records rather than an execution/fill ledger. Missing CSV input may be classified as manual/warn rather than proving a broker barrier. Therefore this path must not open risk-increasing operation.

The canonical Execution Service instead uses command identities, durable send attempts, active/terminal venue correlations, execution evidence, fresh account/position/order barriers, connection epoch/generation, owner fencing, and fail-closed recovery.

A future reconciliation schema must compare, per account and instrument:

- stable client, broker order, permanent order, and execution IDs;
- side, order type, quantity, cumulative fill, remaining, average fill, and terminal state;
- replace/cancel lineage and corrections/busts;
- authoritative position, cash, currency exposure, and margin;
- unresolved send attempts and unknown broker objects;
- exact snapshot epoch/generation and source timestamps.

Legacy smoke scripts and Windows paths are not current runbooks. See [`modules/execution-service.md`](modules/execution-service.md), [`modules/authoritative-state.md`](modules/authoritative-state.md), and [`operations/startup-shutdown.md`](operations/startup-shutdown.md).
