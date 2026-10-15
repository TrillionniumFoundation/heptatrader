// Real adapter + runtime post-fill methods, synthetic callbacks only.
// No native start, production state, network transport, or real broker send.
#include "execution/ib_paper_execution_runtime_composition.h"
#include "execution/execution_coordinator.h"
#include "adapter_ib/ib_venue_correlation.h"
#include <cstdlib>
#include <deque>
#include <dirent.h>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <sys/stat.h>
#include <unistd.h>

static void need(bool v,const std::string& message) { if(!v) throw std::runtime_error(message); }
struct ReplayBroker final : IIBApiWrapper {
    bool connected=false;std::uint64_t epoch=0;int requests=0,execRequest=0,places=0;
    long orderId=0;IBOrderLite order;IBContractLite contract;std::deque<IBEvent> events;
    void SetConnectionEpoch(std::uint64_t e) override {epoch=e;}
    std::uint64_t GetConnectionEpoch() const override {return epoch;}
    bool Connect(const IBConnectParams&) override {connected=true;return true;}
    void Disconnect() override {connected=false;}
    bool IsConnected() const override {return connected;}
    const char* GetStatusString() const override {return "SYNTHETIC_POSTFILL_NO_SOCKET";}
    bool ReqAccountSummary() override {++requests;return true;}
    bool ReqPositions() override {return true;}
    bool ReqAllOpenOrders() override {return true;}
    bool ReqCompletedOrders() override {return true;}
    bool ReqExecutions(int id) override {execRequest=id;return true;}
    bool ReqMktData(int,const IBContractLite&) override {throw std::runtime_error("UNEXPECTED_QUOTE_SEND");}
    bool CancelMktData(int) override {throw std::runtime_error("UNEXPECTED_QUOTE_CANCEL");}
    bool PlaceOrder(long id,const IBContractLite& c,const IBOrderLite& o) override {
        ++places;orderId=id;contract=c;order=o;return true;
    }
    bool PlaceOrderWithTimeInForce(long id,const IBContractLite& c,const IBOrderLite& o,const std::string&) override {return PlaceOrder(id,c,o);}
    bool CancelOrder(long) override {throw std::runtime_error("UNEXPECTED_CANCEL");}
    bool PollOnce(int) override {return true;}
    bool TryDequeueEvent(IBEvent& e) override {if(events.empty())return false;e=events.front();events.pop_front();return true;}
    long GetLastValidOrderId() const override {return 371;}
};

struct IbPostFillReconciliationFixture {
    using Clock=std::chrono::steady_clock;
    static constexpr double total=.2796508;
    std::string dir;ReplayBroker* broker;std::unique_ptr<IbPaperExecutionRuntimeComposition> runtime;
    ExecutionOrderOwner owner;long id=0;IBContractLite btc,eur;
    explicit IbPostFillReconciliationFixture(bool issueSyntheticOrder = true) {
        const char* configured=std::getenv("TMPDIR");
        const std::string parent=configured && *configured ? configured : "/tmp";
        std::string name=parent+"/hepta-postfill-XXXXXX";
        auto p=::mkdtemp(&name[0]);need(p,"temporary private state");dir=p;
        btc.symbol="BTC";btc.secType="CRYPTO";btc.exchange="PAXOS";btc.currency="USD";
        eur.symbol="EUR";eur.secType="CASH";eur.exchange="IDEALPRO";eur.currency="USD";
        IbPaperExecutionRuntimeConfig c;c.stateDirectory=dir;c.profile.account="DU_TEST";c.profile.clientId=73;
        c.profile.userScope=true;c.profile.observationOnly=false;c.profile.enabled=true;
        c.profile.userQuoteContracts="BTC.USD|BTC|CRYPTO|PAXOS|USD;EUR.USD|EUR|CASH|IDEALPRO|USD";
        c.quoteContracts={{"BTC.USD",btc},{"EUR.USD",eur}};
        IbPaperFxCashBaseline b;b.account="DU_TEST";b.instrument="EUR.USD";b.currency="EUR";
        b.baselineCashBalance=b.observedCashBalance=1000;b.observedAtMs=1;b.proof="sha256:"+std::string(64,'e');
        c.fxCashBaselines["EUR.USD"]=b;
        runtime.reset(new IbPaperExecutionRuntimeComposition(c));
        // This narrow offline fixture does not call Start(); initialize the
        // lifecycle gate so a recovery-poll test cannot dereference nullptr.
        runtime->m_lifecycleGate=std::make_shared<ExecutionServiceLifecycleGate>();
        runtime->m_lifecycleGate->ready.store(true);
        broker=new ReplayBroker;runtime->m_adapter.reset(new HeptaIBGatewayAdapter(std::unique_ptr<IIBApiWrapper>(broker)));
        HeptaIBConfig a;a.account="DU_TEST";a.clientId=73;a.readOnly=false;
        a.risk.enableOrderSubmission=true;a.risk.maxDailyOrders=100;a.risk.maxPriceDeviationBps=0;a.risk.duplicateOrderWindowSec=0;
        a.authoritativeCryptoContracts["BTC.USD"]=btc;a.authoritativeCashFxContracts["EUR.USD"]=eur;
        a.authoritativeCashFxBaselines["EUR.USD"]=1000;a.authoritativeCashFxStartupObservedBalances["EUR.USD"]=1000;
        need(runtime->m_adapter->Init(a),"adapter init");need(runtime->m_adapter->Connect(),"synthetic connect");
        need(runtime->m_adapter->ReqAuthoritativeOpenOrders(),"open barrier");push(event(IBEventType::OpenOrderEnd));
        need(runtime->m_adapter->ReqTerminalCorrelations(),"terminal barrier");push(event(IBEventType::CompletedOrdersEnd));
        auto end=event(IBEventType::ExecutionDetailsEnd);end.requestId=broker->execRequest;push(end);
        need(runtime->m_adapter->ReqRiskRefresh(),"baseline risk");finishRisk(total);
        owner.instrument="BTC.USD";owner.side="SELL";owner.account="DU_TEST";owner.executionDomain="PAPER";
        owner.venueCorrelationId="hepta-v1-sha256:"+std::string(64,'a');
        if(!issueSyntheticOrder)return; // flat/no-live-order owner probe
        IBOrderLite order;order.action="SELL";order.orderType="MKT";order.totalQuantity=total;
        IBFinalOrderSendContext ctx;ctx.instrument="BTC.USD";ctx.timeInForce="IOC";ctx.exactReduceOnly=true;
        need(runtime->m_adapter->PlaceOrderCorrelated(btc,order,owner.venueCorrelationId,&id,&ctx),
             "synthetic original reduction: "+runtime->m_adapter->GetLastRejectReason());
        need(broker->places==1,"one synthetic original send only");
    }
    ~IbPostFillReconciliationFixture() {
        runtime.reset();DIR* d=::opendir(dir.c_str());
        if(d){while(auto e=::readdir(d)){if(std::strcmp(e->d_name,".")&&std::strcmp(e->d_name,".."))::unlink((dir+"/"+e->d_name).c_str());}::closedir(d);}
        ::rmdir(dir.c_str());
    }
    IBEvent event(IBEventType type) const {
        IBEvent e;e.type=type;e.id=id;e.account="DU_TEST";e.brokerClientId=73;e.connectionEpoch=broker->epoch;return e;
    }
    void push(IBEvent e,bool deliver=false) {
        broker->events.push_back(e);IBEvent out;int count=0;
        while(runtime->m_adapter->TryDequeueEvent(out)) {
            ++count;
            if(deliver)need(runtime->BeginEventPostFillRiskRefresh(out,owner,id),"runtime postfill begin");
        }
        need(count==1,"callback accepted once");
    }
    void fill(double cumulative,const std::string& executionId) {
        auto e=event(IBEventType::ExecutionDetails);e.requestId=-1;e.key=executionId;e.value="SLD";
        e.number=81396.25;e.number2=cumulative;e.number3=total-cumulative;e.remainingQuantityKnown=true;
        e.contract=btc;e.order.orderRef=broker->order.orderRef;push(e,true);
    }
    void nativeFinal() {
        auto e=event(IBEventType::OrderStatus);e.key="Filled";e.number=81396.25;e.number2=total;e.number3=0;push(e,true);
    }
    void finishRisk(double quantity,bool omitEnd=false) {
        auto e=event(IBEventType::AccountValue);e.key="NetLiquidation:USD";e.value="1000000";push(e);
        e.key="CashBalance:EUR";e.value="1000";push(e);push(event(IBEventType::AccountSummaryEnd));
        if(quantity!=0){e=event(IBEventType::PositionSnapshotItem);e.key="BTC.USD";e.number=quantity;e.contract=btc;push(e);}
        if(!omitEnd)push(event(IBEventType::PositionEnd));
    }
    void nonflatRejectedAuditMustNotScheduleReconnect() {
        // Original account cash push arrived outside an account request.
        // Coherent risk can be refreshed, but a new connection epoch remains
        // required. An owner audit with nonzero physical exposure must not
        // schedule a potentially disruptive reconnection before rejecting.
        const auto before=runtime->m_adapter->GetAuthoritativeRecoveryAuditSnapshot();
        need(before.risk.complete&&before.risk.positionsComplete,
             "initial ordinary risk/positions complete");
        need(before.risk.grossAbsolutePosition>0,
             "nonflat real-position-shaped synthetic fixture");
        auto cash=event(IBEventType::AccountValue);
        cash.key="CashBalance:EUR";cash.value="1001";push(cash);
        need(runtime->m_adapter->ReqRiskRefresh(),"coherent ordinary refresh");
        finishRisk(total);
        const auto coherent=runtime->m_adapter->GetAuthoritativeRecoveryAuditSnapshot();
        need(coherent.risk.complete&&coherent.risk.coherentRefreshComplete &&
             coherent.risk.grossAbsolutePosition>0,
             "nonflat ordinary refresh completed despite sticky owner hold");
        need(!runtime->m_recoveryAuditReconnectRequested.load(),
             "no reconnect before owner audit probe");
        const auto epoch=runtime->m_adapter->GetConnectionEpoch();
        const auto snapshot=runtime->PolicyRecoveryAuditSnapshot();
        need(snapshot.newConnectionEpochRequired && !snapshot.barrierComplete,
             "owner audit must still explicitly refuse incomplete broker generation: "+
                 snapshot.reasonCode + " active=" + std::to_string(snapshot.active.activeOrderIds.size()));
        need(!runtime->m_recoveryAuditReconnectRequested.load(),
             "rejected nonflat owner audit must not schedule hidden reconnect");
        need(runtime->m_adapter->GetConnectionEpoch()==epoch &&
             broker->connected && broker->places==0,
             "rejected nonflat audit leaves broker epoch and sends unchanged");
        // Recovery must remain possible if a separately proven full refresh
        // later shows zero exposure and no active or pending Broker operations.
        need(runtime->m_adapter->ReqRiskRefresh(),"flat risk refresh");
        finishRisk(0);
        const auto flat=runtime->PolicyRecoveryAuditSnapshot();
        need(flat.newConnectionEpochRequired &&
             flat.risk.complete && flat.risk.grossAbsolutePosition==0 &&
             runtime->m_recoveryAuditReconnectRequested.load(),
             "flat coherent owner audit may request new epoch after negative nonflat");
        need(runtime->m_adapter->GetConnectionEpoch()==epoch &&
             broker->connected && broker->places==0,
             "unit requests a future reconnect but never performs Broker IO");
    }
    void flatAuditInvalidatedBeforeDisconnectMustNotReconnect() {
        // The explicit owner request observed flat authoritative risk and
        // scheduled a NEW-epoch audit, but a queued cash change later
        // invalidated that economic state before the poll thread could close
        // the Broker transport. A rejected/stale request is not permission
        // to disconnect an active production owner.
        need(broker->places==0,"audit fixture has never sent an order");
        need(runtime->m_adapter->ReqRiskRefresh(),"flat ordinary risk request");
        finishRisk(0);
        const auto before=runtime->m_adapter->GetAuthoritativeRecoveryAuditSnapshot();
        need(before.risk.complete&&before.risk.grossAbsolutePosition==0,
             "authoritative audit started from genuine flat risk");
        const auto startEpoch=runtime->m_adapter->GetConnectionEpoch();
        const auto scheduled=runtime->PolicyRecoveryAuditSnapshot();
        need(scheduled.newConnectionEpochRequired&&
             runtime->m_recoveryAuditReconnectRequested.load(),
             "flat owner audit scheduled a legitimate future epoch");
        auto drift=event(IBEventType::AccountValue);
        drift.key="CashBalance:EUR";drift.value="1001";
        push(drift); // callback arrives before the scheduled poll phase
        const auto invalid=runtime->m_adapter->GetAuthoritativeRecoveryAuditSnapshot();
        need(!invalid.risk.complete&&
             invalid.reasonCode=="IB_FX_CASH_BALANCE_OUT_OF_GENERATION_CHANGE",
             "actual late cash callback invalidated the same-epoch risk");
        const bool drove=runtime->DriveRecoveryAuditReconnect();
        std::string reason;
        need(drove&&!runtime->HasFatalRuntimeError(&reason),
             "stale owner audit must not fail the healthy Broker runtime: "+reason);
        need(!runtime->m_recoveryAuditReconnectRequested.load()&&
             !runtime->m_reconnectPending.load()&&
             runtime->m_adapter->GetConnectionEpoch()==startEpoch&&
             broker->connected&&broker->places==0,
             "stale owner audit cannot begin a disconnect or new epoch");
        need(runtime->m_lifecycleGate->ready.load(),
             "aborted stale audit restores the original service readiness");
    }
    void rejectIncompleteEvidence(int scenario) {
        fill(scenario==2?.16509636:total,"synthetic-incomplete");
        if(scenario!=2)nativeFinal();
        finishRisk(scenario==1?.01:0,scenario==0);
        const auto originalDeadline=runtime->m_postFillDeadline;
        runtime->DrivePostFillRiskRefresh();
        need(runtime->m_postFillRiskRefreshPending.load(),"missing terminal/End/position mismatch remains gated");
        need(runtime->m_postFillDeadline==originalDeadline,"original deadline never extended");
        runtime->m_postFillDeadline=Clock::now()-std::chrono::milliseconds(1);
        runtime->DrivePostFillRiskRefresh();
        std::string reason;
        need(runtime->HasFatalRuntimeError(&reason)&&reason=="IB_POST_FILL_RISK_RECONCILIATION_FAILED",
             "unresolved evidence retains original fail-closed timeout");
        need(runtime->m_adapter->HasPendingLivePostFillRiskReconciliation(),"unresolved exposure never ACKed");
        need(broker->places==1,"no retry trade or cancellation");
    }
    void reproduceAdvancedExposure(bool expectFixed) {
        fill(.16509636,"synthetic-partial");fill(total,"synthetic-full");nativeFinal();nativeFinal();
        finishRisk(0);
        const auto audit=runtime->m_adapter->GetAuthoritativeRecoveryAuditSnapshot();
        need(audit.risk.complete&&audit.risk.coherentRefreshComplete,"complete but older-generation risk witness");
        need(audit.risk.riskAbsorbedExposureGeneration<audit.exposureGeneration,"last cumulative fill not absorbed");
        runtime->m_postFillStableSince=Clock::now()-std::chrono::milliseconds(300);
        runtime->m_postFillNextRetryAt=Clock::now()-std::chrono::milliseconds(1);
        runtime->DrivePostFillRiskRefresh();
        std::string reason;const bool fatal=runtime->HasFatalRuntimeError(&reason);
        if(!expectFixed){need(fatal&&reason=="IB_POST_FILL_RISK_ACKNOWLEDGEMENT_FAILED","reproduce original premature acknowledgement");return;}
        need(!fatal,"no premature ACK/fatal: "+reason);
        need(runtime->m_postFillRiskRefreshPending.load(),"gate remains closed for last-fill generation");
        need(broker->requests==3,"one bounded newer risk pair requested");
        finishRisk(0);runtime->DrivePostFillRiskRefresh();
        need(runtime->m_postFillRiskRefreshPending.load(),"original stability interval preserved");
        runtime->m_postFillStableSince=Clock::now()-std::chrono::milliseconds(300);
        runtime->DrivePostFillRiskRefresh();
        need(!runtime->HasFatalRuntimeError(&reason),"coherent commit nonfatal: "+reason);
        need(!runtime->m_postFillRiskRefreshPending.load(),"post-fill gate released only on latest generation");
        need(!runtime->m_adapter->HasPendingLivePostFillRiskReconciliation(),"adapter ACK complete");
        const auto done=runtime->m_adapter->GetAuthoritativeRecoveryAuditSnapshot();
        need(done.risk.riskAbsorbedExposureGeneration==done.exposureGeneration,"latest exposure absorbed");
        nativeFinal();need(!runtime->m_postFillRiskRefreshPending.load(),"duplicate native final cannot double-apply");
        need(broker->places==1,"no extra synthetic order");
    }
};
int main(int argc,char**argv){
    try{IbPostFillReconciliationFixture f;
        if(argc>1&&std::string(argv[1])=="--invalidated-flat-audit") {
            IbPostFillReconciliationFixture invalidated(false);
            invalidated.flatAuditInvalidatedBeforeDisconnectMustNotReconnect();
            std::cout<<"PASS invalidated flat audit did not reconnect or mutate Broker\n";
            return 0;
        }
        if(argc>1&&std::string(argv[1])=="--nonflat-owner-audit") {
            IbPostFillReconciliationFixture nonflat(false);
            nonflat.nonflatRejectedAuditMustNotScheduleReconnect();
            std::cout<<"PASS nonflat rejected owner audit did not enqueue broker reconnect\n";
            return 0;
        }
        f.reproduceAdvancedExposure(!(argc>1&&std::string(argv[1])=="--expect-old-failure"));
        if(!(argc>1)){for(int k=0;k<3;++k){IbPostFillReconciliationFixture negative;negative.rejectIncompleteEvidence(k);}}
        std::cout<<"PASS partial/full, duplicate, missing End, position mismatch, missing terminal and fixed timeout; synthetic only\n";return 0;
    }catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}
}
