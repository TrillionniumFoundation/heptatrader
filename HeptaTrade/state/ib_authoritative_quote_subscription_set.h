#pragma once

#include "authoritative_trading_snapshot_store.h"
#include "../adapter_ib/ib_api_wrapper.h"

#include <cstdint>
#include <map>
#include <mutex>
#include <string>
#include <vector>

enum class IBAuthoritativeQuoteConsumeStatus
{
    Applied = 0,
    Ignored,
    Rejected
};

struct IBAuthoritativeQuoteSubscription
{
    int requestId = 0;
    std::string instrument;
    IBContractLite contract;
};

struct IBAuthoritativeQuoteSubscriptionPlan
{
    bool accepted = false;
    std::string reasonCode;
    std::uint64_t connectionEpoch = 0;
    std::uint64_t generation = 0;
    std::vector<int> cancelRequestIds;
    std::vector<IBAuthoritativeQuoteSubscription> subscriptions;
};

struct IBAuthoritativeQuoteSnapshot
{
    double bid = 0.0;
    double ask = 0.0;
    double last = 0.0;
    bool hasBid = false;
    bool hasAsk = false;
    bool hasLast = false;
    std::uint64_t bidObservedAtMs = 0;
    std::uint64_t askObservedAtMs = 0;
    std::uint64_t lastObservedAtMs = 0;

    bool HasQuote() const { return hasBid && hasAsk && ask >= bid; }
    bool HasAny() const { return hasBid || hasAsk || hasLast; }
    std::uint64_t CompositeObservedAtMs() const
    {
        return bidObservedAtMs != 0 && askObservedAtMs != 0 ?
            (bidObservedAtMs < askObservedAtMs ?
                bidObservedAtMs : askObservedAtMs) : 0;
    }
    std::uint64_t LivenessObservedAtMs() const
    {
        return bidObservedAtMs != 0 && askObservedAtMs != 0 ?
            (bidObservedAtMs > askObservedAtMs ?
                bidObservedAtMs : askObservedAtMs) : 0;
    }
};

struct IBAuthoritativeQuoteConsumeResult
{
    IBAuthoritativeQuoteConsumeStatus status = IBAuthoritativeQuoteConsumeStatus::Ignored;
    std::string reasonCode;
    std::string instrument;
    std::uint64_t generation = 0;
    bool primary = false;
    bool cycleComplete = false;
    bool completedNow = false;
    AuthoritativeSnapshotWriteResult write;
};

struct IBAuthoritativeQuoteContractHealth
{
    IBContractLite contract;
    bool active = false;
    int requestId = 0;
    bool dispatchAccepted = false;
    IBAuthoritativeQuoteSnapshot quote;
    int pendingRequestId = 0;
    bool pendingDispatchAccepted = false;
    bool nativeRequestRejected = false;
    std::uint64_t refreshRetryNotBeforeMs = 0;
};

struct IBAuthoritativeQuoteSubscriptionHealth
{
    std::uint64_t desiredRevision = 0;
    std::uint64_t connectionEpoch = 0;
    std::uint64_t generation = 0;
    bool complete = false;
    bool independentQuotes = false;
    bool ReadyForOperations() const
    {
        if (!independentQuotes) return complete;
        if (connectionEpoch == 0 || generation == 0 || contracts.empty() ||
            primaryInstrument.empty()) return false;
        const auto primary = contracts.find(primaryInstrument);
        if (primary == contracts.end()) return false;
        const auto& c = primary->second;
        return c.active && c.dispatchAccepted && c.quote.HasQuote() &&
            c.quote.bid > 0.0 && c.quote.ask > 0.0;
    }
    std::string primaryInstrument;
    std::map<std::string, IBAuthoritativeQuoteContractHealth> contracts;
    // Include an in-flight renewal and a promoted ticker awaiting retirement.
    // Only positively recorded SDK sends may be cancelled.
    std::vector<int> dispatchedRequestIds;
};

class IBAuthoritativeQuoteSubscriptionSet
{
public:
    explicit IBAuthoritativeQuoteSubscriptionSet(AuthoritativeTradingSnapshotStore& store,
                                                 int firstRequestId = 1001);

    bool Configure(const std::map<std::string, IBContractLite>& contracts,
                   const std::string& primaryInstrument,
                   std::string& reason,
                   bool preserveActiveOnNextCycle = false,
                   bool independentQuotes = false);
    IBAuthoritativeQuoteSubscriptionPlan BeginCycle(std::uint64_t connectionEpoch,
                                                    std::uint64_t generation,
                                                    std::uint64_t observedAtMs);
    // Market-data only: refresh a stale secondary CASH ticker in place without
    // changing the primary ticker or its trading authority.
    IBAuthoritativeQuoteSubscriptionPlan BeginStaleSecondaryCashRefresh(
        const std::string& instrument, std::uint64_t connectionEpoch,
        std::uint64_t generation, std::uint64_t observedAtMs,
        std::uint64_t quoteMaxAgeMs, std::uint64_t minRetryMs,
        std::uint64_t freshQuoteMaxAgeMs = 5000);
    IBAuthoritativeQuoteSubscriptionPlan BeginStaleSecondaryCashRefresh(
        const std::string& instrument, std::uint64_t connectionEpoch,
        std::uint64_t generation, std::uint64_t observedAtMs,
        std::uint64_t quoteMaxAgeMs, std::uint64_t minRetryMs,
        std::uint64_t freshQuoteMaxAgeMs,
        std::uint64_t pendingNativePairTimeoutMs);
    bool RecordDispatchResult(std::uint64_t generation, int requestId, bool accepted);
    bool RecordDispatchResult(std::uint64_t generation, int requestId, bool accepted,
                              std::uint64_t observedAtMs);
    bool RecordRetirementResult(std::uint64_t generation, int requestId);
    // A native 354 is terminal only for this exactly owned, sent renewal.
    // It is not a quote or a cancellation acknowledgement.
    bool RejectSecondaryCashRefreshRequest(const IBEvent& event,
        std::uint64_t observedAtMs, std::uint64_t retryBackoffMs = 15000);
    std::vector<int> AbortCycle(std::uint64_t generation);
    IBAuthoritativeQuoteConsumeResult ConsumeTick(const IBEvent& event,
                                                  std::uint64_t observedAtMs);

    IBAuthoritativeQuoteSnapshot GetQuote(const std::string& instrument) const;
    IBAuthoritativeQuoteSnapshot GetPrimaryQuote() const;
    std::string PrimaryInstrument() const;
    bool IsComplete() const;
    std::uint64_t CurrentGeneration() const;
    std::size_t DesiredCount() const;
    IBAuthoritativeQuoteSubscriptionHealth GetHealth() const;
    void ForceFullNextCycle();

private:
    struct QuoteState
    {
        std::string instrument;
        IBContractLite contract;
        bool dispatchAccepted = false;
        // Distinguish an initial/pending SDK dispatch from a positively
        // established pre-SDK refusal. Only the latter may be retried
        // without cancelling an unissued market-data request.
        bool dispatchResultRecorded = false;
        // Native rejection is distinct from a refusal before SDK entry.
        bool nativeRequestRejected = false;
        bool hadCompletePair = false;
        std::uint64_t requestStartedAtMs = 0;
        // The first-pair grace begins when the SDK return was actually
        // observed, not when a plan was created before a potentially slow send.
        std::uint64_t dispatchAcceptedAtMs = 0;
        std::uint64_t freshQuoteMaxAgeMs = 0;
        IBAuthoritativeQuoteSnapshot quote;
    };

    static bool RecognizedTickField(const std::string& field,
                                    bool& bid,
                                    bool& ask,
                                    bool& last);
    static AuthoritativeQuote MaterializeQuote(const QuoteState& state);
    static bool SameContract(const IBContractLite& left, const IBContractLite& right);
    bool AllQuotesReadyLocked() const;
    std::uint64_t OldestLivenessObservationLocked() const;
    std::vector<AuthoritativeQuote> MaterializeAllLocked() const;
    std::vector<int> ActiveRequestIdsLocked() const;

private:
    AuthoritativeTradingSnapshotStore& m_store;
    mutable std::mutex m_mutex;
    std::map<std::string, IBContractLite> m_desiredContracts;
    std::string m_primaryInstrument;
    std::map<int, QuoteState> m_quotesByRequestId;
    std::map<std::string, int> m_requestIdByInstrument;
    std::map<std::string, int> m_pendingRequestIdByInstrument;
    std::map<std::string, int> m_retiringRequestIdByInstrument;
    std::map<std::string, std::uint64_t> m_refreshRetryNotBeforeMs;
    std::int64_t m_nextRequestId;
    std::uint64_t m_connectionEpoch = 0;
    std::uint64_t m_generation = 0;
    bool m_complete = false;
    std::uint64_t m_desiredRevision = 0;
    bool m_preserveActiveOnNextCycle = false;
    bool m_independentQuotes = false;
};
