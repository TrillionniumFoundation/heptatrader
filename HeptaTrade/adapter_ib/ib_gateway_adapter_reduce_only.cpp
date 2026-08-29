#include "ib_gateway_adapter.h"
#include <cmath>
namespace {
bool MatchesFlattenSnapshot(const IBAuthoritativeRiskSnapshot& risk,
    bool streamAuthoritative, std::uint64_t epoch, std::uint64_t generation,
    bool positionResolved, double position, double expectedPosition)
{
    return risk.accountComplete && risk.positionsComplete &&
        risk.fxCashComplete && streamAuthoritative && epoch != 0 &&
        generation != 0 && risk.connectionEpoch == epoch &&
        risk.positionsGeneration == generation && positionResolved &&
        std::isfinite(position) && std::isfinite(expectedPosition);
}}
bool HeptaIBGatewayAdapter::PlaceReduceOnlyOrderCorrelated(
    const IBContractLite& contract, const IBOrderLite& order,
    const std::string& instrument,
    double expectedPositionQuantity,
    std::uint64_t expectedConnectionEpoch,
    std::uint64_t expectedPositionGeneration,
    const std::string& expectedQuoteSubscriptionId,
    std::uint64_t expectedQuoteObservedAtMs,
    std::uint64_t expectedQuoteStaleAfterMs,
    const std::string& venueCorrelationId,
    long* outOrderId,
    double expectedQuoteBid,
    double expectedQuoteAsk)
{
    std::lock_guard<std::recursive_mutex> lock(m_apiMutex);
    double position = 0.0;
    std::string positionReason;
    const bool positionResolved = ResolveAuthoritativePositionQuantity(
        instrument, contract, position, positionReason);
    if (!MatchesFlattenSnapshot(
            m_riskSnapshot, m_eventStreamAuthoritative,
            expectedConnectionEpoch, expectedPositionGeneration,
            positionResolved, position, expectedPositionQuantity))
    {
        m_lastRejectReason =
            "IB_FLATTEN_POSITION_SNAPSHOT_MISMATCH";
        return false;
    }
    if (position != expectedPositionQuantity)
    {
        m_lastRejectReason =
            "IB_FLATTEN_POSITION_CHANGED_BEFORE_SEND";
        return false;
    }
    if (!m_correlationSnapshot.complete ||
        m_correlationSnapshot.connectionEpoch !=
            expectedConnectionEpoch ||
        !m_correlationSnapshot.activeOrderIds.empty())
    {
        m_lastRejectReason =
            "IB_FLATTEN_ACTIVE_ORDER_SNAPSHOT_UNSAFE";
        return false;
    }
    const double quantity = order.totalQuantity;
    const bool opposite =
        (position > 0.0 && order.action == "SELL") ||
        (position < 0.0 && order.action == "BUY");
    if (!opposite || !std::isfinite(quantity) || quantity <= 0.0 ||
        quantity > std::fabs(position) ||
        (order.orderType == "LMT" &&
         quantity != std::fabs(position)))
    {
        m_lastRejectReason =
            "IB_FLATTEN_NOT_EXACT_REDUCE_ONLY";
        return false;
    }
    if (!order.orderRef.empty())
    {
        m_lastRejectReason = "IB_ORDER_REF_RESERVED";
        return false;
    }
    IBOrderLite correlatedOrder = order;
    std::string reason;
    if (!EncodeVenueOrderRef(
            venueCorrelationId, correlatedOrder.orderRef, reason))
    {
        m_lastRejectReason = reason;
        return false;
    }
    IBFinalOrderSendContext context;
    context.exactReduceOnly = true;
    context.instrument = instrument;
    context.quoteSubscriptionId = expectedQuoteSubscriptionId;
    context.quoteObservedAtMs = expectedQuoteObservedAtMs;
    context.quoteStaleAfterMs = expectedQuoteStaleAfterMs;
    context.quoteBid = expectedQuoteBid;
    context.quoteAsk = expectedQuoteAsk;
    long orderId = -1;
    if (!PlaceOrderInternal(
            contract, correlatedOrder, &orderId, &context))
        return false;
    MergeIncrementalActiveOrder(orderId, venueCorrelationId);
    if (outOrderId) *outOrderId = orderId;
    return true;
}
