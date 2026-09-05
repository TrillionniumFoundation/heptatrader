#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

using SimulatorFxRaw = std::int64_t;

// A simulator policy, not a broker instrument master or a settlement calendar.
// Values are millionths; quantity steps must be whole base-currency units.
struct SimulatorFxInstrument
{
    std::string venue = "simulator", instrument = "EUR.USD";
    std::string baseCurrency = "EUR", quoteCurrency = "USD", revision;
    SimulatorFxRaw quantityStepRaw = 1000000, priceTickRaw = 1;
    SimulatorFxRaw minimumQuantityRaw = 1000000, maximumQuantityRaw = 9000000000000000LL;
    std::uint64_t effectiveFromMs = 0, effectiveUntilMs = 0;
};

struct SimulatorFxOpening
{
    std::string bookId, instrumentRevision;
    SimulatorFxRaw baseBalanceRaw = 0, quoteBalanceRaw = 0;
    std::uint64_t asOfMs = 0;
};

enum class SimulatorFxEventKind { Fill = 1, Commission = 2 };
enum class SimulatorFxSide { None = 0, Buy = 1, Sell = 2 };

struct SimulatorFxEvent
{
    std::string eventId, executionId, bookId, instrument, instrumentRevision;
    std::uint64_t sequence = 0, eventTimeMs = 0, recordedAtMs = 0;
    SimulatorFxEventKind kind = SimulatorFxEventKind::Fill;
    SimulatorFxSide side = SimulatorFxSide::None;
    SimulatorFxRaw quantityRaw = 0, priceRaw = 0, commissionRaw = 0;
    std::string commissionCurrency;
};

struct SimulatorFxProjection
{
    std::string bookId, instrumentRevision, digest;
    SimulatorFxRaw baseBalanceRaw = 0, quoteBalanceRaw = 0;
    SimulatorFxRaw netBaseTradeRaw = 0, netQuoteTradeRaw = 0, commissionsRaw = 0;
    std::uint64_t lastSequence = 0, lastRecordedAtMs = 0, asOfMs = 0;
    std::size_t fills = 0, commissions = 0, duplicates = 0;
    bool feesComplete = false;
};

struct SimulatorFxReplayResult
{
    bool accepted = false;
    const char* reasonCode = "FX_LEDGER_UNVALIDATED";
    std::size_t failedIndex = static_cast<std::size_t>(-1);
    SimulatorFxProjection projection;
};

// Independent same-cut simulator observations, not authenticated Broker data.
struct SimulatorFxObservation
{
    std::string bookId, instrumentRevision;
    std::uint64_t asOfMs = 0, lastSequence = 0;
    SimulatorFxRaw baseBalanceRaw = 0, quoteBalanceRaw = 0, commissionsRaw = 0;
    std::size_t fills = 0, commissions = 0;
};

struct SimulatorFxReconciliation
{
    bool matched = false;
    const char* reasonCode = "FX_LEDGER_UNRECONCILED";
    std::string projectionDigest;
};

// Pure replay: inputs unchanged; failures publish no prefix balances or digest.
// Exact duplicate records are ignored; changed IDs or economic replacements
// require an upstream reconciliation policy, never an implicit overwrite here.
class SimulatorFxAccounting
{
public:
    static const char* Version() noexcept { return "hepta.simulator-fx-accounting.v1"; }
    static SimulatorFxReplayResult Replay(const SimulatorFxInstrument& instrument,
        const SimulatorFxOpening& opening, const std::vector<SimulatorFxEvent>& events,
        std::uint64_t asOfMs, std::size_t maximumRecords = 4096);
    static SimulatorFxReconciliation Reconcile(const SimulatorFxInstrument& instrument,
        const SimulatorFxOpening& opening, const std::vector<SimulatorFxEvent>& events,
        std::uint64_t asOfMs, const SimulatorFxObservation& observed,
        std::size_t maximumRecords = 4096);
};
