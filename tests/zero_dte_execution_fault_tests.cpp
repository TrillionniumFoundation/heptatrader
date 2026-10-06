// Canonical coordinator with an in-process synthetic venue. No IB socket.
// A passing test is not an options permission, PAPER fill, or live restart proof.
#include "execution/execution_coordinator.h"
#include "options/zero_dte.h"
#include <cassert>
#include <cmath>
#include <fstream>
#include <iostream>
#include <set>
#include <sys/wait.h>
#include <unistd.h>

namespace {
std::string Temp(){char p[]="/tmp/hepta-option-fault-XXXXXX";int fd=mkstemp(p);assert(fd>=0);close(fd);return p;}
IbPlaceOrderCommand Open(const std::string& id){
    IbPlaceOrderCommand c;c.context.agentId="options-test-owner";c.context.sessionId="options-test-session";
    c.context.toolCallId=id;c.context.strategy="0dte-test";c.context.account="DU123";
    c.context.venue="IB";c.context.executionDomain="PAPER";
    c.context.decisionLeaseFencingToken=1;c.context.decisionLeaseGeneration=1;
    c.instrument="IBOPT:913173379";c.contract.symbol="XSP";c.contract.secType="OPT";
    c.contract.exchange="SMART";c.contract.currency="USD";c.contract.lastTradeDateOrContractMonth="20261005";
    c.contract.right="C";c.contract.strike=775;c.contract.multiplier="100";
    c.contract.tradingClass="XSP";c.contract.localSymbol="XSP   261005C00775000";
    c.order.action="BUY";c.order.orderType="MKT";c.order.totalQuantity=1;c.timeInForce="IOC";
    c.referencePrice=2;c.expiresAtMs=OmsJournal::NowEpochMs()+3600000;return c;
}
ExecutionCoordinatorCallbacks Base(){ExecutionCoordinatorCallbacks c;
    c.validateDecisionLease=[](const AgentExecutionContext&,const std::string&,std::string*){return true;};return c;}
std::string Correlation(OmsJournal& journal,const std::string& id,int* attempts=nullptr){
    std::string correlation;int n=0;
    assert(journal.Replay([&](const OmsJournalEvent& e){if(e.reqId==id && e.eventType=="place_send_attempt"){
        correlation=e.venueCorrelationId;++n;}})>=0);
    if(attempts)*attempts=n;return correlation;
}
void LostAckAndDriverReplay(){
    const std::string path=Temp();const auto cmd=Open("option-lost-ack-command");int sends=0;
    auto cb=Base();cb.placement=VenuePlacement::Immediate([&](const PlaceOrderCommand& c,const std::string& correlation){
        ++sends;assert(c.contract.localSymbol==cmd.contract.localSymbol && c.order.orderType=="MKT");
        assert(!correlation.empty());return VenuePlaceResult::Uncertain("simulated fill then lost ACK",42);
    });
    OmsJournal j;assert(j.Init(path));ExecutionCoordinator live(j,cb);
    assert(live.PlaceIbOrder(cmd).status==ExecutionCommandStatus::Uncertain && sends==1);
    assert(live.PlaceIbOrder(cmd).status==ExecutionCommandStatus::Uncertain && sends==1);
    auto changed=cmd;changed.contract.right="P";changed.contract.localSymbol="XSP   261005P00775000";
    assert(live.PlaceIbOrder(changed).status==ExecutionCommandStatus::Rejected && sends==1);
    ExecutionCoordinator restarted(j,cb);std::string reason;assert(!restarted.RecoverFromJournal(reason));
    assert(restarted.PlaceIbOrder(cmd).status==ExecutionCommandStatus::Uncertain && sends==1);
    int attempts=0;const std::string corr=Correlation(j,cmd.context.toolCallId,&attempts);assert(attempts==1);
    std::size_t resolved=0;
    assert(!restarted.ResolveUncertainPlaceCommands({{corr,42}},false,resolved,reason,false));
    assert(restarted.ResolveUncertainPlaceCommands({{corr,42}},true,resolved,reason,false) && resolved==1);
    assert(restarted.PlaceIbOrder(cmd).status==ExecutionCommandStatus::Duplicate && sends==1);
    // ConId, expiry, strike, multiplier, right all bind the SAME command identity.
    for(int k=0;k<5;++k){changed=cmd;
        if(k==0)changed.instrument="IBOPT:913173380";if(k==1)changed.contract.lastTradeDateOrContractMonth="20261006";
        if(k==2)changed.contract.strike++;if(k==3)changed.contract.multiplier="10";if(k==4)changed.contract.right="P";
        assert(restarted.PlaceIbOrder(changed).status==ExecutionCommandStatus::Rejected && sends==1);
    }
    ExecutionCoordinator twice(j,cb);assert(twice.RecoverFromJournal(reason));
    assert(twice.PlaceIbOrder(cmd).status==ExecutionCommandStatus::Duplicate && sends==1);unlink(path.c_str());
}
void CrashAfterSendBeforeAck(){
    const auto path=Temp();const auto cmd=Open("option-process-crash-command");
    const pid_t child=fork();assert(child>=0);
    if(child==0){
        OmsJournal j;if(!j.Init(path))_exit(80);auto cb=Base();
        cb.placement=VenuePlacement::Immediate([&](const PlaceOrderCommand&,const std::string&)->VenuePlaceResult{
            // Simulated venue received the request. The canonical send marker
            // must already be synced when the process dies before its ACK.
            int attempts=0;const auto corr=Correlation(j,cmd.context.toolCallId,&attempts);
            _exit(attempts==1 && !corr.empty()?77:81);
        });ExecutionCoordinator c(j,cb);c.PlaceIbOrder(cmd);_exit(82);
    }
    int status=0;assert(waitpid(child,&status,0)==child && WIFEXITED(status) && WEXITSTATUS(status)==77);
    OmsJournal j;assert(j.Init(path));int resent=0;auto cb=Base();
    cb.placement=VenuePlacement::Immediate([&](const PlaceOrderCommand&,const std::string&){++resent;return VenuePlaceResult::Submitted(43);});
    ExecutionCoordinator replay(j,cb);std::string reason;assert(!replay.RecoverFromJournal(reason));
    assert(replay.PlaceIbOrder(cmd).status==ExecutionCommandStatus::Uncertain && resent==0);
    std::size_t resolved=0;const auto correlation=Correlation(j,cmd.context.toolCallId);
    assert(replay.ResolveUncertainPlaceCommands({{correlation,43}},true,resolved,reason,false));
    assert(resolved==1 && replay.PlaceIbOrder(cmd).status==ExecutionCommandStatus::Duplicate && resent==0);
    unlink(path.c_str());
}
void FillRacesCancellation() {
    const auto path=Temp();OmsJournal journal;assert(journal.Init(path));
    auto command=Open("option-cancel-race-entry");int sends=0,cancels=0;bool fillObserved=false;
    auto callbacks=Base();
    callbacks.placement=VenuePlacement::Immediate([&](const PlaceOrderCommand&,const std::string&){
        ++sends;return VenuePlaceResult::Submitted(45);
    });
    callbacks.cancelOrder=[&](long id){assert(id==45);++cancels;fillObserved=true;
        return VenueCancelResult::Uncertain("simulated fill arrived before cancel acknowledgement");};
    ExecutionCoordinator coordinator(journal,callbacks);
    assert(coordinator.PlaceIbOrder(command).status==ExecutionCommandStatus::Accepted);
    IbCancelOrderCommand cancel;cancel.context=command.context;
    cancel.context.toolCallId="option-cancel-race-command";cancel.orderId=45;
    assert(coordinator.CancelIbOrder(cancel).status==ExecutionCommandStatus::Uncertain);
    assert(fillObserved && sends==1 && cancels==1);
    ExecutionCoordinator recovered(journal,callbacks);std::string reason;
    assert(!recovered.RecoverFromJournal(reason));std::size_t resolved=0;
    assert(recovered.ResolveUncertainCancelCommands({},true,{{45,"Filled"}},{45},true,resolved,reason));
    assert(resolved==1);ExecutionCommandResult result;
    assert(recovered.GetCommandStatus(command.context.agentId,command.context.sessionId,cancel.context.toolCallId,result));
    assert(result.status==ExecutionCommandStatus::Rejected && result.reasonCode=="AUTHORITATIVE_CANCEL_TARGET_FILLED");
    assert(recovered.CancelIbOrder(cancel).status==ExecutionCommandStatus::Duplicate && cancels==1);
    assert(recovered.PlaceIbOrder(command).status==ExecutionCommandStatus::Duplicate && sends==1);
    unlink(path.c_str());
}
void DataLossAtFinalCheck(){
    const auto path=Temp();OmsJournal j;assert(j.Init(path));int sends=0;auto cb=Base();
    cb.preVenuePlaceCheck=[](const IbPlaceOrderCommand&,std::string* reason){*reason="OPTION_ASK_STALE_OR_MISSING";return false;};
    cb.placement=VenuePlacement::Immediate([&](const PlaceOrderCommand&,const std::string&){++sends;return VenuePlaceResult::Submitted(44);});
    ExecutionCoordinator c(j,cb);auto cmd=Open("option-data-loss-command");
    assert(c.PlaceIbOrder(cmd).status==ExecutionCommandStatus::Rejected && sends==0);
    // Even a locally rejected request retains idempotency; clearing a stale
    // quote is not permission to silently alter the old request's identity.
    assert(c.PlaceIbOrder(cmd).status==ExecutionCommandStatus::Duplicate && sends==0);unlink(path.c_str());
}
}
int main(){
    if(geteuid()==0){std::cerr<<"Run native process tests as an unprivileged user\n";return 1;}
    LostAckAndDriverReplay();CrashAfterSendBeforeAck();FillRacesCancellation();DataLossAtFinalCheck();
    std::cout<<"PASS: actual ExecutionCoordinator option identities; lost ACK, no resend, fork/crash, journal restore, stale final quote rejection. SYNTHETIC ONLY.\n";
}
