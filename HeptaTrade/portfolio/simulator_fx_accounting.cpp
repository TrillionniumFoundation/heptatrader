#include "simulator_fx_accounting.h"

#include <map>
#include <memory>
#include <openssl/evp.h>
#include <tuple>
#include <type_traits>
#include <utility>

namespace
{
constexpr SimulatorFxRaw Scale = 1000000, Maximum = 9000000000000000LL;
constexpr std::size_t NoIndex = static_cast<std::size_t>(-1);
bool Id(const std::string& value)
{
    if (value.empty() || value.size() > 128) return false;
    for (unsigned char c : value)
        if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
              (c >= '0' && c <= '9') || c == '.' || c == '_' || c == '-' || c == ':')) return false;
    return true;
}
bool UnsignedRaw(SimulatorFxRaw n) { return n >= 0 && n <= Maximum; }
bool Add(SimulatorFxRaw a, SimulatorFxRaw b, SimulatorFxRaw& out)
{
    // Both terms are bounded before this call; their sum cannot overflow int64.
    if (a < -Maximum || a > Maximum || b < -Maximum || b > Maximum) return false;
    const auto sum = a + b;
    if (sum < -Maximum || sum > Maximum) return false;
    out = sum; return true;
}
bool ValidInstrument(const SimulatorFxInstrument& s)
{
    return s.venue == "simulator" && s.instrument == "EUR.USD" &&
        s.baseCurrency == "EUR" && s.quoteCurrency == "USD" && Id(s.revision) &&
        s.quantityStepRaw > 0 && s.quantityStepRaw <= Maximum && s.quantityStepRaw % Scale == 0 &&
        s.priceTickRaw > 0 && s.priceTickRaw <= Maximum &&
        s.minimumQuantityRaw > 0 && s.minimumQuantityRaw <= s.maximumQuantityRaw &&
        s.maximumQuantityRaw <= Maximum && s.minimumQuantityRaw % s.quantityStepRaw == 0 &&
        s.maximumQuantityRaw % s.quantityStepRaw == 0 &&
        s.effectiveFromMs != 0 && s.effectiveUntilMs > s.effectiveFromMs;
}
const char* EventError(const SimulatorFxEvent& e, const SimulatorFxInstrument& s,
                       const SimulatorFxOpening& o, std::uint64_t asOf)
{
    if (!Id(e.eventId) || !Id(e.executionId) || e.bookId != o.bookId ||
        e.instrument != s.instrument || e.instrumentRevision != s.revision) return "FX_LEDGER_EVENT_IDENTITY_INVALID";
    if (e.sequence == 0 || e.eventTimeMs < o.asOfMs || e.recordedAtMs < e.eventTimeMs ||
        e.recordedAtMs > asOf) return "FX_LEDGER_EVENT_TIME_INVALID";
    if (e.kind == SimulatorFxEventKind::Fill)
    {
        if ((e.side != SimulatorFxSide::Buy && e.side != SimulatorFxSide::Sell) ||
            e.quantityRaw < s.minimumQuantityRaw || e.quantityRaw > s.maximumQuantityRaw ||
            e.quantityRaw % s.quantityStepRaw != 0 || e.priceRaw <= 0 || e.priceRaw > Maximum ||
            e.priceRaw % s.priceTickRaw != 0 || e.commissionRaw != 0 || !e.commissionCurrency.empty())
            return "FX_LEDGER_FILL_INVALID";
        if (e.eventTimeMs < s.effectiveFromMs || e.eventTimeMs >= s.effectiveUntilMs)
            return "FX_LEDGER_INSTRUMENT_NOT_EFFECTIVE";
    }
    else if (e.kind == SimulatorFxEventKind::Commission)
    {
        if (e.side != SimulatorFxSide::None || e.quantityRaw != 0 || e.priceRaw != 0 ||
            !UnsignedRaw(e.commissionRaw) || e.commissionCurrency != s.quoteCurrency)
            return "FX_LEDGER_COMMISSION_INVALID";
    }
    else return "FX_LEDGER_EVENT_KIND_INVALID";
    return nullptr;
}
bool SameEvent(const SimulatorFxEvent& a, const SimulatorFxEvent& b)
{
    return std::tie(a.eventId,a.executionId,a.bookId,a.instrument,a.instrumentRevision,a.sequence,
        a.eventTimeMs,a.recordedAtMs,a.kind,a.side,a.quantityRaw,a.priceRaw,a.commissionRaw,a.commissionCurrency) ==
        std::tie(b.eventId,b.executionId,b.bookId,b.instrument,b.instrumentRevision,b.sequence,
        b.eventTimeMs,b.recordedAtMs,b.kind,b.side,b.quantityRaw,b.priceRaw,b.commissionRaw,b.commissionCurrency);
}
class Digest
{
    std::unique_ptr<EVP_MD_CTX, decltype(&EVP_MD_CTX_free)> ctx{EVP_MD_CTX_new(), EVP_MD_CTX_free};
    bool ok = ctx && EVP_DigestInit_ex(ctx.get(), EVP_sha256(), nullptr) == 1;
public:
    void Bytes(const void* data, std::size_t n) { if (ok && EVP_DigestUpdate(ctx.get(),data,n) != 1) ok = false; }
    void Number(std::uint64_t n)
    {
        unsigned char bytes[8];
        for (int i = 7; i >= 0; --i) { bytes[i] = static_cast<unsigned char>(n & 255u); n >>= 8; }
        Bytes(bytes,8);
    }
    void Field(const std::string& s) { Number(s.size()); Bytes(s.data(),s.size()); }
    void Event(const SimulatorFxEvent& e)
    {
        Number(1); // Record domain, separate from the final totals record.
        Field(e.eventId); Field(e.executionId); Field(e.bookId); Field(e.instrument); Field(e.instrumentRevision);
        Number(e.sequence); Number(e.eventTimeMs); Number(e.recordedAtMs);
        Number(static_cast<std::uint64_t>(e.kind)); Number(static_cast<std::uint64_t>(e.side));
        Number(static_cast<std::uint64_t>(e.quantityRaw)); Number(static_cast<std::uint64_t>(e.priceRaw));
        Number(static_cast<std::uint64_t>(e.commissionRaw)); Field(e.commissionCurrency);
    }
    std::string Finish()
    {
        unsigned char value[EVP_MAX_MD_SIZE]; unsigned length = 0;
        if (!ok || EVP_DigestFinal_ex(ctx.get(),value,&length) != 1 || length != 32) return {};
        std::string out = "sha256:"; const char* hex = "0123456789abcdef";
        for (unsigned i = 0; i < length; ++i) { out.push_back(hex[value[i]>>4]); out.push_back(hex[value[i]&15]); }
        return out;
    }
};
SimulatorFxReplayResult Reject(const char* reason, std::size_t index = NoIndex)
{
    SimulatorFxReplayResult result; result.reasonCode = reason; result.failedIndex = index; return result;
}
}

SimulatorFxReplayResult SimulatorFxAccounting::Replay(const SimulatorFxInstrument& s,
    const SimulatorFxOpening& o, const std::vector<SimulatorFxEvent>& events,
    std::uint64_t asOf, std::size_t maximumRecords)
{
    if (!ValidInstrument(s)) return Reject("FX_LEDGER_INSTRUMENT_INVALID");
    if (!Id(o.bookId) || o.instrumentRevision != s.revision || !UnsignedRaw(o.baseBalanceRaw) ||
        !UnsignedRaw(o.quoteBalanceRaw) || o.asOfMs < s.effectiveFromMs || o.asOfMs >= s.effectiveUntilMs ||
        asOf < o.asOfMs) return Reject("FX_LEDGER_OPENING_INVALID");
    if (maximumRecords == 0 || maximumRecords > 4096 || events.size() > maximumRecords)
        return Reject("FX_LEDGER_RECORD_LIMIT");
    // Non-allocating preflight over bounded fields precedes any body/map copies.
    for (std::size_t i = 0; i < events.size(); ++i)
        if (const char* reason = EventError(events[i],s,o,asOf)) return Reject(reason,i);
    Digest digest;
    digest.Field(Version()); digest.Field(s.venue); digest.Field(s.instrument);
    digest.Field(s.baseCurrency); digest.Field(s.quoteCurrency); digest.Field(s.revision);
    digest.Number(s.quantityStepRaw); digest.Number(s.priceTickRaw);
    digest.Number(s.minimumQuantityRaw); digest.Number(s.maximumQuantityRaw);
    digest.Number(s.effectiveFromMs); digest.Number(s.effectiveUntilMs);
    digest.Field(o.bookId); digest.Field(o.instrumentRevision); digest.Number(o.baseBalanceRaw);
    digest.Number(o.quoteBalanceRaw); digest.Number(o.asOfMs); digest.Number(asOf);
    SimulatorFxProjection p;
    p.baseBalanceRaw = o.baseBalanceRaw; p.quoteBalanceRaw = o.quoteBalanceRaw;
    p.lastRecordedAtMs = o.asOfMs; p.asOfMs = asOf;
    std::map<std::string,std::size_t> seen;
    struct Execution { std::uint64_t eventTime; bool commission = false; };
    std::map<std::string,Execution> fills;
    for (std::size_t i = 0; i < events.size(); ++i)
    {
        const auto& e = events[i]; const auto prior = seen.find(e.eventId);
        if (prior != seen.end())
        {
            if (!SameEvent(e,events[prior->second])) return Reject("FX_LEDGER_EVENT_CONFLICT",i);
            ++p.duplicates; continue; // Exact replay never advances the canonical cut.
        }
        if (e.sequence != p.lastSequence + 1) return Reject("FX_LEDGER_SEQUENCE_GAP",i);
        if (e.recordedAtMs < p.lastRecordedAtMs) return Reject("FX_LEDGER_RECORDED_TIME_REGRESSION",i);
        if (e.kind == SimulatorFxEventKind::Fill)
        {
            if (fills.find(e.executionId) != fills.end()) return Reject("FX_LEDGER_EXECUTION_CONFLICT",i);
            // Whole base units make quote micro-units exact; no hidden rounding.
            const auto units = e.quantityRaw / Scale;
            if (units > Maximum / e.priceRaw) return Reject("FX_LEDGER_NOTIONAL_RANGE",i);
            const auto notional = units * e.priceRaw;
            const auto sign = e.side == SimulatorFxSide::Buy ? 1 : -1;
            if (!Add(p.baseBalanceRaw,sign*e.quantityRaw,p.baseBalanceRaw) ||
                !Add(p.quoteBalanceRaw,-sign*notional,p.quoteBalanceRaw) ||
                !Add(p.netBaseTradeRaw,sign*e.quantityRaw,p.netBaseTradeRaw) ||
                !Add(p.netQuoteTradeRaw,-sign*notional,p.netQuoteTradeRaw))
                return Reject("FX_LEDGER_BALANCE_RANGE",i);
            if (p.baseBalanceRaw < 0 || p.quoteBalanceRaw < 0) return Reject("FX_LEDGER_INSUFFICIENT_FUNDS",i);
            fills.emplace(e.executionId,Execution{e.eventTimeMs,false}); ++p.fills;
        }
        else
        {
            const auto fill = fills.find(e.executionId);
            if (fill == fills.end()) return Reject("FX_LEDGER_UNKNOWN_EXECUTION",i);
            if (fill->second.commission) return Reject("FX_LEDGER_COMMISSION_CONFLICT",i);
            if (e.eventTimeMs < fill->second.eventTime) return Reject("FX_LEDGER_COMMISSION_TIME_INVALID",i);
            if (!Add(p.quoteBalanceRaw,-e.commissionRaw,p.quoteBalanceRaw) ||
                !Add(p.commissionsRaw,e.commissionRaw,p.commissionsRaw)) return Reject("FX_LEDGER_BALANCE_RANGE",i);
            if (p.quoteBalanceRaw < 0) return Reject("FX_LEDGER_INSUFFICIENT_FUNDS",i);
            fill->second.commission = true; ++p.commissions;
        }
        seen.emplace(e.eventId,i); digest.Event(e);
        p.lastSequence = e.sequence; p.lastRecordedAtMs = e.recordedAtMs;
    }
    p.feesComplete = p.fills == p.commissions;
    digest.Number(2); digest.Number(p.baseBalanceRaw); digest.Number(p.quoteBalanceRaw);
    digest.Number(static_cast<std::uint64_t>(p.netBaseTradeRaw)); digest.Number(static_cast<std::uint64_t>(p.netQuoteTradeRaw));
    digest.Number(p.commissionsRaw); digest.Number(p.lastSequence); digest.Number(p.lastRecordedAtMs);
    digest.Number(p.fills); digest.Number(p.commissions); digest.Number(p.feesComplete ? 1 : 0);
    p.digest = digest.Finish();
    if (p.digest.empty()) return Reject("FX_LEDGER_DIGEST_FAILED");
    p.bookId = o.bookId; p.instrumentRevision = s.revision;
    SimulatorFxReplayResult result; result.projection = std::move(p);
    result.reasonCode = "FX_LEDGER_PROJECTED"; result.accepted = true;
    static_assert(std::is_nothrow_move_constructible<SimulatorFxReplayResult>::value,"Result return must not allocate");
    return result;
}

SimulatorFxReconciliation SimulatorFxAccounting::Reconcile(const SimulatorFxInstrument& s,
    const SimulatorFxOpening& o, const std::vector<SimulatorFxEvent>& events, std::uint64_t asOf,
    const SimulatorFxObservation& observed, std::size_t maximumRecords)
{
    const auto replay = Replay(s,o,events,asOf,maximumRecords);
    SimulatorFxReconciliation r;
    if (!replay.accepted) { r.reasonCode = replay.reasonCode; return r; }
    const auto& p = replay.projection;
    if (!p.feesComplete) { r.reasonCode = "FX_LEDGER_COMMISSION_PENDING"; return r; }
    if (observed.bookId != o.bookId || observed.instrumentRevision != s.revision ||
        observed.asOfMs != asOf || observed.lastSequence != p.lastSequence)
    { r.reasonCode = "FX_LEDGER_OBSERVATION_CUT_MISMATCH"; return r; }
    if (observed.baseBalanceRaw != p.baseBalanceRaw || observed.quoteBalanceRaw != p.quoteBalanceRaw ||
        observed.commissionsRaw != p.commissionsRaw || observed.fills != p.fills || observed.commissions != p.commissions)
    { r.reasonCode = "FX_LEDGER_RECONCILIATION_MISMATCH"; return r; }
    r.projectionDigest = p.digest; r.reasonCode = "FX_LEDGER_RECONCILED"; r.matched = true;
    return r;
}
