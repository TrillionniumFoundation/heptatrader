#include "authoritative_trading_snapshot_store.h"

#include <algorithm>
#include <cmath>
#include <limits>
#include <set>

namespace {

const std::size_t kMaxInstrumentLength = 128;
const std::size_t kMaxAccountLength = 128;
const std::size_t kMaxVenueLength = 32;
const std::size_t kMaxCurrencyLength = 16;
const std::size_t kMaxSourceLength = 64;
const std::size_t kMaxReasonLength = 256;

bool IsBoundedPrintable(const std::string& value, std::size_t maxLength)
{
    if (value.empty() || value.size() > maxLength) return false;
    for (std::string::const_iterator it = value.begin(); it != value.end(); ++it)
    {
        const unsigned char c = static_cast<unsigned char>(*it);
        if (c < 0x20 || c > 0x7e) return false;
    }
    return true;
}

bool IsOptionalBoundedPrintable(const std::string& value, std::size_t maxLength)
{
    if (value.empty()) return true;
    return IsBoundedPrintable(value, maxLength);
}

bool IsFinite(double value)
{
    return std::isfinite(value) != 0;
}

bool IsZero(double value)
{
    return std::abs(value) <= std::numeric_limits<double>::epsilon();
}

bool ValidOptionalMetric(bool present, double value)
{
    return IsFinite(value) && (present || IsZero(value));
}

bool ValidOrderSide(AuthoritativeOrderSide side)
{
    switch (side)
    {
    case AuthoritativeOrderSide::Buy:
    case AuthoritativeOrderSide::Sell:
        return true;
    }
    return false;
}

bool ValidOrderType(AuthoritativeOrderType type)
{
    switch (type)
    {
    case AuthoritativeOrderType::Market:
    case AuthoritativeOrderType::Limit:
    case AuthoritativeOrderType::Stop:
    case AuthoritativeOrderType::StopLimit:
        return true;
    }
    return false;
}

bool ValidActiveOrderStatus(AuthoritativeActiveOrderStatus status)
{
    switch (status)
    {
    case AuthoritativeActiveOrderStatus::PendingSubmit:
    case AuthoritativeActiveOrderStatus::PreSubmitted:
    case AuthoritativeActiveOrderStatus::Submitted:
    case AuthoritativeActiveOrderStatus::PartiallyFilled:
    case AuthoritativeActiveOrderStatus::PendingCancel:
        return true;
    }
    return false;
}

template <typename T>
void RefreshRecordAvailability(T& record, std::uint64_t nowMs, std::uint64_t maxAgeMs)
{
    if (record.state.availability == AuthoritativeSnapshotAvailability::Missing) return;
    if (nowMs < record.state.updatedAtMs || nowMs - record.state.updatedAtMs > maxAgeMs)
        record.state.availability = AuthoritativeSnapshotAvailability::Stale;
    else
        record.state.availability = AuthoritativeSnapshotAvailability::Fresh;
}

template <typename MapType>
std::size_t RefreshMapAvailability(MapType& values,
                                   std::uint64_t nowMs,
                                   std::uint64_t maxAgeMs)
{
    std::size_t stale = 0;
    for (typename MapType::iterator it = values.begin(); it != values.end(); ++it)
    {
        RefreshRecordAvailability(it->second, nowMs, maxAgeMs);
        if (it->second.state.availability == AuthoritativeSnapshotAvailability::Stale) ++stale;
    }
    return stale;
}

AuthoritativeSnapshotDomainState MakeDomainState(bool touched,
                                                  bool complete,
                                                  std::uint64_t lastUpdatedAtMs,
                                                  std::uint64_t lastUpdatedVersion,
                                                  std::size_t recordCount,
                                                  std::size_t staleRecordCount,
                                                  std::uint64_t nowMs,
                                                  std::uint64_t maxAgeMs)
{
    AuthoritativeSnapshotDomainState state;
    state.complete = complete;
    state.lastUpdatedAtMs = lastUpdatedAtMs;
    state.lastUpdatedVersion = lastUpdatedVersion;
    state.recordCount = recordCount;
    state.staleRecordCount = staleRecordCount;
    if (!touched)
        state.availability = AuthoritativeSnapshotAvailability::Missing;
    else if (nowMs < lastUpdatedAtMs || nowMs - lastUpdatedAtMs > maxAgeMs)
        state.availability = AuthoritativeSnapshotAvailability::Stale;
    else
        state.availability = AuthoritativeSnapshotAvailability::Fresh;
    return state;
}

} // namespace

bool AuthoritativePositionKey::operator<(const AuthoritativePositionKey& other) const
{
    if (account != other.account) return account < other.account;
    return instrument < other.instrument;
}

bool AuthoritativeOrderKey::operator<(const AuthoritativeOrderKey& other) const
{
    if (venue != other.venue) return venue < other.venue;
    return orderId < other.orderId;
}

AuthoritativeTradingSnapshotStore::AuthoritativeTradingSnapshotStore()
    : m_snapshotVersion(0)
{
}

AuthoritativeSnapshotWriteResult AuthoritativeTradingSnapshotStore::SetExecutionState(
    bool connected,
    bool authoritative,
    std::uint64_t observedAtMs,
    const std::string& source,
    const std::string& reason)
{
    std::string validationReason;
    if (!ValidateObservation(observedAtMs, source, validationReason) ||
        !IsOptionalBoundedPrintable(reason, kMaxReasonLength) || (authoritative && !connected))
    {
        if (validationReason.empty()) validationReason = authoritative && !connected ?
            "AUTHORITATIVE_REQUIRES_CONNECTION" : "INVALID_EXECUTION_STATE_REASON";
        std::lock_guard<std::mutex> lock(m_mutex);
        return RejectLocked(validationReason);
    }
    std::lock_guard<std::mutex> lock(m_mutex);
    if (m_executionState.updatedAtMs != 0 && observedAtMs < m_executionState.updatedAtMs)
        return RejectLocked("OBSERVATION_TIME_REGRESSION");
    std::uint64_t version = 0;
    if (!NextVersionLocked(version, validationReason)) return RejectLocked(validationReason);
    m_executionState.connected = connected;
    m_executionState.authoritative = authoritative;
    m_executionState.updatedAtMs = observedAtMs;
    m_executionState.updatedAtVersion = version;
    m_executionState.source = source;
    m_executionState.reason = reason;
    m_snapshotVersion = version;
    AuthoritativeSnapshotWriteResult result;
    result.accepted = true;
    result.snapshotVersion = version;
    return result;
}

AuthoritativeSnapshotAvailability AuthoritativeTradingSnapshotStore::Classify(
    std::uint64_t updatedAtMs,
    std::uint64_t nowMs,
    std::uint64_t maxAgeMs)
{
    if (updatedAtMs == 0) return AuthoritativeSnapshotAvailability::Missing;
    if (nowMs < updatedAtMs || nowMs - updatedAtMs > maxAgeMs)
        return AuthoritativeSnapshotAvailability::Stale;
    return AuthoritativeSnapshotAvailability::Fresh;
}

bool AuthoritativeTradingSnapshotStore::ValidateObservation(std::uint64_t observedAtMs,
                                                             const std::string& source,
                                                             std::string& reason)
{
    if (observedAtMs == 0)
    {
        reason = "OBSERVATION_TIME_REQUIRED";
        return false;
    }
    if (!IsBoundedPrintable(source, kMaxSourceLength))
    {
        reason = "INVALID_SOURCE";
        return false;
    }
    return true;
}

bool AuthoritativeTradingSnapshotStore::ValidateQuote(const AuthoritativeQuote& quote,
                                                       std::string& reason)
{
    if (!IsBoundedPrintable(quote.instrument, kMaxInstrumentLength))
    {
        reason = "INVALID_INSTRUMENT";
        return false;
    }
    if (!IsFinite(quote.bid) || !IsFinite(quote.ask) || !IsFinite(quote.last) ||
        !IsFinite(quote.bidSize) || !IsFinite(quote.askSize))
    {
        reason = "NONFINITE_QUOTE_FIELD";
        return false;
    }
    // Do not impose a positive-price assumption here: exchange-traded futures
    // can legitimately trade below zero. Venue/risk policy owns price bands.
    if (quote.ask < quote.bid || quote.bidSize < 0.0 || quote.askSize < 0.0)
    {
        reason = "INVALID_QUOTE_VALUE";
        return false;
    }
    return true;
}

bool AuthoritativeTradingSnapshotStore::ValidateAccount(const AuthoritativeAccount& account,
                                                         std::string& reason)
{
    if (!IsBoundedPrintable(account.account, kMaxAccountLength))
    {
        reason = "INVALID_ACCOUNT";
        return false;
    }
    if (!IsBoundedPrintable(account.currency, kMaxCurrencyLength))
    {
        reason = "INVALID_CURRENCY";
        return false;
    }
    if (!account.hasNetLiquidation && !account.hasAvailableFunds && !account.hasBuyingPower &&
        !account.hasCash && !account.hasMaintenanceMargin && !account.hasRealizedPnl &&
        !account.hasUnrealizedPnl)
    {
        reason = "ACCOUNT_METRICS_MISSING";
        return false;
    }
    if (!ValidOptionalMetric(account.hasNetLiquidation, account.netLiquidation) ||
        !ValidOptionalMetric(account.hasAvailableFunds, account.availableFunds) ||
        !ValidOptionalMetric(account.hasBuyingPower, account.buyingPower) ||
        !ValidOptionalMetric(account.hasCash, account.cash) ||
        !ValidOptionalMetric(account.hasMaintenanceMargin, account.maintenanceMargin) ||
        !ValidOptionalMetric(account.hasRealizedPnl, account.realizedPnl) ||
        !ValidOptionalMetric(account.hasUnrealizedPnl, account.unrealizedPnl))
    {
        reason = "INVALID_ACCOUNT_METRIC";
        return false;
    }
    return true;
}

bool AuthoritativeTradingSnapshotStore::ValidatePosition(const AuthoritativePosition& position,
                                                          std::string& reason)
{
    if (!IsBoundedPrintable(position.account, kMaxAccountLength))
    {
        reason = "INVALID_ACCOUNT";
        return false;
    }
    if (!IsBoundedPrintable(position.instrument, kMaxInstrumentLength))
    {
        reason = "INVALID_INSTRUMENT";
        return false;
    }
    if (!IsFinite(position.quantity) || !IsFinite(position.averageCost))
    {
        reason = "NONFINITE_POSITION_FIELD";
        return false;
    }
    return true;
}

bool AuthoritativeTradingSnapshotStore::ValidateActiveOrder(const AuthoritativeActiveOrder& order,
                                                             std::string& reason)
{
    if (!IsBoundedPrintable(order.venue, kMaxVenueLength))
    {
        reason = "INVALID_VENUE";
        return false;
    }
    if (order.orderId <= 0)
    {
        reason = "INVALID_ORDER_ID";
        return false;
    }
    if (!IsBoundedPrintable(order.account, kMaxAccountLength))
    {
        reason = "INVALID_ACCOUNT";
        return false;
    }
    if (!IsBoundedPrintable(order.instrument, kMaxInstrumentLength))
    {
        reason = "INVALID_INSTRUMENT";
        return false;
    }
    if (!ValidOrderSide(order.side) || !ValidOrderType(order.type) ||
        !ValidActiveOrderStatus(order.status))
    {
        reason = "INVALID_ORDER_ENUM";
        return false;
    }
    if (!IsFinite(order.totalQuantity) || !IsFinite(order.filledQuantity) ||
        !IsFinite(order.remainingQuantity) || !IsFinite(order.limitPrice) ||
        !IsFinite(order.stopPrice))
    {
        reason = "NONFINITE_ORDER_FIELD";
        return false;
    }
    if (order.totalQuantity <= 0.0 || order.filledQuantity < 0.0 ||
        order.remainingQuantity <= 0.0 || order.filledQuantity > order.totalQuantity ||
        order.remainingQuantity > order.totalQuantity)
    {
        reason = "INVALID_ORDER_QUANTITY";
        return false;
    }
    const double quantityTolerance = std::max(1.0, order.totalQuantity) * 1e-9;
    if (std::abs(order.filledQuantity + order.remainingQuantity - order.totalQuantity) > quantityTolerance)
    {
        reason = "INCONSISTENT_ORDER_QUANTITY";
        return false;
    }
    const bool needsLimit = order.type == AuthoritativeOrderType::Limit ||
                            order.type == AuthoritativeOrderType::StopLimit;
    const bool needsStop = order.type == AuthoritativeOrderType::Stop ||
                           order.type == AuthoritativeOrderType::StopLimit;
    if ((needsLimit && IsZero(order.limitPrice)) || (!needsLimit && !IsZero(order.limitPrice)) ||
        (needsStop && IsZero(order.stopPrice)) || (!needsStop && !IsZero(order.stopPrice)))
    {
        reason = "INVALID_ORDER_PRICE";
        return false;
    }
    return true;
}

AuthoritativeSnapshotWriteResult AuthoritativeTradingSnapshotStore::RejectLocked(
    const std::string& reason) const
{
    AuthoritativeSnapshotWriteResult result;
    result.snapshotVersion = m_snapshotVersion;
    result.reasonCode = reason;
    return result;
}

bool AuthoritativeTradingSnapshotStore::NextVersionLocked(std::uint64_t& nextVersion,
                                                           std::string& reason) const
{
    if (m_snapshotVersion == std::numeric_limits<std::uint64_t>::max())
    {
        reason = "SNAPSHOT_VERSION_EXHAUSTED";
        return false;
    }
    nextVersion = m_snapshotVersion + 1;
    return true;
}

void AuthoritativeTradingSnapshotStore::SetRecordState(AuthoritativeSnapshotRecordState& state,
                                                        std::uint64_t observedAtMs,
                                                        std::uint64_t version,
                                                        const std::string& source)
{
    state.availability = AuthoritativeSnapshotAvailability::Fresh;
    state.updatedAtMs = observedAtMs;
    state.updatedAtVersion = version;
    state.source = source;
}

void AuthoritativeTradingSnapshotStore::TouchDomain(DomainTracker& domain,
                                                     std::uint64_t observedAtMs,
                                                     std::uint64_t version,
                                                     bool complete)
{
    const std::uint64_t latestObservedAtMs = domain.touched ?
        std::max(domain.lastUpdatedAtMs, observedAtMs) : observedAtMs;
    domain.touched = true;
    domain.complete = complete;
    domain.lastUpdatedAtMs = latestObservedAtMs;
    domain.lastUpdatedVersion = version;
}

AuthoritativeSnapshotWriteResult AuthoritativeTradingSnapshotStore::UpsertQuote(
    const AuthoritativeQuote& quote,
    std::uint64_t observedAtMs,
    const std::string& source)
{
    std::string reason;
    if (!ValidateObservation(observedAtMs, source, reason) || !ValidateQuote(quote, reason))
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        return RejectLocked(reason);
    }
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::map<std::string, AuthoritativeQuoteRecord>::const_iterator existing = m_quotes.find(quote.instrument);
    if (existing != m_quotes.end() && observedAtMs < existing->second.state.updatedAtMs)
        return RejectLocked("OBSERVATION_TIME_REGRESSION");
    std::uint64_t version = 0;
    if (!NextVersionLocked(version, reason)) return RejectLocked(reason);
    AuthoritativeQuoteRecord record;
    record.value = quote;
    SetRecordState(record.state, observedAtMs, version, source);
    m_quotes[quote.instrument] = record;
    m_snapshotVersion = version;
    TouchDomain(m_quotesDomain, observedAtMs, version, m_quotesDomain.complete);
    AuthoritativeSnapshotWriteResult result;
    result.accepted = true;
    result.snapshotVersion = version;
    return result;
}

AuthoritativeSnapshotWriteResult AuthoritativeTradingSnapshotStore::UpsertAccount(
    const AuthoritativeAccount& account,
    std::uint64_t observedAtMs,
    const std::string& source)
{
    std::string reason;
    if (!ValidateObservation(observedAtMs, source, reason) || !ValidateAccount(account, reason))
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        return RejectLocked(reason);
    }
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::map<std::string, AuthoritativeAccountRecord>::const_iterator existing = m_accounts.find(account.account);
    if (existing != m_accounts.end() && observedAtMs < existing->second.state.updatedAtMs)
        return RejectLocked("OBSERVATION_TIME_REGRESSION");
    std::uint64_t version = 0;
    if (!NextVersionLocked(version, reason)) return RejectLocked(reason);
    AuthoritativeAccountRecord record;
    record.value = account;
    SetRecordState(record.state, observedAtMs, version, source);
    m_accounts[account.account] = record;
    m_snapshotVersion = version;
    TouchDomain(m_accountsDomain, observedAtMs, version, m_accountsDomain.complete);
    AuthoritativeSnapshotWriteResult result;
    result.accepted = true;
    result.snapshotVersion = version;
    return result;
}

AuthoritativeSnapshotWriteResult AuthoritativeTradingSnapshotStore::UpsertPosition(
    const AuthoritativePosition& position,
    std::uint64_t observedAtMs,
    const std::string& source)
{
    std::string reason;
    if (!ValidateObservation(observedAtMs, source, reason) || !ValidatePosition(position, reason))
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        return RejectLocked(reason);
    }
    AuthoritativePositionKey key;
    key.account = position.account;
    key.instrument = position.instrument;
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::map<AuthoritativePositionKey, AuthoritativePositionRecord>::const_iterator existing = m_positions.find(key);
    if (existing != m_positions.end() && observedAtMs < existing->second.state.updatedAtMs)
        return RejectLocked("OBSERVATION_TIME_REGRESSION");
    std::uint64_t version = 0;
    if (!NextVersionLocked(version, reason)) return RejectLocked(reason);
    AuthoritativePositionRecord record;
    record.value = position;
    SetRecordState(record.state, observedAtMs, version, source);
    m_positions[key] = record;
    m_snapshotVersion = version;
    TouchDomain(m_positionsDomain, observedAtMs, version, m_positionsDomain.complete);
    AuthoritativeSnapshotWriteResult result;
    result.accepted = true;
    result.snapshotVersion = version;
    return result;
}

AuthoritativeSnapshotWriteResult AuthoritativeTradingSnapshotStore::UpsertActiveOrder(
    const AuthoritativeActiveOrder& order,
    std::uint64_t observedAtMs,
    const std::string& source)
{
    std::string reason;
    if (!ValidateObservation(observedAtMs, source, reason) || !ValidateActiveOrder(order, reason))
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        return RejectLocked(reason);
    }
    AuthoritativeOrderKey key;
    key.venue = order.venue;
    key.orderId = order.orderId;
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::map<AuthoritativeOrderKey, AuthoritativeActiveOrderRecord>::const_iterator existing = m_activeOrders.find(key);
    if (existing != m_activeOrders.end() && observedAtMs < existing->second.state.updatedAtMs)
        return RejectLocked("OBSERVATION_TIME_REGRESSION");
    std::uint64_t version = 0;
    if (!NextVersionLocked(version, reason)) return RejectLocked(reason);
    AuthoritativeActiveOrderRecord record;
    record.value = order;
    SetRecordState(record.state, observedAtMs, version, source);
    m_activeOrders[key] = record;
    m_snapshotVersion = version;
    TouchDomain(m_activeOrdersDomain, observedAtMs, version, m_activeOrdersDomain.complete);
    AuthoritativeSnapshotWriteResult result;
    result.accepted = true;
    result.snapshotVersion = version;
    return result;
}

AuthoritativeSnapshotWriteResult AuthoritativeTradingSnapshotStore::ReplaceQuotes(
    const std::vector<AuthoritativeQuote>& quotes,
    std::uint64_t observedAtMs,
    const std::string& source)
{
    std::string reason;
    if (!ValidateObservation(observedAtMs, source, reason))
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        return RejectLocked(reason);
    }
    std::map<std::string, AuthoritativeQuoteRecord> replacement;
    for (std::size_t i = 0; i < quotes.size(); ++i)
    {
        if (!ValidateQuote(quotes[i], reason))
        {
            std::lock_guard<std::mutex> lock(m_mutex);
            return RejectLocked(reason);
        }
        if (replacement.find(quotes[i].instrument) != replacement.end())
        {
            std::lock_guard<std::mutex> lock(m_mutex);
            return RejectLocked("DUPLICATE_QUOTE_KEY");
        }
        AuthoritativeQuoteRecord record;
        record.value = quotes[i];
        replacement[quotes[i].instrument] = record;
    }
    std::lock_guard<std::mutex> lock(m_mutex);
    if (m_quotesDomain.touched && observedAtMs < m_quotesDomain.lastUpdatedAtMs)
        return RejectLocked("OBSERVATION_TIME_REGRESSION");
    std::uint64_t version = 0;
    if (!NextVersionLocked(version, reason)) return RejectLocked(reason);
    for (std::map<std::string, AuthoritativeQuoteRecord>::iterator it = replacement.begin(); it != replacement.end(); ++it)
        SetRecordState(it->second.state, observedAtMs, version, source);
    m_quotes.swap(replacement);
    m_snapshotVersion = version;
    TouchDomain(m_quotesDomain, observedAtMs, version, true);
    AuthoritativeSnapshotWriteResult result;
    result.accepted = true;
    result.snapshotVersion = version;
    return result;
}

AuthoritativeSnapshotWriteResult AuthoritativeTradingSnapshotStore::InvalidateQuotes(
    std::uint64_t observedAtMs,
    const std::string& source)
{
    std::string reason;
    if (!ValidateObservation(observedAtMs, source, reason))
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        return RejectLocked(reason);
    }
    std::lock_guard<std::mutex> lock(m_mutex);
    if (m_quotesDomain.touched && observedAtMs < m_quotesDomain.lastUpdatedAtMs)
        return RejectLocked("OBSERVATION_TIME_REGRESSION");
    std::uint64_t version = 0;
    if (!NextVersionLocked(version, reason)) return RejectLocked(reason);
    m_snapshotVersion = version;
    TouchDomain(m_quotesDomain, observedAtMs, version, false);
    AuthoritativeSnapshotWriteResult result;
    result.accepted = true;
    result.snapshotVersion = version;
    return result;
}

AuthoritativeSnapshotWriteResult AuthoritativeTradingSnapshotStore::ReplaceAccounts(
    const std::vector<AuthoritativeAccount>& accounts,
    std::uint64_t observedAtMs,
    const std::string& source)
{
    std::string reason;
    if (!ValidateObservation(observedAtMs, source, reason))
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        return RejectLocked(reason);
    }
    std::map<std::string, AuthoritativeAccountRecord> replacement;
    for (std::size_t i = 0; i < accounts.size(); ++i)
    {
        if (!ValidateAccount(accounts[i], reason))
        {
            std::lock_guard<std::mutex> lock(m_mutex);
            return RejectLocked(reason);
        }
        if (replacement.find(accounts[i].account) != replacement.end())
        {
            std::lock_guard<std::mutex> lock(m_mutex);
            return RejectLocked("DUPLICATE_ACCOUNT_KEY");
        }
        AuthoritativeAccountRecord record;
        record.value = accounts[i];
        replacement[accounts[i].account] = record;
    }
    std::lock_guard<std::mutex> lock(m_mutex);
    if (m_accountsDomain.touched && observedAtMs < m_accountsDomain.lastUpdatedAtMs)
        return RejectLocked("OBSERVATION_TIME_REGRESSION");
    std::uint64_t version = 0;
    if (!NextVersionLocked(version, reason)) return RejectLocked(reason);
    for (std::map<std::string, AuthoritativeAccountRecord>::iterator it = replacement.begin(); it != replacement.end(); ++it)
        SetRecordState(it->second.state, observedAtMs, version, source);
    m_accounts.swap(replacement);
    m_snapshotVersion = version;
    TouchDomain(m_accountsDomain, observedAtMs, version, true);
    AuthoritativeSnapshotWriteResult result;
    result.accepted = true;
    result.snapshotVersion = version;
    return result;
}

AuthoritativeSnapshotWriteResult AuthoritativeTradingSnapshotStore::ReplacePositions(
    const std::vector<AuthoritativePosition>& positions,
    std::uint64_t observedAtMs,
    const std::string& source)
{
    std::string reason;
    if (!ValidateObservation(observedAtMs, source, reason))
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        return RejectLocked(reason);
    }
    std::map<AuthoritativePositionKey, AuthoritativePositionRecord> replacement;
    for (std::size_t i = 0; i < positions.size(); ++i)
    {
        if (!ValidatePosition(positions[i], reason))
        {
            std::lock_guard<std::mutex> lock(m_mutex);
            return RejectLocked(reason);
        }
        AuthoritativePositionKey key;
        key.account = positions[i].account;
        key.instrument = positions[i].instrument;
        if (replacement.find(key) != replacement.end())
        {
            std::lock_guard<std::mutex> lock(m_mutex);
            return RejectLocked("DUPLICATE_POSITION_KEY");
        }
        AuthoritativePositionRecord record;
        record.value = positions[i];
        replacement[key] = record;
    }
    std::lock_guard<std::mutex> lock(m_mutex);
    if (m_positionsDomain.touched && observedAtMs < m_positionsDomain.lastUpdatedAtMs)
        return RejectLocked("OBSERVATION_TIME_REGRESSION");
    std::uint64_t version = 0;
    if (!NextVersionLocked(version, reason)) return RejectLocked(reason);
    for (std::map<AuthoritativePositionKey, AuthoritativePositionRecord>::iterator it = replacement.begin(); it != replacement.end(); ++it)
        SetRecordState(it->second.state, observedAtMs, version, source);
    m_positions.swap(replacement);
    m_snapshotVersion = version;
    TouchDomain(m_positionsDomain, observedAtMs, version, true);
    AuthoritativeSnapshotWriteResult result;
    result.accepted = true;
    result.snapshotVersion = version;
    return result;
}

AuthoritativeSnapshotWriteResult AuthoritativeTradingSnapshotStore::ReplaceActiveOrders(
    const std::vector<AuthoritativeActiveOrder>& orders,
    std::uint64_t observedAtMs,
    const std::string& source)
{
    std::string reason;
    if (!ValidateObservation(observedAtMs, source, reason))
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        return RejectLocked(reason);
    }
    std::map<AuthoritativeOrderKey, AuthoritativeActiveOrderRecord> replacement;
    for (std::size_t i = 0; i < orders.size(); ++i)
    {
        if (!ValidateActiveOrder(orders[i], reason))
        {
            std::lock_guard<std::mutex> lock(m_mutex);
            return RejectLocked(reason);
        }
        AuthoritativeOrderKey key;
        key.venue = orders[i].venue;
        key.orderId = orders[i].orderId;
        if (replacement.find(key) != replacement.end())
        {
            std::lock_guard<std::mutex> lock(m_mutex);
            return RejectLocked("DUPLICATE_ORDER_KEY");
        }
        AuthoritativeActiveOrderRecord record;
        record.value = orders[i];
        replacement[key] = record;
    }
    std::lock_guard<std::mutex> lock(m_mutex);
    if (m_activeOrdersDomain.touched && observedAtMs < m_activeOrdersDomain.lastUpdatedAtMs)
        return RejectLocked("OBSERVATION_TIME_REGRESSION");
    std::uint64_t version = 0;
    if (!NextVersionLocked(version, reason)) return RejectLocked(reason);
    for (std::map<AuthoritativeOrderKey, AuthoritativeActiveOrderRecord>::iterator it = replacement.begin(); it != replacement.end(); ++it)
        SetRecordState(it->second.state, observedAtMs, version, source);
    m_activeOrders.swap(replacement);
    m_snapshotVersion = version;
    TouchDomain(m_activeOrdersDomain, observedAtMs, version, true);
    AuthoritativeSnapshotWriteResult result;
    result.accepted = true;
    result.snapshotVersion = version;
    return result;
}

AuthoritativeSnapshotWriteResult AuthoritativeTradingSnapshotStore::ErasePosition(
    const std::string& account,
    const std::string& instrument,
    std::uint64_t observedAtMs,
    const std::string& source)
{
    std::string reason;
    if (!ValidateObservation(observedAtMs, source, reason) ||
        !IsBoundedPrintable(account, kMaxAccountLength) ||
        !IsBoundedPrintable(instrument, kMaxInstrumentLength))
    {
        if (reason.empty()) reason = "INVALID_POSITION_KEY";
        std::lock_guard<std::mutex> lock(m_mutex);
        return RejectLocked(reason);
    }
    AuthoritativePositionKey key;
    key.account = account;
    key.instrument = instrument;
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::map<AuthoritativePositionKey, AuthoritativePositionRecord>::iterator found = m_positions.find(key);
    if (found == m_positions.end()) return RejectLocked("POSITION_NOT_FOUND");
    if (observedAtMs < found->second.state.updatedAtMs) return RejectLocked("OBSERVATION_TIME_REGRESSION");
    std::uint64_t version = 0;
    if (!NextVersionLocked(version, reason)) return RejectLocked(reason);
    m_positions.erase(found);
    m_snapshotVersion = version;
    TouchDomain(m_positionsDomain, observedAtMs, version, m_positionsDomain.complete);
    AuthoritativeSnapshotWriteResult result;
    result.accepted = true;
    result.snapshotVersion = version;
    return result;
}

AuthoritativeSnapshotWriteResult AuthoritativeTradingSnapshotStore::EraseActiveOrder(
    const std::string& venue,
    long orderId,
    std::uint64_t observedAtMs,
    const std::string& source)
{
    std::string reason;
    if (!ValidateObservation(observedAtMs, source, reason) ||
        !IsBoundedPrintable(venue, kMaxVenueLength) || orderId <= 0)
    {
        if (reason.empty()) reason = "INVALID_ORDER_KEY";
        std::lock_guard<std::mutex> lock(m_mutex);
        return RejectLocked(reason);
    }
    AuthoritativeOrderKey key;
    key.venue = venue;
    key.orderId = orderId;
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::map<AuthoritativeOrderKey, AuthoritativeActiveOrderRecord>::iterator found = m_activeOrders.find(key);
    if (found == m_activeOrders.end()) return RejectLocked("ORDER_NOT_FOUND");
    if (observedAtMs < found->second.state.updatedAtMs) return RejectLocked("OBSERVATION_TIME_REGRESSION");
    std::uint64_t version = 0;
    if (!NextVersionLocked(version, reason)) return RejectLocked(reason);
    m_activeOrders.erase(found);
    m_snapshotVersion = version;
    TouchDomain(m_activeOrdersDomain, observedAtMs, version, m_activeOrdersDomain.complete);
    AuthoritativeSnapshotWriteResult result;
    result.accepted = true;
    result.snapshotVersion = version;
    return result;
}

AuthoritativeQuoteRecord AuthoritativeTradingSnapshotStore::GetQuote(const std::string& instrument,
                                                                      std::uint64_t nowMs,
                                                                      std::uint64_t maxAgeMs) const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::map<std::string, AuthoritativeQuoteRecord>::const_iterator found = m_quotes.find(instrument);
    if (found == m_quotes.end()) return AuthoritativeQuoteRecord();
    AuthoritativeQuoteRecord record = found->second;
    record.state.availability = Classify(record.state.updatedAtMs, nowMs, maxAgeMs);
    return record;
}

AuthoritativeAccountRecord AuthoritativeTradingSnapshotStore::GetAccount(const std::string& account,
                                                                          std::uint64_t nowMs,
                                                                          std::uint64_t maxAgeMs) const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::map<std::string, AuthoritativeAccountRecord>::const_iterator found = m_accounts.find(account);
    if (found == m_accounts.end()) return AuthoritativeAccountRecord();
    AuthoritativeAccountRecord record = found->second;
    record.state.availability = Classify(record.state.updatedAtMs, nowMs, maxAgeMs);
    return record;
}

AuthoritativePositionRecord AuthoritativeTradingSnapshotStore::GetPosition(const std::string& account,
                                                                            const std::string& instrument,
                                                                            std::uint64_t nowMs,
                                                                            std::uint64_t maxAgeMs) const
{
    AuthoritativePositionKey key;
    key.account = account;
    key.instrument = instrument;
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::map<AuthoritativePositionKey, AuthoritativePositionRecord>::const_iterator found = m_positions.find(key);
    if (found == m_positions.end()) return AuthoritativePositionRecord();
    AuthoritativePositionRecord record = found->second;
    record.state.availability = Classify(record.state.updatedAtMs, nowMs, maxAgeMs);
    return record;
}

AuthoritativeActiveOrderRecord AuthoritativeTradingSnapshotStore::GetActiveOrder(const std::string& venue,
                                                                                  long orderId,
                                                                                  std::uint64_t nowMs,
                                                                                  std::uint64_t maxAgeMs) const
{
    AuthoritativeOrderKey key;
    key.venue = venue;
    key.orderId = orderId;
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::map<AuthoritativeOrderKey, AuthoritativeActiveOrderRecord>::const_iterator found = m_activeOrders.find(key);
    if (found == m_activeOrders.end()) return AuthoritativeActiveOrderRecord();
    AuthoritativeActiveOrderRecord record = found->second;
    record.state.availability = Classify(record.state.updatedAtMs, nowMs, maxAgeMs);
    return record;
}

AuthoritativeTradingSnapshot AuthoritativeTradingSnapshotStore::GetSnapshot(
    std::uint64_t nowMs,
    const AuthoritativeSnapshotFreshnessPolicy& policy) const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    AuthoritativeTradingSnapshot snapshot;
    snapshot.snapshotVersion = m_snapshotVersion;
    snapshot.capturedAtMs = nowMs;
    snapshot.executionState = m_executionState;
    snapshot.quotes = m_quotes;
    snapshot.accounts = m_accounts;
    snapshot.positions = m_positions;
    snapshot.activeOrders = m_activeOrders;

    const std::size_t staleQuotes = RefreshMapAvailability(snapshot.quotes, nowMs, policy.quoteMaxAgeMs);
    const std::size_t staleAccounts = RefreshMapAvailability(snapshot.accounts, nowMs, policy.accountMaxAgeMs);
    const std::size_t stalePositions = RefreshMapAvailability(snapshot.positions, nowMs, policy.positionMaxAgeMs);
    const std::size_t staleOrders = RefreshMapAvailability(snapshot.activeOrders, nowMs, policy.activeOrderMaxAgeMs);

    snapshot.quotesState = MakeDomainState(m_quotesDomain.touched, m_quotesDomain.complete,
        m_quotesDomain.lastUpdatedAtMs, m_quotesDomain.lastUpdatedVersion,
        snapshot.quotes.size(), staleQuotes, nowMs, policy.quoteMaxAgeMs);
    snapshot.accountsState = MakeDomainState(m_accountsDomain.touched, m_accountsDomain.complete,
        m_accountsDomain.lastUpdatedAtMs, m_accountsDomain.lastUpdatedVersion,
        snapshot.accounts.size(), staleAccounts, nowMs, policy.accountMaxAgeMs);
    snapshot.positionsState = MakeDomainState(m_positionsDomain.touched, m_positionsDomain.complete,
        m_positionsDomain.lastUpdatedAtMs, m_positionsDomain.lastUpdatedVersion,
        snapshot.positions.size(), stalePositions, nowMs, policy.positionMaxAgeMs);
    snapshot.activeOrdersState = MakeDomainState(m_activeOrdersDomain.touched, m_activeOrdersDomain.complete,
        m_activeOrdersDomain.lastUpdatedAtMs, m_activeOrdersDomain.lastUpdatedVersion,
        snapshot.activeOrders.size(), staleOrders, nowMs, policy.activeOrderMaxAgeMs);
    return snapshot;
}

std::uint64_t AuthoritativeTradingSnapshotStore::SnapshotVersion() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_snapshotVersion;
}
