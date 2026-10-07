#include "execution/ib_paper_execution_runtime_composition.h"
#include "oms_journal.h"
#include <cassert>
#include <fstream>
#include <iostream>
#include <stdexcept>

struct IbRecentBrokerOrderReplayAccess {
    static void Project(IbPaperExecutionRuntimeComposition& runtime,const OmsJournalEvent& e) {
        runtime.ProjectRecentBrokerOrder(e);
    }
    static std::string Json(const IbPaperExecutionRuntimeComposition& runtime,
        const AgentExecutionContext& context) {
        return runtime.RecentBrokerOrdersJson(context);
    }
    static void Validate(const IbPaperExecutionRuntimeComposition& runtime,
        const std::string& command,double quantity,double average,std::size_t ids,
        bool ambiguous=false) {
        const IbPaperExecutionRuntimeComposition::RecentBrokerOrder* found=nullptr;
        for (const auto& item:runtime.m_recentBrokerOrders)
            if (item.second.commandId==command && !item.second.venueCorrelationId.empty()) {
                assert(found==nullptr);found=&item.second;
            }
        assert(found!=nullptr);
        assert(found->brokerExecutionAmbiguous==ambiguous);
        if (ambiguous) return;
        assert(found->terminal && found->status=="Cancelled");
        assert(found->economicFill && found->filledQuantity==quantity);
        assert(found->averageFillPrice==average);
        assert(found->cumulativeExecutions.Points().size()==ids);
        assert(found->terminalQuantityKnown);
        assert(found->cumulativeExecutions.MatchesTerminal(found->terminalFilledQuantity,
            found->terminalAverageFillPrice));
    }
    static bool TerminalSnapshotComplete(
        const IbPaperExecutionRuntimeComposition& runtime,
        const std::string& command,
        const IBAuthoritativeTerminalCorrelationSnapshot& terminal) {
        const IbPaperExecutionRuntimeComposition::RecentBrokerOrder* found=nullptr;
        for (const auto& item:runtime.m_recentBrokerOrders)
            if (item.second.commandId==command && !item.second.venueCorrelationId.empty()) {
                assert(found==nullptr);found=&item.second;
            }
        assert(found!=nullptr);
        return IbPaperExecutionRuntimeComposition::
            TerminalSnapshotCompletesRecentBrokerOrder(*found,terminal);
    }
};

OmsJournalEvent Base() {
    OmsJournalEvent e;e.eventType="place_sent";e.reqId="fixture-command-original";
    e.clientReqId=e.reqId;e.traceId="fixture-session";e.source="agent:fixture-agent";
    e.executionDomain="PAPER";e.account="DU_TEST";e.venue="IB";e.orderId=159;
    e.instrument="BTC.USD";e.side="BUY";e.status="submitted";e.tsMs=10;
    e.venueCorrelationId="hepta-v1-sha256:"+std::string(64,'a');return e;
}
int main(int argc,char** argv) {
    IbPaperExecutionRuntimeConfig config;
    if (argc==3) {
        // Operator verification reads an immutable copy only. No Start(), API,
        // network, current state rewrite, or active journal append is invoked.
        IbPaperExecutionRuntimeComposition runtime(config);
        std::ifstream input(argv[1]);if(!input)throw std::runtime_error("journal not readable");
        AgentExecutionContext context;std::string line;std::size_t count=0;
        while(std::getline(input,line)) {
            OmsJournalEvent e;if(!OmsJournal::ParseJsonLine(line,e))
                throw std::runtime_error("journal row invalid");
            IbRecentBrokerOrderReplayAccess::Project(runtime,e);++count;
            if(e.reqId==argv[2] && e.eventType=="place_sent") {
                const auto colon=e.source.find(':');context.agentId=e.source.substr(colon+1);
                context.sessionId=e.traceId;context.account=e.account;context.executionDomain=e.executionDomain;
            }
        }
        if(context.agentId.empty())throw std::runtime_error("original send binding missing");
        std::cout<<IbRecentBrokerOrderReplayAccess::Json(runtime,context)<<'\n';
        std::cerr<<"OFFLINE_PRODUCTION_REPLAY_ROWS="<<count<<'\n';return 0;
    }
    {
        IbPaperExecutionRuntimeComposition runtime(config);
        auto base=Base();IbRecentBrokerOrderReplayAccess::Project(runtime,base);
        auto execution=base;execution.eventType="broker_execution";
        execution.status="ExecutionDetails";execution.brokerCallbackType="execDetails";
        execution.brokerExecutionId="fixture-terminal-refresh-exec";
        execution.brokerClientId=701;execution.brokerConnectionEpoch=3;
        execution.brokerServiceEpoch="service-a";execution.side="BOT";
        execution.qty=.00049993;execution.price=85611.5;execution.tsMs=20;
        IbRecentBrokerOrderReplayAccess::Project(runtime,execution);
        auto owner=base;owner.eventType="order_owner_reconciled_terminal";
        owner.reqId="owner-audit";owner.qty=0;owner.price=0;
        owner.status="terminal";owner.tsMs=30;
        IbRecentBrokerOrderReplayAccess::Project(runtime,owner);

        IBAuthoritativeTerminalCorrelationSnapshot terminal;
        terminal.complete=true;terminal.connectionEpoch=9;terminal.generation=4;
        terminal.terminalOrderIdsByCorrelation[base.venueCorrelationId]=base.orderId;
        terminal.terminalStatusesByCorrelation[base.venueCorrelationId]="Filled";
        terminal.executionCorrelations.insert(base.venueCorrelationId);
        assert(IbRecentBrokerOrderReplayAccess::TerminalSnapshotComplete(
            runtime,base.reqId,terminal));

        auto wrong=terminal;
        wrong.terminalOrderIdsByCorrelation[base.venueCorrelationId]=base.orderId+1;
        assert(!IbRecentBrokerOrderReplayAccess::TerminalSnapshotComplete(
            runtime,base.reqId,wrong));
        wrong=terminal;wrong.executionCorrelations.clear();
        assert(!IbRecentBrokerOrderReplayAccess::TerminalSnapshotComplete(
            runtime,base.reqId,wrong));
        wrong=terminal;wrong.complete=false;
        assert(!IbRecentBrokerOrderReplayAccess::TerminalSnapshotComplete(
            runtime,base.reqId,wrong));
    }
    for(int reverse=0;reverse<2;++reverse) {
        IbPaperExecutionRuntimeComposition runtime(config);
        auto base=Base();IbRecentBrokerOrderReplayAccess::Project(runtime,base);
        auto first=base;first.eventType="broker_execution";first.status="ExecutionDetails";
        first.brokerCallbackType="execDetails";first.brokerExecutionId="fixture.0001.01.01";
        first.brokerClientId=701;first.brokerConnectionEpoch=3;first.brokerServiceEpoch="service-a";
        first.side="BOT";first.qty=.01;first.price=86436.75;first.tsMs=20;
        auto second=first;second.brokerExecutionId="fixture.0002.01.01";
        second.qty=.03313821;second.price=86437.099117;second.tsMs=30;
        IbRecentBrokerOrderReplayAccess::Project(runtime,reverse?second:first);
        IbRecentBrokerOrderReplayAccess::Project(runtime,reverse?first:second);
        auto terminal=second;terminal.eventType="broker_order_status";terminal.brokerCallbackType="orderStatus";
        terminal.brokerExecutionId.clear();terminal.status="Cancelled";terminal.brokerRemainingQuantity=.31393981;
        terminal.riskCode="IB_ORDER_CANCELLED";terminal.tsMs=40;
        IbRecentBrokerOrderReplayAccess::Project(runtime,terminal);
        IbRecentBrokerOrderReplayAccess::Validate(runtime,base.reqId,.03313821,86437.099117,2);
        auto error=terminal;error.eventType="broker_error";error.brokerCallbackType="error";
        error.qty=0;error.price=0;error.brokerErrorCode=202;error.tsMs=50;
        IbRecentBrokerOrderReplayAccess::Project(runtime,error);
        auto ack=base;ack.eventType="broker_order_accepted";ack.status="Accepted";
        ack.brokerClientId=701;ack.brokerConnectionEpoch=4;ack.brokerServiceEpoch="service-b";ack.tsMs=60;
        IbRecentBrokerOrderReplayAccess::Project(runtime,ack);
        IbRecentBrokerOrderReplayAccess::Project(runtime,first);
        IbRecentBrokerOrderReplayAccess::Project(runtime,second);
        IbRecentBrokerOrderReplayAccess::Validate(runtime,base.reqId,.03313821,86437.099117,2);
        auto owner=terminal;owner.eventType="order_owner_reconciled_terminal";owner.reqId="owner-audit";
        owner.qty=0;owner.price=0;owner.status.clear();owner.tsMs=70;
        IbRecentBrokerOrderReplayAccess::Project(runtime,owner);
        IbRecentBrokerOrderReplayAccess::Validate(runtime,base.reqId,.03313821,86437.099117,2);
        // Reused numeric order with another correlation is a distinct row.
        auto foreign=second;foreign.reqId="different-command";
        foreign.venueCorrelationId="hepta-v1-sha256:"+std::string(64,'b');
        foreign.qty=.9;foreign.price=86438;IbRecentBrokerOrderReplayAccess::Project(runtime,foreign);
        IbRecentBrokerOrderReplayAccess::Validate(runtime,base.reqId,.03313821,86437.099117,2);
        auto wrong=second;wrong.account="DU_OTHER";IbRecentBrokerOrderReplayAccess::Project(runtime,wrong);
        IbRecentBrokerOrderReplayAccess::Validate(runtime,base.reqId,0,0,0,true);
    }
    std::cout<<"PASS production projection: multiple IDs, exact terminal totals, duplicates, ACKs, owner settlement and foreign binding\n";
}
