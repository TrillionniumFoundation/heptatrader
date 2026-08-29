# XTQMT as First-class Venue (parallel to CTP / IB)

Status: proposal
Applies to: proposed XT/QMT venue; no active runtime capability
Verification: same-revision CI for repository placement only

## What is done now

1. Added new adapter folder and scaffold class:
   - `HeptaTrade/adapter_xt/xt_gateway_adapter.h`
   - `HeptaTrade/adapter_xt/xt_gateway_adapter.cpp`

2. Added build entry:
   - `HeptaTrade/HeptaTrader.vcxproj` now includes `adapter_xt/xt_gateway_adapter.cpp`

3. Added config example fields:
   - `HeptaTrade/HeptaTraderConfig.xml.example`
   - new `<XTServer ... />`, `<XTRisk ... />`, and `<Runtime Venue="AUTO" />`

## Design target

Unify venue runtime as:
- `CTP`
- `IB`
- `XT`

and keep one OMS/risk/reconcile pipeline.

## Next implementation steps (Stage-2)

1. Main runtime routing
   - Extend `NormalizeVenue`/selector in `HeptaDemoStrategyTrader.cpp` to accept `XT`.
   - Add XT startup branch (`Init/Connect/ReqAccountSummary/ReqPositions`).

2. Config loading
   - Parse `<XTServer>` and `<XTRisk>` into `HeptaXTConfig`.
   - Keep env override style aligned with CTP/IB (`HEPTA_ALLOW_XT_ORDERS`, etc.).

3. Event normalization
   - Map xtquant callback events to OMS events:
     - `venue_connect`
     - `order_intent/place_sent/status/cancel/reject`
     - account/position snapshots for reconcile.

4. Risk gating
   - Reuse pre-trade risk semantics before XT place/cancel.
   - Global kill switch + flatten-only must behave same as IB/CTP.

5. Smoke tests
   - `HEPTA_VENUE=XT` startup smoke
   - XT order loop smoke (paper/sim)
   - OMS schema validation on XT path.

## Notes

Current XT adapter is a scaffold (stub events), intended to lock API shape and build integration first.
It does NOT place real XT orders yet.
