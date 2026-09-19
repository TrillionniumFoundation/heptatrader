#include "execution_coordinator.h"

#include <set>
#include <tuple>

bool ExecutionCoordinator::EnterPaperTerminalFence(
    const AgentExecutionContext& context,
    const std::string& finalizationId,
    std::string& reason)
{
    (void)context;
    (void)finalizationId;
    reason = "IB_PAPER_TERMINAL_FENCE_V2_BINDING_REQUIRED";
    return false;
}

bool ExecutionCoordinator::EnterPaperTerminalFenceAndProject(
    const PaperTerminalFenceBinding& binding,
    PaperTerminalMutationUniverse& universe,
    std::string& reason)
{
    universe = PaperTerminalMutationUniverse();
    if (!ValidPaperTerminalFenceBinding(binding, reason)) return false;
    std::lock_guard<std::mutex> lock(m_mutex);

    if (m_venueDispatchesInFlight != 0)
    {
        reason = "IB_PAPER_TERMINAL_FENCE_VENUE_DISPATCH_IN_FLIGHT";
        return false;
    }
    if (m_generationStore.IsActive())
        return EnterPaperTerminalFenceAndProjectGenerationAwareLocked(
            binding, universe, reason);

    if (m_mutationBlocked)
    {
        if (m_mutationBlockReason != "IB_PAPER_TERMINAL_HALTED" ||
            !m_paperTerminalFencePresent ||
            !SamePaperTerminalFenceBinding(m_paperTerminalFenceBinding, binding))
        {
            reason = m_mutationBlockReason == "IB_PAPER_TERMINAL_HALTED" ?
                "IB_PAPER_TERMINAL_FENCE_BINDING_MISMATCH" :
                (m_mutationBlockReason.empty() ?
                    "IB_PAPER_TERMINAL_FENCE_COORDINATOR_BLOCKED" : m_mutationBlockReason);
            return false;
        }
    }
    else
    {
        if (!m_orderOwners.empty())
        {
            reason = "IB_PAPER_TERMINAL_FENCE_LOCAL_ORDERS_UNSAFE";
            return false;
        }
        AgentExecutionContext journalContext = binding.owner;
        journalContext.toolCallId = binding.finalizationId;
        const std::string encoded = EncodePaperTerminalFenceBinding(binding);
        if (encoded.empty())
        {
            reason = "IB_PAPER_TERMINAL_FENCE_BINDING_INVALID";
            return false;
        }
        const OmsJournalEvent event = BuildEvent(
            journalContext, "paper_terminal_fence", -1, "", "", 0.0, 0.0,
            std::to_string(binding.recoveryIngressFence), encoded,
            "IB_PAPER_TERMINAL_HALTED", binding.preliminaryReceiptSha256,
            binding.brokerSocketIdentitySha256);
        if (!AppendOrBlockLocked(event, "OMS_PAPER_TERMINAL_FENCE_JOURNAL_FAILED"))
        {
            reason = "OMS_PAPER_TERMINAL_FENCE_JOURNAL_FAILED";
            return false;
        }
        m_mutationBlocked = true;
        m_mutationBlockReason = "IB_PAPER_TERMINAL_HALTED";
        m_paperTerminalFencePresent = true;
        m_paperTerminalFenceBinding = binding;
    }

    std::vector<PaperTerminalMutationRecord> activeTail;
    std::set<std::tuple<std::string, std::string, std::string,
                        std::string, std::string>> seen;
    for (RequestRecordStore::Base::const_iterator it = m_requests.begin();
         it != m_requests.end(); ++it)
    {
        const RequestRecord& request = it->second;
        if (!request.durableMutationIntent ||
            request.context.agentId != binding.owner.agentId ||
            request.context.sessionId != binding.owner.sessionId ||
            request.context.account != binding.owner.account ||
            request.context.executionDomain != binding.owner.executionDomain)
            continue;

        const std::tuple<std::string, std::string, std::string,
                         std::string, std::string> key(
            request.context.agentId, request.context.sessionId,
            request.context.toolCallId, request.operation,
            request.venueCorrelationId);
        if (!seen.insert(key).second) continue;
        PaperTerminalMutationRecord record;
        record.agentId = request.context.agentId;
        record.sessionId = request.context.sessionId;
        record.toolCallId = request.context.toolCallId;
        record.operation = request.operation;
        record.venueCorrelationId = request.venueCorrelationId;
        activeTail.push_back(record);
    }

    return BuildPaperTerminalMutationUniverse(activeTail, universe, reason);
}
