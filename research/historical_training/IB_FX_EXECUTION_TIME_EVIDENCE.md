# IB PAPER FX execution server-time evidence and quote diagnostics

Status: 2026-10-10, standalone research-only read path. No order/cancel, no LIVE access, no model training, no promotion authority. Native Execution, OMS, PnL and PAPER drivers remain unchanged.

## The server-time distinction

IB TWS API Execution.time is the IB server's execution-time string, not the native OMS ts_ms callback arrival, not a venue-certified nanosecond timestamp, and not a timestamped executable order book. References: https://www.interactivebrokers.com/docs/tws-api/ref/execution and https://www.interactivebrokers.com/docs/tws-api/doc/order-management/execution-details/the-execution-object .

The pinned SDK may emit exactly one START_API and one REQ_EXECUTIONS (wire codes 71, 7) from fixed independent client ID 8986. A literal wire whitelist pins the one request ID, the exact USER_RESET PAPER account from the local period, UTC filter, EUR CASH, and the SDK version. Any mutation or foreign message is rejected; the managed-accounts callback must show the exact account in an all-DU PAPER set.

The request may specify up to seven days, but IB may actually return a shorter execution window. Completed execDetailsEnd proves completion of the broker's returned result, **not** coverage of all dates in the filter. The tool sends no automatic retry. Time parser requires explicit UTC or IANA time zone; ambiguous DST, nonexistent local time, unknown time zone, missing zone and out-of-scope results are rejected. The documented IB US/Eastern alias maps to IANA America/New_York, without assuming a fixed offset.

## Append-only OMS source and provenance

Response receipts are written once, privately, with the collector SHA-256, exact period, original OMS digest, request end marker, execution IDs, server second-resolution times, original shares/price/permanent IDs, and zero orders.

Subsequent native OMS appends change the full-file digest. The OMS snapshot utility finds the exact historical digest at a newline boundary, copies only that immutable prefix to a separate private read-only file and re-verifies the source prefix. Any changed, missing, partial, re-ordered or nonmatching evidence stops. Callback repetitions after connection recovery may have different local arrival times or epochs; they are not additional broker fills. Exact economics may not change. Distinct execution IDs with the same IB correction family are held for manual review rather than counted twice.

The quote diagnostic binds the exact broker receipt source code SHA, immutable OMS prefix SHA, period, and all fully re-verified original Laya source archive page pins. It retains the exact list of original live quote source pins and its own code SHA-256 in each finalized receipt. A legitimately growing open quote segment can change the digest of a new observation without altering the previously pinned byte prefix; the earlier receipt remains independently verifiable through its saved pins. Do not replace an older source digest with a newer dynamic feed digest. For each server-time second, it accepts only a source quote already knowable at the **beginning** of that second, within 2 seconds of source arrival and 5 seconds of observed quote age. It never borrows future quotes or IB historical BID/ASK bar OHLC as if those were contemporaneous fills.

The comparison remains research-only. In particular, actual_slippage_verified=false, promotion_allowed=false and orders_sent=0 remain explicit. A server-second and sampled read-only Tool Gateway quote are not proof of venue execution slippage.

## Frozen results, October 10, 2026

A first retrieval returned 18 matching IB executions but failed local reconciliation because the same execution ID appeared in legitimate native OMS callback replays; the original failed receipt is preserved. After the precise fix, a fresh one-request PAPER read returned the same 18 and a completed execDetailsEnd, with all 18 original economics matching. The historical OMS prefix containing 8,610 newline-aligned entries, 9,889,444 bytes, was copied and SHA-verified without altering the native journal. Of the 18 server-time fills, 12 admitted strictly preceding live original quotes; six were excluded for stale quote timing. Apparent price deviations do not claim measured slippage.

The EUR/USD historical source contains 210 calendar days of revised 5-minute prices, with source coverage verified separately. Original live bid/ask and execution-ID deduplicated IB commission callbacks, plus explicitly hypothetical slippage stress, form the offline cost model. Using the same frozen OMS receipt, 190/190 independent FX executions had fee evidence. Three chronological, embargoed OOS test folds all selected no-trade; no positive after-cost alpha or promotion is claimed.

## Release and rollback

The collector is one-shot, operated manually without a timer; it never changes native execution services or strategy state. All files use unique output names and private permissions. Preserve both failed and passed sources. Unit tests cover the wire gate, time-zone ambiguity, duplicate callback semantics, missing immutable prefix, future quote rejection, correction identity and no-actor result. Local SHA-256 is tamper evidence, not independent external signing. Rollback means discontinuing this separate research branch; never delete or revise original Laya, OMS or IB evidence.
