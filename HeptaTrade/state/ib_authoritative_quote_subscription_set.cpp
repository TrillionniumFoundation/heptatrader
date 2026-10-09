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
    bool preserveActiveOnNextCycle, bool independentQuotes)
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
    m_independentQuotes = independentQuotes;
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
        // A preserved active ticker does not preserve a half-built renewal
        // or a ticker whose handoff completed but cancellation is pending.
        for (const auto& pending : m_pendingRequestIdByInstrument)
        {
            const auto state = previousQuotes.find(pending.second);
            if (state != previousQuotes.end() && state->second.dispatchAccepted &&
                !state->second.nativeRequestRejected)
                plan.cancelRequestIds.push_back(pending.second);
        }
        for (const auto& retiring : m_retiringRequestIdByInstrument)
            plan.cancelRequestIds.push_back(retiring.second);
        for (std::map<std::string, int>::const_iterator it = previousRequests.begin();
             it != previousRequests.end(); ++it)
        {
            const std::map<std::string, IBContractLite>::const_iterator desired =
                m_desiredContracts.find(it->first);
            const std::map<int, QuoteState>::const_iterator previous = previousQuotes.find(it->second);
            if (desired == m_desiredContracts.end() || previous == previousQuotes.end() ||
                !SameContract(desired->second, previous->second.contract))
            {
                if (previous != previousQuotes.end() && previous->second.dispatchAccepted &&
                    !previous->second.nativeRequestRejected)
                    plan.cancelRequestIds.push_back(it->second);
            }
        }
    }
    m_quotesByRequestId.clear();
    m_requestIdByInstrument.clear();
    m_pendingRequestIdByInstrument.clear();
    m_retiringRequestIdByInstrument.clear();
    if (!preserveActive) m_refreshRetryNotBeforeMs.clear();
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
        state.requestStartedAtMs = observedAtMs;
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

IBAuthoritativeQuoteSubscriptionPlan
IBAuthoritativeQuoteSubscriptionSet::BeginStaleSecondaryCashRefresh(
    const std::string& instrument, std::uint64_t connectionEpoch,
    std::uint64_t generation, std::uint64_t observedAtMs,
    std::uint64_t quoteMaxAgeMs, std::uint64_t minRetryMs,
    std::uint64_t freshQuoteMaxAgeMs)
{
    IBAuthoritativeQuoteSubscriptionPlan plan;
    plan.connectionEpoch = connectionEpoch;
    plan.generation = generation;
    std::lock_guard<std::mutex> lock(m_mutex);
    // No new exchange subscription can turn an old or missing quote into a
    // tradeable price. Only fresh BID and ASK callbacks can do that.
    if (!m_independentQuotes || !connectionEpoch || !generation ||
        connectionEpoch != m_connectionEpoch || generation != m_generation ||
        instrument == m_primaryInstrument || observedAtMs == 0 ||
        quoteMaxAgeMs == 0 || minRetryMs == 0 || freshQuoteMaxAgeMs == 0)
    {
        plan.reasonCode = "QUOTE_REFRESH_NOT_AUTHORIZED";
        return plan;
    }
    const auto desired = m_desiredContracts.find(instrument);
    const auto request = m_requestIdByInstrument.find(instrument);
    if (desired == m_desiredContracts.end() || desired->second.secType != "CASH" ||
        request == m_requestIdByInstrument.end())
    {
        plan.reasonCode = "QUOTE_REFRESH_NOT_CASH_SECONDARY";
        return plan;
    }
    const auto retiring = m_retiringRequestIdByInstrument.find(instrument);
    if (retiring != m_retiringRequestIdByInstrument.end())
    {
        // Promotion requires real new BID and ASK before this cancellation.
        // Until its exact SDK outcome is recorded, never start another send.
        plan.cancelRequestIds.push_back(retiring->second);
        plan.accepted = true;
        plan.reasonCode = "QUOTE_REFRESH_RETIRE_PREVIOUS";
        return plan;
    }
    const auto pending = m_pendingRequestIdByInstrument.find(instrument);
    if (pending != m_pendingRequestIdByInstrument.end())
    {
        const auto state = m_quotesByRequestId.find(pending->second);
        if (state == m_quotesByRequestId.end() ||
            !state->second.dispatchResultRecorded || state->second.dispatchAccepted)
        {
            // A sent request is a long-lived stream, not a 3s snapshot.
            // Do not cancel/reissue it merely because its first pair is slow.
            plan.reasonCode = "QUOTE_REFRESH_PENDING_NATIVE_PAIR";
            return plan;
        }
        if (observedAtMs < state->second.requestStartedAtMs ||
            observedAtMs - state->second.requestStartedAtMs < minRetryMs)
        {
            plan.reasonCode = "QUOTE_REFRESH_NOT_DUE";
            return plan;
        }
        // Only a recorded, definite pre-SDK refusal may be retried. Its ID
        // was never sent and must not be cancelled or accept callbacks.
        m_quotesByRequestId.erase(state);
        m_pendingRequestIdByInstrument.erase(pending);
    }
    const auto retry = m_refreshRetryNotBeforeMs.find(instrument);
    if (retry != m_refreshRetryNotBeforeMs.end() && observedAtMs < retry->second)
    {
        plan.reasonCode = "QUOTE_REFRESH_BROKER_REJECTION_BACKOFF";
        return plan;
    }
    const auto found = m_quotesByRequestId.find(request->second);
    if (found == m_quotesByRequestId.end() ||
        !found->second.dispatchResultRecorded)
    {
        // A first-cycle optional secondary CASH request is retryable only
        // AFTER RecordDispatchResult(false) certifies it never crossed the
        // SDK send boundary. A pending/unknown request must not be replaced.
        plan.reasonCode = "QUOTE_REFRESH_DISPATCH_OUTCOME_UNAVAILABLE";
        return plan;
    }
    const QuoteState previous = found->second;
    const auto lastCompositeAt = previous.quote.CompositeObservedAtMs();
    if (observedAtMs < previous.requestStartedAtMs ||
        observedAtMs - previous.requestStartedAtMs < minRetryMs ||
        (lastCompositeAt != 0 && observedAtMs >= lastCompositeAt &&
         observedAtMs - lastCompositeAt <= quoteMaxAgeMs))
    {
        plan.reasonCode = "QUOTE_REFRESH_NOT_DUE";
        return plan;
    }
    if (m_nextRequestId > std::numeric_limits<int>::max())
    {
        plan.reasonCode = "QUOTE_REQUEST_ID_EXHAUSTED";
        return plan;
    }
    const int newRequestId = static_cast<int>(m_nextRequestId++);
    QuoteState renewed;
    renewed.instrument = instrument;
    renewed.contract = previous.contract;
    renewed.requestStartedAtMs = observedAtMs;
    renewed.freshQuoteMaxAgeMs = freshQuoteMaxAgeMs;
    m_quotesByRequestId[newRequestId] = renewed;
    m_pendingRequestIdByInstrument[instrument] = newRequestId;
    IBAuthoritativeQuoteSubscription toSend;
    toSend.requestId = newRequestId;
    toSend.instrument = instrument;
    toSend.contract = renewed.contract;
    plan.subscriptions.push_back(toSend);
    plan.accepted = true;
    return plan;
}

bool IBAuthoritativeQuoteSubscriptionSet::RecordDispatchResult(
    std::uint64_t generation,
    int requestId,
    bool accepted)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    if (generation == 0 || generation != m_generation) return false;
    std::map<int, QuoteState>::iterator found = m_quotesByRequestId.find(requestId);
    if (found == m_quotesByRequestId.end() ||
        found->second.dispatchResultRecorded) return false;
    found->second.dispatchAccepted = accepted;
    found->second.dispatchResultRecorded = true;
    return true;
}

bool IBAuthoritativeQuoteSubscriptionSet::RecordRetirementResult(
    std::uint64_t generation, int requestId)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    if (!generation || generation != m_generation) return false;
    for (auto retiring = m_retiringRequestIdByInstrument.begin();
         retiring != m_retiringRequestIdByInstrument.end(); ++retiring)
    {
        if (retiring->second != requestId) continue;
        m_quotesByRequestId.erase(requestId);
        m_retiringRequestIdByInstrument.erase(retiring);
        return true;
    }
    return false;
}

bool IBAuthoritativeQuoteSubscriptionSet::RejectSecondaryCashRefreshRequest(
    const IBEvent& event, std::uint64_t observedAtMs,
    std::uint64_t retryBackoffMs)
{
    if (event.type != IBEventType::Error || event.key != "354" ||
        event.id <= 0 || event.id > std::numeric_limits<int>::max() ||
        !event.account.empty() || !event.order.orderRef.empty() ||
        !event.advancedOrderRejectJson.empty() ||
        observedAtMs == 0 || retryBackoffMs < 15000)
        return false;
    std::lock_guard<std::mutex> lock(m_mutex);
    if (!m_generation || event.connectionEpoch != m_connectionEpoch)
        return false;
    const int id = static_cast<int>(event.id);
    const auto state = m_quotesByRequestId.find(id);
    if (state == m_quotesByRequestId.end() || !state->second.dispatchResultRecorded ||
        !state->second.dispatchAccepted || state->second.contract.secType != "CASH" ||
        !SameContract(event.contract, state->second.contract) ||
        state->second.nativeRequestRejected || state->second.hadCompletePair ||
        state->second.instrument == m_primaryInstrument ||
        observedAtMs < state->second.requestStartedAtMs)
        return false;
    const auto pending = m_pendingRequestIdByInstrument.find(state->second.instrument);
    const bool pendingRequest = pending != m_pendingRequestIdByInstrument.end() && pending->second == id;
    const auto active = m_requestIdByInstrument.find(state->second.instrument);
    const bool initialUnpairedRequest = active != m_requestIdByInstrument.end() && active->second == id;
    if (!pendingRequest && !initialUnpairedRequest)
        return false;
    const std::uint64_t maxTime = std::numeric_limits<std::uint64_t>::max();
    m_refreshRetryNotBeforeMs[state->second.instrument] =
        retryBackoffMs > maxTime - observedAtMs ? maxTime : observedAtMs + retryBackoffMs;
    // Broker rejected this sent renewal. No subscription was established;
    // never send cancelMktData for it or borrow its callbacks for active.
    if (pendingRequest)
    {
        m_quotesByRequestId.erase(state);
        m_pendingRequestIdByInstrument.erase(pending);
    }
    else
    {
        state->second.nativeRequestRejected = true;
        state->second.quote = IBAuthoritativeQuoteSnapshot();
    }
    return true;
}

std::vector<int> IBAuthoritativeQuoteSubscriptionSet::AbortCycle(std::uint64_t generation)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    if (generation == 0 || generation != m_generation) return std::vector<int>();
    const std::vector<int> requestIds = ActiveRequestIdsLocked();
    m_quotesByRequestId.clear();
    m_requestIdByInstrument.clear();
    m_pendingRequestIdByInstrument.clear();
    m_retiringRequestIdByInstrument.clear();
    m_refreshRetryNotBeforeMs.clear();
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
    if (!state.dispatchAccepted || state.nativeRequestRejected) return result;
    const auto active = m_requestIdByInstrument.find(state.instrument);
    if (active == m_requestIdByInstrument.end()) return result;
    const auto pending = m_pendingRequestIdByInstrument.find(state.instrument);
    const bool pendingTick = pending != m_pendingRequestIdByInstrument.end() &&
        pending->second == static_cast<int>(event.id);
    if (!pendingTick && active->second != static_cast<int>(event.id))
        return result; // Retired callbacks cannot update either new leg.
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
    if (state.quote.HasQuote() && state.quote.bid > 0.0 && state.quote.ask > 0.0)
        state.hadCompletePair = true;

    if (pendingTick)
    {
        const auto compositeAt = state.quote.CompositeObservedAtMs();
        if (!state.quote.HasQuote() || state.quote.bid <= 0.0 ||
            state.quote.ask <= 0.0 || !compositeAt ||
            compositeAt > observedAtMs ||
            observedAtMs - compositeAt > state.freshQuoteMaxAgeMs)
            return result;
        const int previousId = active->second;
        const auto previous = m_quotesByRequestId.find(previousId);
        if (previous != m_quotesByRequestId.end() && previous->second.dispatchAccepted &&
            !previous->second.nativeRequestRejected)
            m_retiringRequestIdByInstrument[state.instrument] = previousId;
        else
            m_quotesByRequestId.erase(previousId);
        active->second = static_cast<int>(event.id);
        m_pendingRequestIdByInstrument.erase(pending);
    }

    if (m_independentQuotes)
    {
        // Market closures are instrument-local. Never publish a negative/missing
        // FX quote or borrow its timestamp to authorize a BTC order.
        const bool wasComplete = m_complete;
        m_complete = AllQuotesReadyLocked();
        result.cycleComplete = m_complete;
        result.completedNow = !wasComplete && m_complete;
        if (!state.dispatchAccepted || !state.quote.HasQuote() ||
            state.quote.bid <= 0.0 || state.quote.ask <= 0.0) return result;
        const std::uint64_t observed = state.quote.LivenessObservedAtMs();
        if (observed == 0) return result;
        result.write = m_store.UpsertQuote(MaterializeQuote(state), observed, "ib.instrument_tick");
        if (!result.write.accepted)
        {
            result.status = IBAuthoritativeQuoteConsumeStatus::Rejected;
            result.reasonCode = result.write.reasonCode;
        }
        return result;
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
    health.independentQuotes = m_independentQuotes;
    health.primaryInstrument = m_primaryInstrument;
    health.dispatchedRequestIds = ActiveRequestIdsLocked();
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
        contractHealth.dispatchAccepted = state->second.dispatchAccepted &&
            !state->second.nativeRequestRejected;
        contractHealth.nativeRequestRejected = state->second.nativeRequestRejected;
        contractHealth.quote = state->second.quote;
        const auto retry = m_refreshRetryNotBeforeMs.find(desired->first);
        if (retry != m_refreshRetryNotBeforeMs.end())
            contractHealth.refreshRetryNotBeforeMs = retry->second;
        const auto pending = m_pendingRequestIdByInstrument.find(desired->first);
        if (pending != m_pendingRequestIdByInstrument.end())
        {
            contractHealth.pendingRequestId = pending->second;
            const auto pendingState = m_quotesByRequestId.find(pending->second);
            contractHealth.pendingDispatchAccepted = pendingState != m_quotesByRequestId.end() &&
                pendingState->second.dispatchAccepted;
        }
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
    if (m_requestIdByInstrument.size() != m_desiredContracts.size()) return false;
    for (const auto& active : m_requestIdByInstrument)
    {
        const auto it = m_quotesByRequestId.find(active.second);
        if (it == m_quotesByRequestId.end() || !it->second.dispatchAccepted ||
            it->second.nativeRequestRejected ||
            !it->second.quote.HasQuote()) return false;
        if (m_independentQuotes && (it->second.quote.bid <= 0.0 || it->second.quote.ask <= 0.0)) return false;
    }
    return true;
}

std::uint64_t
IBAuthoritativeQuoteSubscriptionSet::OldestLivenessObservationLocked() const
{
    std::uint64_t oldest = 0;
    for (const auto& active : m_requestIdByInstrument)
    {
        const auto it = m_quotesByRequestId.find(active.second);
        if (it == m_quotesByRequestId.end()) return 0;
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
        if (it->second.dispatchAccepted && !it->second.nativeRequestRejected)
            requestIds.push_back(it->first);
    return requestIds;
}
