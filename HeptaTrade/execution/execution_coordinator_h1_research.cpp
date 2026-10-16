#include "execution_coordinator.h"
#include "ib_bracket_h1_oms_reservation.h"

bool ExecutionCoordinator::ReserveResearchH1BracketForRecoveryOnlyOwner(
    const AgentExecutionContext& originalOwner,
    const hepta_bracket_h1::FrozenBracket& frozen,
    hepta_bracket_h1::Reservation& result, std::string& reason)
{
    result=hepta_bracket_h1::Reservation{};
    // Verify that the original command and BOTH agent/session identities
    // match the request made to the actual Coordinator. Caller-supplied
    // nonzero frozen.fenceToken alone conveys NO authority.
    if(originalOwner.agentId.empty() || originalOwner.sessionId.empty() ||
       originalOwner.toolCallId.empty() ||
       originalOwner.agentId != frozen.owner ||
       originalOwner.sessionId != frozen.sessionId ||
       originalOwner.toolCallId != frozen.originalCommandKey ||
       originalOwner.executionDomain != "IB-PAPER:EUR.USD") {
        reason="H1_BRACKET_ORIGINAL_OWNER_SESSION_MISMATCH";
        return false;
    }
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::string ownerKey=OwnerKey(originalOwner.agentId,originalOwner.sessionId);
    const auto recovery=m_recoveryOnlySessionOwners.find(ownerKey);
    if(recovery==m_recoveryOnlySessionOwners.end() ||
       recovery->second==0 || recovery->second!=frozen.ownerFenceToken) {
        reason="H1_BRACKET_COORDINATOR_RECOVERY_FENCE_MISSING";
        return false;
    }
    if(m_mutationBlocked || m_paperTerminalFencePresent ||
       m_riskMutationDispatchInFlight || m_venueDispatchesInFlight!=0 ||
       !m_cancelPreflightsInFlight.empty()) {
        reason="H1_BRACKET_COORDINATOR_MUTATION_IN_FLIGHT";
        return false;
    }
    for(const auto& owned:m_orderOwners) {
        if(owned.second.agentId==originalOwner.agentId &&
           owned.second.sessionId==originalOwner.sessionId) {
            reason="H1_BRACKET_EXISTING_OWNER_ORDER_UNKNOWN";
            return false;
        }
    }
    const auto prior=m_requests.LookupOrLoad(RequestKey(
        originalOwner.agentId,originalOwner.sessionId,originalOwner.toolCallId));
    if(prior!=m_requests.end()) {
        reason="H1_BRACKET_PRIOR_COMMAND_IDENTITY";
        return false;
    }
    // Research events are CRITICAL and do not look like ordinary
    // order_intent. A journal prefix may exist after a crash. Before
    // admitting a new group, reject ANY recorded original req_id, including
    // orphaned attempts. No external Broker IO happens here.
    // Before appending, use the *same* frozen H1 role derivation that will
    // be written to OMS. A separate original command may not borrow this
    // pair's Group, Parent, STOP, or OCA identity even with a new req_id.
    // The Coordinator mutex protects this admission+append against another
    // Coordinator request. This is not permission to contact the Broker.
    hepta_bracket_h1::BoundRefs frozenIds;
    if(!hepta_bracket_h1::BuildFrozenRefs(frozen,frozenIds,reason))
        return false;
    bool collision=false;
    bool venueIdentityCollision=false;
    const int observed=m_journal.Replay([&](const OmsJournalEvent& e) {
        if(e.reqId==originalOwner.toolCallId ||
           e.clientReqId==originalOwner.toolCallId) collision=true;
        if(e.requestHash==frozenIds.groupCorrelation ||
           e.requestHash==frozenIds.parentCorrelation ||
           e.requestHash==frozenIds.childCorrelation ||
           e.requestHash==frozenIds.ocaCorrelation ||
           e.venueCorrelationId==frozenIds.groupCorrelation ||
           e.venueCorrelationId==frozenIds.parentCorrelation ||
           e.venueCorrelationId==frozenIds.childCorrelation ||
           e.venueCorrelationId==frozenIds.ocaCorrelation)
            venueIdentityCollision=true;
    });
    if(observed<0 || collision || venueIdentityCollision) {
        reason=observed<0 ? "H1_BRACKET_ORIGINAL_JOURNAL_UNAVAILABLE" :
               collision ? "H1_BRACKET_EXISTING_OR_ORPHANED_ORIGINAL_COMMAND" :
               "H1_BRACKET_FOREIGN_FROZEN_H1_IDENTITY_COLLISION";
        return false;
    }
    if(!hepta_bracket_h1::ReservePair(m_journal,frozen,result,reason)) {
        // A partial critical pair can remain on disk. The only safe outcome
        // is UNKNOWN; caller must not reuse reserved native order IDs.
        if(reason.empty())reason="H1_BRACKET_PAIR_DURABILITY_UNCERTAIN";
        return false;
    }
    return true; // no send authorization is returned
}
