#include "../HeptaTrade/client/strategy_intent_client.h"
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <unistd.h>
int main() {
    try {
        hepta::research::BoundedIntent i;i.instrument="TEST";i.action="BUY";i.quantity=1;
        i.limitPrice=100;i.observedAtUs=1000000;i.expiresAtMs=2000;
        TradingToolHostRequest request;std::string reason;
        const std::string permit="sha256:"+std::string(64,'a');
        if(!StrategyIntentClient::BuildRequest(i,"issued-command-01",permit,true,1000,request,reason)) throw std::runtime_error(reason);
        if(request.call.name!="trade.place_order" || !request.call.ibOrder.orderRef.empty() ||
           !request.sessionToken.empty() || request.call.referencePrice!=0 ||
           request.toolCallId!="issued-command-01" || request.call.timeInForce!="DAY")
            throw std::runtime_error("unexpected client authority or identity field");
        if(StrategyIntentClient::BuildRequest(i,"x",permit,true,1000,request,reason))
            throw std::runtime_error("short command id accepted");
        if(StrategyIntentClient::BuildRequest(i,"issued-command-01","fabricated",true,1000,request,reason))
            throw std::runtime_error("malformed permit accepted");
        char directory[]="/tmp/hepta-no-gateway-XXXXXX";
        if(!mkdtemp(directory)) throw std::runtime_error("temp directory failed");
        NativeToolClientConfig config;config.socketPath=std::string(directory)+"/absent.sock";
        config.sessionToken="explicit-test-only-token";config.timeoutMs=50;
        StrategyIntentClient client(config);StrategyCallResult result;
        if(client.Submit(i,"issued-command-01",permit,1000,result,reason) || result.transportComplete ||
           result.commandId!="issued-command-01" || !result.native.envelope.status.empty())
            throw std::runtime_error("transport failure became an acknowledgement");
        rmdir(directory);
        std::cout<<"unprivileged client standalone link and refusal checks: PASS\n";
        return 0;
    } catch(const std::exception& e) {std::cerr<<e.what()<<'\n';return 1;}
}
