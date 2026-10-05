#pragma once

#include "../execution/trading_contract.h"
#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace hepta { namespace options {

struct Session {
    std::string tradingDate; // Exchange date YYYYMMDD, never host-local date.
    std::int64_t openMs = 0, lastTradeMs = 0;
    bool brokerScheduleVerified = false;
};
struct Contract {
    std::int64_t conId = 0, underlyingConId = 0;
    InstrumentRef ref;
    Session session;
    bool european = false, cashSettled = false, pmSettled = false;
    std::string key() const;
};
struct Tick {
    double value = 0;
    std::int64_t observedMs = 0;
    std::uint64_t epoch = 0;
    bool valid = false;
};
struct Book {
    Tick bid, ask, bidSize, askSize;
    int marketDataType = 0; // IB 1=live, 2=frozen, 3=delayed, 4=delayed-frozen.
    bool entitlementDenied = false;
};
struct Greeks {
    double delta = 0, gamma = 0, theta = 0, vega = 0, iv = 0, underlying = 0;
    std::int64_t observedMs = 0;
    std::uint64_t epoch = 0;
    bool valid = false;
};
struct Snapshot {
    std::int64_t nowMs = 0;
    std::uint64_t epoch = 0; // Broker account/position/order epoch.
    // Independent market-data epoch. Zero preserves legacy IB-only captures.
    // Never retag an external feed's old ticks with a new broker epoch.
    std::string marketDataSource = "IB";
    std::uint64_t marketDataEpoch = 0;
    std::string account, owner, executionDomain;
    Book option;
    Tick underlying;
    int underlyingDataType = 0;
    Greeks greeks;
    bool accountComplete = false, positionsComplete = false, ordersComplete = false;
    bool tradingPermissionVerified = false, marginPreviewVerified = false;
    bool killNewEntries = true, unresolvedSend = true;
    int ownedContracts = 0, ownedActiveOrders = 0;
    std::string ownedContractKey;
    double dailyRealizedLossUsd = 0;
    double estimatedFeesUsd = 0;
};
struct Policy {
    std::string account, owner;
    int maxContracts = 1;
    // Deliberately zero until the operator specifies an options-only budget.
    // Never inherit FX's 25k base size or BTC's $25k cash order.
    double maxPremiumUsd = 0, maxDailyLossUsd = 0;
    std::int64_t maxQuoteAgeMs = 1000, maxGreeksAgeMs = 3000;
    std::int64_t entryCutoffBeforeCloseMs = 60 * 60 * 1000;
    std::int64_t exitBeforeCloseMs = 30 * 60 * 1000;
    double maxAbsoluteSpread = 0.10, maxRelativeSpread = 0.10;
};
struct Decision {
    bool marketDataReady = false, eligible = false;
    std::string action = "NO_TRADE";
    std::vector<std::string> reasons;
    double estimatedPremiumUsd = 0, estimatedCostUsd = 0;
    std::int64_t entryCutoffMs = 0, exitAtMs = 0;
};

std::string NewYorkDate(std::int64_t utcMs);
std::int64_t ExchangeTimestamp(const std::string& day, const std::string& hhmm,
                              const std::string& zone);
Session ParseIbSession(const std::string& expiry, const std::string& liquidHours,
                       const std::string& lastTradeTime, const std::string& zone);
std::string ValidateContract(const Contract& contract);
Decision Evaluate(const Policy& policy, const Contract& contract,
                  const Snapshot& snapshot, bool reduceOnly);
std::string DecisionJson(const Decision& decision);
std::string EncodeObservation(const Policy&, const Contract&, const Snapshot&);
bool DecodeObservation(const std::string&, Policy&, Contract&, Snapshot&, std::string& expected, std::string& reason);
std::string EscapeJson(const std::string& value);

struct Execution {
    std::string account, owner, contractKey, commandId, correlationId, executionId;
    std::string side, currency = "USD";
    int contracts = 0, cumulativeContracts = 0;
    double price = 0, cumulativeAveragePrice = 0;
};
struct Terminal {
    std::string commandId, status;
    int filledContracts = 0;
    bool authoritative = false;
};

// Economic evidence only: no socket, order submission or account mutation API.
// Existing ExecutionCoordinator continues to own prepare/send/ACK/unknown states.
class EvidenceLedger {
public:
    EvidenceLedger(const Contract& contract, const std::string& account,
                   const std::string& owner);
    bool ExecutionObserved(const Execution& execution);
    bool FeeObserved(const std::string& executionId, double feeUsd,
                     const std::string& currency);
    bool TerminalObserved(const Terminal& terminal);
    bool CashSettlementObserved(const std::string& receiptId, double cashUsd,
                                double feeUsd, bool brokerPositionFlat,
                                bool brokerCashComplete, std::int64_t observedMs);
    bool ExecutionSetComplete() const;
    bool FeesComplete() const;
    // Requires the exact PAPER account/owner/contract and complete flat-order audit.
    // This is economic evidence validation, never a broker mutation permit.
    bool RoundTripProven(const Snapshot& authoritative) const;
    std::string State(std::int64_t nowMs) const;
    int KnownPosition() const;
    double GrossPnl() const;
    double NetPnl() const;
    const std::string& Failure() const { return failure_; }
    const std::map<std::string, Execution>& Executions() const { return executions_; }
private:
    bool Fail(const char* reason);
    Contract contract_;
    std::string account_, owner_, failure_;
    std::map<std::string, Execution> executions_;
    std::map<std::string, double> fees_;
    std::map<std::string, Terminal> terminals_;
    std::string settlementId_;
    double settlementCash_ = 0, settlementFee_ = 0;
};

} } // namespace hepta::options
