#include "ib_authoritative_account_position_consumer.h"

#include "ib_contract_identity.h"

#include <cmath>
#include <cstdlib>

IBAuthoritativeAccountPositionConsumer::IBAuthoritativeAccountPositionConsumer(
    AuthoritativeTradingSnapshotStore& store,
    const std::string& configuredAccount)
    : m_store(store), m_configuredAccount(configuredAccount)
{
}

bool IBAuthoritativeAccountPositionConsumer::ConfigureAccount(
    const std::string& configuredAccount)
{
    if (m_accountGeneration != 0 || m_positionGeneration != 0) return false;
    m_configuredAccount = configuredAccount;
    return true;
}

void IBAuthoritativeAccountPositionConsumer::BeginAccount(std::uint64_t generation)
{
    m_accountGeneration = generation;
    m_accountRejected = false;
    m_accountRejectReason.clear();
    m_accountCurrency.clear();
    m_accountMetrics.clear();
    m_accountRawValues.clear();
}

IBAuthoritativeSnapshotConsumeStatus IBAuthoritativeAccountPositionConsumer::ConsumeAccountValue(
    const IBEvent& event)
{
    if (m_accountGeneration == 0) return IBAuthoritativeSnapshotConsumeStatus::Ignored;
    if (!event.account.empty() && !m_configuredAccount.empty() && event.account != m_configuredAccount)
        return IBAuthoritativeSnapshotConsumeStatus::Ignored;

    m_accountRawValues[event.key] = event.value;
    std::string metricKey = event.key;
    const std::string::size_type separator = metricKey.find(':');
    if (separator != std::string::npos)
    {
        if (separator + 1 < metricKey.size()) m_accountCurrency = metricKey.substr(separator + 1);
        metricKey = metricKey.substr(0, separator);
    }

    char* end = nullptr;
    const double parsed = std::strtod(event.value.c_str(), &end);
    if (end == event.value.c_str() || end == nullptr || *end != '\0')
        return IBAuthoritativeSnapshotConsumeStatus::Ignored;
    if (!std::isfinite(parsed))
    {
        m_accountRejected = true;
        m_accountRejectReason = "NON_FINITE_ACCOUNT_VALUE";
        return IBAuthoritativeSnapshotConsumeStatus::Rejected;
    }
    m_accountMetrics[metricKey] = parsed;
    return IBAuthoritativeSnapshotConsumeStatus::Applied;
}

IBAuthoritativeAccountCompletion IBAuthoritativeAccountPositionConsumer::CompleteAccount(
    std::uint64_t generation,
    std::uint64_t observedAtMs)
{
    IBAuthoritativeAccountCompletion result;
    if (generation == 0 || generation != m_accountGeneration)
    {
        result.reasonCode = "STALE_ACCOUNT_GENERATION";
        return result;
    }
    result.metrics = m_accountMetrics;
    result.rawValues = m_accountRawValues;
    result.account.account = m_configuredAccount;
    result.account.currency = m_accountCurrency.empty() ? "USD" : m_accountCurrency;
    const auto assignMetric = [&](const char* key, bool& present, double& value) {
        const std::unordered_map<std::string, double>::const_iterator found = m_accountMetrics.find(key);
        if (found == m_accountMetrics.end()) return;
        present = true;
        value = found->second;
    };
    assignMetric("NetLiquidation", result.account.hasNetLiquidation, result.account.netLiquidation);
    assignMetric("AvailableFunds", result.account.hasAvailableFunds, result.account.availableFunds);
    assignMetric("BuyingPower", result.account.hasBuyingPower, result.account.buyingPower);
    assignMetric("TotalCashValue", result.account.hasCash, result.account.cash);
    assignMetric("MaintMarginReq", result.account.hasMaintenanceMargin, result.account.maintenanceMargin);
    assignMetric("RealizedPnL", result.account.hasRealizedPnl, result.account.realizedPnl);
    assignMetric("UnrealizedPnL", result.account.hasUnrealizedPnl, result.account.unrealizedPnl);

    if (m_accountRejected)
        result.reasonCode = m_accountRejectReason;
    else if (m_accountMetrics.empty())
        result.reasonCode = "ACCOUNT_SUMMARY_EMPTY";
    else
    {
        const AuthoritativeSnapshotWriteResult write = m_store.ReplaceAccounts(
            std::vector<AuthoritativeAccount>(1, result.account), observedAtMs, "ib.account_summary");
        result.accepted = write.accepted;
        result.reasonCode = write.reasonCode;
    }
    m_accountGeneration = 0;
    return result;
}

void IBAuthoritativeAccountPositionConsumer::AbortAccount(std::uint64_t generation)
{
    if (generation != 0 && generation == m_accountGeneration) BeginAccount(0);
}

void IBAuthoritativeAccountPositionConsumer::BeginPositions(std::uint64_t generation)
{
    m_positionGeneration = generation;
    m_positionsRejected = false;
    m_positionsRejectReason.clear();
    m_positions.clear();
}

IBAuthoritativeSnapshotConsumeStatus IBAuthoritativeAccountPositionConsumer::ConsumePosition(
    const IBEvent& event)
{
    if (m_positionGeneration == 0) return IBAuthoritativeSnapshotConsumeStatus::Ignored;
    const std::string account = event.account.empty() ? m_configuredAccount : event.account;
    if (!m_configuredAccount.empty() && account != m_configuredAccount)
        return IBAuthoritativeSnapshotConsumeStatus::Ignored;
    if (!std::isfinite(event.number) || !std::isfinite(event.number2))
    {
        m_positionsRejected = true;
        m_positionsRejectReason = "NON_FINITE_POSITION_VALUE";
        return IBAuthoritativeSnapshotConsumeStatus::Rejected;
    }
    const std::string instrument = BuildIBAuthoritativeInstrumentIdentity(event.contract, event.key);
    if (instrument.empty())
    {
        m_positionsRejected = true;
        m_positionsRejectReason = "POSITION_CONTRACT_IDENTITY_REQUIRED";
        return IBAuthoritativeSnapshotConsumeStatus::Rejected;
    }
    AuthoritativePosition position;
    position.account = account;
    position.instrument = instrument;
    position.quantity = event.number;
    position.averageCost = event.number2;
    m_positions[instrument] = position;
    return IBAuthoritativeSnapshotConsumeStatus::Applied;
}

IBAuthoritativePositionCompletion IBAuthoritativeAccountPositionConsumer::CompletePositions(
    std::uint64_t generation,
    std::uint64_t observedAtMs)
{
    IBAuthoritativePositionCompletion result;
    if (generation == 0 || generation != m_positionGeneration)
    {
        result.reasonCode = "STALE_POSITION_GENERATION";
        return result;
    }
    for (std::map<std::string, AuthoritativePosition>::const_iterator it = m_positions.begin();
         it != m_positions.end(); ++it)
    {
        result.positions.push_back(it->second);
        result.quantities[it->first] = it->second.quantity;
    }
    if (m_positionsRejected)
        result.reasonCode = m_positionsRejectReason;
    else
    {
        const AuthoritativeSnapshotWriteResult write = m_store.ReplacePositions(
            result.positions, observedAtMs, "ib.positions");
        result.accepted = write.accepted;
        result.reasonCode = write.reasonCode;
    }
    m_positionGeneration = 0;
    return result;
}

void IBAuthoritativeAccountPositionConsumer::AbortPositions(std::uint64_t generation)
{
    if (generation != 0 && generation == m_positionGeneration) BeginPositions(0);
}
