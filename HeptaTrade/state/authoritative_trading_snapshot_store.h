#pragma once

#include <cstddef>
#include <cstdint>
#include <map>
#include <mutex>
#include <string>
#include <vector>

enum class AuthoritativeSnapshotAvailability
{
    Missing = 0,
    Fresh,
    Stale
};

enum class AuthoritativeOrderSide
{
    Buy = 0,
    Sell
};

enum class AuthoritativeOrderType
{
    Market = 0,
    Limit,
    Stop,
    StopLimit
};

// Only non-terminal broker states belong in the active-order snapshot.
enum class AuthoritativeActiveOrderStatus
{
    PendingSubmit = 0,
    PreSubmitted,
    Submitted,
    PartiallyFilled,
    PendingCancel
};

struct AuthoritativeQuote
{
    std::string instrument;
    double bid = 0.0;
    double ask = 0.0;
    double last = 0.0;
    double bidSize = 0.0;
    double askSize = 0.0;
};

struct AuthoritativeAccount
{
    std::string account;
    std::string currency;

    bool hasNetLiquidation = false;
    double netLiquidation = 0.0;
    bool hasAvailableFunds = false;
    double availableFunds = 0.0;
    bool hasBuyingPower = false;
    double buyingPower = 0.0;
    bool hasCash = false;
    double cash = 0.0;
    bool hasMaintenanceMargin = false;
    double maintenanceMargin = 0.0;
    bool hasRealizedPnl = false;
    double realizedPnl = 0.0;
    bool hasUnrealizedPnl = false;
    double unrealizedPnl = 0.0;
};

struct AuthoritativePosition
{
    std::string account;
    std::string instrument;
    double quantity = 0.0;
    double averageCost = 0.0;
};

struct AuthoritativeActiveOrder
{
    std::string venue;
    long orderId = -1;
    std::string account;
    std::string instrument;
    AuthoritativeOrderSide side = AuthoritativeOrderSide::Buy;
    AuthoritativeOrderType type = AuthoritativeOrderType::Market;
    AuthoritativeActiveOrderStatus status = AuthoritativeActiveOrderStatus::PendingSubmit;
    double totalQuantity = 0.0;
    double filledQuantity = 0.0;
    double remainingQuantity = 0.0;
    double limitPrice = 0.0;
    double stopPrice = 0.0;
};

struct AuthoritativePositionKey
{
    std::string account;
    std::string instrument;

    bool operator<(const AuthoritativePositionKey& other) const;
};

struct AuthoritativeOrderKey
{
    std::string venue;
    long orderId = -1;

    bool operator<(const AuthoritativeOrderKey& other) const;
};

struct AuthoritativeSnapshotRecordState
{
    AuthoritativeSnapshotAvailability availability = AuthoritativeSnapshotAvailability::Missing;
    std::uint64_t updatedAtMs = 0;
    std::uint64_t updatedAtVersion = 0;
    std::string source;
};

struct AuthoritativeQuoteRecord
{
    AuthoritativeQuote value;
    AuthoritativeSnapshotRecordState state;
};

struct AuthoritativeAccountRecord
{
    AuthoritativeAccount value;
    AuthoritativeSnapshotRecordState state;
};

struct AuthoritativePositionRecord
{
    AuthoritativePosition value;
    AuthoritativeSnapshotRecordState state;
};

struct AuthoritativeActiveOrderRecord
{
    AuthoritativeActiveOrder value;
    AuthoritativeSnapshotRecordState state;
};

struct AuthoritativeSnapshotDomainState
{
    AuthoritativeSnapshotAvailability availability = AuthoritativeSnapshotAvailability::Missing;
    bool complete = false;
    std::uint64_t lastUpdatedAtMs = 0;
    std::uint64_t lastUpdatedVersion = 0;
    std::size_t recordCount = 0;
    std::size_t staleRecordCount = 0;
};

struct AuthoritativeSnapshotFreshnessPolicy
{
    std::uint64_t quoteMaxAgeMs = 5000;
    std::uint64_t accountMaxAgeMs = 60000;
    std::uint64_t positionMaxAgeMs = 30000;
    std::uint64_t activeOrderMaxAgeMs = 30000;
};

struct AuthoritativeExecutionState
{
    bool connected = false;
    bool authoritative = false;
    std::uint64_t updatedAtMs = 0;
    std::uint64_t updatedAtVersion = 0;
    std::string source;
    std::string reason;
};

struct AuthoritativeTradingSnapshot
{
    std::uint64_t snapshotVersion = 0;
    std::uint64_t capturedAtMs = 0;
    AuthoritativeExecutionState executionState;

    AuthoritativeSnapshotDomainState quotesState;
    AuthoritativeSnapshotDomainState accountsState;
    AuthoritativeSnapshotDomainState positionsState;
    AuthoritativeSnapshotDomainState activeOrdersState;

    std::map<std::string, AuthoritativeQuoteRecord> quotes;
    std::map<std::string, AuthoritativeAccountRecord> accounts;
    std::map<AuthoritativePositionKey, AuthoritativePositionRecord> positions;
    std::map<AuthoritativeOrderKey, AuthoritativeActiveOrderRecord> activeOrders;
};

struct AuthoritativeSnapshotWriteResult
{
    bool accepted = false;
    std::uint64_t snapshotVersion = 0;
    std::string reasonCode;
};

// A single-lock, versioned state boundary for Agent-facing trading tools.
//
// Every accepted mutation advances one global version. Replace* operations are
// atomic and mark their domain complete, making broker refresh completion
// explicit. Upsert* operations preserve completeness only after a completed
// refresh. Rejected inputs never mutate state or consume a version.
class AuthoritativeTradingSnapshotStore
{
public:
    AuthoritativeTradingSnapshotStore();

    AuthoritativeSnapshotWriteResult SetExecutionState(bool connected,
                                                        bool authoritative,
                                                        std::uint64_t observedAtMs,
                                                        const std::string& source,
                                                        const std::string& reason = "");

    AuthoritativeSnapshotWriteResult UpsertQuote(const AuthoritativeQuote& quote,
                                                  std::uint64_t observedAtMs,
                                                  const std::string& source);
    AuthoritativeSnapshotWriteResult UpsertAccount(const AuthoritativeAccount& account,
                                                    std::uint64_t observedAtMs,
                                                    const std::string& source);
    AuthoritativeSnapshotWriteResult UpsertPosition(const AuthoritativePosition& position,
                                                     std::uint64_t observedAtMs,
                                                     const std::string& source);
    AuthoritativeSnapshotWriteResult UpsertActiveOrder(const AuthoritativeActiveOrder& order,
                                                        std::uint64_t observedAtMs,
                                                        const std::string& source);

    AuthoritativeSnapshotWriteResult ReplaceQuotes(const std::vector<AuthoritativeQuote>& quotes,
                                                    std::uint64_t observedAtMs,
                                                    const std::string& source);
    AuthoritativeSnapshotWriteResult InvalidateQuotes(std::uint64_t observedAtMs,
                                                       const std::string& source);
    AuthoritativeSnapshotWriteResult ReplaceAccounts(const std::vector<AuthoritativeAccount>& accounts,
                                                      std::uint64_t observedAtMs,
                                                      const std::string& source);
    AuthoritativeSnapshotWriteResult ReplacePositions(const std::vector<AuthoritativePosition>& positions,
                                                       std::uint64_t observedAtMs,
                                                       const std::string& source);
    AuthoritativeSnapshotWriteResult ReplaceActiveOrders(const std::vector<AuthoritativeActiveOrder>& orders,
                                                          std::uint64_t observedAtMs,
                                                          const std::string& source);

    AuthoritativeSnapshotWriteResult ErasePosition(const std::string& account,
                                                    const std::string& instrument,
                                                    std::uint64_t observedAtMs,
                                                    const std::string& source);
    AuthoritativeSnapshotWriteResult EraseActiveOrder(const std::string& venue,
                                                       long orderId,
                                                       std::uint64_t observedAtMs,
                                                       const std::string& source);

    AuthoritativeQuoteRecord GetQuote(const std::string& instrument,
                                      std::uint64_t nowMs,
                                      std::uint64_t maxAgeMs) const;
    AuthoritativeAccountRecord GetAccount(const std::string& account,
                                          std::uint64_t nowMs,
                                          std::uint64_t maxAgeMs) const;
    AuthoritativePositionRecord GetPosition(const std::string& account,
                                            const std::string& instrument,
                                            std::uint64_t nowMs,
                                            std::uint64_t maxAgeMs) const;
    AuthoritativeActiveOrderRecord GetActiveOrder(const std::string& venue,
                                                  long orderId,
                                                  std::uint64_t nowMs,
                                                  std::uint64_t maxAgeMs) const;

    AuthoritativeTradingSnapshot GetSnapshot(
        std::uint64_t nowMs,
        const AuthoritativeSnapshotFreshnessPolicy& policy = AuthoritativeSnapshotFreshnessPolicy()) const;
    std::uint64_t SnapshotVersion() const;

private:
    struct DomainTracker
    {
        bool touched = false;
        bool complete = false;
        std::uint64_t lastUpdatedAtMs = 0;
        std::uint64_t lastUpdatedVersion = 0;
    };

    static AuthoritativeSnapshotAvailability Classify(std::uint64_t updatedAtMs,
                                                       std::uint64_t nowMs,
                                                       std::uint64_t maxAgeMs);
    static bool ValidateObservation(std::uint64_t observedAtMs,
                                    const std::string& source,
                                    std::string& reason);
    static bool ValidateQuote(const AuthoritativeQuote& quote, std::string& reason);
    static bool ValidateAccount(const AuthoritativeAccount& account, std::string& reason);
    static bool ValidatePosition(const AuthoritativePosition& position, std::string& reason);
    static bool ValidateActiveOrder(const AuthoritativeActiveOrder& order, std::string& reason);

    AuthoritativeSnapshotWriteResult RejectLocked(const std::string& reason) const;
    bool NextVersionLocked(std::uint64_t& nextVersion, std::string& reason) const;
    static void SetRecordState(AuthoritativeSnapshotRecordState& state,
                               std::uint64_t observedAtMs,
                               std::uint64_t version,
                               const std::string& source);
    static void TouchDomain(DomainTracker& domain,
                            std::uint64_t observedAtMs,
                            std::uint64_t version,
                            bool complete);

private:
    mutable std::mutex m_mutex;
    std::uint64_t m_snapshotVersion;
    DomainTracker m_quotesDomain;
    DomainTracker m_accountsDomain;
    DomainTracker m_positionsDomain;
    DomainTracker m_activeOrdersDomain;
    AuthoritativeExecutionState m_executionState;
    std::map<std::string, AuthoritativeQuoteRecord> m_quotes;
    std::map<std::string, AuthoritativeAccountRecord> m_accounts;
    std::map<AuthoritativePositionKey, AuthoritativePositionRecord> m_positions;
    std::map<AuthoritativeOrderKey, AuthoritativeActiveOrderRecord> m_activeOrders;
};
