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
 e.venue="IB";
 e.instrument="EUR.USD";e.venueCorrelationId=(std::string(type).find("parent")!=std::string::npos ?
    "parent-H1-original-01" : "stop-H1-original-01");
 e.side=(std::string(type).find("parent")!=std::string::npos?"BUY":"SELL");
 e.qty=1000;e.price=(std::string(type).find("parent")!=std::string::npos?1.1205:1.11825);
 if(std::string(type).find("attempt")!=std::string::npos)e.status="POSSIBLY_SENT";
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
   Check(j.AppendDurableCritical(Event("bracket_child_send_attempt",112)),"unknown send not durably sealed");
   Check(j.GetHealthSnapshot().durableSyncWrites>=1,"no forced fsync for unknown child");
   result=Inspect(j);Check(result.containsPotentialSend && result.highestObservedOrderId==112,"unknown send erased floor");
   Check(!AuthorizeResearchIdFloor(result,112,why),"reconnect reused possibly sent child ID");
   Check(!AuthorizeResearchIdFloor(result,113,why),
         "higher next valid ID bypassed UNKNOWN original send");
  }
  {
   OmsJournal j;Check(j.Init(path.data()),"restart open");
   auto result=Inspect(j);std::string why;
   Check(result.complete&&result.containsPotentialSend&&result.highestObservedOrderId==112,"reboot floor changed");
   Check(!AuthorizeResearchIdFloor(result,113,why)&&
         why=="H1_NATIVE_BROKER_SEND_UNKNOWN_REQUIRES_RECONCILIATION",
         "unreconciled send admitted after restart");
   auto orphan=Event("bracket_parent_reserved",120);
   orphan.reqId="separate-orphan";orphan.clientReqId=orphan.reqId;
   orphan.requestHash="other-original-group";
   Check(j.Append(orphan),"orphan record");
   result=Inspect(j);
   Check(result.hasOrphanReservation&&!result.complete,"orphan improperly marked complete");
   Check(!AuthorizeResearchIdFloor(result,121,why),"orphan allowed broker ID lease");
   // Before the fix, two parents and two children globally could be paired
   // across unrelated commands. Different req_id MUST NOT balance the orphan.
   auto foreignChild=Event("bracket_child_reserved",121);
   foreignChild.reqId="yet-another-original";foreignChild.clientReqId=foreignChild.reqId;
   foreignChild.requestHash="unrelated-child-group";
   Check(j.Append(foreignChild),"foreign child record");
   result=Inspect(j);
   Check(!result.complete && !result.reason.empty(),
         "unrelated child falsely balanced unrelated parent");
  }
  Check(unlink(path.data())==0,"cleanup");
  // Even with an unrelated fully valid pair, a new original command may
  // never reuse an old native ID. A global Parent/Child count cannot prove it.
  std::string other="/tmp/h1-native-duplicate-XXXXXX";
  std::vector<char> chars(other.begin(),other.end());chars.push_back('\0');
  const int secondFd=mkstemp(chars.data());Check(secondFd>=0,"duplicate tmp");
  close(secondFd);unlink(chars.data());
  {
    OmsJournal journal;Check(journal.Init(chars.data()),"duplicate journal init");
    Check(journal.AppendDurablePair(Event("bracket_parent_reserved",111),
          Event("bracket_child_reserved",112))==OmsJournal::DurablePairResult::Committed,
          "original duplicate fixture pair");
    auto conflict=Event("bracket_parent_reserved",111);
    conflict.reqId="distinct-original";conflict.clientReqId=conflict.reqId;
    conflict.requestHash="distinct-H1-group";
    Check(journal.AppendDurableCritical(conflict),"append reused ID fixture");
    const auto state=Inspect(journal);
    Check(!state.complete &&
          state.reason=="H1_OMS_DUPLICATE_NATIVE_ORDER_ID",
          "two original commands reused one numeric native ID");
  }
  Check(unlink(chars.data())==0,"cleanup duplicate journal");
  std::cout<<"h1_native_id_restart_floor=PASS\n";return 0;
 }catch(const std::exception& error){std::cerr<<"FAIL "<<error.what()<<'\n';return 1;}
}
