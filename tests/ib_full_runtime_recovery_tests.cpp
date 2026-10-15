// Full Runtime::Start -> real polling/reconnect/OMS/IPC -> Stop. Socket-free
// injected broker transport only. Synthetic fixtures, NOT live qualification.
#include "execution/ib_paper_execution_runtime_composition.h"
#include "execution/execution_coordinator.h"
#include "adapter_ib/ib_venue_correlation.h"
#include "adapter_ib/ib_callback_economics.h"
#include "adapter_ib/ib_admission_event_policy.h"
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <fstream>
#include <functional>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <sstream>
#include <string>
#include <thread>
#include <vector>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>
#include <cerrno>
#include <cstddef>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <linux/audit.h>
#include <sys/prctl.h>
#include <sys/syscall.h>

static void check(bool value,const std::string& message) {
    if(!value)throw std::runtime_error(message);
}
static std::uint64_t ms() {return OmsJournal::NowEpochMs();}
static void prohibitNetworkAndExec() {
#if defined(__x86_64__)
    constexpr unsigned arch=AUDIT_ARCH_X86_64;
#elif defined(__aarch64__)
    constexpr unsigned arch=AUDIT_ARCH_AARCH64;
#else
#error Unsupported architecture for the full-runtime isolation fixture
#endif
    // Kernel enforced, not a fake-wrapper claim. Only AF_UNIX socket creation
    // is permitted; no child executable can replace this isolated test.
    sock_filter code[]={
        BPF_STMT(BPF_LD|BPF_W|BPF_ABS,offsetof(seccomp_data,arch)),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,arch,1,0),
        BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_KILL_PROCESS),
        BPF_STMT(BPF_LD|BPF_W|BPF_ABS,offsetof(seccomp_data,nr)),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,__NR_execve,4,0),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,__NR_execveat,3,0),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,__NR_socket,0,3),
        BPF_STMT(BPF_LD|BPF_W|BPF_ABS,offsetof(seccomp_data,args[0])),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,AF_UNIX,1,0),
        BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_ERRNO|EPERM),
        BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_ALLOW)
    };
    sock_fprog program{static_cast<unsigned short>(sizeof(code)/sizeof(code[0])),code};
    check(prctl(PR_SET_NO_NEW_PRIVS,1,0,0,0)==0,"no_new_privs");
    check(prctl(PR_SET_SECCOMP,SECCOMP_MODE_FILTER,&program)==0,"kernel network isolation");
    check(::socket(AF_INET,SOCK_STREAM,0)==-1&&errno==EPERM,"IPv4 socket denied by kernel");
    check(::socket(AF_INET6,SOCK_STREAM,0)==-1&&errno==EPERM,"IPv6 socket denied by kernel");
}
static IBContractLite contract(bool btc) {
    IBContractLite c;c.symbol=btc?"BTC":"EUR";c.secType=btc?"CRYPTO":"CASH";
    c.exchange=btc?"PAXOS":"IDEALPRO";c.currency="USD";return c;
}
struct Shared {
    std::mutex mutex;
    std::deque<int> controls;
    std::deque<IBEvent> injectedErrors;
    std::map<std::string,unsigned> stages;
    std::atomic<unsigned> connectCount{0},orderCalls{0},accountCalls{0},positionCalls{0},openCalls{0},terminalCalls{0},execCalls{0};
    std::atomic<unsigned> injectedErrorsDequeued{0};
    std::atomic<unsigned> positionEndsQueued{0}, positionEndsDequeued{0};
    std::atomic<int> reconnectCode{1101};
    std::atomic<bool> omitPositions{false};
    std::atomic<int> cashBalanceOverride{0};
    std::atomic<bool> farmAcknowledged{true}, activeOnReconnect{false};
    std::atomic<bool> emitRetiredCashCancelErrors{false};
    bool seeded=false;
    void signal(int code){std::lock_guard<std::mutex> lock(mutex);controls.push_back(code);}
    void injectError(const IBEvent& event){std::lock_guard<std::mutex> lock(mutex);injectedErrors.push_back(event);}
    void stage(const char* name){std::lock_guard<std::mutex> lock(mutex);++stages[name];}
    unsigned count(const char* name){std::lock_guard<std::mutex> lock(mutex);return stages[name];}
};
class NeverEnableTrading final:public IbPaperKillSwitchReader {
public:IbPaperKillSwitchObservation Observe()const override {
    IbPaperKillSwitchObservation x;x.state=IbPaperKillSwitchState::Engaged;x.reasonCode="OFFLINE_FIXTURE_NO_TRADING";return x;
}};
class FullBroker final:public IIBApiWrapper {
    std::shared_ptr<Shared> s;
    bool connected=false,cashReady=true;
    std::uint64_t epoch=0,lastTicks=0;
    std::deque<IBEvent> events;
    std::map<int,IBContractLite> quotes;
    IBCashFarmAdmissionMarker farm;
    std::uint64_t sequence=0;
    IBEvent make(IBEventType type)const {
        IBEvent e;e.type=type;e.connectionEpoch=epoch;e.account="DU90001";e.brokerClientId=701;return e;
    }
    void control(int code) {
        auto e=make(IBEventType::Error);e.id=-1;e.key=std::to_string(code);
        e.value=(code==2103||code==2104)?"Market data farm:cashfarm":"Synthetic transport control";
        if(code==2103){cashReady=false;s->farmAcknowledged=false;farm.ObserveWarning(epoch,++sequence);}
        if(code==2104){cashReady=true;s->farmAcknowledged=true;farm.ObserveReady(epoch,++sequence);}
        events.push_back(e);
    }
public:
    explicit FullBroker(std::shared_ptr<Shared> v):s(v){}
    void SetConnectionEpoch(std::uint64_t e)override{epoch=e;farm.Reset(e);}
    std::uint64_t GetConnectionEpoch()const override{return epoch;}
    bool Connect(const IBConnectParams&)override {
        connected=true;++s->connectCount;control(2104);
        if(s->connectCount>1)control(s->reconnectCode.load());
        return true;
    }
    void Disconnect()override{connected=false;quotes.clear();events.clear();}
    bool IsConnected()const override{return connected;}
    const char* GetStatusString()const override{return "SYNTHETIC_FULL_RUNTIME_NO_SOCKET";}
    bool ReqAccountSummary()override {
        ++s->accountCalls;
        for(const auto& row:std::map<std::string,std::string>{{"AccountReady:","true"},{"NetLiquidation:USD","1000000"},{"CashBalance:EUR",s->cashBalanceOverride.load()!=0?std::to_string(s->cashBalanceOverride.load()):(s->seeded?"1025":"1000")}}) {
            auto e=make(IBEventType::AccountValue);e.key=row.first;e.value=row.second;events.push_back(e);
        }
        events.push_back(make(IBEventType::AccountSummaryEnd));return true;
    }
    bool ReqPositions()override {
        ++s->positionCalls;
        if(s->seeded){auto e=make(IBEventType::Position);e.key="BTC.USD";e.contract=contract(true);e.number=.01;events.push_back(e);}
        if(!s->omitPositions){events.push_back(make(IBEventType::PositionEnd));++s->positionEndsQueued;}return true;
    }
    bool ReqAllOpenOrders()override{
        ++s->openCalls;
        if(s->activeOnReconnect&&s->connectCount>1){
            auto e=make(IBEventType::OpenOrder);e.id=999;e.contract=contract(false);
            e.order.action="BUY";e.order.orderType="MKT";e.order.totalQuantity=1;e.number=1;e.value="Submitted";
            std::string reason;check(IbVenueCorrelationCodec::EncodeOrderRef("hepta-v1-sha256:"+std::string(64,'f'),e.order.orderRef,reason),"active correlation fixture");
            events.push_back(e);
        }
        events.push_back(make(IBEventType::OpenOrderEnd));return true;
    }
    bool ReqCompletedOrders()override {
        ++s->terminalCalls;
        if(s->seeded)for(int i=0;i<2;++i) {
            auto e=make(IBEventType::CompletedOrder);e.id=801+i;e.contract=contract(i==1);
            e.order.action="BUY";e.order.totalQuantity=i?.01:25;e.order.orderType="MKT";
            std::string reason;check(IbVenueCorrelationCodec::EncodeOrderRef("hepta-v1-sha256:"+std::string(64,i?'b':'a'),e.order.orderRef,reason),"encode synthetic correlation");
            e.key="Filled";e.number=i?80000:1.12;e.number2=e.order.totalQuantity;
            events.push_back(e);
        }
        events.push_back(make(IBEventType::CompletedOrdersEnd));return true;
    }
    bool ReqExecutions(int rid)override {
        ++s->execCalls;
        if(s->seeded)for(int i=0;i<2;++i) {
            auto e=make(IBEventType::ExecutionDetails);e.id=801+i;e.requestId=rid;e.key=i?"SYNTH-BTC-EXEC":"SYNTH-EUR-EXEC";
            e.value="BOT";e.contract=contract(i==1);e.number=i?80000:1.12;e.number2=i?.01:25;e.number3=0;
            e.remainingQuantityKnown=false;
            e.economics=CaptureIbExecutionEconomics(e.contract,i?222:111,900801+i,
                e.number2,e.number,e.number2,e.number,false);
            std::string reason;check(IbVenueCorrelationCodec::EncodeOrderRef("hepta-v1-sha256:"+std::string(64,i?'b':'a'),e.order.orderRef,reason),"encode historical execution");
            events.push_back(e);
        }
        auto e=make(IBEventType::ExecutionDetailsEnd);e.requestId=rid;events.push_back(e);return true;
    }
    bool ReqMktData(int id,const IBContractLite& c)override {
        if(c.secType=="CASH"&&!cashReady)return false;
        check(!quotes.count(id),"request must be unique");quotes[id]=c;lastTicks=0;return true;
    }
    bool CancelMktData(int id)override {
        const auto found=quotes.find(id);check(found!=quotes.end(),"cancel owned quote only");
        const auto cancelled=found->second;quotes.erase(found);
        if(s->emitRetiredCashCancelErrors&&cancelled.secType=="CASH") {
            // The fixture supplies the same provenance as the SDK's locally
            // sent/cancelled CASH ledger, only after this exact request was
            // actually retired. Four diagnostics exceed the ordinary fuse.
            for(unsigned i=0;i<4;++i) {
                auto e=make(IBEventType::Error);e.account.clear();
                e.id=e.requestId=id;e.key="300";e.contract=cancelled;
                e.localMarketDataRequestError=true;
                e.value="OFFLINE_RETIRED_CASH_REQUEST_300:"+std::to_string(i);
                events.push_back(e);
            }
        }
        return true;
    }
    bool PlaceOrder(long,const IBContractLite&,const IBOrderLite&)override {++s->orderCalls;throw std::runtime_error("NO_ORDER_TRANSPORT");}
    bool CancelOrder(long)override {++s->orderCalls;throw std::runtime_error("NO_CANCEL_TRANSPORT");}
    bool PollOnce(int)override {
        if(ms()-lastTicks>80) {
            lastTicks=ms();
            for(const auto& q:quotes)if(q.second.secType!="CASH"||cashReady)for(int leg=1;leg<=2;++leg){
                auto e=make(IBEventType::TickPrice);e.id=q.first;e.key=std::to_string(leg);
                e.number=q.second.secType=="CASH"?(leg==1?1.12:1.1201):(leg==1?80000:80001);events.push_back(e);
            }
        }
        {std::lock_guard<std::mutex> lock(s->mutex);while(!s->controls.empty()){control(s->controls.front());s->controls.pop_front();}
            while(!s->injectedErrors.empty()){events.push_back(s->injectedErrors.front());s->injectedErrors.pop_front();}}
        return true;
    }
    bool TryDequeueEvent(IBEvent& e)override {
        if(events.empty())return false;e=events.front();events.pop_front();
        if(e.type==IBEventType::PositionEnd)++s->positionEndsDequeued;
        const std::string injectedPrefix="OFFLINE_INJECTED:";
        if(e.type==IBEventType::Error&&e.value.compare(0,injectedPrefix.size(),injectedPrefix)==0)
            ++s->injectedErrorsDequeued;
        return true;
    }
    long GetLastValidOrderId()const override{return 900;}
};
static int listener(const std::string& path) {
    int fd=::socket(AF_UNIX,SOCK_STREAM,0);check(fd>=0,"unix fixture socket");sockaddr_un a{};a.sun_family=AF_UNIX;
    check(path.size()<sizeof(a.sun_path),"unix path length");std::strcpy(a.sun_path,path.c_str());
    check(::bind(fd,reinterpret_cast<sockaddr*>(&a),sizeof(a))==0&&::listen(fd,8)==0,"unix fixture listen");return fd;
}
static void writePrivate(const std::string& path,const std::string& text) {
    std::ofstream f(path,std::ios::binary|std::ios::trunc);
    check(f.is_open(),"fixture credential create failed");
    f.write(text.data(),static_cast<std::streamsize>(text.size()));
    f.flush();
    check(f.good(),"fixture credential write failed (temporary storage unavailable)");
    f.close();
    check(!f.fail(),"fixture credential close failed");
    check(::chmod(path.c_str(),0400)==0,"credential mode");
}
struct Fixture {
    std::string dir;std::shared_ptr<Shared> shared=std::make_shared<Shared>();
    std::unique_ptr<IbPaperExecutionRuntimeComposition> runtime;
    IbPaperExecutionRuntimeConfig savedConfig;
    explicit Fixture(bool seeded=false,bool unknown=false) {
        // Test-only storage choice: do not depend on another task's /tmp capacity.
        // Production credential/control paths and validation remain unchanged.
        const char* configured=::getenv("TMPDIR");
        std::string temporaryRoot=configured&&*configured?configured:"/tmp";
        check(temporaryRoot[0]=='/',"fixture TMPDIR must be absolute");
        std::string tmpl=temporaryRoot+"/hepta-full-XXXXXX";
        auto p=::mkdtemp(&tmpl[0]);check(p,"private fixture directory");dir=p;
        check(::mkdir((dir+"/state").c_str(),0700)==0 && ::mkdir((dir+"/cred").c_str(),0700)==0,"fixture dirs");shared->seeded=seeded;
        IbPaperExecutionRuntimeConfig c;c.mode=IbPaperExecutionRuntimeMode::Paper;c.profile.enabled=true;c.profile.userScope=true;c.profile.observationOnly=false;
        c.profile.account="DU90001";c.profile.host="127.0.0.1";c.profile.port=4002;c.profile.clientId=701;
        c.profile.stateDirectory=c.stateDirectory=dir+"/state";c.journalPath=c.stateDirectory+"/oms-journal.jsonl";
        c.profile.controlDirectory=c.controlDirectory=IbPaperExecutionProfileConfig::UserControlDirectoryPath();
        c.profile.authorizationCredentialPath=c.authorizationCredentialPath=dir+"/cred/"+IbPaperExecutionProfileConfig::AuthorizationCredentialName();
        c.fenceCredentialPath=dir+"/cred/"+IbPaperExecutionRuntimeConfig::FenceCredentialName();
        c.fxCashBaselineCredentialPath=dir+"/cred/"+IbPaperExecutionRuntimeConfig::FxCashBaselineCredentialName();
        c.profile.userQuoteContracts="BTC.USD|BTC|CRYPTO|PAXOS|USD;EUR.USD|EUR|CASH|IDEALPRO|USD";
        c.primaryQuoteInstrument=c.profile.userPrimaryQuoteInstrument="BTC.USD";
        c.profile.userCryptoAgentId="fixture-btc";c.profile.userCryptoSessionId="session-btc";
        c.profile.userQuoteMaxAgeMs=c.quoteMaxAgeMs=5000;c.profile.maxOrderQuantity=100000;c.profile.maxOrderNotional=100000;
        c.profile.maxGrossPosition=100000;c.profile.maxActiveOrders=1;c.profile.maxOrdersPerMinute=2;
        for(bool btc:{false,true}){auto v=contract(btc);InstrumentRef q;q.symbol=v.symbol;q.secType=v.secType;q.exchange=v.exchange;q.currency=v.currency;c.quoteContracts[btc?"BTC.USD":"EUR.USD"]=q;}
        c.allowedGatewayUids.insert(::geteuid());c.gatewayContextBinding.agentId="fixture-fx";c.gatewayContextBinding.account=c.profile.account;
        c.gatewayContextBinding.venue="IB";c.gatewayContextBinding.executionDomain="PAPER";
        c.listenFd=listener(dir+"/rpc");c.eventListenFd=listener(dir+"/events");
        c.readinessTimeoutMs=1000;c.initialConnectTimeoutMs=1000;c.reconnectTimeoutMs=3000;
        IbPaperFxCashBaseline baseline;baseline.account=c.profile.account;baseline.currency="EUR";baseline.instrument="EUR.USD";
        baseline.baselineCashBalance=1000;baseline.observedCashBalance=seeded?1025:1000;baseline.campaignExecutionDelta=seeded?25:0;
        baseline.observedAtMs=ms();baseline.proof="sha256:"+std::string(64,'e');c.fxCashBaselines["EUR.USD"]=baseline;
        // Existing fixture commands precede the *original* synthetic cash
        // checkpoint by construction. Using ms() a second time here made
        // their classification depend on whether a slow CI worker crossed
        // a millisecond boundary (rather than testing post-checkpoint fills).
        const std::int64_t historicalEventTsMs =
            static_cast<std::int64_t>(baseline.observedAtMs) - 1000;
        check(historicalEventTsMs > 0, "synthetic historical command timestamp");
        std::string reason,auth;check(c.profile.BuildAuthorizationCredential(auth,reason),"auth fixture: "+reason);
        writePrivate(c.authorizationCredentialPath,auth+"\n");writePrivate(c.fenceCredentialPath,"HFC1\nfencing_token=912\ngeneration=5\n");
        if(seeded){OmsJournal j;check(j.Init(c.journalPath),"seed journal");for(int i=0;i<2;++i){
            OmsJournalEvent e;e.schemaVersion=4;e.eventType="order_intent";e.tsMs=historicalEventTsMs;e.reqId=i?"original-btc":"original-eur";
            e.clientReqId=e.reqId;e.traceId=i?"session-btc":"session-eur";e.eventId=e.reqId+":intent";e.source=i?"agent.tool:fixture-btc":"agent.tool:fixture-fx";
            e.account=c.profile.account;e.venue="IB";e.executionDomain="PAPER";e.instrument=i?"BTC.USD":"EUR.USD";e.side="BUY";
            e.qty=i?.01:25;e.price=i?80000:1.12;e.status="intent_recorded";e.requestHash="sha256:"+std::string(64,i?'c':'d');
            e.venueCorrelationId="hepta-v1-sha256:"+std::string(64,i?'b':'a');check(j.Append(e),"intent");
            e.eventType="place_send_attempt";e.eventId=e.reqId+":attempt";e.status="send_attempt";check(j.Append(e),"attempt");
            e.eventType="place_sent";e.eventId=e.reqId+":sent";e.status="submitted";e.orderId=801+i;check(j.Append(e),"sent");
        }}
        if(unknown){
            OmsJournal j;check(j.Init(c.journalPath),"unknown original journal");
            OmsJournalEvent e;e.schemaVersion=4;e.eventType="order_intent";e.tsMs=historicalEventTsMs;e.reqId="original-unknown";
            e.clientReqId=e.reqId;e.traceId="session-unknown";e.eventId="unknown:intent";e.source="agent.tool:fixture-fx";
            e.account=c.profile.account;e.venue="IB";e.executionDomain="PAPER";e.instrument="EUR.USD";e.side="BUY";
            e.qty=25;e.price=1.12;e.status="intent_recorded";e.requestHash="sha256:"+std::string(64,'7');
            e.venueCorrelationId="hepta-v1-sha256:"+std::string(64,'8');check(j.Append(e),"unknown original intent");
            e.eventType="place_send_attempt";e.eventId="unknown:attempt";e.status="send_attempt";check(j.Append(e),"unknown original attempt");
        }
        savedConfig=c;start(c);
    }
    void start(const IbPaperExecutionRuntimeConfig& c) {
        std::shared_ptr<Shared> state=shared;
        IbPaperExecutionRuntimeTestHooks hooks;hooks.onStage=[state](const char* name){state->stage(name);};
        hooks.reconnectApiFactory=[state](){return std::unique_ptr<IIBApiWrapper>(new FullBroker(state));};
        runtime.reset(new IbPaperExecutionRuntimeComposition(c,std::unique_ptr<IIBApiWrapper>(new FullBroker(shared)),hooks,std::make_shared<NeverEnableTrading>()));
        std::string reason;const bool started=runtime->Start(reason);
        check(started,"FULL_RUNTIME_START: "+reason);
        check(runtime->IsRunning(),"real IPC/poller ready");
    }
    ~Fixture(){if(runtime)runtime->Stop();}
    std::vector<std::pair<int,long>> originalCommands() {
        std::vector<std::pair<int,long>> statuses;
        for(int i=0;i<2;++i){ExecutionCommandResult status;
            check(runtime->Coordinator().GetCommandStatus(i?"fixture-btc":"fixture-fx",i?"session-btc":"session-eur",i?"original-btc":"original-eur",status),"original command/owner present");
            check(status.orderId==801+i,"original order number retained");
            statuses.push_back({static_cast<int>(status.status),status.orderId});
        }
        return statuses;
    }
    void restartSameJournal() {
        const auto prior=originalCommands();const auto oldService=runtime->ServiceEpoch();
        runtime->Stop();runtime.reset();
        ::unlink((dir+"/rpc").c_str());::unlink((dir+"/events").c_str());
        auto c=savedConfig;c.listenFd=listener(dir+"/rpc");c.eventListenFd=listener(dir+"/events");
        start(c);
        check(runtime->ServiceEpoch()!=oldService,"new runtime service epoch");
        check(originalCommands()==prior,"same original command statuses after full reconstruction");
        const auto pos=runtime->Adapter().GetAuthoritativePositionQuantities();
        check(pos.at("EUR.USD")==25&&pos.at("BTC.USD")==.01,"positions survive full runtime reconstruction");
        check(shared->orderCalls==0,"no order methods during reconstruction");
        std::ifstream input(savedConfig.journalPath);std::string line;unsigned intents=0,sends=0,attempts=0;
        std::map<std::string,long> permanent;
        while(std::getline(input,line)){OmsJournalEvent e;check(OmsJournal::ParseJsonLine(line,e),"durable journal parse");
            intents+=e.eventType=="order_intent";sends+=e.eventType=="place_sent";attempts+=e.eventType=="place_send_attempt";
            if(e.eventType=="broker_execution"){
                check(e.brokerEconomics.valid==1&&e.brokerEconomics.ValidShape(),"retained exact economic callback");
                const long perm=e.brokerEconomics.permanentOrderId;
                check(perm==(e.brokerExecutionId=="SYNTH-BTC-EXEC"?900802:900801),"permanent broker identity retained");
                auto found=permanent.find(e.brokerExecutionId);
                check(found==permanent.end()||found->second==perm,"replayed execution cannot change permanent ID");
                permanent[e.brokerExecutionId]=perm;
            }
        }
        check(intents==2&&sends==2&&attempts==2,"reconstruction cannot create new sends or intents");
        check(permanent.size()==2,"both original execution identities retained after restart");
    }
    void injectAccountValue(const std::string& key,const std::string& value) {
        IBEvent event;event.type=IBEventType::AccountValue;
        event.connectionEpoch=runtime->Adapter().GetConnectionEpoch();
        event.account="DU90001";event.brokerClientId=701;event.key=key;event.value=value;
        shared->injectError(event); // injected native event, no Broker socket
    }
    void cashDriftOrdinaryRefresh(bool missingPositions=false) {
        const auto before=runtime->Adapter().GetAuthoritativeRecoveryAuditSnapshot();
        check(before.risk.complete&&before.active.complete&&before.terminal.complete,
              "cash fixture starts with complete original evidence");
        const auto accountCalls=shared->accountCalls.load(),positionCalls=shared->positionCalls.load();
        const auto epoch=runtime->Adapter().GetConnectionEpoch();
        const int original=shared->seeded?1025:1000;
        shared->cashBalanceOverride=original+1;shared->omitPositions=missingPositions;
        injectAccountValue("CashBalance:EUR",std::to_string(original+1));
        const auto until=ms()+2200;
        while(ms()<until&&shared->count("cash_drift_ordinary_risk_dispatched")==0&&
              !runtime->HasFatalRuntimeError())std::this_thread::sleep_for(std::chrono::milliseconds(5));
        check(shared->count("cash_drift_ordinary_risk_dispatched")==1,
              "CASH_DRIFT_ORDINARY_REFRESH_NOT_DISPATCHED");
        const auto completeUntil=ms()+1000;
        while(!missingPositions&&ms()<completeUntil&&
              !runtime->Adapter().GetAuthoritativeRiskSnapshot().complete)
            std::this_thread::sleep_for(std::chrono::milliseconds(5));
        auto after=runtime->Adapter().GetAuthoritativeRecoveryAuditSnapshot();
        check(shared->accountCalls==accountCalls+1&&shared->positionCalls==positionCalls+1,
              "cash drift dispatches exactly one account/positions cohort");
        check(!after.barrierComplete,"ordinary cash refresh never clears owner recovery audit");
        if(missingPositions) {
            check(!after.risk.complete,"missing positions End cannot complete ordinary cash risk");
        } else {
            check(after.risk.complete&&after.risk.coherentRefreshComplete&&after.risk.fxCashComplete,
                  "actual new account/position callbacks complete ordinary risk");
            check(after.reasonCode=="IB_FX_CASH_BALANCE_OUT_OF_GENERATION_CHANGE",
                  "sticky owner audit reason remains unchanged");
            check(after.risk.riskAbsorbedExposureGeneration==before.exposureGeneration,
                  "ordinary refresh does not invent economic exposure");
            if(shared->seeded) {
                check(after.positionQuantities.at("BTC.USD")==.01,
                      "completed cash refresh preserves original physical BTC position");
            }
        }
        // The old delayed balance is genuinely different, not a no-op duplicate.
        // Once this epoch/exposure pair was dispatched it cannot start a storm.
        for(int i=0;i<8;++i)injectAccountValue("CashBalance:EUR",std::to_string(original));
        std::this_thread::sleep_for(std::chrono::milliseconds(1250));
        after=runtime->Adapter().GetAuthoritativeRecoveryAuditSnapshot();
        check(shared->accountCalls==accountCalls+1&&shared->positionCalls==positionCalls+1&&
              shared->count("cash_drift_ordinary_risk_dispatched")==1,
              "repeated delayed balance callbacks must not dispatch more cohorts");
        check(!after.barrierComplete&&runtime->Adapter().GetConnectionEpoch()==epoch&&
              shared->orderCalls==0&&runtime->IsRunning()&&!runtime->HasFatalRuntimeError(),
              "ordinary repair neither reconnects nor orders nor grants owner audit");
        if(shared->seeded) originalCommands();
    }
    void cashRefreshDoesNotBypassOtherHolds(bool unknown) {
        const auto accountCalls=shared->accountCalls.load(),positionCalls=shared->positionCalls.load();
        if(unknown)check(runtime->Coordinator().IsMutationBlocked(),"uncertain original command hold exists");
        shared->cashBalanceOverride=1001;
        injectAccountValue(unknown?"CashBalance:EUR":"AccountReady:",unknown?"1001":"false");
        const auto until=ms()+1000;
        while(ms()<until&&runtime->Adapter().GetAuthoritativeRiskSnapshot().complete)
            std::this_thread::sleep_for(std::chrono::milliseconds(5));
        check(!runtime->Adapter().GetAuthoritativeRiskSnapshot().complete,"negative native callback invalidates risk");
        std::this_thread::sleep_for(std::chrono::milliseconds(1250));
        check(shared->count("cash_drift_ordinary_risk_dispatched")==0&&
              shared->accountCalls==accountCalls&&shared->positionCalls==positionCalls,
              "uncertain command or non-cash risk failure cannot acquire refresh eligibility");
        check(!runtime->Adapter().GetAuthoritativeRecoveryAuditSnapshot().barrierComplete&&shared->orderCalls==0,
              "negative evidence stays held without any order method");
    }
    void unknownOutcomeSurvivesReconstruction() {
        for(int pass=0;pass<2;++pass){
            ExecutionCommandResult status;
            check(runtime->Coordinator().GetCommandStatus("fixture-fx","session-unknown","original-unknown",status),"unknown command retained under original owner");
            check(status.status==ExecutionCommandStatus::Uncertain&&runtime->Coordinator().IsMutationBlocked(),"missing execution is not proof of zero-fill");
            check(shared->orderCalls==0,"no blind resend for uncertain original");
            if(pass==0){runtime->Stop();runtime.reset();::unlink((dir+"/rpc").c_str());::unlink((dir+"/events").c_str());
                auto c=savedConfig;c.listenFd=listener(dir+"/rpc");c.eventListenFd=listener(dir+"/events");start(c);}
        }
        std::ifstream input(savedConfig.journalPath);std::string line;unsigned intents=0,attempts=0,sent=0;
        while(std::getline(input,line)){OmsJournalEvent e;check(OmsJournal::ParseJsonLine(line,e),"unknown journal parse");
            intents+=e.eventType=="order_intent";attempts+=e.eventType=="place_send_attempt";sent+=e.eventType=="place_sent";}
        check(intents==1&&attempts==1&&sent==0,"uncertain restart may not invent sends or replacement commands");
    }
    void activeOrderFailsClosed() {
        shared->activeOnReconnect=true;shared->signal(1100);const auto deadline=ms()+6000;
        while(ms()<deadline&&!runtime->HasFatalRuntimeError())std::this_thread::sleep_for(std::chrono::milliseconds(5));
        std::string reason;check(runtime->HasFatalRuntimeError(&reason),"active broker order blocks recovery");
        check(reason=="IB_PAPER_BROKER_RECONNECT_ACTIVE_ORDERS_UNSAFE","active order exact recovery gate: "+reason);
        check(!runtime->IsRunning()&&shared->orderCalls==0,"active order not cancelled or overwritten to pass");
    }
    void missingPositionsFailsClosed() {
        const unsigned completed=shared->count("broker_reconnect_complete");
        shared->omitPositions=true;shared->signal(1100);
        const auto deadline=ms()+6000;
        while(ms()<deadline&&!runtime->HasFatalRuntimeError())std::this_thread::sleep_for(std::chrono::milliseconds(5));
        std::string reason;check(runtime->HasFatalRuntimeError(&reason),"missing positions must remain fatal/unready");
        check(!runtime->IsRunning()&&shared->count("broker_reconnect_complete")==completed,"incomplete barrier cannot publish reconnect complete");
        check(shared->orderCalls==0,"missing positions cannot trigger orders");
        std::cout<<"FULL_RUNTIME_MISSING_POSITIONS_FAIL_CLOSED "<<reason<<std::endl;
    }
    void secondaryFarmRecovery() {
        const auto epoch=runtime->Adapter().GetConnectionEpoch();
        const int primary=runtime->QuoteHealthForObservation().contracts.at("BTC.USD").requestId;
        shared->signal(2103);auto deadline=ms()+1000;
        while(shared->farmAcknowledged&&ms()<deadline)std::this_thread::sleep_for(std::chrono::milliseconds(5));
        check(!shared->farmAcknowledged,"real poll consumed synthetic farm loss");
        std::this_thread::sleep_for(std::chrono::milliseconds(5200));
        auto before=runtime->QuoteHealthForObservation();const auto& eq=before.contracts.at("EUR.USD").quote;
        check(!eq.HasQuote()||ms()-eq.bidObservedAtMs>5000||ms()-eq.askObservedAtMs>5000,"lost farm EUR remains unready");
        check(runtime->IsRunning()&&!runtime->HasFatalRuntimeError(),"secondary farm must not kill primary runtime");
        shared->signal(2104);deadline=ms()+1500;bool restored=false;
        while(ms()<deadline){auto current=runtime->QuoteHealthForObservation();const auto& q=current.contracts.at("EUR.USD").quote;
            restored=q.HasQuote()&&q.bidObservedAtMs>eq.bidObservedAtMs&&q.askObservedAtMs>eq.askObservedAtMs&&ms()-q.bidObservedAtMs<=5000&&ms()-q.askObservedAtMs<=5000;
            if(restored)break;std::this_thread::sleep_for(std::chrono::milliseconds(5));}
        check(restored,"farm restoration needs two newer legs");
        check(runtime->Adapter().GetConnectionEpoch()==epoch&&runtime->QuoteHealthForObservation().contracts.at("BTC.USD").requestId==primary,"unrelated BTC ownership survives secondary farm loss");
        check(shared->orderCalls==0,"farm recovery never orders");
    }
    void reconnect(int restore) {
        auto oldEpoch=runtime->Adapter().GetConnectionEpoch();auto count=shared->count("broker_reconnect_complete");
        shared->reconnectCode=restore;shared->signal(1100);
        const auto until=ms()+6000;while(ms()<until&&shared->count("broker_reconnect_complete")==count&&!runtime->HasFatalRuntimeError())std::this_thread::sleep_for(std::chrono::milliseconds(5));
        std::string reason;runtime->HasFatalRuntimeError(&reason);
        if(shared->count("broker_reconnect_complete")!=count+1) {
            const auto audit=runtime->Adapter().GetAuthoritativeRecoveryAuditSnapshot();
            std::ostringstream diagnostic;
            diagnostic<<"FULL_RECONNECT_FAILED: "<<reason
                      <<" epoch="<<runtime->Adapter().GetConnectionEpoch()
                      <<" account_calls="<<shared->accountCalls.load()
                      <<" position_calls="<<shared->positionCalls.load()
                      <<" position_ends_queued="<<shared->positionEndsQueued.load()
                      <<" position_ends_dequeued="<<shared->positionEndsDequeued.load()
                      <<" risk="<<audit.risk.reasonCode
                      <<" account_complete="<<audit.risk.accountComplete
                      <<" positions_complete="<<audit.risk.positionsComplete
                      <<" active_complete="<<audit.active.complete
                      <<" terminal_complete="<<audit.terminal.complete
                      <<" barrier_complete="<<audit.barrierComplete
                      <<" reconnect_refresh_dispatched="<<shared->count("broker_reconnect_refresh_dispatched")
                      <<" reconnect_risk_dispatched="<<shared->count("broker_reconnect_recovery_risk_dispatched");
            throw std::runtime_error(diagnostic.str());
        }
        check(runtime->Adapter().GetConnectionEpoch()>oldEpoch,"full adapter epoch advanced");
        check(shared->orderCalls==0,"reconnect has no order methods");
        const auto audit=runtime->Adapter().GetAuthoritativeRecoveryAuditSnapshot();
        check(audit.barrierComplete&&audit.risk.accountComplete&&audit.risk.positionsComplete&&audit.risk.fxCashComplete&&audit.active.complete&&audit.terminal.complete,"ALL_RECOVERY_BARRIERS");
    }
    unsigned retainedErrorCount(long requestId,const std::string& messagePrefix) {
        std::ifstream input(savedConfig.journalPath);std::string line;unsigned count=0;
        while(std::getline(input,line)) {
            // A concurrent append may leave its last line incomplete. It is
            // never accepted as evidence; the bounded caller retries it.
            OmsJournalEvent e;if(!OmsJournal::ParseJsonLine(line,e)) {
                check(input.eof(),"malformed committed OMS error receipt");break;
            }
            if(e.eventType!="broker_error"||e.brokerRequestId!=requestId||
               e.brokerMessage.compare(0,messagePrefix.size(),messagePrefix)!=0)continue;
            check(e.brokerCallbackType=="error"&&
                  e.status=="Error"&&e.brokerClientId>=-1,
                  "raw request error identity retained");
            ++count;
        }
        return count;
    }
    void waitForRetainedErrors(long requestId,const std::string& prefix,unsigned expected) {
        const auto deadline=ms()+1500;
        while(ms()<deadline&&retainedErrorCount(requestId,prefix)<expected&&
              !runtime->HasFatalRuntimeError())
            std::this_thread::sleep_for(std::chrono::milliseconds(5));
        check(retainedErrorCount(requestId,prefix)==expected,"exact raw error callbacks durably retained");
    }
    void localCancelledCashErrorsPreserveEconomicRecovery() {
        const auto before=runtime->Adapter().GetAuthoritativeRecoveryAuditSnapshot();
        check(before.barrierComplete&&before.active.complete&&before.terminal.complete&&
              before.risk.complete&&before.risk.coherentRefreshComplete&&
              !before.postFillRiskReconciliationPending,"complete economic barrier before market-data diagnostic");
        const auto originals=originalCommands();
        const auto quotesBefore=runtime->QuoteHealthForObservation();
        const int btc=quotesBefore.contracts.at("BTC.USD").requestId;
        const int eur=quotesBefore.contracts.at("EUR.USD").requestId;
        const int extraCashRequest=1000901;
        check(extraCashRequest!=btc&&extraCashRequest!=eur,"isolated extra CASH ticker identity");
        check(!runtime->Adapter().IsCircuitBreakerTripped(),"error fuse initially clear");
        // No current subscription is replaced. The real adapter dispatches
        // and cancels a separate locally owned ticker; its late 300 callbacks
        // then traverse the complete Runtime poll/adapter/OMS path.
        check(runtime->Adapter().ReqMktData(extraCashRequest,contract(false)),"extra CASH request actually dispatched");
        shared->emitRetiredCashCancelErrors=true;
        check(runtime->Adapter().CancelMktData(extraCashRequest),"exact extra CASH ticker actually cancelled");
        shared->emitRetiredCashCancelErrors=false;
        waitForRetainedErrors(extraCashRequest,"OFFLINE_RETIRED_CASH_REQUEST_300:",4);
        const auto after=runtime->Adapter().GetAuthoritativeRecoveryAuditSnapshot();
        check(runtime->IsRunning()&&!runtime->HasFatalRuntimeError()&&after.barrierComplete&&
              after.reasonCode.empty()&&!after.newConnectionEpochRequired,
              "known retired CASH errors preserve recovery barrier");
        check(after.active.complete&&after.active.connectionEpoch==before.active.connectionEpoch&&
              after.active.generation==before.active.generation&&
              after.active.activeOrderIds==before.active.activeOrderIds&&
              after.active.activeOrderIdsByCorrelation==before.active.activeOrderIdsByCorrelation,
              "market-data diagnostic cannot change active-order proof");
        check(after.terminal.complete&&after.terminal.connectionEpoch==before.terminal.connectionEpoch&&
              after.terminal.generation==before.terminal.generation&&
              after.terminal.terminalOrderIdsByCorrelation==before.terminal.terminalOrderIdsByCorrelation&&
              after.terminal.executionCorrelations==before.terminal.executionCorrelations,
              "market-data diagnostic cannot change terminal economic proof");
        check(after.risk.complete&&after.risk.coherentRefreshComplete&&after.risk.accountComplete&&
              after.risk.positionsComplete&&after.risk.fxCashComplete&&
              after.risk.connectionEpoch==before.risk.connectionEpoch&&
              after.risk.generation==before.risk.generation&&
              after.risk.accountGeneration==before.risk.accountGeneration&&
              after.risk.positionsGeneration==before.risk.positionsGeneration&&
              after.risk.fxCashGeneration==before.risk.fxCashGeneration&&
              after.risk.grossAbsolutePosition==before.risk.grossAbsolutePosition&&
              after.positionQuantities==before.positionQuantities&&
              after.exposureGeneration==before.exposureGeneration&&
              after.terminalExposureGeneration==before.terminalExposureGeneration&&
              after.riskAbsorbedExposureGeneration==before.riskAbsorbedExposureGeneration&&
              !after.postFillRiskReconciliationPending,
              "all economic generations and nonzero FX/BTC positions stay unchanged");
        check(!runtime->Adapter().IsCircuitBreakerTripped(),"four scoped market-data errors do not accumulate ordinary fuse");
        check(originalCommands()==originals&&shared->orderCalls==0,"no order ownership or send affected by ticker cancellation");
        const auto quotesAfter=runtime->QuoteHealthForObservation();
        check(quotesAfter.contracts.at("BTC.USD").requestId==btc&&
              quotesAfter.contracts.at("EUR.USD").requestId==eur&&
              quotesAfter.contracts.at("BTC.USD").quote.HasQuote()&&
              quotesAfter.contracts.at("EUR.USD").quote.HasQuote(),
              "retired third ticker cannot replace either current quote");
        std::ifstream obs(savedConfig.stateDirectory+"/ib-observability.jsonl");
        std::string line;unsigned localDiagnostics=0;
        while(std::getline(obs,line))if(line.find("\"event\":\"market_data.local_request_failure\"")!=std::string::npos&&
                                      line.find("\"request_id\":"+std::to_string(extraCashRequest))!=std::string::npos)
            ++localDiagnostics;
        check(localDiagnostics==4,"each raw local failure also emits its market-data diagnostic");
        std::ifstream raw(savedConfig.journalPath);unsigned exactRawErrors=0;
        const std::string localErrorPrefix="OFFLINE_RETIRED_CASH_REQUEST_300:";
        while(std::getline(raw,line)) {
            OmsJournalEvent e;if(!OmsJournal::ParseJsonLine(line,e)) {
                check(raw.eof(),"malformed committed local error receipt");break;
            }
            if(e.eventType!="broker_error"||e.brokerRequestId!=extraCashRequest)continue;
            check(e.orderId==extraCashRequest&&e.brokerErrorCode==300&&e.brokerClientId==701&&
                  e.brokerConnectionEpoch==before.risk.connectionEpoch&&
                  e.source=="ib-api-callback"&&e.account.empty()&&e.reqId.empty()&&
                  e.brokerAdvancedOrderRejectJson.empty()&&
                  e.brokerMessage.compare(0,localErrorPrefix.size(),localErrorPrefix)==0,
                  "local cancellation diagnostic retained without attributing it to an order or account");
            ++exactRawErrors;
        }
        check(exactRawErrors==4,"all four original local callbacks retained in OMS");
    }
    void unscopedSecDefFarmLossRetainsEconomicRecovery() {
        // Actual 2157/-1 at 2026-10-14 23:45:48 (+08): a disconnected
        // SECURITY-DEFINITION farm, NOT a live CASH quote rejection.
        const auto before=runtime->Adapter().GetAuthoritativeRecoveryAuditSnapshot();
        check(before.barrierComplete && before.risk.complete &&
              before.risk.fxCashComplete, "unscoped farm starts with true economic recovery");
        const auto originals=originalCommands();
        const auto primary=runtime->QuoteHealthForObservation().contracts.at("BTC.USD").requestId;
        const auto epoch=runtime->Adapter().GetConnectionEpoch();
        const auto accountCalls=shared->accountCalls.load();
        const auto positionCalls=shared->positionCalls.load();
        IBEvent error;error.type=IBEventType::Error;error.id=-1;
        error.requestId=0;error.account.clear();error.brokerClientId=701;
        error.connectionEpoch=epoch;error.key="2157";
        error.value="OFFLINE_INJECTED:SEC_DEF_FARM_CONNECTION_BROKEN";
        shared->injectError(error);
        waitForRetainedErrors(0,error.value,1);
        const auto after=runtime->Adapter().GetAuthoritativeRecoveryAuditSnapshot();
        check(runtime->IsRunning() && !runtime->HasFatalRuntimeError() &&
              runtime->Adapter().IsEventStreamAuthoritative() &&
              after.barrierComplete && after.reasonCode.empty(),
              "unscoped 2157 must not poison a previously valid live stream");
        check(after.risk.generation==before.risk.generation &&
              after.active.generation==before.active.generation &&
              after.terminal.generation==before.terminal.generation &&
              after.exposureGeneration==before.exposureGeneration &&
              after.positionQuantities==before.positionQuantities &&
              shared->accountCalls==accountCalls &&
              shared->positionCalls==positionCalls,
              "global sec-def notice cannot change economic snapshots");
        check(runtime->QuoteHealthForObservation().contracts.at("BTC.USD").requestId==primary &&
              originalCommands()==originals && shared->orderCalls==0,
              "unscoped farm event cannot replace quote ownership or submit orders");
        std::ifstream obs(savedConfig.stateDirectory+"/ib-observability.jsonl");
        std::string row;unsigned notices=0;
        while(std::getline(obs,row))
            notices += row.find("\"event\":\"broker.secdef_farm_unavailable_unscoped\"")!=std::string::npos;
        check(notices==1,"raw 2157 remains visible as a scoped native diagnostic");
    }
    void uncorroboratedMarketDataErrorClosesRecovery(IBEvent event) {
        const auto before=runtime->Adapter().GetAuthoritativeRecoveryAuditSnapshot();
        check(before.barrierComplete,"negative error fixture starts with complete barrier");
        event.value="OFFLINE_INJECTED:UNCORROBORATED_MARKET_DATA_ERROR";
        const auto originals=originalCommands();shared->injectError(event);
        waitForRetainedErrors(event.requestId,event.value,1);
        const auto after=runtime->Adapter().GetAuthoritativeRecoveryAuditSnapshot();
        check(!after.barrierComplete&&after.reasonCode=="IB_RECOVERY_AUDIT_LATE_BROKER_EVENT",
              "unknown or wrong-scoped request error cannot keep economic recovery authorized");
        check(after.positionQuantities==before.positionQuantities&&
              after.exposureGeneration==before.exposureGeneration&&
              originalCommands()==originals&&shared->orderCalls==0,
              "refusing diagnostic cannot invent a fill, clear position, or resend original order");
    }
    void differentEpochMarketDataErrorsCannotAuthorizeCurrentEpoch() {
        reconnect(1101);const auto before=runtime->Adapter().GetAuthoritativeRecoveryAuditSnapshot();
        const auto current=runtime->Adapter().GetConnectionEpoch();
        check(current>1&&before.barrierComplete,"old nonzero epoch exists for boundary regression");
        const unsigned consumed=shared->injectedErrorsDequeued.load();
        for(const auto epoch:{current-1,current+1}) {
            IBEvent e;e.type=IBEventType::Error;e.key="300";e.id=e.requestId=1000901;
            e.connectionEpoch=epoch;e.brokerClientId=701;e.contract=contract(false);
            e.localMarketDataRequestError=true;e.value="OFFLINE_INJECTED:DIFFERENT_EPOCH_MARKET_DATA_ERROR";
            shared->injectError(e);
        }
        const auto deadline=ms()+1500;
        while(ms()<deadline&&shared->injectedErrorsDequeued<consumed+2)
            std::this_thread::sleep_for(std::chrono::milliseconds(5));
        check(shared->injectedErrorsDequeued==consumed+2,"real adapter consumed both old/future callbacks");
        const auto after=runtime->Adapter().GetAuthoritativeRecoveryAuditSnapshot();
        check(after.barrierComplete&&after.risk.connectionEpoch==current&&
              after.risk.generation==before.risk.generation&&
              after.positionQuantities==before.positionQuantities&&
              after.exposureGeneration==before.exposureGeneration&&
              !runtime->Adapter().IsCircuitBreakerTripped()&&shared->orderCalls==0,
              "existing epoch filter quarantines errors rather than reinterpreting them as current proof");
        check(retainedErrorCount(1000901,"OFFLINE_INJECTED:DIFFERENT_EPOCH_MARKET_DATA_ERROR")==0,
              "wrong-epoch diagnostic is not relabelled as a current OMS callback");
        std::ifstream obs(savedConfig.stateDirectory+"/ib-observability.jsonl");
        std::string line;unsigned quarantined=0;
        while(std::getline(obs,line))if(line.find("\"event\":\"event.stale_connection_epoch\"")!=std::string::npos)
            ++quarantined;
        check(quarantined>=2,"old and future callback epochs retain quarantine diagnostics");
    }
};
static IBEvent corroboratedCashError(std::uint64_t epoch,const std::string& code) {
    IBEvent e;e.type=IBEventType::Error;e.key=code;e.id=e.requestId=1000901;
    e.connectionEpoch=epoch;e.brokerClientId=701;e.contract=contract(false);
    e.localMarketDataRequestError=true;return e;
}
static void unknownRequestErrorsStillBlockQuoteAdmission() {
    // Numeric 300/354 never suffice to rearm a sender. Exercise the actual
    // admission state on both sides of its reservation linearization point.
    for(const std::string code:{"300","354"})for(bool reserved:{false,true}) {
        auto e=corroboratedCashError(1,code);e.localMarketDataRequestError=false;
        check(!IsIbCorroboratedCashMarketDataRequestError(e)&&IbAdmissionBlockingEvent(e),
              "unknown market-data error remains a blocking callback");
        IBMarketDataAdmissionState admission;std::uint64_t generation=0;
        check(admission.Begin(generation),"negative quote admission opens");
        if(reserved)check(admission.TryReserve(generation),"negative quote admission reserves");
        admission.ObserveBlockingCallback();
        check(admission.IsFailed(),"unknown callback blocks actual quote admission");
        if(reserved) {
            check(admission.EndSend(generation,false),"unsafe reserved send settles its lifecycle");
            check(admission.GetPhase()==IBMarketDataAdmissionState::Blocked&&admission.IsFailed(),
                  "settled unsafe send remains blocked and faulted");
        } else check(!admission.TryReserve(generation),"unsafe open admission cannot reserve");
        check(admission.Complete(),"failed admission closes its lifecycle");
        check(admission.GetPhase()==IBMarketDataAdmissionState::Idle&&admission.IsFailed(),
              "closing failed admission never clears its sticky fault");
        std::uint64_t nextGeneration=0;
        check(!admission.Begin(nextGeneration)&&nextGeneration==0&&
              !admission.TryReserve(generation),"failed admission cannot reopen or reserve again");
    }
}
int main(int argc,char**argv){try{
    check(::geteuid()!=0,"unprivileged test required");prohibitNetworkAndExec();
    if(argc==2&&std::string(argv[1])=="--secdef-only") {
        {Fixture f(true);f.unscopedSecDefFarmLossRetainsEconomicRecovery();}
        {Fixture f(true);auto event=IBEvent{};event.type=IBEventType::Error;
         event.key="2157";event.id=1000352;event.requestId=1000352;
         event.connectionEpoch=f.runtime->Adapter().GetConnectionEpoch();
         event.brokerClientId=701;
         f.uncorroboratedMarketDataErrorClosesRecovery(event);}
        std::cout<<"PASS native original 2157/-1 advisory and unsafe scoped 2157 controls; no orders\n";
        return 0;
    }

    {Fixture f;f.cashDriftOrdinaryRefresh();}
    {Fixture f(true);f.cashDriftOrdinaryRefresh();}
    {Fixture f;f.cashDriftOrdinaryRefresh(true);}
    {Fixture f(false,true);f.cashRefreshDoesNotBypassOtherHolds(true);}
    {Fixture f;f.cashRefreshDoesNotBypassOtherHolds(false);}
    if(argc==2&&std::string(argv[1])=="--cash-refresh-only") {
        std::cout<<"PASS 5 full-loop cash-refresh cases; missing End/unknown command/other risk held; no orders or Broker socket"<<std::endl;
        return 0;
    }

    {Fixture f(true);f.unscopedSecDefFarmLossRetainsEconomicRecovery();}
    {Fixture f(true);auto event=IBEvent{};event.type=IBEventType::Error;
     event.key="2157";event.id=1000352;event.requestId=1000352;
     event.connectionEpoch=f.runtime->Adapter().GetConnectionEpoch();
     event.brokerClientId=701;
     f.uncorroboratedMarketDataErrorClosesRecovery(event);}
    {Fixture f;f.reconnect(1101);f.reconnect(1102);check(f.shared->accountCalls>=3&&f.shared->positionCalls>=3&&f.shared->terminalCalls>=3,"full risk/order refresh methods executed");}
    {Fixture f(true);const auto originals=f.originalCommands();f.reconnect(1101);
        check(f.originalCommands()==originals,"original commands and owners preserved through full reconnect");
        auto pos=f.runtime->Adapter().GetAuthoritativePositionQuantities();check(pos.at("BTC.USD")==.01&&pos.at("EUR.USD")==25,"NONZERO_FX_BTC_POSITIONS_PRESERVED");
        f.restartSameJournal();}
    {Fixture f;f.missingPositionsFailsClosed();}
    {Fixture f;f.secondaryFarmRecovery();}
    {Fixture f;f.activeOrderFailsClosed();}
    {Fixture f(false,true);f.unknownOutcomeSurvivesReconstruction();}
    {Fixture f(true);f.localCancelledCashErrorsPreserveEconomicRecovery();}
    {Fixture f(true);f.differentEpochMarketDataErrorsCannotAuthorizeCurrentEpoch();}
    const std::vector<std::function<void(IBEvent&)>> invalidMarketDataScopes={
        [](IBEvent& e){e.localMarketDataRequestError=false;},
        [](IBEvent& e){e.key="354";e.localMarketDataRequestError=false;},
        [](IBEvent& e){e.brokerClientId=702;},
        [](IBEvent& e){e.brokerClientId=-1;},
        [](IBEvent& e){e.account="DU90001";},
        [](IBEvent& e){e.order.orderRef="foreign-order-reference";},
        [](IBEvent& e){e.advancedOrderRejectJson="{}";},
        [](IBEvent& e){e.requestId=e.id+1;},
        [](IBEvent& e){e.id=e.requestId=999999;},
        [](IBEvent& e){e.contract.secType="CRYPTO";},
        [](IBEvent& e){e.contract.exchange.clear();},
        [](IBEvent& e){e.connectionEpoch=0;},
        [](IBEvent& e){e.key="301";}
    };
    for(const auto& invalidate:invalidMarketDataScopes) {
        Fixture f(true);auto e=corroboratedCashError(f.runtime->Adapter().GetConnectionEpoch(),"300");
        invalidate(e);f.uncorroboratedMarketDataErrorClosesRecovery(e);
    }
    unknownRequestErrorsStillBlockQuoteAdmission();
    std::cout<<"{\"schema\":\"heptatrader.full-runtime-recovery-test.v1\",\"full_start_executed\":true,\"production_reconnect_loop_executed\":true,\"same_journal_runtime_reconstruction\":true,\"kernel_network_denied\":true,\"synthetic_broker\":true,\"broker_socket_calls\":0,\"order_method_calls\":0,\"local_cash_error_economic_barrier_preserved\":true,\"raw_local_errors_retained\":4,\"different_epoch_errors_quarantined\":true,\"uncorroborated_error_scope_cases\":"<<invalidMarketDataScopes.size()<<",\"live_recovery_qualified\":false,\"scenarios\":"<<15+invalidMarketDataScopes.size()<<"}"<<std::endl;
    return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<std::endl;return 1;}}
