#include "../HeptaTrade/execution/execution_coordinator.h"
#include "../HeptaTrade/execution/ib_bracket_h1_oms_reservation.h"
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
#include <unistd.h>
using namespace hepta_bracket_h1;
static void Must(bool ok,const char* label){if(!ok)throw std::runtime_error(label);}
static FrozenBracket Example(){
    FrozenBracket f;f.owner="agent-a";f.sessionId="session-1";
    f.originalCommandKey="new-h1-bracket-001";
    f.account="DU-SYNTHETIC";f.executionDomain="PAPER";
    f.side="BUY";f.contractId=12087792;f.clientId=991;
    f.parentOrderId=4100;f.childOrderId=4101;
    f.connectionEpoch=81;f.positionGeneration=91;
    f.ownerFenceToken=31;f.intentObservedAtMs=1792080000123ULL;
    f.quantity=89200;f.referencePrice=1.1205;f.stopPrice=1.11825;
    f.minTick=.00005;return f;
}
static AgentExecutionContext Context(){
    AgentExecutionContext c;c.agentId="agent-a";c.sessionId="session-1";
    c.toolCallId="new-h1-bracket-001";
    c.executionDomain="IB-PAPER:EUR.USD";return c;
}
// A Coordinator admission scan must reject other original commands that
// borrow this frozen H1 group or child correlation, not only duplicate req_id.
static void ForeignH1IdentityAdmission(int borrowedRole) {
    const bool shouldReject=borrowedRole!=0;
    auto frozen=Example();auto owner=Context();
    std::vector<char> name;
    const std::string pattern="/tmp/Hepta-H1-Admission-XXXXXX";
    name.assign(pattern.begin(),pattern.end());name.push_back('\0');
    const int fd=::mkstemp(name.data());Must(fd>=0,"collision-temp");
    ::close(fd);Must(::unlink(name.data())==0,"collision-unlink");
    const std::string path(name.data());
    OmsJournal journal;Must(journal.Init(path),"collision-journal-init");
    ExecutionCoordinatorCallbacks callbacks;
    ExecutionCoordinator coordinator(journal,callbacks);
    std::string reason;Reservation output;BoundRefs binding;
    Must(BuildFrozenRefs(frozen,binding,reason),"collision-identity-invalid");
    Must(coordinator.EnterRecoveryOnlyOwner(owner.agentId,owner.sessionId,
         frozen.ownerFenceToken,reason),"collision-recovery-fence-failed");
    auto foreign=Intent(frozen,binding,true);
    foreign.reqId="unrelated-original-command";
    foreign.clientReqId=foreign.reqId;
    foreign.eventId="unrelated-original-event";
    const std::string otherGroup=HashCanonical("foreign-unrelated-group");
    const std::string otherRef=HashCanonical("foreign-unrelated-venue-ref");
    foreign.requestHash=otherGroup;
    foreign.venueCorrelationId=otherRef;
    // 0 = unrelated, 1..4 reuse a frozen group/role as requestHash,
    // 5..8 reuse the same binding as the venue order correlation.
    if(borrowedRole==1)foreign.requestHash=binding.groupCorrelation;
    if(borrowedRole==2)foreign.requestHash=binding.parentCorrelation;
    if(borrowedRole==3)foreign.requestHash=binding.childCorrelation;
    if(borrowedRole==4)foreign.requestHash=binding.ocaCorrelation;
    if(borrowedRole==5)foreign.venueCorrelationId=binding.groupCorrelation;
    if(borrowedRole==6)foreign.venueCorrelationId=binding.parentCorrelation;
    if(borrowedRole==7)foreign.venueCorrelationId=binding.childCorrelation;
    if(borrowedRole==8)foreign.venueCorrelationId=binding.ocaCorrelation;
    Must(journal.AppendDurableCritical(foreign),"collision-foreign-evidence-write");
    const bool admitted=coordinator.ReserveResearchH1BracketForRecoveryOnlyOwner(
         owner,frozen,output,reason);
    if(shouldReject) {
        Must(!admitted &&
             reason=="H1_BRACKET_FOREIGN_FROZEN_H1_IDENTITY_COLLISION",
             "foreign command borrowed frozen H1 group or child without refusal");
        Must(!output.sendAuthorized && !output.driverStopAllowed,
             "foreign event minted send/stop authority");
    } else {
        Must(admitted && output.replayComplete && !output.sendAuthorized,
             "fully disjoint historical original command incorrectly blocked");
    }
    int replayed=journal.Replay([](const OmsJournalEvent&){});
    Must(replayed==(shouldReject?2:4),"failed admission appended unknown reservation");
    Must(::unlink(path.c_str())==0,"collision-journal-cleanup");
}

int main(){
 try{
    ForeignH1IdentityAdmission(0); // independent historical command allowed
    for(int role=1;role<=8;++role) ForeignH1IdentityAdmission(role);
    std::string name="/tmp/Hepta-H1-OwnerFence-XXXXXX";
    std::vector<char> path(name.begin(),name.end());path.push_back('\0');
    int fd=::mkstemp(path.data());Must(fd>=0,"temp-file");
    ::close(fd);Must(::unlink(path.data())==0,"unlink-reservation");
    std::string file=path.data();
    OmsJournal journal;Must(journal.Init(file),"owner-ledger-init");
    ExecutionCoordinatorCallbacks callbacks;
    ExecutionCoordinator coordinator(journal,callbacks);
    std::string reason;Reservation result;
    auto f=Example();auto identity=Context();
    Must(!coordinator.ReserveResearchH1BracketForRecoveryOnlyOwner(
          identity,f,result,reason) &&
          reason=="H1_BRACKET_COORDINATOR_RECOVERY_FENCE_MISSING",
          "unfenced owner reserved protective bracket");
    Must(coordinator.EnterRecoveryOnlyOwner(
          identity.agentId,identity.sessionId,31,reason),"root-fence-failed");
    Must(coordinator.SessionOwnerRecoveryIngressFence(
          identity.agentId,identity.sessionId)==31,
          "coordinator fence token not authoritative");
    auto wrong=f;wrong.ownerFenceToken=30;
    Must(!coordinator.ReserveResearchH1BracketForRecoveryOnlyOwner(
          identity,wrong,result,reason) &&
          reason=="H1_BRACKET_COORDINATOR_RECOVERY_FENCE_MISSING",
          "non-matching owner fence admitted");
    wrong=f;wrong.sessionId="another";
    Must(!coordinator.ReserveResearchH1BracketForRecoveryOnlyOwner(
          identity,wrong,result,reason) &&
          reason=="H1_BRACKET_ORIGINAL_OWNER_SESSION_MISMATCH",
          "foreign session got another owner's fence");
    Must(coordinator.ReserveResearchH1BracketForRecoveryOnlyOwner(
          identity,f,result,reason),"genuine owner token failed to reserve OMS pair");
    Must(result.replayComplete && !result.sendAuthorized &&
         !result.driverStopAllowed,"owner fence was used as Broker send permit");
    Must(!coordinator.ReserveResearchH1BracketForRecoveryOnlyOwner(
          identity,f,result,reason) &&
          reason=="H1_BRACKET_EXISTING_OR_ORPHANED_ORIGINAL_COMMAND",
          "duplicate reservation appended to OMS");
    std::vector<OmsJournalEvent> entries;
    Must(journal.Replay([&](const OmsJournalEvent& e){entries.push_back(e);})==3,
        "root fence plus exactly two H1 reservations not persisted");
    Reservation observed;
    Must(ConfirmReplay(entries,f,observed,reason),"owner-backed H1 pair replay failed");
    Must(!observed.sendAuthorized,"valid H1 pair still has no Broker authority");
    // Recover the actual Coordinator and prove both the recovery-only
    // no-new-entry fence and the original H1 pair survive process restart.
    {
        ExecutionCoordinator restarted(journal,callbacks);
        Must(restarted.RecoverFromJournal(reason),"native Coordinator OMS restart failed");
        Must(restarted.SessionOwnerRecoveryIngressFence(
            identity.agentId,identity.sessionId)==31,
            "restart lost authoritative ingress fence");
        Must(!restarted.ReserveResearchH1BracketForRecoveryOnlyOwner(
            identity,f,result,reason) &&
            reason=="H1_BRACKET_EXISTING_OR_ORPHANED_ORIGINAL_COMMAND",
            "restart permitted duplicated parent+child reservation");
    }
    Must(coordinator.EnterRecoveryOnlyOwner(
        identity.agentId,identity.sessionId,32,reason),"fence epoch rollover rejected");
    Must(!coordinator.ReserveResearchH1BracketForRecoveryOnlyOwner(
        identity,f,result,reason) &&
        reason=="H1_BRACKET_COORDINATOR_RECOVERY_FENCE_MISSING",
        "stale ingress token silently reused");
    Must(::unlink(file.c_str())==0,"test scratch journal cleanup");
    std::cout<<"execution_coordinator_h1_owner_fence=PASS\n";
    return 0;
 }catch(const std::exception& e){std::cerr<<"FAIL: "<<e.what()<<'\n';return 1;}
}
