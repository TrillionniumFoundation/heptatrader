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
int main(){
 try{
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
