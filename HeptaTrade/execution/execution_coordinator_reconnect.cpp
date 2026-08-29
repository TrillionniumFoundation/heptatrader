#include "execution_coordinator.h"

bool ExecutionCoordinator::BeginBrokerReconnectFence(std::string& reason)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    if (m_mutationBlocked)
    {
        reason = m_mutationBlockReason.empty() ?
            "IB_PAPER_BROKER_RECONNECT_COORDINATOR_BLOCKED" :
            m_mutationBlockReason;
        return false;
    }
    if (!m_orderOwners.empty())
    {
        reason = "IB_PAPER_BROKER_RECONNECT_LOCAL_ORDERS_UNSAFE";
        return false;
    }
    m_mutationBlocked = true;
    m_mutationBlockReason = "IB_PAPER_BROKER_RECONNECT_PENDING";
    reason.clear();
    return true;
}

bool ExecutionCoordinator::EndBrokerReconnectFence(std::string& reason)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    if (!m_mutationBlocked ||
        m_mutationBlockReason != "IB_PAPER_BROKER_RECONNECT_PENDING")
    {
        reason = m_mutationBlockReason.empty() ?
            "IB_PAPER_BROKER_RECONNECT_FENCE_MISSING" :
            m_mutationBlockReason;
        return false;
    }
    m_mutationBlocked = false;
    m_mutationBlockReason.clear();
    reason.clear();
    return true;
}

ExecutionOrderOwnerLookupStatus ExecutionCoordinator::TryGetOrderOwner(
    long orderId, ExecutionOrderOwner& out) const
{
    std::unique_lock<std::mutex> lock(m_mutex, std::try_to_lock);
    if (!lock.owns_lock()) return ExecutionOrderOwnerLookupStatus::Busy;
    const std::unordered_map<long, ExecutionOrderOwner>::const_iterator it =
        m_orderOwners.find(orderId);
    if (it == m_orderOwners.end())
        return ExecutionOrderOwnerLookupStatus::Missing;
    out = it->second;
    return ExecutionOrderOwnerLookupStatus::Found;
}

ExecutionOwnedActiveOrderProjection
ExecutionCoordinator::ProjectOwnedActiveOrders(
    const std::set<long>& authoritativeActiveOrderIds,
    const AgentExecutionContext& ownerScope) const
{
    ExecutionOwnedActiveOrderProjection projection;
    if (ownerScope.agentId.empty() || ownerScope.sessionId.empty() ||
        ownerScope.account.empty() || ownerScope.executionDomain.empty())
        return projection;

    std::lock_guard<std::mutex> lock(m_mutex);
    projection.complete = true;
    for (std::set<long>::const_iterator active =
             authoritativeActiveOrderIds.begin();
         active != authoritativeActiveOrderIds.end(); ++active)
    {
        const std::unordered_map<long, ExecutionOrderOwner>::const_iterator
            owner = m_orderOwners.find(*active);
        if (owner == m_orderOwners.end() || owner->second.agentId.empty() ||
            owner->second.sessionId.empty() || owner->second.account.empty() ||
            owner->second.executionDomain.empty())
        {
            projection.complete = false;
            projection.unmappedOrderIds.insert(*active);
            continue;
        }
        if (owner->second.agentId == ownerScope.agentId &&
            owner->second.sessionId == ownerScope.sessionId &&
            owner->second.account == ownerScope.account &&
            owner->second.executionDomain == ownerScope.executionDomain)
            projection.ownedOrderIds.insert(*active);
    }
    return projection;
}

bool ExecutionCoordinator::AuditRecoveryOwner(
    const std::set<long>& authoritativeActiveOrderIds,
    bool authoritativeOpenOrdersComplete,
    const AgentExecutionContext& ownerScope,
    std::uint64_t& activeOrderCount,
    std::uint64_t& uncertainCommandCount,
    std::string& reason) const
{
    activeOrderCount = 0;
    uncertainCommandCount = 0;
    if (!authoritativeOpenOrdersComplete || ownerScope.agentId.empty() ||
        ownerScope.sessionId.empty() || ownerScope.account.empty() ||
        ownerScope.executionDomain.empty())
    {
        reason = "RECOVERY_OWNER_AUDIT_SCOPE_INCOMPLETE";
        return false;
    }
    std::lock_guard<std::mutex> lock(m_mutex);
    for (std::set<long>::const_iterator order =
             authoritativeActiveOrderIds.begin();
         order != authoritativeActiveOrderIds.end(); ++order)
    {
        const std::unordered_map<long, ExecutionOrderOwner>::const_iterator
            owner = m_orderOwners.find(*order);
        if (owner == m_orderOwners.end())
        {
            reason = "RECOVERY_OWNER_AUDIT_UNMAPPED_ACTIVE_ORDER";
            return false;
        }
        if (owner->second.agentId != ownerScope.agentId ||
            owner->second.sessionId != ownerScope.sessionId)
            continue;
        if (owner->second.account != ownerScope.account ||
            owner->second.executionDomain != ownerScope.executionDomain)
        {
            reason = "RECOVERY_OWNER_AUDIT_SCOPE_MISMATCH";
            return false;
        }
        ++activeOrderCount;
    }
    for (std::unordered_map<std::string, RequestRecord>::const_iterator request =
             m_requests.begin(); request != m_requests.end(); ++request)
    {
        const AgentExecutionContext& context = request->second.context;
        if (context.agentId != ownerScope.agentId ||
            context.sessionId != ownerScope.sessionId)
            continue;
        if (context.account != ownerScope.account ||
            context.executionDomain != ownerScope.executionDomain)
        {
            reason = "RECOVERY_OWNER_AUDIT_SCOPE_MISMATCH";
            return false;
        }
        if (request->second.status == ExecutionCommandStatus::Uncertain)
            ++uncertainCommandCount;
    }
    if (uncertainCommandCount != 0)
    {
        reason = "RECOVERY_OWNER_UNCERTAIN_COMMANDS_REMAIN";
        return false;
    }
    reason.clear();
    return true;
}
