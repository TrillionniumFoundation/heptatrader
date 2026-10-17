#include "../HeptaTrade/adapter_ib/ib_native_h1_oca_exit_research.h"
#include <iostream>
#include <stdexcept>
#include <string>

using namespace hepta_bracket_h1;
using namespace hepta_ib_h1_oca_exit_research;
namespace {
void Need(bool b,const char* why){if(!b)throw std::runtime_error(why);}
FrozenBracket Original(const std::string& side="BUY") {
 FrozenBracket f;
 f.originalCommandKey="original-oca-001";
 f.account="DU-SYNTHETIC";f.owner="owner-A";f.sessionId="session-1";
 f.executionDomain="PAPER";f.side=side;
 f.contractId=12087792;f.clientId=991;
 f.parentOrderId=4100;f.childOrderId=4101;f.connectionEpoch=81;
 f.positionGeneration=91;f.ownerFenceToken=23;f.intentObservedAtMs=1792080000123ULL;
 f.quantity=89200;f.referencePrice=1.1205;
 f.stopPrice=(side=="BUY"?1.11825:1.1230);
 f.minTick=0.00005;return f;
}
BoundRefs Refs(const FrozenBracket& f) {
 BoundRefs b;std::string why;Need(BuildFrozenRefs(f,b,why),"could not create exact frozen H1");
 return b;
}
BrokerStopAndPositionEvidence Fresh(
  const FrozenBracket& f,const BoundRefs& refs) {
 BrokerStopAndPositionEvidence s;
 s.account=f.account;s.owner=f.owner;s.sessionId=f.sessionId;
 s.originalCommandKey=f.originalCommandKey;
 s.exactStopRef=refs.childRef;s.exactOcaGroup=refs.ocaGroup;
 s.stopStatus="Submitted";s.stopTimeInForce="GTC";
 s.stopAction=f.side=="BUY"?"SELL":"BUY";
 s.originalParentExecutionId="synthetic-original-fill-01";
 s.contractId=f.contractId;s.stopNativeOrderId=f.childOrderId;
 s.originalParentPermanentId=76543;
 s.expectedStopPermanentId=76544;s.brokerStopPermanentId=76544;
 s.proposedExitOrderId=4102;s.brokerClientId=f.clientId;s.stopOcaType=1;
 s.brokerEpoch=82;s.positionEpoch=82;s.openOrdersEpoch=82;
 s.observedAtMs=10000;s.validUntilMs=11000;
 s.exactOriginalBrokerExecution=true;
 s.brokerPositionAuthoritative=true;
 s.globalActiveOrdersComplete=true;
 s.originalStopOpenOrderAttested=true;
 s.originalStopOcaServerAttested=true;
 s.originalStopTriggerMethodAttested=true;
 s.ownerNewEntryFenceAttested=true;
 s.exactExitIdReserved=true;
 s.noUnknownOriginalCommands=true;
 s.noCancelOrReplacePending=true;
 s.activeOriginalAccountOrders=1;
 s.originalParentFilledQty=f.quantity;
 s.brokerSignedPosition=f.side=="BUY"?f.quantity:-f.quantity;
 s.stopOrderTotalQty=f.quantity;
 s.stopOrderRemainingQty=f.quantity;
 s.stopOrderFilledQty=0;
 s.stopPrice=f.stopPrice;
 return s;
}
void Refuse(const FrozenBracket& f,const BoundRefs& refs,
            const BrokerStopAndPositionEvidence& s,
            const char* message, std::uint64_t now=10500) {
 Candidate out;std::string reason;
 Need(!BuildUnsentH1OrdinaryOcaExit(f,refs,s,now,out,reason),message);
 Need(!out.sendAuthority&&!out.maintenanceAuthority&&!out.mayRetry,
      "failure manufactured order send permission");
}
void ExactCandidate(const std::string& side){
 const auto f=Original(side);
 const auto refs=Refs(f);
 const auto s=Fresh(f,refs);
 Candidate p;std::string reason;
 Need(BuildUnsentH1OrdinaryOcaExit(f,refs,s,10500,p,reason),
      "fresh fake original stop evidence refused");
 Need(p.contract.conId==12087792&&p.contract.secType=="CASH"&&
      p.contract.exchange=="IDEALPRO"&&p.contract.currency=="USD",
      "wrong native CASH account contract");
 Need(p.normalExit.action==(side=="BUY"?"SELL":"BUY")&&
      p.normalExit.account==f.account&&p.normalExit.clientId==991&&
      p.normalExit.orderId==4102&&p.normalExit.orderType=="MKT"&&
      p.normalExit.parentId==0&&p.normalExit.tif=="DAY"&&p.normalExit.transmit,
      "wrong native ordinary close group");
 Need(p.normalExit.ocaType==1&&p.normalExit.ocaGroup==refs.ocaGroup&&
      p.normalExit.orderRef.size()==45&&
      p.normalExit.orderRef.compare(0,2,"H1")==0&&
      p.normalExit.orderRef!=refs.parentRef&&
      p.normalExit.orderRef!=refs.childRef&&
      p.normalExit.orderRef!=refs.ocaGroup,
      "OCA H1 alias or missing overfill with-block");
 Need(DecimalFunctions::decimalToDouble(p.normalExit.totalQuantity)==f.quantity,
      "incorrect native close qty");
 Need(!p.sendAuthority&&!p.maintenanceAuthority&&
      !p.workingProtectionProven&&!p.mayRetry,
      "synthetic Broker testimony minted production authority");
 Candidate repeat;Need(BuildUnsentH1OrdinaryOcaExit(
     f,refs,s,10500,repeat,reason),"deterministic OCA exit refused");
 Need(p.normalExit.orderRef==repeat.normalExit.orderRef,
      "same original created two unrelated OCA exit identities");
}
void CancelAndPartialMustRefuse(){
 const auto f=Original();
 const auto refs=Refs(f);
 auto s=Fresh(f,refs);
 s.stopOrderFilledQty=100;s.stopOrderRemainingQty-=100;
 Refuse(f,refs,s,"partially filled stop accepted");
 s=Fresh(f,refs);s.stopStatus="Cancelled";
 Refuse(f,refs,s,"canceled stop accepted");
 s=Fresh(f,refs);s.brokerSignedPosition=0;
 Refuse(f,refs,s,"flat account triggered new reversal order");
 s=Fresh(f,refs);s.brokerSignedPosition=f.quantity-100;
 Refuse(f,refs,s,"reduced position reused old full stop");
 s=Fresh(f,refs);s.originalParentFilledQty/=2;
 Refuse(f,refs,s,"partially filled entry treated as fully protected");
 s=Fresh(f,refs);s.stopOrderRemainingQty=0;
 Refuse(f,refs,s,"terminal stop working proof accepted");
 s=Fresh(f,refs);s.noCancelOrReplacePending=false;
 Refuse(f,refs,s,"cancel in flight accepted");
 s=Fresh(f,refs);s.activeOriginalAccountOrders=2;
 Refuse(f,refs,s,"second simultaneous broker order ignored");
 s=Fresh(f,refs);s.globalActiveOrdersComplete=false;
 Refuse(f,refs,s,"partial inventory treated as complete");
 s=Fresh(f,refs);s.noUnknownOriginalCommands=false;
 Refuse(f,refs,s,"unknown submission treated as zero-fill");
}
void OwnerAndReconnectMustRefuse(){
 const auto f=Original();
 const auto refs=Refs(f);
 auto s=Fresh(f,refs);s.account="DU-OTHER";
 Refuse(f,refs,s,"other IB PAPER account accepted");
 s=Fresh(f,refs);s.brokerClientId=992;
 Refuse(f,refs,s,"other Broker client accepted");
 s=Fresh(f,refs);s.expectedStopPermanentId++;
 Refuse(f,refs,s,"replayed foreign stop permId accepted");
 s=Fresh(f,refs);s.stopNativeOrderId++;
 Refuse(f,refs,s,"wrong child order accepted");
 s=Fresh(f,refs);s.exactOcaGroup=refs.childRef;
 Refuse(f,refs,s,"OCA group substituted");
 s=Fresh(f,refs);s.stopOcaType=2;
 Refuse(f,refs,s,"OCA proportionate fill misrepresented as cancel-with-block");
 s=Fresh(f,refs);s.brokerEpoch=80;s.openOrdersEpoch=80;s.positionEpoch=80;
 Refuse(f,refs,s,"prior epoch stop accepted");
 s=Fresh(f,refs);s.brokerEpoch=83;
 Refuse(f,refs,s,"split broker epochs accepted");
 s=Fresh(f,refs);s.validUntilMs=10040;
 Refuse(f,refs,s,"expired original broker snapshot accepted");
 s=Fresh(f,refs);s.originalStopOcaServerAttested=false;
 Refuse(f,refs,s,"unconfirmed IB OCA formation accepted");
 s=Fresh(f,refs);s.exactExitIdReserved=false;
 Refuse(f,refs,s,"unreserved exit ID accepted");
 s=Fresh(f,refs);s.originalParentExecutionId="";
 Refuse(f,refs,s,"missing original execution reference accepted");
 s=Fresh(f,refs);s.ownerNewEntryFenceAttested=false;
 Refuse(f,refs,s,"absent owner new-entry fence accepted");
 s=Fresh(f,refs);s.stopPrice+=0.00005;
 Refuse(f,refs,s,"Broker-mutated stop trigger accepted");
 s=Fresh(f,refs);s.originalCommandKey="foreign-command";
 Refuse(f,refs,s,"original command takeover accepted");
 s=Fresh(f,refs);s.proposedExitOrderId=f.childOrderId;
 Refuse(f,refs,s,"unreserved exit native ID accepted");
 // Even a fully matching at 10.5s snapshot expires at 11.0s:
 s=Fresh(f,refs);Refuse(f,refs,s,"future stale candidate accepted",11001);
}
}
int main(){
 try {
  ExactCandidate("BUY");ExactCandidate("SELL");
  CancelAndPartialMustRefuse();OwnerAndReconnectMustRefuse();
  std::cout<<"h1_oca_exit_overfill_negative_checks=PASS\n";return 0;
 } catch(const std::exception& err){
  std::cerr<<"FAILED: "<<err.what()<<"\n";return 1;
 }
}
