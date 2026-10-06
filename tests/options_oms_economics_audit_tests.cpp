// Native coordinator + callback receipts into temporary OMS, then read-only audit.
// The synthetic venue cannot connect to a broker. No real execution/PnL proof.
#include "options/oms_economics_audit.h"
#include "adapter_ib/ib_callback_economics.h"
#include "execution/execution_coordinator.h"
#include <algorithm>
#include <cassert>
#include <cmath>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <limits>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>
using namespace hepta::options;
namespace {
std::string Temp(){char n[]="/tmp/hepta-option-audit-XXXXXX";int fd=mkstemp(n);assert(fd>=0);close(fd);return n;}
OmsOptionAuditScope Scope(){OmsOptionAuditScope s;s.account="DU123";s.agentId="options-audit-test";s.sessionId="options-session";
 s.brokerClientId=701;s.conId=913173379;std::string reason;assert(ParseCanonicalOcc("XSP   261005C00775000",s.identity,reason));return s;}
InstrumentRef Contract(){const auto i=Scope().identity;InstrumentRef c;c.symbol=i.symbol;c.secType="OPT";c.exchange="SMART";c.currency="USD";
 c.lastTradeDateOrContractMonth=i.expiry;c.right=i.right;c.strike=i.strikeMilli/1000.;c.multiplier="100";c.tradingClass=i.tradingClass;c.localSymbol=i.localSymbol;return c;}
IbPlaceOrderCommand Order(const std::string& id,const std::string& side,int qty=1){const auto s=Scope();IbPlaceOrderCommand c;
 c.context.agentId=s.agentId;c.context.sessionId=s.sessionId;c.context.toolCallId=id;c.context.strategy="audit-fixture";
 c.context.account=s.account;c.context.venue="IB";c.context.executionDomain="PAPER";c.context.decisionLeaseFencingToken=1;c.context.decisionLeaseGeneration=1;
 c.instrument="IBOPT:"+std::to_string(s.conId);c.contract=Contract();c.order.action=side;c.order.orderType="MKT";c.order.totalQuantity=qty;
 c.timeInForce="IOC";c.referencePrice=2;c.expiresAtMs=OmsJournal::NowEpochMs()+100000;return c;}
std::vector<OmsJournalEvent> Read(const std::string& path){std::vector<OmsJournalEvent> rows;std::string hash,reason;
 assert(ReadPrivateOmsAuditInput(path,rows,hash,reason));assert(hash.size()==64);return rows;}
OmsJournalEvent Callback(const OmsJournalEvent& send,long order,const std::string& id,double price,int qty=1,int cum=1,double avg=0){
 OmsJournalEvent e=send;e.eventType="broker_execution";e.eventId.clear();e.brokerCallbackType="execDetails";e.status="ExecutionDetails";
 e.source="agent:"+Scope().agentId;
 e.orderId=order;e.brokerExecutionId=id;e.brokerClientId=701;e.brokerServiceEpoch="service-A";e.brokerConnectionEpoch=11;e.brokerRequestId=-1;
 e.qty=cum;e.price=avg>0?avg:price;e.brokerEconomics=CaptureIbExecutionEconomics(Contract(),Scope().conId,1000+order,qty,price,cum,e.price,false);return e;}
OmsJournalEvent Fee(const OmsJournalEvent& fill,double fee=1.25){OmsJournalEvent e=fill;IBEvent callback;
 callback.type=IBEventType::CommissionReport;callback.key=fill.brokerExecutionId;
 callback.economics=CaptureIbCommissionEconomics(fee,"USD",std::numeric_limits<double>::max());
 RetainIbCallbackEconomics(callback,e);return e;}
OmsJournalEvent Terminal(const OmsJournalEvent& fill,const std::string& status="Filled",int qty=1){auto e=fill;
 e.eventType="broker_order_status";e.brokerCallbackType="orderStatus";e.brokerEconomics=IbEconomicEvidence();e.brokerExecutionId.clear();
 e.status=status;e.qty=qty;e.brokerRemainingQuantity=0;return e;}
std::vector<OmsJournalEvent> Base(int quantity=1,bool close=true){const auto path=Temp();
 {OmsJournal j;assert(j.Init(path));long next=41;ExecutionCoordinatorCallbacks callbacks;
 callbacks.validateDecisionLease=[](const AgentExecutionContext&,const std::string&,std::string*){return true;};
 callbacks.placement=VenuePlacement::Immediate([&](const PlaceOrderCommand&,const std::string&){return VenuePlaceResult::Submitted(++next);});
 ExecutionCoordinator coordinator(j,callbacks);
 assert(coordinator.PlaceIbOrder(Order("open","BUY",quantity)).status==ExecutionCommandStatus::Accepted);
 if(close)assert(coordinator.PlaceIbOrder(Order("close","SELL",quantity)).status==ExecutionCommandStatus::Accepted);
 }
 auto rows=Read(path);unlink(path.c_str());return rows;
}
OmsJournalEvent Send(const std::vector<OmsJournalEvent>& rows,const std::string& id){
 for(const auto& e:rows)if(e.eventType=="place_send_attempt" && e.reqId==id)return e;assert(false);return {};
}
std::vector<OmsJournalEvent> Roundtrip(){auto rows=Base();auto buy=Callback(Send(rows,"open"),42,"buy.01.01",2);
 auto sell=Callback(Send(rows,"close"),43,"sell.01.01",2.2);
 // Fees precede executions; terminal may precede callback dispatch as well.
 rows.push_back(Fee(sell));rows.push_back(Terminal(buy));rows.push_back(Fee(buy));rows.push_back(sell);rows.push_back(buy);rows.push_back(Terminal(sell));return rows;}
bool Has(const OmsOptionAuditResult& r,const std::string& s){return std::find(r.issues.begin(),r.issues.end(),s)!=r.issues.end();}
void JoinedAndRestart(){auto rows=Roundtrip();auto result=AuditOptionOms(Scope(),rows);
 if(!result.executionCashFlowComplete || !result.feesComplete)std::cerr<<result.Json()<<"\n";
 assert(result.executionCashFlowComplete&&result.feesComplete&&result.executions==2&&result.sendAttempts==2);
 assert(std::fabs(result.executionCashFlowUsd-20)<1e-8&&result.feesUsd==2.5);
 assert(result.Json().find("\"roundtrip_proven\":false")!=std::string::npos);
 assert(result.Json().find("\"realized_pnl_usd\":null")!=std::string::npos);
 const auto path=Temp();
 const auto child=fork();assert(child>=0);if(child==0){OmsJournal j;if(!j.Init(path))_exit(80);
  for(const auto& e:rows)if(!j.Append(e))_exit(81);_exit(77);}
 int status=0;assert(waitpid(child,&status,0)==child&&WIFEXITED(status)&&WEXITSTATUS(status)==77);
 const auto reloaded=Read(path);assert(AuditOptionOms(Scope(),reloaded).Json()==result.Json());
 for(const auto& e:rows)if(e.eventType=="broker_execution"){
  auto replay=e;replay.brokerServiceEpoch="service-B";replay.brokerConnectionEpoch=12;replay.brokerRequestId=9001;rows.push_back(replay);break;}
 auto again=AuditOptionOms(Scope(),rows);assert(again.duplicateExecutions==1&&again.executions==2&&again.feesComplete);
 assert(again.executionCashFlowUsd==result.executionCashFlowUsd&&again.feesUsd==result.feesUsd);unlink(path.c_str());
}
void MissingAndAmbiguous(){auto base=Roundtrip();
 for(int variant=0;variant<12;++variant){auto rows=base;bool changed=false;
  for(auto& e:rows)if(e.eventType=="broker_execution" && e.reqId=="open"){
   if(variant==0)e.brokerEconomics=IbEconomicEvidence();
   if(variant==1)e.brokerEconomics.priceRevisionPending=1;
   if(variant==2)e.brokerEconomics.contractId++;
   if(variant==3)e.venueCorrelationId="other";
   if(variant==4)e.brokerClientId++;
   if(variant==5)e.brokerEconomics.contractIdentity="wrong-expiry";
   if(variant==6)e.brokerEconomics.shares=.5;
   if(variant==7)e.brokerEconomics.cumulativeAverage=9;
   if(variant==8)e.account="DU999";
   if(variant==9)e.brokerExecutionId.clear();
   if(variant==10)e.traceId="other-session";
   if(variant==11)e.brokerServiceEpoch.clear();changed=true;break;
  }
  assert(changed);const auto result=AuditOptionOms(Scope(),rows);assert(!result.executionCashFlowComplete&&!result.feesComplete);
 }
 auto collision=base;for(const auto& e:base)if(e.eventType=="broker_execution"){
  auto foreign=e;foreign.account="DU_OTHER";foreign.brokerClientId=999;collision.push_back(foreign);break;}
 assert(Has(AuditOptionOms(Scope(),collision),"EXECUTION_ID_COLLISION_OR_ECONOMIC_CONFLICT"));
 auto wrongOwner=base;for(const auto& e:base)if(e.eventType=="broker_execution"){
  auto replay=e;replay.traceId="foreign-session";wrongOwner.push_back(replay);break;}
 assert(Has(AuditOptionOms(Scope(),wrongOwner),"EXECUTION_NATIVE_OWNER_JOIN_UNPROVEN"));
 auto lostFee=base;lostFee.erase(std::find_if(lostFee.begin(),lostFee.end(),[](const OmsJournalEvent& e){return e.eventType=="broker_commission";}));
 auto r=AuditOptionOms(Scope(),lostFee);assert(r.executionCashFlowComplete&&!r.feesComplete);
 assert(r.Json().find("\"net_execution_cash_flow_usd\":null")!=std::string::npos);
 auto badFee=base;for(auto& e:badFee)if(e.eventType=="broker_commission"){e.brokerEconomics.valid=0;e.brokerEconomics.commission=0;break;}
 assert(!AuditOptionOms(Scope(),badFee).feesComplete);
}
void PartialCancellationNotCompletedTotal(){auto rows=Base(2,false);auto fill=Callback(Send(rows,"open"),42,"partial.01.01",2);
 rows.push_back(fill);rows.push_back(Fee(fill));auto done=Terminal(fill,"Cancelled",2);
 done.eventType="broker_completed_order";done.brokerCallbackType="completedOrder";rows.push_back(done);
 auto incomplete=AuditOptionOms(Scope(),rows);assert(!incomplete.executionCashFlowComplete&&incomplete.ignoredCompletedOrders==1);
 rows.push_back(Terminal(fill,"Cancelled",1));const auto result=AuditOptionOms(Scope(),rows);
 assert(result.executionCashFlowComplete&&result.feesComplete&&result.boughtContracts==1&&result.soldContracts==0);
 assert(result.executionCashFlowUsd==-200&&result.Json().find("\"realized_pnl_usd\":null")!=std::string::npos);
 // Full-order terminal quantity would fabricate the unfilled second contract.
 rows.back().qty=2;assert(!AuditOptionOms(Scope(),rows).executionCashFlowComplete);
}
void CumulativeCheckpointsAndCorrections(){auto rows=Base(2,false);auto send=Send(rows,"open");
 auto first=Callback(send,42,"first.01.01",2,1,1,2);auto second=Callback(send,42,"second.01.01",3,1,2,2.5);
 rows.push_back(second);rows.push_back(Fee(second));rows.push_back(Terminal(second,"Filled",2));
 assert(!AuditOptionOms(Scope(),rows).executionCashFlowComplete);
 rows.push_back(first);rows.push_back(Fee(first));auto result=AuditOptionOms(Scope(),rows);
 assert(result.executionCashFlowComplete&&result.boughtContracts==2&&result.executionCashFlowUsd==-500);
 auto bad=rows;for(auto& e:bad)if(e.brokerExecutionId==first.brokerExecutionId&&e.eventType=="broker_execution"){
  e.brokerEconomics.cumulativeAverage=9;e.price=9;}
 assert(Has(AuditOptionOms(Scope(),bad),"INDIVIDUAL_CUMULATIVE_CHECKPOINT_MISMATCH"));
 auto correction=first;correction.brokerExecutionId="first.01.02";rows.push_back(correction);
 assert(Has(AuditOptionOms(Scope(),rows),"EXECUTION_CORRECTION_RECONCILIATION_REQUIRED"));
}
void SendCounters(){auto rows=Roundtrip();rows.push_back(Send(rows,"open"));
 auto result=AuditOptionOms(Scope(),rows);assert(result.sendAttempts==3&&!result.executionCashFlowComplete);
 assert(Has(result,"NATIVE_SEND_ATTEMPT_COUNT_NOT_ONE"));
 auto empty=AuditOptionOms(Scope(),{});assert(!empty.executionCashFlowComplete&&Has(empty,"NO_MATCHING_NATIVE_SEND"));}
void InputSafety(){const auto path=Temp();auto rows=Roundtrip();{OmsJournal j;assert(j.Init(path));for(const auto& e:rows)assert(j.Append(e));}
 auto before=Read(path);std::vector<OmsJournalEvent> out;std::string hash,reason;
 assert(ReadPrivateOmsAuditInput(path,out,hash,reason));const auto oldHash=hash;
 assert(ReadPrivateOmsAuditInput(path,out,hash,reason)&&hash==oldHash);
 const auto link=path+"-link";assert(symlink(path.c_str(),link.c_str())==0);assert(!ReadPrivateOmsAuditInput(link,out,hash,reason));unlink(link.c_str());
 assert(chmod(path.c_str(),0644)==0);assert(!ReadPrivateOmsAuditInput(path,out,hash,reason));assert(chmod(path.c_str(),0600)==0);
 struct stat st{};assert(stat(path.c_str(),&st)==0);assert(truncate(path.c_str(),st.st_size-1)==0);
 assert(!ReadPrivateOmsAuditInput(path,out,hash,reason)&&reason=="AUDIT_TRUNCATED_TAIL"&&out.empty());unlink(path.c_str());
}
}
int main(){assert(geteuid()!=0);JoinedAndRestart();MissingAndAmbiguous();PartialCancellationNotCompletedTotal();
 CumulativeCheckpointsAndCorrections();SendCounters();InputSafety();
 std::cout<<"PASS native temporary OMS -> offline audit: fee-first, crash/reopen, partial cancellation, identities, correction/gap refusal. SYNTHETIC ONLY.\n";}
