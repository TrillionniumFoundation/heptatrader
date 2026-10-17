#include "ib_gateway_adapter.h"
#include "../execution/execution_coordinator.h"
#include "../execution/ib_bracket_h1_oms_reservation.h"

// Standalone native Execution adapter library translation unit, not an Agent
// tool or broker sender. This member has access to the Coordinator's *actual*
// journal. There is deliberately no journal or path argument in the caller's
// method signature, preventing a caller from substituting an empty OMS.
bool ExecutionCoordinator::ReserveResearchNativeH1ThroughOwnedJournal(
    HeptaIBGatewayAdapter& adapter,
    const AgentExecutionContext& originalOwner,
    const hepta_bracket_h1::FrozenBracket& frozen,
    hepta_bracket_h1::Reservation& result,
    long& parentId, long& stopId, std::string& reason)
{
    result=hepta_bracket_h1::Reservation{};
    parentId=-1; stopId=-1;
    if (frozen.account.compare(0,2,"DU")!=0 ||
        frozen.clientId <= 0 || frozen.connectionEpoch == 0 ||
        originalOwner.agentId.empty() ||
        originalOwner.sessionId.empty() ||
        originalOwner.toolCallId.empty() ||
        originalOwner.agentId!=frozen.owner ||
        originalOwner.sessionId!=frozen.sessionId ||
        originalOwner.toolCallId!=frozen.originalCommandKey ||
        originalOwner.executionDomain!="IB-PAPER:EUR.USD" ||
        m_journal.GetPath().empty()) {
        reason="H1_BRACKET_COORDINATOR_JOURNAL_OR_OWNER_INVALID";
        return false;
    }

    // Do not hold m_mutex BEFORE m_apiMutex: the existing PlaceOrder dispatch
    // releases the coordinator lock before its adapter-send boundary. The
    // callback re-acquires m_mutex only after adapter owns its send mutex.
    // The Coordinator and its m_journal must outlive this synchronous call.
    return adapter.ReserveResearchH1NativeIdPair(
        frozen.account, frozen.connectionEpoch, m_journal,
        [&](long actualParent,long actualStop) -> bool {
            auto bound=frozen;
            bound.parentOrderId=actualParent;
            bound.childOrderId=actualStop;
            return ReserveResearchH1BracketForRecoveryOnlyOwner(
                originalOwner,bound,result,reason);
        },parentId,stopId,reason,frozen.clientId);
}
