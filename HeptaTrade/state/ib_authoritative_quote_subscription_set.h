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
};

struct IBAuthoritativeQuoteSubscriptionHealth
{
    std::uint64_t desiredRevision = 0;
    std::uint64_t connectionEpoch = 0;
    std::uint64_t generation = 0;
    bool complete = false;
    std::string primaryInstrument;
    std::map<std::string, IBAuthoritativeQuoteContractHealth> contracts;
};

class IBAuthoritativeQuoteSubscriptionSet
{
public:
    explicit IBAuthoritativeQuoteSubscriptionSet(AuthoritativeTradingSnapshotStore& store,
                                                 int firstRequestId = 1001);

    bool Configure(const std::map<std::string, IBContractLite>& contracts,
                   const std::string& primaryInstrument,
                   std::string& reason,
                   bool preserveActiveOnNextCycle = false);
    IBAuthoritativeQuoteSubscriptionPlan BeginCycle(std::uint64_t connectionEpoch,
                                                    std::uint64_t generation,
                                                    std::uint64_t observedAtMs);
    bool RecordDispatchResult(std::uint64_t generation, int requestId, bool accepted);
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
    std::int64_t m_nextRequestId;
    std::uint64_t m_connectionEpoch = 0;
    std::uint64_t m_generation = 0;
    bool m_complete = false;
    std::uint64_t m_desiredRevision = 0;
    bool m_preserveActiveOnNextCycle = false;
};
