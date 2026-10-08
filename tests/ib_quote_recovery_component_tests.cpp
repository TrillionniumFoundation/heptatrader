// Actual Runtime::RefreshStaleSecondaryCashQuote + adapter + subscription set.
// This executable has no network transport. It is not a live Broker certificate.
#include "execution/ib_paper_execution_runtime_composition.h"
#include <iostream>
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
    bool connected=false, failCancel=false;
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
    IbQuoteRecoveryFixture(bool initialCashNoSend=false) {
        IbPaperExecutionRuntimeConfig config;
        config.profile.enabled=true; config.profile.userScope=true;
        config.profile.observationOnly=false; // DO NOT bypass the production mixed-user branch.
        config.profile.userQuoteContracts="BTC.USD|BTC|CRYPTO|PAXOS|USD;EUR.USD|EUR|CASH|IDEALPRO|USD";
        config.quoteMaxAgeMs=5000;
        require(config.profile.UsesUserCrypto(), "exact production mixed-user branch");
        IbPaperExecutionRuntimeTestHooks hooks;
        hooks.quoteClockMs=[this] { return clock; };
        hooks.onStage=[this](const char* stage) { stages.emplace_back(stage); };
        runtime.reset(new Runtime(config, std::unique_ptr<IIBApiWrapper>(), hooks));
        auto factory=[this]() -> std::unique_ptr<IIBApiWrapper> {
            std::unique_ptr<SocketFreeApi> next(new SocketFreeApi(counts)); api=next.get(); return std::move(next);
        };
        auto first=factory();
        runtime->m_adapter.reset(new HeptaIBGatewayAdapter(std::move(first), factory));
        HeptaIBConfig adapterConfig; adapterConfig.readOnly=true; adapterConfig.account="DU_TEST";
        require(runtime->m_adapter->Init(adapterConfig), "adapter init");
        require(runtime->m_adapter->Connect(100), "socket-free adapter connect");
        IBContractLite btc, eur;
        btc.symbol="BTC";btc.secType="CRYPTO";btc.exchange="PAXOS";btc.currency="USD";
        eur.symbol="EUR";eur.secType="CASH";eur.exchange="IDEALPRO";eur.currency="USD";
        runtime->m_quoteSubscriptions.reset(new IBAuthoritativeQuoteSubscriptionSet(runtime->m_authoritativeSnapshots));
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
    }
    IBAuthoritativeQuoteConsumeResult tick(int id, const char* leg, double value, std::uint64_t epoch=0) {
        IBEvent e;e.type=IBEventType::TickPrice;e.id=id;e.key=leg;e.number=value;e.connectionEpoch=epoch?epoch:api->epoch;
        return runtime->m_quoteSubscriptions->ConsumeTick(e,clock);
    }
    int currentFx() const { return runtime->m_quoteSubscriptions->GetHealth().contracts.at("EUR.USD").requestId; }
    bool refresh(std::string& reason) { return runtime->RefreshStaleSecondaryCashQuote(reason); }
    void noOrders() const { require(counts->orderAttempts==0 && counts->accountAttempts==0, "no order/account entrypoint reached"); }
    void testUnsentAndFarmRecovery() {
        std::string reason;const unsigned initial=counts->requests;
        require(refresh(reason) && counts->requests==initial, "no pre-cooldown renewal");
        clock+=15000;require(refresh(reason), "farm unavailable is definite no-send");
        int refused=currentFx();require(refused!=fxId && counts->cancels==0, "do not cancel unissued request");
        require(!runtime->m_quoteSubscriptions->GetQuote("EUR.USD").HasQuote(), "missing is not fresh");
        clock+=15000;api->ready();require(refresh(reason), "fresh farm lease permits real path renewal");
        int renewed=currentFx();require(renewed!=refused && counts->requests==initial+1, "one new market request");
        require(tick(fxId,"1",1.12).status==IBAuthoritativeQuoteConsumeStatus::Ignored, "old ID ignored");
        tick(renewed,"1",1.12);require(!runtime->m_quoteSubscriptions->GetQuote("EUR.USD").HasQuote(), "one leg not enough");
        tick(renewed,"2",1.1201);require(runtime->m_quoteSubscriptions->GetQuote("EUR.USD").HasQuote(), "both real fixture callbacks");
        require(runtime->m_quoteSubscriptions->GetHealth().contracts.at("BTC.USD").requestId==btcId, "BTC ID unchanged");
        noOrders();
    }
    void testDefiniteCancelAndUnknownFailures() {
        std::string reason;clock+=15000;api->failCancel=true;
        require(!refresh(reason) && reason=="IB_PAPER_STALE_CASH_REFRESH_OUTCOME_UNSAFE", "uncertain cancellation refuses");
        noOrders();
    }
    void testSdkException(SocketFreeApi::Outcome outcome) {
        std::string reason;clock+=15000;api->outcome=outcome;
        require(!refresh(reason) && reason=="IB_PAPER_STALE_CASH_REFRESH_OUTCOME_UNSAFE", "unknown SDK outcome fatal");
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
        { IbQuoteRecoveryFixture f;f.testDefiniteCancelAndUnknownFailures(); }
        { IbQuoteRecoveryFixture f;f.testSdkException(SocketFreeApi::ThrowBefore); }
        { IbQuoteRecoveryFixture f;f.testSdkException(SocketFreeApi::ThrowAfter); }
        { IbQuoteRecoveryFixture f;f.testRealAdapterEpochBoundary(); }
        std::cout << "{\"schema\":\"heptatrader.native-quote-component-test.v1\",\"production_refresh_method_executed\":true,\"observation_profile_bypass\":false,\"live_broker_qualification\":false,\"order_method_calls\":0,\"socket_calls\":0,\"scenarios_passed\":5}\n";
        return 0;
    } catch (const std::exception& e) { std::cerr<<e.what()<<'\n';return 1; }
}
