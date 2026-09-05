#include "portfolio/portfolio_compiler.h"

#include <algorithm>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <new>
#include <random>
#include <sstream>
#include <thread>

namespace fault {
thread_local long failAfter = -1, calls = 0;
thread_local bool measure = false;
thread_local std::size_t largest = 0;
}
__attribute__((noinline)) void* operator new(std::size_t n) {
    if (fault::measure) fault::largest = std::max(fault::largest, n);
    if (fault::failAfter >= 0 && fault::calls++ == fault::failAfter) throw std::bad_alloc();
    void* memory = std::malloc(n ? n : 1);
    if (!memory) throw std::bad_alloc();
    return memory;
}
__attribute__((noinline)) void* operator new[](std::size_t n) { return ::operator new(n); }
__attribute__((noinline)) void operator delete(void* p) noexcept { std::free(p); }
__attribute__((noinline)) void operator delete[](void* p) noexcept { std::free(p); }
#if defined(__cpp_sized_deallocation)
__attribute__((noinline)) void operator delete(void* p, std::size_t) noexcept { std::free(p); }
__attribute__((noinline)) void operator delete[](void* p, std::size_t) noexcept { std::free(p); }
#endif
namespace {
using Compiler = PortfolioCompiler;
using Raw = PortfolioMicrounits;
std::uint64_t assertions = 0;
void Require(bool ok, const char* expression, int line) {
    ++assertions;
    if (!ok) { std::cerr << "failure at " << line << ": " << expression << '\n'; std::abort(); }
}
#define REQUIRE(x) Require(static_cast<bool>(x), #x, __LINE__)
PortfolioCapitalPolicy Policy() {
    PortfolioCapitalPolicy p; p.maximumGrossTarget = 1000000;
    p.maximumStrategies = 8; p.maximumInstruments = 16;
    p.strategyBudgets["alpha"] = {"alpha", 800000};
    p.strategyBudgets["hedge"] = {"hedge", 800000};
    return p;
}
AuthoritativePortfolioInput Snapshot() {
    AuthoritativePortfolioInput s; s.complete = true; s.generation = 7;
    s.currentPositions["EUR.USD"] = 10; s.currentPositions["OLD"] = -5; return s;
}
StrategyTargetIntent Intent(std::string strategy="alpha", std::string instrument="EUR.USD", Raw amount=100) {
    return {std::move(strategy), std::move(instrument), amount, 7};
}
void Empty(const PortfolioCompileResult& r) {
    REQUIRE(!r.accepted && !r.reasonCode.empty());
    REQUIRE(r.netTargets.empty() && r.strategyGrossTargets.empty() && r.deltas.empty());
    REQUIRE(r.portfolioGrossTarget == 0);
}
std::string Image(const PortfolioCompileResult& r) {
    std::ostringstream s;
    s << r.accepted << '|' << r.reasonCode << '|' << r.portfolioGrossTarget;
    for (const auto& p : r.netTargets) s << "|t:" << p.first << ':' << p.second;
    for (const auto& p : r.strategyGrossTargets) s << "|s:" << p.first << ':' << p.second;
    for (const auto& p : r.deltas) s << "|d:" << p.instrument << ':' << p.currentPosition << ':' << p.targetPosition << ':' << p.delta;
    return s.str();
}

void TestCompletePolicyIncludingUnusedEntries() {
    for (int bad=0; bad<7; ++bad) {
        auto p=Policy(); StrategyCapitalBudget b{"unused", 100}; std::string key="unused";
        switch (bad) {
        case 0: b.strategyId="other"; break;
        case 1: b.maximumGrossTarget=0; break;
        case 2: b.maximumGrossTarget=-1; break;
        case 3: b.strategyId=key=std::string(65,'x'); break;
        case 4: b.strategyId=key="bad/name"; break;
        case 5: b.strategyId=key=std::string("bad\0key",7); break;
        case 6: b.strategyId=key=std::string(1,static_cast<char>(0xff)); break;
        }
        p.strategyBudgets[key]=b;
        for (const auto& intents : {std::vector<StrategyTargetIntent>{},std::vector<StrategyTargetIntent>{Intent()}}) {
            const auto r=Compiler::Compile(intents,Snapshot(),p); Empty(r);
            REQUIRE(r.reasonCode=="PORTFOLIO_STRATEGY_BUDGET_INVALID");
        }
    }
    auto p=Policy(); p.maximumStrategies=1; // Registered universe is not the participating count.
    REQUIRE(Compiler::Compile({Intent()},Snapshot(),p).accepted);
    REQUIRE(Compiler::Compile({},Snapshot(),p).accepted);
    const auto missing=Compiler::Compile({Intent("absent")},Snapshot(),p);
    Empty(missing); REQUIRE(missing.reasonCode=="PORTFOLIO_STRATEGY_BUDGET_MISSING");
}

void TestCapacityAndFieldsPrecedeNormalization() {
    const auto base=Policy(); const auto snap=Snapshot();
    for (int scenario=0; scenario<9; ++scenario) {
        auto p=base; auto s=snap; std::vector<StrategyTargetIntent> intents{Intent()};
        switch (scenario) {
        case 0: intents.back().instrument.assign(2u<<20,'x'); break;
        case 1: intents.back().strategyId.assign(2u<<20,'x'); break;
        case 2: intents.resize(Compiler::kMaximumIntents+1,intents[0]); break;
        case 3: s.currentPositions.clear();
                for(std::size_t i=0;i<=Compiler::kMaximumSnapshotPositions;++i) s.currentPositions["P"+std::to_string(i)]=0;
                intents.clear(); break;
        case 4: p.strategyBudgets.clear();
                for(std::size_t i=0;i<=Compiler::kMaximumStrategyBudgets;++i) {auto name="S"+std::to_string(i);p.strategyBudgets[name]={name,100};}
                intents.clear(); break;
        case 5: s.currentPositions[std::string(2u<<20,'x')]=0; break;
        case 6: p.strategyBudgets["unused"]={std::string(2u<<20,'x'),100}; break;
        case 7: intents.assign(16384,Intent());intents.back().snapshotGeneration=8;break;
        case 8: intents.assign(16384,Intent());intents.back().targetPosition=std::numeric_limits<Raw>::min();break;
        }
        fault::largest=0; fault::measure=true;
        const auto result=Compiler::Compile(intents,s,p);
        fault::measure=false; Empty(result);
        REQUIRE(fault::largest<=128); // A bounded reason string is permitted, not a copied body/index.
    }
    for (int bad=0;bad<4;++bad) {
        auto p=base;
        if(bad==0)p.maximumStrategies=0;
        if(bad==1)p.maximumStrategies=std::numeric_limits<std::size_t>::max();
        if(bad==2)p.maximumInstruments=0;
        if(bad==3)p.maximumInstruments=std::numeric_limits<std::size_t>::max();
        const auto r=Compiler::Compile({},snap,p); Empty(r); REQUIRE(r.reasonCode=="PORTFOLIO_POLICY_INVALID");
    }
}

void TestInclusiveHardBoundsAndCompleteDeltaUnion() {
    auto p=Policy(); p.maximumStrategies=Compiler::kMaximumStrategyBudgets;
    p.maximumInstruments=Compiler::kMaximumTargetInstruments;
    p.strategyBudgets.clear();
    for(std::size_t i=0;i<Compiler::kMaximumStrategyBudgets;++i) {
        const auto id="S"+std::to_string(i);p.strategyBudgets[id]={id,1000000};
    }
    auto s=Snapshot();s.currentPositions.clear();
    for(std::size_t i=0;i<Compiler::kMaximumSnapshotPositions;++i)s.currentPositions["OLD"+std::to_string(i)]=1;
    std::vector<StrategyTargetIntent> intents;
    for(std::size_t strategy=0;strategy<4;++strategy)
        for(std::size_t instrument=0;instrument<Compiler::kMaximumTargetInstruments;++instrument)
            intents.push_back(Intent("S"+std::to_string(strategy),"NEW"+std::to_string(instrument),1));
    REQUIRE(intents.size()==Compiler::kMaximumIntents);
    const auto r=Compiler::Compile(intents,s,p);
    REQUIRE(r.accepted && r.netTargets.size()==4096 && r.deltas.size()==Compiler::kMaximumDeltaInstruments);
    REQUIRE(r.strategyGrossTargets.size()==4 && r.portfolioGrossTarget==16384);
    for(const auto& d:r.deltas) {
        if(d.instrument.compare(0,3,"OLD")==0) REQUIRE(d.currentPosition==1 && d.targetPosition==0 && d.delta==-1);
        else REQUIRE(d.currentPosition==0 && d.targetPosition==4 && d.delta==4);
    }
    const auto empty=Compiler::Compile({},s,p);
    REQUIRE(empty.accepted && empty.reasonCode=="PORTFOLIO_NO_INTENTS" && empty.deltas.empty());
    // A low target-universe budget must not silently delete held-position reductions.
    p.maximumInstruments=1;
    const auto narrow=Compiler::Compile({Intent("S0","NEW",0)},s,p);
    REQUIRE(narrow.accepted && narrow.deltas.size()==4096);
    intents.push_back(intents[0]);
    const auto over=Compiler::Compile(intents,s,p);Empty(over);
    REQUIRE(over.reasonCode=="PORTFOLIO_INTENT_CAPACITY_EXCEEDED");
}

void TestIdEndpointsAndAdmissionOrder() {
    auto p=Policy();auto s=Snapshot();const std::string strategy(64,'s'),symbol(128,'x');
    p.strategyBudgets[strategy]={strategy,100};
    auto valid=Intent(strategy,symbol,10);REQUIRE(Compiler::Compile({valid},s,p).accepted);
    auto invalid=valid;invalid.instrument.push_back('x');
    Empty(Compiler::Compile({invalid},s,p));invalid=valid;invalid.strategyId.push_back('s');
    Empty(Compiler::Compile({invalid},s,p));
    for(int mode=0;mode<3;++mode){auto e=Intent();
        if(mode==0)e.instrument="bad/symbol";
        if(mode==1)e.instrument=std::string("a\0b",3);
        if(mode==2)e.strategyId.clear();
        Empty(Compiler::Compile({Intent(),e},s,p));
    }
    s.complete=false; p.maximumGrossTarget=0;
    REQUIRE(Compiler::Compile({invalid},s,p).reasonCode=="PORTFOLIO_SNAPSHOT_INCOMPLETE");
}

void TestPrefixFailuresAndIntegerSemanticsRemainClosed() {
    const auto max=std::numeric_limits<Raw>::max();
    auto p=Policy();p.maximumGrossTarget=max;
    p.strategyBudgets["alpha"].maximumGrossTarget=max;
    p.strategyBudgets["hedge"].maximumGrossTarget=max;
    AuthoritativePortfolioInput s;s.complete=true;s.generation=7;
    REQUIRE(Compiler::Compile({Intent("alpha","X",max)},s,p).accepted);
    Empty(Compiler::Compile({Intent("alpha","X",max),Intent("hedge","X",1)},s,p));
    Empty(Compiler::Compile({Intent("alpha","X",max),Intent("alpha","Y",1)},s,p));
    s.currentPositions["X"]=-1;
    const auto delta=Compiler::Compile({Intent("alpha","X",max)},s,p);Empty(delta);
    REQUIRE(delta.reasonCode=="PORTFOLIO_ARITHMETIC_OVERFLOW");
    s.currentPositions["X"]=std::numeric_limits<Raw>::min();
    Empty(Compiler::Compile({},s,p));
    std::vector<StrategyTargetIntent> intents{Intent("alpha","A",1),Intent("alpha","Z",1),Intent("alpha","Z",2)};
    const auto dup=Compiler::Compile(intents,Snapshot(),Policy());Empty(dup);
    REQUIRE(dup.reasonCode=="PORTFOLIO_DUPLICATE_STRATEGY_INSTRUMENT");
}

void TestIndependentSmallValueModelAndPermutation() {
    std::mt19937 rng(20260905);
    for(unsigned sample=0;sample<500;++sample) {
        auto p=Policy();auto s=Snapshot();s.currentPositions.clear();p.strategyBudgets.clear();
        p.maximumStrategies=8;p.maximumInstruments=16;p.maximumGrossTarget=1000000;
        std::vector<StrategyTargetIntent> intents;
        std::map<std::string,Raw> net,gross;
        const auto strategies=1+rng()%8,instruments=1+rng()%16;
        for(unsigned a=0;a<strategies;++a) {
            const auto id="S"+std::to_string(a);p.strategyBudgets[id]={id,1000000};
            for(unsigned b=0;b<instruments;++b) {
                const auto instrument="X"+std::to_string(b);const auto amount=static_cast<Raw>(rng()%201)-100;
                intents.push_back(Intent(id,instrument,amount));net[instrument]+=amount;gross[id]+=amount<0?-amount:amount;
            }
        }
        for(unsigned i=0;i<20;++i)s.currentPositions["X"+std::to_string(i)]=static_cast<Raw>(rng()%201)-100;
        const auto r=Compiler::Compile(intents,s,p);REQUIRE(r.accepted && r.netTargets==net && r.strategyGrossTargets==gross);
        Raw portfolioGross=0;for(const auto& pair:net)portfolioGross+=pair.second<0?-pair.second:pair.second;
        REQUIRE(r.portfolioGrossTarget==portfolioGross);
        std::map<std::string,Raw> changes;
        for(const auto& pair:s.currentPositions) changes[pair.first]=-pair.second;
        for(const auto& pair:net)changes[pair.first]+=pair.second;
        for(auto it=changes.begin();it!=changes.end();)if(it->second==0)it=changes.erase(it);else ++it;
        REQUIRE(r.deltas.size()==changes.size());
        for(const auto& d:r.deltas) REQUIRE(changes.at(d.instrument)==d.delta);
        std::shuffle(intents.begin(),intents.end(),rng);REQUIRE(Image(Compiler::Compile(intents,s,p))==Image(r));
    }
    std::cout<<"portfolio_oracle_valid_fixtures=500\n";
}

void TestAllocationFailureAtomicityAndPureParallelCalls() {
    auto p=Policy();auto s=Snapshot();std::vector<StrategyTargetIntent> intents{Intent(),Intent("hedge","EUR.USD",-30)};
    const auto before=Image(Compiler::Compile(intents,s,p));
    std::size_t failures=0;bool completed=false;
    for(long ordinal=0;ordinal<256;++ordinal) {
        PortfolioCompileResult r;bool threw=false;fault::calls=0;fault::failAfter=ordinal;
        try{r=Compiler::Compile(intents,s,p);}catch(const std::bad_alloc&){threw=true;}
        fault::failAfter=-1;
        REQUIRE(Image(Compiler::Compile(intents,s,p))==before);
        if(threw){++failures;REQUIRE(!r.accepted && r.netTargets.empty() && r.deltas.empty());}
        else {REQUIRE(r.accepted && Image(r)==before);completed=true;break;}
    }
    REQUIRE(completed && failures>0);
    std::vector<std::string> outputs(16);std::vector<std::thread> threads;
    for(std::size_t i=0;i<outputs.size();++i)threads.emplace_back([&,i]{outputs[i]=Image(Compiler::Compile(intents,s,p));});
    for(auto& t:threads)t.join();
    for(const auto& out:outputs)REQUIRE(out==before);
    std::cout<<"portfolio_allocation_failure_positions="<<failures<<'\n';
}
}
int main(){
    TestCompletePolicyIncludingUnusedEntries();TestCapacityAndFieldsPrecedeNormalization();
    TestInclusiveHardBoundsAndCompleteDeltaUnion();TestIdEndpointsAndAdmissionOrder();
    TestPrefixFailuresAndIntegerSemanticsRemainClosed();TestIndependentSmallValueModelAndPermutation();
    TestAllocationFailureAtomicityAndPureParallelCalls();
    std::cout<<"portfolio_admission_assertions="<<assertions<<'\n';
}
