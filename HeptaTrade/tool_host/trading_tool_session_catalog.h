#pragma once

#include "../execution/trading_contract.h"

#include <cstdint>
#include <functional>
#include <map>
#include <mutex>
#include <string>

struct TradingToolSessionContractRegistration
{
    std::string token;
    std::string agentId;
    std::string sessionId;
    std::uint64_t expiresAtMs = 0;
    std::map<std::string, InstrumentRef> contracts;
};

struct TradingToolSessionContractRecord
{
    InstrumentRef contract;
    std::size_t sessionReferences = 0;
};

struct TradingToolSessionContractCatalogSnapshot
{
    std::uint64_t revision = 0;
    std::size_t sessionCount = 0;
    std::map<std::string, TradingToolSessionContractRecord> contracts;
};

class TradingToolSessionContractCatalog
{
public:
    typedef std::function<void(const TradingToolSessionContractCatalogSnapshot&)> Observer;

    bool Register(const TradingToolSessionContractRegistration& registration,
                  std::string& reason);
    bool Replace(const std::string& currentToken,
                 const TradingToolSessionContractRegistration& registration,
                 std::string& reason);
    bool Revoke(const std::string& token);
    // Finalization cleanup is intentionally idempotent.  A crash after the
    // catalog entry was removed but before the durable HSL7 transition must
    // be able to repeat cleanup without treating naked absence as evidence
    // that the PAPER owner was safely finalized.
    bool RevokeIfPresent(const std::string& token);
    TradingToolSessionContractCatalogSnapshot GetSnapshot() const;
    void SetObserver(const Observer& observer);

private:
    static bool SameContract(const InstrumentRef& left, const InstrumentRef& right);
    void RebuildContractsLocked();

private:
    mutable std::mutex m_mutex;
    std::map<std::string, TradingToolSessionContractRegistration> m_sessions;
    std::map<std::string, TradingToolSessionContractRecord> m_contracts;
    std::uint64_t m_revision = 0;
    Observer m_observer;
};
