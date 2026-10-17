#include "../HeptaTrade/adapter_ib/ib_native_h1_pair_research.h"
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
#include <unistd.h>

using namespace hepta_bracket_h1;
using namespace hepta_ib_native_h1_transport;
namespace {
void Require(bool ok,const char* why){if(!ok)throw std::runtime_error(why);}
FrozenBracket Frozen() {
    FrozenBracket f;
    f.originalCommandKey="transport-test-command-001";
    f.account="DU-SYNTHETIC";f.owner="synthetic-owner";
    f.sessionId="synthetic-session";
    f.executionDomain="PAPER";f.contractId=12087792;
    f.clientId=991;f.parentOrderId=4100;f.childOrderId=4101;
    f.connectionEpoch=81;f.positionGeneration=91;
    f.ownerFenceToken=23;f.intentObservedAtMs=1792080000123;
    f.side="BUY";f.quantity=89200;f.referencePrice=1.1205;
    f.stopPrice=1.11825;f.minTick=.00005;return f;
}
struct FakeEClient {
    int called=0;
    int throwAt=0;
    std::vector<Order> orders;
    std::vector<Contract> contracts;
    void placeOrder(long id,const Contract& contract,const Order& order) {
        ++called;
        Require(order.orderId==id,"TWS ID and SDK Order ID mismatch");
        orders.push_back(order);
        contracts.push_back(contract);
        if(throwAt && called==throwAt)throw std::runtime_error("fake socket interruption");
    }
};
std::string UniqueLedger() {
    const char* env=getenv("TMPDIR");const std::string prefix=(env?env:"/tmp");
    std::string pattern=prefix+"/native-h1-pair-XXXXXX";
    std::vector<char> path(pattern.begin(),pattern.end());path.push_back('\0');
    int fd=mkstemp(path.data());Require(fd>=0,"mkstemp");
    close(fd);Require(unlink(path.data())==0,"unlink temp");
    return path.data();
}
void Scenario(int fakeFailure) {
    const std::string path=UniqueLedger();
    auto f=Frozen();
    FakeEClient sender;sender.throwAt=fakeFailure;
    {
        OmsJournal journal;Require(journal.Init(path),"oms init failed");
        Reservation reserve;std::string reason;
        Require(ReservePair(journal,f,reserve,reason),"native frozen pair durable reservation failed");
        NativePair candidate;
        Require(PrepareNativePaperPair(f,reserve.ids,candidate,reason),
                "true IB SDK native H1 pair rejected");
        Require(candidate.parent.orderRef==reserve.ids.parentRef &&
                candidate.child.orderRef==reserve.ids.childRef &&
                candidate.child.ocaGroup==reserve.ids.ocaGroup &&
                candidate.parent.orderRef.size()==45 &&
                candidate.child.orderRef.size()==45 &&
                candidate.parent.transmit==false &&
                candidate.child.transmit==true &&
                candidate.child.parentId==candidate.parent.orderId &&
                DecimalFunctions::decimalToDouble(candidate.child.totalQuantity)==f.quantity,
                "wrong native bracket fields");
        Require(!candidate.brokerSendAuthorized && !candidate.stopWorking &&
                !candidate.driverStopAuthorized,"native builder gave authority");
        const auto outcome=ExerciseDurablePairTransport(journal,reserve,sender);
        const int wantCalls=fakeFailure==1?1:2;
        Require(sender.called==wantCalls,"unexpected number of fake order calls");
        const PairDispatchStage expected=fakeFailure==1 ?
            PairDispatchStage::ParentPossiblySent : fakeFailure==2 ?
            PairDispatchStage::ChildPossiblySent :
            PairDispatchStage::LocalCallsReturnedBrokerUnconfirmed;
        Require(outcome.stage==expected && !outcome.liveProtectionProven &&
                !outcome.mayRetry,"broken outcome after unknown send");
        if(sender.called>=2) {
            const Order& p=sender.orders[0];
            const Order& s=sender.orders[1];
            Require(!p.transmit&&s.transmit &&
                    p.orderType=="MKT"&&s.orderType=="STP" &&
                    s.parentId==p.orderId && s.ocaType==1 &&
                    s.ocaGroup==reserve.ids.ocaGroup &&
                    s.auxPrice==f.stopPrice,"fake EClient wire path altered original bracket");
        }
        std::vector<OmsJournalEvent> events;
        const int len=journal.Replay([&](const OmsJournalEvent& evt){events.push_back(evt);});
        Require(len==2+wantCalls,"wrong OMS durable attempts");
        Require(events[0].eventType=="bracket_parent_reserved" &&
                events[1].eventType=="bracket_child_reserved" &&
                events[2].eventType=="bracket_parent_send_attempt" &&
                events[2].status=="POSSIBLY_SENT",
                "parent send was not journaled in advance");
        if(wantCalls==2)
            Require(events[3].eventType=="bracket_child_send_attempt" &&
                    events[3].status=="POSSIBLY_SENT",
                    "child send was not journaled in advance");
        Reservation recovery;
        Require(!ConfirmReplay(events,f,recovery,reason)&&recovery.uncertain,
                "Broker uncertainty recovered as free order IDs");
        const auto attemptAgain=ExerciseDurablePairTransport(journal,reserve,sender);
        Require(attemptAgain.stage==PairDispatchStage::Refused &&
                sender.called==wantCalls,"duplicate native pair was emitted");
    }
    {
        OmsJournal restart;Require(restart.Init(path),"restart OMS open failed");
        std::vector<OmsJournalEvent> events;
        Require(restart.Replay([&](const OmsJournalEvent& e){events.push_back(e);})==
                2+(fakeFailure==1?1:2),"reboot lost native send attempt");
        Reservation recovery;std::string reason;
        Require(!ConfirmReplay(events,f,recovery,reason) && recovery.uncertain,
                "restart granted retransmission of unacknowledged bracket");
    }
    Require(unlink(path.c_str())==0,"isolated OMS cleanup");
}
void NegativeScope() {
    auto f=Frozen();BoundRefs bound;std::string reason;
    Require(BuildFrozenRefs(f,bound,reason),"base H1 missing");
    NativePair result;
    f.executionDomain="LIVE";
    Require(!PrepareNativePaperPair(f,bound,result,reason) &&
            !result.brokerSendAuthorized,"LIVE native pair permitted");
    f=Frozen();f.clientId=1000;
    Require(!PrepareNativePaperPair(f,bound,result,reason),
            "foreign IB client reused original stop identity");
    f=Frozen();f.stopPrice+=.00005;
    Require(!PrepareNativePaperPair(f,bound,result,reason),
            "changed stop ref reused");
    f=Frozen();bound.childRef=bound.parentRef;
    Require(!PrepareNativePaperPair(f,bound,result,reason),
            "swapped parent and child H1 accepted");
}
}
int main(){
    try {
        Scenario(0);Scenario(1);Scenario(2);NegativeScope();
        std::cout<<"native_H1_parent_stop_transport_fake_EClient=PASS\n";
        return 0;
    }catch(const std::exception& e){
        std::cerr<<"FAILED: "<<e.what()<<"\n";return 1;
    }
}
