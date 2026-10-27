// Real adapter + runtime post-fill methods, synthetic callbacks only.
// No native start, production state, network transport, or real broker send.
#include "execution/ib_paper_execution_runtime_composition.h"
#include "execution/execution_coordinator.h"
#include "execution/execution_decision_lease_authority.h"
#include "events/execution_event_hub.h"
#include "execution/ib_paper_execution_profile.h"
#include "execution/unix_execution_service_server.h"
#include "adapter_ib/ib_venue_correlation.h"
#include <cstdlib>
#include <deque>
#include <functional>
#include <future>
#include <condition_variable>
#include <atomic>
#include <mutex>
#include <fcntl.h>
#include <dirent.h>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <sys/stat.h>
#include <unistd.h>

static void need(bool v,const std::string& message) { if(!v) throw std::runtime_error(message); }
struct ReplayBroker final : IIBApiWrapper {
    bool connected=false;std::uint64_t epoch=0;int requests=0,execRequest=0;
    std::atomic<int> places{0}, cancels{0};
    bool allowSyntheticCancel=false, allowSyntheticQuotes=false, returnFalseAfterPlace=false;
    long orderId=0;IBOrderLite order;IBContractLite contract;std::deque<IBEvent> events;
    void SetConnectionEpoch(std::uint64_t e) override {epoch=e;}
    std::uint64_t GetConnectionEpoch() const override {return epoch;}
    bool Connect(const IBConnectParams&) override {connected=true;return true;}
    std::function<void()> onDisconnect;
    unsigned disconnectCalls = 0;
    void Disconnect() override {
        connected = false;
        ++disconnectCalls;
        std::function<void()> callback;
        callback.swap(onDisconnect);
        if (callback) callback();
    }
    bool IsConnected() const override {return connected;}
    const char* GetStatusString() const override {return "SYNTHETIC_POSTFILL_NO_SOCKET";}
    bool ReqAccountSummary() override {++requests;return true;}
    bool ReqPositions() override {return true;}
    bool ReqAllOpenOrders() override {return true;}
    bool ReqCompletedOrders() override {return true;}
    bool ReqExecutions(int id) override {execRequest=id;return true;}
    bool ReqMktData(int,const IBContractLite&) override {
        if (!allowSyntheticQuotes) throw std::runtime_error("UNEXPECTED_QUOTE_SEND");
        return true;
    }
    bool CancelMktData(int) override {
        if (!allowSyntheticQuotes) throw std::runtime_error("UNEXPECTED_QUOTE_CANCEL");
        return true;
    }
    bool PlaceOrder(long id,const IBContractLite& c,const IBOrderLite& o) override {
        ++places;orderId=id;contract=c;order=o;return !returnFalseAfterPlace;
    }
    bool PlaceOrderWithTimeInForce(long id,const IBContractLite& c,const IBOrderLite& o,const std::string&) override {return PlaceOrder(id,c,o);}
    bool CancelOrder(long) override {
        if (!allowSyntheticCancel) throw std::runtime_error("UNEXPECTED_CANCEL");
        ++cancels; return true;
    }
    bool PollOnce(int) override {return true;}
    std::function<void()> onQueueEmpty;
    bool TryDequeueEvent(IBEvent& e) override {
        if(events.empty()) {if(onQueueEmpty)onQueueEmpty();return false;}
        e=events.front();events.pop_front();return true;
    }
    long GetLastValidOrderId() const override {return 371;}
};

struct RuntimeExitBarrier {
    std::mutex mutex; std::condition_variable changed;
    bool entered=false, released=false;
    void Signal() { std::lock_guard<std::mutex> lock(mutex); entered=true; changed.notify_all(); }
    void Block() {
        std::unique_lock<std::mutex> lock(mutex); entered=true; changed.notify_all();
        need(changed.wait_for(lock,std::chrono::seconds(15),[&]{return released;}),"runtime fixture release deadline");
    }
    void Wait() {
        std::unique_lock<std::mutex> lock(mutex);
        need(changed.wait_for(lock,std::chrono::seconds(2),[&]{return entered;}),"runtime callback progress before release");
    }
    void Release() { std::lock_guard<std::mutex> lock(mutex); released=true; changed.notify_all(); }
};
class RuntimeExitKillSwitch final : public IbPaperKillSwitchReader {
public:
    IbPaperKillSwitchObservation Observe() const override {
        IbPaperKillSwitchObservation observation; observation.state=IbPaperKillSwitchState::Disarmed; return observation;
    }
};
struct RuntimeLogPipe {
    std::string path; int reader=-1;
    explicit RuntimeLogPipe(const std::string& p):path(p) {
        need(::unlink(path.c_str())==0,"replace only private fixture diagnostic log");
        need(::mkfifo(path.c_str(),0600)==0,"private blocked diagnostic sink");
    }
    void Unblock() noexcept { if(reader<0) reader=::open(path.c_str(),O_RDONLY|O_NONBLOCK|O_CLOEXEC); }
    ~RuntimeLogPipe() {
        if(reader>=0)::close(reader);
        ::unlink(path.c_str());
        const int file=::open(path.c_str(),O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC,0600);
        if(file>=0)::close(file);
    }
};
struct RuntimeExitRelease {
    RuntimeExitBarrier* barrier=nullptr; RuntimeLogPipe* pipe=nullptr;
    ~RuntimeExitRelease() { if(barrier)barrier->Release(); if(pipe)pipe->Unblock(); }
};

struct IbPostFillReconciliationFixture {
    using Clock=std::chrono::steady_clock;
    static constexpr double total=.2796508;
    std::string dir, account;ReplayBroker* broker;std::unique_ptr<IbPaperExecutionRuntimeComposition> runtime;
    ExecutionOrderOwner owner;long id=0;IBContractLite btc,eur;
    explicit IbPostFillReconciliationFixture(bool issueSyntheticOrder = true,
        const std::string& accountName = "DU_TEST") : account(accountName) {
        const char* configured=std::getenv("TMPDIR");
        const std::string parent=configured && *configured ? configured : "/tmp";
        std::string name=parent+"/hepta-postfill-XXXXXX";
        auto p=::mkdtemp(&name[0]);need(p,"temporary private state");dir=p;
        btc.symbol="BTC";btc.secType="CRYPTO";btc.exchange="PAXOS";btc.currency="USD";
        eur.symbol="EUR";eur.secType="CASH";eur.exchange="IDEALPRO";eur.currency="USD";
        IbPaperExecutionRuntimeConfig c;c.stateDirectory=dir;c.profile.account=account;c.profile.clientId=73;
        c.profile.userScope=true;c.profile.observationOnly=false;c.profile.enabled=true;
        c.profile.userQuoteContracts="BTC.USD|BTC|CRYPTO|PAXOS|USD;EUR.USD|EUR|CASH|IDEALPRO|USD";
        c.quoteContracts={{"BTC.USD",btc},{"EUR.USD",eur}};
        IbPaperFxCashBaseline b;b.account=account;b.instrument="EUR.USD";b.currency="EUR";
        b.baselineCashBalance=b.observedCashBalance=1000;b.observedAtMs=1;b.proof="sha256:"+std::string(64,'e');
        c.fxCashBaselines["EUR.USD"]=b;
        runtime.reset(new IbPaperExecutionRuntimeComposition(c));
        // This narrow offline fixture does not call Start(); initialize the
        // lifecycle gate so a recovery-poll test cannot dereference nullptr.
        runtime->m_lifecycleGate=std::make_shared<ExecutionServiceLifecycleGate>();
        runtime->m_lifecycleGate->ready.store(true);
        broker=new ReplayBroker;runtime->m_adapter.reset(new HeptaIBGatewayAdapter(std::unique_ptr<IIBApiWrapper>(broker)));
        HeptaIBConfig a;a.account=account;a.clientId=73;a.readOnly=false;
        a.observabilityLogPath=dir+"/observability.jsonl";
        a.risk.enableOrderSubmission=true;a.risk.maxDailyOrders=100;a.risk.maxPriceDeviationBps=0;a.risk.duplicateOrderWindowSec=0;
        a.authoritativeCryptoContracts["BTC.USD"]=btc;a.authoritativeCashFxContracts["EUR.USD"]=eur;
        a.authoritativeCashFxBaselines["EUR.USD"]=1000;a.authoritativeCashFxStartupObservedBalances["EUR.USD"]=1000;
        need(runtime->m_adapter->Init(a),"adapter init");need(runtime->m_adapter->Connect(),"synthetic connect");
        need(runtime->m_adapter->ReqAuthoritativeOpenOrders(),"open barrier");push(event(IBEventType::OpenOrderEnd));
        need(runtime->m_adapter->ReqTerminalCorrelations(),"terminal barrier");push(event(IBEventType::CompletedOrdersEnd));
        auto end=event(IBEventType::ExecutionDetailsEnd);end.requestId=broker->execRequest;push(end);
        need(runtime->m_adapter->ReqRiskRefresh(),"baseline risk");finishRisk(total);
        owner.instrument="BTC.USD";owner.side="SELL";owner.account=account;owner.executionDomain="PAPER";
        owner.venueCorrelationId="hepta-v1-sha256:"+std::string(64,'a');
        if(!issueSyntheticOrder)return; // flat/no-live-order owner probe
        IBOrderLite order;order.action="SELL";order.orderType="MKT";order.totalQuantity=total;
        IBFinalOrderSendContext ctx;ctx.instrument="BTC.USD";ctx.timeInForce="IOC";ctx.exactReduceOnly=true;
        need(runtime->m_adapter->PlaceOrderCorrelated(btc,order,owner.venueCorrelationId,&id,&ctx),
             "synthetic original reduction: "+runtime->m_adapter->GetLastRejectReason());
        need(broker->places==1,"one synthetic original send only");
    }
    ~IbPostFillReconciliationFixture() {
        if (broker) broker->onDisconnect = {};
        runtime.reset();DIR* d=::opendir(dir.c_str());
        if(d){while(auto e=::readdir(d)){if(std::strcmp(e->d_name,".")&&std::strcmp(e->d_name,".."))::unlink((dir+"/"+e->d_name).c_str());}::closedir(d);}
        ::rmdir(dir.c_str());
    }
    IBEvent event(IBEventType type) const {
        IBEvent e;e.type=type;e.id=id;e.account=account;e.brokerClientId=73;e.connectionEpoch=broker->epoch;return e;
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
    void prepareConcurrentPolicy() {
        need(broker->places==0,"concurrency fixture starts with no synthetic order");
        IbPaperExecutionProfileConfig profile;
        profile.enabled=true;profile.account=account;profile.host="127.0.0.1";
        profile.port=4002;profile.clientId=73;profile.stateDirectory=dir;
        profile.controlDirectory="/run/hepta/ib-paper-control-fixture";
        profile.authorizationCredentialPath=dir+"/hepta-ib-paper-authorization";
        profile.maxOrderQuantity=1;profile.maxOrderNotional=100;
        profile.maxOrdersPerMinute=4;profile.maxActiveOrders=4;profile.maxGrossPosition=4;
        std::string reason;
        need(profile.Validate(reason),"valid bounded LOCAL concurrency profile: "+reason);
        runtime->m_config.profile=profile;
        runtime->m_config.quoteMaxAgeMs=5000;
        runtime->m_config.primaryQuoteInstrument="EUR.USD";
        runtime->m_config.quoteContracts={{"EUR.USD",eur}};
        runtime->m_killSwitch=std::make_shared<RuntimeExitKillSwitch>();
        need(GenerateExecutionServiceIdentity(1,runtime->m_serviceIdentity,reason),"isolated service identity");
        runtime->m_eventHub.reset(new ExecutionEventHub(1024,runtime->m_serviceIdentity.serviceEpoch));
        runtime->m_decisionLeases.reset(new ExecutionDecisionLeaseAuthority());
        runtime->m_quoteSubscriptions.reset(new IBAuthoritativeQuoteSubscriptionSet(runtime->m_authoritativeSnapshots,1000001));
        need(runtime->m_quoteSubscriptions->Configure({{"EUR.USD",eur}},"EUR.USD",reason),"synthetic quote configuration: "+reason);
        const auto now=OmsJournal::NowEpochMs();
        const auto plan=runtime->m_quoteSubscriptions->BeginCycle(broker->epoch,1,now);
        need(plan.accepted&&plan.subscriptions.size()==1,"synthetic quote cycle");
        broker->allowSyntheticQuotes=true;
        const auto& subscription=plan.subscriptions.front();
        need(runtime->m_adapter->ReqMktData(subscription.requestId,subscription.contract),"socket-free quote dispatch");
        need(runtime->m_quoteSubscriptions->RecordDispatchResult(plan.generation,subscription.requestId,true),"quote dispatch recorded");
        for(const auto& leg:std::map<std::string,double>{{"1",1.10},{"2",1.11}}) {
            auto tick=event(IBEventType::TickPrice);tick.id=subscription.requestId;tick.key=leg.first;tick.number=leg.second;
            need(runtime->m_quoteSubscriptions->ConsumeTick(tick,now).status==IBAuthoritativeQuoteConsumeStatus::Applied,"real quote projection");
        }
        runtime->m_adapter->UpdateReferencePrice(1.105);
        runtime->m_adapter->SetPrePlaceOrderSendCheck([this](const IBFinalOrderSendContext* context,
            const IBContractLite& contract,const IBOrderLite& order,std::string* detail) {
            return runtime->AuthorizeFinalBrokerSend(context,contract,order,detail);
        });
        need(runtime->m_journal.Init(dir+"/oms-journal.jsonl"),"private concurrency OMS");
        runtime->BuildCoordinator();
        need(runtime->RecoveryReason().empty(),"coordinator starts from actual empty journal");
        need(runtime->BuildPolicyAuthority(reason),"real runtime policy construction: "+reason);
        broker->allowSyntheticCancel=true;
    }
    PlaceOrderCommand concurrentOrder(const std::string& request) {
        PlaceOrderCommand command;command.context.agentId="runtime-exit-fixture";
        command.context.sessionId="session";command.context.toolCallId=request;
        command.context.account=account;command.context.venue="IB";command.context.executionDomain="PAPER";
        command.instrument="EUR.USD";command.contract=eur;command.order.action="BUY";
        command.order.orderType="MKT";command.order.totalQuantity=1;command.timeInForce="DAY";
        command.referencePrice=1.11;command.expiresAtMs=OmsJournal::NowEpochMs()+60000;
        std::string reason;
        need(runtime->m_decisionLeases->Authorize(command.context,command.instrument,reason),"real native decision lease: "+reason);
        return command;
    }
    CancelOrderCommand confirmedCancel() {
        const auto original=concurrentOrder("confirmed-old");
        const auto sent=runtime->m_policyAuthority->PlaceOrder(original);
        need(sent.status==ExecutionCommandStatus::Accepted,"real policy/adapter synthetic original send: "+sent.reasonCode);
        auto acknowledgement=event(IBEventType::OrderStatus);
        acknowledgement.id=sent.orderId;acknowledgement.key="Submitted";
        acknowledgement.number=1.11;acknowledgement.number2=0;acknowledgement.number3=1;
        push(acknowledgement);
        std::string reason;
        need(runtime->m_adapter->CanCancelOrder(sent.orderId,&reason),"original order independently acknowledged: "+reason);
        CancelOrderCommand cancel;cancel.context=original.context;
        cancel.context.toolCallId="cancel-confirmed";cancel.orderId=sent.orderId;
        return cancel;
    }
    void slowRuntimePreparationAllowsConfirmedCancel() {
        prepareConcurrentPolicy();
        const auto cancel=confirmedCancel();
        const auto entry=concurrentOrder("slow-prepare");
        RuntimeExitBarrier blocked;
        runtime->m_testHooks.onStage=[&](const char* stage) {
            if(std::strcmp(stage,"before_venue_send")==0)blocked.Block();
        };
        std::future<ExecutionCommandResult> placement,cancellation;
        RuntimeExitRelease release;release.barrier=&blocked;
        placement=std::async(std::launch::async,[&]{return runtime->m_policyAuthority->PlaceOrder(entry);});
        blocked.Wait();
        const auto started=Clock::now();
        cancellation=std::async(std::launch::async,[&]{return runtime->m_policyAuthority->CancelOrder(cancel);});
        need(cancellation.wait_for(std::chrono::seconds(1))==std::future_status::ready,"confirmed cancel progresses before slow runtime preparation release");
        const auto cancelled=cancellation.get();
        const auto ns=std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now()-started).count();
        need(cancelled.status==ExecutionCommandStatus::Accepted&&broker->cancels==1,"exact synthetic cancel sent once");
        need(placement.wait_for(std::chrono::milliseconds(0))==std::future_status::timeout,"new placement remains blocked outside send locks");
        need(runtime->m_policyAuthority->CancelOrder(cancel).status==ExecutionCommandStatus::Duplicate&&broker->cancels==1,"runtime duplicate cancel remains idempotent");
        blocked.Release();
        need(placement.get().status==ExecutionCommandStatus::Accepted&&broker->places==2,"same runtime executes original new placement once after release");
        runtime->m_testHooks.onStage={};
        std::cout<<"{\"schema\":\"heptatrader.runtime-exit-progress.v1\",\"cancel_total_ns\":"<<ns
                 <<",\"before_prepare_release\":true,\"real_adapter\":true,\"socket_free\":true}\n";
    }
    void blockedDiagnosticFlushReleasesSendLocks() {
        prepareConcurrentPolicy();
        const auto cancel=confirmedCancel();
        auto acknowledgement=event(IBEventType::OrderStatus);
        acknowledgement.id=cancel.orderId;acknowledgement.key="Submitted";
        acknowledgement.number=1.11;acknowledgement.number2=0;acknowledgement.number3=1;
        acknowledgement.contract=broker->contract;acknowledgement.order=broker->order;
        const auto entry=concurrentOrder("blocked-log");
        RuntimeExitBarrier afterPlace,afterCancel,queueDrained;
        runtime->m_testHooks.onStage=[&](const char* stage) {
            if(std::strcmp(stage,"after_venue_send")==0)afterPlace.Signal();
            if(std::strcmp(stage,"after_cancel_venue_send")==0)afterCancel.Signal();
        };
        // A writer open cannot complete until this private FIFO gets a reader.
        // The fixture unblocks it on every exception path before futures join.
        RuntimeLogPipe pipe(dir+"/observability.jsonl");
        struct QueueHookReset {
            ReplayBroker* broker;
            ~QueueHookReset() {broker->onQueueEmpty={};}
        } queueHookReset{broker};
        std::future<ExecutionCommandResult> placement,cancellation;
        std::future<bool> drain;
        RuntimeExitRelease release;release.pipe=&pipe;
        placement=std::async(std::launch::async,[&]{return runtime->m_policyAuthority->PlaceOrder(entry);});
        afterPlace.Wait();
        need(placement.wait_for(std::chrono::milliseconds(0))==std::future_status::timeout,"diagnostic writer still blocked");
        // Use the real polling entry point. The last empty-queue notification
        // is unreachable if ACK logging recaptures the API/quote locks before
        // waiting on the blocked writer; cancel cannot win that race first.
        broker->events.push_back(acknowledgement);
        broker->onQueueEmpty=[&]{queueDrained.Signal();};
        drain=std::async(std::launch::async,[&]{return runtime->DrainAdapterEvents(0);});
        queueDrained.Wait();
        need(drain.wait_for(std::chrono::milliseconds(0))==std::future_status::timeout,"ACK diagnostic flush remains blocked outside send locks");
        const auto started=Clock::now();
        cancellation=std::async(std::launch::async,[&]{return runtime->m_policyAuthority->CancelOrder(cancel);});
        afterCancel.Wait();
        const auto ns=std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now()-started).count();
        need(broker->cancels==1&&broker->places==2,"confirmed cancel reached real adapter while other send logging is blocked");
        need(placement.wait_for(std::chrono::milliseconds(0))==std::future_status::timeout,"no diagnostic unblock was needed for cancel transport progress");
        pipe.Unblock();need(pipe.reader>=0,"open private diagnostic drain");
        need(placement.get().status==ExecutionCommandStatus::Accepted,"logging delay preserves place result");
        need(cancellation.get().status==ExecutionCommandStatus::Accepted,"logging delay preserves cancel result");
        need(drain.get(),"real event drain accepted the queued ACK");
        need(!runtime->HasFatalRuntimeError(nullptr),"ACK projection and durable routing retain valid identity");
        char bytes[8192];const auto count=::read(pipe.reader,bytes,sizeof(bytes));
        need(count>0,"deferred immutable observations are written");
        const std::string records(bytes,static_cast<std::size_t>(count));
        need(records.find("api_place")!=std::string::npos&&records.find("api_cancel")!=std::string::npos&&
             records.find("callback.order_status")!=std::string::npos,"place, cancel and ACK observations survive deferred flush");
        runtime->m_testHooks.onStage={};
        std::cout<<"{\"schema\":\"heptatrader.runtime-exit-progress.v1\",\"cancel_dispatch_ns\":"<<ns
                 <<",\"before_log_unblock\":true,\"ack_processed_before_cancel\":true,\"quote_and_api_locks_released\":true,\"socket_free\":true}\n";
    }
    void falseOrdinarySendRequiresReconciliationAndNeverRetries() {
        prepareConcurrentPolicy();
        auto invalid=concurrentOrder("invalid-local-order");invalid.order.totalQuantity=0;
        const auto rejected=runtime->m_adapter->PlaceOrderWithResult(invalid.contract,invalid.order,
            "hepta-v1-sha256:"+std::string(64,'d'));
        need(rejected.disposition==VenuePlaceDisposition::Rejected&&broker->places==0,
             "proven pre-send invalid quantity retains no-effect rejection");
        broker->returnFalseAfterPlace=true;
        const auto entry=concurrentOrder("false-send");
        const auto uncertain=runtime->m_policyAuthority->PlaceOrder(entry);
        need(uncertain.status==ExecutionCommandStatus::Uncertain&&
             uncertain.reasonCode=="IB_PLACE_OUTCOME_UNCERTAIN"&&broker->places==1,
             "false after real wrapper invocation is an unknown effect");
        need(runtime->m_coordinator->IsMutationBlocked(nullptr),"unknown result requires authoritative reconciliation");
        const auto replay=runtime->m_policyAuthority->PlaceOrder(entry);
        need(replay.status==ExecutionCommandStatus::Uncertain&&broker->places==1,
             "exact unknown command never invokes wrapper again");
        const auto another=runtime->m_policyAuthority->PlaceOrder(concurrentOrder("new-after-unknown"));
        need(another.status==ExecutionCommandStatus::Rejected&&another.reasonCode=="MUTATION_BLOCKED"&&broker->places==1,
             "changing the request identity cannot bypass unknown-effect hold");
        std::cout<<"{\"schema\":\"heptatrader.runtime-exit-progress.v1\",\"false_send_uncertain\":true,\"wrapper_calls\":1,\"retry_suppressed\":true,\"socket_free\":true}\n";
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
    void lateLiveFillDuringRecoveryAuditDisconnectMustFailClosed() {
        need(broker->places == 0, "boundary fixture must start without any order send");
        need(runtime->m_adapter->ReqRiskRefresh(), "flat coherent risk refresh");
        finishRisk(0);
        std::string reason;
        need(GenerateExecutionServiceIdentity(1, runtime->m_serviceIdentity, reason),
             "real isolated service identity: " + reason);
        need(runtime->m_journal.Init(dir + "/oms-journal.jsonl"),
             "real isolated OMS journal");
        runtime->BuildCoordinator();
        need(runtime->m_coordinator && runtime->RecoveryReason().empty(),
             "real coordinator recovered empty journal: " + runtime->RecoveryReason());
        need(runtime->BuildPolicyAuthority(reason), "real policy authority: " + reason);
        need(!runtime->m_coordinator->IsMutationBlocked(&reason),
             "empty reconstructed coordinator initially unfenced: " + reason);
        const auto before = runtime->m_adapter->GetAuthoritativeRecoveryAuditSnapshot();
        need(before.transportConnected && before.risk.complete &&
             before.risk.coherentRefreshComplete && before.risk.grossAbsolutePosition == 0 &&
             !runtime->m_adapter->HasPendingLivePostFillRiskReconciliation() &&
             !runtime->m_postFillRiskRefreshPending.load(),
             "real flat complete account before audit");
        const auto epoch = runtime->m_adapter->GetConnectionEpoch();
        const auto postFillDeadline = runtime->m_postFillDeadline;
        const auto reconnectDeadline = runtime->m_reconnectDeadline;
        need(postFillDeadline == Clock::time_point() &&
             reconnectDeadline == Clock::time_point(),
             "fixture starts with neither deadline armed");
        const int requests = broker->requests;
        const unsigned disconnects = broker->disconnectCalls;
        const auto scheduled = runtime->PolicyRecoveryAuditSnapshot();
        need(scheduled.newConnectionEpochRequired && !scheduled.barrierComplete &&
             runtime->m_recoveryAuditReconnectRequested.load(),
             "original flat policy audit must queue reconnect: " + scheduled.reasonCode);
        IBEvent late = event(IBEventType::ExecutionDetails);
        late.id = 9371;
        late.requestId = -1;
        late.key = "synthetic-live-fill-at-disconnect-9371";
        late.value = "BOT";
        late.number = 81396.25;
        late.number2 = 0.01;
        late.number3 = 0;
        late.contract = btc;
        late.order.orderRef = "manual-disconnect-boundary";
        late.remainingQuantityKnown = false;
        broker->onDisconnect = [this, late]() {
            need(!runtime->m_lifecycleGate->ready.load(), "real Begin closed ready gate");
            need(!runtime->m_quoteSubscriptions, "subscriptions stopped before disconnect");
            broker->events.push_back(late);
        };
        for (int turn = 0; turn < 2; ++turn) {
            const bool drove = runtime->DriveRecoveryAuditReconnect();
            need(!drove && runtime->HasFatalRuntimeError(&reason) &&
                 reason == "IB_PAPER_BROKER_DISCONNECTED_DURING_POST_FILL_RECONCILIATION",
                 "live fill after Disconnect must fail closed instead of defer: " + reason);
            need(!broker->connected && !runtime->m_adapter->IsConnected() &&
                 broker->disconnectCalls == disconnects + 1,
                 "exactly one real synthetic Disconnect boundary");
            need(runtime->m_adapter->GetConnectionEpoch() == epoch,
                 "no subsequent transport reconnect epoch");
            need(runtime->m_adapter->HasPendingLivePostFillRiskReconciliation() &&
                 !runtime->m_postFillRiskRefreshPending.load(),
                 "adapter-only live post-fill pending without runtime reconciliation owner");
            need(!runtime->m_reconnectPending.load() &&
                 runtime->m_recoveryAuditReconnectRequested.load() &&
                 !runtime->m_lifecycleGate->ready.load(),
                 "queued audit, unscheduled reconnect, closed lifecycle gate");
            need(runtime->m_postFillDeadline == postFillDeadline &&
                 runtime->m_reconnectDeadline == reconnectDeadline &&
                 runtime->m_postFillRiskRefreshAttempts == 0 &&
                 broker->requests == requests,
                 "neither post-fill nor reconnect deadline/refresh was started");
            need(broker->places == 0 && broker->events.empty(),
                 "boundary callback consumed without any order send");
            need(runtime->m_coordinator->IsMutationBlocked(&reason) &&
                 reason == "IB_PAPER_BROKER_RECONNECT_PENDING",
                 "actual Begin installed original coordinator reconnect fence: " + reason);
        }
        unsigned retained = 0;
        const int replayed = runtime->m_journal.Replay([&](const OmsJournalEvent& row) {
            if (row.eventType != "broker_execution" || row.brokerExecutionId != late.key) return;
            ++retained;
            need(row.orderId == late.id && row.brokerRequestId == -1 &&
                 row.brokerConnectionEpoch == epoch && row.brokerClientId == 73 &&
                 row.account == "DU_TEST" && row.brokerCallbackType == "execDetails" &&
                 row.brokerServiceEpoch == runtime->m_serviceIdentity.serviceEpoch &&
                 row.instrument == "BTC.USD" && row.side == "BOT" &&
                 row.qty == late.number2 && row.price == late.number &&
                 row.source == "ib-api-callback",
                 "original same-account same-epoch live callback retained exactly");
        });
        need(replayed >= 1 && retained == 1, "one original boundary execution persisted in real OMS");
    }

    void deferredAuditAbortRestoresOriginalReady(
        bool stopBeforeAbort = false, bool originallyReady = true,
        bool callBeginWhileDeferred = false) {
        // The queued audit is a state-machine input; the separate flat-audit
        // test covers PolicyRecoveryAuditSnapshot's real enqueue boundary.
        if (callBeginWhileDeferred) {
            std::string setupReason;
            need(runtime->m_journal.Init(dir + "/oms-journal.jsonl"),
                 "real journal for pure Begin defer");
            runtime->BuildCoordinator();
            need(runtime->m_coordinator && runtime->RecoveryReason().empty(),
                 "real empty coordinator recovery: " + runtime->RecoveryReason());
            need(runtime->BuildPolicyAuthority(setupReason),
                 "real policy before partial fill: " + setupReason);
            const auto initial = runtime->m_adapter->GetAuthoritativeRecoveryAuditSnapshot();
            need(initial.active.complete && initial.terminal.complete &&
                 initial.active.activeOrderIds.count(id) == 1,
                 "original synthetic send retains complete active/terminal snapshots");
            need(!runtime->m_coordinator->IsMutationBlocked(&setupReason),
                 "pure Begin starts without coordinator fence: " + setupReason);
        }
        if (!originallyReady) runtime->m_lifecycleGate->ready.store(false);
        need(runtime->m_lifecycleGate->ready.load() == originallyReady,
             "original readiness");
        const auto startEpoch = runtime->m_adapter->GetConnectionEpoch();
        runtime->m_recoveryAuditReconnectRequested.store(true);
        fill(.16509636, "synthetic-deferred-audit-partial");
        need(runtime->m_postFillRiskRefreshPending.load() &&
             runtime->m_adapter->HasPendingLivePostFillRiskReconciliation(),
             "original partial fill entered both reconciliation gates");
        need(runtime->DriveRecoveryAuditReconnect(), "first poll deferred");
        need(!runtime->m_lifecycleGate->ready.load() &&
             runtime->m_recoveryAuditReconnectRequested.load() &&
             !runtime->m_reconnectPending.load(),
             "deferred poll keeps its request and closes readiness");

        if (callBeginWhileDeferred) {
            const auto deadline = runtime->m_postFillDeadline;
            const auto disconnects = broker->disconnectCalls;
            std::string beginReason;
            const auto began = runtime->BeginBrokerReconnect(beginReason, true);
            need(began == decltype(began)::DeferredPostFill,
                 "Begin returns typed DeferredPostFill: " + beginReason);
            need(broker->connected && broker->disconnectCalls == disconnects &&
                 runtime->m_adapter->GetConnectionEpoch() == startEpoch &&
                 !runtime->m_reconnectPending.load() &&
                 runtime->m_recoveryAuditReconnectRequested.load() &&
                 runtime->m_postFillRiskRefreshPending.load() &&
                 runtime->m_postFillDeadline == deadline &&
                 !runtime->m_lifecycleGate->ready.load(),
                 "pure Begin must defer before disconnect/fence/deadline changes: " + beginReason);
            need(!runtime->m_coordinator->IsMutationBlocked(&beginReason),
                 "pure Begin defer cannot install reconnect fence: " + beginReason);
        }

        fill(total, "synthetic-deferred-audit-full");
        nativeFinal(); nativeFinal(); finishRisk(0);
        const auto older = runtime->m_adapter->GetAuthoritativeRecoveryAuditSnapshot();
        need(older.risk.complete && older.risk.coherentRefreshComplete &&
             older.risk.riskAbsorbedExposureGeneration < older.exposureGeneration,
             "first original risk cohort predates final exposure");
        runtime->m_postFillStableSince = Clock::now()-std::chrono::milliseconds(300);
        runtime->m_postFillNextRetryAt = Clock::now()-std::chrono::milliseconds(1);
        runtime->DrivePostFillRiskRefresh();
        std::string reason;
        need(!runtime->HasFatalRuntimeError(&reason) &&
             runtime->m_postFillRiskRefreshPending.load(),
             "older risk evidence waits for a newer original cohort");
        finishRisk(0); runtime->DrivePostFillRiskRefresh();
        need(runtime->m_postFillRiskRefreshPending.load(),
             "original stability interval remains enforced");
        runtime->m_postFillStableSince = Clock::now()-std::chrono::milliseconds(300);
        runtime->DrivePostFillRiskRefresh();
        need(!runtime->HasFatalRuntimeError(&reason) &&
             !runtime->m_postFillRiskRefreshPending.load() &&
             !runtime->m_adapter->HasPendingLivePostFillRiskReconciliation(),
             "original callbacks complete post-fill before audit cancellation");

        auto drift=event(IBEventType::AccountValue);
        drift.key="CashBalance:EUR"; drift.value="1001"; push(drift);
        const auto changed=runtime->m_adapter->GetAuthoritativeRecoveryAuditSnapshot();
        need(!changed.risk.complete && changed.reasonCode==
             "IB_FX_CASH_BALANCE_OUT_OF_GENERATION_CHANGE",
             "original cash callback invalidates queued audit economics");

        const auto stoppedAtSeam=std::make_shared<bool>(false);
        const auto originalHook=runtime->m_testHooks.onStage;
        if(stopBeforeAbort) {
            runtime->m_testHooks.onStage=[this,stoppedAtSeam](const char* stage) {
                if(!*stoppedAtSeam && std::strcmp(stage,
                        "before_recovery_audit_reconnect_drain")==0) {
                    *stoppedAtSeam=true;
                    runtime->Stop();
                }
            };
        }
        const bool drove=runtime->DriveRecoveryAuditReconnect();
        runtime->m_testHooks.onStage=originalHook;
        need(drove && !runtime->HasFatalRuntimeError(&reason),
             "second poll cancels without fatal");
        need(*stoppedAtSeam==stopBeforeAbort, "requested Stop seam executed");
        need(!runtime->m_recoveryAuditReconnectRequested.load() &&
             !runtime->m_reconnectPending.load(), "no hidden reconnect after cancel");
        need(runtime->m_lifecycleGate->ready.load()==
                 (originallyReady && !stopBeforeAbort),
             stopBeforeAbort ? "Stop must keep readiness closed" :
             originallyReady ? "deferred audit must restore original readiness" :
                               "audit must not open an originally closed gate");
        need(broker->connected==!stopBeforeAbort &&
             runtime->m_adapter->GetConnectionEpoch()==startEpoch && broker->places==1,
             "preserve original epoch and single synthetic send");
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
int main(int argc, char** argv) {
    try {
        if(argc==1||(argc==2&&std::string(argv[1])=="--concurrent-exits")) {
            {IbPostFillReconciliationFixture runtime(false,"DU90001");runtime.slowRuntimePreparationAllowsConfirmedCancel();}
            {IbPostFillReconciliationFixture runtime(false,"DU90002");runtime.blockedDiagnosticFlushReleasesSendLocks();}
            {IbPostFillReconciliationFixture runtime(false,"DU90003");runtime.falseOrdinarySendRequiresReconciliationAndNeverRetries();}
            if(argc==2)return 0;
        }
        if (argc > 1 && std::string(argv[1]) == "--late-live-fill-disconnect-fails-closed") {
            IbPostFillReconciliationFixture boundary(false);
            boundary.lateLiveFillDuringRecoveryAuditDisconnectMustFailClosed();
            std::cout << "PASS disconnected live-fill boundary fails closed and retains journal callback; no order send\n";
            return 0;
        }
        IbPostFillReconciliationFixture f;
        if (argc > 1 && std::string(argv[1]) == "--pure-begin-deferred-audit") {
            f.deferredAuditAbortRestoresOriginalReady(false, true, true);
            std::cout << "PASS pure Begin defer preserved readiness ownership through original callbacks\n";
            return 0;
        }
        if (argc > 1 &&
            std::string(argv[1]) == "--deferred-audit-restores-ready") {
            f.deferredAuditAbortRestoresOriginalReady();
            return 0;
        }
        if (argc > 1 &&
            std::string(argv[1]) == "--deferred-audit-stop") {
            f.deferredAuditAbortRestoresOriginalReady(true);
            return 0;
        }
        if (argc > 1 &&
            std::string(argv[1]) == "--deferred-audit-originally-closed") {
            f.deferredAuditAbortRestoresOriginalReady(false, false);
            return 0;
        }
        if (argc > 1 &&
            std::string(argv[1]) == "--invalidated-flat-audit") {
            IbPostFillReconciliationFixture invalidated(false);
            invalidated.flatAuditInvalidatedBeforeDisconnectMustNotReconnect();
            std::cout << "PASS invalidated flat audit did not reconnect or mutate Broker\n";
            return 0;
        }
        if (argc > 1 &&
            std::string(argv[1]) == "--nonflat-owner-audit") {
            IbPostFillReconciliationFixture nonflat(false);
            nonflat.nonflatRejectedAuditMustNotScheduleReconnect();
            std::cout << "PASS nonflat rejected owner audit did not enqueue broker reconnect\n";
            return 0;
        }
        f.reproduceAdvancedExposure(
            !(argc > 1 && std::string(argv[1]) == "--expect-old-failure"));
        if (argc == 1) {
            {
                IbPostFillReconciliationFixture boundary(false);
                boundary.lateLiveFillDuringRecoveryAuditDisconnectMustFailClosed();
            }
            {
                IbPostFillReconciliationFixture pureBegin;
                pureBegin.deferredAuditAbortRestoresOriginalReady(false, true, true);
            }
            for (int k = 0; k < 3; ++k) {
                IbPostFillReconciliationFixture negative;
                negative.rejectIncompleteEvidence(k);
            }
            {
                IbPostFillReconciliationFixture deferred;
                deferred.deferredAuditAbortRestoresOriginalReady();
            }
            {
                IbPostFillReconciliationFixture stopped;
                stopped.deferredAuditAbortRestoresOriginalReady(true);
            }
            {
                IbPostFillReconciliationFixture closed;
                closed.deferredAuditAbortRestoresOriginalReady(false, false);
            }
            {
                IbPostFillReconciliationFixture invalidated(false);
                invalidated.flatAuditInvalidatedBeforeDisconnectMustNotReconnect();
            }
            {
                IbPostFillReconciliationFixture nonflat(false);
                nonflat.nonflatRejectedAuditMustNotScheduleReconnect();
            }
        }
        std::cout << "PASS post-fill, fixed timeout and recovery audit readiness; synthetic only\n";
        return 0;
    } catch (const std::exception& e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
}
