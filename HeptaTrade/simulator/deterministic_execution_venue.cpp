#include "deterministic_execution_venue.h"

#include <chrono>
#include <cmath>

namespace
{
std::uint64_t NowMs()
{
    return static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count());
}

bool ValidQuote(const double bid, const double ask,
                const std::uint64_t observedAtMs,
                const std::uint64_t staleAfterMs,
                const std::uint64_t nowMs)
{
    return std::isfinite(bid) && std::isfinite(ask) &&
        bid > 0.0 && ask > 0.0 && ask >= bid &&
        observedAtMs > 0 && observedAtMs <= nowMs &&
        staleAfterMs >= observedAtMs && nowMs <= staleAfterMs;
}
}

DeterministicExecutionVenue::DeterministicExecutionVenue()
    : m_nextOrderId(1000000), m_generation(1)
{
}

void DeterministicExecutionVenue::SetEventSink(const EventSink& sink)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    m_sink = sink;
}

void DeterministicExecutionVenue::SetQuote(const std::string& instrument, double bid, double ask)
{
    const std::uint64_t now = NowMs();
    SetQuoteObserved(instrument, bid, ask, now, now + 60000);
}

void DeterministicExecutionVenue::SetQuoteObserved(
    const std::string& instrument, double bid, double ask,
    std::uint64_t observedAtMs, std::uint64_t staleAfterMs)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    Quote quote;
    quote.bid = bid;
    quote.ask = ask;
    quote.observedAtMs = observedAtMs;
    quote.staleAfterMs = staleAfterMs;
    m_quotes[instrument] = quote;
}

MarketQuoteSnapshot DeterministicExecutionVenue::GetQuoteSnapshot(
    const std::string& instrument, std::uint64_t nowMs) const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    MarketQuoteSnapshot snapshot;
    snapshot.instrument = instrument;
    const std::map<std::string, Quote>::const_iterator found = m_quotes.find(instrument);
    if (found == m_quotes.end()) return snapshot;
    snapshot.subscriptionId = "sim:" + instrument;
    snapshot.bid = found->second.bid;
    snapshot.ask = found->second.ask;
    snapshot.observedAtMs = found->second.observedAtMs;
    snapshot.staleAfterMs = found->second.staleAfterMs;
    if (!std::isfinite(snapshot.bid) || !std::isfinite(snapshot.ask) ||
        snapshot.bid <= 0.0 || snapshot.ask <= 0.0 ||
        snapshot.ask < snapshot.bid || snapshot.observedAtMs == 0 ||
        snapshot.staleAfterMs < snapshot.observedAtMs ||
        snapshot.observedAtMs > nowMs)
        snapshot.state = MarketSubscriptionState::Unavailable;
    else
        snapshot.state = nowMs <= snapshot.staleAfterMs ?
            MarketSubscriptionState::Active : MarketSubscriptionState::Stale;
    return snapshot;
}

bool DeterministicExecutionVenue::GetQuote(const std::string& instrument, double& bid, double& ask) const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::map<std::string, Quote>::const_iterator found = m_quotes.find(instrument);
    if (found == m_quotes.end()) return false;
    bid = found->second.bid;
    ask = found->second.ask;
    return ValidQuote(bid, ask, found->second.observedAtMs,
                      found->second.staleAfterMs, NowMs());
}

std::string DeterministicExecutionVenue::Instrument(const InstrumentRef& contract)
{
    return contract.currency.empty() ? contract.symbol : contract.symbol + "." + contract.currency;
}

bool DeterministicExecutionVenue::PlaceOrder(const InstrumentRef& contract, const OrderIntent& order, long* orderId)
{
    return PlaceOrderCorrelated(contract, order, std::string(), orderId);
}

bool DeterministicExecutionVenue::PlaceOrderCorrelated(
    const InstrumentRef& contract, const OrderIntent& order,
    const std::string& correlationId, long* orderId)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::string instrument = Instrument(contract);
    if (instrument.empty() || (order.action != "BUY" && order.action != "SELL") ||
        order.totalQuantity <= 0.0 || !std::isfinite(order.totalQuantity) ||
        (order.orderType != "MKT" && order.orderType != "LMT"))
    {
        m_lastRejectReason = "SIM_INVALID_ORDER";
        return false;
    }
    if (order.orderType == "LMT" && (order.lmtPrice <= 0.0 || !std::isfinite(order.lmtPrice)))
    {
        m_lastRejectReason = "SIM_INVALID_LIMIT_PRICE";
        return false;
    }
    const std::map<std::string, Quote>::const_iterator quote = m_quotes.find(instrument);
    if (quote == m_quotes.end())
    {
        m_lastRejectReason = "SIM_QUOTE_NOT_READY";
        return false;
    }
    const std::uint64_t now = NowMs();
    if (!std::isfinite(quote->second.bid) ||
        !std::isfinite(quote->second.ask) ||
        quote->second.bid <= 0.0 || quote->second.ask <= 0.0 ||
        quote->second.ask < quote->second.bid ||
        quote->second.observedAtMs == 0 ||
        quote->second.staleAfterMs < quote->second.observedAtMs ||
        quote->second.observedAtMs > now)
    {
        m_lastRejectReason = "SIM_QUOTE_INVALID";
        return false;
    }
    if (now > quote->second.staleAfterMs)
    {
        m_lastRejectReason = "SIM_QUOTE_STALE";
        return false;
    }
    Order stored;
    stored.id = m_nextOrderId++;
    stored.instrument = instrument;
    stored.request = order;
    stored.correlationId = correlationId;
    m_orders[stored.id] = stored;
    ++m_generation;
    if (orderId) *orderId = stored.id;
    m_lastRejectReason.clear();
    return true;
}

std::map<std::string, long> DeterministicExecutionVenue::ActiveOrderCorrelations() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    std::map<std::string, long> correlations;
    for (std::map<long, Order>::const_iterator it = m_orders.begin(); it != m_orders.end(); ++it)
    {
        if (!it->second.terminal && !it->second.correlationId.empty())
            correlations[it->second.correlationId] = it->first;
    }
    return correlations;
}

std::map<std::string, long>
DeterministicExecutionVenue::TerminalOrderCorrelations() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    std::map<std::string, long> correlations;
    for (std::map<long, Order>::const_iterator it = m_orders.begin();
         it != m_orders.end(); ++it)
        if (it->second.terminal && !it->second.correlationId.empty())
            correlations[it->second.correlationId] = it->first;
    return correlations;
}

std::map<long, std::string>
DeterministicExecutionVenue::TerminalOrderStatuses() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    std::map<long, std::string> statuses;
    for (std::map<long, Order>::const_iterator it = m_orders.begin();
         it != m_orders.end(); ++it)
        if (it->second.terminal && !it->second.terminalStatus.empty())
            statuses[it->first] = it->second.terminalStatus;
    return statuses;
}

std::set<long> DeterministicExecutionVenue::ExecutionOrderIds() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    std::set<long> executions;
    for (std::map<long, Order>::const_iterator it = m_orders.begin();
         it != m_orders.end(); ++it)
        if (it->second.terminalStatus == "Filled") executions.insert(it->first);
    return executions;
}

bool DeterministicExecutionVenue::CanCancelOrder(long orderId, std::string* reason) const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::map<long, Order>::const_iterator found = m_orders.find(orderId);
    if (found == m_orders.end()) { if (reason) *reason = "SIM_ORDER_NOT_FOUND"; return false; }
    if (found->second.terminal) { if (reason) *reason = "SIM_ORDER_TERMINAL"; return false; }
    return true;
}

bool DeterministicExecutionVenue::CancelOrder(long orderId)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    std::map<long, Order>::iterator found = m_orders.find(orderId);
    if (found == m_orders.end() || found->second.terminal)
    {
        m_lastRejectReason = "SIM_CANCEL_REJECTED";
        return false;
    }
    found->second.cancelRequested = true;
    ++m_generation;
    m_lastRejectReason.clear();
    return true;
}

std::string DeterministicExecutionVenue::LastRejectReason() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_lastRejectReason;
}

void DeterministicExecutionVenue::RestoreNextOrderIdAtLeast(long nextOrderId)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    if (nextOrderId > m_nextOrderId) m_nextOrderId = nextOrderId;
}

void DeterministicExecutionVenue::Process()
{
    std::vector<SimulatedOrderEvent> events;
    EventSink sink;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        sink = m_sink;
        for (std::map<long, Order>::iterator it = m_orders.begin(); it != m_orders.end(); ++it)
        {
            Order& order = it->second;
            if (order.terminal) continue;
            if (!order.submitted)
            {
                order.submitted = true;
                SimulatedOrderEvent submitted;
                submitted.orderId = order.id;
                submitted.instrument = order.instrument;
                submitted.side = order.request.action;
                submitted.status = "Submitted";
                submitted.remainingQuantity = order.request.totalQuantity;
                events.push_back(submitted);
            }
            if (order.cancelRequested)
            {
                order.terminal = true;
                order.terminalStatus = "Cancelled";
                ++m_generation;
                SimulatedOrderEvent cancelled;
                cancelled.orderId = order.id;
                cancelled.instrument = order.instrument;
                cancelled.side = order.request.action;
                cancelled.status = "Cancelled";
                cancelled.remainingQuantity = order.request.totalQuantity;
                events.push_back(cancelled);
                continue;
            }
            const std::map<std::string, Quote>::const_iterator quoteFound =
                m_quotes.find(order.instrument);
            const std::uint64_t now = NowMs();
            if (quoteFound == m_quotes.end() ||
                !ValidQuote(quoteFound->second.bid, quoteFound->second.ask,
                    quoteFound->second.observedAtMs,
                    quoteFound->second.staleAfterMs, now))
                continue;
            const Quote& quote = quoteFound->second;
            const bool marketable = order.request.orderType == "MKT" ||
                (order.request.action == "BUY" && order.request.lmtPrice >= quote.ask) ||
                (order.request.action == "SELL" && order.request.lmtPrice <= quote.bid);
            if (!marketable) continue;
            order.terminal = true;
            order.terminalStatus = "Filled";
            ++m_generation;
            const double fillPrice = order.request.action == "BUY" ? quote.ask : quote.bid;
            m_positions[order.instrument] += order.request.action == "BUY" ?
                order.request.totalQuantity : -order.request.totalQuantity;
            SimulatedOrderEvent filled;
            filled.orderId = order.id;
            filled.instrument = order.instrument;
            filled.side = order.request.action;
            filled.status = "Filled";
            filled.filledQuantity = order.request.totalQuantity;
            filled.averageFillPrice = fillPrice;
            events.push_back(filled);
        }
    }
    if (sink)
        for (std::size_t i = 0; i < events.size(); ++i) sink(events[i]);
}

double DeterministicExecutionVenue::Position(const std::string& instrument) const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::map<std::string, double>::const_iterator found = m_positions.find(instrument);
    return found == m_positions.end() ? 0.0 : found->second;
}

std::map<std::string, double> DeterministicExecutionVenue::Positions() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_positions;
}

std::set<long> DeterministicExecutionVenue::ActiveOrderIds() const
{
    std::set<long> active;
    std::lock_guard<std::mutex> lock(m_mutex);
    for (std::map<long, Order>::const_iterator it = m_orders.begin(); it != m_orders.end(); ++it)
        if (!it->second.terminal) active.insert(it->first);
    return active;
}

SimulatedRecoveryAuditSnapshot
DeterministicExecutionVenue::RecoveryAuditSnapshot() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    SimulatedRecoveryAuditSnapshot snapshot;
    snapshot.generation = m_generation;
    for (std::map<long, Order>::const_iterator it = m_orders.begin();
         it != m_orders.end(); ++it)
    {
        const Order& order = it->second;
        if (!order.terminal)
        {
            snapshot.activeOrderIds.insert(it->first);
            if (!order.correlationId.empty())
                snapshot.activeCorrelations[order.correlationId] = it->first;
            continue;
        }
        if (!order.correlationId.empty())
            snapshot.terminalCorrelations[order.correlationId] = it->first;
        if (!order.terminalStatus.empty())
            snapshot.terminalStatuses[it->first] = order.terminalStatus;
        if (order.terminalStatus == "Filled")
            snapshot.executionOrderIds.insert(it->first);
    }
    snapshot.complete = snapshot.generation != 0;
    return snapshot;
}
