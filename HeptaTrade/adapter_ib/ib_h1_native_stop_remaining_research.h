#pragma once
// Strictly diagnostic pairing of a real SDK openOrder(Submitted) and the
// *latest* native orderStatus (filled/remaining) for the SAME frozen H1 stop.
// These callbacks do NOT by themselves prove current server-held risk cover,
// ordinary OCA exit safety, or authorize Broker orders/Driver stop.
#include "ib_h1_native_stop_echo_research.h"
#include <cmath>

namespace hepta_ib_h1_native_stop_remaining {
inline bool MatchesUnfilledStopStatus(
    const IBEvent& originalOpenOrder,
    const IBEvent& latestStatus,
    const hepta_bracket_h1::FrozenBracket& frozen,
    const hepta_bracket_h1::BoundRefs& refs,
    std::uint64_t brokerEpoch)
{
    if (!hepta_ib_h1_native_stop_echo::MatchesOriginalSdkStop(
            originalOpenOrder,frozen,refs,brokerEpoch))
        return false;
    return latestStatus.type==IBEventType::OrderStatus &&
        latestStatus.id==frozen.childOrderId &&
        latestStatus.id==originalOpenOrder.id &&
        latestStatus.key=="Submitted" &&
        latestStatus.value.empty() &&
        latestStatus.requestId == 0 &&
        latestStatus.connectionEpoch==brokerEpoch &&
        latestStatus.connectionEpoch==originalOpenOrder.connectionEpoch &&
        latestStatus.brokerClientId==frozen.clientId &&
        latestStatus.account==frozen.account &&
        latestStatus.queueIngressMonotonicNs!=0 &&
        latestStatus.queueIngressMonotonicNs>=originalOpenOrder.queueIngressMonotonicNs &&
        // SDK orderStatus lacks a native account/parent field; never make it
        // a complete native order witness. Permanent ID and current owner
        // binding must match the separately captured real openOrder.
        !latestStatus.nativeOrder.present &&
        latestStatus.nativeOrder.permanentId>0 &&
        latestStatus.nativeOrder.permanentId==
            originalOpenOrder.nativeOrder.permanentId &&
        latestStatus.remainingQuantityKnown &&
        std::isfinite(latestStatus.number) &&
        std::isfinite(latestStatus.number2) &&
        std::isfinite(latestStatus.number3) &&
        latestStatus.number2==0.0 &&
        latestStatus.number3==frozen.quantity &&
        latestStatus.number2+latestStatus.number3==frozen.quantity;
}
} // namespace hepta_ib_h1_native_stop_remaining
