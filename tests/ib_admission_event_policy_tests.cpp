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
 std::cout<<"PASS: global 2172 advisory retained without startup fault; scoped, unknown, order, entitlement and connection errors stay blocking\n";
}
