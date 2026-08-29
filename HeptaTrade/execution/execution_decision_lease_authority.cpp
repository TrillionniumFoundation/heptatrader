#include "execution_decision_lease_authority.h"

namespace
{
std::string Component(const std::string& value)
{
    return std::to_string(value.size()) + ":" + value;
}
}

ExecutionDecisionLeaseAuthority::ExecutionDecisionLeaseAuthority(
    std::chrono::milliseconds leaseTtl)
    : m_manager(DecisionLeaseManager::NowProvider(), leaseTtl),
      m_leaseTtl(leaseTtl)
{
}

ExecutionDecisionLeaseAuthority::ExecutionDecisionLeaseAuthority(
    const DecisionLeaseManager::NowProvider& nowProvider,
    std::chrono::milliseconds leaseTtl)
    : m_manager(nowProvider, leaseTtl), m_leaseTtl(leaseTtl)
{
}

std::string ExecutionDecisionLeaseAuthority::MapKey(
    const DecisionLeaseKey& key)
{
    return Component(key.executionDomain) + Component(key.account) +
        Component(key.instrument);
}

std::string ExecutionDecisionLeaseAuthority::AuthorizationFailure(
    DecisionLeaseStatus status)
{
    if (status == DecisionLeaseStatus::Busy)
        return "EXECUTION_DECISION_LEASE_BUSY";
    if (status == DecisionLeaseStatus::ClockFailure)
        return "EXECUTION_DECISION_LEASE_CLOCK_FAILURE";
    if (status == DecisionLeaseStatus::FencingExhausted)
        return "EXECUTION_DECISION_LEASE_FENCING_EXHAUSTED";
    return "EXECUTION_DECISION_LEASE_AUTHORIZATION_FAILED";
}

bool ExecutionDecisionLeaseAuthority::Authorize(
    AgentExecutionContext& context,
    const std::string& instrument,
    std::string& reason)
{
    context.decisionLeaseFencingToken = 0;
    context.decisionLeaseGeneration = 0;

    DecisionLeaseKey key;
    key.executionDomain = context.executionDomain;
    key.account = context.account;
    key.instrument = instrument;
    DecisionLeaseOwner owner;
    owner.agentId = context.agentId;
    owner.sessionId = context.sessionId;

    std::lock_guard<std::mutex> lock(m_mutex);
    const std::string mapKey = MapKey(key);
    std::map<std::string, ActiveLease>::iterator active = m_active.find(mapKey);
    DecisionLeaseResult lease;
    if (active != m_active.end() && active->second.owner == owner)
    {
        lease = m_manager.Renew(
            key, owner, active->second.credential, m_leaseTtl);
        if (lease.status == DecisionLeaseStatus::Expired ||
            lease.status == DecisionLeaseStatus::NotFound)
        {
            m_active.erase(active);
            lease = m_manager.Acquire(key, owner, m_leaseTtl);
        }
    }
    else
        lease = m_manager.Acquire(key, owner, m_leaseTtl);

    if (!lease.Succeeded())
    {
        reason = AuthorizationFailure(lease.status);
        return false;
    }

    ActiveLease granted;
    granted.key = key;
    granted.owner = owner;
    granted.credential = lease.credential;
    m_active[mapKey] = granted;
    context.decisionLeaseFencingToken = lease.credential.fencingToken;
    context.decisionLeaseGeneration = lease.credential.generation;
    reason.clear();
    return true;
}

bool ExecutionDecisionLeaseAuthority::Validate(
    const AgentExecutionContext& context,
    const std::string& instrument,
    std::string* detail)
{
    DecisionLeaseKey key;
    key.executionDomain = context.executionDomain;
    key.account = context.account;
    key.instrument = instrument;
    DecisionLeaseOwner owner;
    owner.agentId = context.agentId;
    owner.sessionId = context.sessionId;
    DecisionLeaseCredential credential;
    credential.fencingToken = context.decisionLeaseFencingToken;
    credential.generation = context.decisionLeaseGeneration;
    const DecisionLeaseResult result =
        m_manager.Validate(key, owner, credential);
    if (!result.Succeeded() && detail != nullptr)
        *detail = std::string("EXECUTION_DECISION_LEASE_") +
            DecisionLeaseManager::StatusName(result.status);
    return result.Succeeded();
}

std::size_t ExecutionDecisionLeaseAuthority::FenceOwner(
    const std::string& agentId,
    const std::string& sessionId)
{
    DecisionLeaseOwner owner;
    owner.agentId = agentId;
    owner.sessionId = sessionId;
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::size_t fenced = m_manager.FenceOwner(owner);
    for (std::map<std::string, ActiveLease>::iterator it = m_active.begin();
         it != m_active.end();)
    {
        if (it->second.owner == owner) it = m_active.erase(it);
        else ++it;
    }
    return fenced;
}
