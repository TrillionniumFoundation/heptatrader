#include "trading_tool_session_catalog.h"

#include "../state/ib_contract_identity.h"

bool TradingToolSessionContractCatalog::Register(
    const TradingToolSessionContractRegistration& registration,
    std::string& reason)
{
    if (registration.token.empty() || registration.agentId.empty() ||
        registration.sessionId.empty())
    {
        reason = "CATALOG_SESSION_IDENTITY_REQUIRED";
        return false;
    }
    for (std::map<std::string, InstrumentRef>::const_iterator it = registration.contracts.begin();
         it != registration.contracts.end(); ++it)
    {
        if (it->first.empty() ||
            BuildIBAuthoritativeInstrumentIdentity(it->second, it->first) != it->first)
        {
            reason = "CATALOG_CONTRACT_IDENTITY_MISMATCH";
            return false;
        }
    }

    {
        std::lock_guard<std::mutex> lock(m_mutex);
        if (m_sessions.find(registration.token) != m_sessions.end())
        {
            reason = "CATALOG_SESSION_EXISTS";
            return false;
        }
        for (std::map<std::string, InstrumentRef>::const_iterator it = registration.contracts.begin();
             it != registration.contracts.end(); ++it)
        {
            const std::map<std::string, TradingToolSessionContractRecord>::const_iterator existing =
                m_contracts.find(it->first);
            if (existing != m_contracts.end() && !SameContract(existing->second.contract, it->second))
            {
                reason = "CATALOG_CONTRACT_CONFLICT";
                return false;
            }
        }
        m_sessions[registration.token] = registration;
        RebuildContractsLocked();
        ++m_revision;
    }
    reason.clear();
    const TradingToolSessionContractCatalogSnapshot snapshot = GetSnapshot();
    Observer observer;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        observer = m_observer;
    }
    if (observer) observer(snapshot);
    return true;
}

bool TradingToolSessionContractCatalog::Revoke(const std::string& token)
{
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        if (m_sessions.erase(token) == 0) return false;
        RebuildContractsLocked();
        ++m_revision;
    }
    const TradingToolSessionContractCatalogSnapshot snapshot = GetSnapshot();
    Observer observer;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        observer = m_observer;
    }
    if (observer) observer(snapshot);
    return true;
}

bool TradingToolSessionContractCatalog::RevokeIfPresent(
    const std::string& token)
{
    bool changed = false;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        changed = m_sessions.erase(token) != 0;
        if (changed)
        {
            RebuildContractsLocked();
            ++m_revision;
        }
    }
    if (!changed) return true;
    const TradingToolSessionContractCatalogSnapshot snapshot = GetSnapshot();
    Observer observer;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        observer = m_observer;
    }
    if (observer) observer(snapshot);
    return true;
}

bool TradingToolSessionContractCatalog::Replace(
    const std::string& currentToken,
    const TradingToolSessionContractRegistration& registration,
    std::string& reason)
{
    if (currentToken.empty() || registration.token.empty() || registration.agentId.empty() ||
        registration.sessionId.empty())
    {
        reason = "CATALOG_SESSION_IDENTITY_REQUIRED";
        return false;
    }
    for (std::map<std::string, InstrumentRef>::const_iterator it = registration.contracts.begin();
         it != registration.contracts.end(); ++it)
    {
        if (it->first.empty() ||
            BuildIBAuthoritativeInstrumentIdentity(it->second, it->first) != it->first)
        {
            reason = "CATALOG_CONTRACT_IDENTITY_MISMATCH";
            return false;
        }
    }
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        const std::map<std::string, TradingToolSessionContractRegistration>::iterator current =
            m_sessions.find(currentToken);
        if (current == m_sessions.end())
        {
            reason = "CATALOG_SESSION_NOT_FOUND";
            return false;
        }
        if (registration.token != currentToken &&
            m_sessions.find(registration.token) != m_sessions.end())
        {
            reason = "CATALOG_SESSION_EXISTS";
            return false;
        }
        for (std::map<std::string, TradingToolSessionContractRegistration>::const_iterator session =
                 m_sessions.begin(); session != m_sessions.end(); ++session)
        {
            if (session->first == currentToken) continue;
            for (std::map<std::string, InstrumentRef>::const_iterator contract =
                     registration.contracts.begin(); contract != registration.contracts.end(); ++contract)
            {
                const std::map<std::string, InstrumentRef>::const_iterator existing =
                    session->second.contracts.find(contract->first);
                if (existing != session->second.contracts.end() &&
                    !SameContract(existing->second, contract->second))
                {
                    reason = "CATALOG_CONTRACT_CONFLICT";
                    return false;
                }
            }
        }
        m_sessions.erase(current);
        m_sessions[registration.token] = registration;
        RebuildContractsLocked();
        ++m_revision;
    }
    reason.clear();
    const TradingToolSessionContractCatalogSnapshot snapshot = GetSnapshot();
    Observer observer;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        observer = m_observer;
    }
    if (observer) observer(snapshot);
    return true;
}

TradingToolSessionContractCatalogSnapshot TradingToolSessionContractCatalog::GetSnapshot() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    TradingToolSessionContractCatalogSnapshot snapshot;
    snapshot.revision = m_revision;
    snapshot.sessionCount = m_sessions.size();
    snapshot.contracts = m_contracts;
    return snapshot;
}

void TradingToolSessionContractCatalog::SetObserver(const Observer& observer)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    m_observer = observer;
}

bool TradingToolSessionContractCatalog::SameContract(
    const InstrumentRef& left,
    const InstrumentRef& right)
{
    return left.symbol == right.symbol && left.secType == right.secType &&
        left.exchange == right.exchange && left.currency == right.currency &&
        left.primaryExchange == right.primaryExchange &&
        left.lastTradeDateOrContractMonth == right.lastTradeDateOrContractMonth &&
        left.right == right.right && left.strike == right.strike &&
        left.multiplier == right.multiplier && left.tradingClass == right.tradingClass &&
        left.localSymbol == right.localSymbol;
}

void TradingToolSessionContractCatalog::RebuildContractsLocked()
{
    m_contracts.clear();
    for (std::map<std::string, TradingToolSessionContractRegistration>::const_iterator session =
             m_sessions.begin(); session != m_sessions.end(); ++session)
    {
        for (std::map<std::string, InstrumentRef>::const_iterator contract =
                 session->second.contracts.begin(); contract != session->second.contracts.end(); ++contract)
        {
            TradingToolSessionContractRecord& record = m_contracts[contract->first];
            record.contract = contract->second;
            ++record.sessionReferences;
        }
    }
}
