#include "execution_coordinator.h"
#include "ib_bracket_h1_oms_reservation.h"
#include <algorithm>
#include <limits>

bool ExecutionCoordinator::GetDurableResearchH1NativeIdFloor(
    const std::string& paperAccount, std::int64_t& nextIdFloor,
    std::string& reason) const
{
    nextIdFloor=1;
    if(paperAccount.size()<3 || paperAccount.compare(0,2,"DU")!=0) {
        reason="H1_RECOVERED_NATIVE_ID_ACCOUNT_NOT_PAPER";
        return false;
    }
    const long maxId=static_cast<long>(
        std::numeric_limits<std::int32_t>::max());
    bool invalid=false;
    std::lock_guard<std::mutex> lock(m_mutex);
    const int rows=m_journal.Replay([&](const OmsJournalEvent& e){
        // IB numeric order IDs belong to the native Broker client, NOT to
        // one account. Original shadow journals have no attested client ID,
        // so a changed DU account may not reuse a predecessor's unsent IDs.
        // Burn the upper bound of every PAPER H1 reservation in this journal,
        // regardless of the original account; malformed scope fails closed.
        const bool exit=e.eventType=="bracket_oca_exit_reserved";
        const bool h1=e.eventType=="bracket_parent_reserved" ||
            e.eventType=="bracket_child_reserved" ||
            e.eventType=="bracket_parent_send_attempt" ||
            e.eventType=="bracket_child_send_attempt" || exit;
        if(!h1)return;
        if(e.account.size()<3 || e.account.compare(0,2,"DU")!=0 ||
           e.venue!="IB" || e.executionDomain!="PAPER" ||
           e.source!="SHADOW_NO_BROKER_EGRESS" ||
           e.orderId<1 || e.orderId>maxId ||
           // These are never broker-send receipts. Reject malformed local
           // OCA EXIT markers rather than interpreting them as a safe zero.
           (exit && (e.reqId.empty() || e.eventId.empty() ||
               e.tsMs<=0 ||
               e.requestHash.size()!=80 ||
               e.requestHash.compare(0,16,"hepta-v1-sha256:")!=0 ||
               e.venueCorrelationId.size()!=80 ||
               e.venueCorrelationId.compare(0,16,"hepta-v1-sha256:")!=0))) {
            invalid=true;return;
        }
        // Conservative even for a lone durable parent after fsync failure.
        // Never infer broker zero-fill or convert a prefix into a send permit.
        nextIdFloor=std::max(nextIdFloor,
            static_cast<std::int64_t>(e.orderId)+1);
    });
    if(rows<0 || invalid) {
        nextIdFloor=1;
        reason=rows<0 ? "H1_RECOVERED_NATIVE_ID_JOURNAL_UNAVAILABLE" :
            "H1_RECOVERED_NATIVE_ID_EVENT_INVALID";
        return false;
    }
    reason.clear();return true;
}

bool ExecutionCoordinator::ReserveResearchH1BracketForRecoveryOnlyOwner(
    const AgentExecutionContext& originalOwner,
    const hepta_bracket_h1::FrozenBracket& frozen,
    hepta_bracket_h1::Reservation& result, std::string& reason)
{
    return ReserveResearchH1BracketInternal(
        originalOwner, frozen, nullptr, result, reason);
}

bool ExecutionCoordinator::ReserveResearchH1BracketFromAdapterForRecoveryOnlyOwner(
    const AgentExecutionContext& originalOwner,
    const hepta_bracket_h1::FrozenBracket& frozenWithoutIds,
    const H1UnsentNativeIdAllocator& localIdAllocator,
    hepta_bracket_h1::Reservation& result, std::string& reason)
{
    return ReserveResearchH1BracketInternal(
        originalOwner, frozenWithoutIds, &localIdAllocator, result, reason);
}

bool ExecutionCoordinator::ReserveResearchH1BracketInternal(
    const AgentExecutionContext& originalOwner,
    hepta_bracket_h1::FrozenBracket frozen,
    const H1UnsentNativeIdAllocator* localIdAllocator,
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
        // The present owner map cannot attest that a colliding numeric
        // native Broker order ID belongs to a different IB client. Refuse
        // ambiguous reuse before writing any new shadow reservation.
        if(!localIdAllocator &&
           (owned.first==frozen.parentOrderId ||
            owned.first==frozen.childOrderId)) {
            reason="H1_BRACKET_NATIVE_ORDER_ID_COLLISION";
            return false;
        }
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
    // A new Owner-bound local pair is consumed only AFTER the durable
    // recovery-only fence, mutation state, and original command identity
    // have been independently checked while holding this same Coordinator
    // mutex. This method itself has no Broker sender. The injected callback
    // must be a trusted local-only adapter allocator, not an arbitrary
    // user/Agent-supplied operation or proof of transport authority.
    if(localIdAllocator) {
        if(frozen.parentOrderId!=0 || frozen.childOrderId!=0) {
            reason="H1_BRACKET_ADAPTER_PAIR_CALLER_IDS_NOT_ALLOWED";
            return false;
        }
        bool priorOriginal=false;
        const int replayed=m_journal.Replay([&](const OmsJournalEvent& e) {
            if(e.reqId==originalOwner.toolCallId ||
               e.clientReqId==originalOwner.toolCallId)
                priorOriginal=true;
        });
        if(replayed<0 || priorOriginal) {
            reason=replayed<0 ? "H1_BRACKET_ORIGINAL_JOURNAL_UNAVAILABLE" :
                "H1_BRACKET_EXISTING_OR_ORPHANED_ORIGINAL_COMMAND";
            return false;
        }
        long parent=-1, child=-1;
        if(!(*localIdAllocator)(frozen.account, frozen.clientId,
                frozen.connectionEpoch, parent, child, reason)) {
            if(reason.empty())reason="H1_BRACKET_NATIVE_LOCAL_PAIR_UNAVAILABLE";
            return false;
        }
        frozen.parentOrderId=parent;
        frozen.childOrderId=child;
        for(const auto& owned:m_orderOwners) {
            if(owned.first==parent || owned.first==child) {
                reason="H1_BRACKET_NATIVE_ORDER_ID_COLLISION";
                return false;
            }
        }
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
    bool nativeOrderIdCollision=false;
    const int observed=m_journal.Replay([&](const OmsJournalEvent& e) {
        // Native IDs are scoped to the Broker sender's client, but legacy
        // journal events cannot reliably prove a disjoint original client.
        // Fail closed on exact account/PAPER/IB numerical reuse.
        if(e.account==frozen.account && e.executionDomain=="PAPER" &&
           e.venue=="IB" &&
           (e.orderId==frozen.parentOrderId ||
            e.orderId==frozen.childOrderId)) nativeOrderIdCollision=true;
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
    if(observed<0 || collision || venueIdentityCollision || nativeOrderIdCollision) {
        reason=observed<0 ? "H1_BRACKET_ORIGINAL_JOURNAL_UNAVAILABLE" :
               collision ? "H1_BRACKET_EXISTING_OR_ORPHANED_ORIGINAL_COMMAND" :
               venueIdentityCollision ? "H1_BRACKET_FOREIGN_FROZEN_H1_IDENTITY_COLLISION" :
               "H1_BRACKET_NATIVE_ORDER_ID_COLLISION";
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

// Isolated research only: bind an adapter-consumed UNSENT numeric OCA EXIT ID
// to the same original Owner and critically durable OMS before returning it.
// Absolutely no native IB send, cancel, trading, or maintenance permission.
bool ExecutionCoordinator::ReserveResearchH1OcaExitForRecoveryOnlyOwner(
    const AgentExecutionContext& originalOwner,
    const hepta_bracket_h1::FrozenBracket& frozen,
    std::uint64_t nativeBrokerEpoch, long stopPermanentId,
    const H1UnsentNativeOcaExitAllocator& adapterLocalAllocator,
    H1LocalOcaExitReceipt& result, std::string& exitCorrelation,
    std::string& reason)
{
    result=H1LocalOcaExitReceipt{};
    exitCorrelation.clear();
    if(!adapterLocalAllocator || originalOwner.agentId.empty() ||
       originalOwner.sessionId.empty() || originalOwner.toolCallId.empty() ||
       originalOwner.agentId!=frozen.owner ||
       originalOwner.sessionId!=frozen.sessionId ||
       originalOwner.toolCallId!=frozen.originalCommandKey ||
       originalOwner.executionDomain!="IB-PAPER:EUR.USD" ||
       nativeBrokerEpoch==0 || stopPermanentId<=0 ||
       frozen.intentObservedAtMs>
           static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max())) {
        reason="H1_OCA_EXIT_ORIGINAL_OWNER_OR_EPOCH_INVALID";
        return false;
    }
    std::lock_guard<std::mutex> lock(m_mutex);
    const auto owner=m_recoveryOnlySessionOwners.find(
        OwnerKey(originalOwner.agentId,originalOwner.sessionId));
    if(owner==m_recoveryOnlySessionOwners.end() || owner->second==0 ||
       owner->second!=frozen.ownerFenceToken) {
        reason="H1_OCA_EXIT_COORDINATOR_RECOVERY_FENCE_MISSING";
        return false;
    }
    if(m_mutationBlocked || m_paperTerminalFencePresent ||
       m_riskMutationDispatchInFlight || m_venueDispatchesInFlight!=0 ||
       !m_cancelPreflightsInFlight.empty()) {
        reason="H1_OCA_EXIT_COORDINATOR_MUTATION_IN_FLIGHT";
        return false;
    }
    hepta_bracket_h1::BoundRefs refs;
    if(!hepta_bracket_h1::BuildFrozenRefs(frozen,refs,reason))return false;

    std::vector<OmsJournalEvent> original;
    original.reserve(24);
    bool oversized=false,priorExit=false;
    const int replayed=m_journal.Replay([&](const OmsJournalEvent& e){
        const bool sameOriginal=e.reqId==frozen.originalCommandKey ||
            e.clientReqId==frozen.originalCommandKey;
        const bool borrowed=e.requestHash==refs.groupCorrelation ||
            e.venueCorrelationId==refs.parentCorrelation ||
            e.venueCorrelationId==refs.childCorrelation ||
            e.venueCorrelationId==refs.ocaCorrelation;
        const bool originalNativeId=e.venue=="IB" &&
            e.executionDomain=="PAPER" &&
            (e.orderId==frozen.parentOrderId ||
             e.orderId==frozen.childOrderId);
        if(e.eventType=="bracket_oca_exit_reserved" &&
           (sameOriginal || borrowed))priorExit=true;
        if(sameOriginal || borrowed || originalNativeId) {
            if(original.size()>=96)oversized=true;
            else original.push_back(e);
        }
    });
    if(replayed<0 || oversized || priorExit) {
        reason=replayed<0 ? "H1_OCA_EXIT_ORIGINAL_JOURNAL_UNAVAILABLE" :
            oversized ? "H1_OCA_EXIT_ORIGINAL_EVIDENCE_UNBOUNDED" :
                        "H1_OCA_EXIT_ALREADY_DURABLE_NO_REISSUE";
        return false;
    }
    hepta_bracket_h1::Reservation attested;
    if(!hepta_bracket_h1::ConfirmReplayForOriginalNativeEcho(
            original,frozen,attested,reason) ||
       attested.latestNativeOpenOrderEpoch!=nativeBrokerEpoch ||
       attested.latestNativeStopPermanentId!=stopPermanentId ||
       attested.sendAuthorized || attested.driverStopAllowed) {
        reason="H1_OCA_EXIT_ORIGINAL_NATIVE_STOP_NOT_REATTESTED";
        return false;
    }

    // Callback must be bound by the caller to the real Adapter-local UNSENT
    // native cursor. The typed result is validated independently: a generic
    // callback is never an authority to contact the Broker.
    H1LocalOcaExitReceipt local;
    if(!adapterLocalAllocator(frozen.account,frozen.clientId,
            nativeBrokerEpoch,frozen.childOrderId,local,reason)) {
        if(reason.empty())reason="H1_OCA_EXIT_LOCAL_NATIVE_ID_UNAVAILABLE";
        return false;
    }
    if(local.paperAccount!=frozen.account ||
       local.brokerClientId!=frozen.clientId ||
       local.connectionEpoch!=nativeBrokerEpoch ||
       local.stopOrderId!=frozen.childOrderId ||
       local.orderId<=frozen.childOrderId ||
       local.orderId>std::numeric_limits<std::int32_t>::max() ||
       local.sendAuthorized || local.maintenanceAuthorized) {
        reason="H1_OCA_EXIT_LOCAL_ADAPTER_RECEIPT_UNVERIFIED";
        return false;
    }
    std::string correlation;
    if(!hepta_bracket_h1::BuildResearchH1OcaExitCorrelation(
            frozen,refs,local.orderId,stopPermanentId,correlation,reason))
        return false;
    bool duplicateNativeId=false,foreignCorrelation=false;
    const int tested=m_journal.Replay([&](const OmsJournalEvent& e){
        // Legacy OMS does not prove that a numeric IB ID belongs to a
        // different native Broker client, even across different DU accounts.
        if(e.venue=="IB" && e.executionDomain=="PAPER" &&
           e.orderId==local.orderId)duplicateNativeId=true;
        if(e.venueCorrelationId==correlation ||
           e.requestHash==correlation)foreignCorrelation=true;
    });
    if(tested<0 || duplicateNativeId || foreignCorrelation) {
        reason=tested<0 ? "H1_OCA_EXIT_NATIVE_ID_JOURNAL_UNAVAILABLE" :
            duplicateNativeId ? "H1_OCA_EXIT_NATIVE_ID_ALREADY_CONSUMED" :
                                "H1_OCA_EXIT_CORRELATION_ALREADY_USED";
        return false;
    }
    OmsJournalEvent event;
    event.eventType="bracket_oca_exit_reserved";
    event.tsMs=static_cast<long long>(frozen.intentObservedAtMs);
    event.orderId=local.orderId;
    event.reqId=frozen.originalCommandKey;
    event.clientReqId=frozen.originalCommandKey;
    event.eventId="unsent-oca-exit:"+refs.groupCorrelation;
    event.account=frozen.account;
    event.traceId=frozen.sessionId;
    event.strategy=frozen.owner;
    event.source="SHADOW_NO_BROKER_EGRESS";
    event.executionDomain="PAPER";
    event.venue="IB";
    event.instrument="EUR.USD";
    event.side=frozen.side=="BUY"?"SELL":"BUY";
    event.qty=frozen.quantity;
    event.price=frozen.stopPrice;
    event.requestHash=refs.groupCorrelation;
    event.venueCorrelationId=correlation;
    event.brokerClientId=frozen.clientId;
    event.brokerConnectionEpoch=nativeBrokerEpoch;
    if(!m_journal.AppendDurableCritical(event)) {
        reason="H1_OCA_EXIT_CRITICAL_OMS_FSYNC_UNCERTAIN";
        return false;
    }
    result=local;
    exitCorrelation=correlation;
    reason.clear();
    return true;
}
