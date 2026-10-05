#pragma once
#include "ib_api_wrapper.h"
#include <cerrno>
#include <cstdlib>

// Observed Broker 2172 is the global advance notice of a future TWS/Gateway
// minimum-version change, not a rejected order or data entitlement. Preserve
// its normal error/OMS diagnostics; exclude ONLY its unscoped form from the
// sender safety latch and risk-error fuse. All scoped/unknown errors still fail.
inline bool IsIbGlobalUpgradeNotice(const IBEvent& event) {
    return event.type==IBEventType::Error && event.key=="2172" && event.id==-1 &&
        event.account.empty() && event.order.orderRef.empty() &&
        event.advancedOrderRejectJson.empty();
}

inline bool IbAdmissionBlockingEvent(const IBEvent& event) {
        if (IsIbGlobalUpgradeNotice(event)) return false;
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

