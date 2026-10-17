#include "../HeptaTrade/execution/ib_bracket_h1_identity.h"
#include <iostream>
#include <stdexcept>
#include <string>
#include <functional>
using namespace hepta_bracket_h1;
namespace {
void require(bool ok,const char* why){if(!ok)throw std::runtime_error(why);}
FrozenBracket original(){FrozenBracket x;x.originalCommandKey="original-001";x.account="DU-SYNTHETIC";x.owner="exec-owner-1";x.sessionId="session-1";x.executionDomain="PAPER";x.side="BUY";x.contractId=12087792;x.clientId=991;x.parentOrderId=4100;x.childOrderId=4101;x.connectionEpoch=81;x.positionGeneration=91;x.ownerFenceToken=23;x.intentObservedAtMs=1792080000123;x.quantity=89200;x.referencePrice=1.1205;x.stopPrice=1.11825;x.minTick=0.00005;return x;}
void reject(FrozenBracket x,const char* msg){BoundRefs r;std::string why;require(!BuildFrozenRefs(x,r,why),msg);require(r.parentRef.empty()&&r.childRef.empty(),"failed build left valid H1");}
void roundtrip(){BoundRefs b;std::string reason;auto x=original();require(BuildFrozenRefs(x,b,reason),"original group refused");require(b.parentRef.size()==45&&b.childRef.size()==45&&b.ocaGroup.size()==45,"H1 length wrong");require(b.parentRef.compare(0,2,"H1")==0&&b.childRef.compare(0,2,"H1")==0,"H1 prefix missing");require(b.parentRef!=b.childRef&&b.ocaGroup!=b.parentRef,"roles aliased");require(VerifyFrozenRefs(x,b,reason),"original failed to verify");require(!b.orderSendAuthority&&!b.maintenanceAuthorized,"H1 gave order privileges");BoundRefs repeat;require(BuildFrozenRefs(x,repeat,reason),"second build failed");require(repeat.parentRef==b.parentRef&&repeat.childRef==b.childRef&&repeat.ocaGroup==b.ocaGroup,"non-deterministic role binding");
    auto swapped=b;swapped.parentRef=b.childRef;require(!VerifyFrozenRefs(x,swapped,reason),"swapped parent permitted");
    swapped=b;swapped.ocaGroup=b.parentRef;require(!VerifyFrozenRefs(x,swapped,reason),"OCA substituted");
    swapped=b;swapped.childRef[5]=(swapped.childRef[5]=='A'?'B':'A');require(!VerifyFrozenRefs(x,swapped,reason),"corrupted H1 permitted");
    swapped=b;swapped.orderSendAuthority=true;require(!VerifyFrozenRefs(x,swapped,reason),"identity minted trading authority");
}
void mutationChangesRole(){auto x=original();BoundRefs base;std::string reason;require(BuildFrozenRefs(x,base,reason),"base missing");
    auto changed=[&](std::function<void(FrozenBracket&)> mutate){auto t=x;mutate(t);BoundRefs out;require(BuildFrozenRefs(t,out,reason),"valid mutation refused");require(out.parentRef!=base.parentRef&&out.childRef!=base.childRef,"mutation reused original refs");require(!VerifyFrozenRefs(t,base,reason),"old refs accepted after modified original");};
    changed([](FrozenBracket& v){v.originalCommandKey="original-002";});changed([](FrozenBracket& v){v.owner="other-owner";});
    changed([](FrozenBracket& v){v.sessionId="other-session";});changed([](FrozenBracket& v){v.account="DU-ANOTHER";});changed([](FrozenBracket& v){v.side="SELL";v.stopPrice=1.12300;});changed([](FrozenBracket& v){v.parentOrderId=4102;v.childOrderId=4103;});changed([](FrozenBracket& v){v.ownerFenceToken=88;});
}
void failClosed(){auto x=original();x.executionDomain="LIVE";reject(x,"LIVE identity accepted");x=original();x.account="U123";reject(x,"non-paper account accepted");x=original();x.contractId=123;reject(x,"wrong contract accepted");x=original();x.owner="";reject(x,"empty owner accepted");x=original();x.originalCommandKey="bad:key";reject(x,"unsafe command key accepted");x=original();x.ownerFenceToken=0;reject(x,"unfenced owner accepted");x=original();x.connectionEpoch=0;reject(x,"no broker epoch accepted");x=original();x.childOrderId=x.parentOrderId+2;reject(x,"nonconsecutive orderIDs accepted");x=original();x.minTick=.00001;reject(x,"wrong market rule accepted");x=original();x.stopPrice=1.11826;reject(x,"off-grid stop accepted");x=original();x.quantity=100001;reject(x,"qty cap accepted");x=original();x.side="SELL";reject(x,"inverted stop accepted");x=original();x.quantity=90000;reject(x,"notional cap accepted");x=original();x.stopPrice=0.0;reject(x,"zero stop accepted");x=original();x.stopPrice=std::numeric_limits<double>::infinity();reject(x,"infinite stop accepted");}
}
int main(){try{roundtrip();mutationChangesRole();failClosed();std::cout<<"IB_H1_BRACKET_ROLE_BINDING_PASS\n";return 0;}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
