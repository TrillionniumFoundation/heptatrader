#include "adapter_ib/ib_gateway_adapter.h"
#include "adapter_ib/ib_venue_correlation.h"
#include "execution/execution_coordinator.h"
#include <cassert>
#include <cmath>
#include <cstdio>
#include <deque>
#include <functional>
#include <limits>
#include <memory>
#include <unistd.h>

namespace {
void TestWrapperQuantityTrackerCannotMixBrokerClients() {
    IBContractLite contract;contract.symbol="EUR";contract.secType="CASH";
    contract.exchange="IDEALPRO";contract.currency="USD";
    IBOrderLite order;order.totalQuantity=1;order.action="BUY";std::string reason;
    assert(IbVenueCorrelationCodec::EncodeOrderRef("hepta-v1-sha256:"+std::string(64,'a'),order.orderRef,reason));
    const auto execution=[&](long id,double quantity,double price) {
        IBEvent event;event.type=IBEventType::ExecutionDetails;event.id=id;
        event.connectionEpoch=1;event.requestId=-1;event.brokerClientId=73;
        event.account="DU123";event.key="execution";event.value="BOT";
        event.number=price;event.number2=quantity;event.contract=contract;
        event.order.orderRef=order.orderRef;return event;
    };
    IBSubmittedOrderQuantityTracker quantities;
    quantities.Reset(1,73,"DU123");quantities.Record(41,contract,order);
    quantities.ObserveOrderStatus(74,41,"Cancelled",0,1,0);
    quantities.ObserveOrderStatus(-1,41,"Filled",1,0,1.18);
    assert(quantities.ObserveExecution(execution(41,0.25,1.18))==1);
    const std::vector<std::function<void(IBEvent&)>> wrongExecutions={
        [](IBEvent& e){++e.brokerClientId;},[](IBEvent& e){e.brokerClientId=-1;},
        [](IBEvent& e){e.account="DU999";},[](IBEvent& e){e.order.orderRef="H1bad";},
        [](IBEvent& e){e.contract.symbol="GBP";},[](IBEvent& e){e.contract.secType="STK";},
        [](IBEvent& e){e.contract.currency="JPY";},[](IBEvent& e){e.contract.exchange="OTHER";},
        [](IBEvent& e){e.value="SLD";},[](IBEvent& e){e.key.clear();},
        [](IBEvent& e){e.requestId=5;},[](IBEvent& e){e.requestId=-2;},
        [](IBEvent& e){e.requestId=-99;},[](IBEvent& e){e.connectionEpoch=0;},
        [](IBEvent& e){++e.connectionEpoch;}};
    for(const auto& change:wrongExecutions) {
        auto wrong=execution(41,1,1.18);change(wrong);
        assert(quantities.ObserveExecution(wrong)==0);
        // A rejected full fill must not consume the total needed for the
        // subsequent legitimate exec-only synthetic-status lifecycle.
        assert(quantities.ObserveExecution(execution(41,0.5,1.18))==1);
    }
    // Near-full partials retain the true total; no epsilon can manufacture
    // Filled or erase the quantity before the final real execution arrives.
    assert(quantities.ObserveExecution(execution(41,1-5e-10,1.18))==1);
    assert(quantities.ObserveExecution(execution(41,1,1.18))==1);
    assert(quantities.ObserveExecution(execution(41,1,1.18))==0);

    quantities.Record(42,contract,order);
    quantities.ObserveOrderStatus(73,42,"Filled",0.5,0,1.18);
    assert(quantities.ObserveExecution(execution(42,2,1.18))==0);
    assert(quantities.ObserveExecution(execution(42,1,0))==0);
    assert(quantities.ObserveExecution(execution(42,
        std::numeric_limits<double>::quiet_NaN(),1.18))==0);
    assert(quantities.ObserveExecution(execution(42,0.5,1.18))==1);
    quantities.ObserveOrderStatus(73,42,"ApiCancelled",0.5,0.5,1.18);
    assert(quantities.ObserveExecution(execution(42,0.5,1.18))==0);
    quantities.Record(43,contract,order);quantities.ObserveOrderStatus(73,43,"Filled",1,0,1.18);
    assert(quantities.ObserveExecution(execution(43,1,1.18))==0);
    quantities.Record(44,contract,order);quantities.Reset(2,73,"DU123");
    assert(quantities.ObserveExecution(execution(44,1,1.18))==0);
}
class Broker : public IIBApiWrapper {
public:
    bool connected=false;std::uint64_t epoch=0;int executionsRequest=0,cancels=0;
    IBOrderLite submitted;IBContractLite contract;long orderId=-1;
    std::deque<IBEvent> events;
    bool Connect(const IBConnectParams&) override {connected=true;return true;}
    void SetConnectionEpoch(std::uint64_t value) override {epoch=value;}
    void Disconnect() override {connected=false;}
    bool IsConnected() const override {return connected;}
    const char* GetStatusString() const override {return "fixture";}
    bool ReqAccountSummary() override {return true;}
    bool ReqPositions() override {return true;}
    bool ReqAllOpenOrders() override {return true;}
    bool ReqCompletedOrders() override {return true;}
    bool ReqExecutions(int id) override {executionsRequest=id;return true;}
    bool ReqMktData(int,const IBContractLite&) override {return true;}
    bool CancelMktData(int) override {return true;}
    bool PlaceOrder(long id,const IBContractLite& c,const IBOrderLite& o) override {
        orderId=id;contract=c;submitted=o;return true;
    }
    bool CancelOrder(long) override {++cancels;return true;}
    bool PollOnce(int) override {return true;}
    bool TryDequeueEvent(IBEvent& event) override {
        if(events.empty()) return false;
        event=events.front();events.pop_front();return true;
    }
    long GetLastValidOrderId() const override {return 41;}
};
struct Fixture {
    Broker* broker;HeptaIBGatewayAdapter adapter;
    Fixture(bool finishBootstrap=true):broker(new Broker()),adapter(
        std::unique_ptr<IIBApiWrapper>(broker), [this]() {
            broker=new Broker();return std::unique_ptr<IIBApiWrapper>(broker);
        }) {
        HeptaIBConfig cfg;cfg.account="DU123";cfg.clientId=73;cfg.readOnly=false;
        cfg.risk.enableOrderSubmission=true;cfg.risk.maxDailyOrders=100;
        cfg.risk.duplicateOrderWindowSec=0;cfg.risk.maxPriceDeviationBps=0;
        assert(adapter.Init(cfg));assert(adapter.Connect());Bootstrap(finishBootstrap);
    }
    int Drain() {IBEvent event;int count=0;while(adapter.TryDequeueEvent(event)) ++count;return count;}
    int Push(IBEvent event) {broker->events.push_back(event);return Drain();}
    IBEvent Event(IBEventType type) const {
        IBEvent event;event.type=type;event.connectionEpoch=broker->epoch;
        event.brokerClientId=73;event.id=broker->orderId;event.account="DU123";return event;
    }
    void Bootstrap(bool finish=true) {
        assert(adapter.ReqAuthoritativeOpenOrders());Push(Event(IBEventType::OpenOrderEnd));
        assert(adapter.ReqTerminalCorrelations());if(finish) FinishBootstrap();
    }
    void FinishBootstrap() {
        Push(Event(IBEventType::CompletedOrdersEnd));
        auto end=Event(IBEventType::ExecutionDetailsEnd);end.requestId=broker->executionsRequest;Push(end);
        assert(adapter.GetAuthoritativeTerminalCorrelationSnapshot().complete);
    }
    long Place(const std::string& correlation="hepta-v1-sha256:"+std::string(64,'a')) {
        IBContractLite c;c.symbol="EUR";c.secType="CASH";c.exchange="IDEALPRO";c.currency="USD";
        return PlaceContract(c,correlation);
    }
    long PlaceContract(const IBContractLite& c,
        const std::string& correlation="hepta-v1-sha256:"+std::string(64,'a')) {
        IBOrderLite o;o.action="BUY";o.orderType="LMT";o.lmtPrice=1.18;o.totalQuantity=1;
        long id=-1;assert(adapter.PlaceOrderCorrelated(c,o,correlation,&id));return id;
    }
    void AbsorbFill(long id) {
        assert(adapter.ReqRiskRefresh());
        FinishRiskRefresh();
        assert(adapter.AcknowledgePostFillRiskReconciled(id));
    }
    void FinishRiskRefresh() {
        auto value=Event(IBEventType::AccountValue);value.key="NetLiquidation:USD";value.value="1000";Push(value);
        Push(Event(IBEventType::AccountSummaryEnd));Push(Event(IBEventType::PositionEnd));
    }
    IBEvent Execution(double quantity=1) const {
        auto e=Event(IBEventType::ExecutionDetails);e.requestId=-1;e.key="execution-fixture";
        e.value="BOT";e.number=1.18;e.number2=quantity;e.number3=1-quantity;
        e.contract=broker->contract;e.order.orderRef=broker->submitted.orderRef;return e;
    }
    IBEvent Status(const std::string& status="Filled",double filled=1) const {
        auto e=Event(IBEventType::OrderStatus);e.key=status;e.number=filled>0?1.18:0;
        e.number2=filled;e.number3=status=="Filled"?0:1-filled;return e;
    }
};
void TestSameEpochPostBootstrapLiveFillAndDeferredCancel() {
    Fixture f;const auto initial=f.adapter.GetAuthoritativeTerminalCorrelationSnapshot();
    const long id=f.Place();assert(f.adapter.CancelOrder(id));
    assert(f.adapter.GetLastRejectReason()=="IB_CANCEL_DEFERRED_UNTIL_BROKER_ACK");
    f.Push(f.Status());
    assert(f.adapter.GetAuthoritativeTerminalCorrelationSnapshot().executionOrderIds.empty());
    f.Push(f.Execution());
    const auto live=f.adapter.GetAuthoritativeTerminalCorrelationSnapshot();
    assert(live.complete&&live.connectionEpoch==initial.connectionEpoch&&live.generation>initial.generation);
    assert(live.executionOrderIds.count(id)==1&&live.terminalOrderIdsByCorrelation.begin()->second==id);
    assert(live.terminalStatusesByCorrelation.begin()->second=="Filled");
    assert(live.exposureGeneration>initial.exposureGeneration&&f.broker->cancels==0);
    f.Push(f.Execution());f.Push(f.Status());
    assert(f.adapter.GetAuthoritativeTerminalCorrelationSnapshot().generation==live.generation);

    char path[]="/tmp/hepta-live-cancel-XXXXXX";const int fd=mkstemp(path);assert(fd>=0);close(fd);
    OmsJournal journal;assert(journal.Init(path));OmsJournalEvent event;
    event.schemaVersion=OmsJournal::kSchemaVersion;event.eventType="cancel";event.tsMs=OmsJournal::NowEpochMs();
    event.eventId="deferred-fixture";event.orderId=id;event.reqId="cancel-fixture";event.clientReqId=event.reqId;
    event.traceId="session";event.source="agent.tool:agent";event.strategy="fixture";event.account="DU123";
    event.venue="IB";event.executionDomain="PAPER:fixture";event.instrument="EUR.USD";event.side="BUY";
    event.status="cancel_pending";event.riskCode="IB_CANCEL_DEFERRED_UNTIL_BROKER_ACK";event.requestHash="sha256:fixture";
    assert(journal.Append(event));ExecutionCoordinator coordinator(journal,ExecutionCoordinatorCallbacks());
    std::string reason;assert(!coordinator.RecoverFromJournal(reason));
    std::size_t resolved=0;
    // A positive partial/full execution must not resolve a still-active target.
    assert(coordinator.ResolveUncertainCancelCommands({id},true,{},live.executionOrderIds,true,resolved,reason));
    assert(resolved==0);
    f.AbsorbFill(id);
    assert(f.adapter.ReqAuthoritativeOpenOrders());f.Push(f.Event(IBEventType::OpenOrderEnd));
    std::map<long,std::string> terminal;terminal[id]="Filled";
    assert(coordinator.ResolveUncertainCancelCommands(f.adapter.GetAuthoritativeCorrelationSnapshot().activeOrderIds,
        true,terminal,live.executionOrderIds,true,resolved,reason));
    assert(resolved==1);ExecutionCommandResult result;
    assert(coordinator.GetCommandStatus("agent","session","cancel-fixture",result));
    assert(result.status==ExecutionCommandStatus::Rejected&&result.reasonCode=="AUTHORITATIVE_CANCEL_TARGET_FILLED");
    ExecutionCoordinator replay(journal,ExecutionCoordinatorCallbacks());assert(replay.RecoverFromJournal(reason));
    assert(replay.GetCommandStatus("agent","session","cancel-fixture",result));
    assert(result.reasonCode=="AUTHORITATIVE_CANCEL_TARGET_FILLED");std::remove(path);
}
void TestCancelledAndPartialCancelled() {
    for(const char* status:{"Cancelled","ApiCancelled","Inactive","Rejected"}) {
        Fixture f;const long id=f.Place();const auto gen=f.adapter.GetAuthoritativeTerminalCorrelationSnapshot().generation;
        f.Push(f.Status(status,0));const auto terminal=f.adapter.GetAuthoritativeTerminalCorrelationSnapshot();
        assert(terminal.complete&&terminal.generation>gen&&terminal.terminalStatusesByCorrelation.begin()->second==status);
        assert(terminal.terminalOrderIdsByCorrelation.begin()->second==id&&terminal.executionOrderIds.empty());
    }
    Fixture f;const long id=f.Place();f.Push(f.Execution(0.25));
    const auto firstPartial=f.adapter.GetAuthoritativeTerminalCorrelationSnapshot();
    f.Push(f.Execution(0.5));
    auto partial=f.adapter.GetAuthoritativeTerminalCorrelationSnapshot();
    assert(partial.executionOrderIds.count(id)==1&&partial.terminalOrderIdsByCorrelation.empty());
    assert(partial.generation>firstPartial.generation&&partial.exposureGeneration>firstPartial.exposureGeneration);
    f.Push(f.Execution(0.25));
    assert(f.adapter.GetAuthoritativeTerminalCorrelationSnapshot().generation==partial.generation);
    f.Push(f.Status("Cancelled",0.5));const auto terminal=f.adapter.GetAuthoritativeTerminalCorrelationSnapshot();
    assert(terminal.terminalStatusesByCorrelation.begin()->second=="Cancelled"&&terminal.executionOrderIds.count(id)==1);
    assert(terminal.generation>partial.generation);
}
void TestBoundContractExtensionsAndBrokerEnrichment() {
    IBContractLite contract;contract.symbol="EUR";contract.secType="CASH";
    contract.exchange="IDEALPRO";contract.currency="USD";
    contract.primaryExchange="IDEALPRO";contract.localSymbol="EUR.USD";
    contract.multiplier="1";contract.tradingClass="FX";
    // Every optional field specified by the actual send is binding, including
    // derivative fields on contracts where the general adapter permits them.
    contract.lastTradeDateOrContractMonth="202609";contract.right="C";contract.strike=1;
    const std::vector<std::function<void(IBEvent&)>> mismatches={
        [](IBEvent& e){e.contract.primaryExchange="OTHER";},
        [](IBEvent& e){e.contract.localSymbol="GBP.USD";},
        [](IBEvent& e){e.contract.lastTradeDateOrContractMonth="202610";},
        [](IBEvent& e){e.contract.right="P";},[](IBEvent& e){e.contract.strike=2;},
        [](IBEvent& e){e.contract.multiplier="100";},[](IBEvent& e){e.contract.tradingClass="OTHER";}};
    for(const auto& change:mismatches) {
        Fixture f;f.PlaceContract(contract);auto e=f.Execution();change(e);
        assert(f.Push(e)==0);
        assert(f.adapter.GetAuthoritativeTerminalCorrelationSnapshot().executionOrderIds.empty());
    }
    Fixture f;f.Place();auto enriched=f.Execution();enriched.contract.localSymbol="EUR.USD";
    enriched.contract.primaryExchange="IDEALPRO";
    assert(f.Push(enriched)==1);
    assert(f.adapter.GetAuthoritativeTerminalCorrelationSnapshot().executionOrderIds.count(f.broker->orderId)==1);

    Fixture other;other.Place();auto wrongCorrelation=other.Execution();std::string reason;
    assert(IbVenueCorrelationCodec::EncodeOrderRef("hepta-v1-sha256:"+std::string(64,'b'),wrongCorrelation.order.orderRef,reason));
    assert(other.Push(wrongCorrelation)==0);
    assert(other.adapter.GetAuthoritativeTerminalCorrelationSnapshot().executionOrderIds.empty());
}
void TestForeignStatusCannotRetireOurPartialOrder() {
    const std::vector<std::function<void(IBEvent&)>> wrongIdentity={
        [](IBEvent& e){++e.brokerClientId;},[](IBEvent& e){e.brokerClientId=-1;},
        [](IBEvent& e){e.account="DU999";},[](IBEvent& e){e.number2=2;},
        [](IBEvent& e){e.number3=2;}};
    for(const auto& change:wrongIdentity) {
        Fixture f;const long id=f.Place();auto cancelled=f.Status("Cancelled",0);change(cancelled);
        assert(f.Push(cancelled)==0); // Not returned to downstream OMS/runtime either.
        assert(f.adapter.GetAuthoritativeCorrelationSnapshot().activeOrderIds.count(id)==1);
        f.Push(f.Execution(0.5));
        assert(f.adapter.GetAuthoritativeCorrelationSnapshot().activeOrderIds.count(id)==1);
        const auto terminal=f.adapter.GetAuthoritativeTerminalCorrelationSnapshot();
        assert(terminal.executionOrderIds.count(id)==1&&terminal.terminalOrderIdsByCorrelation.empty());
    }
}
void TestValidatedSyntheticFillWithoutNativeStatus() {
    Fixture f;const long id=f.Place();auto synthetic=f.Status();synthetic.value="execDetails";synthetic.requestId=-1;
    assert(f.Push(synthetic)==0); // Synthetic text before economic proof is untrusted.
    assert(f.adapter.GetAuthoritativeCorrelationSnapshot().activeOrderIds.count(id)==1);
    f.Push(f.Execution());
    auto wrongPrice=synthetic;wrongPrice.number=9;assert(f.Push(wrongPrice)==0);
    assert(f.Push(synthetic)==1);
    const auto proof=f.adapter.GetAuthoritativeTerminalCorrelationSnapshot();
    assert(proof.executionOrderIds.count(id)==1&&proof.terminalOrderIdsByCorrelation.empty());
    f.AbsorbFill(id);
    assert(f.adapter.GetAuthoritativeCorrelationSnapshot().activeOrderIds.empty());
    assert(!f.adapter.HasPendingLivePostFillRiskReconciliation());
}
void TestQuarantineInvalidatesRecoveryBarrier() {
    Fixture f;f.Place();f.Push(f.Status("Cancelled",0));
    assert(f.adapter.ReqRecoveryAuditRiskRefresh());f.FinishRiskRefresh();
    assert(f.adapter.GetAuthoritativeRecoveryAuditSnapshot().barrierComplete);
    auto foreign=f.Execution();++foreign.brokerClientId;
    assert(f.Push(foreign)==0);
    const auto quarantined=f.adapter.GetAuthoritativeRecoveryAuditSnapshot();
    assert(!quarantined.barrierComplete&&!quarantined.risk.complete);
    assert(quarantined.terminal.executionOrderIds.empty());
    assert(quarantined.terminal.terminalStatusesByCorrelation.begin()->second=="Cancelled");
}
void TestHostileEvidenceCannotPromoteFill() {
    const std::vector<std::function<void(IBEvent&)>> hostile={
        [](IBEvent& e){e.connectionEpoch=0;},[](IBEvent& e){++e.connectionEpoch;},
        [](IBEvent& e){e.brokerClientId=-1;},[](IBEvent& e){++e.brokerClientId;},
        [](IBEvent& e){e.account="DU999";},[](IBEvent& e){e.contract.symbol="GBP";},
        [](IBEvent& e){e.contract.secType="STK";},[](IBEvent& e){e.contract.currency="JPY";},
        [](IBEvent& e){e.order.orderRef="";},[](IBEvent& e){e.order.orderRef="H1bad";},
        [](IBEvent& e){e.value="SLD";},[](IBEvent& e){e.number2=2;},
        [](IBEvent& e){e.number2=0;},[](IBEvent& e){e.number=0;},
        [](IBEvent& e){e.key.clear();},[](IBEvent& e){e.id=0;},
        [](IBEvent& e){e.requestId=1;},[](IBEvent& e){e.requestId=-2;},[](IBEvent& e){e.requestId=-99;},
        [](IBEvent& e){e.contract.exchange="SMART";},
        [](IBEvent& e){e.number2=std::numeric_limits<double>::quiet_NaN();}};
    for(const auto& change:hostile) {
        Fixture f;f.Place();const auto initial=f.adapter.GetAuthoritativeTerminalCorrelationSnapshot();
        f.Push(f.Status());auto e=f.Execution();change(e);f.Push(e);
        const auto actual=f.adapter.GetAuthoritativeTerminalCorrelationSnapshot();
        assert(actual.generation==initial.generation&&actual.executionOrderIds.empty()&&actual.terminalOrderIdsByCorrelation.empty());
    }
    Fixture f;f.Place();auto status=f.Status("Cancelled",0);status.brokerClientId=-1;f.Push(status);
    assert(f.adapter.GetAuthoritativeTerminalCorrelationSnapshot().terminalOrderIdsByCorrelation.empty());
    f.Push(f.Execution());
    assert(f.adapter.GetAuthoritativeTerminalCorrelationSnapshot().terminalOrderIdsByCorrelation.empty());
}
void TestReconnectAndIncompleteBootstrap() {
    Fixture f;f.Place();const auto oldExecution=f.Execution(),oldStatus=f.Status();
    f.adapter.Disconnect();assert(!f.adapter.GetAuthoritativeTerminalCorrelationSnapshot().complete);
    assert(f.adapter.Connect());assert(!f.adapter.GetAuthoritativeTerminalCorrelationSnapshot().complete);
    assert(oldExecution.connectionEpoch!=f.adapter.GetConnectionEpoch());
    f.Bootstrap();const auto fresh=f.adapter.GetAuthoritativeTerminalCorrelationSnapshot();
    f.Push(oldExecution);f.Push(oldStatus);
    // Merely relabelling old events with the new epoch still cannot invent an
    // actual send binding in this new adapter incarnation.
    auto forgedExecution=oldExecution;forgedExecution.connectionEpoch=f.adapter.GetConnectionEpoch();
    auto forgedStatus=oldStatus;forgedStatus.connectionEpoch=f.adapter.GetConnectionEpoch();
    f.Push(forgedExecution);f.Push(forgedStatus);
    const auto actual=f.adapter.GetAuthoritativeTerminalCorrelationSnapshot();
    assert(actual.complete&&actual.generation==fresh.generation&&actual.executionOrderIds.empty());
    assert(actual.terminalOrderIdsByCorrelation.empty());

    Fixture pending(false);pending.Place();pending.Push(pending.Execution());pending.Push(pending.Status());
    auto incomplete=pending.adapter.GetAuthoritativeTerminalCorrelationSnapshot();
    assert(!incomplete.complete&&incomplete.executionOrderIds.empty());
    pending.FinishBootstrap();const auto completed=pending.adapter.GetAuthoritativeTerminalCorrelationSnapshot();
    assert(completed.complete&&completed.executionOrderIds.count(pending.broker->orderId)==1);
    assert(completed.terminalStatusesByCorrelation.begin()->second=="Filled");
}
}
int main() {
    TestWrapperQuantityTrackerCannotMixBrokerClients();
    TestSameEpochPostBootstrapLiveFillAndDeferredCancel();TestCancelledAndPartialCancelled();
    TestBoundContractExtensionsAndBrokerEnrichment();TestForeignStatusCannotRetireOurPartialOrder();
    TestValidatedSyntheticFillWithoutNativeStatus();
    TestQuarantineInvalidatesRecoveryBarrier();
    TestHostileEvidenceCannotPromoteFill();TestReconnectAndIncompleteBootstrap();
    std::puts("ib_live_terminal_reconciliation_tests PASS");
}
