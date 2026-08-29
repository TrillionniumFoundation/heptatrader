#pragma once

#include "authoritative_trading_snapshot_store.h"
#include "../adapter_ib/ib_api_wrapper.h"

#include <cstdint>
#include <map>
#include <string>
#include <unordered_map>
#include <vector>

enum class IBAuthoritativeSnapshotConsumeStatus
{
    Applied = 0,
    Ignored,
    Rejected
};

struct IBAuthoritativeAccountCompletion
{
    bool accepted = false;
    std::string reasonCode;
    AuthoritativeAccount account;
    std::unordered_map<std::string, double> metrics;
    std::map<std::string, std::string> rawValues;
};

struct IBAuthoritativePositionCompletion
{
    bool accepted = false;
    std::string reasonCode;
    std::vector<AuthoritativePosition> positions;
    std::unordered_map<std::string, double> quantities;
};

class IBAuthoritativeAccountPositionConsumer
{
public:
    IBAuthoritativeAccountPositionConsumer(AuthoritativeTradingSnapshotStore& store,
                                           const std::string& configuredAccount);

    bool ConfigureAccount(const std::string& configuredAccount);

    void BeginAccount(std::uint64_t generation);
    IBAuthoritativeSnapshotConsumeStatus ConsumeAccountValue(const IBEvent& event);
    IBAuthoritativeAccountCompletion CompleteAccount(std::uint64_t generation,
                                                     std::uint64_t observedAtMs);
    void AbortAccount(std::uint64_t generation);

    void BeginPositions(std::uint64_t generation);
    IBAuthoritativeSnapshotConsumeStatus ConsumePosition(const IBEvent& event);
    IBAuthoritativePositionCompletion CompletePositions(std::uint64_t generation,
                                                        std::uint64_t observedAtMs);
    void AbortPositions(std::uint64_t generation);

private:
    AuthoritativeTradingSnapshotStore& m_store;
    std::string m_configuredAccount;

    std::uint64_t m_accountGeneration = 0;
    bool m_accountRejected = false;
    std::string m_accountRejectReason;
    std::string m_accountCurrency;
    std::unordered_map<std::string, double> m_accountMetrics;
    std::map<std::string, std::string> m_accountRawValues;

    std::uint64_t m_positionGeneration = 0;
    bool m_positionsRejected = false;
    std::string m_positionsRejectReason;
    std::map<std::string, AuthoritativePosition> m_positions;
};
