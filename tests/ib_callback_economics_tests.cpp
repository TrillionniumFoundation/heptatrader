// Synthetic callback/OMS serialization tests only. No network or order API.
#include "adapter_ib/ib_callback_economics.h"
#include <cassert>
#include <cmath>
#include <fstream>
#include <iostream>
#include <limits>
#include <locale>
#include <unistd.h>

namespace {
InstrumentRef Contract(){InstrumentRef c;c.symbol="XSP";c.secType="OPT";c.currency="USD";c.exchange="SMART";
 c.lastTradeDateOrContractMonth="20261005";c.right="C";c.strike=775;c.multiplier="100";
 c.tradingClass="XSP";c.localSymbol="XSP   261005C00775000";return c;}
OmsJournalEvent ExecutionReceipt(){
 IBEvent callback;callback.type=IBEventType::ExecutionDetails;callback.id=159;callback.key="synthetic.01.01";
 callback.economics=CaptureIbExecutionEconomics(Contract(),123,456,1,3,2,2.5,false);
 OmsJournalEvent e;e.eventType="broker_execution";e.brokerCallbackType="execDetails";e.brokerExecutionId=callback.key;
 e.qty=2;e.price=2.5;e.orderId=159;e.venue="IB";e.executionDomain="PAPER";e.account="DU123";
 e.brokerServiceEpoch="test-service";e.brokerConnectionEpoch=11;e.brokerClientId=701;
 e.reqId="test-command";e.source="agent:test";e.traceId="test-session";
 RetainIbCallbackEconomics(callback,e);return e;
}
OmsJournalEvent CommissionReceipt(double fee=1.25,double pnl=std::numeric_limits<double>::max()){
 IBEvent callback;callback.type=IBEventType::CommissionReport;callback.key="synthetic.01.01";
 callback.economics=CaptureIbCommissionEconomics(fee,"USD",pnl);
 auto e=ExecutionReceipt();RetainIbCallbackEconomics(callback,e);return e;
}
std::string Temp(){char name[]="/tmp/hepta-callback-economics-XXXXXX";int fd=mkstemp(name);assert(fd>=0);close(fd);return name;}
std::string JournalLine(const OmsJournalEvent& e){const auto file=Temp();
 {OmsJournal j;assert(j.Init(file));assert(j.Append(e));}
 std::ifstream input(file);std::string line;assert(bool(std::getline(input,line)));input.close();unlink(file.c_str());return line;
}
void TypedReceipts(){
 auto execution=ExecutionReceipt();const auto& data=execution.brokerEconomics;
 assert(data.ValidShape()&&data.valid&&data.shares==1&&data.executionPrice==3&&data.cumulativeQuantity==2&&data.cumulativeAverage==2.5);
 assert(execution.qty==2&&execution.price==2.5); // legacy cumulative semantics unchanged.
 auto malformed=execution.brokerEconomics;malformed.shares=-1;assert(!malformed.ValidShape());
 auto partial=IbEconomicEvidence();partial.commission=1;assert(!partial.Absent());
 auto fee=CommissionReceipt();assert(fee.brokerEconomics.ValidShape()&&fee.brokerEconomics.valid);
 assert(!fee.brokerEconomics.realizedPnlKnown&&fee.brokerEconomics.realizedPnl==0);
 assert(fee.orderId==-1&&fee.account.empty()&&fee.reqId.empty()&&fee.traceId.empty()&&fee.venueCorrelationId.empty());
 assert(fee.brokerClientId==-1&&fee.qty==0&&fee.price==0&&fee.source=="ib-api-callback");
 auto zero=CommissionReceipt(0,0);assert(zero.brokerEconomics.valid&&zero.brokerEconomics.realizedPnlKnown);
 auto rebate=CommissionReceipt(-.25,10);assert(rebate.brokerEconomics.valid&&rebate.brokerEconomics.commission==-.25);
 auto bad=CommissionReceipt(std::numeric_limits<double>::quiet_NaN());
 assert(!bad.brokerEconomics.valid&&bad.brokerEconomics.commission==0); // explicit unusable, never free execution.
 auto pending=CaptureIbExecutionEconomics(Contract(),123,456,1,2,1,2,true);
 assert(pending.valid&&pending.priceRevisionPending);
 auto invalid=CaptureIbExecutionEconomics(Contract(),123,456,1,std::numeric_limits<double>::max(),1,2,false);
 assert(!invalid.valid&&invalid.executionPrice==0&&invalid.ValidShape());
}
void StrictGroupedCodec(){
 OmsJournalEvent parsed;const auto line=JournalLine(ExecutionReceipt());assert(OmsJournal::ParseJsonLine(line,parsed));
 assert(parsed.brokerEconomics.version==1&&parsed.brokerEconomics.shares==1&&parsed.brokerEconomics.cumulativeQuantity==2);
 assert(parsed.brokerEconomics.contractIdentity==IbEconomicContractIdentity(Contract()));
 const auto feeLine=JournalLine(CommissionReceipt());assert(OmsJournal::ParseJsonLine(feeLine,parsed));
 assert(parsed.brokerEconomics.commission==1.25&&!parsed.brokerEconomics.realizedPnlKnown);
 auto missing=feeLine;const std::string token=",\"broker_econ_commission\":1.25";
 const auto pos=missing.find(token);assert(pos!=std::string::npos);missing.erase(pos,token.size());
 assert(!OmsJournal::ParseJsonLine(missing,parsed));
 auto malformed=feeLine;const auto at=malformed.find("\"broker_econ_valid\":1");assert(at!=std::string::npos);
 malformed.replace(at,std::string("\"broker_econ_valid\":1").size(),"\"broker_econ_valid\":true");
 assert(!OmsJournal::ParseJsonLine(malformed,parsed));
 auto future=feeLine;future.insert(future.size()-1,",\"broker_econ_future\":1");assert(!OmsJournal::ParseJsonLine(future,parsed));
 auto bound=CommissionReceipt();bound.account="DU123";const auto file=Temp();
 {OmsJournal j;assert(j.Init(file));assert(!j.Append(bound));assert(j.Replay([](const OmsJournalEvent&) {})==0);}
 unlink(file.c_str());
 assert(!OmsJournal::ParseJsonLine("{\"event\":\"broker_commission\"}",parsed));
 assert(!OmsJournal::ParseJsonLine("{\"event\":\"broker_execution\",\"broker_econ_version\":1}",parsed));
 assert(OmsJournal::ParseJsonLine("{\"event\":\"broker_execution\",\"qty\":2,\"price\":2.5}",parsed));
 assert(parsed.brokerEconomics.version==0&&parsed.qty==2&&parsed.price==2.5);
 auto legacy=ExecutionReceipt();legacy.brokerEconomics=IbEconomicEvidence();
 assert(JournalLine(legacy).find("broker_econ_")==std::string::npos);
 legacy.brokerEconomics.commission=1.25;
 const auto incomplete=Temp();{OmsJournal journal;assert(journal.Init(incomplete));assert(!journal.Append(legacy));}
 unlink(incomplete.c_str());
}
void FeeBeforeExecutionAndRestart(){const auto path=Temp();auto fee=CommissionReceipt();auto fill=ExecutionReceipt();
 {OmsJournal journal;assert(journal.Init(path));assert(journal.Append(fee));assert(journal.Append(fill));
  assert(journal.GetHealthSnapshot().durableSyncWrites>=2);}
 {OmsJournal replay;assert(replay.Init(path));int n=0;
  assert(replay.Replay([&](const OmsJournalEvent& e){
   if(n++==0){assert(e.eventType=="broker_commission"&&e.brokerEconomics.commission==1.25&&e.account.empty());}
   else assert(e.eventType=="broker_execution"&&e.brokerEconomics.shares==1&&e.qty==2);
  })==2);assert(n==2);}
 unlink(path.c_str());
}
void FullIdentity(){const auto c=Contract();const auto key=IbEconomicContractIdentity(c);auto venue=c;venue.exchange="CBOE";
 assert(IbEconomicContractIdentity(venue)==key); // routing venue differs from execution venue.
 for(int n=0;n<6;++n){auto changed=c;if(n==0)changed.strike++;if(n==1)changed.right="P";
  if(n==2)changed.multiplier="10";if(n==3)changed.lastTradeDateOrContractMonth="20261006";
  if(n==4)changed.currency="EUR";if(n==5)changed.tradingClass="SPXW";
  assert(IbEconomicContractIdentity(changed)!=key);}
}
}
int main(){TypedReceipts();StrictGroupedCodec();FeeBeforeExecutionAndRestart();FullIdentity();
 std::cout<<"PASS: native callback economic capture and same-OMS typed durable replay. SYNTHETIC ONLY, no Broker roundtrip proof.\n";}
