# XT/QMT venue plan

Status: EXPERIMENTAL  
Applies to: `HeptaTrade/adapter_xt/`

The current adapter is an event-shape scaffold with **no transport**. It fails connection, query, place, and cancel operations with `XT_TRANSPORT_NOT_IMPLEMENTED`; it must not emit synthetic broker acknowledgements or local submitted order IDs.

Before status promotion, implement and review:

- a pinned SDK or a versioned Python sidecar protocol;
- process/credential/network isolation from Agent and Gateway;
- connection and account-subscription state machine;
- asset, position, order, trade, quote, order-error, and cancel-error barriers;
- stable order/trade/cancel correlation and uncertain-send recovery;
- exchange price type, lot size, market hours, short-sale, and account semantics;
- journal, risk, authoritative snapshot, reconciliation, and fault-injection tests;
- broker-observed PAPER qualification.

The common Execution Service remains the sole mutation authority. A sidecar may translate XT semantics but may not become a second order path. See [`modules/xt-adapter.md`](modules/xt-adapter.md).
