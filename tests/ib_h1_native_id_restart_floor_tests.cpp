#include "../HeptaTrade/execution/ib_h1_native_id_restart_floor.h"
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <vector>
#include <unistd.h>
using namespace hepta_h1_restart_floor;
static void Check(bool ok,const char* text){if(!ok)throw std::runtime_error(text);}
static OmsJournalEvent Event(const char* type,long id){
 OmsJournalEvent e;e.eventType=type;e.orderId=id;
 e.account="DU-SYNTHETIC";e.reqId="original-01";e.clientReqId=e.reqId;
 e.requestHash="frozen-H1-group";e.executionDomain="PAPER";e.source="SHADOW_NO_BROKER_EGRESS";
 return e;
}
int main(){
 try{
  std::string tmp="/tmp/h1-native-id-restart-XXXXXX";
  std::vector<char> path(tmp.begin(),tmp.end());path.push_back('\0');
  int fd=mkstemp(path.data());Check(fd>=0,"mkstemp");close(fd);unlink(path.data());
  {
   OmsJournal j;Check(j.Init(path.data()),"init");
   Check(j.AppendDurablePair(Event("bracket_parent_reserved",111),
       Event("bracket_child_reserved",112))==OmsJournal::DurablePairResult::Committed,"pair durable");
   auto result=Inspect(j);Check(result.complete && result.highestObservedOrderId==112,"floor lost");
   std::string why;
   Check(!AuthorizeResearchIdFloor(result,112,why),"equal next valid ID admitted");
   Check(!AuthorizeResearchIdFloor(result,111,why),"lower next valid ID admitted");
   Check(AuthorizeResearchIdFloor(result,113,why),"higher next valid ID refused");
   Check(j.Append(Event("bracket_child_send_attempt",112)),"unknown send receipt");
   result=Inspect(j);Check(result.containsPotentialSend && result.highestObservedOrderId==112,"unknown send erased floor");
   Check(!AuthorizeResearchIdFloor(result,112,why),"reconnect reused possibly sent child ID");
  }
  {
   OmsJournal j;Check(j.Init(path.data()),"restart open");
   auto result=Inspect(j);std::string why;
   Check(result.complete&&result.containsPotentialSend&&result.highestObservedOrderId==112,"reboot floor changed");
   Check(AuthorizeResearchIdFloor(result,113,why),"reboot higher broker ID refused");
   Check(j.Append(Event("bracket_parent_reserved",120)),"orphan record");
   result=Inspect(j);
   Check(result.hasOrphanReservation&&!result.complete,"orphan improperly marked complete");
   Check(!AuthorizeResearchIdFloor(result,121,why),"orphan allowed broker ID lease");
  }
  Check(unlink(path.data())==0,"cleanup");
  std::cout<<"h1_native_id_restart_floor=PASS\n";return 0;
 }catch(const std::exception& error){std::cerr<<"FAIL "<<error.what()<<'\n';return 1;}
}
