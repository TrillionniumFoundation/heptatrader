#include "ib_authoritative_quote_subscription_set.h"

#include "ib_contract_identity.h"

#include <cmath>
#include <cstdlib>
#include <limits>

IBAuthoritativeQuoteSubscriptionSet::IBAuthoritativeQuoteSubscriptionSet(
    AuthoritativeTradingSnapshotStore& store,
    int firstRequestId)
    : m_store(store),
      m_nextRequestId(firstRequestId > 0 ? firstRequestId : 1)
{
}

bool IBAuthoritativeQuoteSubscriptionSet::Configure(
    const std::map<std::string, IBContractLite>& contracts,
    const std::string& primaryInstrument,
    std::string& reason,
    bool preserveActiveOnNextCycle)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    if (contracts.empty())
    {
        reason = "QUOTE_CONTRACTS_REQUIRED";
        return false;
    }
    if (primaryInstrument.empty() || contracts.find(primaryInstrument) == contracts.end())
    {
        reason = "PRIMARY_QUOTE_CONTRACT_REQUIRED";
        return false;
    }
    for (std::map<std::string, IBContractLite>::const_iterator it = contracts.begin();
         it != contracts.end(); ++it)
    {
        if (it->first.empty() ||
            BuildIBAuthoritativeInstrumentIdentity(it->second, it->first) != it->first)
        {
            reason = "QUOTE_CONTRACT_IDENTITY_MISMATCH";
            return false;
        }
    }
    m_desiredContracts = contracts;
    m_primaryInstrument = primaryInstrument;
    ++m_desiredRevision;
    m_preserveActiveOnNextCycle = preserveActiveOnNextCycle && m_generation != 0;
    reason.clear();
    return true;
}

IBAuthoritativeQuoteSubscriptionPlan IBAuthoritativeQuoteSubscriptionSet::BeginCycle(
    std::uint64_t connectionEpoch,
    std::uint64_t generation,
    std::uint64_t observedAtMs)
{
    IBAuthoritativeQuoteSubscriptionPlan plan;
    plan.connectionEpoch = connectionEpoch;
    plan.generation = generation;
    if (connectionEpoch == 0 || generation == 0 || observedAtMs == 0)
    {
        plan.reasonCode = "QUOTE_CYCLE_ID_REQUIRED";
        return plan;
    }

    std::lock_guard<std::mutex> lock(m_mutex);
    if (m_desiredContracts.empty() || m_primaryInstrument.empty())
    {
        plan.reasonCode = "QUOTE_CONTRACTS_NOT_CONFIGURED";
        return plan;
    }
    if (m_nextRequestId <= 0 || m_nextRequestId > std::numeric_limits<int>::max())
    {
        plan.reasonCode = "QUOTE_REQUEST_ID_EXHAUSTED";
        return plan;
    }
    const std::uint64_t availableIds = static_cast<std::uint64_t>(
        static_cast<std::int64_t>(std::numeric_limits<int>::max()) - m_nextRequestId) + 1;
    if (m_desiredContracts.size() > availableIds)
    {
        plan.reasonCode = "QUOTE_REQUEST_ID_EXHAUSTED";
        return plan;
    }
    const AuthoritativeSnapshotWriteResult invalidated = m_store.InvalidateQuotes(
        observedAtMs, "ib.quote_cycle_begin");
    if (!invalidated.accepted)
    {
        plan.reasonCode = invalidated.reasonCode;
        return plan;
    }

    const bool preserveActive = m_preserveActiveOnNextCycle &&
        m_connectionEpoch == connectionEpoch && m_generation != 0;
    m_preserveActiveOnNextCycle = false;
    const std::map<int, QuoteState> previousQuotes = m_quotesByRequestId;
    const std::map<std::string, int> previousRequests = m_requestIdByInstrument;
    if (!preserveActive) plan.cancelRequestIds = ActiveRequestIdsLocked();
    else
    {
        for (std::map<std::string, int>::const_iterator it = previousRequests.begin();
             it != previousRequests.end(); ++it)
        {
            const std::map<std::string, IBContractLite>::const_iterator desired =
                m_desiredContracts.find(it->first);
            const std::map<int, QuoteState>::const_iterator previous = previousQuotes.find(it->second);
            if (desired == m_desiredContracts.end() || previous == previousQuotes.end() ||
                !SameContract(desired->second, previous->second.contract))
                plan.cancelRequestIds.push_back(it->second);
        }
    }
    m_quotesByRequestId.clear();
    m_requestIdByInstrument.clear();
    m_connectionEpoch = connectionEpoch;
    m_generation = generation;
    m_complete = false;
    for (std::map<std::string, IBContractLite>::const_iterator it = m_desiredContracts.begin();
         it != m_desiredContracts.end(); ++it)
    {
        if (preserveActive)
        {
            const std::map<std::string, int>::const_iterator previousRequest =
                previousRequests.find(it->first);
            if (previousRequest != previousRequests.end())
            {
                const std::map<int, QuoteState>::const_iterator previous =
                    previousQuotes.find(previousRequest->second);
                if (previous != previousQuotes.end() &&
                    SameContract(it->second, previous->second.contract))
                {
                    m_quotesByRequestId[previousRequest->second] = previous->second;
                    m_requestIdByInstrument[it->first] = previousRequest->second;
                    continue;
                }
            }
        }
        const int requestId = static_cast<int>(m_nextRequestId++);
        QuoteState state;
        state.instrument = it->first;
        state.contract = it->second;
        m_quotesByRequestId[requestId] = state;
        m_requestIdByInstrument[it->first] = requestId;
        IBAuthoritativeQuoteSubscription subscription;
        subscription.requestId = requestId;
        subscription.instrument = it->first;
        subscription.contract = it->second;
        plan.subscriptions.push_back(subscription);
    }
    plan.accepted = true;
    return plan;
}

bool IBAuthoritativeQuoteSubscriptionSet::SameContract(
    const IBContractLite& left,
    const IBContractLite& right)
{
    return left.symbol == right.symbol && left.secType == right.secType &&
        left.exchange == right.exchange && left.primaryExchange == right.primaryExchange &&
        left.currency == right.currency &&
        left.lastTradeDateOrContractMonth == right.lastTradeDateOrContractMonth &&
        left.right == right.right && left.strike == right.strike &&
        left.multiplier == right.multiplier && left.tradingClass == right.tradingClass &&
        left.localSymbol == right.localSymbol;
}

bool IBAuthoritativeQuoteSubscriptionSet::RecordDispatchResult(
    std::uint64_t generation,
    int requestId,
    bool accepted)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    if (generation == 0 || generation != m_generation) return false;
    std::map<int, QuoteState>::iterator found = m_quotesByRequestId.find(requestId);
    if (found == m_quotesByRequestId.end()) return false;
    found->second.dispatchAccepted = accepted;
    return true;
}

std::vector<int> IBAuthoritativeQuoteSubscriptionSet::AbortCycle(std::uint64_t generation)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    if (generation == 0 || generation != m_generation) return std::vector<int>();
    const std::vector<int> requestIds = ActiveRequestIdsLocked();
    m_quotesByRequestId.clear();
    m_requestIdByInstrument.clear();
    m_connectionEpoch = 0;
    m_generation = 0;
    m_complete = false;
    return requestIds;
}

IBAuthoritativeQuoteConsumeResult IBAuthoritativeQuoteSubscriptionSet::ConsumeTick(
    const IBEvent& event,
    std::uint64_t observedAtMs)
{
    IBAuthoritativeQuoteConsumeResult result;
    if (event.type != IBEventType::TickPrice || observedAtMs == 0 ||
        !std::isfinite(event.number)) return result;

    bool bid = false;
    bool ask = false;
    bool last = false;
    if (!RecognizedTickField(event.key, bid, ask, last)) return result;

    std::lock_guard<std::mutex> lock(m_mutex);
    if (m_generation == 0 || event.connectionEpoch != m_connectionEpoch) return result;
    std::map<int, QuoteState>::iterator found = m_quotesByRequestId.find(
        static_cast<int>(event.id));
    if (found == m_quotesByRequestId.end()) return result;

    QuoteState& state = found->second;
    result.status = IBAuthoritativeQuoteConsumeStatus::Applied;
    result.instrument = state.instrument;
    result.generation = m_generation;
    result.primary = state.instrument == m_primaryInstrument;
    if (bid)
    {
        state.quote.bid = event.number;
        state.quote.hasBid = true;
        state.quote.bidObservedAtMs = observedAtMs;
    }
    if (ask)
    {
        state.quote.ask = event.number;
        state.quote.hasAsk = true;
        state.quote.askObservedAtMs = observedAtMs;
    }
    if (last)
    {
        state.quote.last = event.number;
        state.quote.hasLast = true;
        state.quote.lastObservedAtMs = observedAtMs;
    }

    if (!m_complete)
    {
        if (!AllQuotesReadyLocked()) return result;
        const std::uint64_t livenessObservedAtMs =
            OldestLivenessObservationLocked();
        if (livenessObservedAtMs == 0)
        {
            result.status = IBAuthoritativeQuoteConsumeStatus::Rejected;
            result.reasonCode = "QUOTE_COMPOSITE_OBSERVATION_REQUIRED";
            return result;
        }
        result.write = m_store.ReplaceQuotes(
            MaterializeAllLocked(), livenessObservedAtMs,
            "ib.market_data_snapshot");
        if (!result.write.accepted)
        {
            result.status = IBAuthoritativeQuoteConsumeStatus::Rejected;
            result.reasonCode = result.write.reasonCode;
            return result;
        }
        m_complete = true;
        result.completedNow = true;
        result.cycleComplete = true;
        return result;
    }

    result.cycleComplete = true;
    if (!state.quote.HasQuote()) return result;
    const std::uint64_t livenessObservedAtMs =
        state.quote.LivenessObservedAtMs();
    if (livenessObservedAtMs == 0)
    {
        result.status = IBAuthoritativeQuoteConsumeStatus::Rejected;
        result.reasonCode = "QUOTE_COMPOSITE_OBSERVATION_REQUIRED";
        return result;
    }
    result.write = m_store.UpsertQuote(
        MaterializeQuote(state), livenessObservedAtMs, "ib.tick_price");
    if (!result.write.accepted)
    {
        result.status = IBAuthoritativeQuoteConsumeStatus::Rejected;
        result.reasonCode = result.write.reasonCode;
    }
    return result;
}

IBAuthoritativeQuoteSnapshot IBAuthoritativeQuoteSubscriptionSet::GetQuote(
    const std::string& instrument) const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::map<std::string, int>::const_iterator request =
        m_requestIdByInstrument.find(instrument);
    if (request == m_requestIdByInstrument.end()) return IBAuthoritativeQuoteSnapshot();
    const std::map<int, QuoteState>::const_iterator quote =
        m_quotesByRequestId.find(request->second);
    if (quote == m_quotesByRequestId.end()) return IBAuthoritativeQuoteSnapshot();
    return quote->second.quote;
}

IBAuthoritativeQuoteSnapshot IBAuthoritativeQuoteSubscriptionSet::GetPrimaryQuote() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::map<std::string, int>::const_iterator request =
        m_requestIdByInstrument.find(m_primaryInstrument);
    if (request == m_requestIdByInstrument.end()) return IBAuthoritativeQuoteSnapshot();
    const std::map<int, QuoteState>::const_iterator quote =
        m_quotesByRequestId.find(request->second);
    if (quote == m_quotesByRequestId.end()) return IBAuthoritativeQuoteSnapshot();
    return quote->second.quote;
}

std::string IBAuthoritativeQuoteSubscriptionSet::PrimaryInstrument() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_primaryInstrument;
}

bool IBAuthoritativeQuoteSubscriptionSet::IsComplete() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_complete;
}

std::uint64_t IBAuthoritativeQuoteSubscriptionSet::CurrentGeneration() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_generation;
}

std::size_t IBAuthoritativeQuoteSubscriptionSet::DesiredCount() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_desiredContracts.size();
}

IBAuthoritativeQuoteSubscriptionHealth IBAuthoritativeQuoteSubscriptionSet::GetHealth() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    IBAuthoritativeQuoteSubscriptionHealth health;
    health.desiredRevision = m_desiredRevision;
    health.connectionEpoch = m_connectionEpoch;
    health.generation = m_generation;
    health.complete = m_complete;
    health.primaryInstrument = m_primaryInstrument;
    for (std::map<std::string, IBContractLite>::const_iterator desired = m_desiredContracts.begin();
         desired != m_desiredContracts.end(); ++desired)
    {
        IBAuthoritativeQuoteContractHealth& contractHealth = health.contracts[desired->first];
        contractHealth.contract = desired->second;
        const std::map<std::string, int>::const_iterator request =
            m_requestIdByInstrument.find(desired->first);
        if (request == m_requestIdByInstrument.end()) continue;
        const std::map<int, QuoteState>::const_iterator state = m_quotesByRequestId.find(request->second);
        if (state == m_quotesByRequestId.end()) continue;
        contractHealth.active = true;
        contractHealth.requestId = request->second;
        contractHealth.dispatchAccepted = state->second.dispatchAccepted;
        contractHealth.quote = state->second.quote;
    }
    return health;
}

void IBAuthoritativeQuoteSubscriptionSet::ForceFullNextCycle()
{
    std::lock_guard<std::mutex> lock(m_mutex);
    m_preserveActiveOnNextCycle = false;
}

bool IBAuthoritativeQuoteSubscriptionSet::RecognizedTickField(
    const std::string& field,
    bool& bid,
    bool& ask,
    bool& last)
{
    char* end = nullptr;
    const long value = std::strtol(field.c_str(), &end, 10);
    if (end == field.c_str() || *end != '\0') return false;
    // This authority requests real-time market data. Delayed tick fields
    // (66/67/68) must never be re-stamped with local arrival time and treated
    // as current PAPER authorization data.
    bid = value == 1;
    ask = value == 2;
    last = value == 4;
    return bid || ask || last;
}

AuthoritativeQuote IBAuthoritativeQuoteSubscriptionSet::MaterializeQuote(
    const QuoteState& state)
{
    AuthoritativeQuote quote;
    quote.instrument = state.instrument;
    quote.bid = state.quote.bid;
    quote.ask = state.quote.ask;
    quote.last = state.quote.hasLast ? state.quote.last :
        ((state.quote.bid + state.quote.ask) * 0.5);
    return quote;
}

bool IBAuthoritativeQuoteSubscriptionSet::AllQuotesReadyLocked() const
{
    if (m_quotesByRequestId.size() != m_desiredContracts.size()) return false;
    for (std::map<int, QuoteState>::const_iterator it = m_quotesByRequestId.begin();
         it != m_quotesByRequestId.end(); ++it)
    {
        if (!it->second.dispatchAccepted || !it->second.quote.HasQuote()) return false;
    }
    return true;
}

std::uint64_t
IBAuthoritativeQuoteSubscriptionSet::OldestLivenessObservationLocked() const
{
    std::uint64_t oldest = 0;
    for (std::map<int, QuoteState>::const_iterator it =
             m_quotesByRequestId.begin();
         it != m_quotesByRequestId.end(); ++it)
    {
        const std::uint64_t observed =
            it->second.quote.LivenessObservedAtMs();
        if (observed == 0) return 0;
        if (oldest == 0 || observed < oldest) oldest = observed;
    }
    return oldest;
}

std::vector<AuthoritativeQuote>
IBAuthoritativeQuoteSubscriptionSet::MaterializeAllLocked() const
{
    std::vector<AuthoritativeQuote> quotes;
    quotes.reserve(m_requestIdByInstrument.size());
    for (std::map<std::string, int>::const_iterator it = m_requestIdByInstrument.begin();
         it != m_requestIdByInstrument.end(); ++it)
    {
        const std::map<int, QuoteState>::const_iterator state =
            m_quotesByRequestId.find(it->second);
        if (state != m_quotesByRequestId.end()) quotes.push_back(MaterializeQuote(state->second));
    }
    return quotes;
}

std::vector<int> IBAuthoritativeQuoteSubscriptionSet::ActiveRequestIdsLocked() const
{
    std::vector<int> requestIds;
    requestIds.reserve(m_quotesByRequestId.size());
    for (std::map<int, QuoteState>::const_iterator it = m_quotesByRequestId.begin();
         it != m_quotesByRequestId.end(); ++it)
        requestIds.push_back(it->first);
    return requestIds;
}
