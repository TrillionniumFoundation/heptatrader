#pragma once

#include <cstdint>
#include <set>
#include <string>

struct PreTradeRiskConfig {
    bool enableOrderSubmission = false;
    bool globalKillSwitch = false;
    bool flattenOnly = false;

    double maxOrderQuantity = 1.0;
    int maxDailyOrders = 1;
    double maxPriceDeviationBps = 30.0;

    // Base-currency limits. A zero value disables the corresponding optional
    // limit. Portfolio limits require an explicit, fresh authoritative
    // snapshot; default numeric zeroes never mean "observed zero".
    double maxOrderNotional = 0.0;
    double maxWorstCaseGrossNotional = 0.0;
    double maxDailyLoss = 0.0;
    double maxDrawdown = 0.0;
    std::int64_t maxSnapshotAgeMs = 0;

    bool allowLiveTrading = false;
    bool liveKillSwitch = true;
};

// Supplied by the execution authority from its configured portfolio, never
// inferred from an order or a numeric snapshot. Instrument ids must identify
// full contracts (including expiry/strike where applicable), not root symbols.
struct PreTradeRiskSubject {
    std::string portfolioId;
    std::string account;
    std::string venue;
    std::string baseCurrency;
    std::set<std::string> instruments;
};

struct PreTradeRiskSnapshotIdentity {
    PreTradeRiskSubject subject;
    bool present = false;
    bool complete = false;
    std::uint64_t connectionEpoch = 0;
    std::uint64_t generation = 0;
    std::int64_t observedAtMs = 0;
    std::int64_t evaluatedAtMs = 0;
};

struct PreTradeRiskExposureSnapshot {
    PreTradeRiskSubject subject;
    bool present = false;
    std::uint64_t connectionEpoch = 0;
    std::uint64_t generation = 0;
    double currentGrossNotional = 0.0;
    double pendingBuyNotional = 0.0;
    double pendingSellNotional = 0.0;
};

struct PreTradeRiskPnlSnapshot {
    PreTradeRiskSubject subject;
    bool present = false;
    std::uint64_t connectionEpoch = 0;
    std::uint64_t generation = 0;
    double realizedPnl = 0.0;
    double unrealizedPnl = 0.0;
};

struct PreTradeRiskEquitySnapshot {
    PreTradeRiskSubject subject;
    bool present = false;
    std::uint64_t connectionEpoch = 0;
    std::uint64_t generation = 0;
    double peakEquity = 0.0;
    double currentEquity = 0.0;
};

struct PreTradeRiskAuthoritativeSnapshot {
    PreTradeRiskSnapshotIdentity identity;
    PreTradeRiskExposureSnapshot exposure;
    PreTradeRiskPnlSnapshot pnl;
    PreTradeRiskEquitySnapshot equity;
};

enum class PreTradeRiskQuantityUnit { Unspecified, BaseCurrencyUnits, Shares, Contracts };
enum class PreTradeRiskPriceUnit { Unspecified, QuoteCurrencyPerUnit };
enum class PreTradeRiskInstrumentKind { Unspecified, CashFx, Stock, Future, Option };

// Reviewed instrument metadata supplied independently of conversion evidence.
// Options use quoted premium * contract multiplier; futures use quoted price
// * contract multiplier. Other conventions require a new explicit contract.
struct PreTradeRiskInstrumentContract {
    std::string specificationId;
    std::uint64_t specificationVersion = 0;
    std::string instrument;
    PreTradeRiskInstrumentKind kind = PreTradeRiskInstrumentKind::Unspecified;
    PreTradeRiskQuantityUnit quantityUnit = PreTradeRiskQuantityUnit::Unspecified;
    PreTradeRiskPriceUnit priceUnit = PreTradeRiskPriceUnit::Unspecified;
    double multiplier = 0.0;
    std::string quoteCurrency;
};

struct PreTradeRiskPriceEvidence {
    std::string sourceId;
    std::string instrument;
    std::string currency;
    std::uint64_t connectionEpoch = 0;
    std::uint64_t generation = 0;
    std::int64_t observedAtMs = 0;
    double price = 0.0;
};

struct PreTradeRiskFxEvidence {
    std::string sourceId;
    std::string fromCurrency;
    std::string toCurrency;
    std::uint64_t connectionEpoch = 0;
    std::uint64_t generation = 0;
    std::int64_t observedAtMs = 0;
    double rate = 0.0; // account base currency per quote currency unit
};

struct PreTradeRiskOrderNotionalEvidence {
    bool present = false;
    PreTradeRiskSubject subject;
    std::uint64_t connectionEpoch = 0;
    std::uint64_t generation = 0;
    PreTradeRiskInstrumentContract contract;
    double quantity = 0.0;
    double baseCurrencyNotional = 0.0;
    PreTradeRiskPriceEvidence quote;
    PreTradeRiskFxEvidence fx;
};

struct PreTradeRiskContext {
    std::string venue;      // IB / CTP / ...
    std::string account;
    std::string symbol;
    std::string action;     // BUY / SELL
    std::string orderType;  // LMT / MKT

    PreTradeRiskSubject authorizedSubject;
    PreTradeRiskInstrumentContract instrumentContract;
    std::string authorizedQuoteSourceId;
    std::string authorizedFxSourceId;
    // Execution-owned clock for this decision, independent of replayed data.
    std::int64_t evaluatedAtMs = 0;

    double totalQuantity = 0.0;
    double limitPrice = 0.0;
    double referencePrice = 0.0;

    int todayOrderCount = 0;
    bool accountWhitelisted = false;
    bool paperAccount = true;

    bool positionKnown = false;
    double netPosition = 0.0;

    PreTradeRiskOrderNotionalEvidence orderNotionalEvidence;

    PreTradeRiskAuthoritativeSnapshot authoritativeSnapshot;

    // adapter extension points (for CTP etc.)
    std::string adapterTag;
};

struct PreTradeRiskDecision {
    bool allow = false;
    std::string reasonCode; // unified RISK_XXX
    std::string detail;
    double orderNotional = 0.0;
    double worstCaseGrossNotional = 0.0;
    std::uint64_t snapshotConnectionEpoch = 0;
    std::uint64_t snapshotGeneration = 0;
};

class PreTradeRiskEngine {
public:
    static PreTradeRiskDecision Evaluate(const PreTradeRiskConfig& cfg,
                                         const PreTradeRiskContext& ctx);

private:
    static bool IsFlatteningOrder(const PreTradeRiskContext& ctx);
};
