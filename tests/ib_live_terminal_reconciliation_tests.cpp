#include "adapter_ib/ib_gateway_adapter.h"
#include "adapter_ib/ib_venue_correlation.h"
#include "execution/execution_coordinator.h"
#include "execution/ib_bracket_h1_oms_reservation.h"
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
    mutable long nextValidOrderId=41;
    mutable long jumpFloorAfterNextRead=0; // deterministic mock of EReader callback
    std::string submittedTif;
    IBOrderLite submitted;IBContractLite contract;long orderId=-1;
    std::deque<IBEvent> events;
    bool Connect(const IBConnectParams& p) override {connectBudgetMs=p.handshakeTimeoutMs;connected=true;return true;}
    void SetConnectionEpoch(std::uint64_t value) override {epoch=value;}
    std::uint64_t GetConnectionEpoch() const override {return epoch;}
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
    long GetLastValidOrderId() const override {
        // Test an asynchronous Broker callback raising the native floor
        // BETWEEN the research reservation's first and second reads.
        const long before=nextValidOrderId;
        if(jumpFloorAfterNextRead!=0) {
            nextValidOrderId=jumpFloorAfterNextRead;
            jumpFloorAfterNextRead=0;
        }
        return before;
    }
};
struct Fixture {
    Broker* broker;HeptaIBGatewayAdapter adapter;
    Fixture(bool finishBootstrap=true, bool cryptoScope=false, bool cashScope=false,
            const std::string& observabilityPath=std::string(),
            bool requireNextValidId=true):broker(new Broker()),adapter(
        std::unique_ptr<IIBApiWrapper>(broker), [this]() {
            broker=new Broker();return std::unique_ptr<IIBApiWrapper>(broker);
        }) {
        HeptaIBConfig cfg;cfg.account="DU123";cfg.clientId=73;cfg.readOnly=false;
        cfg.observabilityLogPath=observabilityPath;
        cfg.risk.enableOrderSubmission=true;cfg.risk.maxDailyOrders=100;
        cfg.risk.duplicateOrderWindowSec=0;cfg.risk.maxPriceDeviationBps=0;
        cfg.risk.requireNextValidId=requireNextValidId;
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
void TestNativeOrderIdOutOfInt32RangeCannotReachFakeBroker() {
    Fixture f;
    f.broker->nextValidOrderId = 2147483648L; // exceeds TWS OrderId:int32
    IBContractLite c;c.symbol="EUR";c.secType="CASH";
    c.exchange="IDEALPRO";c.currency="USD";
    IBOrderLite o;o.action="BUY";o.orderType="LMT";
    o.lmtPrice=1.18;o.totalQuantity=1;
    long received=-1;
    const bool admitted=f.adapter.PlaceOrderCorrelated(
        c,o,"hepta-v1-sha256:"+std::string(64,'f'),&received);
    if(admitted || f.broker->places!=0 ||
       f.adapter.GetLastRejectReason()!="IB_NATIVE_ORDER_ID_OUT_OF_RANGE")
        throw std::runtime_error("IB_NATIVE_ORDER_ID_EXCEEDS_32BIT_BROKER_RANGE");
    // Even when the old optional pre-risk flag is disabled, the native
    // allocator may not mint arbitrary ID 1 without a real Broker floor.
    Fixture noBrokerFloor(true,false,false,std::string(),false);
    noBrokerFloor.broker->nextValidOrderId=0;
    long missing=-1;
    const bool madeUp=noBrokerFloor.adapter.PlaceOrderCorrelated(
        c,o,"hepta-v1-sha256:"+std::string(64,'d'),&missing);
    if(madeUp || noBrokerFloor.broker->places!=0 ||
       noBrokerFloor.adapter.GetLastRejectReason()!=
           "IB_NATIVE_ORDER_ID_OUT_OF_RANGE")
        throw std::runtime_error("ORDER_ID_MINTED_WITHOUT_NEXT_VALID_CALLBACK");
    Fixture nearLimit;
    nearLimit.broker->nextValidOrderId=2147483647L;
    const long last=nearLimit.Place();
    if(last!=2147483647L || nearLimit.broker->places!=1)
        throw std::runtime_error("NATIVE_ORDER_ID_MAX_VALID_VALUE_REFUSED");
    long later=-1;
    const bool replay=nearLimit.adapter.PlaceOrderCorrelated(
        c,o,"hepta-v1-sha256:"+std::string(64,'b'),&later);
    if(replay || nearLimit.broker->places!=1 ||
       nearLimit.adapter.GetLastRejectReason()!="IB_NATIVE_ORDER_ID_OUT_OF_RANGE")
        throw std::runtime_error("NATIVE_ORDER_ID_EXHAUSTION_ALLOWED_SECOND_SEND");
}
void TestBrokerFloorRaisesAfterLocalIdAllocationCannotReachSend() {
    Fixture f;
    IBContractLite contract;
    contract.symbol="EUR"; contract.secType="CASH";
    contract.exchange="IDEALPRO"; contract.currency="USD";
    IBOrderLite order;
    order.action="BUY"; order.orderType="LMT";
    order.lmtPrice=1.18; order.totalQuantity=1;
    bool finalCheckWasCalled=false;
    f.adapter.SetPrePlaceOrderSendCheck(
        [&](const IBFinalOrderSendContext*, const IBContractLite&,
            const IBOrderLite&, std::string*) {
            finalCheckWasCalled=true;
            // New native nextValidId or an observed foreign openOrder can
            // raise the floor after adapter allocated the earlier local ID.
            f.broker->nextValidOrderId=900;
            return true;
        });
    long assigned=-1;
    const bool sent=f.adapter.PlaceOrderCorrelated(contract,order,
        "hepta-v1-sha256:"+std::string(64,'e'),&assigned);
    if(!finalCheckWasCalled || sent || f.broker->places!=0 ||
       f.adapter.GetLastRejectReason()!=
           "IB_NATIVE_ORDER_ID_STALE_BEFORE_SEND")
        throw std::runtime_error("FOREIGN_BROKER_ORDER_FLOOR_OVERRAN_RESERVED_ID");
}
void TestUnsentH1PairCannotReturnOvertakenNativeBrokerIds() {
    // Model an asynchronous EReader floor change immediately after the
    // initial SDK floor read, before the unsent ID pair is published.
    // An SDK connection losing readiness is also a refused reservation.
    for (const long observed : {-1L, 42L, 43L, 900L}) {
        Fixture f;
        const auto epoch=f.adapter.GetConnectionEpoch();
        IBResearchH1LocalIdPair pair;
        std::string reason;
        f.broker->jumpFloorAfterNextRead=observed;
        const bool granted=f.adapter.ReserveResearchUnsentH1LocalIdPair(
            "DU123",73,epoch,pair,reason);
        if(granted || pair.parentOrderId!=-1 || f.broker->places!=0 ||
           reason!="IB_H1_LOCAL_PAIR_BROKER_FLOOR_CHANGED_BEFORE_RETURN")
            throw std::runtime_error(
                "H1_UNSENT_PAIR_RETURNED_STALE_ID_AFTER_BROKER_FLOOR_JUMP");
        if(observed<0) f.broker->nextValidOrderId=900;
        const long expected=observed<0 ? 900 :
                            (observed<=43 ? 43 : observed);
        if(!f.adapter.ReserveResearchUnsentH1LocalIdPair(
                "DU123",73,epoch,pair,reason) ||
           pair.parentOrderId!=expected ||
           pair.childOrderId!=expected+1 ||
           pair.sendAuthorized || pair.brokerWorkingStopProven ||
           f.broker->places!=0)
            throw std::runtime_error("H1_LATE_BROKER_FLOOR_RETRY_REUSED_ID");
    }
}
void TestResearchH1PairConsumesOnlyNativeAdapterIdsWithoutBrokerEgress() {
    Fixture f;
    const auto epoch = f.adapter.GetConnectionEpoch();
    IBResearchH1LocalIdPair pair;
    std::string reason;
    auto reserve=[&](const std::string& account, int clientId,
                     std::uint64_t connectionEpoch) {
        return f.adapter.ReserveResearchUnsentH1LocalIdPair(
            account,clientId,connectionEpoch,pair,reason);
    };
    if (reserve("DU123",73,0) || pair.parentOrderId!=-1 ||
        reserve("DU123",74,epoch) || pair.parentOrderId!=-1 ||
        reserve("DU-FOREIGN",73,epoch) || pair.parentOrderId!=-1)
        throw std::runtime_error("H1_LOCAL_PAIR_ACCEPTED_FOREIGN_OWNER_OR_EPOCH");
    if (!reserve("DU123",73,epoch) || pair.parentOrderId!=41 ||
        pair.childOrderId!=42 || pair.brokerClientId!=73 ||
        pair.connectionEpoch!=epoch || pair.paperAccount!="DU123" ||
        pair.sendAuthorized || pair.brokerWorkingStopProven ||
        f.broker->places!=0)
        throw std::runtime_error("H1_LOCAL_PAIR_MINTED_NATIVE_SEND_AUTHORITY");
    if (!reserve("DU123",73,epoch) || pair.parentOrderId!=43 ||
        pair.childOrderId!=44 || f.broker->places!=0)
        throw std::runtime_error("H1_LOCAL_PAIR_REUSED_ALREADY_CONSUMED_ID");
    f.broker->nextValidOrderId=900;
    if (!reserve("DU123",73,epoch) || pair.parentOrderId!=900 ||
        pair.childOrderId!=901 || f.broker->places!=0)
        throw std::runtime_error("H1_LOCAL_PAIR_IGNORED_RAISED_BROKER_FLOOR");
    f.broker->nextValidOrderId=0;
    if (reserve("DU123",73,epoch) || pair.parentOrderId!=-1 ||
        f.broker->places!=0)
        throw std::runtime_error("H1_LOCAL_PAIR_MINTED_ID_WITHOUT_NEXTVALID");
    f.broker->nextValidOrderId=902;
    if (!reserve("DU123",73,epoch) || pair.parentOrderId!=902 ||
        pair.childOrderId!=903)
        throw std::runtime_error("H1_LOCAL_PAIR_FAILED_RECOVERY_AFTER_MISSING_FLOOR");
    f.adapter.Disconnect();
    if (reserve("DU123",73,epoch) || pair.parentOrderId!=-1 ||
        f.broker->places!=0)
        throw std::runtime_error("H1_LOCAL_PAIR_BYPASSED_DISCONNECTED_OWNER");
    // The same local cursor must be shared with existing ordinary single-ID
    // submits. The only Place() here is the isolated mock Broker, not IBKR.
    Fixture mixed;
    const auto mixEpoch=mixed.adapter.GetConnectionEpoch();
    if (!mixed.adapter.ReserveResearchUnsentH1LocalIdPair(
            "DU123",73,mixEpoch,pair,reason) || pair.parentOrderId!=41 ||
        pair.childOrderId!=42 || mixed.broker->places!=0)
        throw std::runtime_error("H1_LOCAL_PAIR_MIXED_SOURCE_PREPARE_FAILED");
    const long ordinaryId=mixed.Place("hepta-v1-sha256:"+std::string(64,'c'));
    if (ordinaryId!=43 || mixed.broker->places!=1 ||
        !mixed.adapter.ReserveResearchUnsentH1LocalIdPair(
            "DU123",73,mixEpoch,pair,reason) ||
        pair.parentOrderId!=44 || pair.childOrderId!=45 ||
        mixed.broker->places!=1)
        throw std::runtime_error("H1_LOCAL_PAIR_COLLIDED_WITH_REGULAR_ORDER_ID");
    // Reconnect invalidates any old Broker epoch. The adapter may reserve
    // local IDs only after the fresh wrapper reports its new nextValidId.
    Fixture reconnecting;
    const auto oldEpoch=reconnecting.adapter.GetConnectionEpoch();
    if (!reconnecting.adapter.ReserveResearchUnsentH1LocalIdPair(
            "DU123",73,oldEpoch,pair,reason))
        throw std::runtime_error("H1_LOCAL_PAIR_BEFORE_RECONNECT_INVALID");
    reconnecting.adapter.Disconnect();
    if (!reconnecting.adapter.Connect() ||
        reconnecting.adapter.GetConnectionEpoch()<=oldEpoch)
        throw std::runtime_error("H1_LOCAL_PAIR_NEW_EPOCH_UNAVAILABLE");
    if (reconnecting.adapter.ReserveResearchUnsentH1LocalIdPair(
            "DU123",73,oldEpoch,pair,reason) || pair.parentOrderId!=-1)
        throw std::runtime_error("H1_LOCAL_PAIR_STALE_EPOCH_GRANTED");
    const auto newEpoch=reconnecting.adapter.GetConnectionEpoch();
    if (!reconnecting.adapter.ReserveResearchUnsentH1LocalIdPair(
            "DU123",73,newEpoch,pair,reason) ||
        pair.parentOrderId!=43 || pair.childOrderId!=44 ||
        reconnecting.broker->places!=0)
        throw std::runtime_error("H1_LOCAL_PAIR_NEW_EPOCH_REUSED_OLD_ID");
    // Read-only connected client cannot claim native mutation reservations.
    Broker* readOnlyBroker=new Broker();
    HeptaIBGatewayAdapter readOnlyAdapter{
        std::unique_ptr<IIBApiWrapper>(readOnlyBroker)};
    HeptaIBConfig readCfg;readCfg.account="DU123";readCfg.clientId=73;
    readCfg.readOnly=true;
    if (!readOnlyAdapter.Init(readCfg) || !readOnlyAdapter.Connect() ||
        readOnlyAdapter.ReserveResearchUnsentH1LocalIdPair(
            "DU123",73,readOnlyAdapter.GetConnectionEpoch(),pair,reason) ||
        pair.parentOrderId!=-1 || readOnlyBroker->places!=0)
        throw std::runtime_error("H1_LOCAL_PAIR_READ_ONLY_CLIENT_GRANTED");

    Fixture exhausted;
    auto exEpoch=exhausted.adapter.GetConnectionEpoch();
    exhausted.broker->nextValidOrderId=2147483646L;
    if (!exhausted.adapter.ReserveResearchUnsentH1LocalIdPair(
            "DU123",73,exEpoch,pair,reason) ||
        pair.parentOrderId!=2147483646L || pair.childOrderId!=2147483647L ||
        exhausted.broker->places!=0)
        throw std::runtime_error("H1_LOCAL_PAIR_REJECTED_LAST_VALID_SIGNED_IDS");
    if (exhausted.adapter.ReserveResearchUnsentH1LocalIdPair(
            "DU123",73,exEpoch,pair,reason) || pair.parentOrderId!=-1 ||
        exhausted.broker->places!=0)
        throw std::runtime_error("H1_LOCAL_PAIR_OVERFLOWED_SIGNED_TWS_ID");
    // A pair may legally consume MAX-1 and MAX. The next ordinary send
    // must refuse, not wrap the local cursor to a positive Broker order ID.
    IBContractLite exhaustedContract;
    exhaustedContract.symbol="EUR";
    exhaustedContract.secType="CASH";
    exhaustedContract.exchange="IDEALPRO";
    exhaustedContract.currency="USD";
    IBOrderLite exhaustedOrder;
    exhaustedOrder.action="BUY";
    exhaustedOrder.orderType="LMT";
    exhaustedOrder.lmtPrice=1.18;
    exhaustedOrder.totalQuantity=1;
    long exhaustedAssigned=-1;
    if (exhausted.adapter.PlaceOrderCorrelated(
            exhaustedContract, exhaustedOrder,
            "hepta-v1-sha256:"+std::string(64,'f'),
            &exhaustedAssigned) ||
        exhaustedAssigned>0 || exhausted.broker->places!=0 ||
        exhausted.adapter.GetLastRejectReason()!=
            "IB_NATIVE_ORDER_ID_OUT_OF_RANGE")
        throw std::runtime_error("H1_LOCAL_PAIR_EXHAUSTION_ALLOWED_ORDINARY_SEND");
}

void TestOwnerBoundAdapterNativePairPersistsToSameOriginalOms() {
    using namespace hepta_bracket_h1;
    // This is a wholly in-process Broker mock plus a private temporary OMS.
    // Even a successful pair reservation can NEVER send a real IB order.
    Fixture fixture;
    OmsJournal journal;
    std::string templ="/tmp/Hepta-H1-LocalOwnerHandoff-XXXXXX";
    std::vector<char> path(templ.begin(),templ.end());path.push_back('\0');
    int fd=::mkstemp(path.data());
    if(fd<0)throw std::runtime_error("H1_HANDOFF_SCRATCH_CREATE_FAILED");
    ::close(fd);::unlink(path.data());
    if(!journal.Init(path.data()))
        throw std::runtime_error("H1_HANDOFF_SCRATCH_OMS_INIT_FAILED");
    ExecutionCoordinatorCallbacks callbacks;
    ExecutionCoordinator owner(journal,callbacks);
    AgentExecutionContext ctx;
    ctx.agentId="original-owner";ctx.sessionId="owner-session";
    ctx.toolCallId="original-h1-entry-001";
    ctx.executionDomain="IB-PAPER:EUR.USD";
    FrozenBracket f;
    f.originalCommandKey=ctx.toolCallId;f.account="DU123";
    f.owner=ctx.agentId;f.sessionId=ctx.sessionId;
    f.executionDomain="PAPER";f.side="BUY";
    f.contractId=12087792;f.clientId=73;
    f.parentOrderId=0;f.childOrderId=0;
    f.connectionEpoch=fixture.adapter.GetConnectionEpoch();
    f.positionGeneration=91;f.ownerFenceToken=31;
    f.intentObservedAtMs=1792080000123ULL;
    f.quantity=89200;f.referencePrice=1.1205;
    f.stopPrice=1.11825;f.minTick=0.00005;
    std::string reason;
    hepta_bracket_h1::Reservation persisted;
    int allocations=0;
    ExecutionCoordinator::H1UnsentNativeIdAllocator nativeIds =
        [&](const std::string& account,int clientId,std::uint64_t epoch,
            long& parent,long& child,std::string& error)->bool {
        ++allocations;
        IBResearchH1LocalIdPair candidate;
        if(!fixture.adapter.ReserveResearchUnsentH1LocalIdPair(
                account,clientId,epoch,candidate,error))return false;
        if(candidate.sendAuthorized || candidate.brokerWorkingStopProven ||
           candidate.paperAccount!=account ||
           candidate.brokerClientId!=clientId ||
           candidate.connectionEpoch!=epoch)return false;
        parent=candidate.parentOrderId;child=candidate.childOrderId;
        return true;
    };
    if(owner.ReserveResearchH1BracketFromAdapterForRecoveryOnlyOwner(
            ctx,f,nativeIds,persisted,reason) || allocations!=0 ||
       fixture.broker->places!=0)
        throw std::runtime_error("H1_HANDOFF_MISSING_OWNER_FENCE_ALLOCATED_IDS");
    if(!owner.EnterRecoveryOnlyOwner(ctx.agentId,ctx.sessionId,31,reason))
        throw std::runtime_error("H1_HANDOFF_OWNER_RECOVERY_FENCE_FAILED");
    auto preselected=f;preselected.parentOrderId=9000;preselected.childOrderId=9001;
    if(owner.ReserveResearchH1BracketFromAdapterForRecoveryOnlyOwner(
            ctx,preselected,nativeIds,persisted,reason) || allocations!=0)
        throw std::runtime_error("H1_HANDOFF_CALLER_PRESELECTED_NATIVE_IDS");
    if(!owner.ReserveResearchH1BracketFromAdapterForRecoveryOnlyOwner(
            ctx,f,nativeIds,persisted,reason) || allocations!=1 ||
       persisted.frozen.parentOrderId!=41 ||
       persisted.frozen.childOrderId!=42 ||
       !persisted.replayComplete || persisted.sendAuthorized ||
       persisted.driverStopAllowed || fixture.broker->places!=0)
        throw std::runtime_error("H1_HANDOFF_NATIVE_ID_TO_DURABLE_OMS_MISMATCH");
    std::vector<OmsJournalEvent> entries;
    if(journal.Replay([&](const OmsJournalEvent& e){entries.push_back(e);})!=3)
        throw std::runtime_error("H1_HANDOFF_DURABLE_OMS_PAIR_NOT_EXACT");
    hepta_bracket_h1::Reservation checked;
    if(!ConfirmReplay(entries,persisted.frozen,checked,reason) ||
       checked.ids.childRef!=persisted.ids.childRef)
        throw std::runtime_error("H1_HANDOFF_RESTART_CANNOT_BIND_ORIGINAL_PAIR");
    if(owner.ReserveResearchH1BracketFromAdapterForRecoveryOnlyOwner(
            ctx,f,nativeIds,persisted,reason) || allocations!=1 ||
       fixture.broker->places!=0)
        throw std::runtime_error("H1_HANDOFF_DUPLICATE_BURNED_MORE_NATIVE_IDS");
    // If the original Broker callback floor temporarily disappears, the
    // next original command must leave NO prefix and consume NO local IDs.
    auto secondCtx=ctx;secondCtx.toolCallId="original-h1-entry-002";
    auto second=f;second.originalCommandKey=secondCtx.toolCallId;
    fixture.broker->nextValidOrderId=0;
    if(owner.ReserveResearchH1BracketFromAdapterForRecoveryOnlyOwner(
            secondCtx,second,nativeIds,persisted,reason) ||
       allocations!=2 || persisted.replayComplete ||
       journal.Replay([](const OmsJournalEvent&) {})!=3)
        throw std::runtime_error("H1_HANDOFF_NO_NEXTVALID_PERSISTED_FALSE_PAIR");
    fixture.broker->nextValidOrderId=43;
    if(!owner.ReserveResearchH1BracketFromAdapterForRecoveryOnlyOwner(
            secondCtx,second,nativeIds,persisted,reason) ||
       allocations!=3 || persisted.frozen.parentOrderId!=43 ||
       persisted.frozen.childOrderId!=44 || fixture.broker->places!=0 ||
       journal.Replay([](const OmsJournalEvent&) {})!=5)
        throw std::runtime_error("H1_HANDOFF_BROKER_FLOOR_RETURN_LOST_ID_CURSOR");
    // New Adapter process, same Mock Broker nextValidId=41: the Broker has
    // never seen the UNSENT OMS reservations 41..44. Without recovery-floor
    // adoption an unrelated ordinary order will reuse durable H1 numeric IDs.
    Fixture freshAdapterAfterProcessRestart;
    std::int64_t replayedH1Floor=0;
    if(!owner.GetDurableResearchH1NativeIdFloor(
            "DU123",replayedH1Floor,reason) || replayedH1Floor!=45)
        throw std::runtime_error("H1_RESTART_OMS_DURABLE_NATIVE_FLOOR_MISSING");
    // Native order IDs are Broker CLIENT-scoped, not per DU account.
    // Reconfigure the SAME client 73 to another PAPER account using a new
    // in-process mock adapter. It may not reuse any locally reserved ID 41..44.
    std::int64_t changedDuFloor=0;
    if(!owner.GetDurableResearchH1NativeIdFloor(
            "DU-ANOTHER",changedDuFloor,reason) || changedDuFloor!=45)
        throw std::runtime_error("H1_CROSS_ACCOUNT_BROKER_CLIENT_FLOOR_REUSED");
    Broker* changedBroker=new Broker();
    HeptaIBGatewayAdapter changedAdapter{
        std::unique_ptr<IIBApiWrapper>(changedBroker)};
    HeptaIBConfig changedCfg;
    changedCfg.account="DU-ANOTHER";
    changedCfg.clientId=73;
    changedCfg.readOnly=false;
    if(!changedAdapter.Init(changedCfg) || !changedAdapter.Connect())
        throw std::runtime_error("H1_CROSS_ACCOUNT_MOCK_ADAPTER_SETUP_FAILED");
    const auto changedEpoch=changedAdapter.GetConnectionEpoch();
    if(!changedAdapter.AdoptDurableResearchH1NativeIdFloor(
            "DU-ANOTHER",73,changedEpoch,changedDuFloor,reason))
        throw std::runtime_error("H1_CROSS_ACCOUNT_DURABLE_ID_FLOOR_NOT_ADOPTED");
    IBResearchH1LocalIdPair crossAccountPair;
    if(!changedAdapter.ReserveResearchUnsentH1LocalIdPair(
            "DU-ANOTHER",73,changedEpoch,crossAccountPair,reason) ||
       crossAccountPair.parentOrderId!=45 ||
       crossAccountPair.childOrderId!=46 ||
       crossAccountPair.sendAuthorized || changedBroker->places!=0)
        throw std::runtime_error("H1_CROSS_ACCOUNT_NATIVE_ID_PAIR_REUSED_OR_SENT");
    if(freshAdapterAfterProcessRestart.adapter.AdoptDurableResearchH1NativeIdFloor(
            "DU-FOREIGN",73,freshAdapterAfterProcessRestart.adapter.GetConnectionEpoch(),
            replayedH1Floor,reason) ||
       freshAdapterAfterProcessRestart.adapter.AdoptDurableResearchH1NativeIdFloor(
            "DU123",73,freshAdapterAfterProcessRestart.adapter.GetConnectionEpoch()+1,
            replayedH1Floor,reason) ||
       freshAdapterAfterProcessRestart.adapter.AdoptDurableResearchH1NativeIdFloor(
            "DU123",74,freshAdapterAfterProcessRestart.adapter.GetConnectionEpoch(),
            replayedH1Floor,reason) ||
       !freshAdapterAfterProcessRestart.adapter.AdoptDurableResearchH1NativeIdFloor(
            "DU123",73,freshAdapterAfterProcessRestart.adapter.GetConnectionEpoch(),
            replayedH1Floor,reason))
        throw std::runtime_error("H1_RESTART_NATIVE_FLOOR_BROKER_SCOPE_INVALID");
    const long ordinaryAfterRestart=freshAdapterAfterProcessRestart.Place(
        "hepta-v1-sha256:"+std::string(64,'8'));
    if(ordinaryAfterRestart<=44)
        throw std::runtime_error("OMS_RESERVED_H1_NATIVE_IDS_REUSED_AFTER_RESTART");
    // A crash can leave ONLY the parent reservation durably written.
    // Even that prefix burns its native ID until authoritative reconciliation.
    std::string partialMask="/tmp/Hepta-H1-PartialFloor-XXXXXX";
    std::vector<char> partialPath(partialMask.begin(),partialMask.end());
    partialPath.push_back('\0');
    const int partialFd=::mkstemp(partialPath.data());
    if(partialFd<0)throw std::runtime_error("H1_PARTIAL_FLOOR_SCRATCH_FAILED");
    ::close(partialFd);::unlink(partialPath.data());
    {
        OmsJournal partial;
        if(!partial.Init(partialPath.data()) ||
           !partial.AppendDurableCritical(hepta_bracket_h1::Intent(
               persisted.frozen,persisted.ids,false)))
            throw std::runtime_error("H1_PARTIAL_PARENT_NOT_DURABLE");
        ExecutionCoordinator partialOwner(partial,callbacks);
        std::int64_t partialFloor=0;
        if(!partialOwner.GetDurableResearchH1NativeIdFloor(
               "DU123",partialFloor,reason) ||
           partialFloor!=persisted.frozen.parentOrderId+1)
            throw std::runtime_error("H1_PARTIAL_PARENT_ID_RECOVERY_WAS_LOST");
        auto corrupted=hepta_bracket_h1::Intent(
            persisted.frozen,persisted.ids,false);
        corrupted.source="UNTRUSTED_BROKER_PROOF";
        if(!partial.AppendDurableCritical(corrupted) ||
           partialOwner.GetDurableResearchH1NativeIdFloor(
               "DU123",partialFloor,reason) ||
           reason!="H1_RECOVERED_NATIVE_ID_EVENT_INVALID")
            throw std::runtime_error("H1_MALFORMED_ORIGINAL_PREFIX_MINTED_NATIVE_IDS");
    }
    if(::unlink(partialPath.data())!=0)
        throw std::runtime_error("H1_PARTIAL_FLOOR_CLEANUP_FAILED");
    // The pre-send durable group must not mint another pair on Coordinator
    // restart even if the caller still has a valid nextValidId callback.
    {
        ExecutionCoordinator recovered(journal,callbacks);
        if(!recovered.RecoverFromJournal(reason))
            throw std::runtime_error("H1_HANDOFF_RESTART_RECOVERY_FAILED");
        auto old=f;
        if(recovered.ReserveResearchH1BracketFromAdapterForRecoveryOnlyOwner(
                ctx,old,nativeIds,persisted,reason) || allocations!=3 ||
           fixture.broker->places!=0)
            throw std::runtime_error("H1_HANDOFF_RESTART_CONSUMED_DUPLICATE_IDS");
    }
    if(::unlink(path.data())!=0)
        throw std::runtime_error("H1_HANDOFF_SCRATCH_OMS_UNLINK_FAILED");
}

void TestOwnerDurableOcaExitUsesActualLocalAdapterCursorWithoutSending() {
    // Real Adapter + Coordinator/OMS classes, in-process fake IB wrapper.
    // The Broker echo below is an explicitly SYNTHETIC positive fixture.
    Fixture fixture;
    const std::uint64_t epoch=fixture.adapter.GetConnectionEpoch();
    std::string filename="/tmp/Hepta-H1-Owner-Exit-XXXXXX";
    std::vector<char> path(filename.begin(),filename.end());path.push_back('\0');
    const int fd=::mkstemp(path.data());
    if(fd<0)throw std::runtime_error("H1_OWNER_EXIT_OMS_SCRATCH_FAILED");
    ::close(fd);::unlink(path.data());
    OmsJournal journal;
    if(!journal.Init(path.data()))
        throw std::runtime_error("H1_OWNER_EXIT_OMS_INIT_FAILED");
    ExecutionCoordinatorCallbacks callbacks;
    ExecutionCoordinator coordinator(journal,callbacks);
    AgentExecutionContext owner;
    owner.agentId="original-owner";owner.sessionId="h1-exit-session";
    owner.toolCallId="native-h1-exit-original-001";
    owner.executionDomain="IB-PAPER:EUR.USD";
    hepta_bracket_h1::FrozenBracket f;
    f.originalCommandKey=owner.toolCallId;
    f.account="DU123";f.owner=owner.agentId;f.sessionId=owner.sessionId;
    f.executionDomain="PAPER";f.side="BUY";
    f.contractId=12087792;f.clientId=73;
    f.connectionEpoch=epoch;f.positionGeneration=91;
    f.ownerFenceToken=31;f.intentObservedAtMs=1792080000123ULL;
    f.quantity=89200;f.referencePrice=1.1205;
    f.stopPrice=1.11825;f.minTick=.00005;
    std::string reason;
    if(!coordinator.EnterRecoveryOnlyOwner(
            owner.agentId,owner.sessionId,f.ownerFenceToken,reason))
        throw std::runtime_error("H1_OWNER_EXIT_RECOVERY_FENCE_MISSING");
    const ExecutionCoordinator::H1UnsentNativeIdAllocator pairAllocator=
        [&](const std::string& account,int clientId,std::uint64_t generation,
            long& parent,long& child,std::string& error)->bool {
        IBResearchH1LocalIdPair pair;
        if(!fixture.adapter.ReserveResearchUnsentH1LocalIdPair(
                account,clientId,generation,pair,error))return false;
        if(pair.sendAuthorized || pair.brokerWorkingStopProven ||
           pair.paperAccount!=account || pair.brokerClientId!=clientId ||
           pair.connectionEpoch!=generation)return false;
        parent=pair.parentOrderId;child=pair.childOrderId;return true;
    };
    hepta_bracket_h1::Reservation pair;
    if(!coordinator.ReserveResearchH1BracketFromAdapterForRecoveryOnlyOwner(
            owner,f,pairAllocator,pair,reason) ||
       pair.frozen.parentOrderId!=41 || pair.frozen.childOrderId!=42 ||
       pair.sendAuthorized || fixture.broker->places!=0)
        throw std::runtime_error("H1_OWNER_EXIT_LOCAL_PARENT_CHILD_IDS_NOT_DURABLE");
    f=pair.frozen;
    if(!hepta_bracket_h1::MarkPotentialSend(journal,pair,false,reason) ||
       !hepta_bracket_h1::MarkPotentialSend(journal,pair,true,reason))
        throw std::runtime_error("H1_OWNER_EXIT_UNSENT_PRESEND_MARKERS_MISSING");
    OmsJournalEvent synthetic=hepta_bracket_h1::Intent(f,pair.ids,true);
    synthetic.eventType="broker_open_order_bracket";
    synthetic.source="agent:"+f.owner;
    synthetic.brokerClientId=f.clientId;
    synthetic.brokerConnectionEpoch=f.connectionEpoch;
    synthetic.brokerCallbackType="openOrder";
    synthetic.status="Submitted";
    synthetic.eventId=pair.ids.childRef+"-"+
        std::to_string(f.connectionEpoch)+"-88333";
    auto& bracket=synthetic.brokerBracket;
    bracket.version=2;bracket.contractId=f.contractId;
    bracket.parentOrderId=f.parentOrderId;
    bracket.permanentOrderId=88333;
    bracket.transmit=1;bracket.ocaType=1;bracket.triggerMethod=0;
    bracket.orderRef=pair.ids.childRef;
    bracket.ocaGroup=pair.ids.ocaGroup;
    bracket.timeInForce="GTC";
    bracket.quantity=f.quantity;bracket.stopAuxPrice=f.stopPrice;
    if(!bracket.ValidShape() || !journal.AppendDurableCritical(synthetic))
        throw std::runtime_error("H1_OWNER_EXIT_SYNTHETIC_OMS_ECHO_FAILED");
    int exitAllocations=0;
    const ExecutionCoordinator::H1UnsentNativeOcaExitAllocator exitAllocator=
        [&](const std::string& account,int clientId,std::uint64_t generation,
            long stop,ExecutionCoordinator::H1LocalOcaExitReceipt& out,
            std::string& error)->bool {
        ++exitAllocations;
        IBResearchH1LocalExitId candidate;
        if(!fixture.adapter.ReserveResearchUnsentH1LocalOcaExitId(
                account,clientId,generation,stop,candidate,error))return false;
        out.orderId=candidate.orderId;
        out.stopOrderId=candidate.stopOrderId;
        out.brokerClientId=candidate.brokerClientId;
        out.connectionEpoch=candidate.connectionEpoch;
        out.paperAccount=candidate.paperAccount;
        out.sendAuthorized=candidate.sendAuthorized;
        out.maintenanceAuthorized=candidate.maintenanceAuthorized;
        return true;
    };
    ExecutionCoordinator::H1LocalOcaExitReceipt recorded;
    std::string exitCorrelation;
    if(!coordinator.ReserveResearchH1OcaExitForRecoveryOnlyOwner(
            owner,f,epoch,88333,exitAllocator,
            recorded,exitCorrelation,reason) ||
       exitAllocations!=1 || recorded.orderId!=43 ||
       recorded.sendAuthorized || recorded.maintenanceAuthorized ||
       fixture.broker->places!=0)
        throw std::runtime_error("H1_OWNER_EXIT_ADAPTER_ID_NOT_DURABLY_BOUND");
    // A local UNSENT OCA ID receipt may not impersonate a Broker response.
    // Replaying it with fabricated broker evidence must invalidate the
    // original H1 stop's native attestation, not mint a usable EXIT state.
    std::vector<OmsJournalEvent> ocaEvents;
    if(journal.Replay([&](const OmsJournalEvent& e){ocaEvents.push_back(e);})!=7)
        throw std::runtime_error("H1_OCA_EXPECTED_SEVEN_OWNER_AND_ORIGINAL_RECORDS");
    hepta_bracket_h1::Reservation replayedStop;
    if(!hepta_bracket_h1::ConfirmReplayForOriginalNativeEcho(
            ocaEvents,f,replayedStop,reason))
        throw std::runtime_error("H1_OCA_CLEAN_REPLAY_NOT_ATTESTED");
    auto forgedOca=ocaEvents;
    bool modified=false;
    for(auto& item:forgedOca) {
        if(item.eventType=="bracket_oca_exit_reserved") {
            item.reason="BROKER_STOP_WORKING_FAKE";
            modified=true;
        }
    }
    if(!modified || hepta_bracket_h1::ConfirmReplayForOriginalNativeEcho(
            forgedOca,f,replayedStop,reason) || !replayedStop.uncertain)
        throw std::runtime_error("H1_OCA_FORGED_BROKER_REASON_ACCEPTED");
    forgedOca=ocaEvents;
    for(auto& item:forgedOca)
        if(item.eventType=="bracket_oca_exit_reserved") {
            item.brokerErrorCode=201;
            item.brokerMessage="BROKER_REJECTED_FAKE";
        }
    if(hepta_bracket_h1::ConfirmReplayForOriginalNativeEcho(
            forgedOca,f,replayedStop,reason) || !replayedStop.uncertain)
        throw std::runtime_error("H1_OCA_FORGED_BROKER_ERROR_ACCEPTED");
    std::int64_t floor=0;
    if(!coordinator.GetDurableResearchH1NativeIdFloor(
            "DU-SECOND",floor,reason) || floor!=44)
        throw std::runtime_error("H1_OWNER_EXIT_DURABLE_ID_FLOOR_LOST");
    if(coordinator.ReserveResearchH1OcaExitForRecoveryOnlyOwner(
            owner,f,epoch,88333,exitAllocator,
            recorded,exitCorrelation,reason) || exitAllocations!=1)
        throw std::runtime_error("H1_OWNER_EXIT_DUPLICATE_CONSUMED_ADAPTER_ID");
    Fixture freshAdapter;
    if(!freshAdapter.adapter.AdoptDurableResearchH1NativeIdFloor(
            "DU123",73,freshAdapter.adapter.GetConnectionEpoch(),
            floor,reason))
        throw std::runtime_error("H1_OWNER_EXIT_RESTART_ID_FLOOR_NOT_ADOPTED");
    IBResearchH1LocalIdPair next;
    if(!freshAdapter.adapter.ReserveResearchUnsentH1LocalIdPair(
            "DU123",73,freshAdapter.adapter.GetConnectionEpoch(),next,reason) ||
       next.parentOrderId!=44 || next.childOrderId!=45 ||
       freshAdapter.broker->places!=0)
        throw std::runtime_error("H1_OWNER_EXIT_RESTART_REUSED_LOCAL_EXIT_ID");
    if(::unlink(path.data())!=0)
        throw std::runtime_error("H1_OWNER_EXIT_SCRATCH_CLEANUP_FAILED");
}

void TestResearchH1OcaExitUsesSameNativeBrokerFloorWithoutSending() {
    Fixture fixture;
    std::string reason;
    auto epoch=fixture.adapter.GetConnectionEpoch();
    IBResearchH1LocalIdPair group;
    if(!fixture.adapter.ReserveResearchUnsentH1LocalIdPair(
            "DU123",73,epoch,group,reason) ||
       group.parentOrderId!=41 || group.childOrderId!=42)
        throw std::runtime_error("H1_OCA_INITIAL_NATIVE_GROUP_UNAVAILABLE");
    IBResearchH1LocalExitId exitId;
    auto reserve=[&](const std::string& account,int client,
                     std::uint64_t e,long afterStop) {
        return fixture.adapter.ReserveResearchUnsentH1LocalOcaExitId(
            account,client,e,afterStop,exitId,reason);
    };
    if(reserve("DU123",74,epoch,42) || exitId.orderId!=-1 ||
       reserve("DU-OTHER",73,epoch,42) || exitId.orderId!=-1 ||
       reserve("DU123",73,epoch+1,42) || exitId.orderId!=-1 ||
       reserve("DU123",73,epoch,0) || exitId.orderId!=-1 ||
       reserve("DU123",73,epoch,9000) || exitId.orderId!=-1 ||
       reserve("DU123",73,epoch,2147483647L) || exitId.orderId!=-1)
        throw std::runtime_error("H1_OCA_UNVERIFIED_OWNER_STOP_OR_EPOCH_GOT_ID");
    if(!reserve("DU123",73,epoch,42) || exitId.orderId!=43 ||
       exitId.stopOrderId!=42 || exitId.brokerClientId!=73 ||
       exitId.connectionEpoch!=epoch || exitId.paperAccount!="DU123" ||
       exitId.sendAuthorized || exitId.maintenanceAuthorized ||
       fixture.broker->places!=0)
        throw std::runtime_error("H1_OCA_UNSENT_EXIT_MINTED_BROKER_AUTHORITY");
    // Ordinary next-order ID shares the exact same local cursor.
    const long ordinary=fixture.Place(
        "hepta-v1-sha256:"+std::string(64,'d'));
    if(ordinary!=44 || fixture.broker->places!=1)
        throw std::runtime_error("H1_OCA_EXIT_COLLIDED_WITH_ORDINARY_ID");
    if(!reserve("DU123",73,epoch,42) || exitId.orderId!=45 ||
       fixture.broker->places!=1)
        throw std::runtime_error("H1_OCA_REPEATED_REQUEST_REUSED_NATIVE_ID");
    fixture.broker->jumpFloorAfterNextRead=100;
    if(reserve("DU123",73,epoch,42) || exitId.orderId!=-1 ||
       reason!="IB_H1_LOCAL_OCA_BROKER_FLOOR_CHANGED_BEFORE_RETURN" ||
       fixture.broker->places!=1)
        throw std::runtime_error("H1_OCA_OVERTAKEN_BROKER_FLOOR_RETURNED_STALE_EXIT");
    if(!reserve("DU123",73,epoch,42) || exitId.orderId!=100 ||
       fixture.broker->places!=1)
        throw std::runtime_error("H1_OCA_AFTER_FLOOR_JUMP_REUSED_EXIT_ID");
    fixture.adapter.Disconnect();
    if(reserve("DU123",73,epoch,42) || exitId.orderId!=-1)
        throw std::runtime_error("H1_OCA_OLD_DISCONNECTED_EPOCH_GRANTED_ID");
    // The final signed TWS ID can be consumed exactly once for an UNSENT
    // exit candidate. No wrap, Broker send or automatic identity reissue.
    Fixture nearLimit;
    const auto highEpoch=nearLimit.adapter.GetConnectionEpoch();
    nearLimit.broker->nextValidOrderId=2147483645L;
    IBResearchH1LocalIdPair lastPair;
    if(!nearLimit.adapter.ReserveResearchUnsentH1LocalIdPair(
            "DU123",73,highEpoch,lastPair,reason) ||
       lastPair.parentOrderId!=2147483645L ||
       lastPair.childOrderId!=2147483646L ||
       !nearLimit.adapter.ReserveResearchUnsentH1LocalOcaExitId(
            "DU123",73,highEpoch,lastPair.childOrderId,exitId,reason) ||
       exitId.orderId!=2147483647L ||
       nearLimit.adapter.ReserveResearchUnsentH1LocalOcaExitId(
            "DU123",73,highEpoch,lastPair.childOrderId,exitId,reason) ||
       exitId.orderId!=-1 || nearLimit.broker->places!=0)
        throw std::runtime_error("H1_OCA_SIGNED_NATIVE_ID_OVERFLOW_OR_SEND");
}

void TestDurableH1OcaExitNativeIdFloorAfterRestart() {
    // Isolated OMS fixture. This is NOT a production EXIT marker writer or
    // an actual Broker OCA placement. Prove only that a *future* critically
    // durable exit-reserved event burns its numeric ID after process restart.
    std::string templ="/tmp/Hepta-H1-OCA-Floor-XXXXXX";
    std::vector<char> tmp(templ.begin(),templ.end());tmp.push_back('\0');
    const int fd=::mkstemp(tmp.data());
    if(fd<0) throw std::runtime_error("H1_OCA_FLOOR_SCRATCH_CREATE_FAILED");
    ::close(fd);::unlink(tmp.data());
    const std::string path(tmp.data());
    {
        OmsJournal journal;
        if(!journal.Init(path))
            throw std::runtime_error("H1_OCA_FLOOR_SCRATCH_OMS_FAILED");
        OmsJournalEvent marker;
        marker.eventType="bracket_oca_exit_reserved";
        marker.tsMs=1792140000123LL;
        marker.orderId=1000;
        marker.account="DU123";
        marker.executionDomain="PAPER";
        marker.venue="IB";
        marker.instrument="EUR.USD";
        marker.source="SHADOW_NO_BROKER_EGRESS";
        marker.reqId="original-h1-oca-scratch-001";
        marker.eventId="unsent-original-oca-exit-001";
        marker.requestHash="hepta-v1-sha256:"+std::string(64,'a');
        marker.venueCorrelationId="hepta-v1-sha256:"+std::string(64,'b');
        marker.side="SELL";marker.qty=89200;marker.price=1.11825;
        if(!journal.AppendDurableCritical(marker))
            throw std::runtime_error("H1_OCA_FLOOR_CRITICAL_MARKER_WRITE_FAILED");
        ExecutionCoordinatorCallbacks callbacks;
        ExecutionCoordinator owner(journal,callbacks);
        std::int64_t next=0;std::string reason;
        if(!owner.GetDurableResearchH1NativeIdFloor(
                "DU123",next,reason) || next!=1001)
            throw std::runtime_error("H1_OCA_EXIT_DURABLE_NATIVE_ID_FLOOR_LOST");
        // Broker numeric IDs are client-scoped even after selecting another
        // DU account; the original unsent exit must not be reused.
        if(!owner.GetDurableResearchH1NativeIdFloor(
                "DU-SECOND",next,reason) || next!=1001)
            throw std::runtime_error("H1_OCA_EXIT_DURABLE_CROSS_DU_FLOOR_LOST");
        Fixture restarted;
        const auto epoch=restarted.adapter.GetConnectionEpoch();
        if(!restarted.adapter.AdoptDurableResearchH1NativeIdFloor(
                "DU123",73,epoch,next,reason))
            throw std::runtime_error("H1_OCA_EXIT_RESTART_ADOPT_FAILED");
        IBResearchH1LocalIdPair afterRestart;
        if(!restarted.adapter.ReserveResearchUnsentH1LocalIdPair(
                "DU123",73,epoch,afterRestart,reason) ||
           afterRestart.parentOrderId!=1001 ||
           afterRestart.childOrderId!=1002 || restarted.broker->places!=0)
            throw std::runtime_error("H1_OCA_EXIT_NATIVE_ID_REUSED_AFTER_OMS_RESTART");
        // An invalid write-once EXIT marker is not an observed zero floor.
        // Fail closed, rather than adopt a forged native cursor.
        auto malformed=marker;
        malformed.orderId=9000;
        malformed.eventId.clear();
        if(!journal.AppendDurableCritical(malformed))
            throw std::runtime_error("H1_OCA_MALFORMED_CRITICAL_RECORD_NOT_RETAINED");
        next=0;
        if(owner.GetDurableResearchH1NativeIdFloor(
                "DU123",next,reason) ||
           reason!="H1_RECOVERED_NATIVE_ID_EVENT_INVALID" || next!=1)
            throw std::runtime_error("H1_OCA_MALFORMED_RECORD_MINTED_NATIVE_FLOOR");
    }
    if(::unlink(path.c_str())!=0)
        throw std::runtime_error("H1_OCA_FLOOR_SCRATCH_UNLINK_FAILED");
}

void TestAdapterReinitNeverReusesUnsentH1NativeIds() {
    Fixture fixture;
    IBResearchH1LocalIdPair pair;
    std::string reason;
    auto epoch=fixture.adapter.GetConnectionEpoch();
    if(!fixture.adapter.ReserveResearchUnsentH1LocalIdPair(
            "DU123",73,epoch,pair,reason) ||
       pair.parentOrderId!=41 || pair.childOrderId!=42)
        throw std::runtime_error("H1_REINIT_INITIAL_PAIR_UNAVAILABLE");
    HeptaIBConfig same; same.account="DU123";
    same.clientId=73; same.readOnly=false;
    // A connected reconfiguration must not silently reset the local cursor,
    // broker client, connection identity, or original unsent H1 reservation.
    if(fixture.adapter.Init(same))
        throw std::runtime_error("H1_CONNECTED_INIT_RESET_NATIVE_RESERVATION");
    if(!fixture.adapter.ReserveResearchUnsentH1LocalIdPair(
            "DU123",73,epoch,pair,reason) ||
       pair.parentOrderId!=43 || pair.childOrderId!=44)
        throw std::runtime_error("H1_CONNECTED_INIT_LOST_LOCAL_ID_FLOOR");
    fixture.adapter.Disconnect();
    // The numeric order sequence is Broker-client scoped even if a second
    // DU PAPER account is configured after an explicit disconnect.
    same.account="DU-SECOND";
    if(!fixture.adapter.Init(same) || !fixture.adapter.Connect())
        throw std::runtime_error("H1_DISCONNECTED_RECONFIGURE_RECONNECT_FAILED");
    epoch=fixture.adapter.GetConnectionEpoch();
    if(!fixture.adapter.ReserveResearchUnsentH1LocalIdPair(
            "DU-SECOND",73,epoch,pair,reason) ||
       pair.parentOrderId!=45 || pair.childOrderId!=46 ||
       fixture.broker->places!=0)
        throw std::runtime_error("H1_DISCONNECTED_INIT_REUSED_UNSENT_IDS");
}

int main() {
    TestAdapterReinitNeverReusesUnsentH1NativeIds();
    TestDurableH1OcaExitNativeIdFloorAfterRestart();
    TestOwnerDurableOcaExitUsesActualLocalAdapterCursorWithoutSending();
    TestResearchH1OcaExitUsesSameNativeBrokerFloorWithoutSending();
    TestOwnerBoundAdapterNativePairPersistsToSameOriginalOms();
    TestUnsentH1PairCannotReturnOvertakenNativeBrokerIds();
    TestResearchH1PairConsumesOnlyNativeAdapterIdsWithoutBrokerEgress();
    TestBrokerFloorRaisesAfterLocalIdAllocationCannotReachSend();
    TestNativeOrderIdOutOfInt32RangeCannotReachFakeBroker();
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
    TestRecoveredOpenOrderRestoresOriginalTerminalBindingWithoutSend();
    TestCryptoCashBaselineUsesSharedResidentAndCanaryEnvelope();
    TestCashMarketTerminalNeedsActualExecutionAndNativeFinalStatus();
    TestWrapperQuantityTrackerCannotMixBrokerClients();
    TestSameEpochPostBootstrapLiveFillAndDeferredCancel();TestCancelledAndPartialCancelled();
    TestBoundContractExtensionsAndBrokerEnrichment();TestForeignStatusCannotRetireOurPartialOrder();
    TestValidatedSyntheticFillWithoutNativeStatus();
    TestQuarantineInvalidatesRecoveryBarrier();
    TestHostileEvidenceCannotPromoteFill();TestTerminalRefreshBindsUnknownCompletedOrderIdFromExactExecutionCorrelation();TestTerminalRefreshDoesNotCrossCorrelateReusedNumericOrderId();TestAdapterForwardsBoundedHandshakeBudget();TestReconnectAndIncompleteBootstrap();
    std::puts("ib_live_terminal_reconciliation_tests PASS");
}
