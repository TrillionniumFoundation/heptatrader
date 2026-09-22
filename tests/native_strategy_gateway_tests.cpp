#include "../HeptaTrade/client/strategy_intent_client.h"
#include "../HeptaTrade/execution/execution_coordinator.h"
#include "../HeptaTrade/simulator/deterministic_execution_venue.h"
#include "../HeptaTrade/tool_host/unix_tool_server.h"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <unistd.h>
#include <vector>

namespace {
void Require(bool ok, const std::string& msg) { if (!ok) throw std::runtime_error(msg); }
void TestGateway() {
    char dir[]="/tmp/hepta-strategy-integration-XXXXXX";
    Require(mkdtemp(dir)!=nullptr,"temp directory");
    const std::string journalPath=std::string(dir)+"/oms", socketPath=std::string(dir)+"/gateway.sock";
    OmsJournal journal; Require(journal.Init(journalPath),"journal init");
    DeterministicExecutionVenue venue; venue.SetQuote("TEST",99,101);
    DecisionLeaseManager leases;
    std::atomic<int> mutations(0);
    std::atomic<bool> permitMutation(true);
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.placement=VenuePlacement::Immediate([&](const PlaceOrderCommand& command,const std::string& correlation) {
        ++mutations;
        return venue.PlaceOrderWithResult(command.contract,command.order,correlation,true);
    });
    callbacks.validateDecisionLease=[&](const AgentExecutionContext& context,const std::string& instrument,std::string* reason) {
        DecisionLeaseKey key; key.executionDomain=context.executionDomain; key.account=context.account; key.instrument=instrument;
        DecisionLeaseOwner owner; owner.agentId=context.agentId; owner.sessionId=context.sessionId;
        DecisionLeaseCredential credential; credential.fencingToken=context.decisionLeaseFencingToken;
        credential.generation=context.decisionLeaseGeneration;
        const auto result=leases.Validate(key,owner,credential);
        if(reason) *reason=DecisionLeaseManager::StatusName(result.status);
        return result.status==DecisionLeaseStatus::Valid;
    };
    ExecutionCoordinator execution(journal,callbacks);
    // Test-only simulator preview fixture. Real profile permission issuance and
    // authoritative risk are tested by the repository's maintained core suite.
    const std::string permit="sha256:"+std::string(64,'a');
    TradingToolReadCallbacks reads;
    reads.riskPreviewOrder=[&](const TradingToolSession&,const TradingToolCall&,std::string& payload,std::string&) {
        payload="{\"preview_permit\":\""+permit+"\",\"command_id\":\"strategy-order-0001\",\"single_use\":true}";
        return true;
    };
    reads.executionGetCommandStatus=[&](const TradingToolSession& session,const TradingToolCall& call,std::string& payload,std::string& reason) {
        ExecutionCommandResult found;
        if(!execution.GetCommandStatus(session.executionContext.agentId,session.executionContext.sessionId,call.targetCommandId,found)) {
            reason="UNKNOWN_COMMAND"; return false;
        }
        payload="{\"command_id\":\""+call.targetCommandId+"\",\"order_id\":"+std::to_string(found.orderId)+"}";
        return true;
    };
    TradingToolRegistry registry(execution,reads);
    TradingToolHost host(registry,leases,[&](const TradingToolSession&,const TradingToolCall&,std::string& reason) {
        if(!permitMutation.load()) {reason="TEST_EXECUTION_NOT_READY";return false;} return true;
    });
    TradingToolHostSessionBinding binding;
    binding.token="native-research-test-token"; binding.peerUid=static_cast<std::uint32_t>(getuid());
    binding.session.executionContext.agentId="strategy-test-agent";
    binding.session.executionContext.sessionId="strategy-test-session";
    binding.session.executionContext.account="SIM-PAPER";
    binding.session.executionContext.venue="SIM";
    binding.session.executionContext.strategy="breakout-test";
    binding.session.environment="PAPER";
    binding.session.capabilities={"system.read","risk.read","trade.place","orders.read"};
    binding.allowedInstruments.insert("TEST");
    InstrumentRef contract;contract.symbol="TEST";contract.secType="STK";contract.exchange="SIM";contract.currency="USD";
    binding.instrumentContracts["TEST"]=contract;binding.maxOrderQuantity=10;
    binding.maxTradeCallsPerMinute=100;binding.executionDomain="SIM-PAPER";
    const auto now=OmsJournal::NowEpochMs();binding.expiresAtMs=now+60000;
    std::string reason;Require(host.RegisterSession(binding,reason),"session: "+reason);
    UnixToolServer server(host);server.AllowMissingDecisionAuditForTests();Require(server.Start(socketPath,reason),reason);
    NativeToolClientConfig config;config.socketPath=socketPath;config.sessionToken=binding.token;config.timeoutMs=5000;
    StrategyIntentClient client(config);
    hepta::research::BoundedIntent intent;intent.instrument="TEST";intent.action="BUY";intent.quantity=2;
    intent.limitPrice=100;intent.observedAtUs=now*1000;intent.expiresAtMs=now+30000;
    StrategyCallResult result;
    Require(client.Preview(intent,"strategy-preview-0001",now,result,reason),"preview transport: "+reason);
    Require(result.native.envelope.status=="ok","preview status: "+result.native.responseJson);
    Require(mutations==0,"preview must not mutate venue");
    Require(!client.Submit(intent,"strategy-order-0001","",now,result,reason),"missing permit must fail locally");
    Require(mutations==0 && !result.transportComplete,"no permit no send");
    Require(client.Submit(intent,"strategy-order-0001",permit,now,result,reason),"submit transport: "+reason);
    Require(result.native.envelope.status=="ok","submit status: "+result.native.responseJson);
    Require(mutations==1 && result.native.envelope.orderId>=0,"single execution-owned mutation");
    const long id=result.native.envelope.orderId;
    std::vector<std::uint64_t> latency;latency.push_back(result.elapsedUs);
    Require(client.Submit(intent,"strategy-order-0001",permit,now,result,reason),"duplicate transport");
    Require(result.native.envelope.status=="duplicate" && mutations==1,"stable identity prevents duplicate send");
    Require(result.native.envelope.orderId==id,"duplicate retains venue order id");
    for(int i=0;i<5;++i) {
        Require(client.Query("strategy-order-0001","strategy-status-000"+std::to_string(i),result,reason),"status transport: "+reason);
        Require(result.native.envelope.status=="ok","status result: "+result.native.responseJson);latency.push_back(result.elapsedUs);
    }
    hepta::research::BoundedIntent conflict=intent;conflict.quantity=3;
    Require(client.Submit(conflict,"strategy-order-0001",permit,now,result,reason),"conflict transport");
    Require(result.native.envelope.status!="ok" && mutations==1,"identity conflict rejected without venue send");
    permitMutation=false;
    Require(client.Submit(intent,"strategy-order-0002",permit,now,result,reason),"readiness response");
    Require(result.native.envelope.status!="ok" && mutations==1,"execution readiness cannot be bypassed");
    Require(!client.Submit(intent,"strategy-order-0003",permit,intent.expiresAtMs,result,reason),"expired proposal rejected");
    server.Stop();
    Require(!client.Submit(intent,"strategy-order-0004",permit,now,result,reason),"disconnected gateway fails");
    Require(!result.transportComplete && result.commandId=="strategy-order-0004" && mutations==1,"transport failure retains command identity");
    Require(result.native.envelope.status.empty(),"stale acknowledgement cleared");
    // Durable command identity survives rebuilding the coordinator. No adapter
    // call occurs during recovery or an exact replay.
    ExecutionCoordinator recovered(journal,callbacks);Require(recovered.RecoverFromJournal(reason),reason);
    ExecutionCommandResult recoveredResult;
    Require(recovered.GetCommandStatus("strategy-test-agent","strategy-test-session","strategy-order-0001",recoveredResult),"journal identity recovered");
    Require(recoveredResult.orderId==id && mutations==1,"durable recovered owner/id");
    std::sort(latency.begin(),latency.end());
    std::cout<<"native strategy -> Unix Gateway -> ExecutionCoordinator -> deterministic simulator: PASS\n"
             <<"fixture transport latency_us samples="<<latency.size()<<" median="<<latency[latency.size()/2]<<" max="<<latency.back()<<" (not a production latency SLA)\n";
    // The fixture directory may retain additional journal sidecars on failure;
    // successful runs remove only the files they explicitly own.
    std::remove(journalPath.c_str());rmdir(dir);
}
}
int main(){try{TestGateway();return 0;}catch(const std::exception& e){std::cerr<<"FAIL: "<<e.what()<<'\n';return 1;}}
