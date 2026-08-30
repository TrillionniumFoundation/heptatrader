#include "ib_authoritative_recovery_event_consumer.h"

#include <cerrno>
#include <cstdlib>
#include <limits>

namespace
{
bool ParseBrokerErrorCode(const std::string& value, int& code)
{
    if (value.empty() || (value.size() > 1 && value[0] == '0')) return false;
    for (std::size_t i = 0; i < value.size(); ++i)
        if (value[i] < '0' || value[i] > '9') return false;
    char* end = nullptr;
    errno = 0;
    const unsigned long long parsed = std::strtoull(value.c_str(), &end, 10);
    if (errno != 0 || end == value.c_str() || *end != '\0' ||
        parsed > static_cast<unsigned long long>(std::numeric_limits<int>::max()))
        return false;
    code = static_cast<int>(parsed);
    return true;
}
}

IBAuthoritativeRecoveryEventConsumer::IBAuthoritativeRecoveryEventConsumer(
    IBAuthoritativeRecoveryCoordinator& recovery,
    IBAuthoritativeAccountPositionConsumer& accountPositions,
    IBAuthoritativeOpenOrderConsumer& openOrders,
    IBAuthoritativeQuoteSubscriptionSet& quotes)
    : m_recovery(recovery),
      m_accountPositions(accountPositions),
      m_openOrders(openOrders),
      m_quotes(quotes)
{
}

IBAuthoritativeRecoveryQuoteEventResult IBAuthoritativeRecoveryEventConsumer::ConsumeQuote(
    const IBEvent& event,
    std::uint64_t observedAtMs)
{
    IBAuthoritativeRecoveryQuoteEventResult result;
    result.quote = m_quotes.ConsumeTick(event, observedAtMs);
    if (result.quote.status == IBAuthoritativeQuoteConsumeStatus::Rejected)
    {
        result.recoveryCompletionAttempted = true;
        result.recovery = m_recovery.CompleteQuotes(
            result.quote.generation, false, observedAtMs, result.quote.reasonCode);
    }
    else if (result.quote.completedNow)
    {
        result.recoveryCompletionAttempted = true;
        result.recovery = m_recovery.CompleteQuotes(
            result.quote.generation, true, observedAtMs);
    }
    return result;
}

IBAuthoritativeRecoveryControlAction IBAuthoritativeRecoveryEventConsumer::ClassifyControlEvent(
    const IBEvent& event)
{
    IBAuthoritativeRecoveryControlAction action;
    if (event.type == IBEventType::EventQueueOverflow)
    {
        action.handled = true;
        action.overflow = true;
        action.overflowGeneration = event.overflowGeneration;
        action.recoveryReason = "event_queue_overflow";
        return action;
    }
    if (event.type != IBEventType::Error) return action;
    action.handled = true;
    if (!ParseBrokerErrorCode(event.key, action.errorCode))
    {
        // A malformed broker error must not be interpreted as code zero and
        // silently bypass the reconnect/disconnect recovery boundary.
        action.forceDisconnect = true;
        action.recoveryReason = "ib_error_code_invalid";
        return action;
    }
    if (action.errorCode == 1101 || action.errorCode == 1102)
    {
        action.reconnectEpoch = true;
        action.recoveryReason = action.errorCode == 1101 ? "ib_error_1101" : "ib_error_1102";
    }
    else if (action.errorCode == 504 || action.errorCode == 1100 || action.errorCode == 1300)
    {
        action.forceDisconnect = true;
        action.recoveryReason = action.errorCode == 504 ? "ib_error_504_not_connected" :
            (action.errorCode == 1100 ? "ib_error_1100_connection_lost" : "ib_error_1300_socket_reset");
    }
    return action;
}

IBAuthoritativeRecoveryEventCompletion
IBAuthoritativeRecoveryEventConsumer::ConsumeCompletion(
    const IBEvent& event,
    std::uint64_t observedAtMs)
{
    IBAuthoritativeRecoveryEventCompletion result;
    SnapshotRefreshKind snapshotKind = SnapshotRefreshKind::AccountSummary;
    if (event.type == IBEventType::AccountSummaryEnd)
        result.kind = IBAuthoritativeRecoveryEventCompletionKind::AccountSummary;
    else if (event.type == IBEventType::PositionEnd)
    {
        result.kind = IBAuthoritativeRecoveryEventCompletionKind::Positions;
        snapshotKind = SnapshotRefreshKind::Positions;
    }
    else if (event.type == IBEventType::OpenOrderEnd)
    {
        result.kind = IBAuthoritativeRecoveryEventCompletionKind::OpenOrders;
        snapshotKind = SnapshotRefreshKind::OpenOrders;
    }
    else return result;

    result.handled = true;
    result.generation = m_recovery.CurrentSnapshotGeneration(snapshotKind);
    if (result.generation == 0 || observedAtMs == 0)
    {
        result.reasonCode = result.generation == 0 ?
            "RECOVERY_COMPLETION_WITHOUT_GENERATION" : "RECOVERY_COMPLETION_TIME_REQUIRED";
        return result;
    }
    result.hadActiveGeneration = true;
    result.recoveryWasPending = m_recovery.GetSnapshot().pending;
    if (result.kind == IBAuthoritativeRecoveryEventCompletionKind::AccountSummary)
    {
        result.account = m_accountPositions.CompleteAccount(result.generation, observedAtMs);
        result.snapshotAccepted = result.account.accepted;
        result.reasonCode = result.account.reasonCode;
    }
    else if (result.kind == IBAuthoritativeRecoveryEventCompletionKind::Positions)
    {
        result.positions = m_accountPositions.CompletePositions(result.generation, observedAtMs);
        result.snapshotAccepted = result.positions.accepted;
        result.reasonCode = result.positions.reasonCode;
    }
    else
    {
        result.openOrders = m_openOrders.CompleteRefresh(result.generation, observedAtMs);
        result.snapshotAccepted = result.openOrders.accepted;
        result.reasonCode = result.openOrders.reasonCode;
    }
    result.recovery = m_recovery.CompleteSnapshot(
        snapshotKind, result.generation, result.snapshotAccepted, observedAtMs, result.reasonCode);
    return result;
}
