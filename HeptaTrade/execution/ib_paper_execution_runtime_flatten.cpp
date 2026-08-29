#include "ib_paper_execution_runtime_composition.h"
#include <chrono>
#include <cmath>
#include <limits>
#include <map>
namespace
{
std::uint64_t CurrentEpochMs()
{
    return static_cast<std::uint64_t>(std::chrono::duration_cast<
        std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count());
}
bool AuthoritativeRiskReady(
    const IBAuthoritativeRiskSnapshot& risk, std::string& reason)
{
    if (risk.accountComplete && risk.positionsComplete && risk.fxCashComplete &&
        risk.accountGeneration != 0 && risk.positionsGeneration != 0 &&
        risk.fxCashGeneration != 0)
        return true;
    reason = risk.reasonCode.empty() ?
        "IB_PAPER_AUTHORITATIVE_RISK_UNREADY" : risk.reasonCode;
    return false;
}
bool RejectFlatten(std::string& reason, const char* code)
{ reason = code; return false; }
}
bool IbPaperExecutionRuntimeComposition::HasPositiveTradableQuote(
    const IBAuthoritativeQuoteSnapshot& quote)
{
    return quote.hasBid && quote.hasAsk && std::isfinite(quote.bid) &&
        std::isfinite(quote.ask) &&
        quote.bid > 0.0 && quote.ask > 0.0 && quote.ask >= quote.bid;
}
bool IbPaperExecutionRuntimeComposition::FreshCompositeQuote(
    const IBAuthoritativeQuoteSnapshot& quote,
    std::uint64_t nowMs,
    std::uint64_t maxAgeMs,
    std::uint64_t& observedAtMs,
    std::uint64_t& staleAfterMs)
{
    observedAtMs = quote.LivenessObservedAtMs();
    if (!HasPositiveTradableQuote(quote) || observedAtMs == 0 ||
        observedAtMs > nowMs)
        return false;
    staleAfterMs = observedAtMs >
        std::numeric_limits<std::uint64_t>::max() - maxAgeMs ?
        std::numeric_limits<std::uint64_t>::max() : observedAtMs + maxAgeMs;
    return nowMs <= staleAfterMs;
}
MarketQuoteSnapshot IbPaperExecutionRuntimeComposition::AuthoritativeQuote(
    const std::string& instrument) const
{
    std::lock_guard<std::recursive_mutex> quoteLock(m_authoritativeQuoteSendMutex);
    MarketQuoteSnapshot result;
    result.instrument = instrument;
    if (instrument.empty() || !m_quoteSubscriptions)
        return result;
    const IBAuthoritativeQuoteSubscriptionHealth health = m_quoteSubscriptions->GetHealth();
    const std::map<std::string, IBAuthoritativeQuoteContractHealth>::
        const_iterator subscription = health.contracts.find(instrument);
    if (!health.complete || health.connectionEpoch == 0 ||
        health.generation == 0 ||
        subscription == health.contracts.end() ||
        !subscription->second.active || !subscription->second.dispatchAccepted ||
        !HasPositiveTradableQuote(subscription->second.quote))
        return result;
    result.subscriptionId = "IB:" + std::to_string(health.connectionEpoch) + ':' +
        std::to_string(health.generation) + ':' +
        std::to_string(subscription->second.requestId);
    result.bid = subscription->second.quote.bid;
    result.ask = subscription->second.quote.ask;
    const std::uint64_t now = CurrentEpochMs();
    std::uint64_t observedAtMs = 0, staleAfterMs = 0;
    if (!FreshCompositeQuote(subscription->second.quote, now,
            m_config.quoteMaxAgeMs, observedAtMs, staleAfterMs))
    {
        result.observedAtMs = observedAtMs;
        result.staleAfterMs = staleAfterMs;
        result.state = MarketSubscriptionState::Stale;
        return result;
    }
    const AuthoritativeQuoteRecord quote = m_authoritativeSnapshots.GetQuote(
        instrument, now, m_config.quoteMaxAgeMs);
    if (quote.value.instrument != instrument ||
        quote.state.availability ==
            AuthoritativeSnapshotAvailability::Missing)
        return result;
    result.observedAtMs = observedAtMs;
    result.staleAfterMs = staleAfterMs;
    result.state = quote.state.availability ==
        AuthoritativeSnapshotAvailability::Fresh ?
        MarketSubscriptionState::Active : MarketSubscriptionState::Stale;
    return result;
}
bool IbPaperExecutionRuntimeComposition::AllowsAuthoritativeFlatten(
    std::string& reason, bool requireSettledQuote) const
{
    if (requireSettledQuote && m_pendingAuthoritativeQuoteEvents != 0)
        return RejectFlatten(reason, "IB_PAPER_FLATTEN_QUOTE_CHANGED_BEFORE_SEND");
    if (HasFatalRuntimeError(&reason))
    {
        if (reason.empty()) reason = "IB_PAPER_RUNTIME_FATAL";
        return false;
    }
    if (!m_lifecycleGate || !m_lifecycleGate->ready.load())
        return RejectFlatten(reason, "IB_PAPER_RUNTIME_NOT_READY");
    if (PostFillRiskRefreshPending())
        return RejectFlatten(reason, "IB_POST_FILL_RISK_REFRESH_PENDING");
    if (!m_adapter)
        return RejectFlatten(reason, "IB_PAPER_AUTHORITATIVE_RISK_UNREADY");
    const IBAuthoritativeRiskSnapshot risk = m_adapter->GetAuthoritativeRiskSnapshot();
    if (!AuthoritativeRiskReady(risk, reason))
        return false;
    if (!m_killSwitch)
        return RejectFlatten(reason, "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN");
    const IbPaperKillSwitchObservation observation =
        m_killSwitch->Observe();
    if (observation.state == IbPaperKillSwitchState::Uncertain)
    {
        reason = observation.reasonCode.empty() ?
            "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN" :
            observation.reasonCode;
        return false;
    }
    reason.clear();
    return true;
}
