#include "ib_paper_execution_runtime_internal.h"

#include <algorithm>
#include <cmath>
#include <map>
#include <mutex>
#include <sstream>
#include <vector>

using namespace ib_paper_execution_runtime_internal;

void IbPaperExecutionRuntimeComposition::ProjectAcceptedRecentBrokerOrder(
    const OmsJournalEvent& event, const std::string& agentId)
{
    const std::map<long, RecentBrokerOrder>::const_iterator existing =
        m_recentBrokerOrders.find(event.orderId);
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
    accepted.status = "Accepted";
    accepted.remainingQuantity = event.qty;
    accepted.serviceEpoch = event.brokerServiceEpoch;
    accepted.connectionEpoch = event.brokerConnectionEpoch;
    m_recentBrokerOrders[event.orderId] = accepted;
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
    if (!event.instrument.empty()) order.instrument = event.instrument;
    if (!event.side.empty()) order.side = event.side;
    if (!event.status.empty() && !order.terminal)
        order.status = event.status;
    if (!event.riskCode.empty()) order.reasonCode = event.riskCode;
    if (!order.terminal)
        order.remainingQuantity = event.brokerRemainingQuantity;
}

void IbPaperExecutionRuntimeComposition::ApplyRecentBrokerExecutionIdentity(
    RecentBrokerOrder& order, const OmsJournalEvent& event)
{
    if (event.eventType != "broker_execution" ||
        event.brokerExecutionId.empty() || order.brokerExecutionAmbiguous)
        return;
    if (order.brokerExecutionId.empty())
    {
        order.brokerExecutionId = event.brokerExecutionId;
        order.brokerExecutionQuantity = event.qty;
        order.brokerExecutionPrice = event.price;
        return;
    }
    const bool conflictingReplay =
        order.brokerExecutionId != event.brokerExecutionId ||
        order.brokerExecutionQuantity != event.qty ||
        order.brokerExecutionPrice != event.price;
    if (!conflictingReplay) return;
    // One recent_orders row cannot faithfully represent multiple executions
    // or a conflicting replay under one execution id. Remove the usable id so
    // performance accounting must fail closed instead of inventing a VWAP.
    order.brokerExecutionId.clear();
    order.brokerExecutionAmbiguous = true;
    order.brokerExecutionQuantity = 0.0;
    order.brokerExecutionPrice = 0.0;
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
    if (!economicCallback || !positiveEconomicEvidence) return;
    order.economicFill = true;
    order.filledQuantity = std::max(order.filledQuantity, event.qty);
    order.averageFillPrice = event.price;
    order.reasonCode.clear();
}

void IbPaperExecutionRuntimeComposition::ApplyRecentBrokerTerminalEvidence(
    RecentBrokerOrder& order, const OmsJournalEvent& event,
    bool reconciledTerminal, bool positiveEconomicEvidence)
{
    if (reconciledTerminal)
    {
        order.terminal = true;
        order.remainingQuantity = 0.0;
        if (order.economicFill)
        {
            order.status = "Filled";
            order.reasonCode.clear();
        }
    }
    const bool explicitNonFillTerminal =
        event.status == "Cancelled" || event.status == "ApiCancelled" ||
        event.status == "Inactive" || event.status == "Rejected";
    const bool economicTerminal = event.eventType == "broker_order_status" &&
        event.status == "Filled" && positiveEconomicEvidence;
    const bool errorTerminal = event.eventType == "broker_error" &&
        (event.brokerErrorCode == 201 || event.brokerErrorCode == 202);
    if (explicitNonFillTerminal || reconciledTerminal ||
        economicTerminal || errorTerminal)
        order.terminal = true;
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
        std::map<long, RecentBrokerOrder>::iterator oldest =
            m_recentBrokerOrders.begin();
        for (std::map<long, RecentBrokerOrder>::iterator it =
                 m_recentBrokerOrders.begin();
             it != m_recentBrokerOrders.end(); ++it)
            if (it->second.observedAtMs < oldest->second.observedAtMs)
                oldest = it;
        m_recentBrokerOrders.erase(oldest);
    }
}

std::string IbPaperExecutionRuntimeComposition::RecentBrokerOrdersJson(
    const AgentExecutionContext& context) const
{
    std::vector<RecentBrokerOrder> matching;
    {
        std::lock_guard<std::mutex> lock(m_recentBrokerOrdersMutex);
        for (std::map<long, RecentBrokerOrder>::const_iterator it =
                 m_recentBrokerOrders.begin();
             it != m_recentBrokerOrders.end(); ++it)
            if (it->second.agentId == context.agentId &&
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
    std::ostringstream output;
    output << '[';
    for (std::size_t i = 0; i < matching.size() && i < 64; ++i)
    {
        if (i != 0) output << ',';
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
    }
    output << ']';
    return output.str();
}
