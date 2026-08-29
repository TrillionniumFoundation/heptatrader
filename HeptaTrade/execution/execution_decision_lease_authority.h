#pragma once

#include "execution_authority.h"
#include "../agent/decision_lease_manager.h"

#include <chrono>
#include <cstddef>
#include <map>
#include <mutex>
#include <string>

class ExecutionDecisionLeaseAuthority
{
public:
    explicit ExecutionDecisionLeaseAuthority(
        std::chrono::milliseconds leaseTtl = std::chrono::milliseconds(30000));
    ExecutionDecisionLeaseAuthority(
        const DecisionLeaseManager::NowProvider& nowProvider,
        std::chrono::milliseconds leaseTtl);

    bool Authorize(AgentExecutionContext& context,
                   const std::string& instrument,
                   std::string& reason);
    bool Validate(const AgentExecutionContext& context,
                  const std::string& instrument,
                  std::string* detail);
    std::size_t FenceOwner(const std::string& agentId,
                           const std::string& sessionId);

private:
    struct ActiveLease
    {
        DecisionLeaseKey key;
        DecisionLeaseOwner owner;
        DecisionLeaseCredential credential;
    };

    static std::string MapKey(const DecisionLeaseKey& key);
    static std::string AuthorizationFailure(DecisionLeaseStatus status);

    mutable std::mutex m_mutex;
    DecisionLeaseManager m_manager;
    std::chrono::milliseconds m_leaseTtl;
    std::map<std::string, ActiveLease> m_active;
};
