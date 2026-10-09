// Actual Runtime::RefreshStaleSecondaryCashQuote + adapter + subscription set.
// This executable has no network transport. It is not a live Broker certificate.
#include "execution/ib_paper_execution_runtime_composition.h"
#include "adapter_ib/ib_admission_event_policy.h"
#include <iostream>
#include <cstdlib>
#include <cstdio>
#include <unistd.h>
#include <map>
#include <memory>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

static void require(bool ok, const char* message) {
    if (!ok) throw std::runtime_error(message); // Assertions also run in Release.
}
struct Counters {
    unsigned requests=0, cancels=0, orderAttempts=0, accountAttempts=0;
};
class SocketFreeApi final : public IIBApiWrapper {
public:
    enum Outcome { Good, NoSend, ThrowBefore, ThrowAfter } outcome=Good;
    bool connected=false, failCancel=false, refuseCancel=false;
    std::uint64_t epoch=0, sequence=0;
    IBCashFarmAdmissionMarker farm;
    IBMarketDataAdmissionState admission;
    std::set<int> issued;
    std::shared_ptr<Counters> counts;
    explicit SocketFreeApi(std::shared_ptr<Counters> c) : counts(c) {}
    void SetConnectionEpoch(std::uint64_t value) override { epoch=value; farm.Reset(value); }
    std::uint64_t GetConnectionEpoch() const override { return epoch; }
    void ready() { farm.ObserveReady(epoch, ++sequence); }
    void lostFarm() { farm.ObserveWarning(epoch, ++sequence); }
    bool Connect(const IBConnectParams& p) override {
        require(p.readOnly, "adapter egress must be read-only"); connected=true; ready(); return true;
    }
    void Disconnect() override { connected=false; issued.clear(); }
    bool IsConnected() const override { return connected; }
    const char* GetStatusString() const override { return "SOCKET_FREE_FIXTURE"; }
    bool EventIngressAdmissionFailed() const override { return admission.IsFailed(); }
    bool ReqAccountSummary() override { ++counts->accountAttempts; throw std::runtime_error("ACCOUNT_IO_FORBIDDEN"); }
    bool ReqPositions() override { ++counts->accountAttempts; throw std::runtime_error("ACCOUNT_IO_FORBIDDEN"); }
    bool ReqMktData(int id, const IBContractLite& contract) override {
        if (!connected || (contract.secType=="CASH" && !farm.IsReady(epoch)) || outcome==NoSend) return false;
        if (outcome==ThrowBefore) throw std::runtime_error("SDK_UNKNOWN_BEFORE_RETURN");
        std::uint64_t generation=0;
        require(admission.Begin(generation) && admission.TryReserve(generation), "real admission reservation");
        require(issued.insert(id).second, "duplicate market-data ID");
        ++counts->requests;
        require(admission.EndSend(generation, false) && admission.Complete(), "real admission closes");
        if (outcome==ThrowAfter) throw std::runtime_error("SDK_UNKNOWN_AFTER_SEND");
        return true;
    }
    bool CancelMktData(int id) override {
        if (refuseCancel) return false;
        if (failCancel) throw std::runtime_error("CANCEL_OUTCOME_UNKNOWN");
        require(issued.erase(id)==1, "cancel only actually issued request"); ++counts->cancels; return true;
    }
    bool PlaceOrder(long, const IBContractLite&, const IBOrderLite&) override {
        ++counts->orderAttempts; throw std::runtime_error("ORDER_FORBIDDEN");
    }
    bool CancelOrder(long) override { ++counts->orderAttempts; throw std::runtime_error("ORDER_FORBIDDEN"); }
    bool PollOnce(int) override { return false; }
    bool TryDequeueEvent(IBEvent&) override { return false; }
    long GetLastValidOrderId() const override { return 1; }
};

struct IbQuoteRecoveryFixture {
    using Runtime=IbPaperExecutionRuntimeComposition;
    std::uint64_t clock=1000000;
    std::shared_ptr<Counters> counts=std::make_shared<Counters>();
    SocketFreeApi* api=nullptr;
    std::vector<std::string> stages;
    std::unique_ptr<Runtime> runtime;
    int btcId=0, fxId=0;
    std::string tempDirectory, journalPath;
    IbQuoteRecoveryFixture(bool initialCashNoSend=false) {
        IbPaperExecutionRuntimeConfig config;
        config.profile.enabled=true; config.profile.userScope=true;
        config.profile.clientId=701;
        config.profile.observationOnly=false; // DO NOT bypass the production mixed-user branch.
        config.profile.userQuoteContracts="BTC.USD|BTC|CRYPTO|PAXOS|USD;EUR.USD|EUR|CASH|IDEALPRO|USD";
        config.quoteMaxAgeMs=5000;
        InstrumentRef btcContract, eurContract;
        btcContract.symbol="BTC";btcContract.secType="CRYPTO";btcContract.exchange="PAXOS";btcContract.currency="USD";
        eurContract.symbol="EUR";eurContract.secType="CASH";eurContract.exchange="IDEALPRO";eurContract.currency="USD";
        config.quoteContracts={{"BTC.USD",btcContract},{"EUR.USD",eurContract}};
        config.primaryQuoteInstrument="BTC.USD";
        require(config.profile.UsesUserCrypto(), "exact production mixed-user branch");
        IbPaperExecutionRuntimeTestHooks hooks;
        hooks.quoteClockMs=[this] { return clock; };
        hooks.onStage=[this](const char* stage) { stages.emplace_back(stage); };
        runtime.reset(new Runtime(config, std::unique_ptr<IIBApiWrapper>(), hooks));
        char directory[]="/tmp/hepta-quote-component-XXXXXX";
        const char* created=::mkdtemp(directory);require(created!=nullptr,"private temporary evidence directory");
        tempDirectory=created;journalPath=tempDirectory+"/oms-journal.jsonl";
        require(runtime->m_journal.Init(journalPath),"real durable OMS journal");
        auto factory=[this]() -> std::unique_ptr<IIBApiWrapper> {
            std::unique_ptr<SocketFreeApi> next(new SocketFreeApi(counts)); api=next.get(); return std::move(next);
        };
        auto first=factory();
        runtime->m_adapter.reset(new HeptaIBGatewayAdapter(std::move(first), factory));
        HeptaIBConfig adapterConfig; adapterConfig.readOnly=true; adapterConfig.account="DU_TEST";adapterConfig.clientId=701;
        require(runtime->m_adapter->Init(adapterConfig), "adapter init");
        require(runtime->m_adapter->Connect(100), "socket-free adapter connect");
        IBContractLite btc, eur;
        btc.symbol="BTC";btc.secType="CRYPTO";btc.exchange="PAXOS";btc.currency="USD";
        eur.symbol="EUR";eur.secType="CASH";eur.exchange="IDEALPRO";eur.currency="USD";
        runtime->m_quoteSubscriptions.reset(new IBAuthoritativeQuoteSubscriptionSet(runtime->m_authoritativeSnapshots,1000001));
        std::string reason;
        require(runtime->m_quoteSubscriptions->Configure({{"BTC.USD",btc},{"EUR.USD",eur}},"BTC.USD",reason,false,true), "configure quotes");
        auto plan=runtime->m_quoteSubscriptions->BeginCycle(api->epoch,1,clock);
        require(plan.accepted, "initial cycle");
        for (const auto& sub:plan.subscriptions) {
            if (sub.instrument=="BTC.USD") btcId=sub.requestId;
            else { fxId=sub.requestId; if(initialCashNoSend) api->lostFarm(); }
            bool sent=runtime->m_adapter->ReqMktData(sub.requestId,sub.contract);
            require(runtime->m_quoteSubscriptions->RecordDispatchResult(plan.generation,sub.requestId,sent), "record exact outcome");
        }
        tick(btcId,"1",85000);tick(btcId,"2",85001);
        require(runtime->m_quoteSubscriptions->GetHealth().ReadyForOperations(), "BTC remains independent");
    }
    ~IbQuoteRecoveryFixture() {
        // Close the fake transport before teardown after unknown outcomes.
        // This is a test cleanup boundary, not another cancellation/retry.
        if (runtime && runtime->m_adapter) runtime->m_adapter->Disconnect();
        runtime.reset();
        if(!journalPath.empty())std::remove(journalPath.c_str());
        if(!tempDirectory.empty())::rmdir(tempDirectory.c_str());
    }
    IBAuthoritativeQuoteConsumeResult tick(int id, const char* leg, double value, std::uint64_t epoch=0) {
        IBEvent e;e.type=IBEventType::TickPrice;e.id=id;e.key=leg;e.number=value;e.connectionEpoch=epoch?epoch:api->epoch;
        return runtime->m_quoteSubscriptions->ConsumeTick(e,clock);
    }
    int currentFx() const { return runtime->m_quoteSubscriptions->GetHealth().contracts.at("EUR.USD").requestId; }
    int pendingFx() const { return runtime->m_quoteSubscriptions->GetHealth().contracts.at("EUR.USD").pendingRequestId; }
    bool refresh(std::string& reason) { return runtime->RefreshStaleSecondaryCashQuote(reason); }
    void requireRawBrokerError(const IBEvent& expected) {
        bool found=false;
        require(runtime->m_journal.Replay([&](const OmsJournalEvent& row) {
            if(row.eventType=="broker_error"&&row.orderId==expected.id&&
               row.brokerErrorCode==std::stoi(expected.key)&&row.brokerRequestId==expected.requestId&&
               row.brokerConnectionEpoch==expected.connectionEpoch&&row.brokerClientId==expected.brokerClientId&&
               row.brokerMessage==expected.value&&row.riskCode=="IB_ERROR_"+expected.key)
                found=true;
        })>0&&found,"original native refusal retained in real OMS ledger");
    }
    void noOrders() const { require(counts->orderAttempts==0 && counts->accountAttempts==0, "no order/account entrypoint reached"); }
    void testUnsentAndFarmRecovery() {
        std::string reason;const unsigned initial=counts->requests;
        require(refresh(reason) && counts->requests==initial, "no pre-cooldown renewal");
        clock+=15000;require(refresh(reason), "farm unavailable is definite no-send");
        int refused=pendingFx();require(refused!=fxId && refused>0 && counts->cancels==0, "do not cancel unissued request");
        require(!runtime->m_quoteSubscriptions->GetQuote("EUR.USD").HasQuote(), "missing is not fresh");
        clock+=15000;api->ready();require(refresh(reason), "fresh farm lease permits real path renewal");
        int renewed=pendingFx();require(renewed!=refused && counts->requests==initial+1, "one new market request");
        require(tick(fxId,"1",1.12).status==IBAuthoritativeQuoteConsumeStatus::Ignored, "old ID ignored");
        tick(renewed,"1",1.12);require(!runtime->m_quoteSubscriptions->GetQuote("EUR.USD").HasQuote(), "one leg not enough");
        tick(renewed,"2",1.1201);require(runtime->m_quoteSubscriptions->GetQuote("EUR.USD").HasQuote(), "both real fixture callbacks");
        require(runtime->m_quoteSubscriptions->GetHealth().contracts.at("BTC.USD").requestId==btcId, "BTC ID unchanged");
        noOrders();
    }
    void testQuietCashRefreshPrecedesOriginalFiveSecondExpiry() {
        tick(fxId,"1",1.12);tick(fxId,"2",1.1201);
        const auto original=runtime->m_quoteSubscriptions->GetQuote("EUR.USD");
        const auto requests=counts->requests;std::string reason;
        clock+=3000;require(refresh(reason)&&counts->requests==requests,"renewal trigger boundary remains rate-limited");
        ++clock;require(refresh(reason),"bounded renewal reserves native callback latency");
        require(counts->requests==requests+1,"renewal starts before original five-second validity expires");
        const int fresh=pendingFx();require(fresh!=fxId&&fresh>0,"new subscription identity");
        require(currentFx()==fxId&&counts->cancels==0,"keep old native stream during warmup");
        const auto warming=runtime->m_quoteSubscriptions->GetQuote("EUR.USD");
        require(warming.bidObservedAtMs==original.bidObservedAtMs&&warming.askObservedAtMs==original.askObservedAtMs,"renewal never restamps old quote");
        clock+=1000;tick(fresh,"1",1.12);
        require(currentFx()==fxId,"one pending leg cannot promote");
        tick(fxId,"1",1.2);tick(fxId,"2",1.21);
        require(currentFx()==fxId,"old real callbacks cannot fill pending ask");
        tick(fresh,"2",1.1201);
        const auto actual=runtime->m_quoteSubscriptions->GetQuote("EUR.USD");
        require(actual.HasQuote()&&actual.bidObservedAtMs==clock&&actual.askObservedAtMs==clock,"only new broker callbacks restore original TTL");
        require(currentFx()==fresh,"new pair atomically replaces active");
        require(refresh(reason)&&counts->cancels==1,"retire old only after new pair");
        require(tick(fxId,"1",1.3).status==IBAuthoritativeQuoteConsumeStatus::Ignored,"retired callback cannot overwrite new leg");
        clock+=2999;require(refresh(reason)&&counts->requests==requests+1,"renewal rate remains bounded");
        require(runtime->m_quoteSubscriptions->GetHealth().contracts.at("BTC.USD").requestId==btcId,"primary identity unchanged");
        noOrders();
    }
    void testDefiniteCancelAndUnknownFailures() {
        std::string reason;clock+=3001;require(refresh(reason),"warm pending before cancel uncertainty");
        const int pending=pendingFx();tick(pending,"1",1.12);tick(pending,"2",1.1201);api->failCancel=true;
        require(!refresh(reason) && reason=="IB_PAPER_STALE_CASH_REFRESH_OUTCOME_UNSAFE", "uncertain cancellation refuses");
        noOrders();
    }
    void testCancelFalseAndAdmissionFault(bool cancelFalse) {
        std::string reason;clock+=3001;
        if(cancelFalse) {
            require(refresh(reason),"warm pending before cancel refusal");
            const int pending=pendingFx();tick(pending,"1",1.12);tick(pending,"2",1.1201);api->refuseCancel=true;
        }
        else api->admission.MarkFault();
        const auto requests=counts->requests;
        require(!refresh(reason),"cancellation/admission failure must fail closed");
        require(counts->requests==requests,"no replacement request on cancellation/admission failure");
        require(reason==(cancelFalse?"IB_PAPER_STALE_CASH_REFRESH_OUTCOME_UNSAFE":"IB_PAPER_STALE_CASH_REFRESH_STREAM_UNSAFE"),"exact failing gate");
        noOrders();
    }
    void testSlowAcceptedPairIsNotCancelledEveryThreeSeconds() {
        tick(fxId,"1",1.12);tick(fxId,"2",1.1201);
        const auto original=runtime->m_quoteSubscriptions->GetQuote("EUR.USD");
        const auto requests=counts->requests;std::string reason;
        clock+=3001;require(refresh(reason),"start one bounded warm stream");
        const int pending=pendingFx();
        for(unsigned i=0;i<20;++i) {++clock;clock+=1000;require(refresh(reason),"healthy slow stream waits");}
        require(pendingFx()==pending&&currentFx()==fxId&&counts->requests==requests+1&&counts->cancels==0,"accepted slow request neither cancelled nor reissued");
        const auto stale=runtime->m_quoteSubscriptions->GetQuote("EUR.USD");
        require(stale.bidObservedAtMs==original.bidObservedAtMs&&stale.askObservedAtMs==original.askObservedAtMs&&clock-stale.CompositeObservedAtMs()>5000,"old pair remains genuinely stale after original five seconds");
        tick(pending,"1",1.12);clock+=5001;tick(pending,"2",1.1201);
        require(currentFx()==fxId,"stale pending bid cannot be combined into fresh pair");
        tick(pending,"1",1.12);require(currentFx()==pending,"only two actually fresh pending legs promote");
        require(refresh(reason)&&counts->cancels==1&&counts->requests==requests+1,"promotion retires exactly old stream");
        noOrders();
    }
    void testStopIncludesPendingAndRetiringStreams(bool promoted) {
        std::string reason;clock+=3001;require(refresh(reason),"start bounded pending");
        const int pending=pendingFx();
        if(promoted){tick(pending,"1",1.12);tick(pending,"2",1.1201);}
        require(api->issued.size()==3,"primary plus at most two cash streams");
        require(runtime->StopQuoteSubscriptions(&reason),"stop cancels every actually issued stream");
        require(api->issued.empty()&&counts->cancels==3,"neither pending nor retiring stream leaks");noOrders();
    }
    void testActualEntitlementErrorSequenceRemainsFatal() {
        std::string reason;clock+=3001;require(refresh(reason),"warm real pending request");
        const int pending=pendingFx();const auto requests=counts->requests;
        for(const int code:{354,300}) {
            IBEvent e;e.type=IBEventType::Error;e.id=pending;e.key=std::to_string(code);e.connectionEpoch=api->epoch;e.value="unmarked native request error";
            require(IbAdmissionBlockingEvent(e),"real scoped 354/300 remain blocking");
            api->admission.ObserveBlockingCallback(true);
            require(runtime->RoutePersistedAdapterEvent(e)==Runtime::AdapterRouteAction::Processed,"raw error retains normal OMS route");
            requireRawBrokerError(e);
        }
        require(pendingFx()==pending&&currentFx()==fxId,"raw number/text without provenance cannot localize pending rejection");
        require(!refresh(reason)&&reason=="IB_PAPER_STALE_CASH_REFRESH_STREAM_UNSAFE","actual refusal sequence remains fail closed");
        require(counts->requests==requests&&counts->cancels==0,"no retry or old cancellation after blocking callback");noOrders();
    }
    void testScopedPendingEntitlementRefusalBacksOff() {
        tick(fxId,"1",1.12);tick(fxId,"2",1.1201);
        const auto original=runtime->m_quoteSubscriptions->GetQuote("EUR.USD");
        std::string reason;clock+=3001;require(refresh(reason),"warm one pending request");
        const int pending=pendingFx();const auto requests=counts->requests;
        IBEvent rejected;rejected.type=IBEventType::Error;rejected.id=pending;rejected.key="354";rejected.connectionEpoch=api->epoch;
        rejected.contract=runtime->m_quoteSubscriptions->GetHealth().contracts.at("EUR.USD").contract;
        auto foreign=rejected;foreign.connectionEpoch++;
        require(!runtime->m_quoteSubscriptions->RejectSecondaryCashRefreshRequest(foreign,clock),"future epoch cannot reject current request");
        foreign.connectionEpoch=0;
        require(!runtime->m_quoteSubscriptions->RejectSecondaryCashRefreshRequest(foreign,clock),"unproven epoch cannot reject current request");
        foreign=rejected;foreign.id=fxId;
        require(!runtime->m_quoteSubscriptions->RejectSecondaryCashRefreshRequest(foreign,clock),"active request is not pending rejection");
        foreign=rejected;foreign.key="300";
        require(!runtime->m_quoteSubscriptions->RejectSecondaryCashRefreshRequest(foreign,clock),"300 cannot invent rejection or cancellation proof");
        foreign=rejected;foreign.account="DU_FOREIGN";
        require(!runtime->m_quoteSubscriptions->RejectSecondaryCashRefreshRequest(foreign,clock),"account/order scoped error remains unsafe");
        require(api->issued.erase(pending)==1,"Broker refuses this exact sent subscription");
        const auto rejectedAt=clock;
        rejected.requestId=pending;rejected.brokerClientId=701;rejected.localMarketDataRequestError=true;
        rejected.contract=runtime->m_quoteSubscriptions->GetHealth().contracts.at("EUR.USD").contract;
        rejected.value="Broker rejected requested market data subscription";
        require(IsIbCorroboratedCashMarketDataRequestError(rejected),"native marker binds exact production request identity");
        require(runtime->RoutePersistedAdapterEvent(rejected)==Runtime::AdapterRouteAction::Processed&&!runtime->HasFatalRuntimeError(),"actual runtime route localizes owned pending refusal after persistence");
        requireRawBrokerError(rejected);
        require(!runtime->m_quoteSubscriptions->RejectSecondaryCashRefreshRequest(rejected,clock),"replayed rejection has no active ownership");
        require(currentFx()==fxId&&pendingFx()==0&&counts->cancels==0,"rejected ticker is not cancelled and old stream preserved");
        const auto still=runtime->m_quoteSubscriptions->GetQuote("EUR.USD");
        require(still.bidObservedAtMs==original.bidObservedAtMs&&still.askObservedAtMs==original.askObservedAtMs,"rejection cannot refresh old quote");
        require(tick(pending,"1",1.14).status==IBAuthoritativeQuoteConsumeStatus::Ignored,"rejected id callback cannot revive quote");
        clock=rejectedAt+14999;require(refresh(reason)&&counts->requests==requests&&pendingFx()==0,"native refusal backs off for at least fifteen seconds");
        require(clock-still.CompositeObservedAtMs()>5000,"old quote expires under original validity bound");
        clock=rejectedAt+15000;require(refresh(reason)&&counts->requests==requests+1,"bounded later retry is a distinct native request");
        const int next=pendingFx();require(next!=pending&&next!=fxId,"retry never reuses refused id");
        tick(next,"1",1.12);tick(next,"2",1.1201);require(currentFx()==next,"only actual retry pair heals unavailable quote");
        require(refresh(reason)&&counts->cancels==1,"retire original only after successful retry pair");
        IBEvent retired=rejected;retired.id=retired.requestId=fxId;retired.key="300";
        retired.value="Ticker id not found after exact local market data cancellation";
        require(api->issued.count(fxId)==0,"the exact retired stream was actually cancelled");
        api->admission.ObserveBlockingCallback(false);
        require(runtime->RoutePersistedAdapterEvent(retired)==Runtime::AdapterRouteAction::Processed&&!runtime->HasFatalRuntimeError(),"corroborated retired 300 keeps exact diagnostic without changing owner");
        requireRawBrokerError(retired);
        require(currentFx()==next&&pendingFx()==0&&counts->requests==requests+1,"retired cancellation notice is not a recovery request");noOrders();
    }
    IBEvent markedCashRefusal(int requestId) {
        IBEvent e;e.type=IBEventType::Error;e.id=e.requestId=requestId;e.key="354";
        e.connectionEpoch=api->epoch;e.brokerClientId=701;e.localMarketDataRequestError=true;
        e.contract=runtime->m_quoteSubscriptions->GetHealth().contracts.at("EUR.USD").contract;
        e.value="Broker rejected requested market data subscription";return e;
    }
    void testInitiallySentUnpairedCashCanRecoverFromNativeRejection() {
        tick(fxId,"1",1.12);
        const auto refused=markedCashRefusal(fxId);const auto requests=counts->requests;
        require(api->issued.erase(fxId)==1,"native initial refusal removes exact subscribed id");
        require(runtime->RoutePersistedAdapterEvent(refused)==Runtime::AdapterRouteAction::Processed&&!runtime->HasFatalRuntimeError(),"actual route records initial unpaired native rejection");
        requireRawBrokerError(refused);
        const auto rejected=runtime->m_quoteSubscriptions->GetHealth().contracts.at("EUR.USD");
        require(rejected.nativeRequestRejected&&!rejected.dispatchAccepted&&!rejected.quote.HasAny(),"native rejection is distinct from pre-SDK refusal and clears only unpaired evidence");
        require(tick(fxId,"2",1.1201).status==IBAuthoritativeQuoteConsumeStatus::Ignored,"late refused callback cannot heal initial quote");
        std::string reason;clock+=14999;require(refresh(reason)&&counts->requests==requests&&counts->cancels==0,"initial native refusal also observes fifteen-second backoff");
        ++clock;require(refresh(reason)&&counts->requests==requests+1,"initial retry uses bounded new request");
        const int retry=pendingFx();tick(retry,"1",1.12);tick(retry,"2",1.1201);
        require(currentFx()==retry&&pendingFx()==0,"only retry's two real legs restore initial secondary");
        require(refresh(reason)&&counts->cancels==0,"explicitly rejected original ticker is never cancelled");
        require(!runtime->m_quoteSubscriptions->GetHealth().contracts.at("EUR.USD").nativeRequestRejected,"successful different request has its own provenance");noOrders();
    }
    void testPreviouslyLiveCash354CannotBeLocalizedAfterBadLeg() {
        tick(fxId,"1",1.12);tick(fxId,"2",1.1201);tick(fxId,"2",-1.0);
        require(!runtime->m_quoteSubscriptions->GetQuote("EUR.USD").HasQuote(),"latest malformed leg invalidates pair");
        const auto liveRefusal=markedCashRefusal(fxId);const auto requests=counts->requests;
        require(runtime->RoutePersistedAdapterEvent(liveRefusal)==Runtime::AdapterRouteAction::Fatal,"once-live active 354 cannot borrow pending scope");
        requireRawBrokerError(liveRefusal);
        std::string reason;require(runtime->HasFatalRuntimeError(&reason)&&reason=="IB_PAPER_MARKET_DATA_REJECTION_SCOPE_UNSAFE","once-live request failure stays fail closed even after invalid leg");
        require(counts->requests==requests&&counts->cancels==0,"no automatic replacement after unsafe live refusal");noOrders();
    }
    void testFailureToPersistNativeRefusalCannotRearm() {
        tick(fxId,"1",1.12);tick(fxId,"2",1.1201);
        std::string reason;clock+=3001;require(refresh(reason),"start exact pending before journal failure");
        const int pending=pendingFx();const auto requests=counts->requests;
        const auto rejected=markedCashRefusal(pending);
        require(std::remove(journalPath.c_str())==0,"remove pinned journal path for storage failure");
        require(runtime->RoutePersistedAdapterEvent(rejected)==Runtime::AdapterRouteAction::Fatal,"refusal must persist before localized state change");
        require(runtime->HasFatalRuntimeError(&reason)&&reason=="OMS_BROKER_EVENT_WRITE_FAILED","ledger failure stays fail closed");
        require(pendingFx()==pending&&currentFx()==fxId&&counts->requests==requests&&counts->cancels==0,"unrecorded error cannot remove pending authority or schedule retry");noOrders();
    }
    void testTradeMarkerCannotLocalizeCashRefusal() {
        std::string reason;clock+=3001;require(refresh(reason),"create pending cash stream");
        const int pending=pendingFx();const auto requests=counts->requests;
        auto trade=markedCashRefusal(pending);trade.contract.secType="CRYPTO";trade.contract.symbol="BTC";
        require(!IsIbCorroboratedCashMarketDataRequestError(trade),"raw marker cannot broaden CASH request provenance to trade contract");
        require(IbAdmissionBlockingEvent(trade),"unsupported contract refusal remains sender blocking");
        api->admission.ObserveBlockingCallback(true);
        require(runtime->RoutePersistedAdapterEvent(trade)==Runtime::AdapterRouteAction::Processed,"unknown trade-shaped marker follows raw diagnostic route");
        requireRawBrokerError(trade);
        require(pendingFx()==pending&&currentFx()==fxId,"trade-shaped marker cannot reject a cash request");
        require(!refresh(reason)&&reason=="IB_PAPER_STALE_CASH_REFRESH_STREAM_UNSAFE"&&counts->requests==requests,"unsafe marker cannot rearm native stream");noOrders();
    }
    void testDifferentCashContractCannotBorrowPendingRequestIdentity() {
        tick(fxId,"1",1.12);tick(fxId,"2",1.1201);
        const auto original=runtime->m_quoteSubscriptions->GetQuote("EUR.USD");
        std::string reason;clock+=3001;require(refresh(reason),"create exact EUR pending stream");
        const int pending=pendingFx();const auto requests=counts->requests;
        auto forged=markedCashRefusal(pending);forged.contract.symbol="GBP";
        require(IsIbCorroboratedCashMarketDataRequestError(forged),"CASH marker structure alone cannot prove exact subscribed contract");
        require(runtime->RoutePersistedAdapterEvent(forged)==Runtime::AdapterRouteAction::Fatal,"different CASH contract cannot borrow exact EUR request id");
        requireRawBrokerError(forged);
        require(runtime->HasFatalRuntimeError(&reason)&&reason=="IB_PAPER_MARKET_DATA_REJECTION_SCOPE_UNSAFE","mismatched marked contract remains fail closed");
        const auto unchanged=runtime->m_quoteSubscriptions->GetQuote("EUR.USD");
        require(currentFx()==fxId&&pendingFx()==pending&&counts->requests==requests&&counts->cancels==0,"contract mismatch changes no request or cancellation ownership");
        require(unchanged.bid==original.bid&&unchanged.ask==original.ask&&unchanged.bidObservedAtMs==original.bidObservedAtMs&&unchanged.askObservedAtMs==original.askObservedAtMs,"contract mismatch cannot alter price or timestamp");
        require(runtime->m_quoteSubscriptions->GetHealth().contracts.at("EUR.USD").refreshRetryNotBeforeMs==0,"mismatched error cannot schedule localized backoff");noOrders();
    }
    void testSdkException(SocketFreeApi::Outcome outcome) {
        std::string reason;clock+=15000;api->outcome=outcome;
        require(!refresh(reason) && reason=="IB_PAPER_STALE_CASH_REFRESH_OUTCOME_UNSAFE", "unknown SDK outcome fatal");
        noOrders();
    }
    IBEvent control(int code, const std::string& message, std::uint64_t epoch=0) {
        IBEvent e;e.type=IBEventType::Error;e.id=-1;e.key=std::to_string(code);
        e.value=message;e.connectionEpoch=epoch?epoch:api->epoch;return e;
    }
    void testMaintainedDataDoesNotResubscribeOrRestamp() {
        tick(fxId,"1",1.12);tick(fxId,"2",1.1201);
        const auto before=runtime->m_quoteSubscriptions->GetQuote("EUR.USD");
        const auto sent=counts->requests, cancelled=counts->cancels;
        runtime->m_reconnectPending.store(true);
        runtime->m_reconnectUpstreamUnavailable.store(true);
        clock+=10000;
        auto action=runtime->HandleAdapterControlEvent(control(1102,"Connectivity restored - data maintained"));
        require(action==Runtime::AdapterControlAction::Consumed,"production 1102 routing");
        require(runtime->m_reconnectUpstreamRestored.load() && !runtime->m_reconnectUpstreamUnavailable.load(),"1102 upstream restored");
        const auto after=runtime->m_quoteSubscriptions->GetQuote("EUR.USD");
        require(before.bidObservedAtMs==after.bidObservedAtMs && before.askObservedAtMs==after.askObservedAtMs,"1102 must not restamp legs");
        require(sent==counts->requests && cancelled==counts->cancels && currentFx()==fxId,"1102 does not replace issued ticker");
        require(!runtime->HasFatalRuntimeError(),"maintained data must not fatal");
        noOrders();
    }
    void testLostDataBeforeQuotesAndOldEpochIsolation() {
        runtime->m_reconnectPending.store(true);
        const auto sent=counts->requests;
        auto stale=control(1101,"Connectivity restored - data lost",api->epoch+1);
        auto action=runtime->HandleAdapterControlEvent(stale);
        require(action==Runtime::AdapterControlAction::Route && !runtime->m_reconnectUpstreamRestored.load(),"foreign epoch cannot restore");
        const auto h=runtime->m_quoteSubscriptions->GetHealth();
        for(const auto& item:h.contracts) if(item.second.dispatchAccepted)
            require(runtime->m_adapter->CancelMktData(item.second.requestId),"cancel issued fixture ticker");
        runtime->m_quoteSubscriptions->AbortCycle(h.generation);
        action=runtime->HandleAdapterControlEvent(control(1101,"Connectivity restored - data lost"));
        require(action==Runtime::AdapterControlAction::Consumed && runtime->m_reconnectUpstreamRestored.load(),"1101 before quote cycle restores transport only");
        require(!runtime->m_quoteSubscriptions->GetHealth().ReadyForOperations(),"1101 cannot invent quote readiness");
        require(counts->requests==sent && !runtime->HasFatalRuntimeError(),"no implicit order or quote retry");
        noOrders();
    }
    void testSecondaryFarmLossDoesNotKillPrimary() {
        runtime->m_reconnectPending.store(true);
        api->lostFarm();
        auto action=runtime->HandleAdapterControlEvent(control(2103,"Market data farm disconnected:cashfarm"));
        require(action==Runtime::AdapterControlAction::Route,"secondary farm is instrument scoped");
        require(!runtime->HasFatalRuntimeError(),"secondary CASH loss cannot invalidate healthy BTC");
        require(runtime->m_quoteSubscriptions->GetHealth().contracts.at("BTC.USD").requestId==btcId,"primary identity preserved");
        require(runtime->m_quoteSubscriptions->GetHealth().ReadyForOperations(),"primary still present");
        noOrders();
    }
    void testRealAdapterEpochBoundary() {
        auto oldEpoch=api->epoch;int oldId=fxId;
        runtime->m_adapter->Disconnect();clock+=20000;
        std::string reason;const auto before=counts->requests;
        require(refresh(reason) && counts->requests==before, "no renewal on disconnected adapter");
        require(runtime->m_adapter->Connect(100), "real adapter rebuilds injected wrapper");
        require(api->epoch>oldEpoch, "adapter epoch advances");
        auto plan=runtime->m_quoteSubscriptions->BeginCycle(api->epoch,2,clock);
        require(plan.accepted, "new epoch cycle");
        for(const auto& sub:plan.subscriptions) {
            require(runtime->m_adapter->ReqMktData(sub.requestId,sub.contract), "new-epoch request");
            require(runtime->m_quoteSubscriptions->RecordDispatchResult(2,sub.requestId,true), "new-epoch outcome");
        }
        require(tick(oldId,"2",1.2,oldEpoch).status==IBAuthoritativeQuoteConsumeStatus::Ignored, "old epoch cannot restore quote");
        require(!runtime->m_quoteSubscriptions->GetQuote("EUR.USD").HasQuote(), "new epoch starts missing");
        noOrders();
    }
};
int main() {
    try {
        { IbQuoteRecoveryFixture f(true);f.testUnsentAndFarmRecovery(); }
        { IbQuoteRecoveryFixture f;f.testQuietCashRefreshPrecedesOriginalFiveSecondExpiry(); }
        { IbQuoteRecoveryFixture f;f.testDefiniteCancelAndUnknownFailures(); }
        { IbQuoteRecoveryFixture f;f.testSdkException(SocketFreeApi::ThrowBefore); }
        { IbQuoteRecoveryFixture f;f.testSdkException(SocketFreeApi::ThrowAfter); }
        { IbQuoteRecoveryFixture f;f.testRealAdapterEpochBoundary(); }
        { IbQuoteRecoveryFixture f;f.testMaintainedDataDoesNotResubscribeOrRestamp(); }
        { IbQuoteRecoveryFixture f;f.testLostDataBeforeQuotesAndOldEpochIsolation(); }
        { IbQuoteRecoveryFixture f;f.testSecondaryFarmLossDoesNotKillPrimary(); }
        { IbQuoteRecoveryFixture f;f.testCancelFalseAndAdmissionFault(true); }
        { IbQuoteRecoveryFixture f;f.testCancelFalseAndAdmissionFault(false); }
        { IbQuoteRecoveryFixture f;f.testSlowAcceptedPairIsNotCancelledEveryThreeSeconds(); }
        { IbQuoteRecoveryFixture f;f.testStopIncludesPendingAndRetiringStreams(false); }
        { IbQuoteRecoveryFixture f;f.testStopIncludesPendingAndRetiringStreams(true); }
        { IbQuoteRecoveryFixture f;f.testActualEntitlementErrorSequenceRemainsFatal(); }
        { IbQuoteRecoveryFixture f;f.testScopedPendingEntitlementRefusalBacksOff(); }
        { IbQuoteRecoveryFixture f;f.testInitiallySentUnpairedCashCanRecoverFromNativeRejection(); }
        { IbQuoteRecoveryFixture f;f.testPreviouslyLiveCash354CannotBeLocalizedAfterBadLeg(); }
        { IbQuoteRecoveryFixture f;f.testFailureToPersistNativeRefusalCannotRearm(); }
        { IbQuoteRecoveryFixture f;f.testTradeMarkerCannotLocalizeCashRefusal(); }
        { IbQuoteRecoveryFixture f;f.testDifferentCashContractCannotBorrowPendingRequestIdentity(); }
        std::cout << "{\"schema\":\"heptatrader.native-quote-component-test.v1\",\"production_refresh_method_executed\":true,\"observation_profile_bypass\":false,\"live_broker_qualification\":false,\"order_method_calls\":0,\"socket_calls\":0,\"scenarios_passed\":21}\n";
        return 0;
    } catch (const std::exception& e) { std::cerr<<e.what()<<'\n';return 1; }
}
