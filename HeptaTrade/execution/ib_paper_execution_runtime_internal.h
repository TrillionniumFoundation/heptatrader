#pragma once

#include "ib_paper_execution_runtime_composition.h"

#include "execution_coordinator.h"
#include "execution_decision_lease_authority.h"
#include "execution_event_feed_server.h"
#include "ib_paper_execution_hook_authority.h"
#include "ib_paper_execution_profile.h"
#include "ib_paper_kill_switch.h"
#include "unix_execution_service_server.h"

#include <cstddef>
#include <cstdint>
#include <limits>
#include <string>

namespace ib_paper_execution_runtime_internal
{
extern const std::size_t kMaxPendingAdapterEvents;
// A positive CASH-farm 2104 callback is followed by a bounded quiet lease
// before formal quote admission.  The same bound is used by post-dispatch
// settlement; keeping it in one internal constant makes startup/reconnect
// admission symmetric and testable.
static const int kMarketDataAdmissionStabilityWindowMs = 250;
extern const char* const kFxCashRestartCheckpointFile;
extern const char* const kPaperTerminalLatchFile;

std::string EscapeJson(const std::string& value);
bool ParsePositiveUnsigned(
    const std::string& value, std::uint64_t& parsed);
bool HasPositiveEconomicFillEvidence(const IBEvent& event);
bool IsHistoricalSyntheticExecutionStatus(const IBEvent& event);
bool IsEconomicallyTerminalOrderStatus(const IBEvent& event);
bool IsPersistedBrokerCallback(IBEventType type);
bool ParseBrokerErrorCode(const std::string& value, int& code);
inline bool IsCurrentBrokerEpoch(
    const HeptaIBGatewayAdapter* adapter, std::uint64_t eventEpoch)
{
    const std::uint64_t currentEpoch = adapter ?
        adapter->GetConnectionEpoch() : 0;
    return currentEpoch != 0 && eventEpoch != 0 &&
        eventEpoch == currentEpoch;
}
inline bool IsActiveAuthoritativeQuoteRequest(
    const IBAuthoritativeQuoteSubscriptionSet* subscriptions,
    long callbackId)
{
    if (!subscriptions || callbackId <= 0 ||
        callbackId > static_cast<long>(std::numeric_limits<int>::max()))
        return false;
    const IBAuthoritativeQuoteSubscriptionHealth health =
        subscriptions->GetHealth();
    const int requestId = static_cast<int>(callbackId);
    for (std::map<std::string, IBAuthoritativeQuoteContractHealth>::const_iterator
             it = health.contracts.begin(); it != health.contracts.end(); ++it)
        if (it->second.active && it->second.dispatchAccepted &&
            it->second.requestId == requestId)
            return true;
    return false;
}
inline bool HasActiveAuthoritativeQuoteCycle(
    const IBAuthoritativeQuoteSubscriptionSet* subscriptions)
{
    if (!subscriptions) return false;
    const IBAuthoritativeQuoteSubscriptionHealth health =
        subscriptions->GetHealth();
    for (std::map<std::string, IBAuthoritativeQuoteContractHealth>::const_iterator
             it = health.contracts.begin(); it != health.contracts.end(); ++it)
        if (it->second.active) return true;
    return false;
}
inline bool IsCurrentEpochMarketData10197(
    const IBEvent& event, const HeptaIBGatewayAdapter* adapter)
{
    int errorCode = 0;
    return event.type == IBEventType::Error &&
        ParseBrokerErrorCode(event.key, errorCode) && errorCode == 10197 &&
        IsCurrentBrokerEpoch(adapter, event.connectionEpoch);
}
inline bool IsMarketData10197(
    const IBEvent& event,
    const IBAuthoritativeQuoteSubscriptionSet* subscriptions,
    const HeptaIBGatewayAdapter* adapter)
{
    if (!IsCurrentEpochMarketData10197(event, adapter)) return false;
    // IB uses id=-1/0 for account/session-wide errors and can deliver the
    // callback synchronously from reqMktData(), before RecordDispatchResult
    // has committed the ticker id.  The callback id is therefore only an
    // advisory correlation; once any authoritative quote leg is active, a
    // current-epoch 10197 must fail closed even when the id is absent,
    // mismatched, or the dispatch acknowledgement is not recorded yet.
    return HasActiveAuthoritativeQuoteCycle(subscriptions);
}
inline bool RequiresCashMarketDataFarm(
    const std::map<std::string, InstrumentRef>& quoteContracts)
{
    for (std::map<std::string, InstrumentRef>::const_iterator it =
             quoteContracts.begin(); it != quoteContracts.end(); ++it)
        if (it->second.secType == "CASH") return true;
    return false;
}

// CASH market-data admission is driven directly by the current epoch's
// positive farm witness in the runtime and wrapper; no timer or alternate
// readiness path exists.
std::string StatusReasonCode(const std::string& status);
std::string NormalizeExecutionSide(const std::string& side);
std::uint64_t NowEpochMs();
std::string Sha256Text(const std::string& value);
bool ReadSmallPrivateFile(
    const std::string& path, std::string& contents, std::string& reason);
bool ValidateOrCreatePrivateFile(
    const std::string& path, std::string& reason);
}
