#include "portfolio/simulator_fx_accounting.h"
#include <algorithm>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <new>
#include <openssl/evp.h>
#include <thread>

namespace fault {
thread_local long at = -1, calls = 0;
thread_local bool measure = false;
thread_local std::size_t largest = 0, total = 0;
thread_local int crypto = 0;
}
__attribute__((noinline)) void* operator new(std::size_t n) {
    if (fault::measure) { fault::largest = std::max(fault::largest,n); fault::total += n; }
    if (fault::at >= 0 && fault::calls++ == fault::at) throw std::bad_alloc();
    void* p = std::malloc(n ? n : 1); if (!p) throw std::bad_alloc(); return p;
}
__attribute__((noinline)) void* operator new[](std::size_t n) { return ::operator new(n); }
__attribute__((noinline)) void operator delete(void* p) noexcept { std::free(p); }
__attribute__((noinline)) void operator delete[](void* p) noexcept { std::free(p); }
#if defined(__cpp_sized_deallocation)
__attribute__((noinline)) void operator delete(void* p,std::size_t) noexcept { std::free(p); }
__attribute__((noinline)) void operator delete[](void* p,std::size_t) noexcept { std::free(p); }
#endif
extern "C" {
int __real_EVP_DigestInit_ex(EVP_MD_CTX*,const EVP_MD*,ENGINE*);
int __real_EVP_DigestUpdate(EVP_MD_CTX*,const void*,size_t);
int __real_EVP_DigestFinal_ex(EVP_MD_CTX*,unsigned char*,unsigned*);
int __wrap_EVP_DigestInit_ex(EVP_MD_CTX* c,const EVP_MD* d,ENGINE* e) { return fault::crypto==1 ? 0 : __real_EVP_DigestInit_ex(c,d,e); }
int __wrap_EVP_DigestUpdate(EVP_MD_CTX* c,const void* b,size_t n) { return fault::crypto==2 ? 0 : __real_EVP_DigestUpdate(c,b,n); }
int __wrap_EVP_DigestFinal_ex(EVP_MD_CTX* c,unsigned char* b,unsigned* n) { return fault::crypto==3 ? 0 : __real_EVP_DigestFinal_ex(c,b,n); }
}
namespace {
std::uint64_t assertions = 0;
void Check(bool ok,const char* expression,int line) { ++assertions; if(!ok) { std::cerr<<line<<": "<<expression<<'\n';std::abort(); } }
#define REQUIRE(x) Check(static_cast<bool>(x),#x,__LINE__)
constexpr SimulatorFxRaw S=1000000, M=9000000000000000LL;
using Accounting=SimulatorFxAccounting;
SimulatorFxInstrument Spec() { SimulatorFxInstrument s;s.revision="fx-sim-v1";s.effectiveFromMs=900;s.effectiveUntilMs=2000;return s; }
SimulatorFxOpening Opening() { SimulatorFxOpening o;o.bookId="book-a";o.instrumentRevision=Spec().revision;o.quoteBalanceRaw=1000*S;o.asOfMs=1000;return o; }
SimulatorFxEvent Fill(std::uint64_t seq,const std::string& exec,SimulatorFxSide side,SimulatorFxRaw q,SimulatorFxRaw p) {
    SimulatorFxEvent e;e.eventId="ev-"+std::to_string(seq);e.executionId=exec;e.bookId=Opening().bookId;
    e.instrument=Spec().instrument;e.instrumentRevision=Spec().revision;e.sequence=seq;
    e.eventTimeMs=e.recordedAtMs=1000+seq*100;e.side=side;e.quantityRaw=q;e.priceRaw=p;return e;
}
SimulatorFxEvent Fee(std::uint64_t seq,const std::string& exec,SimulatorFxRaw fee) {
    auto e=Fill(seq,exec,SimulatorFxSide::None,0,0);e.kind=SimulatorFxEventKind::Commission;
    e.commissionRaw=fee;e.commissionCurrency="USD";return e;
}
std::vector<SimulatorFxEvent> Events() { return {Fill(1,"exec-a",SimulatorFxSide::Buy,100*S,1100000),Fee(2,"exec-a",S),
    Fill(3,"exec-b",SimulatorFxSide::Sell,40*S,1200000),Fee(4,"exec-b",S/2)}; }
void Rejected(const SimulatorFxReplayResult& r) {
    REQUIRE(!r.accepted);const auto& p=r.projection;
    REQUIRE(p.digest.empty() && p.bookId.empty() && p.instrumentRevision.empty());
    REQUIRE(p.baseBalanceRaw==0 && p.quoteBalanceRaw==0 && p.commissionsRaw==0 && p.fills==0 && p.commissions==0);
    REQUIRE(p.lastSequence==0 && !p.feesComplete);
}
SimulatorFxObservation Observed() {
    SimulatorFxObservation o;o.bookId="book-a";o.instrumentRevision="fx-sim-v1";o.asOfMs=1800;o.lastSequence=4;
    o.baseBalanceRaw=60*S;o.quoteBalanceRaw=936500000;o.commissionsRaw=1500000;o.fills=o.commissions=2;return o;
}
void TestCashConservationAndGoldenReplay() {
    const auto r=Accounting::Replay(Spec(),Opening(),Events(),1800);REQUIRE(r.accepted);const auto& p=r.projection;
    REQUIRE(p.baseBalanceRaw==60*S && p.quoteBalanceRaw==936500000 && p.commissionsRaw==1500000);
    REQUIRE(p.netBaseTradeRaw==60*S && p.netQuoteTradeRaw==-62*S && p.feesComplete);
    REQUIRE(p.baseBalanceRaw==Opening().baseBalanceRaw+p.netBaseTradeRaw);
    REQUIRE(p.quoteBalanceRaw==Opening().quoteBalanceRaw+p.netQuoteTradeRaw-p.commissionsRaw);
    REQUIRE(p.fills==2 && p.commissions==2 && p.duplicates==0 && p.lastSequence==4 && p.lastRecordedAtMs==1400);
    const auto match=Accounting::Reconcile(Spec(),Opening(),Events(),1800,Observed());
    REQUIRE(match.matched && match.projectionDigest==p.digest);
    REQUIRE(Accounting::Replay(Spec(),Opening(),Events(),1800).projection.digest==p.digest);
    std::cout<<"fx_golden_projection="<<p.digest<<'\n';
}
void TestDuplicateRecordsNeverDoublePost() {
    const auto original=Events();auto events=original;events.insert(events.begin()+2,original[0]);events.push_back(original[3]);
    const auto r=Accounting::Replay(Spec(),Opening(),events,1800);REQUIRE(r.accepted && r.projection.duplicates==2);
    REQUIRE(r.projection.digest==Accounting::Replay(Spec(),Opening(),original,1800).projection.digest);
    REQUIRE(Accounting::Reconcile(Spec(),Opening(),events,1800,Observed()).matched);
    for(int field=0;field<14;++field) {
        events=original;auto e=original[0];
        switch(field) {
        case 0:e.executionId="different";break;case 1:e.bookId="other";break;case 2:e.instrument="USD.EUR";break;
        case 3:e.instrumentRevision="v2";break;case 4:e.sequence=5;break;case 5:++e.eventTimeMs;break;
        case 6:++e.recordedAtMs;break;case 7:e.kind=SimulatorFxEventKind::Commission;break;
        case 8:e.side=SimulatorFxSide::Sell;break;case 9:e.quantityRaw+=S;break;case 10:++e.priceRaw;break;
        case 11:e.commissionRaw=1;break;case 12:e.commissionCurrency="USD";break;case 13:e.eventId="distinct";e.sequence=5;break;
        }
        events.push_back(e);Rejected(Accounting::Replay(Spec(),Opening(),events,1800));
    }
}
void TestSequenceCaptureAndFeeAssociation() {
    for(int mode=0;mode<11;++mode) {
        auto e=Events();
        switch(mode) {
        case 0:e[1].sequence=3;break;case 1:e[0].sequence=0;break;case 2:e[0].sequence=std::uint64_t(-1);break;
        case 3:std::swap(e[0],e[1]);break;case 4:e[1].executionId="unknown";break;
        case 5:e[3].executionId="exec-a";break;case 6:e[2].executionId="exec-a";break;
        case 7:e[1].eventTimeMs=1050;break;case 8:e[2].recordedAtMs=1150;e[2].eventTimeMs=1100;break;
        case 9:e[0].recordedAtMs=1801;break;case 10:e[0].eventTimeMs=999;break;
        }
        Rejected(Accounting::Replay(Spec(),Opening(),e,1800));
    }
    auto e=Events();e[1].eventTimeMs=e[0].eventTimeMs;REQUIRE(Accounting::Replay(Spec(),Opening(),e,1800).accepted);
    // Equal recording timestamps and out-of-order economic times remain explicit.
    e=Events();for(auto& event:e) event.recordedAtMs=1500;e[2].eventTimeMs=1050;
    REQUIRE(Accounting::Replay(Spec(),Opening(),e,1800).accepted);
}
void TestMissingFeeAndReconciliationCutFailClosed() {
    auto events=Events();events.pop_back();
    const auto r=Accounting::Replay(Spec(),Opening(),events,1800);REQUIRE(r.accepted && !r.projection.feesComplete);
    auto observation=Observed();observation.lastSequence=3;observation.commissions=1;observation.commissionsRaw=S;observation.quoteBalanceRaw=937*S;
    const auto pending=Accounting::Reconcile(Spec(),Opening(),events,1800,observation);
    REQUIRE(!pending.matched && pending.projectionDigest.empty());REQUIRE(std::string(pending.reasonCode)=="FX_LEDGER_COMMISSION_PENDING");
    for(int mode=0;mode<9;++mode) {
        auto o=Observed();
        switch(mode){case 0:o.bookId="wrong";break;case 1:o.instrumentRevision="wrong";break;case 2:--o.asOfMs;break;
        case 3:--o.lastSequence;break;case 4:++o.baseBalanceRaw;break;case 5:++o.quoteBalanceRaw;break;
        case 6:++o.commissionsRaw;break;case 7:++o.fills;break;case 8:++o.commissions;break;}
        const auto result=Accounting::Reconcile(Spec(),Opening(),Events(),1800,o);
        REQUIRE(!result.matched && result.projectionDigest.empty());
    }
    events=Events();events[1].commissionRaw=0;events[3].commissionRaw=0;
    REQUIRE(Accounting::Replay(Spec(),Opening(),events,1800).projection.feesComplete);
}
void TestNoMarginRoundingOrCrossCurrencyAssumptions() {
    auto e=Events();auto opening=Opening();opening.quoteBalanceRaw=109*S;
    Rejected(Accounting::Replay(Spec(),opening,e,1800));
    opening=Opening();e={Fill(1,"short",SimulatorFxSide::Sell,S,1100000)};Rejected(Accounting::Replay(Spec(),opening,e,1800));
    e=Events();e[1].commissionRaw=891*S;Rejected(Accounting::Replay(Spec(),opening,e,1800));
    for(int mode=0;mode<10;++mode) {
        e=Events();
        switch(mode) {case 0:e[0].quantityRaw=S+1;break;case 1:e[0].priceRaw=0;break;case 2:e[0].quantityRaw=-S;break;
        case 3:e[0].priceRaw=M+1;break;case 4:e[1].commissionRaw=-1;break;case 5:e[1].commissionCurrency="EUR";break;
        case 6:e[1].priceRaw=1;break;case 7:e[0].commissionCurrency="USD";break;
        case 8:e[1].side=SimulatorFxSide::Buy;break;case 9:e[0].kind=static_cast<SimulatorFxEventKind>(3);break;}
        Rejected(Accounting::Replay(Spec(),opening,e,1800));
    }
    auto spec=Spec();spec.priceTickRaw=10;e=Events();++e[0].priceRaw;Rejected(Accounting::Replay(spec,opening,e,1800));
    e={Fill(1,"micro-notional",SimulatorFxSide::Buy,S,1),Fee(2,"micro-notional",0)};
    const auto exact=Accounting::Replay(Spec(),opening,e,1800);REQUIRE(exact.accepted && exact.projection.netQuoteTradeRaw==-1);
}
void TestMetadataAndEffectiveTimeBounds() {
    for(int mode=0;mode<14;++mode) {
        auto s=Spec();switch(mode) {
        case 0:s.venue="IB";break;case 1:s.instrument="GBP.USD";break;case 2:s.baseCurrency="USD";break;
        case 3:s.quoteCurrency="EUR";break;case 4:s.quantityStepRaw=1;break;case 5:s.priceTickRaw=0;break;
        case 6:s.minimumQuantityRaw=0;break;case 7:s.maximumQuantityRaw=M+1;break;case 8:s.revision="bad value";break;
        case 9:s.effectiveFromMs=0;break;case 10:s.effectiveUntilMs=s.effectiveFromMs;break;
        case 11:s.quantityStepRaw=M+1;break;case 12:s.minimumQuantityRaw=S+1;break;case 13:s.maximumQuantityRaw=M-1;break;}
        Rejected(Accounting::Replay(s,Opening(),Events(),1800));
    }
    auto e=Events();e[0].eventTimeMs=e[0].recordedAtMs=2000;Rejected(Accounting::Replay(Spec(),Opening(),e,2500));
    // A commission may arrive after retirement, for an earlier effective fill.
    e={Events()[0],Events()[1]};e[1].eventTimeMs=e[1].recordedAtMs=2500;
    REQUIRE(Accounting::Replay(Spec(),Opening(),e,2500).accepted);
    auto o=Opening();o.asOfMs=900;REQUIRE(Accounting::Replay(Spec(),o,{},900).accepted);
    o.asOfMs=2000;Rejected(Accounting::Replay(Spec(),o,{},2000));
    const auto max=std::numeric_limits<std::uint64_t>::max();auto s=Spec();s.effectiveFromMs=max-10;s.effectiveUntilMs=max;
    o=Opening();o.asOfMs=max-9;REQUIRE(Accounting::Replay(s,o,{},max).accepted);
}
void TestIntegerRangeBeforeMultiplication() {
    auto o=Opening();o.quoteBalanceRaw=M;
    auto e=std::vector<SimulatorFxEvent>{Fill(1,"max",SimulatorFxSide::Buy,S,M),Fee(2,"max",0)};
    const auto r=Accounting::Replay(Spec(),o,e,1800);REQUIRE(r.accepted && r.projection.quoteBalanceRaw==0);
    e[0].quantityRaw=2*S;const auto notional=Accounting::Replay(Spec(),o,e,1800);
    Rejected(notional);REQUIRE(std::string(notional.reasonCode)=="FX_LEDGER_NOTIONAL_RANGE");
    o=Opening();o.baseBalanceRaw=M;e={Fill(1,"overflow-base",SimulatorFxSide::Buy,S,1)};
    Rejected(Accounting::Replay(Spec(),o,e,1800));
    o=Opening();o.baseBalanceRaw=S;o.quoteBalanceRaw=M;e={Fill(1,"overflow-quote",SimulatorFxSide::Sell,S,1)};
    Rejected(Accounting::Replay(Spec(),o,e,1800));
    for(auto n:{std::numeric_limits<SimulatorFxRaw>::min(),std::numeric_limits<SimulatorFxRaw>::max()}) {
        e=Events();e[0].quantityRaw=n;Rejected(Accounting::Replay(Spec(),Opening(),e,1800));
        e=Events();e[1].commissionRaw=n;Rejected(Accounting::Replay(Spec(),Opening(),e,1800));
    }
}
void TestPreflightCapacityAndNoBodySizedRejectionCopies() {
    auto e=Events();auto s=Spec();auto o=Opening();
    for(int mode=0;mode<4;++mode) {
        e=Events();if(mode==0)e[0].eventId.assign(2u<<20,'x');if(mode==1)e[0].executionId.assign(2u<<20,'x');
        if(mode==2)e.resize(4097,e[0]);
        if(mode==3)e[0].instrumentRevision.assign(2u<<20,'x');
        fault::largest=fault::total=0;fault::measure=true;
        const auto r=Accounting::Replay(s,o,e,1800);fault::measure=false;
        Rejected(r);REQUIRE(fault::largest==0 && fault::total==0);
    }
    e.assign(4096,Events()[0]);const auto at=Accounting::Replay(s,o,e,1800);
    REQUIRE(at.accepted && at.projection.fills==1 && at.projection.duplicates==4095);
    Rejected(Accounting::Replay(s,o,e,1800,4095));Rejected(Accounting::Replay(s,o,{},1800,0));
    Rejected(Accounting::Replay(s,o,{},1800,4097));
}
void TestDigestBindsPolicyOpeningCutAndEventSemantics() {
    const auto original=Accounting::Replay(Spec(),Opening(),Events(),1800);REQUIRE(original.accepted);
    for(int mode=0;mode<8;++mode) {
        auto s=Spec();auto o=Opening();auto e=Events();std::uint64_t cut=1800;
        switch(mode){case 0:s.priceTickRaw=10;break;case 1:++o.quoteBalanceRaw;break;case 2:++cut;break;
        case 3:e[0].eventId="different-event";break;case 4:++e[0].priceRaw;break;case 5:++e[1].commissionRaw;break;
        case 6:++e[0].recordedAtMs;break;case 7:s.effectiveUntilMs=2100;break;}
        const auto r=Accounting::Replay(s,o,e,cut);REQUIRE(r.accepted && r.projection.digest!=original.projection.digest);
    }
    for(int stage=1;stage<=3;++stage){fault::crypto=stage;const auto r=Accounting::Replay(Spec(),Opening(),Events(),1800);
        fault::crypto=0;Rejected(r);REQUIRE(std::string(r.reasonCode)=="FX_LEDGER_DIGEST_FAILED");}
}
void TestAllocationFailuresAndThreadIndependentReplay() {
    auto s=Spec();auto o=Opening();auto e=Events();const auto before=Accounting::Replay(s,o,e,1800).projection.digest;
    unsigned failures=0;bool completed=false;
    for(long ordinal=0;ordinal<256;++ordinal) {
        SimulatorFxReplayResult result;bool threw=false;fault::calls=0;fault::at=ordinal;
        try{result=Accounting::Replay(s,o,e,1800);}catch(const std::bad_alloc&){threw=true;}fault::at=-1;
        REQUIRE(Accounting::Replay(s,o,e,1800).projection.digest==before);
        if(threw){++failures;Rejected(result);}else{REQUIRE(result.accepted && result.projection.digest==before);completed=true;break;}
    }
    REQUIRE(completed && failures>0);std::vector<SimulatorFxReplayResult> results(16);std::vector<std::thread> workers;
    for(std::size_t i=0;i<results.size();++i)workers.emplace_back([&,i]{results[i]=Accounting::Replay(s,o,e,1800);});
    for(auto& worker:workers)worker.join();
    for(const auto& result:results)REQUIRE(result.accepted && result.projection.digest==before);
    std::cout<<"fx_allocation_failure_positions="<<failures<<'\n';
}
}
int main() {
    TestCashConservationAndGoldenReplay();TestDuplicateRecordsNeverDoublePost();TestSequenceCaptureAndFeeAssociation();
    TestMissingFeeAndReconciliationCutFailClosed();TestNoMarginRoundingOrCrossCurrencyAssumptions();TestMetadataAndEffectiveTimeBounds();
    TestIntegerRangeBeforeMultiplication();TestPreflightCapacityAndNoBodySizedRejectionCopies();
    TestDigestBindsPolicyOpeningCutAndEventSemantics();TestAllocationFailuresAndThreadIndependentReplay();
    std::cout<<"fx_accounting_assertions="<<assertions<<'\n';
}
