#include "ib_paper_execution_runtime_internal.h"

#include <algorithm>
#include <cmath>
#include <map>
#include <mutex>
#include <sstream>
#include <vector>

using namespace ib_paper_execution_runtime_internal;

std::string IbPaperExecutionRuntimeComposition::RecentBrokerOrderKey(
    const OmsJournalEvent& event, const std::string& agentId) const
{
    if (!event.venueCorrelationId.empty())
        return std::string("corr|") + agentId + "|" + event.traceId + "|" +
            event.executionDomain + "|" + event.venueCorrelationId;
    return std::string("legacy|") + agentId + "|" + event.traceId + "|" +
        event.executionDomain + "|" + event.brokerServiceEpoch + "|" +
        std::to_string(event.brokerConnectionEpoch) + "|" +
        std::to_string(event.orderId);
}

void IbPaperExecutionRuntimeComposition::ProjectAcceptedRecentBrokerOrder(
    const OmsJournalEvent& event, const std::string& agentId)
{
    const std::string key = RecentBrokerOrderKey(event, agentId);
    const std::map<std::string, RecentBrokerOrder>::const_iterator existing =
        m_recentBrokerOrders.find(key);
    if (existing != m_recentBrokerOrders.end() &&
        existing->second.agentId == agentId &&
        existing->second.sessionId == event.traceId &&
        existing->second.executionDomain == event.executionDomain &&
        existing->second.serviceEpoch == event.brokerServiceEpoch &&
        existing->second.connectionEpoch == event.brokerConnectionEpoch &&
        existing->second.status != "Accepted")
        return;
    RecentBrokerOrder accepted;
    accepted.orderId = event.orderId;
    accepted.observedAtMs = event.tsMs > 0 ?
        static_cast<std::uint64_t>(event.tsMs) : 0;
    accepted.agentId = agentId;
    accepted.sessionId = event.traceId;
    accepted.executionDomain = event.executionDomain;
    accepted.account = event.account;
    accepted.instrument = event.instrument;
    accepted.side = event.side;
    accepted.venueCorrelationId = event.venueCorrelationId;
    accepted.commandId = event.reqId;
    accepted.brokerClientId = event.brokerClientId;
    accepted.status = "Accepted";
    accepted.remainingQuantity = event.qty;
    accepted.serviceEpoch = event.brokerServiceEpoch;
    accepted.connectionEpoch = event.brokerConnectionEpoch;
    m_recentBrokerOrders[key] = accepted;
}

void IbPaperExecutionRuntimeComposition::ApplyRecentBrokerOrderIdentity(
    RecentBrokerOrder& order, const OmsJournalEvent& event,
    const std::string& agentId)
{
    if (order.orderId >= 0 &&
        (order.agentId != agentId || order.sessionId != event.traceId ||
         order.executionDomain != event.executionDomain))
        order = RecentBrokerOrder();
    order.orderId = event.orderId;
    order.observedAtMs = event.tsMs > 0 ?
        static_cast<std::uint64_t>(event.tsMs) : order.observedAtMs;
    order.agentId = agentId;
    order.sessionId = event.traceId;
    order.executionDomain = event.executionDomain;
    if (!event.account.empty()) order.account = event.account;
    if (!event.brokerServiceEpoch.empty())
        order.serviceEpoch = event.brokerServiceEpoch;
    if (event.brokerConnectionEpoch > 0)
        order.connectionEpoch = event.brokerConnectionEpoch;
    if (event.brokerClientId >= 0) order.brokerClientId = event.brokerClientId;
    if (!event.reqId.empty() && event.eventType != "order_owner_reconciled_terminal")
    {
        if (!order.commandId.empty() && order.commandId != event.reqId)
        {
            order.brokerExecutionAmbiguous = true;
            order.reasonCode = "IB_RECENT_COMMAND_IDENTITY_CONFLICT";
        }
        else order.commandId = event.reqId;
    }
    if (!event.instrument.empty()) order.instrument = event.instrument;
    if (!event.side.empty()) order.side = event.side == "BOT" ? "BUY" : event.side == "SLD" ? "SELL" : event.side;
    if (!event.venueCorrelationId.empty())
        order.venueCorrelationId = event.venueCorrelationId;
    if (!event.status.empty() && !order.terminal)
        order.status = event.status;
    if (!event.riskCode.empty()) order.reasonCode = event.riskCode;
    if (!order.terminal)
        order.remainingQuantity = event.brokerRemainingQuantity;
}

void IbPaperExecutionRuntimeComposition::ApplyRecentBrokerExecutionIdentity(
    RecentBrokerOrder& order, const OmsJournalEvent& event)
{
    if (event.eventType != "broker_execution" || event.brokerExecutionId.empty() ||
        order.brokerExecutionAmbiguous) return;
    if (!order.executionAggregate.Observe(event.brokerExecutionId, event.qty, event.price)) {
        order.brokerExecutionAmbiguous = true;
        order.brokerExecutionId.clear();
        order.brokerExecutionQuantity = 0;
        order.brokerExecutionPrice = 0;
        order.reasonCode = order.executionAggregate.Reason();
        return;
    }
    // A second genuine execution is not a conflicting replay. The wrapper's
    // qty/price are broker cumulative quantity / cumulative average; never SUM.
    order.brokerExecutionId = order.executionAggregate.LastExecutionId();
    order.brokerExecutionQuantity = order.executionAggregate.Quantity();
    order.brokerExecutionPrice = order.executionAggregate.AveragePrice();
}

bool IbPaperExecutionRuntimeComposition::HasPositiveEconomicEvidence(
    const OmsJournalEvent& event)
{
    return event.qty > 0.0 && event.price > 0.0 &&
        std::isfinite(event.qty) && std::isfinite(event.price);
}

void IbPaperExecutionRuntimeComposition::ApplyRecentBrokerEconomicEvidence(
    RecentBrokerOrder& order, const OmsJournalEvent& event,
    bool positiveEconomicEvidence)
{
    const bool economicCallback = event.eventType == "broker_execution" ||
        event.eventType == "broker_order_status";
    if (!economicCallback || !positiveEconomicEvidence || order.brokerExecutionAmbiguous) return;
    order.economicFill = true;
    if (order.executionAggregate.Valid()) {
        order.filledQuantity = order.executionAggregate.Quantity();
        order.averageFillPrice = order.executionAggregate.AveragePrice();
        order.reasonCode = event.qty > order.filledQuantity ?
            "IB_EXECUTION_DETAILS_PENDING" : "";
    } else if (event.qty >= order.filledQuantity) {
        // A status can inform the display but has no execution id and therefore
        // cannot authorize economic reconciliation by itself.
        order.filledQuantity = event.qty;
        order.averageFillPrice = event.price;
        order.reasonCode.clear();
    }
}

void IbPaperExecutionRuntimeComposition::ApplyRecentBrokerTerminalEvidence(
    RecentBrokerOrder& order, const OmsJournalEvent& event,
    bool reconciledTerminal, bool positiveEconomicEvidence)
{
    if (reconciledTerminal)
    {
        order.terminal = true;
        order.remainingQuantity = 0.0;
        // Removal from active orders proves a lifecycle boundary, not that
        // every requested unit filled. Preserve Cancelled-with-partial-fill.
        if (order.economicFill && !order.brokerExecutionAmbiguous)
            order.reasonCode.clear();
    }
    const bool explicitNonFillTerminal =
        event.status == "Cancelled" || event.status == "ApiCancelled" ||
        event.status == "Inactive" || event.status == "Rejected";
    const bool economicTerminal = event.eventType == "broker_order_status" &&
        event.status == "Filled" && positiveEconomicEvidence;
    const bool errorTerminal = event.eventType == "broker_error" &&
        (event.brokerErrorCode == 201 || event.brokerErrorCode == 202);
    if (explicitNonFillTerminal || reconciledTerminal || economicTerminal || errorTerminal) {
        order.terminal = true;
        order.remainingQuantity = 0;
        if (explicitNonFillTerminal || economicTerminal || errorTerminal)
            order.status = event.status;
    }
    if (event.status != "Filled" || positiveEconomicEvidence ||
        order.economicFill)
        return;
    order.terminal = false;
    order.reasonCode = "IB_FILLED_ECONOMIC_EVIDENCE_REQUIRED";
}

void IbPaperExecutionRuntimeComposition::TrimRecentBrokerOrders()
{
    while (m_recentBrokerOrders.size() > 256)
    {
        std::map<std::string, RecentBrokerOrder>::iterator oldest =
            m_recentBrokerOrders.begin();
        for (std::map<std::string, RecentBrokerOrder>::iterator it =
                 m_recentBrokerOrders.begin();
             it != m_recentBrokerOrders.end(); ++it)
            if (it->second.observedAtMs < oldest->second.observedAtMs)
                oldest = it;
        m_recentBrokerOrders.erase(oldest);
    }
}

std::string IbPaperExecutionRuntimeComposition::RecentBrokerOrdersJson(
    const AgentExecutionContext& context, std::size_t* totalRows,
    std::size_t* returnedRows) const
{
    std::vector<RecentBrokerOrder> matching;
    {
        std::lock_guard<std::mutex> lock(m_recentBrokerOrdersMutex);
        for (std::map<std::string, RecentBrokerOrder>::const_iterator it =
                 m_recentBrokerOrders.begin();
             it != m_recentBrokerOrders.end(); ++it)
            if (it->second.orderId >= 0 && it->second.agentId == context.agentId &&
                it->second.sessionId == context.sessionId &&
                it->second.executionDomain == context.executionDomain)
                matching.push_back(it->second);
    }
    std::sort(matching.begin(), matching.end(),
        [](const RecentBrokerOrder& left, const RecentBrokerOrder& right) {
            if (left.observedAtMs != right.observedAtMs)
                return left.observedAtMs > right.observedAtMs;
            return left.orderId > right.orderId;
        });
    // HEX control/result framing is capped at 32768 bytes. Reserve space for
    // active-order/owner metadata and framing instead of overflowing the client.
    const std::size_t recentByteBudget = 24576;
    std::string result("[");
    std::size_t returned = 0;
    if (totalRows) *totalRows = matching.size();
    for (std::size_t i = 0; i < matching.size() && i < 64; ++i)
    {
        std::ostringstream output;
        output.precision(17);
        const RecentBrokerOrder& order = matching[i];
        output << "{\"order_id\":" << order.orderId
               << ",\"status\":\"" << EscapeJson(order.status) << "\""
               << ",\"terminal\":" << (order.terminal ? "true" : "false")
               << ",\"economic_fill\":"
               << (order.economicFill ? "true" : "false")
               << ",\"filled_quantity\":" << order.filledQuantity
               << ",\"remaining_quantity\":" << order.remainingQuantity
               << ",\"average_fill_price\":" << order.averageFillPrice
               << ",\"reason_code\":\""
               << EscapeJson(order.reasonCode) << "\""
               << ",\"observed_at_ms\":" << order.observedAtMs
               << ",\"evidence_service_epoch\":\""
               << EscapeJson(order.serviceEpoch) << "\""
               << ",\"evidence_connection_epoch\":"
               << order.connectionEpoch
               << ",\"command_id\":\"" << EscapeJson(order.commandId) << "\""
               << ",\"broker_client_id\":" << order.brokerClientId
               << ",\"venue_correlation_id\":\""
               << EscapeJson(order.venueCorrelationId) << "\""
               << ",\"broker_execution_id\":\""
               << EscapeJson(order.brokerExecutionId) << "\""
               << ",\"broker_execution_ambiguous\":"
               << (order.brokerExecutionAmbiguous ? "true" : "false")
               << ",\"broker_execution_quantity\":"
               << order.brokerExecutionQuantity
               << ",\"broker_execution_price\":"
               << order.brokerExecutionPrice
               << ",\"account\":\"" << EscapeJson(order.account) << "\""
               << ",\"execution_domain\":\""
               << EscapeJson(order.executionDomain) << "\""
               << ",\"instrument\":\"" << EscapeJson(order.instrument)
               << "\",\"side\":\"" << EscapeJson(order.side) << "\"}";
        // Keep every real execution identity in the result; the legacy scalar
        // names the broker's latest cumulative witness, never a fabricated ID.
        std::string serialized = output.str();
        serialized.pop_back();
        std::ostringstream evidence;
        evidence.precision(17);
        evidence << ",\"broker_execution_semantics\":\"CUMULATIVE\",\"broker_executions\":[";
        std::size_t index = 0;
        for (const auto& point : order.executionAggregate.Points()) {
            if (index++) evidence << ',';
            evidence << "{\"execution_id\":\"" << EscapeJson(point.first)
                << "\",\"cumulative_quantity\":" << point.second.cumulativeQuantity
                << ",\"cumulative_average_price\":" << point.second.cumulativeAveragePrice << '}';
        }
        evidence << "]}";
        const std::string row = serialized + evidence.str();
        if (result.size() + row.size() + (returned ? 1 : 0) + 1 > recentByteBudget)
            break;
        if (returned) result += ',';
        result += row;
        ++returned;
    }
    result += ']';
    if (returnedRows) *returnedRows = returned;
    return result;
}
