#include "deterministic_execution_venue.h"

#include <chrono>
#include <cmath>
#include <algorithm>
#include <limits>

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

DeterministicExecutionVenue::DeterministicExecutionVenue(const Clock& clock)
    : m_clock(clock ? clock : Clock(NowMs)), m_nextOrderId(1000000),
      m_admittedOrderCount(0), m_generation(1)
{
    // Standalone fixtures retain their existing broad order envelope. The
    // production composition replaces this with its bounded USD policy.
    m_riskConfig.enableOrderSubmission = true;
    m_riskConfig.maxOrderQuantity = 1000000.0;
    m_riskConfig.maxDailyOrders = 1000000;
    m_riskConfig.maxPriceDeviationBps = 0.0;
}

void DeterministicExecutionVenue::SetRiskConfig(const PreTradeRiskConfig& config)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    m_riskConfig = config;
    ++m_generation;
}

PreTradeRiskDecision DeterministicExecutionVenue::PreviewRisk(
    const InstrumentRef& contract, const OrderIntent& order) const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    return EvaluateRiskLocked(contract, order, m_clock());
}

PreTradeRiskDecision DeterministicExecutionVenue::EvaluateRiskLocked(
    const InstrumentRef& contract, const OrderIntent& order, std::uint64_t now) const
{
    const auto reject = [](const char* reason) {
        PreTradeRiskDecision decision;
        decision.reasonCode = reason;
        return decision;
    };
    if (contract.symbol.empty() || (order.action != "BUY" && order.action != "SELL") ||
        order.totalQuantity <= 0.0 || !std::isfinite(order.totalQuantity) ||
        (order.orderType != "MKT" && order.orderType != "LMT"))
        return reject("SIM_INVALID_ORDER");
    if (order.orderType == "LMT" && (order.lmtPrice <= 0.0 || !std::isfinite(order.lmtPrice)))
        return reject("SIM_INVALID_LIMIT_PRICE");
    const auto quote = m_quotes.find(Instrument(contract));
    if (quote == m_quotes.end()) return reject("SIM_QUOTE_NOT_READY");
    if (!std::isfinite(quote->second.bid) || !std::isfinite(quote->second.ask) ||
        quote->second.bid <= 0.0 || quote->second.ask <= 0.0 ||
        quote->second.ask < quote->second.bid || quote->second.observedAtMs == 0 ||
        quote->second.staleAfterMs < quote->second.observedAtMs ||
        quote->second.observedAtMs > now)
        return reject("SIM_QUOTE_INVALID");
    if (now > quote->second.staleAfterMs) return reject("SIM_QUOTE_STALE");
    PreTradeRiskContext context;
    context.venue = "SIMULATOR";
    context.account = "SIM";
    context.symbol = Instrument(contract);
    context.action = order.action;
    context.orderType = order.orderType;
    context.totalQuantity = order.totalQuantity;
    context.evaluatedAtMs = static_cast<std::int64_t>(now);
    context.limitPrice = order.lmtPrice;
    context.accountWhitelisted = true;
    context.paperAccount = true;
    context.positionKnown = true;
    const auto position = m_positions.find(context.symbol);
    if (position != m_positions.end()) context.netPosition = position->second;
    if (m_riskConfig.flattenOnly && std::isfinite(context.netPosition))
    {
        // Flatten orders may bypass monetary exposure limits, so reserve the
        // reducible quantity separately. Ordinary orders admitted before a
        // policy change can consume it too. Do not borrow capacity from an
        // opposite-side order that has not filled, or refund a cancel request.
        double remaining = std::fabs(context.netPosition);
        for (const auto& pending : m_orders)
        {
            const Order& reserved = pending.second;
            if (reserved.terminal || reserved.instrument != context.symbol)
                continue;
            if (!std::isfinite(reserved.request.totalQuantity) ||
                reserved.request.totalQuantity <= 0.0)
                return reject("SIM_FLATTEN_RESERVATION_INCONSISTENT");
            const bool reducesCurrentPosition =
                (context.netPosition > 0.0 && reserved.request.action == "SELL") ||
                (context.netPosition < 0.0 && reserved.request.action == "BUY");
            if (reducesCurrentPosition)
                remaining = reserved.request.totalQuantity >= remaining ?
                    0.0 : remaining - reserved.request.totalQuantity;
        }
        // Use the same strict double subtraction as fills; no tolerance may
        // authorize a small crossing or fabricate an exact-zero position.
        context.netPosition = std::copysign(remaining, context.netPosition);
    }
    // This cumulative conservative budget also includes admitted orders
    // recovered from the journal, so restart cannot reset the order limit.
    context.todayOrderCount = static_cast<int>(std::min<std::uint64_t>(
        m_admittedOrderCount, static_cast<std::uint64_t>(std::numeric_limits<int>::max())));
    context.referencePrice = order.action == "BUY" ? quote->second.ask : quote->second.bid;

    const bool monetary = m_riskConfig.maxOrderNotional > 0.0 ||
        m_riskConfig.maxWorstCaseGrossNotional > 0.0 ||
        m_riskConfig.maxDailyLoss > 0.0 || m_riskConfig.maxDrawdown > 0.0;
    if (!monetary) return PreTradeRiskEngine::Evaluate(m_riskConfig, context);

    // This small fixture has a reviewed unit contract only for these CASH/USD
    // pairs. Derivatives, cross-currency assets and incomplete account PnL are
    // rejected, not valued through a generic quantity*price assumption.
    if (contract.secType != "CASH" || contract.currency != "USD" ||
        (contract.symbol != "EUR" && contract.symbol != "GBP") ||
        !contract.lastTradeDateOrContractMonth.empty() || !contract.right.empty() ||
        contract.strike != 0.0 ||
        (!contract.multiplier.empty() && contract.multiplier != "1") ||
        !contract.localSymbol.empty() || !contract.tradingClass.empty())
    {
        PreTradeRiskDecision rejected;
        rejected.reasonCode = "SIM_RISK_UNIT_OR_QUOTE_UNAVAILABLE";
        return rejected;
    }
    context.authorizedSubject.portfolioId = "simulator-usd-v1";
    context.authorizedSubject.account = context.account;
    context.authorizedSubject.venue = context.venue;
    context.authorizedSubject.baseCurrency = "USD";
    context.authorizedSubject.instruments = {"EUR.USD", "GBP.USD"};
    auto& identity = context.authoritativeSnapshot.identity;
    identity.subject = context.authorizedSubject;
    identity.present = true;
    identity.complete = true;
    identity.connectionEpoch = 1;
    identity.generation = m_generation;
    identity.observedAtMs = static_cast<std::int64_t>(now);
    identity.evaluatedAtMs = static_cast<std::int64_t>(now);
    auto& exposure = context.authoritativeSnapshot.exposure;
    exposure.subject = context.authorizedSubject;
    exposure.present = true;
    exposure.connectionEpoch = identity.connectionEpoch;
    exposure.generation = identity.generation;
    // Every held or reserved position participates, even before activation.
    auto mark = [&](const std::string& instrument, double& price) {
        const auto found = m_quotes.find(instrument);
        if (context.authorizedSubject.instruments.count(instrument) == 0 ||
            found == m_quotes.end() || !ValidQuote(found->second.bid, found->second.ask,
                found->second.observedAtMs, found->second.staleAfterMs, now) ||
            (m_riskConfig.maxSnapshotAgeMs > 0 &&
             now - found->second.observedAtMs >
                static_cast<std::uint64_t>(m_riskConfig.maxSnapshotAgeMs)))
            return false;
        price = found->second.ask;
        return true;
    };
    for (const auto& held : m_positions)
    {
        if (held.second == 0.0) continue;
        double price = 0.0;
        if (!mark(held.first, price)) exposure.present = false;
        exposure.currentGrossNotional += std::fabs(held.second) * price;
    }
    for (const auto& pending : m_orders)
    {
        if (pending.second.terminal) continue;
        double price = 0.0;
        if (!mark(pending.second.instrument, price)) exposure.present = false;
        if (pending.second.request.orderType == "LMT")
            price = std::max(price, pending.second.request.lmtPrice);
        const double value = pending.second.request.totalQuantity * price;
        if (pending.second.request.action == "BUY") exposure.pendingBuyNotional += value;
        else exposure.pendingSellNotional += value;
    }
    context.instrumentContract.specificationId = "simulator-cash-usd-v1";
    context.instrumentContract.specificationVersion = 1;
    context.instrumentContract.instrument = context.symbol;
    context.instrumentContract.kind = PreTradeRiskInstrumentKind::CashFx;
    context.instrumentContract.quantityUnit = PreTradeRiskQuantityUnit::BaseCurrencyUnits;
    context.instrumentContract.priceUnit = PreTradeRiskPriceUnit::QuoteCurrencyPerUnit;
    context.instrumentContract.multiplier = 1.0;
    context.instrumentContract.quoteCurrency = "USD";
    context.authorizedQuoteSourceId = "sim:" + context.symbol;
    context.authorizedFxSourceId = "sim:USD/USD";
    auto& evidence = context.orderNotionalEvidence;
    evidence.present = true;
    evidence.subject = context.authorizedSubject;
    evidence.connectionEpoch = identity.connectionEpoch;
    evidence.generation = identity.generation;
    evidence.contract = context.instrumentContract;
    evidence.quantity = context.totalQuantity;
    const double riskPrice = order.orderType == "LMT" ?
        std::max(context.referencePrice, order.lmtPrice) : context.referencePrice;
    evidence.baseCurrencyNotional = order.totalQuantity * riskPrice;
    evidence.quote.sourceId = context.authorizedQuoteSourceId;
    evidence.quote.instrument = context.symbol;
    evidence.quote.currency = "USD";
    evidence.quote.generation = identity.generation;
    evidence.quote.connectionEpoch = identity.connectionEpoch;
    evidence.quote.observedAtMs = static_cast<std::int64_t>(quote->second.observedAtMs);
    evidence.quote.price = context.referencePrice;
    evidence.fx.sourceId = context.authorizedFxSourceId;
    evidence.fx.fromCurrency = "USD";
    evidence.fx.toCurrency = "USD";
    evidence.fx.generation = identity.generation;
    evidence.fx.connectionEpoch = identity.connectionEpoch;
    evidence.fx.observedAtMs = identity.observedAtMs;
    evidence.fx.rate = 1.0;
    return PreTradeRiskEngine::Evaluate(m_riskConfig, context);
}

void DeterministicExecutionVenue::SetEventSink(const EventSink& sink)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    m_sink = sink;
}

void DeterministicExecutionVenue::SetQuote(const std::string& instrument, double bid, double ask)
{
    const std::uint64_t now = m_clock();
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
    ++m_generation;
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
                      found->second.staleAfterMs, m_clock());
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
    const std::string& correlationId, long* orderId, bool activate)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::string instrument = Instrument(contract);
    const std::uint64_t now = m_clock();
    const PreTradeRiskDecision risk = EvaluateRiskLocked(contract, order, now);
    if (!risk.allow)
    {
        m_lastRejectReason = risk.reasonCode;
        return false;
    }
    if (m_nextOrderId == std::numeric_limits<long>::max() ||
        m_admittedOrderCount == std::numeric_limits<std::uint64_t>::max())
    {
        m_lastRejectReason = "SIM_ORDER_ID_EXHAUSTED";
        return false;
    }
    Order stored;
    stored.id = m_nextOrderId++;
    stored.instrument = instrument;
    stored.request = order;
    stored.correlationId = correlationId;
    stored.activated = activate;
    stored.flattenCapacityReserved = m_riskConfig.flattenOnly;
    m_orders[stored.id] = stored;
    ++m_admittedOrderCount;
    ++m_generation;
    if (orderId) *orderId = stored.id;
    m_lastRejectReason.clear();
    return true;
}

bool DeterministicExecutionVenue::ActivateOrder(long orderId, std::string* reason)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    const auto found = m_orders.find(orderId);
    if (found == m_orders.end() || found->second.terminal)
    {
        if (reason) *reason = "SIM_ACTIVATION_ORDER_UNAVAILABLE";
        return false;
    }
    found->second.activated = true;
    ++m_generation;
    if (reason) reason->clear();
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

bool DeterministicExecutionVenue::RestoreRiskState(
    const std::map<std::string, double>& positions,
    std::uint64_t admittedOrderCount, std::string& reason)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    if (!m_orders.empty())
    {
        reason = "SIM_RISK_RESTORE_AFTER_ADMISSION";
        return false;
    }
    for (const auto& position : positions)
    {
        if (position.first.empty() || !std::isfinite(position.second))
        {
            reason = "SIM_RISK_RESTORE_POSITION_INVALID";
            return false;
        }
    }
    m_positions = positions;
    m_admittedOrderCount = admittedOrderCount;
    ++m_generation;
    reason.clear();
    return true;
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
            if (order.terminal || !order.activated) continue;
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
            const std::uint64_t now = m_clock();
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
            const double currentPosition = m_positions[order.instrument];
            const double afterPosition = currentPosition +
                (order.request.action == "BUY" ?
                    order.request.totalQuantity : -order.request.totalQuantity);
            if (order.flattenCapacityReserved || m_riskConfig.flattenOnly)
            {
                // Recheck actual fill-time capacity after activation/policy
                // changes or another fill. The current flatten policy also
                // constrains ordinary orders admitted before it was enabled.
                const bool reducesWithoutCrossing =
                    std::isfinite(currentPosition) && std::isfinite(afterPosition) &&
                    ((order.request.action == "SELL" && currentPosition > 0.0 &&
                      afterPosition >= 0.0 && afterPosition < currentPosition) ||
                     (order.request.action == "BUY" && currentPosition < 0.0 &&
                      afterPosition <= 0.0 && afterPosition > currentPosition));
                if (!reducesWithoutCrossing)
                {
                    order.terminal = true;
                    order.terminalStatus = "Rejected";
                    ++m_generation;
                    SimulatedOrderEvent rejected;
                    rejected.orderId = order.id;
                    rejected.instrument = order.instrument;
                    rejected.side = order.request.action;
                    rejected.status = "Rejected";
                    rejected.remainingQuantity = order.request.totalQuantity;
                    events.push_back(rejected);
                    continue;
                }
            }
            order.terminal = true;
            order.terminalStatus = "Filled";
            ++m_generation;
            const double fillPrice = order.request.action == "BUY" ? quote.ask : quote.bid;
            m_positions[order.instrument] = afterPosition;
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
