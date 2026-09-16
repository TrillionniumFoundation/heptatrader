#include "execution_coordinator.h"

#include <algorithm>
#include <tuple>

namespace
{
const std::size_t kHistoricalCommandCache = 256U;

ExecutionCommandStatus HistoricalStatus(const std::string& value)
{
    if (value == "accepted") return ExecutionCommandStatus::Accepted;
    if (value == "rejected") return ExecutionCommandStatus::Rejected;
    return ExecutionCommandStatus::Uncertain;
}
}

ExecutionCoordinator::RequestRecordStore::RequestRecordStore(
    OmsGenerationStore* generationStore)
    : m_generationStore(generationStore)
{
}

bool ExecutionCoordinator::RequestRecordStore::DecodeRequestKey(
    const std::string& key,
    std::string& agentId,
    std::string& sessionId,
    std::string& commandId)
{
    const char separator = '\x1f';
    const std::size_t first = key.find(separator);
    if (first == std::string::npos) return false;
    const std::size_t second = key.find(separator, first + 1U);
    if (second == std::string::npos ||
        key.find(separator, second + 1U) != std::string::npos)
        return false;
    agentId = key.substr(0, first);
    sessionId = key.substr(first + 1U, second - first - 1U);
    commandId = key.substr(second + 1U);
    return !agentId.empty() && !sessionId.empty() && !commandId.empty();
}

void ExecutionCoordinator::RequestRecordStore::RememberHistorical(
    const std::string& key)
{
    if (!m_historicalKeys.insert(key).second) return;
    m_historicalOrder.push_back(key);
    while (m_historicalKeys.size() > kHistoricalCommandCache &&
           !m_historicalOrder.empty())
    {
        const std::string oldest = m_historicalOrder.front();
        m_historicalOrder.pop_front();
        if (m_historicalKeys.erase(oldest) != 0)
            Base::erase(oldest);
    }
    // Promotions leave stale deque entries. Compact them occasionally so the
    // cache metadata itself cannot grow with long-running duplicate traffic.
    if (m_historicalOrder.size() > kHistoricalCommandCache * 4U)
    {
        std::deque<std::string> compact;
        for (std::deque<std::string>::const_iterator it = m_historicalOrder.begin();
             it != m_historicalOrder.end(); ++it)
            if (m_historicalKeys.find(*it) != m_historicalKeys.end())
                compact.push_back(*it);
        m_historicalOrder.swap(compact);
    }
}

void ExecutionCoordinator::RequestRecordStore::Promote(
    const std::string& key)
{
    m_historicalKeys.erase(key);
}

ExecutionCoordinator::RequestRecordStore::Base::iterator
ExecutionCoordinator::RequestRecordStore::LoadHistorical(
    const std::string& key)
{
    Base::iterator existing = Base::find(key);
    if (existing != Base::end()) return existing;
    if (m_generationStore == nullptr || !m_generationStore->IsActive())
        return Base::end();

    std::string agentId, sessionId, commandId;
    if (!DecodeRequestKey(key, agentId, sessionId, commandId))
        return Base::end();

    OmsGenerationCommandRecord historical;
    std::string reason;
    const OmsGenerationLookupStatus status = m_generationStore->LookupCommand(
        agentId, sessionId, commandId, historical, reason);
    if (status == OmsGenerationLookupStatus::Missing)
        return Base::end();

    RequestRecord record;
    record.context.agentId = agentId;
    record.context.sessionId = sessionId;
    record.context.toolCallId = commandId;
    record.durableMutationIntent = true;
    if (status == OmsGenerationLookupStatus::Error)
    {
        // Every mutation path first consults this store. An index integrity or
        // identity failure therefore becomes an uncertain existing command,
        // never a permission to treat the key as new and send externally.
        record.status = ExecutionCommandStatus::Uncertain;
        record.reasonCode = "OMS_GENERATION_INDEX_FAILED";
        record.detail = reason.empty() ?
            "permanent command index is unavailable" : reason;
    }
    else
    {
        record.status = HistoricalStatus(historical.status);
        record.orderId = historical.orderId;
        record.reasonCode = historical.reasonCode;
        record.requestHash = historical.requestHash;
        record.venueCorrelationId = historical.venueCorrelationId;
        record.operation = historical.operation;
        record.context.account = historical.account;
        record.context.executionDomain = historical.executionDomain;
        record.durableMutationIntent = historical.durableMutationIntent;
    }
    const std::pair<Base::iterator, bool> inserted =
        Base::insert(std::make_pair(key, record));
    RememberHistorical(key);
    return inserted.first;
}

ExecutionCoordinator::RequestRecordStore::Base::iterator
ExecutionCoordinator::RequestRecordStore::find(const std::string& key)
{
    Base::iterator existing = Base::find(key);
    return existing != Base::end() ? existing : LoadHistorical(key);
}

ExecutionCoordinator::RequestRecordStore::Base::const_iterator
ExecutionCoordinator::RequestRecordStore::find(const std::string& key) const
{
    Base::const_iterator existing = Base::find(key);
    if (existing != Base::end()) return existing;
    RequestRecordStore* self = const_cast<RequestRecordStore*>(this);
    const Base::iterator loaded = self->LoadHistorical(key);
    return loaded == self->Base::end() ? Base::end() : loaded;
}

ExecutionCoordinator::RequestRecord&
ExecutionCoordinator::RequestRecordStore::operator[](const std::string& key)
{
    Base::iterator existing = find(key);
    if (existing != Base::end())
    {
        Promote(key);
        return existing->second;
    }
    return Base::operator[](key);
}

void ExecutionCoordinator::RequestRecordStore::clear()
{
    Base::clear();
    m_historicalOrder.clear();
    m_historicalKeys.clear();
}

std::size_t ExecutionCoordinator::RequestRecordStore::HotSize() const
{
    return Base::size() >= m_historicalKeys.size() ?
        Base::size() - m_historicalKeys.size() : 0U;
}

bool ExecutionCoordinator::EnterPaperTerminalFenceAndProjectGenerationAwareLocked(
    const PaperTerminalFenceBinding& binding,
    PaperTerminalMutationUniverse& universe,
    std::string& reason)
{
    universe = PaperTerminalMutationUniverse();
    if (m_mutationBlocked)
    {
        if (m_mutationBlockReason != "IB_PAPER_TERMINAL_HALTED" ||
            !m_paperTerminalFencePresent ||
            !SamePaperTerminalFenceBinding(m_paperTerminalFenceBinding, binding))
        {
            reason = m_mutationBlockReason == "IB_PAPER_TERMINAL_HALTED" ?
                "IB_PAPER_TERMINAL_FENCE_BINDING_MISMATCH" :
                (m_mutationBlockReason.empty() ?
                    "IB_PAPER_TERMINAL_FENCE_COORDINATOR_BLOCKED" :
                    m_mutationBlockReason);
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
        if (!AppendOrBlockLocked(
                event, "OMS_PAPER_TERMINAL_FENCE_JOURNAL_FAILED"))
        {
            reason = "OMS_PAPER_TERMINAL_FENCE_JOURNAL_FAILED";
            return false;
        }
        m_mutationBlocked = true;
        m_mutationBlockReason = "IB_PAPER_TERMINAL_HALTED";
        m_paperTerminalFencePresent = true;
        m_paperTerminalFenceBinding = binding;
    }

    std::vector<PaperTerminalMutationRecord> records;
    std::set<std::tuple<std::string, std::string, std::string,
                        std::string, std::string>> seen;
    if (m_generationStore.IsActive())
    {
        std::vector<OmsGenerationMutationRecord> historical;
        if (!m_generationStore.EnumerateMutationRecords(
                binding.owner.account, binding.owner.executionDomain,
                historical, reason))
            return false;
        for (std::size_t i = 0; i < historical.size(); ++i)
        {
            const OmsGenerationMutationRecord& source = historical[i];
            const std::tuple<std::string, std::string, std::string,
                             std::string, std::string> key(
                source.agentId, source.sessionId, source.commandId,
                source.operation, source.venueCorrelationId);
            if (!seen.insert(key).second) continue;
            PaperTerminalMutationRecord record;
            record.agentId = source.agentId;
            record.sessionId = source.sessionId;
            record.toolCallId = source.commandId;
            record.operation = source.operation;
            record.venueCorrelationId = source.venueCorrelationId;
            records.push_back(record);
        }
    }
    for (RequestRecordStore::Base::const_iterator it = m_requests.begin();
         it != m_requests.end(); ++it)
    {
        const RequestRecord& request = it->second;
        if (!request.durableMutationIntent ||
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
        records.push_back(record);
    }
    return BuildPaperTerminalMutationUniverse(records, universe, reason);
}
