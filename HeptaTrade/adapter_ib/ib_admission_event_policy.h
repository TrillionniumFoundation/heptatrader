#pragma once
#include "ib_api_wrapper.h"
#include <cerrno>
#include <cstdlib>
#include <climits>

// Provenance comes from the wrapper's locally sent CASH request ledger, not
// from the Broker error text or numeric id alone. These failures invalidate a
// pending quote request, not an account/order recovery generation. They still
// block an OPEN/RESERVED quote admission at the sender boundary.
inline bool IsIbCorroboratedCashMarketDataRequestError(const IBEvent& event) {
    return event.type == IBEventType::Error &&
        event.localMarketDataRequestError &&
        (event.key == "354" || event.key == "300") &&
        event.id >= 1000000 && event.id <= INT_MAX &&
        event.requestId == event.id && event.connectionEpoch != 0 &&
        event.brokerClientId >= 0 && event.account.empty() &&
        event.order.orderRef.empty() && event.advancedOrderRejectJson.empty() &&
        event.contract.secType == "CASH" && !event.contract.symbol.empty() &&
        !event.contract.currency.empty() && !event.contract.exchange.empty();
}

// Observed Broker 2172 is the global advance notice of a future TWS/Gateway
// minimum-version change, not a rejected order or data entitlement. Preserve
// its normal error/OMS diagnostics; exclude ONLY its unscoped form from the
// sender safety latch and risk-error fuse. All scoped/unknown errors still fail.
inline bool IsIbGlobalUpgradeNotice(const IBEvent& event) {
    return event.type==IBEventType::Error && event.key=="2172" && event.id==-1 &&
        event.account.empty() && event.order.orderRef.empty() &&
        event.advancedOrderRejectJson.empty();
}

// An unscoped 2105 reports historical-market-data farm loss, not a live
// quote-send result. Retain its original Broker error for diagnostics and
// never interpret it as a positive market-data readiness witness. A scoped
// order/request/account 2105 remains unsafe, as do 1100/1101/2110.
inline bool IsIbUnscopedHistoricalFarmDisconnect(const IBEvent& event) {
    return event.type == IBEventType::Error && event.key == "2105" &&
        event.id == -1 && event.account.empty() &&
        event.order.orderRef.empty() && event.advancedOrderRejectJson.empty();
}

// Broker 2157, when truly unscoped, is a security-*definition* farm
// notification, not loss of the connected live-quote feed or a rejected
// market-data/order request. An original 2157/-1 caused an otherwise healthy
// PAPER Execution to fatal-exit at 2026-10-14 23:45:48 (+08). Never classify
// a scoped or economic callback as an advisory; do not infer farm recovery.
inline bool IsIbUnscopedSecDefFarmDisconnect(const IBEvent& event) {
    return event.type == IBEventType::Error && event.key == "2157" &&
        event.id == -1 && (event.requestId == 0 || event.requestId == -1) &&
        event.account.empty() && event.whyHeld.empty() &&
        event.order.orderRef.empty() && event.order.action.empty() &&
        event.order.orderType.empty() &&
        event.order.positionEffect.empty() &&
        event.order.totalQuantity == 0 && event.order.cashQuantity == 0 &&
        event.order.lmtPrice == 0 && event.order.auxPrice == 0 &&
        !event.order.outsideRth &&
        event.marketCapPrice == 0 &&
        event.overflowGeneration == 0 && event.droppedEventCount == 0 &&
        event.advancedOrderRejectJson.empty() &&
        event.contract.symbol.empty() &&
        event.contract.secType.empty() && event.contract.exchange.empty() &&
        event.contract.currency.empty() &&
        event.economics.Absent() && !event.localMarketDataRequestError &&
        event.number == 0 && event.number2 == 0 && event.number3 == 0;
}

inline bool IbAdmissionBlockingEvent(const IBEvent& event) {
        if (IsIbGlobalUpgradeNotice(event) ||
            IsIbUnscopedHistoricalFarmDisconnect(event) ||
            IsIbUnscopedSecDefFarmDisconnect(event)) return false;
        if (event.type == IBEventType::ConnectionClosed ||
            event.type == IBEventType::EventQueueOverflow)
            return true;
        if (event.type != IBEventType::Error || event.key.empty())
            return event.type == IBEventType::Error;
        char* end = nullptr;
        errno = 0;
        const long code = std::strtol(event.key.c_str(), &end, 10);
        if (errno != 0 || end == event.key.c_str() ||
            end == nullptr || *end != '\0')
            return true; // unknown broker error: keep the send gate closed
        switch (code) {
        case 2103: // scoped market-data farm loss is consumed by the runtime
                   // before/after admission; it is not by itself a global
                   // sender-boundary fault.
        case 2104: // market-data farm connection OK
        case 2106: // HMDS farm connection OK
        case 2107: // HMDS farm inactive (not needed for live quotes)
        case 2108:
        case 2109: // outside-RTH notice
        case 2119: // CASH-farm status is handled by the runtime gate; it is
                   // promoted to a blocking witness only while admission is
                   // formally open (see PushEvent above).
        case 2158: // sec-def farm connection OK
        case 1102: // connectivity restored; data maintained
            return false;
        case 1101: // connectivity restored; data lost: force a fresh
                    // admission/recovery boundary before another quote send
            return true;
        default:
            // Unknown and all other broker errors keep the admission closed;
            // routing will preserve the exact callback/reason.
            return true;
        }
    }
