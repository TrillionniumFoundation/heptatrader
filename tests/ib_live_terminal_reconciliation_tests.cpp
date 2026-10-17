#include "adapter_ib/ib_gateway_adapter.h"
#include "adapter_ib/ib_venue_correlation.h"
#include "execution/execution_coordinator.h"
#include "execution/ib_paper_account_broker_evidence.h"
#include "execution/ib_paper_startup_recovery.h"
#include <cassert>
#include <cmath>
#include <cstdio>
#include <fstream>
#include <deque>
#include <functional>
#include <limits>
#include <memory>
#include <new>
#include <cstdlib>
#include <stdexcept>
#include <unistd.h>

namespace { bool failNextCancelAllocation = false; }
void* operator new(std::size_t size) {
    if (failNextCancelAllocation) { failNextCancelAllocation = false; throw std::bad_alloc(); }
    if (void* result = std::malloc(size ? size : 1)) return result;
    throw std::bad_alloc();
}
void operator delete(void* ptr) noexcept { std::free(ptr); }
void operator delete(void* ptr, std::size_t) noexcept { std::free(ptr); }

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
    bool connected=false;std::uint64_t epoch=0;int executionsRequest=0,cancels=0;int connectBudgetMs=0;
    int cancelFault = 0, placeFault = 0, places = 0;
    int allOrderReads = 0, accountReads = 0, positionReads = 0;
    std::string submittedTif;
    IBOrderLite submitted;IBContractLite contract;long orderId=-1;
    std::deque<IBEvent> events;
    bool Connect(const IBConnectParams& p) override {connectBudgetMs=p.handshakeTimeoutMs;connected=true;return true;}
    void SetConnectionEpoch(std::uint64_t value) override {epoch=value;}
    void Disconnect() override {connected=false;}
    bool IsConnected() const override {return connected;}
    const char* GetStatusString() const override {return "fixture";}
    bool ReqAccountSummary() override {++accountReads;return true;}
    bool ReqPositions() override {++positionReads;return true;}
    bool ReqAllOpenOrders() override {++allOrderReads;return true;}
    bool ReqCompletedOrders() override {return true;}
    bool ReqExecutions(int id) override {executionsRequest=id;return true;}
    bool ReqMktData(int,const IBContractLite&) override {return true;}
    bool CancelMktData(int) override {return true;}
    bool PlaceOrder(long id,const IBContractLite& c,const IBOrderLite& o) override {
        return PlaceOrderWithTimeInForce(id,c,o,"DAY");
    }
    bool PlaceOrderWithTimeInForce(long id,const IBContractLite& c,
        const IBOrderLite& o,const std::string& tif) override {
        ++places; orderId=id;contract=c;submitted=o;submittedTif=tif;
        if (placeFault == 1) throw std::runtime_error("after flatten send");
        if (placeFault == 2) return false;
        if (placeFault == 3) failNextCancelAllocation = true;
        return true;
    }
    bool CancelOrder(long) override {
        ++cancels;
        if (cancelFault == 1) throw std::runtime_error("after cancel send");
        if (cancelFault == 2) return false;
        if (cancelFault == 3) failNextCancelAllocation = true;
        return true;
    }
    bool PollOnce(int) override {return true;}
    bool TryDequeueEvent(IBEvent& event) override {
        if(events.empty()) return false;
        event=events.front();events.pop_front();return true;
    }
    long GetLastValidOrderId() const override {return 41;}
};
struct Fixture {
    Broker* broker;HeptaIBGatewayAdapter adapter;
    Fixture(bool finishBootstrap=true, bool cryptoScope=false, bool cashScope=false,
            const std::string& observabilityPath=std::string()):broker(new Broker()),adapter(
        std::unique_ptr<IIBApiWrapper>(broker), [this]() {
            broker=new Broker();return std::unique_ptr<IIBApiWrapper>(broker);
        }) {
        HeptaIBConfig cfg;cfg.account="DU123";cfg.clientId=73;cfg.readOnly=false;
        cfg.observabilityLogPath=observabilityPath;
        cfg.risk.enableOrderSubmission=true;cfg.risk.maxDailyOrders=100;
        cfg.risk.duplicateOrderWindowSec=0;cfg.risk.maxPriceDeviationBps=0;
        if(cryptoScope) {
            IBContractLite c;c.symbol="BTC";c.secType="CRYPTO";c.exchange="PAXOS";c.currency="USD";
            cfg.authoritativeCryptoContracts["BTC.USD"]=c;
        }
        if(cashScope) {
            InstrumentRef c;c.symbol="EUR";c.secType="CASH";c.exchange="IDEALPRO";c.currency="USD";
            cfg.authoritativeCashFxContracts["EUR.USD"]=c;
            cfg.authoritativeCashFxBaselines["EUR.USD"]=-100000;
            cfg.authoritativeCashFxStartupObservedBalances["EUR.USD"]=-75000;
        }
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
void TestInvalidCashMonitorRepetitionCannotAbortIndependentPositionsRefresh() {
    // Regression: an already invalid cash generation has no authoritative
    // cash map. Repeating a numeric subscription value used to reset the
    // positions-refresh pending flag and discard its real PositionEnd.
    Fixture f(true,false,true);
    auto account=[&](const std::string& cash) {
        auto value=f.Event(IBEventType::AccountValue);
        value.key="NetLiquidation:USD";value.value="1000";f.Push(value);
        value.key="AccountReady:";value.value="true";f.Push(value);
        value.key="CashBalance:EUR";value.value=cash;f.Push(value);
    };
    assert(f.adapter.ReqRecoveryAuditRiskRefresh());
    account("-75000");
    f.Push(f.Event(IBEventType::AccountSummaryEnd));
    f.Push(f.Event(IBEventType::PositionEnd));
    const auto initial=f.adapter.GetAuthoritativeRecoveryAuditSnapshot();
    assert(initial.barrierComplete && initial.risk.coherentRefreshComplete);

    auto monitor=f.Event(IBEventType::AccountValue);
    monitor.key="CashBalance:EUR";monitor.value="-75001";
    f.Push(monitor);
    const auto stale=f.adapter.GetAuthoritativeRecoveryAuditSnapshot();
    assert(!stale.barrierComplete && !stale.risk.accountComplete &&
           !stale.risk.fxCashComplete && !stale.risk.positionsComplete);
    assert(stale.risk.reasonCode=="IB_FX_CASH_BALANCE_OUT_OF_GENERATION_CHANGE");

    // A new positions read MUST still be allowed to reach its real end
    // boundary even if stale monitor values continue being delivered.
    assert(f.adapter.ReqPositions());
    const auto partial=f.adapter.GetAuthoritativeRiskSnapshot();
    assert(!partial.positionsComplete);
    monitor.value="-75002";f.Push(monitor);
    monitor.value="-75000";f.Push(monitor);
    f.Push(f.Event(IBEventType::PositionEnd));
    const auto positionOnly=f.adapter.GetAuthoritativeRecoveryAuditSnapshot();
    assert(positionOnly.risk.positionsComplete);
    assert(!positionOnly.risk.accountComplete &&
           !positionOnly.risk.fxCashComplete &&
           !positionOnly.barrierComplete);
    assert(positionOnly.risk.positionsGeneration>initial.risk.positionsGeneration);

    // A monitor reversion or position end cannot grant order authority.
    IBContractLite c;c.symbol="EUR";c.secType="CASH";
    c.exchange="IDEALPRO";c.currency="USD";
    IBOrderLite o;o.action="BUY";o.orderType="LMT";
    o.lmtPrice=1.18;o.totalQuantity=1;
    long id=-1;
    assert(!f.adapter.PlaceOrderCorrelated(c,o,
        "hepta-v1-sha256:"+std::string(64,'e'),&id));
    assert(f.broker->places==0 && f.broker->cancels==0);

    // Only a new two-leg account+positions request/end with the correctly
    // attested current value may establish internal adapter completeness.
    assert(f.adapter.ReqRecoveryAuditRiskRefresh());
    account("-75000");
    f.Push(f.Event(IBEventType::AccountSummaryEnd));
    f.Push(f.Event(IBEventType::PositionEnd));
    const auto renewed=f.adapter.GetAuthoritativeRecoveryAuditSnapshot();
    assert(renewed.barrierComplete && renewed.risk.coherentRefreshComplete);
    assert(renewed.risk.accountGeneration>initial.risk.accountGeneration);
    assert(renewed.risk.positionsGeneration>positionOnly.risk.positionsGeneration);
    assert(f.broker->places==0 && f.broker->cancels==0);
}
void TestCashMonitorRedactedGenerationObservation() {
    // Provenance-only log: no callback cash values, and no mutation or orders.
    const char* env=std::getenv("TMPDIR");
    const std::string root=env && *env ? env : "/tmp";
    const std::string pattern=root+"/hepta-cash-observation-XXXXXX";
    std::vector<char> temp(pattern.begin(),pattern.end());
    temp.push_back('\0');
    const int fd=mkstemp(temp.data());
    assert(fd>=0);close(fd);
    {
        Fixture f(true,false,true,temp.data());
        assert(f.adapter.ReqRecoveryAuditRiskRefresh());
        auto value=f.Event(IBEventType::AccountValue);
        value.key="NetLiquidation:USD";value.value="1000";f.Push(value);
        value.key="AccountReady:";value.value="true";f.Push(value);
        value.key="CashBalance:EUR";value.value="-75000";f.Push(value);
        f.Push(f.Event(IBEventType::AccountSummaryEnd));
        f.Push(f.Event(IBEventType::PositionEnd));
        assert(f.adapter.GetAuthoritativeRecoveryAuditSnapshot().barrierComplete);
        value.id=98501;value.value="-75001";f.Push(value);
        assert(!f.adapter.GetAuthoritativeRecoveryAuditSnapshot().barrierComplete);
        // Duplicate monitor updates are still rejected, but cannot generate
        // a new invalidation receipt without a fresh coherent risk generation.
        f.Push(value);f.Push(value);
        assert(!f.adapter.GetAuthoritativeRecoveryAuditSnapshot().barrierComplete);
        assert(f.broker->places==0 && f.broker->cancels==0);
    }
    std::ifstream input(temp.data());
    assert(input.good());
    std::string line;
    int found=0;
    while(std::getline(input,line)) {
        if (line.find("\"event\":\"risk.fx_cash_monitor_invalidated\"") ==
            std::string::npos) continue;
        ++found;
        assert(line.find("\"subscription_request_id\":98501")!=std::string::npos);
        assert(line.find("\"previous_cash_checkpoint_present\":true")!=std::string::npos);
        assert(line.find("\"account_complete_before\":true")!=std::string::npos);
        assert(line.find("-75001")==std::string::npos);
        assert(line.find("-75000")==std::string::npos);
    }
    assert(found==1);
    std::remove(temp.data());
}
void TestCashMonitorChangeRequiresFreshCoherentBrokerGeneration() {
    // Synthetic callbacks only. No real broker, place or cancel request.
    Fixture f(true,false,true);
    auto pushAccountAndPositions=[&](const std::string& cash) {
        auto value=f.Event(IBEventType::AccountValue);
        value.key="NetLiquidation:USD";value.value="1000";f.Push(value);
        value.key="AccountReady:";value.value="true";f.Push(value);
        value.key="CashBalance:EUR";value.value=cash;f.Push(value);
        f.Push(f.Event(IBEventType::AccountSummaryEnd));
        f.Push(f.Event(IBEventType::PositionEnd));
    };
    assert(f.adapter.ReqRecoveryAuditRiskRefresh());
    pushAccountAndPositions("-75000");
    const auto first=f.adapter.GetAuthoritativeRecoveryAuditSnapshot();
    assert(first.barrierComplete && first.risk.coherentRefreshComplete);
    auto monitor=f.Event(IBEventType::AccountValue);
    monitor.key="CashBalance:EUR";monitor.value="-75000";
    f.Push(monitor);
    assert(f.adapter.GetAuthoritativeRecoveryAuditSnapshot().barrierComplete);
    monitor.value="-75001";f.Push(monitor);
    const auto invalidated=f.adapter.GetAuthoritativeRecoveryAuditSnapshot();
    assert(!invalidated.barrierComplete);
    assert(!invalidated.risk.complete);
    assert(invalidated.reasonCode=="IB_FX_CASH_BALANCE_OUT_OF_GENERATION_CHANGE");
    // A standalone broker monitor mismatch must refuse an actual risk-increase
    // attempt in the local fake broker, independently of telemetry's fixed
    // authorization_effect=NONE field. No original permission is invented.
    IBContractLite blockedContract;blockedContract.symbol="EUR";
    blockedContract.secType="CASH";blockedContract.exchange="IDEALPRO";
    blockedContract.currency="USD";
    IBOrderLite blockedOrder;blockedOrder.action="BUY";
    blockedOrder.orderType="LMT";blockedOrder.lmtPrice=1.18;
    blockedOrder.totalQuantity=1;
    long blockedId=-1;
    assert(!f.adapter.PlaceOrderCorrelated(blockedContract,blockedOrder,
        "hepta-v1-sha256:"+std::string(64,'d'),&blockedId));
    assert(f.broker->places==0);
    monitor.value="-75000";f.Push(monitor);
    assert(!f.adapter.GetAuthoritativeRecoveryAuditSnapshot().barrierComplete);
    // A genuinely new matched request/end boundary is required. An old
    // subscription push can never reauthorize an unchanged risk snapshot.
    assert(f.adapter.ReqRecoveryAuditRiskRefresh());
    pushAccountAndPositions("-75000");
    const auto renewed=f.adapter.GetAuthoritativeRecoveryAuditSnapshot();
    assert(renewed.barrierComplete && renewed.risk.complete);
    assert(renewed.risk.accountGeneration>first.risk.accountGeneration);
    assert(renewed.risk.positionsGeneration>first.risk.positionsGeneration);
    assert(f.broker->places==0 && f.broker->cancels==0);
}

void TestDuplicateRejectedCashObservationDoesNotErasePhysicalCrypto() {
    // All observations below are synthetic. No broker connection or sends.
    for(int invalidation=0;invalidation<6;++invalidation) {
        Fixture f(true,true,true);
        assert(f.adapter.ReqRiskRefresh());
        auto value=f.Event(IBEventType::AccountValue);
        value.key="NetLiquidation:USD";value.value="1000";f.Push(value);
        value.key="AccountReady:";value.value="true";f.Push(value);
        value.key="CashBalance:EUR";value.value="-75100";f.Push(value);
        f.Push(f.Event(IBEventType::AccountSummaryEnd));
        auto position=f.Event(IBEventType::Position);position.key="BTC.USD";position.number=.02;
        position.contract.symbol="BTC";position.contract.secType="CRYPTO";
        position.contract.exchange="PAXOS";position.contract.currency="USD";f.Push(position);
        f.Push(f.Event(IBEventType::PositionEnd));
        const auto before=f.adapter.GetPhysicalCryptoPositionObservation();
        assert(before.complete && before.positions.at("BTC.USD")==.02);
        for(int repeat=0;repeat<5;++repeat) {
            f.Push(value);
            const auto risk=f.adapter.GetAuthoritativeRiskSnapshot();
            assert(!risk.complete && !risk.accountComplete && !risk.fxCashComplete);
            assert(risk.reasonCode=="IB_FX_CASH_ATTESTED_BALANCE_MISMATCH");
            const auto observed=f.adapter.GetPhysicalCryptoPositionObservation();
            assert(observed.complete && observed.positions==before.positions);
            assert(observed.positionGeneration==before.positionGeneration);
            assert(!f.adapter.GetAuthoritativePositionQuantities().count("EUR.USD"));
        }
        // Only identical pushes can be no-ops. A real change, return to the
        // old checkpoint, malformed value, loss of readiness, disconnect or
        // a new request/end generation invalidates the observation.
        auto changed=value;
        if(invalidation==0)changed.value="-75101";
        else if(invalidation==1)changed.value="-75000";
        else if(invalidation==2)changed.value="nan";
        else if(invalidation==3){changed.key="AccountReady:";changed.value="false";}
        if(invalidation==4)f.adapter.Disconnect();
        else if(invalidation==5)assert(f.adapter.ReqAccountSummary());
        else f.Push(changed);
        assert(!f.adapter.GetPhysicalCryptoPositionObservation().complete);
        f.Push(value); // A delayed old duplicate cannot resurrect authority.
        assert(!f.adapter.GetPhysicalCryptoPositionObservation().complete);
        assert(!f.adapter.GetAuthoritativeRiskSnapshot().complete);
        assert(f.broker->places==0 && f.broker->cancels==0);
    }
}

void TestCashOutsideGenerationCannotBorrowOrdinaryRiskFreshness() {
    // Reproduce the live 2026-10-09 cash epoch mismatch with a socket-free
    // synthetic Broker; this is not a live recovery, order or login.
    Fixture f(true,false,true);
    const auto finishRisk = [&](const char* cash) {
        auto value=f.Event(IBEventType::AccountValue);
        value.key="NetLiquidation:USD";value.value="1000";f.Push(value);
        value.key="AccountReady:";value.value="true";f.Push(value);
        value.key="CashBalance:EUR";value.value=cash;f.Push(value);
        f.Push(f.Event(IBEventType::AccountSummaryEnd));
        f.Push(f.Event(IBEventType::PositionEnd));
    };
    assert(f.adapter.ReqRecoveryAuditRiskRefresh());
    finishRisk("-75000");
    const auto initial=f.adapter.GetAuthoritativeRecoveryAuditSnapshot();
    assert(initial.barrierComplete && initial.risk.coherentRefreshComplete);
    // Outside any account request/end cohort. This negative observation
    // invalidates the broker audit even if prices and terminal data are fresh.
    auto changed=f.Event(IBEventType::AccountValue);
    changed.key="CashBalance:EUR";changed.value="-75001";
    f.Push(changed);
    auto invalid=f.adapter.GetAuthoritativeRecoveryAuditSnapshot();
    assert(!invalid.barrierComplete && !invalid.risk.complete);
    assert(invalid.reasonCode=="IB_FX_CASH_BALANCE_OUT_OF_GENERATION_CHANGE");
    // Ordinary coherent account+positions refresh is never a recovery audit.
    assert(f.adapter.ReqRiskRefresh());
    finishRisk("-75001");
    const auto ordinary=f.adapter.GetAuthoritativeRecoveryAuditSnapshot();
    assert(ordinary.risk.complete && ordinary.risk.coherentRefreshComplete);
    assert(ordinary.risk.fxCashComplete);
    assert(!ordinary.barrierComplete);
    assert(ordinary.reasonCode=="IB_FX_CASH_BALANCE_OUT_OF_GENERATION_CHANGE");
    // A real owner audit must require the *next* API connection epoch. A
    // direct same-epoch risk refresh cannot forge that required boundary.
    const auto request=f.adapter.BeginRecoveryAuditBarrier();
    assert(request.newConnectionEpochRequired && !request.barrierComplete);
    assert(request.reasonCode=="IB_RECOVERY_AUDIT_NEW_CONNECTION_EPOCH_REQUIRED");
    assert(!f.adapter.ReqRecoveryAuditRiskRefresh());
    assert(f.adapter.GetLastRejectReason() ==
           "IB_RECOVERY_AUDIT_NEW_CONNECTION_EPOCH_REQUIRED");
    const auto sameEpoch=f.adapter.GetAuthoritativeRecoveryAuditSnapshot();
    assert(sameEpoch.risk.coherentRefreshComplete);
    assert(!sameEpoch.barrierComplete);
    const auto stillNeeded=f.adapter.BeginRecoveryAuditBarrier();
    assert(stillNeeded.newConnectionEpochRequired);
    assert(f.broker->places==0 && f.broker->cancels==0);
    // Only a genuine next transport epoch and new correlation, terminal,
    // account and position End can establish a fresh recovery audit.
    f.adapter.Disconnect();
    assert(f.adapter.Connect());
    f.Bootstrap();
    assert(f.adapter.ReqRecoveryAuditRiskRefresh());
    finishRisk("-75000");
    const auto recovered=f.adapter.GetAuthoritativeRecoveryAuditSnapshot();
    assert(recovered.barrierComplete);
    assert(recovered.risk.coherentRefreshComplete);
    assert(recovered.risk.fxCashComplete);
    assert(recovered.active.connectionEpoch == recovered.risk.connectionEpoch);
    assert(f.broker->places==0 && f.broker->cancels==0);
}

void TestCryptoCashBaselineUsesSharedResidentAndCanaryEnvelope() {
    Fixture f(true,true);assert(f.adapter.ReqRiskRefresh());f.FinishRiskRefresh();
    IBContractLite c;c.symbol="BTC";c.secType="CRYPTO";c.exchange="PAXOS";c.currency="USD";
    IBFinalOrderSendContext ctx;ctx.instrument="BTC.USD";ctx.timeInForce="IOC";
    ctx.authoritativeQuoteBound=true;ctx.quoteBid=84999.75;ctx.quoteAsk=85000;
    auto place=[&](double cash,char suffix) {
        IBOrderLite o;o.action="BUY";o.orderType="MKT";o.cashQuantity=cash;
        const std::string corr="hepta-v1-sha256:"+std::string(63,'a')+suffix;
        return f.adapter.PlaceOrderWithResult(c,o,corr,&ctx);
    };
    auto tiny=place(42.50,'b');
    assert(tiny.disposition==VenuePlaceDisposition::Submitted);
    assert(f.broker->submitted.cashQuantity==42.50 && f.broker->submitted.totalQuantity==0.0);
    auto cancelled=f.Status("Cancelled",0);cancelled.id=tiny.orderId;cancelled.number3=0;f.Push(cancelled);
    for (const double cash : {42.51,150.01,151.0,24999.99}) {
        const int places=f.broker->places;
        auto rejected=place(cash,'c');
        assert(rejected.disposition==VenuePlaceDisposition::Rejected);
        assert(f.adapter.GetLastRejectReason()=="IB_CRYPTO_EXPOSURE_LIMIT_EXCEEDED");
        assert(f.broker->places==places);
    }
    ctx.quoteAsk=400000.0;
    auto cap=place(150.0,'d');
    assert(cap.disposition==VenuePlaceDisposition::Submitted);
    assert(f.broker->submitted.cashQuantity==150.0);
}

void TestCashMarketTerminalNeedsActualExecutionAndNativeFinalStatus() {
    for(int mode=0;mode<5;++mode) {
        Fixture f(true,true);assert(f.adapter.ReqRiskRefresh());f.FinishRiskRefresh();
        IBContractLite c;c.symbol="BTC";c.secType="CRYPTO";c.exchange="PAXOS";c.currency="USD";
        IBOrderLite order;order.action="BUY";order.orderType="MKT";order.cashQuantity=25000;
        IBFinalOrderSendContext ctx;ctx.instrument="BTC.USD";ctx.timeInForce="IOC";
        ctx.authoritativeQuoteBound=true;ctx.quoteBid=84999.75;ctx.quoteAsk=85000;
        const std::string corr="hepta-v1-sha256:"+std::string(64,'a');
        const auto placed=f.adapter.PlaceOrderWithResult(c,order,corr,&ctx);
        assert(placed.disposition==VenuePlaceDisposition::Submitted);
        assert(f.broker->submitted.cashQuantity==25000 && f.broker->submitted.totalQuantity==0);
        assert(f.broker->submitted.orderType=="MKT" && f.broker->submittedTif=="IOC");
        const double quantity=mode==2?0.00101:mode==3?0.0005:0.00099;
        auto fill=f.Execution(quantity);fill.number=84000;fill.number3=0;fill.remainingQuantityKnown=false;
        auto final=f.Status(mode==3?"Cancelled":"Filled",quantity);final.number=84000;final.number3=0;
        if(mode==4) {
            final=f.Status("Cancelled",0);final.number3=0;f.Push(final);
            auto snap=f.adapter.GetAuthoritativeTerminalCorrelationSnapshot();
            assert(snap.terminalStatusesByCorrelation.at(corr)=="Cancelled" && snap.executionCorrelations.empty());
            assert(!snap.HasEconomicTerminal(corr,f.broker->orderId,f.broker->epoch));
            continue;
        }
        if(mode==1) {
            f.Push(final);
            assert(f.adapter.GetAuthoritativeTerminalCorrelationSnapshot().terminalOrderIdsByCorrelation.empty());
        }
        auto wrong=fill;wrong.brokerClientId++;
        f.Push(wrong);
        assert(f.adapter.GetAuthoritativeTerminalCorrelationSnapshot().executionCorrelations.empty());
        f.Push(fill);
        const auto afterFill=f.adapter.GetAuthoritativeTerminalCorrelationSnapshot();
        assert(afterFill.executionCorrelations.count(corr)==1);
        if(mode!=1) {
            assert(afterFill.terminalOrderIdsByCorrelation.empty());
            assert(!afterFill.HasEconomicTerminal(corr,f.broker->orderId,f.broker->epoch));
            auto synthetic=final;synthetic.value="execDetails";synthetic.requestId=-1;
            f.Push(synthetic);
            assert(f.adapter.GetAuthoritativeTerminalCorrelationSnapshot().terminalOrderIdsByCorrelation.empty());
            f.Push(final);
        }
        auto terminal=f.adapter.GetAuthoritativeTerminalCorrelationSnapshot();
        assert(terminal.terminalStatusesByCorrelation.at(corr)==(mode==3?"Cancelled":"Filled"));
        assert(terminal.HasEconomicTerminal(corr,f.broker->orderId,f.broker->epoch));
        assert(!terminal.HasEconomicTerminal(corr,f.broker->orderId,f.broker->epoch+1));
        assert(!terminal.HasEconomicTerminal(corr,f.broker->orderId+1,f.broker->epoch));
        assert(!terminal.HasEconomicTerminal("hepta-v1-sha256:"+std::string(64,'b'),f.broker->orderId,f.broker->epoch));
        auto incomplete=terminal;incomplete.complete=false;
        assert(!incomplete.HasEconomicTerminal(corr,f.broker->orderId,f.broker->epoch));
        const auto generation=terminal.generation;f.Push(fill);f.Push(final);
        assert(f.adapter.GetAuthoritativeTerminalCorrelationSnapshot().generation==generation);
        assert(f.broker->places==1 && f.broker->cancels==0);
        IBSubmittedOrderQuantityTracker quantities;quantities.Reset(f.broker->epoch,73,"DU123");
        quantities.Record(f.broker->orderId,c,f.broker->submitted);
        assert(quantities.ObserveExecution(fill)==0); // No estimated full-fill synthesis.
    }
}

void TestForeignClientOpenOrdersNeverBecomeOurCancelOrTerminalAuthority() {
    Fixture f;
    const long colliding=146;
    assert(f.adapter.ReqAuthoritativeOpenOrders());
    auto foreign=f.Event(IBEventType::OpenOrder);
    foreign.id=colliding;foreign.brokerClientId=999;
    foreign.value="Submitted";
    foreign.order.action="BUY";foreign.order.orderType="LMT";
    foreign.order.totalQuantity=0.001;
    foreign.order.orderRef="H1not-our-order-reference";
    f.Push(foreign);f.Push(f.Event(IBEventType::OpenOrderEnd));
    const auto before=f.adapter.GetAuthoritativeCorrelationSnapshot();
    assert(before.complete);
    assert(before.activeOrderIds.count(colliding)==1);
    assert(before.activeOrderClientIdsByOrderId.at(colliding)==999);
    assert(before.activeOrderIdsByCorrelation.empty());
    // The shared-account Broker's foreign active order is not a cancelable
    // order of our service, even though the numeric order id is plausible.
    assert(f.adapter.CancelOrder(colliding).detail=="NO_BROKER_SUBMIT");
    assert(f.broker->cancels==0);
    auto terminal=f.Status("Cancelled",0);
    terminal.id=colliding;
    f.Push(terminal);
    const auto after=f.adapter.GetAuthoritativeCorrelationSnapshot();
    assert(after.complete && after.activeOrderIds.count(colliding)==1);
    assert(after.activeOrderClientIdsByOrderId.at(colliding)==999);
    // The true Broker-global refresh may later prove this foreign order
    // absent; only that new completed snapshot removes it.
    assert(f.adapter.ReqAuthoritativeOpenOrders());
    f.Push(f.Event(IBEventType::OpenOrderEnd));
    const auto empty=f.adapter.GetAuthoritativeCorrelationSnapshot();
    assert(empty.complete && empty.activeOrderIds.empty());
    assert(empty.activeOrderClientIdsByOrderId.empty());
}

void TestForeignAndOwnClientOrderIdCollisionFailsGlobalSnapshotClosed() {
    Fixture f;
    assert(f.adapter.ReqAuthoritativeOpenOrders());
    auto own=f.Event(IBEventType::OpenOrder);
    own.id=281;own.brokerClientId=73;own.value="Submitted";
    auto foreign=own;foreign.brokerClientId=999;
    f.Push(own);f.Push(foreign);f.Push(f.Event(IBEventType::OpenOrderEnd));
    const auto collide=f.adapter.GetAuthoritativeCorrelationSnapshot();
    assert(!collide.complete);
    assert(collide.reasonCode=="IB_ACTIVE_ORDER_CLIENT_ID_COLLISION");
    assert(collide.activeOrderIds.empty());
    assert(collide.activeOrderClientIdsByOrderId.empty());

    Fixture reverse;
    assert(reverse.adapter.ReqAuthoritativeOpenOrders());
    auto first=reverse.Event(IBEventType::OpenOrder);
    first.id=282;first.brokerClientId=999;first.value="Submitted";
    auto second=first;second.brokerClientId=73;
    reverse.Push(first);reverse.Push(second);
    reverse.Push(reverse.Event(IBEventType::OpenOrderEnd));
    assert(!reverse.adapter.GetAuthoritativeCorrelationSnapshot().complete);
    assert(reverse.adapter.GetAuthoritativeCorrelationSnapshot().reasonCode==
           "IB_ACTIVE_ORDER_CLIENT_ID_COLLISION");

    // An unknown SDK client on reqAllOpenOrders is NEVER qualified as an
    // empty or complete account-global order universe.
    Fixture unknown;
    assert(unknown.adapter.ReqAuthoritativeOpenOrders());
    auto untrusted=unknown.Event(IBEventType::OpenOrder);
    untrusted.id=283;untrusted.brokerClientId=-1;untrusted.value="Submitted";
    unknown.Push(untrusted);unknown.Push(unknown.Event(IBEventType::OpenOrderEnd));
    const auto missing=unknown.adapter.GetAuthoritativeCorrelationSnapshot();
    assert(!missing.complete && missing.reasonCode=="IB_ACTIVE_ORDER_CLIENT_ID_UNKNOWN");

    // Collision also invalidates a previously complete snapshot when
    // account-global Order callbacks arrive asynchronously after End.
    Fixture late;
    auto external=late.Event(IBEventType::OpenOrder);
    external.id=365;external.brokerClientId=999;external.value="Submitted";
    late.Push(external);
    const auto before=late.adapter.GetAuthoritativeCorrelationSnapshot();
    assert(before.complete && before.activeOrderClientIdsByOrderId.at(365)==999);
    auto local=external;local.brokerClientId=73;
    late.Push(local);
    const auto after=late.adapter.GetAuthoritativeCorrelationSnapshot();
    assert(!after.complete && after.reasonCode=="IB_ACTIVE_ORDER_CLIENT_ID_COLLISION");
    assert(after.activeOrderIds.empty());
}

void TestRecoveredOpenOrderRestoresOriginalTerminalBindingWithoutSend() {
    for (const bool filled : {false,true}) {
        Fixture f;
        const std::string corr="hepta-v1-sha256:"+std::string(64,'d');
        f.broker->orderId=146;
        auto open=f.Event(IBEventType::OpenOrder);
        open.contract.symbol="BTC";open.contract.secType="CRYPTO";
        open.contract.currency="USD";open.contract.exchange="PAXOS";
        open.order.action="BUY";open.order.orderType="LMT";
        open.order.totalQuantity=0.001;open.order.lmtPrice=84569.75;
        std::string reason;assert(IbVenueCorrelationCodec::EncodeOrderRef(corr,open.order.orderRef,reason));
        open.value="PendingCancel";open.number=0.001;
        f.Push(open);
        assert(f.adapter.GetAuthoritativeCorrelationSnapshot().activeOrderIds.count(146)==1);
        assert(f.adapter.CancelOrder(146).detail=="CANCEL_ALREADY_PENDING");
        if(filled) {
            auto execution=f.Event(IBEventType::ExecutionDetails);execution.requestId=-1;
            execution.contract=open.contract;execution.order.orderRef=open.order.orderRef;
            execution.key="original-execution-146";execution.value="BOT";
            execution.number=84500;execution.number2=0.001;execution.number3=0;
            f.Push(execution);
        }
        auto terminal=f.Status(filled?"Filled":"Cancelled",filled?0.001:0);
        terminal.number=filled?84500:0;terminal.number3=0;
        f.Push(terminal);
        const auto snapshot=f.adapter.GetAuthoritativeTerminalCorrelationSnapshot();
        assert(snapshot.terminalOrderIdsByCorrelation.at(corr)==146);
        assert(snapshot.terminalStatusesByCorrelation.at(corr)==(filled?"Filled":"Cancelled"));
        assert(snapshot.executionCorrelations.count(corr)==(filled?1:0));
        assert(f.broker->places==0 && f.broker->cancels==0);
    }
    Fixture foreign;foreign.broker->orderId=146;
    auto open=foreign.Event(IBEventType::OpenOrder);open.brokerClientId=999;
    open.contract.symbol="BTC";open.contract.secType="CRYPTO";open.contract.currency="USD";open.contract.exchange="PAXOS";
    open.order.action="BUY";open.order.totalQuantity=0.001;open.value="PendingCancel";
    std::string reason;assert(IbVenueCorrelationCodec::EncodeOrderRef("hepta-v1-sha256:"+std::string(64,'e'),open.order.orderRef,reason));
    foreign.Push(open);auto terminal=foreign.Status("Cancelled",0);terminal.number3=0;foreign.Push(terminal);
    assert(foreign.adapter.GetAuthoritativeTerminalCorrelationSnapshot().terminalOrderIdsByCorrelation.empty());
    assert(foreign.broker->places==0 && foreign.broker->cancels==0);
}
void TestIncompleteHistoryReadOnlyAdmissionDoesNotCreateTerminalProof() {
    IBAuthoritativeCorrelationSnapshot active;active.complete=true;active.connectionEpoch=7;
    active.generation=11;active.activeOrderIds.insert(146);
    active.activeOrderIdsByCorrelation["hepta-v1-sha256:"+std::string(64,'a')]=146;
    IBAuthoritativeTerminalCorrelationSnapshot terminal;
    terminal.connectionEpoch=7;terminal.generation=1;
    terminal.reasonCode="IB_TERMINAL_CORRELATION_REFRESH_PENDING";
    auto allowed=[&](bool scope,bool connected,bool stream,std::uint64_t epoch) {
        return CanKeepPendingOrderReadOnly(scope,connected,stream,epoch,active,terminal);
    };
    assert(allowed(true,true,true,7));
    assert(!terminal.complete && terminal.terminalOrderIdsByCorrelation.empty());
    assert(!allowed(false,true,true,7));assert(!allowed(true,false,true,7));
    assert(!allowed(true,true,false,7));assert(!allowed(true,true,true,0));
    assert(!allowed(true,true,true,8));
    active.complete=false;assert(!allowed(true,true,true,7));active.complete=true;
    active.generation=0;assert(!allowed(true,true,true,7));active.generation=11;
    terminal.complete=true;assert(!allowed(true,true,true,7));terminal.complete=false;
    terminal.reasonCode="IB_TERMINAL_CORRELATION_CONFLICT";
    assert(!allowed(true,true,true,7));terminal.reasonCode="IB_TERMINAL_CORRELATION_REFRESH_PENDING";
    terminal.generation=0;assert(!allowed(true,true,true,7));terminal.generation=1;
    terminal.connectionEpoch=6;assert(!allowed(true,true,true,7));terminal.connectionEpoch=7;
    active.activeOrderIds.insert(147);assert(!allowed(true,true,true,7));
    active.activeOrderIdsByCorrelation["other"]=146;assert(!allowed(true,true,true,7));
    active.activeOrderIdsByCorrelation["other"]=147;assert(allowed(true,true,true,7));
    active.activeOrderIds.clear();assert(!allowed(true,true,true,7));
}
void TestExactOrderSizeParse320TerminatesOnlyCurrentBoundCashOrder() {
    const auto placeCash=[](Fixture& f,std::string correlation) {
        assert(f.adapter.ReqRiskRefresh());f.FinishRiskRefresh();
        IBContractLite c;c.symbol="BTC";c.secType="CRYPTO";c.exchange="PAXOS";c.currency="USD";
        IBOrderLite o;o.action="BUY";o.orderType="MKT";o.cashQuantity=25000.0;
        IBFinalOrderSendContext ctx;ctx.instrument="BTC.USD";ctx.timeInForce="IOC";
        ctx.authoritativeQuoteBound=true;ctx.quoteBid=85000;ctx.quoteAsk=85001;
        const auto placed=f.adapter.PlaceOrderWithResult(c,o,correlation,&ctx);
        assert(placed.disposition==VenuePlaceDisposition::Submitted);
        return placed.orderId;
    };
    const std::string corr="hepta-v1-sha256:"+std::string(64,'f');
    {
        Fixture f(true,true);const long id=placeCash(f,corr);
        auto error=f.Event(IBEventType::Error);error.id=id;error.key="320";
        error.brokerClientId=-1;error.account.clear();
        error.value="localized prefix: Unable to parse field: 'Order Size' for input string: '-SNaN'";
        f.Push(error);
        const auto active=f.adapter.GetAuthoritativeCorrelationSnapshot();
        const auto terminal=f.adapter.GetAuthoritativeTerminalCorrelationSnapshot();
        assert(active.complete&&active.activeOrderIds.count(id)==0);
        assert(terminal.complete&&terminal.terminalOrderIdsByCorrelation.at(corr)==id);
        assert(terminal.terminalStatusesByCorrelation.at(corr)=="Rejected");
        assert(terminal.executionCorrelations.count(corr)==0);
        const auto generation=terminal.generation;
        f.Push(error);
        assert(f.adapter.GetAuthoritativeTerminalCorrelationSnapshot().generation==generation);
        assert(f.broker->places==1&&f.broker->cancels==0);
    }
    for(int mode=0;mode<4;++mode) {
        Fixture f(true,true);const long id=placeCash(f,corr);
        auto error=f.Event(IBEventType::Error);error.id=id;error.key="320";
        error.brokerClientId=-1;error.account.clear();
        error.value="Unable to parse field: 'Order Size' for input string: '-SNaN'";
        if(mode==0)error.key="321";
        if(mode==1)error.value="Unable to parse field: 'Limit Price' for input string: '-SNaN'";
        if(mode==2)++error.id;
        if(mode==3)++error.connectionEpoch;
        f.Push(error);
        const auto terminal=f.adapter.GetAuthoritativeTerminalCorrelationSnapshot();
        assert(terminal.terminalOrderIdsByCorrelation.count(corr)==0);
        assert(f.broker->places==1&&f.broker->cancels==0);
    }
}

void TestIbAdapterPreservesQualifiedIntentAtFinalSend() {
    Fixture f;
    IBContractLite c;c.symbol="ES";c.secType="FUT";c.exchange="CME";
    c.currency="USD";c.lastTradeDateOrContractMonth="202612";
    c.multiplier="50";
    IBOrderLite o;o.action="BUY";o.orderType="LMT";o.lmtPrice=6000;
    o.totalQuantity=1;o.positionEffect="OPEN";
    IBFinalOrderSendContext ctx;ctx.timeInForce="IOC";
    const std::string correlation="hepta-v1-sha256:"+std::string(64,'c');
    const auto placed=f.adapter.PlaceOrderWithResult(c,o,correlation,&ctx);
    assert(placed.disposition==VenuePlaceDisposition::Submitted);
    assert(f.broker->places==1&&f.broker->submittedTif=="IOC");
    assert(f.broker->submitted.positionEffect=="OPEN");

    const int before=f.broker->places;
    o.positionEffect="CLOSE_TODAY";
    const auto rejected=f.adapter.PlaceOrderWithResult(c,o,
        "hepta-v1-sha256:"+std::string(64,'d'),&ctx);
    assert(rejected.disposition==VenuePlaceDisposition::Rejected);
    assert(f.broker->places==before);
    assert(rejected.detail=="RISK_POSITION_EFFECT_NOT_REPRESENTABLE_BY_IB");

    o.positionEffect="CLOSE";
    ctx.timeInForce="GTC";
    const auto badTif=f.adapter.PlaceOrderWithResult(c,o,
        "hepta-v1-sha256:"+std::string(64,'e'),&ctx);
    assert(badTif.disposition==VenuePlaceDisposition::Rejected);
    assert(f.broker->places==before);
    assert(badTif.detail=="RISK_TIME_IN_FORCE_INVALID");
}
void TestTypedFlattenCapturesResultAtSendBoundary() {
    // Generic adapter fixture only. This does not expand the deployed PAPER
    // profile to STK or bypass any runtime profile/quote/kill-switch check.
    for (int fault = 0; fault < 4; ++fault) {
        Fixture f;
        IBContractLite contract; contract.symbol="XYZ"; contract.secType="STK";
        contract.exchange="SMART"; contract.currency="USD";
        assert(f.adapter.ReqRiskRefresh());
        auto position=f.Event(IBEventType::PositionSnapshotItem);
        position.key="XYZ"; position.number=1; position.contract=contract; f.Push(position);
        f.FinishRiskRefresh();
        const auto risk=f.adapter.GetAuthoritativeRiskSnapshot();
        assert(risk.accountComplete && risk.positionsComplete && risk.fxCashComplete);
        IBOrderLite order; order.action="SELL"; order.orderType="LMT";
        order.lmtPrice=1.18; order.totalQuantity=1;
        const auto send = [&](double quantity) {
            return f.adapter.PlaceReduceOnlyOrderCorrelated(contract,order,"XYZ",quantity,
                risk.connectionEpoch,risk.positionsGeneration,"fixture-quote",1,100,
                "hepta-v1-sha256:"+std::string(64,'b'),1.18,1.19);
        };
        const auto rejected=send(2);
        assert(rejected.disposition==VenueFlattenDisposition::RejectedBeforeSend);
        assert(rejected.rejection==VenueFlattenRejection::PositionChangedBeforeSend);
        assert(f.broker->places==0);
        f.broker->placeFault=fault;
        const auto result=send(1);
        assert(!failNextCancelAllocation && f.broker->places==1);
        assert(result.disposition==(fault==0 ? VenueFlattenDisposition::Submitted :
            VenueFlattenDisposition::Uncertain));
        if(fault==0 || fault==3) assert(result.orderId==f.broker->orderId);
        if(fault==2) assert(result.detail=="IB_FLATTEN_API_OUTCOME_UNCERTAIN");
        // Change the adapter's mutable legacy diagnostic after result capture.
        // Neither the returned rejection nor the send outcome is reclassified.
        auto invalid=order; invalid.orderRef="reserved";
        assert(!f.adapter.PlaceOrder(contract,invalid));
        assert(f.adapter.GetLastRejectReason()=="IB_ORDER_REF_RESERVED");
        assert(rejected.detail=="IB_FLATTEN_POSITION_CHANGED_BEFORE_SEND");
        assert(result.disposition==(fault==0 ? VenueFlattenDisposition::Submitted :
            VenueFlattenDisposition::Uncertain));
        assert(f.broker->places==1);
    }
}
void TestTypedCancelAndDeferredNoResendAfterException() {
    for (int fault = 0; fault < 4; ++fault) {
        Fixture f;
        const long id = f.Place();
        f.Push(f.Status("Submitted", 0));
        f.broker->cancelFault = fault;
        const auto result = f.adapter.CancelOrder(id);
        assert(result.disposition == (fault == 0 ? VenueCancelDisposition::Submitted : VenueCancelDisposition::Uncertain));
        assert(!failNextCancelAllocation && f.broker->cancels == 1);
    }
    Fixture disconnected;
    disconnected.adapter.Disconnect();
    assert(disconnected.adapter.CancelOrder(41).disposition == VenueCancelDisposition::RejectedBeforeSend);
    assert(disconnected.broker->cancels == 0);
    Fixture deferred;
    const long id = deferred.Place();
    assert(deferred.adapter.CancelOrder(id).disposition == VenueCancelDisposition::Deferred);
    assert(deferred.broker->cancels == 0);
    {
        Fixture accepted;
        const long acceptedId = accepted.Place();
        assert(accepted.adapter.CancelOrder(acceptedId).disposition == VenueCancelDisposition::Deferred);
        accepted.Push(accepted.Status("Accepted", 0));
        assert(accepted.broker->cancels == 1);
        accepted.Push(accepted.Status("Accepted", 0));
        assert(accepted.broker->cancels == 1);
    }

    deferred.broker->cancelFault = 1;
    bool threw = false;
    try { deferred.Push(deferred.Status("Submitted", 0)); }
    catch (const std::runtime_error&) { threw = true; }
    assert(threw && deferred.broker->cancels == 1);
    deferred.broker->cancelFault = 0;
    deferred.Push(deferred.Status("Submitted", 0));
    deferred.Push(deferred.Status("Submitted", 0));
    assert(deferred.broker->cancels == 1);
}
void TestPendingCancelDoesNotResendOrManufactureTerminalEvidence() {
    Fixture f;
    const long id=f.Place();
    f.Push(f.Status("Submitted",0));
    assert(f.adapter.CancelOrder(id).disposition==VenueCancelDisposition::Submitted);
    assert(f.broker->cancels==1);
    f.Push(f.Status("PendingCancel",0));
    for(int n=0;n<3;++n) {
        std::string reason;
        assert(!f.adapter.CanCancelOrder(id,&reason));
        assert(reason=="CANCEL_ALREADY_PENDING");
        const auto duplicate=f.adapter.CancelOrder(id);
        assert(duplicate.disposition==VenueCancelDisposition::RejectedBeforeSend);
        assert(duplicate.detail=="CANCEL_ALREADY_PENDING");
        assert(f.broker->cancels==1);
    }
    // A late Submitted callback or economic-free Filled label cannot resend.
    f.Push(f.Status("Submitted",0));
    f.Push(f.Status("Filled",0));
    assert(f.adapter.CancelOrder(id).disposition==VenueCancelDisposition::RejectedBeforeSend);
    assert(f.broker->cancels==1);
    f.Push(f.Status("Cancelled",0));
    const auto terminal=f.adapter.CancelOrder(id);
    assert(terminal.disposition==VenueCancelDisposition::RejectedBeforeSend);
    assert(terminal.detail=="ALREADY_FINAL");
    assert(f.broker->cancels==1);
    // A real fill can still win the cancellation race. The latch must not
    // hide execution evidence or misreport it as zero-fill cancellation.
    Fixture filled;
    const long filledId=filled.Place();
    filled.Push(filled.Status("PendingCancel",0));
    filled.Push(filled.Execution());
    filled.Push(filled.Status("Filled",1));
    const auto evidence=filled.adapter.GetAuthoritativeTerminalCorrelationSnapshot();
    assert(evidence.executionOrderIds.count(filledId)==1);
    assert(evidence.terminalStatusesByCorrelation.begin()->second=="Filled");
    assert(filled.adapter.CancelOrder(filledId).disposition==VenueCancelDisposition::RejectedBeforeSend);
    assert(filled.broker->cancels==0);
}
void TestPendingCancelErrorDoesNotTripGlobalFuse() {
    for (const auto* pending : {"PendingCancel", "PreCancelled"}) {
        Fixture f; const auto id = f.Place();
        f.Push(f.Status(pending, 0));
        auto error = f.Event(IBEventType::Error); error.key = "10148";
        error.value = std::string("OrderId that needs to be cancelled can not be cancelled, state: ") + pending;
        f.Push(error);
        assert(f.adapter.GetAuthoritativeTerminalCorrelationSnapshot().terminalOrderIdsByCorrelation.empty());
        assert(f.adapter.CancelOrder(id).detail == "CANCEL_ALREADY_PENDING");
        assert(f.broker->cancels == 0);
        // The diagnostic must not globally disable unrelated, valid sends.
        f.Place("hepta-v1-sha256:" + std::string(64, 'b'));
        assert(f.broker->places == 2);
    }
    Fixture unknown; unknown.Place(); unknown.Push(unknown.Status("Submitted", 0));
    auto error = unknown.Event(IBEventType::Error); error.key = "10148";
    error.value = "can not be cancelled, state: PendingCancel";
    unknown.Push(error);
    IBContractLite c; c.symbol="EUR";c.secType="CASH";c.exchange="IDEALPRO";c.currency="USD";
    IBOrderLite o; o.action="BUY";o.orderType="LMT";o.lmtPrice=1.18;o.totalQuantity=1;
    long id=-1;
    assert(!unknown.adapter.PlaceOrderCorrelated(c,o,"hepta-v1-sha256:"+std::string(64,'b'),&id));
    assert(unknown.broker->places==1);
}
void TestSameEpochPostBootstrapLiveFillAndDeferredCancel() {
    Fixture f;const auto initial=f.adapter.GetAuthoritativeTerminalCorrelationSnapshot();
    const long id=f.Place();assert(f.adapter.CancelOrder(id).disposition == VenueCancelDisposition::Deferred);
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

    const char* temporaryDirectory=std::getenv("TMPDIR");
    const std::string tempRoot=temporaryDirectory && *temporaryDirectory ?
        temporaryDirectory : "/tmp";
    const std::string pattern=tempRoot+"/hepta-live-cancel-XXXXXX";
    std::vector<char> path(pattern.begin(),pattern.end());
    path.push_back('\0');
    const int fd=mkstemp(path.data());assert(fd>=0);close(fd);
    OmsJournal journal;assert(journal.Init(path.data()));OmsJournalEvent event;
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
    assert(result.reasonCode=="AUTHORITATIVE_CANCEL_TARGET_FILLED");std::remove(path.data());
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
void TestExactNativeTerminalStatusReplayPreservesRecoveryAudit() {
    // Original callbacks from the fake broker; no socket, account or live send.
    for (int scenario=0; scenario<3; ++scenario) {
        Fixture f; const long id=f.Place();
        const double quantity=scenario==0?0.0:(scenario==1?0.4:1.0);
        const auto original=f.Status(scenario==2?"Filled":"Cancelled",quantity);
        if(quantity>0.0) f.Push(f.Execution(quantity));
        f.Push(original);
        if(quantity>0.0) f.AbsorbFill(id);
        assert(f.adapter.ReqRecoveryAuditRiskRefresh()); f.FinishRiskRefresh();
        const auto before=f.adapter.GetAuthoritativeRecoveryAuditSnapshot();
        assert(before.barrierComplete);
        for(int n=0;n<8;++n) {
            auto repeated=original; repeated.queueIngressMonotonicNs=1000+n;
            assert(f.Push(repeated)==1);
            const auto after=f.adapter.GetAuthoritativeRecoveryAuditSnapshot();
            if(!after.barrierComplete)
                throw std::runtime_error("EXACT_NATIVE_TERMINAL_REPLAY_INVALIDATED_RECOVERY_AUDIT");
            assert(after.exposureGeneration==before.exposureGeneration);
            assert(after.terminal.generation==before.terminal.generation);
            assert(after.risk.generation==before.risk.generation);
            assert(f.broker->places==1 && f.broker->cancels==0);
        }
    }
}

void TestChangedTerminalStatusStillInvalidatesRecoveryAudit() {
    const std::vector<std::function<void(IBEvent&)>> changes={
        [](IBEvent& e){e.key="ApiCancelled";},
        [](IBEvent& e){e.number=1.19;},
        [](IBEvent& e){e.number2=0.2;},
        [](IBEvent& e){e.number3+=0.1;},
        [](IBEvent& e){++e.requestId;},
        [](IBEvent& e){e.whyHeld="changed";},
        [](IBEvent& e){e.marketCapPrice=1.17;},
        [](IBEvent& e){e.remainingQuantityKnown=!e.remainingQuantityKnown;},
        [](IBEvent& e){e.advancedOrderRejectJson="changed";},
        [](IBEvent& e){e.economics.version=1;},
        [](IBEvent& e){++e.brokerClientId;},
        [](IBEvent& e){e.account="DU999";},
        [](IBEvent& e){++e.id;},
        [](IBEvent& e){e.number=std::numeric_limits<double>::quiet_NaN();}};
    int changeIndex=0;
    for(const auto& change:changes) {
        Fixture f;const long id=f.Place();f.Push(f.Execution(0.4));
        const auto original=f.Status("Cancelled",0.4);f.Push(original);f.AbsorbFill(id);
        assert(f.adapter.ReqRecoveryAuditRiskRefresh());f.FinishRiskRefresh();
        assert(f.adapter.GetAuthoritativeRecoveryAuditSnapshot().barrierComplete);
        auto changed=original;change(changed);f.Push(changed);
        if(f.adapter.GetAuthoritativeRecoveryAuditSnapshot().barrierComplete)
            throw std::runtime_error("CHANGED_NATIVE_TERMINAL_ACCEPTED_CASE_"+std::to_string(changeIndex));
        ++changeIndex;
        // A subsequent old duplicate must never repair a now-invalid barrier.
        f.Push(original);
        assert(!f.adapter.GetAuthoritativeRecoveryAuditSnapshot().barrierComplete);
        assert(f.broker->places==1 && f.broker->cancels==0);
    }
    Fixture f;f.Place();const auto original=f.Status("Cancelled",0);f.Push(original);
    assert(f.adapter.ReqRecoveryAuditRiskRefresh());f.FinishRiskRefresh();
    auto old=original;old.connectionEpoch=0;assert(f.Push(old)==0);
    // Preserve the original conservative quarantine for an untagged epoch.
    assert(!f.adapter.GetAuthoritativeRecoveryAuditSnapshot().barrierComplete);
    f.adapter.Disconnect();assert(f.adapter.Connect());f.Bootstrap();
    f.Push(original);assert(!f.adapter.GetAuthoritativeRecoveryAuditSnapshot().barrierComplete);
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
void TestTerminalRefreshBindsUnknownCompletedOrderIdFromExactExecutionCorrelation() {
    Fixture f(false);
    const std::string correlation =
        "hepta-v1-sha256:" + std::string(64, 'a');
    const long id = f.Place(correlation);

    auto completed = f.Event(IBEventType::CompletedOrder);
    completed.id = 0;
    completed.key = "Filled";
    completed.number = 0.0001;
    completed.number2 = 0.0;
    completed.order.orderRef = f.broker->submitted.orderRef;
    f.Push(completed);

    auto execution = f.Execution();
    execution.id = id;
    execution.requestId = f.broker->executionsRequest;
    execution.key = "historical-execution-id";
    execution.order.orderRef = f.broker->submitted.orderRef;
    f.Push(execution);
    f.FinishBootstrap();

    const auto snapshot =
        f.adapter.GetAuthoritativeTerminalCorrelationSnapshot();
    assert(snapshot.complete);
    assert(snapshot.terminalStatusesByCorrelation.at(correlation) == "Filled");
    assert(snapshot.terminalOrderIdsByCorrelation.at(correlation) == id);
    assert(snapshot.executionCorrelations.count(correlation) == 1);
    assert(snapshot.executionOrderIds.count(id) == 1);
    assert(snapshot.HasEconomicTerminal(correlation, id, f.broker->epoch));
}

void TestTerminalRefreshDoesNotCrossCorrelateReusedNumericOrderId() {
    Fixture f(false);
    const std::string currentCorrelation =
        "hepta-v1-sha256:" + std::string(64, 'a');
    const std::string historicalCorrelation =
        "hepta-v1-sha256:" + std::string(64, 'b');
    const long reusedId = f.Place(currentCorrelation);

    auto completed = f.Event(IBEventType::CompletedOrder);
    completed.id = reusedId;
    completed.key = "Filled";
    completed.number = 1.0;
    completed.number2 = 1.18;
    completed.order.orderRef = f.broker->submitted.orderRef;
    f.Push(completed);

    auto historical = f.Execution();
    historical.id = reusedId;
    historical.requestId = f.broker->executionsRequest;
    historical.key = "historical-execution-id";
    historical.value = "SLD";
    std::string reason;
    assert(IbVenueCorrelationCodec::EncodeOrderRef(
        historicalCorrelation, historical.order.orderRef, reason));
    f.Push(historical);
    f.FinishBootstrap();

    const auto snapshot =
        f.adapter.GetAuthoritativeTerminalCorrelationSnapshot();
    assert(snapshot.complete);
    assert(snapshot.executionCorrelations.count(historicalCorrelation) == 1);
    assert(snapshot.executionCorrelations.count(currentCorrelation) == 0);
    // The historical execution for numeric id N cannot prove the current
    // correlation's Filled record. Compatibility numeric execution evidence
    // therefore remains empty as well.
    assert(snapshot.executionOrderIds.count(reusedId) == 0);
    assert(snapshot.terminalOrderIdsByCorrelation.count(currentCorrelation) == 0);
}

void TestActualAdapterLockIncludesConnectionAndStreamInBrokerAudit()
{
    Fixture f;
    const auto ready = f.adapter.GetAuthoritativeRecoveryAuditSnapshot();
    assert(ready.transportConnected);
    assert(ready.eventStreamAuthoritative);
    assert(ready.active.complete && ready.terminal.complete);
    assert(ready.active.connectionEpoch == ready.terminal.connectionEpoch);
    f.adapter.Disconnect();
    const auto disconnected = f.adapter.GetAuthoritativeRecoveryAuditSnapshot();
    assert(!disconnected.transportConnected);
    assert(!disconnected.active.complete);
    assert(!disconnected.terminal.complete);
}


void TestOriginalPostFenceReadSequencesNewAllOpenEndBeforeRisk()
{
    Fixture f;
    // No original custodian fence has been installed in this adapter unit
    // fixture. Only the separate original policy control can authorize
    // BeginAccountPostFenceRead in production.
    assert(!f.adapter.PollAccountPostFenceRead().started);
    assert(f.broker->allOrderReads==1);
    std::string reason;
    assert(!f.adapter.BeginAccountPostFenceRead(reason));
    assert(reason=="IB_ACCOUNT_POST_FENCE_BASELINE_INCOMPLETE");
    assert(f.broker->allOrderReads==1);
    assert(f.adapter.ReqRecoveryAuditRiskRefresh());
    f.FinishRiskRefresh();
    assert(f.adapter.GetAuthoritativeRecoveryAuditSnapshot().barrierComplete);
    assert(f.broker->accountReads==1 && f.broker->positionReads==1);

    assert(f.adapter.BeginAccountPostFenceRead(reason));
    assert(reason=="IB_ACCOUNT_POST_FENCE_OPEN_ORDER_END_PENDING");
    assert(f.broker->allOrderReads==2);
    auto pending=f.adapter.PollAccountPostFenceRead();
    assert(pending.started && !pending.activeReadComplete);
    assert(!pending.activeThenRiskObserved);
    assert(pending.maintenanceStopPermitted==false);
    assert(f.broker->accountReads==1 && f.broker->positionReads==1);
    auto seen=f.Event(IBEventType::OpenOrderEnd);
    f.Push(seen);
    auto riskPending=f.adapter.PollAccountPostFenceRead();
    assert(riskPending.activeReadComplete);
    assert(!riskPending.riskReadComplete);
    assert(f.broker->accountReads==2 && f.broker->positionReads==2);
    // Repeated reads do not resend Broker refreshes.
    assert(!f.adapter.PollAccountPostFenceRead().riskReadComplete);
    assert(f.broker->accountReads==2 && f.broker->positionReads==2);
    f.FinishRiskRefresh();
    const auto observed=f.adapter.PollAccountPostFenceRead();
    assert(observed.activeReadComplete && observed.riskReadComplete);
    assert(observed.activeThenRiskObserved && observed.brokerZeroObserved);
    assert(observed.connectionEpoch==f.broker->epoch);
    assert(observed.activeGeneration==2);
    assert(observed.riskGeneration>0);
    assert(!observed.terminalPostFenceComplete && !observed.maintenanceStopPermitted);
    assert(observed.reasonCode=="IB_ACCOUNT_POST_FENCE_ACTIVE_RISK_ZERO_NOT_STOP_PERMIT");
    assert(!f.adapter.BeginAccountPostFenceRead(reason));
    assert(reason=="IB_ACCOUNT_POST_FENCE_READ_ALREADY_ATTEMPTED");
    assert(f.broker->allOrderReads==2 && f.broker->accountReads==2);

    // A connection loss after an observed zero revokes even this read-only
    // diagnostic: it cannot survive an unrelated new broker epoch.
    f.adapter.Disconnect();
    const auto lost=f.adapter.PollAccountPostFenceRead();
    assert(!lost.activeThenRiskObserved && !lost.brokerZeroObserved);
    assert(!lost.maintenanceStopPermitted);
    assert(lost.reasonCode=="IB_ACCOUNT_POST_FENCE_CONNECTION_OR_STREAM_CHANGED");
}

void TestPostFenceReadRejectsForeignOrdersAndIncompleteRefresh()
{
    Fixture foreign;
    assert(foreign.adapter.ReqRecoveryAuditRiskRefresh());
    foreign.FinishRiskRefresh();
    std::string reason;
    assert(foreign.adapter.BeginAccountPostFenceRead(reason));
    auto order=foreign.Event(IBEventType::OpenOrder);
    order.id=146;order.brokerClientId=999;order.value="Submitted";
    foreign.Push(order);foreign.Push(foreign.Event(IBEventType::OpenOrderEnd));
    auto afterOpen=foreign.adapter.PollAccountPostFenceRead();
    assert(afterOpen.activeReadComplete && !afterOpen.riskReadComplete);
    foreign.FinishRiskRefresh();
    const auto nonzero=foreign.adapter.PollAccountPostFenceRead();
    assert(nonzero.activeThenRiskObserved && !nonzero.brokerZeroObserved);
    assert(nonzero.globalActiveOrders==1);
    assert(!nonzero.maintenanceStopPermitted);
    assert(nonzero.reasonCode=="IB_ACCOUNT_POST_FENCE_ACTIVE_RISK_PRESENT_NOT_STOP_PERMIT");

    Fixture invalid;
    assert(invalid.adapter.ReqRecoveryAuditRiskRefresh());
    invalid.FinishRiskRefresh();
    assert(invalid.adapter.BeginAccountPostFenceRead(reason));
    auto unknown=invalid.Event(IBEventType::OpenOrder);
    unknown.id=365;unknown.brokerClientId=-1;unknown.value="Submitted";
    invalid.Push(unknown);invalid.Push(invalid.Event(IBEventType::OpenOrderEnd));
    const auto poisoned=invalid.adapter.PollAccountPostFenceRead();
    assert(!poisoned.activeThenRiskObserved);
    assert(poisoned.reasonCode=="IB_ACCOUNT_POST_FENCE_ACTIVE_DOWNLOAD_FAILED");
    assert(!invalid.adapter.PollAccountPostFenceRead().activeThenRiskObserved);
    assert(invalid.broker->accountReads==1); // never begins risk from bad orders

    Fixture pending;
    assert(pending.adapter.ReqRecoveryAuditRiskRefresh());
    pending.FinishRiskRefresh();
    assert(pending.adapter.BeginAccountPostFenceRead(reason));
    assert(!pending.adapter.PollAccountPostFenceRead().activeReadComplete);
    pending.adapter.Disconnect();
    const auto closed=pending.adapter.PollAccountPostFenceRead();
    assert(closed.reasonCode=="IB_ACCOUNT_POST_FENCE_CONNECTION_OR_STREAM_CHANGED");
    assert(pending.broker->accountReads==1);
}


void TestAccountPostFenceProbeCannotSurviveOwnerRevocationOrNewSend()
{
    Fixture revoked;
    assert(revoked.adapter.ReqRecoveryAuditRiskRefresh());
    revoked.FinishRiskRefresh();
    std::string reason;
    assert(revoked.adapter.BeginAccountPostFenceRead(reason));
    revoked.adapter.InvalidateAccountPostFenceRead(
        "IB_ACCOUNT_POST_FENCE_LOCAL_DRAIN_CHANGED");
    revoked.Push(revoked.Event(IBEventType::OpenOrderEnd));
    const auto blocked=revoked.adapter.PollAccountPostFenceRead();
    assert(blocked.started && !blocked.activeThenRiskObserved);
    assert(blocked.reasonCode=="IB_ACCOUNT_POST_FENCE_LOCAL_DRAIN_CHANGED");
    assert(revoked.broker->accountReads==1); // NO risk request after revoke

    Fixture concurrent;
    assert(concurrent.adapter.ReqRecoveryAuditRiskRefresh());
    concurrent.FinishRiskRefresh();
    assert(concurrent.adapter.BeginAccountPostFenceRead(reason));
    concurrent.Push(concurrent.Event(IBEventType::OpenOrderEnd));
    // This is an isolated Broker FAKE with no original account fence. A
    // concurrent venue send must invalidate the post-fence watermark;
    // production stops the same send at the original coordinator gate.
    concurrent.Place("hepta-v1-sha256:"+std::string(64,'f'));
    assert(concurrent.broker->places==1);
    const auto mutated=concurrent.adapter.PollAccountPostFenceRead();
    assert(mutated.started && !mutated.activeThenRiskObserved);
    assert(mutated.reasonCode=="IB_ACCOUNT_POST_FENCE_BROKER_MUTATION_OR_FILL");
    assert(concurrent.broker->accountReads==1);
}


void TestAccountPostFenceFreshTerminalEpochRequiresExistingRecoveryOwnerAudit()
{
    Fixture f;
    std::string reason;
    // Qualify a single post-fence active -> coherent risk generation in the
    // adapter fixture. Real production starts it only after the original
    // authenticated Crypto owner fsyncs the coordinator account fence.
    assert(f.adapter.ReqRecoveryAuditRiskRefresh());f.FinishRiskRefresh();
    assert(f.adapter.BeginAccountPostFenceRead(reason));
    f.Push(f.Event(IBEventType::OpenOrderEnd));
    const auto awaitingRisk=f.adapter.PollAccountPostFenceRead();
    assert(awaitingRisk.activeReadComplete && !awaitingRisk.riskReadComplete);
    f.FinishRiskRefresh();
    const auto oldEpoch=f.adapter.PollAccountPostFenceRead();
    assert(oldEpoch.brokerZeroObserved && oldEpoch.activeThenRiskObserved);
    assert(!oldEpoch.terminalPostFenceComplete);

    // Health/status alone cannot promote a fresh terminal epoch or trigger
    // reconnect. The existing recovery audit control has to demand it first.
    assert(!f.adapter.BindAccountPostFenceToFreshTerminalEpoch(reason));
    assert(reason=="IB_ACCOUNT_POST_FENCE_NEW_TERMINAL_BINDING_UNAVAILABLE");
    const auto originalEpoch=f.adapter.GetConnectionEpoch();
    const auto requested=f.adapter.BeginRecoveryAuditBarrier();
    assert(requested.newConnectionEpochRequired);
    assert(!requested.barrierComplete);
    assert(f.adapter.BindAccountPostFenceToFreshTerminalEpoch(reason));
    assert(reason=="IB_ACCOUNT_POST_FENCE_NEW_TERMINAL_EPOCH_REQUIRED");
    const auto mustReconnect=f.adapter.PollAccountPostFenceRead();
    assert(mustReconnect.terminalNewEpochRequired);
    assert(!mustReconnect.terminalNewEpochObserved);
    assert(!mustReconnect.brokerZeroObserved);
    assert(!mustReconnect.maintenanceStopPermitted);
    // Neither the adapter begin nor the status read has disconnected us:
    // the existing owner-controlled runtime reconnect state owns that work.
    assert(f.adapter.GetConnectionEpoch()==originalEpoch);
    assert(f.broker->connected);
    assert(f.broker->places==0 && f.broker->cancels==0);

    f.adapter.Disconnect();
    assert(f.adapter.PollAccountPostFenceRead().terminalNewEpochRequired);
    assert(f.adapter.Connect());
    assert(f.adapter.GetConnectionEpoch()==originalEpoch+1);
    auto beforeDownload=f.adapter.PollAccountPostFenceRead();
    assert(!beforeDownload.terminalNewEpochObserved);
    assert(beforeDownload.reasonCode==
        "IB_ACCOUNT_POST_FENCE_NEW_TERMINAL_BARRIER_PENDING");

    f.Bootstrap(false);
    assert(!f.adapter.PollAccountPostFenceRead().terminalNewEpochObserved);
    f.FinishBootstrap();
    assert(!f.adapter.PollAccountPostFenceRead().terminalNewEpochObserved);
    assert(f.adapter.ReqRecoveryAuditRiskRefresh());
    assert(!f.adapter.PollAccountPostFenceRead().terminalNewEpochObserved);
    f.FinishRiskRefresh();
    const auto fresh=f.adapter.PollAccountPostFenceRead();
    assert(fresh.terminalNewEpochRequired);
    assert(fresh.terminalNewEpochObserved && fresh.terminalPostFenceComplete);
    assert(fresh.newTerminalConnectionEpoch==originalEpoch+1);
    assert(fresh.newTerminalGeneration>0);
    assert(fresh.newActiveGeneration>0 && fresh.newRiskGeneration>0);
    assert(fresh.activeThenRiskObserved && fresh.brokerZeroObserved);
    assert(fresh.globalActiveOrders==0);
    assert(!fresh.maintenanceStopPermitted);
    assert(fresh.reasonCode==
        "IB_ACCOUNT_POST_FENCE_NEW_TERMINAL_EPOCH_ZERO_NOT_STOP_PERMIT");
    const auto duplicate=f.adapter.PollAccountPostFenceRead();
    assert(duplicate.terminalNewEpochObserved);
    assert(!duplicate.maintenanceStopPermitted);
    assert(f.adapter.ReqRiskRefresh());
    f.FinishRiskRefresh();
    const auto superseded=f.adapter.PollAccountPostFenceRead();
    assert(!superseded.terminalNewEpochObserved);
    assert(!superseded.brokerZeroObserved);
    assert(superseded.reasonCode ==
        "IB_ACCOUNT_POST_FENCE_NEW_TERMINAL_GENERATION_DRIFT");
}

void TestAccountPostFenceFreshTerminalEpochCannotMaskReconnectOrMutation()
{
    const auto start=[&](Fixture& f) {
        std::string reason;
        assert(f.adapter.ReqRecoveryAuditRiskRefresh());f.FinishRiskRefresh();
        assert(f.adapter.BeginAccountPostFenceRead(reason));
        f.Push(f.Event(IBEventType::OpenOrderEnd));
        assert(!f.adapter.PollAccountPostFenceRead().riskReadComplete);
        f.FinishRiskRefresh();
        assert(f.adapter.PollAccountPostFenceRead().brokerZeroObserved);
        const auto newEpoch=f.adapter.BeginRecoveryAuditBarrier();
        assert(newEpoch.newConnectionEpochRequired);
        assert(f.adapter.BindAccountPostFenceToFreshTerminalEpoch(reason));
    };
    {
        Fixture stale;
        start(stale);
        const auto oldEpoch=stale.adapter.GetConnectionEpoch();
        stale.adapter.Disconnect();
        assert(stale.adapter.Connect());
        stale.adapter.Disconnect();
        assert(stale.adapter.Connect());
        assert(stale.adapter.GetConnectionEpoch()==oldEpoch+2);
        const auto denied=stale.adapter.PollAccountPostFenceRead();
        assert(denied.terminalNewEpochRequired);
        assert(!denied.terminalNewEpochObserved);
        assert(denied.reasonCode==
            "IB_ACCOUNT_POST_FENCE_NEW_TERMINAL_EPOCH_SKIPPED");
    }
    {
        Fixture sent;
        start(sent);
        sent.adapter.Disconnect();assert(sent.adapter.Connect());sent.Bootstrap();
        assert(sent.adapter.ReqRecoveryAuditRiskRefresh());sent.FinishRiskRefresh();
        assert(sent.adapter.PollAccountPostFenceRead().terminalNewEpochObserved);
        // A new actual Broker send (even in this offline fake) invalidates the
        // old account-wide result instead of carrying its zero forward.
        sent.Place("hepta-v1-sha256:"+std::string(64,'c'));
        const auto mutated=sent.adapter.PollAccountPostFenceRead();
        assert(!mutated.terminalNewEpochObserved);
        assert(!mutated.brokerZeroObserved);
        assert(mutated.reasonCode==
            "IB_ACCOUNT_POST_FENCE_NEW_EPOCH_BROKER_MUTATION");
    }
    {
        Fixture foreign;
        start(foreign);
        foreign.adapter.Disconnect();assert(foreign.adapter.Connect());
        foreign.Bootstrap();
        auto outside=foreign.Event(IBEventType::OpenOrder);
        outside.id=146;outside.brokerClientId=999;outside.value="Submitted";
        foreign.Push(outside);
        assert(foreign.adapter.ReqRecoveryAuditRiskRefresh());
        foreign.FinishRiskRefresh();
        const auto nonzero=foreign.adapter.PollAccountPostFenceRead();
        assert(nonzero.terminalNewEpochObserved);
        assert(!nonzero.brokerZeroObserved && nonzero.globalActiveOrders==1);
        assert(!nonzero.maintenanceStopPermitted);
        assert(nonzero.reasonCode==
            "IB_ACCOUNT_POST_FENCE_NEW_TERMINAL_EPOCH_RISK_PRESENT");
    }
}


void TestMultipleUnboundManualTwsOrdersRetainDistinctBrokerPermanentIds()
{
    Fixture f;
    assert(f.broker->places==0 && f.broker->cancels==0);
    assert(f.adapter.ReqAuthoritativeOpenOrders());
    auto manual=f.Event(IBEventType::OpenOrder);
    manual.id=0;manual.brokerClientId=0;manual.brokerPermId=900101;
    manual.value="Submitted";
    manual.order.action="BUY";
    manual.order.totalQuantity=3;
    f.Push(manual);
    auto second=manual;
    second.brokerPermId=900102;
    second.order.action="SELL";
    f.Push(second);
    f.Push(f.Event(IBEventType::OpenOrderEnd));
    const auto active=f.adapter.GetAuthoritativeCorrelationSnapshot();
    assert(active.complete);
    assert(active.activeOrderIds.size()==1);
    assert(active.activeOrderIds.count(0)==1);
    assert(active.activeOrderClientIdsByOrderId.at(0)==0);
    assert(active.unboundManualPermanentIds.size()==2);
    assert(active.activeOrderByPermanentId.size()==2);
    assert(active.activeOrderByPermanentId.at(900101).first==0);
    assert(active.activeOrderByPermanentId.at(900102).second==0);
    assert(!active.activeOrderMissingPermanentId);
    assert(active.VisibleActiveOrderCount()==2);
    assert(active.activeOrderIdsByCorrelation.empty());
    assert(f.broker->places==0 && f.broker->cancels==0);

    assert(f.adapter.ReqRecoveryAuditRiskRefresh());
    f.FinishRiskRefresh();
    const auto risk=f.adapter.GetAuthoritativeRecoveryAuditSnapshot();
    assert(risk.barrierComplete);
    const auto examined=InspectIbPaperAccountBrokerEvidence(risk,73);
    assert(examined.snapshotCoherent && !examined.brokerZeroObserved);
    assert(examined.globalActiveOrders==2);
    assert(examined.foreignActiveOrders==2);
    assert(examined.observedUnboundManualOrders==2);
    assert(examined.observedBrokerPermanentIds==2);
    assert(examined.observedPermanentIdentityComplete);
    assert(!examined.externalManualTwsUniverseComplete);
    assert(std::string(examined.reasonCode)==
           "IB_ACCOUNT_BROKER_UNBOUND_MANUAL_ORDERS_PRESENT");

    // A THIRD same-number manual order appearing asynchronously must also
    // remain separately visible, not replace the previous permIds.
    auto third=manual;third.brokerPermId=900103;
    f.Push(third);
    const auto newer=f.adapter.GetAuthoritativeCorrelationSnapshot();
    assert(newer.complete && newer.VisibleActiveOrderCount()==3);
    assert(newer.unboundManualPermanentIds.size()==3);
    assert(f.broker->places==0 && f.broker->cancels==0);

    // A NEW complete reqAllOpenOrders cohort with no manual orders can
    // retire those observed IDs. Local status/cancel did not invent absence.
    assert(f.adapter.ReqAuthoritativeOpenOrders());
    f.Push(f.Event(IBEventType::OpenOrderEnd));
    const auto newRead=f.adapter.GetAuthoritativeCorrelationSnapshot();
    assert(newRead.complete && newRead.VisibleActiveOrderCount()==0);
    assert(newRead.unboundManualPermanentIds.empty());
    assert(newRead.activeOrderByPermanentId.empty());
}

void TestManualPermIdMissingCollisionAndWrongClientFailClosed()
{
    {
        Fixture missing;
        assert(missing.adapter.ReqAuthoritativeOpenOrders());
        auto manual=missing.Event(IBEventType::OpenOrder);
        manual.id=0;manual.brokerClientId=0;manual.brokerPermId=0;
        manual.value="Submitted";
        missing.Push(manual);
        missing.Push(missing.Event(IBEventType::OpenOrderEnd));
        const auto bad=missing.adapter.GetAuthoritativeCorrelationSnapshot();
        assert(!bad.complete);
        assert(bad.reasonCode=="IB_UNBOUND_MANUAL_PERMID_REQUIRED");
        assert(bad.VisibleActiveOrderCount()==0); // incomplete != zero proof
        assert(missing.broker->places==0 && missing.broker->cancels==0);
    }
    {
        Fixture wrong;
        assert(wrong.adapter.ReqAuthoritativeOpenOrders());
        auto foreign=wrong.Event(IBEventType::OpenOrder);
        foreign.id=0;foreign.brokerClientId=701;
        foreign.brokerPermId=900301;foreign.value="Submitted";
        wrong.Push(foreign);wrong.Push(wrong.Event(IBEventType::OpenOrderEnd));
        const auto bad=wrong.adapter.GetAuthoritativeCorrelationSnapshot();
        assert(!bad.complete);
        assert(bad.reasonCode=="IB_UNBOUND_MANUAL_PERMID_REQUIRED");
    }
    {
        Fixture collided;
        assert(collided.adapter.ReqAuthoritativeOpenOrders());
        auto first=collided.Event(IBEventType::OpenOrder);
        first.id=101;first.brokerClientId=73;first.brokerPermId=991001;
        first.value="Submitted";
        first.order.action="BUY";first.order.totalQuantity=1;
        collided.Push(first);
        auto second=first;
        second.id=102;second.brokerPermId=991001;
        collided.Push(second);
        collided.Push(collided.Event(IBEventType::OpenOrderEnd));
        const auto bad=collided.adapter.GetAuthoritativeCorrelationSnapshot();
        assert(!bad.complete);
        assert(bad.reasonCode=="IB_ACTIVE_ORDER_PERMID_COLLISION");
    }
    {
        Fixture incremental;
        assert(incremental.adapter.ReqAuthoritativeOpenOrders());
        auto order=incremental.Event(IBEventType::OpenOrder);
        order.id=101;order.brokerClientId=999;
        order.brokerPermId=900451;order.value="Submitted";
        incremental.Push(order);
        incremental.Push(incremental.Event(IBEventType::OpenOrderEnd));
        assert(incremental.adapter.GetAuthoritativeCorrelationSnapshot().complete);
        auto conflict=order;conflict.id=102;
        incremental.Push(conflict);
        const auto bad=incremental.adapter.GetAuthoritativeCorrelationSnapshot();
        assert(!bad.complete);
        assert(bad.reasonCode=="IB_ACTIVE_ORDER_PERMID_COLLISION");
        assert(incremental.broker->places==0 && incremental.broker->cancels==0);
    }
    {
        Fixture absent;
        assert(absent.adapter.ReqAuthoritativeOpenOrders());
        auto order=absent.Event(IBEventType::OpenOrder);
        order.id=333;order.brokerClientId=999;order.brokerPermId=0;
        order.value="Submitted";
        absent.Push(order);absent.Push(absent.Event(IBEventType::OpenOrderEnd));
        const auto unproven=absent.adapter.GetAuthoritativeCorrelationSnapshot();
        assert(unproven.complete);
        assert(unproven.VisibleActiveOrderCount()==1);
        assert(unproven.activeOrderMissingPermanentId);
        assert(unproven.activeOrderByPermanentId.empty());
    }
}


void TestManualPermIdBlocksPostFenceBrokerZeroAndUnknownAccountFailsClosed()
{
    Fixture f;
    assert(f.adapter.ReqRecoveryAuditRiskRefresh());
    f.FinishRiskRefresh();
    std::string reason;
    assert(f.adapter.BeginAccountPostFenceRead(reason));
    for (std::int64_t permId=900701;permId<=900702;++permId) {
        auto manual=f.Event(IBEventType::OpenOrder);
        manual.id=0;
        manual.brokerClientId=0;
        manual.brokerPermId=permId;
        manual.value="Submitted";
        f.Push(manual);
    }
    f.Push(f.Event(IBEventType::OpenOrderEnd));
    const auto pending=f.adapter.PollAccountPostFenceRead();
    assert(pending.activeReadComplete && !pending.riskReadComplete);
    f.FinishRiskRefresh();
    const auto observed=f.adapter.PollAccountPostFenceRead();
    assert(observed.activeThenRiskObserved);
    assert(observed.globalActiveOrders==2);
    assert(observed.observedUnboundManualOrders==2);
    assert(observed.observedBrokerPermanentIds==2);
    assert(observed.observedPermanentIdentityComplete);
    assert(!observed.externalManualTwsUniverseComplete);
    assert(!observed.brokerZeroObserved);
    assert(!observed.maintenanceStopPermitted);
    assert(observed.reasonCode==
        "IB_ACCOUNT_POST_FENCE_ACTIVE_RISK_PRESENT_NOT_STOP_PERMIT");
    assert(f.broker->places==0 && f.broker->cancels==0);

    Fixture missing;
    assert(missing.adapter.ReqAuthoritativeOpenOrders());
    auto unknown=missing.Event(IBEventType::OpenOrder);
    unknown.id=0;unknown.brokerClientId=0;
    unknown.brokerPermId=991001;unknown.account.clear();
    unknown.value="Submitted";
    missing.Push(unknown);
    missing.Push(missing.Event(IBEventType::OpenOrderEnd));
    const auto rejected=missing.adapter.GetAuthoritativeCorrelationSnapshot();
    assert(!rejected.complete);
    assert(rejected.reasonCode=="IB_ACTIVE_ORDER_ACCOUNT_UNKNOWN");

    Fixture late;
    const auto old=late.adapter.GetAuthoritativeCorrelationSnapshot();
    assert(old.complete && old.activeOrderIds.empty());
    auto lateUnknown=late.Event(IBEventType::OpenOrder);
    lateUnknown.id=0;lateUnknown.brokerClientId=0;
    lateUnknown.brokerPermId=991002;lateUnknown.account.clear();
    lateUnknown.value="Submitted";
    late.Push(lateUnknown);
    const auto invalidated=late.adapter.GetAuthoritativeCorrelationSnapshot();
    assert(!invalidated.complete);
    assert(invalidated.reasonCode=="IB_ACTIVE_ORDER_ACCOUNT_UNKNOWN");
    assert(late.broker->places==0 && late.broker->cancels==0);

    // A positively identified DIFFERENT IB account is not allowed to
    // contaminate the original configured account's risk projection.
    Fixture other;
    assert(other.adapter.ReqAuthoritativeOpenOrders());
    auto knownForeign=other.Event(IBEventType::OpenOrder);
    knownForeign.id=0;knownForeign.brokerClientId=0;
    knownForeign.brokerPermId=991003;
    knownForeign.account="DU_OTHER";
    knownForeign.value="Submitted";
    other.Push(knownForeign);
    other.Push(other.Event(IBEventType::OpenOrderEnd));
    const auto isolated=other.adapter.GetAuthoritativeCorrelationSnapshot();
    assert(isolated.complete && isolated.VisibleActiveOrderCount()==0);
}


void TestBrokerProtectiveMetadataIsOnlyVisibleCandidateNotProtection()
{
    Fixture f;
    assert(f.adapter.ReqAuthoritativeOpenOrders());
    auto stop=f.Event(IBEventType::OpenOrder);
    stop.id=811;stop.brokerPermId=900811;
    stop.brokerClientId=999;
    stop.value="Submitted";
    stop.order.action="SELL";stop.order.orderType="STP";
    stop.order.auxPrice=99.0;
    stop.order.totalQuantity=1.0;
    stop.brokerOcaGroup="fixture-oca";
    stop.brokerParentOrderId=810;
    stop.brokerTransmit=true;
    f.Push(stop);f.Push(f.Event(IBEventType::OpenOrderEnd));
    const auto view=f.adapter.GetAuthoritativeCorrelationSnapshot();
    assert(view.complete && view.VisibleActiveOrderCount()==1);
    assert(view.visibleProtectiveCandidatesByPermId.size()==1);
    const auto candidate=view.visibleProtectiveCandidatesByPermId.at(900811);
    assert(candidate.orderType=="STP" && candidate.action=="SELL");
    assert(candidate.ocaGroup=="fixture-oca" &&
           candidate.parentOrderId==810 && candidate.transmit);
    assert(candidate.stopTrigger==99.0 && candidate.totalQuantity==1.0);
    assert(candidate.brokerClientId==999 && candidate.orderId==811);
    assert(f.broker->places==0 && f.broker->cancels==0);
    // A subsequent genuine global Broker End without this candidate must
    // replace it, never keep an old local "protective" impression.
    assert(f.adapter.ReqAuthoritativeOpenOrders());
    f.Push(f.Event(IBEventType::OpenOrderEnd));
    const auto empty=f.adapter.GetAuthoritativeCorrelationSnapshot();
    assert(empty.complete && empty.visibleProtectiveCandidatesByPermId.empty());

    Fixture noPerm;
    assert(noPerm.adapter.ReqAuthoritativeOpenOrders());
    auto unknown=noPerm.Event(IBEventType::OpenOrder);
    unknown.id=812;unknown.brokerClientId=999;unknown.brokerPermId=0;
    unknown.value="Submitted";unknown.order.orderType="STP";
    unknown.order.action="SELL";unknown.order.auxPrice=98;
    unknown.order.totalQuantity=1;
    noPerm.Push(unknown);
    noPerm.Push(noPerm.Event(IBEventType::OpenOrderEnd));
    const auto incomplete=noPerm.adapter.GetAuthoritativeCorrelationSnapshot();
    assert(incomplete.complete && incomplete.protectiveMetadataIncomplete);
    assert(incomplete.visibleProtectiveCandidatesByPermId.empty());

    Fixture conflict;
    assert(conflict.adapter.ReqAuthoritativeOpenOrders());
    auto original=conflict.Event(IBEventType::OpenOrder);
    original.id=813;original.brokerClientId=999;original.brokerPermId=900813;
    original.value="Submitted";original.order.orderType="STP";
    original.order.action="SELL";original.order.totalQuantity=1;
    original.order.auxPrice=90.0;
    auto divergent=original;divergent.order.auxPrice=88.0;
    conflict.Push(original);conflict.Push(divergent);
    conflict.Push(conflict.Event(IBEventType::OpenOrderEnd));
    const auto poisoned=conflict.adapter.GetAuthoritativeCorrelationSnapshot();
    assert(!poisoned.complete);
    assert(poisoned.visibleProtectiveCandidatesByPermId.empty());
    assert(conflict.broker->places==0 && conflict.broker->cancels==0);
}

void TestAdapterForwardsBoundedHandshakeBudget() {
    Broker* broker=new Broker();
    HeptaIBGatewayAdapter adapter{std::unique_ptr<IIBApiWrapper>(broker)};
    HeptaIBConfig cfg;cfg.account="DU123";cfg.clientId=73;cfg.readOnly=false;
    cfg.risk.enableOrderSubmission=true;cfg.risk.maxDailyOrders=100;
    cfg.risk.duplicateOrderWindowSec=0;cfg.risk.maxPriceDeviationBps=0;
    assert(adapter.Init(cfg));assert(adapter.Connect(2500));
    assert(broker->connectBudgetMs==2500);adapter.Disconnect();
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
    TestExactNativeTerminalStatusReplayPreservesRecoveryAudit();
    TestChangedTerminalStatusStillInvalidatesRecoveryAudit();
    TestInvalidCashMonitorRepetitionCannotAbortIndependentPositionsRefresh();
    TestCashMonitorRedactedGenerationObservation();
    TestCashMonitorChangeRequiresFreshCoherentBrokerGeneration();
    TestDuplicateRejectedCashObservationDoesNotErasePhysicalCrypto();
    TestCashOutsideGenerationCannotBorrowOrdinaryRiskFreshness();
    TestIbAdapterPreservesQualifiedIntentAtFinalSend();
    TestExactOrderSizeParse320TerminatesOnlyCurrentBoundCashOrder();
    TestTypedFlattenCapturesResultAtSendBoundary();
    TestTypedCancelAndDeferredNoResendAfterException();
    TestPendingCancelDoesNotResendOrManufactureTerminalEvidence();
    TestPendingCancelErrorDoesNotTripGlobalFuse();
    TestIncompleteHistoryReadOnlyAdmissionDoesNotCreateTerminalProof();
    TestForeignClientOpenOrdersNeverBecomeOurCancelOrTerminalAuthority();
    TestForeignAndOwnClientOrderIdCollisionFailsGlobalSnapshotClosed();
    TestRecoveredOpenOrderRestoresOriginalTerminalBindingWithoutSend();
    TestCryptoCashBaselineUsesSharedResidentAndCanaryEnvelope();
    TestCashMarketTerminalNeedsActualExecutionAndNativeFinalStatus();
    TestWrapperQuantityTrackerCannotMixBrokerClients();
    TestSameEpochPostBootstrapLiveFillAndDeferredCancel();TestCancelledAndPartialCancelled();
    TestBoundContractExtensionsAndBrokerEnrichment();TestForeignStatusCannotRetireOurPartialOrder();
    TestValidatedSyntheticFillWithoutNativeStatus();
    TestQuarantineInvalidatesRecoveryBarrier();
    TestHostileEvidenceCannotPromoteFill();TestTerminalRefreshBindsUnknownCompletedOrderIdFromExactExecutionCorrelation();TestTerminalRefreshDoesNotCrossCorrelateReusedNumericOrderId();TestOriginalPostFenceReadSequencesNewAllOpenEndBeforeRisk();TestPostFenceReadRejectsForeignOrdersAndIncompleteRefresh();TestAccountPostFenceProbeCannotSurviveOwnerRevocationOrNewSend();TestMultipleUnboundManualTwsOrdersRetainDistinctBrokerPermanentIds();TestManualPermIdMissingCollisionAndWrongClientFailClosed();TestManualPermIdBlocksPostFenceBrokerZeroAndUnknownAccountFailsClosed();TestAccountPostFenceFreshTerminalEpochRequiresExistingRecoveryOwnerAudit();TestAccountPostFenceFreshTerminalEpochCannotMaskReconnectOrMutation();TestBrokerProtectiveMetadataIsOnlyVisibleCandidateNotProtection();TestActualAdapterLockIncludesConnectionAndStreamInBrokerAudit();TestAdapterForwardsBoundedHandshakeBudget();TestReconnectAndIncompleteBootstrap();
    std::puts("ib_live_terminal_reconciliation_tests PASS");
}
