#include "adapter_ib/ib_admission_event_policy.h"
#include <cassert>
#include <iostream>
IBEvent Error(const std::string& code,long id=-1){IBEvent e;e.type=IBEventType::Error;e.key=code;e.id=id;return e;}
int main(){
 const auto notice=Error("2172");assert(IsIbGlobalUpgradeNotice(notice));assert(!IbAdmissionBlockingEvent(notice));
 const auto hmds=Error("2105");
 assert(IsIbUnscopedHistoricalFarmDisconnect(hmds));
 assert(!IbAdmissionBlockingEvent(hmds));
 for(int k=0;k<5;++k) {
  auto scoped=hmds;
  if(k==0)scoped.id=7302;
  if(k==1)scoped.account="DU123";
  if(k==2)scoped.order.orderRef="original-order";
  if(k==3)scoped.advancedOrderRejectJson="{}";
  if(k==4)scoped.key="21051";
  assert(!IsIbUnscopedHistoricalFarmDisconnect(scoped));
  assert(IbAdmissionBlockingEvent(scoped));
 }
 // Exact real 2026-10-14 callback: global sec-def farm 2157, ID -1.
 // Its loss is NOT an existing live CASH quote, TWS API socket, order or
 // account-state failure. Only the fully unscoped native callback is exempt.
 const auto secdef=Error("2157");
 assert(IsIbUnscopedSecDefFarmDisconnect(secdef));
 assert(!IbAdmissionBlockingEvent(secdef));
 for(int k=0;k<23;++k) {
  auto scoped=secdef;
  if(k==0)scoped.id=1000352;
  if(k==1)scoped.account="DU123";
  if(k==2)scoped.order.orderRef="broker-order";
  if(k==3)scoped.advancedOrderRejectJson="{}";
  if(k==4)scoped.requestId=701;
  if(k==5)scoped.key="21570";
  if(k==6)scoped.contract.symbol="EUR";
  if(k==7)scoped.order.action="BUY";
  if(k==8)scoped.economics.version=1;
  if(k==9)scoped.localMarketDataRequestError=true;
  if(k==10)scoped.requestId=-2;
  if(k==11)scoped.order.cashQuantity=100;
  if(k==12)scoped.order.positionEffect="OPEN";
  if(k==13)scoped.order.lmtPrice=1.1;
  if(k==14)scoped.order.auxPrice=2.0;
  if(k==15)scoped.order.outsideRth=true;
  if(k==16)scoped.order.totalQuantity=1;
  if(k==17)scoped.order.orderType="MKT";
  if(k==18)scoped.marketCapPrice=1.0;
  if(k==19)scoped.overflowGeneration=1;
  if(k==20)scoped.droppedEventCount=1;
  if(k==21)scoped.whyHeld="held";
  if(k==22)scoped.number=1.0;
  assert(!IsIbUnscopedSecDefFarmDisconnect(scoped));
  assert(IbAdmissionBlockingEvent(scoped));
 }
 for(int k=0;k<4;++k){auto scoped=notice;if(k==0)scoped.id=159;if(k==1)scoped.account="DU123";
  if(k==2)scoped.order.orderRef="original-order";if(k==3)scoped.advancedOrderRejectJson="{}";
  assert(!IsIbGlobalUpgradeNotice(scoped));assert(IbAdmissionBlockingEvent(scoped));
 }
 for(const std::string code:{"1100","1101","2110","1300","201","202","354","10090","10197","2173","bad","","999999999999999999999999"})
  assert(IbAdmissionBlockingEvent(Error(code)));
 for(const std::string code:{"2103","2104","2106","2107","2108","2109","2119","2158","1102"})
  assert(!IbAdmissionBlockingEvent(Error(code)));
 auto e=notice;e.type=IBEventType::ConnectionClosed;assert(IbAdmissionBlockingEvent(e));
 e.type=IBEventType::EventQueueOverflow;assert(IbAdmissionBlockingEvent(e));
 e.type=IBEventType::ExecutionDetails;assert(!IsIbGlobalUpgradeNotice(e));
 auto closed=secdef;closed.type=IBEventType::ConnectionClosed;
 assert(IbAdmissionBlockingEvent(closed));
 closed.type=IBEventType::EventQueueOverflow;
 assert(IbAdmissionBlockingEvent(closed));
 std::cout<<"PASS: global 2172 advisory retained without startup fault; scoped, unknown, order, entitlement and connection errors stay blocking\n";
}
