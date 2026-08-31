# Authoritative reconciliation rules

Canonical reconciliation compares the durable local projection with Broker/venue authoritative account, position, open-order, execution and completion evidence. Legacy CSV startup reports and the deprecated monolith are not production truth.

## Invariants

1. venue identity is normalized and bound to the configured account/execution domain;
2. connection/service epochs prevent evidence from different Broker sessions being spliced together;
3. open-order and position snapshots are complete before they can authorize risk increase;
4. duplicate/out-of-order callbacks are idempotently projected;
5. local orders missing at the venue, venue orders missing locally, quantity/status mismatch and unknown correlation are explicit drift;
6. drift never causes automatic risk-increasing repair;
7. outcome uncertain is resolved through authoritative queries using the original command/correlation IDs;
8. every correction or terminal resolution is durably journaled。

## Decision classes

- **PASS**：authoritative snapshot complete and equal to local projection。
- **WAIT**：snapshot still collecting；no new risk is allowed。
- **RESTRICT**：state is known enough for cancel/reduce-only/flatten but not for risk increase。
- **BLOCK/P1**：identity、completeness、correlation、journal or projection is inconsistent or uncertain。

Missing positions/open orders are not assumed empty unless the venue protocol provides an authenticated end-of-snapshot marker for the current connection epoch。

## Startup and reconnect

Startup begins restricted。Execution replays the journal，establishes a new service/connection epoch，requests authoritative snapshots，consumes explicit completion markers and reconciles before opening any order gate。Reconnect invalidates freshness from the old connection；it does not silently reuse the previous ready state。

## Operator response

On drift or uncertain outcome：engage kill switch or remain restricted；preserve journal and callback evidence；query current open orders、fills and positions；resolve each command deterministically；run the relevant fault regression；only then restore read-only followed by bounded mutations。Do not edit the journal or insert synthetic terminal events。
