#include "adapter_ib/ib_local_cancel_notice.h"
#include "adapter_ib/ib_admission_event_policy.h"
#include <iostream>
#include <stdexcept>
#include <functional>
#include <limits>
#include <vector>
static void need(bool b,const char* text){if(!b)throw std::runtime_error(text);}
struct Fixture {
 IBLocalCancelNoticeWitness witness;IBMarketDataAdmissionState state;IBOrderLite order;
 Fixture(){witness.Reset(3,701,"DU_SYNTHETIC");order.action="SELL";order.totalQuantity=.30727158;order.orderRef="H1-local-submitted-reference";witness.Record(370,order);}
 IBEvent nativeStatus()const{IBEvent e;e.type=IBEventType::OrderStatus;e.id=370;e.connectionEpoch=3;e.brokerClientId=701;e.key="Cancelled";e.number=81408.5;e.number2=.02762078;e.number3=.2796508;return e;}
 IBEvent notice()const{IBEvent e;e.type=IBEventType::Error;e.id=370;e.connectionEpoch=3;e.key="202";e.value="localized cancellation text is not authority";return e;}
 void callback(const IBEvent&e){witness.ObserveNativeStatus(e);if(IbAdmissionBlockingEvent(e))state.ObserveBlockingCallback(!witness.Corroborates(e));}
};
int main(){try{
 {Fixture f;f.callback(f.notice());need(f.state.IsFailed(),"uncorroborated 202 still faults idle");}
 {Fixture f;f.callback(f.nativeStatus());auto e=f.notice();need(IbAdmissionBlockingEvent(e),"generic 202 classification remains blocking");need(f.witness.Corroborates(e),"exact native cancellation corroborated");f.callback(e);need(!f.state.IsFailed(),"known local cancellation not global idle quote fault");std::uint64_t g=0;need(f.state.Begin(g),"subsequent quote admission possible");}
 for(bool reserved:{false,true}) {Fixture f;f.callback(f.nativeStatus());std::uint64_t g=0;need(f.state.Begin(g),"begin quote");if(reserved)need(f.state.TryReserve(g),"reserve quote");f.callback(f.notice());need(f.state.IsFailed(),"even known cancellation still blocks OPEN/RESERVED admission");}
 const std::vector<std::function<void(IBEvent&)>> invalidStatus={
  [](IBEvent&e){e.brokerClientId=702;},[](IBEvent&e){e.brokerClientId=-1;},
  [](IBEvent&e){e.account="DU_OTHER";},[](IBEvent&e){e.connectionEpoch=2;},
  [](IBEvent&e){e.connectionEpoch=4;},[](IBEvent&e){e.key="PendingCancel";},
  [](IBEvent&e){e.value="execDetails";e.requestId=-1;},[](IBEvent&e){e.number2=2;},
  [](IBEvent&e){e.number3=std::numeric_limits<double>::quiet_NaN();},
  [](IBEvent&e){e.number=0;},[](IBEvent&e){e.id=371;}};
 for(const auto& change:invalidStatus){Fixture f;auto s=f.nativeStatus();change(s);f.callback(s);f.callback(f.notice());need(f.state.IsFailed(),"untrusted native status cannot corroborate notice");}
 const std::vector<std::function<void(IBEvent&)>> invalidNotice={
  [](IBEvent&e){e.id=-1;},[](IBEvent&e){e.id=371;},[](IBEvent&e){e.connectionEpoch=2;},
  [](IBEvent&e){e.connectionEpoch=4;},[](IBEvent&e){e.brokerClientId=702;},
  [](IBEvent&e){e.account="DU_OTHER";},[](IBEvent&e){e.order.orderRef="another-order";},
  [](IBEvent&e){e.advancedOrderRejectJson="{}";},[](IBEvent&e){e.key="201";},
  [](IBEvent&e){e.key="10197";},[](IBEvent&e){e.key="2020";}};
 for(const auto& change:invalidNotice){Fixture f;f.callback(f.nativeStatus());auto e=f.notice();change(e);f.callback(e);need(f.state.IsFailed(),"wrong notice still faults admission");}
 {Fixture f;f.callback(f.nativeStatus());f.witness.Record(370,f.order);need(!f.witness.Corroborates(f.notice()),"numeric id reuse invalidates previous witness");}
 {Fixture f;f.callback(f.nativeStatus());f.witness.Reset(4,701,"DU_SYNTHETIC");auto e=f.notice();e.connectionEpoch=4;need(!f.witness.Corroborates(e),"new epoch cannot borrow old cancellation");}
 {Fixture f;f.callback(f.nativeStatus());auto s=f.nativeStatus();s.key="Filled";s.number2=f.order.totalQuantity;s.number3=0;f.callback(s);need(!f.witness.Corroborates(f.notice()),"subsequent full fill invalidates cancel witness");}
 std::cout<<"PASS narrow notice cases; generic and in-flight safety retained; no broker calls\n";return 0;
 }catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
