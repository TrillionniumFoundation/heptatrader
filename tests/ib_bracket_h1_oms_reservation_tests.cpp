#include "../HeptaTrade/execution/ib_bracket_h1_oms_reservation.h"
#include <unistd.h>
#include <cstdio>
#include <cstdlib>
#include <stdexcept>
#include <iostream>
#include <vector>
using namespace hepta_bracket_h1;
static void Must(bool b,const char* s){if(!b)throw std::runtime_error(s);}
static FrozenBracket Base(){
 FrozenBracket f;
 f.originalCommandKey="h1-shadow-001";f.account="DU-SYNTHETIC";f.owner="isolated-owner";
 f.executionDomain="PAPER";f.side="BUY";f.contractId=12087792;f.clientId=991;
 f.parentOrderId=4100;f.childOrderId=4101;f.connectionEpoch=81;
 f.positionGeneration=91;f.ownerFenceToken=23;f.intentObservedAtMs=1792080000123ULL;
 f.quantity=89200;f.referencePrice=1.1205;f.stopPrice=1.11825;f.minTick=.00005;return f;
}
int main(){
 try {
  auto f=Base();std::string reason;Reservation info;
  const std::string path="/tmp/hepta-h1-oms-XXXXXX";
  std::vector<char> chars(path.begin(),path.end());chars.push_back('\0');
  int fd=mkstemp(chars.data());Must(fd>=0,"create scratch");close(fd);
  unlink(chars.data());
  const std::string target(chars.data());
  {
   OmsJournal j;Must(j.Init(target),"init isolated original OMS");
   Must(ReservePair(j,f,info,reason),"durable pair refused");
   Must(info.replayComplete&&!info.sendAuthorized&&!info.driverStopAllowed,"durability granted broker authority");
   std::vector<OmsJournalEvent> events;
   Must(j.Replay([&](const OmsJournalEvent& e){events.push_back(e);})==2,"pair replay count");
   Reservation checked;Must(ConfirmReplay(events,f,checked,reason),"pair not revalidated");
   Must(checked.ids.childRef==info.ids.childRef,"H1 child changed");
   auto incomplete=events;incomplete.pop_back();
   Must(!ConfirmReplay(incomplete,f,checked,reason)&&checked.uncertain,"missing child accepted");
   auto swapped=events;swapped[1].venueCorrelationId=swapped[0].venueCorrelationId;
   Must(!ConfirmReplay(swapped,f,checked,reason)&&checked.uncertain,"swapped H1 accepted");
   auto duplicated=events;duplicated.push_back(events[1]);
   Must(!ConfirmReplay(duplicated,f,checked,reason)&&checked.uncertain,"duplicate child accepted");
   auto tampered=events;tampered[1].price+=.00005;
   Must(!ConfirmReplay(tampered,f,checked,reason)&&checked.uncertain,"changed stop accepted");
   auto otherVenue=events;otherVenue[1].venue="SIMULATED";
   Must(!ConfirmReplay(otherVenue,f,checked,reason)&&checked.uncertain,"wrong venue accepted");
   auto otherSource=events;otherSource[0].source="UNTRUSTED";
   Must(!ConfirmReplay(otherSource,f,checked,reason)&&checked.uncertain,"wrong source accepted");
   auto otherInstrument=events;otherInstrument[0].instrument="BTC.USD";
   Must(!ConfirmReplay(otherInstrument,f,checked,reason)&&checked.uncertain,"wrong instrument accepted");
   auto otherTimestamp=events;otherTimestamp[1].tsMs+=1000;
   Must(!ConfirmReplay(otherTimestamp,f,checked,reason)&&checked.uncertain,"wrong timestamp accepted");
   auto moved=f;moved.ownerFenceToken+=1;
   Must(!ConfirmReplay(events,moved,checked,reason)&&checked.uncertain,"different owner fence accepted");
   auto foreign=events;foreign[0].eventType="place_send_attempt";
   Must(!ConfirmReplay(foreign,f,checked,reason)&&checked.uncertain,"unknown send accepted");
   Must(MarkPotentialSend(j,info,false,reason),"parent attempt not durable");
   Must(MarkPotentialSend(j,info,true,reason),"child attempt not durable");
   std::vector<OmsJournalEvent> uncertain;
   Must(j.Replay([&](const OmsJournalEvent& e){uncertain.push_back(e);})==4,
        "attempt record not present in full journal");
   Must(!ConfirmReplay(uncertain,f,checked,reason)&&checked.uncertain &&
        !checked.sendAuthorized,"potential broker sends allowed reissue");
   Must(reason=="H1_BROKER_SEND_UNCERTAIN_REQUIRES_ORIGINAL_BROKER_RECOVERY",
        "unsafe send path recovered as committed");

  }
  {
   OmsJournal j;Must(j.Init(target),"reopen OMS failed");
   std::vector<OmsJournalEvent> events;
   Must(j.Replay([&](const OmsJournalEvent& e){events.push_back(e);})==4,"reboot lost attempts");
   Must(!ConfirmReplay(events,f,info,reason)&&info.uncertain,"unknown send reappeared as safe reservation");
   Must(!info.sendAuthorized&&!info.driverStopAllowed,"reboot minted order permissions");
  }
  // A future real sender must NEVER trust ordinary Append() when its
  // critical-write durability switch is off and async buffering is enabled.
  // This is a negative control for the exact pre-send crash boundary.
  {
   Must(setenv("HEPTA_OMS_SYNC_CRITICAL","0",1)==0,"env sync off");
   Must(setenv("HEPTA_OMS_ASYNC_FLUSH","1",1)==0,"env async on");
   Must(setenv("HEPTA_OMS_CRITICAL_FLUSH_QUEUED","0",1)==0,"env queue mode");
   const std::string mask="/tmp/hepta-h1-presend-XXXXXX";
   std::vector<char> tmp(mask.begin(),mask.end());tmp.push_back('\0');
   const int tfd=mkstemp(tmp.data());Must(tfd>=0,"async temp file");
   close(tfd);unlink(tmp.data());
   {
    OmsJournal j;Must(j.Init(tmp.data()),"async OMS init failed");
    Reservation saved;Must(ReservePair(j,f,saved,reason),"async H1 pair reserve");
    auto before=j.GetHealthSnapshot();
    Must(before.asyncEnabled&&!before.syncCritical,
         "did not activate async noncritical durability hazard");
    Must(MarkPotentialSend(j,saved,true,reason),"forced durable presend failed");
    auto after=j.GetHealthSnapshot();
    Must(after.durableSyncWrites==before.durableSyncWrites+1,
         "PRE_SEND_RETURNED_WITHOUT_FSYNC");
    Must(!saved.sendAuthorized&&!saved.driverStopAllowed,"async minted risk grant");
   }
   Must(unlink(tmp.data())==0,"async test cleanup failed");
   unsetenv("HEPTA_OMS_SYNC_CRITICAL");
   unsetenv("HEPTA_OMS_ASYNC_FLUSH");
   unsetenv("HEPTA_OMS_CRITICAL_FLUSH_QUEUED");
  }
  Must(unlink(target.c_str())==0,"test temporary cleanup");
  std::cout<<"ib_bracket_h1_oms_reservation=PASS\n";return 0;
 }catch(const std::exception& e){std::cerr<<"FAIL "<<e.what()<<'\n';return 1;}
}
