// Opt-in, never installed or registered as an ordinary test. No service Start().
// Production quote runtime/adapter/SDK are behind an external frame allowlist.
#include "execution/ib_paper_execution_runtime_composition.h"
#include <algorithm>
#include <chrono>
#include <csignal>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>
namespace {
volatile std::sig_atomic_t stopped=0;
void onSignal(int) { stopped=1; }
std::uint64_t wallMs() {return static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::system_clock::now().time_since_epoch()).count());}
std::uint64_t monoMs() {return static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now().time_since_epoch()).count());}
int bounded(const char* value,int low,int high) {
    std::string s(value);std::size_t used=0;long n=std::stol(s,&used);
    if(used!=s.size() || n<low || n>high)throw std::runtime_error("INVALID_BOUNDED_ARGUMENT");
    return static_cast<int>(n);
}
}
struct IbQuoteRecoveryLiveFixture {
    using Runtime=IbPaperExecutionRuntimeComposition;
    std::unique_ptr<Runtime> runtime;
    std::uint64_t start=monoMs(), durationMs, generation=0, callbacks=0;
    std::uint64_t attempts=0, successfulConnections=0, refreshChecks=0, refreshFailures=0;
    std::uint64_t cashReadySince=0, nextReconnectAt=0, nextSampleAt=0, nextRefreshAt=0;
    std::uint64_t samples=0, eurFreshSamples=0, btcFreshSamples=0, firstEurAt=0;
    std::uint64_t staleRun=0, longestStale=0;
    bool subscribed=false, reconnectRequested=false, fatal=false;
    IbQuoteRecoveryLiveFixture(int port,int clientId,int seconds):durationMs(seconds*1000ULL) {
        IbPaperExecutionRuntimeConfig c;
        c.profile.enabled=true;c.profile.userScope=true;c.profile.observationOnly=false;
        c.profile.userQuoteContracts="BTC.USD|BTC|CRYPTO|PAXOS|USD;EUR.USD|EUR|CASH|IDEALPRO|USD";
        c.primaryQuoteInstrument="BTC.USD";c.quoteMaxAgeMs=5000;
        if(!c.profile.UsesUserCrypto())throw std::runtime_error("MIXED_PROFILE_REQUIRED");
        IbPaperExecutionRuntimeTestHooks hooks;
        hooks.onStage=[](const char* s) {
            if(std::string(s)=="stale_cash_quote_resubscribed" || std::string(s)=="stale_cash_quote_deferred_no_send")
                std::cout<<"{\"kind\":\"production_refresh_stage\",\"at_ms\":"<<wallMs()<<",\"stage\":\""<<s<<"\"}"<<std::endl;
        };
        runtime.reset(new Runtime(c,std::unique_ptr<IIBApiWrapper>(),hooks));
        auto factory=[](){return std::unique_ptr<IIBApiWrapper>(CreateIBApiWrapper());};
        runtime->m_adapter.reset(new HeptaIBGatewayAdapter(factory(),factory));
        HeptaIBConfig a;a.host="127.0.0.1";a.port=port;a.clientId=clientId;a.readOnly=true;
        a.risk.enableOrderSubmission=false;a.risk.globalKillSwitch=true;
        // Account stays empty. The probe never requests account/order/position data.
        if(!runtime->m_adapter->Init(a))throw std::runtime_error("READ_ONLY_ADAPTER_INIT_FAILED");
        IBContractLite btc,eur;
        btc.symbol="BTC";btc.secType="CRYPTO";btc.exchange="PAXOS";btc.currency="USD";
        eur.symbol="EUR";eur.secType="CASH";eur.exchange="IDEALPRO";eur.currency="USD";
        runtime->m_quoteSubscriptions.reset(new IBAuthoritativeQuoteSubscriptionSet(runtime->m_authoritativeSnapshots,5100001));
        std::string reason;
        if(!runtime->m_quoteSubscriptions->Configure({{"BTC.USD",btc},{"EUR.USD",eur}},"BTC.USD",reason,false,true))
            throw std::runtime_error("QUOTE_CONFIGURATION_FAILED");
    }
    ~IbQuoteRecoveryLiveFixture(){close();}
    void close(){if(runtime && runtime->m_adapter)runtime->m_adapter->Disconnect();}
    void drain() {
        runtime->m_adapter->PollOnce(0);IBEvent e;unsigned limit=0;
        while(++limit<=10000 && runtime->m_adapter->TryDequeueEvent(e)) {
            if(e.connectionEpoch!=runtime->m_adapter->GetConnectionEpoch())continue;
            if(e.type==IBEventType::TickPrice) {
                ++callbacks;runtime->HandleNonPersistentAdapterEvent(e);
            } else if(e.type==IBEventType::ConnectionClosed || e.type==IBEventType::EventQueueOverflow) {
                reconnectRequested=true;
            } else if(e.type==IBEventType::Error) {
                int code=0;try{code=std::stoi(e.key);}catch(...){fatal=true;continue;}
                const auto colon=e.value.rfind(':');std::string farm=colon==std::string::npos?"":e.value.substr(colon+1);
                while(!farm.empty() && farm.front()==' ')farm.erase(farm.begin());
                const bool cash=farm=="cashfarm" || farm=="hfarm";
                if(cash && code==2104)cashReadySince=monoMs();
                if(cash && (code==2103 || code==2119))cashReadySince=0;
                if(code==1100 || code==1101 || code==504 || code==509 || code==1300)reconnectRequested=true;
                // 1102 is maintained data: do not turn it into another request.
                if(code==10197 || code==100 || code==101 || code==102 || code==10089 || code==10090)fatal=true;
                std::cout<<"{\"kind\":\"broker_control\",\"at_ms\":"<<wallMs()<<",\"connection_epoch\":"<<e.connectionEpoch
                         <<",\"code\":"<<code<<",\"cash_farm\":"<<(cash?"true":"false")<<"}"<<std::endl;
            }
        }
        if(limit>10000 || runtime->HasFatalRuntimeError())fatal=true;
    }
    bool connectCycle() {
        ++attempts;cashReadySince=0;subscribed=false;reconnectRequested=false;
        if(attempts>8){fatal=true;return false;}
        std::cout<<"{\"kind\":\"connect_attempt\",\"at_ms\":"<<wallMs()<<",\"attempt\":"<<attempts<<"}"<<std::endl;
        if(!runtime->m_adapter->Connect(8000)){nextReconnectAt=monoMs()+10000;return false;}
        ++successfulConnections;
        auto plan=runtime->m_quoteSubscriptions->BeginCycle(runtime->m_adapter->GetConnectionEpoch(),++generation,wallMs());
        if(!plan.accepted){fatal=true;return false;}
        // Old socket subscriptions died at socket teardown. Never cancel old IDs
        // on the new connection. BeginCycle generates new IDs and empty quotes.
        const auto waitEnd=monoMs()+12000;
        while(!stopped && !fatal && !reconnectRequested && monoMs()<waitEnd &&
              (!cashReadySince || monoMs()-cashReadySince<500)) {
            drain();std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
        if(stopped || fatal || reconnectRequested)return false;
        for(const auto& sub:plan.subscriptions) {
            const bool sent=runtime->m_adapter->ReqMktData(sub.requestId,sub.contract);
            if(!runtime->m_quoteSubscriptions->RecordDispatchResult(plan.generation,sub.requestId,sent)){fatal=true;return false;}
            std::cout<<"{\"kind\":\"initial_quote_dispatch\",\"at_ms\":"<<wallMs()<<",\"connection_epoch\":"<<plan.connectionEpoch
                     <<",\"instrument\":\""<<sub.instrument<<"\",\"request_id\":"<<sub.requestId<<",\"sent\":"<<(sent?"true":"false")<<"}"<<std::endl;
        }
        subscribed=true;nextRefreshAt=monoMs()+5000;return true;
    }
    bool fresh(const IBAuthoritativeQuoteContractHealth& c,std::uint64_t at)const {
        const auto& q=c.quote;
        return c.active && c.dispatchAccepted && q.HasQuote() && std::isfinite(q.bid) && std::isfinite(q.ask)
            && q.bid>0 && q.ask>=q.bid && q.bidObservedAtMs && q.askObservedAtMs
            && q.bidObservedAtMs<=at && q.askObservedAtMs<=at
            && at-q.bidObservedAtMs<=5000 && at-q.askObservedAtMs<=5000;
    }
    void sample() {
        const auto at=wallMs(),elapsed=monoMs()-start;const auto h=runtime->m_quoteSubscriptions->GetHealth();
        const auto eur=h.contracts.find("EUR.USD"),btc=h.contracts.find("BTC.USD");
        const bool connected=runtime->m_adapter->IsConnected() && !reconnectRequested;
        const bool ef=connected && eur!=h.contracts.end() && fresh(eur->second,at);
        const bool bf=connected && btc!=h.contracts.end() && fresh(btc->second,at);
        ++samples;eurFreshSamples+=ef;btcFreshSamples+=bf;
        if(ef){if(!firstEurAt)firstEurAt=elapsed;staleRun=0;}
        else{++staleRun;longestStale=std::max(longestStale,staleRun);}
        std::cout<<"{\"kind\":\"quote_sample\",\"at_ms\":"<<at<<",\"elapsed_ms\":"<<elapsed
                 <<",\"connection_epoch\":"<<h.connectionEpoch<<",\"connected\":"<<(connected?"true":"false")
                 <<",\"eur_fresh\":"<<(ef?"true":"false")<<",\"btc_fresh\":"<<(bf?"true":"false");
        if(eur!=h.contracts.end()) {
            const auto& c=eur->second;const auto& q=c.quote;
            std::cout<<",\"eur_request_id\":"<<c.requestId<<",\"eur_dispatched\":"<<(c.dispatchAccepted?"true":"false")
                     <<",\"bid_callback_ms\":"<<q.bidObservedAtMs<<",\"ask_callback_ms\":"<<q.askObservedAtMs;
        }
        if(btc!=h.contracts.end())std::cout<<",\"btc_request_id\":"<<btc->second.requestId;
        std::cout<<"}"<<std::endl;
    }
    int run() {
        std::cout<<"{\"kind\":\"header\",\"schema\":\"heptatrader.native-live-quote-component.v1\",\"at_ms\":"<<wallMs()
                 <<",\"duration_ms\":"<<durationMs<<",\"observation_profile_bypass\":false,\"full_execution_started\":false,"
                   "\"source\":\"NATIVE_REQ_MKT_DATA\",\"quote_age_limit_ms\":5000,\"full_runtime_qualification\":false}"<<std::endl;
        while(!stopped && !fatal && monoMs()-start<durationMs) {
            if(reconnectRequested || !runtime->m_adapter->IsConnected()) {
                if(subscribed || reconnectRequested){close();subscribed=false;reconnectRequested=false;nextReconnectAt=monoMs()+5000;}
                if(monoMs()>=nextReconnectAt)connectCycle();
            } else {
                drain();
                if(subscribed && !reconnectRequested && monoMs()>=nextRefreshAt) {
                    nextRefreshAt=monoMs()+5000;++refreshChecks;std::string reason;
                    if(!runtime->RefreshStaleSecondaryCashQuote(reason)) {
                        ++refreshFailures;fatal=true;
                        std::cout<<"{\"kind\":\"refresh_failed\",\"at_ms\":"<<wallMs()<<",\"reason\":\""<<reason<<"\"}"<<std::endl;
                    }
                }
            }
            if(monoMs()>=nextSampleAt){sample();nextSampleAt=monoMs()+1000;}
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
        if(runtime->m_adapter->IsConnected()) {
            auto h=runtime->m_quoteSubscriptions->GetHealth();
            for(const auto& e:h.contracts)if(e.second.dispatchAccepted)
                if(!runtime->m_adapter->CancelMktData(e.second.requestId))fatal=true;
        }
        close();
        std::cout<<"{\"kind\":\"end\",\"at_ms\":"<<wallMs()<<",\"elapsed_ms\":"<<monoMs()-start
                 <<",\"duration_completed\":"<<(!stopped && !fatal && monoMs()-start>=durationMs?"true":"false")
                 <<",\"fatal\":"<<(fatal?"true":"false")<<",\"interrupted\":"<<(stopped?"true":"false")
                 <<",\"samples\":"<<samples<<",\"eur_fresh_samples\":"<<eurFreshSamples<<",\"btc_fresh_samples\":"<<btcFreshSamples
                 <<",\"first_eur_after_start_ms\":"<<firstEurAt<<",\"longest_stale_sample_run\":"<<longestStale
                 <<",\"successful_connections\":"<<successfulConnections<<",\"refresh_checks\":"<<refreshChecks
                 <<",\"refresh_failures\":"<<refreshFailures<<",\"full_runtime_qualification\":false,\"strict_nav_eligible\":false}"<<std::endl;
        return stopped || fatal?2:0;
    }
};
int main(int argc,char** argv) {
    try {
        if(argc!=4)throw std::runtime_error("REQUIRE_PROXY_PORT_ISOLATED_CLIENT_ID_DURATION");
        int port=bounded(argv[1],14000,14999),cid=bounded(argv[2],29000,29999),seconds=bounded(argv[3],30,3600);
        std::signal(SIGTERM,onSignal);std::signal(SIGINT,onSignal);
        IbQuoteRecoveryLiveFixture probe(port,cid,seconds);return probe.run();
    }catch(const std::exception& e){std::cerr<<"PROBE_FAIL_CLOSED "<<e.what()<<std::endl;return 3;}
}
