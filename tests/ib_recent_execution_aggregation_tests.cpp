#include "execution/ib_paper_execution_runtime_composition.h"
#include "oms_journal.h"
#include <cassert>
#include <cmath>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>

class IbRecentOrderProjectionTestAccess {
    using Runtime=IbPaperExecutionRuntimeComposition;
    static void Apply(Runtime::RecentBrokerOrder& o,const OmsJournalEvent& e){
        Runtime::ApplyRecentBrokerOrderIdentity(o,e,"pocket4-paper");
        Runtime::ApplyRecentBrokerExecutionIdentity(o,e);
        const bool economic=Runtime::HasPositiveEconomicEvidence(e);
        Runtime::ApplyRecentBrokerEconomicEvidence(o,e,economic);
        Runtime::ApplyRecentBrokerTerminalEvidence(o,e,e.eventType=="order_owner_reconciled_terminal",economic);
    }
    static OmsJournalEvent Event(const std::string& type,const std::string& id,double qty,double price){
        OmsJournalEvent e;e.eventType=type;e.brokerExecutionId=id;e.qty=qty;e.price=price;
        e.source="agent:pocket4-paper";e.traceId="crypto-session";e.account="DU123";e.executionDomain="PAPER";
        e.instrument="BTC.USD";e.side="BUY";e.reqId="entry-command";e.venueCorrelationId="real-correlation";
        e.orderId=159;e.brokerClientId=701;e.brokerServiceEpoch="first-epoch";e.brokerConnectionEpoch=1;
        e.status=type=="broker_execution"?"ExecutionDetails":"Cancelled";return e;
    }
public:
    static void Test(){
        Runtime::RecentBrokerOrder a;
        auto first=Event("broker_execution","00012dbb.6ac2b092.01.01",.01,86436.75);
        auto second=Event("broker_execution","00012dbb.6ac2b093.01.01",.03313821,86437.099117);
        auto cancelled=Event("broker_order_status","",.03313821,86437.099117);
        Apply(a,first);Apply(a,second);Apply(a,cancelled);
        assert(a.terminal && a.status=="Cancelled" && a.economicFill && !a.brokerExecutionAmbiguous);
        assert(a.filledQuantity==.03313821 && a.averageFillPrice==86437.099117);
        assert(a.brokerExecutionId==second.brokerExecutionId && a.cumulativeExecutions.Points().size()==2);
        first.brokerServiceEpoch="after-restart";second.brokerServiceEpoch="after-restart";
        Apply(a,first);Apply(a,second);
        assert(a.filledQuantity==.03313821 && a.averageFillPrice==86437.099117 && a.status=="Cancelled");
        auto reconciled=Event("order_owner_reconciled_terminal","",0,0);Apply(a,reconciled);
        assert(a.status=="Cancelled");
        Runtime::RecentBrokerOrder reverse;Apply(reverse,second);Apply(reverse,first);Apply(reverse,cancelled);
        assert(reverse.filledQuantity==a.filledQuantity && reverse.averageFillPrice==a.averageFillPrice);
        second.price+=1;Apply(a,second);assert(a.brokerExecutionAmbiguous && a.brokerExecutionId.empty());
        std::cout<<"PASS: production read model cumulative multi-execution, duplicate/reverse replay, partial cancellation, conflict fence\n";
    }
    static int Audit(const std::string& path,const std::string& command,const std::string& account){
        std::ifstream in(path);if(!in)throw std::runtime_error("audit file not found");
        Runtime::RecentBrokerOrder o;std::string line,correlation;std::size_t events=0;
        while(std::getline(in,line)){
            OmsJournalEvent e;if(!OmsJournal::ParseJsonLine(line,e))throw std::runtime_error("invalid OMS prefix");
            if(e.reqId!=command || e.account!=account || e.executionDomain!="PAPER")continue;
            if(e.eventType!="broker_execution" && e.eventType!="broker_order_status" && e.eventType!="broker_completed_order")continue;
            if(e.venueCorrelationId.empty() || e.source!="agent:pocket4-paper")continue;
            if(correlation.empty())correlation=e.venueCorrelationId;
            if(e.venueCorrelationId!=correlation)throw std::runtime_error("correlation conflict");
            Apply(o,e);events++;
        }
        std::cout.precision(17);
        std::cout<<"{\"audit_only\":true,\"broker_authorized\":false,\"events\":"<<events
          <<",\"terminal\":"<<(o.terminal?"true":"false")<<",\"status\":\""<<o.status
          <<"\",\"filled_quantity\":"<<o.filledQuantity<<",\"average_fill_price\":"<<o.averageFillPrice
          <<",\"execution_count\":"<<o.cumulativeExecutions.Points().size()<<",\"execution_ambiguous\":"<<(o.brokerExecutionAmbiguous?"true":"false")<<"}\n";
        return events>0 && !o.brokerExecutionAmbiguous && o.cumulativeExecutions.HasEvidence()?0:1;
    }
};
int main(int argc,char** argv){try{
    if(argc==5 && std::string(argv[1])=="--audit")return IbRecentOrderProjectionTestAccess::Audit(argv[2],argv[3],argv[4]);
    if(argc!=1)throw std::runtime_error("invalid test invocation");IbRecentOrderProjectionTestAccess::Test();return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
