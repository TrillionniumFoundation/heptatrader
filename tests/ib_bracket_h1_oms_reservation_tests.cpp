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
  Must(unlink(target.c_str())==0,"test temporary cleanup");
  std::cout<<"ib_bracket_h1_oms_reservation=PASS\n";return 0;
 }catch(const std::exception& e){std::cerr<<"FAIL "<<e.what()<<'\n';return 1;}
}
