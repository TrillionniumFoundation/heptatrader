#include "ib_gateway_adapter.h"
#include <cmath>
#include <exception>
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
VenueFlattenResult HeptaIBGatewayAdapter::PlaceReduceOnlyOrderCorrelated(
    const IBContractLite& contract, const IBOrderLite& order,
    const std::string& instrument,
    double expectedPositionQuantity,
    std::uint64_t expectedConnectionEpoch,
    std::uint64_t expectedPositionGeneration,
    const std::string& expectedQuoteSubscriptionId,
    std::uint64_t expectedQuoteObservedAtMs,
    std::uint64_t expectedQuoteStaleAfterMs,
    const std::string& venueCorrelationId,
    double expectedQuoteBid,
    double expectedQuoteAsk)
{
    std::lock_guard<std::recursive_mutex> lock(m_apiMutex);
    long orderId = -1;
    try
    {
        m_lastRejectReason.clear();
        const auto reject = [&]() {
            if (m_lastRejectReason.empty()) return VenueFlattenResult();
            return VenueFlattenResult::RejectedBeforeSend(
                VenueFlattenRejectionFromReasonCode(m_lastRejectReason), m_lastRejectReason);
        };
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
            return reject();
        }
        if (position != expectedPositionQuantity)
        {
            m_lastRejectReason =
                "IB_FLATTEN_POSITION_CHANGED_BEFORE_SEND";
            return reject();
        }
        if (!m_correlationSnapshot.complete ||
            m_correlationSnapshot.connectionEpoch !=
                expectedConnectionEpoch ||
            !m_correlationSnapshot.activeOrderIds.empty())
        {
            m_lastRejectReason =
                "IB_FLATTEN_ACTIVE_ORDER_SNAPSHOT_UNSAFE";
            return reject();
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
            return reject();
        }
        if (!order.orderRef.empty())
        {
            m_lastRejectReason = "IB_ORDER_REF_RESERVED";
            return reject();
        }
        IBOrderLite correlatedOrder = order;
        std::string reason;
        if (!EncodeVenueOrderRef(
                venueCorrelationId, correlatedOrder.orderRef, reason))
        {
            m_lastRejectReason = reason;
            return reject();
        }
        IBFinalOrderSendContext context;
        context.exactReduceOnly = true;
        context.instrument = instrument;
        context.quoteSubscriptionId = expectedQuoteSubscriptionId;
        context.quoteObservedAtMs = expectedQuoteObservedAtMs;
        context.quoteStaleAfterMs = expectedQuoteStaleAfterMs;
        context.quoteBid = expectedQuoteBid;
        context.quoteAsk = expectedQuoteAsk;
        bool sendAttempted = false;
        if (!PlaceOrderInternal(
                contract, correlatedOrder, &orderId, &context, &sendAttempted))
        {
            // The wrapper may have performed IO before returning false. No mutable
            // last-error string can prove a pre-send rejection after invocation.
            if (sendAttempted)
                return VenueFlattenResult::Uncertain("IB_FLATTEN_API_OUTCOME_UNCERTAIN", orderId);
            return reject();
        }
        MergeIncrementalActiveOrder(orderId, venueCorrelationId);
        return VenueFlattenResult::Submitted(orderId);
    }
    catch (const std::exception& error)
    { return VenueFlattenResult::Uncertain(error.what(), orderId); }
    catch (...)
    { return VenueFlattenResult::Uncertain("unknown adapter flatten exception", orderId); }
}
