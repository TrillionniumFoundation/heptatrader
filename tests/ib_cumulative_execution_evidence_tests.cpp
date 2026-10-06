#include "execution/ib_cumulative_execution_evidence.h"
#include "adapter_ib/ib_crypto_settlement_observation.h"
#include <cassert>
#include <limits>
#include <iostream>
int main() {
    const std::string a="fixture.0001.01.01",b="fixture.0002.01.01";
    IbCumulativeExecutionEvidence e;
    assert(e.Observe(a,.01,86436.75,100));
    assert(e.Observe(b,.03313821,86437.099117,200));
    assert(e.Latest().quantity==.03313821 && e.Latest().average==86437.099117);
    assert(e.Points().size()==2 && e.LatestId()==b);
    assert(e.MatchesTerminal(.03313821,86437.099117));
    assert(!e.MatchesTerminal(.04313821,86437.099117)); // cumulative is not shares
    assert(!e.MatchesTerminal(0,0));
    assert(e.Observe(a,.01,86436.75,999));
    assert(e.Latest().observedAtMs==200 && e.LatestId()==b);
    IbCumulativeExecutionEvidence reverse;
    assert(reverse.Observe(b,.03313821,86437.099117,200));
    assert(reverse.Observe(a,.01,86436.75,100));
    assert(reverse.LatestId()==b && reverse.Points().size()==2);
    auto conflict=e;
    assert(!conflict.Observe(a,.02,86436.75,900));
    assert(conflict.Ambiguous() && !conflict.HasEvidence());
    assert(!conflict.Observe("later",.04,86438,901)); // sticky
    auto corrected=e;
    assert(!corrected.Observe("fixture.0002.01.02",.04,86438,300));
    assert(corrected.Reason()=="IB_EXECUTION_CORRECTION_RECONCILIATION_REQUIRED");
    auto sameTotal=e;
    assert(!sameTotal.Observe("other",.03313821,86437.099117,300));
    auto lowerNotional=e;
    assert(!lowerNotional.Observe("next",.05,100,300));
    auto higherPrefix=e;
    assert(!higherPrefix.Observe("prefix",.005,10000000,50));
    for (double bad : {0.0,-1.0,std::numeric_limits<double>::infinity(),
                        std::numeric_limits<double>::quiet_NaN()}) {
        IbCumulativeExecutionEvidence q,p;
        assert(!q.Observe(a,bad,100,1));assert(!p.Observe(a,1,bad,1));
    }
    IbCumulativeExecutionEvidence revision,empty,large,capacity;
    assert(!revision.Observe(a,1,100,1,true));
    assert(!empty.Observe("",1,100,1));
    assert(!large.Observe(std::string(129,'x'),1,100,1));
    for (std::size_t i=1;i<=IbCumulativeExecutionEvidence::Capacity();++i)
        assert(capacity.Observe("execution-"+std::to_string(i),double(i),100,100+i));
    assert(!capacity.Observe("overflow",129,100,1000));
    IBAuthoritativeRecoveryAuditSnapshot s;
    s.risk.connectionEpoch=9;s.risk.positionsComplete=true;s.risk.positionsGeneration=2;
    s.active.complete=true;s.active.generation=1;s.active.connectionEpoch=9;
    s.terminal.complete=true;s.terminal.generation=1;s.terminal.connectionEpoch=9;
    assert(CanObservePhysicalCryptoPositions(true,true,9,s));
    assert(!s.risk.complete && !s.risk.accountComplete && !s.risk.fxCashComplete);
    assert(!CanObservePhysicalCryptoPositions(false,true,9,s));
    assert(!CanObservePhysicalCryptoPositions(true,false,9,s));
    assert(!CanObservePhysicalCryptoPositions(true,true,0,s));
    const auto valid=s;
    for (int i=0;i<15;++i) {
        s=valid;
        switch(i) {
        case 0:s.risk.positionsComplete=false;break;
        case 1:s.risk.positionsGeneration=0;break;
        case 2:s.risk.connectionEpoch=8;break;
        case 3:s.active.complete=false;break;
        case 4:s.active.generation=0;break;
        case 5:s.active.connectionEpoch=8;break;
        case 6:s.active.activeOrderIds.insert(77);break;
        case 7:s.active.activeOrderIdsByCorrelation["foreign"]=77;break;
        case 8:s.terminal.complete=false;break;
        case 9:s.terminal.generation=0;break;
        case 10:s.terminal.connectionEpoch=8;break;
        case 11:s.exposureGeneration=1;break;
        case 12:s.terminalExposureGeneration=1;break;
        case 13:s.callbackConflictCount=1;break;
        case 14:s.newConnectionEpochRequired=true;break;
        }
        assert(!CanObservePhysicalCryptoPositions(true,true,9,s));
    }
    std::cout<<"PASS cumulative partial-fill IDs, replay/order/correction/capacity conflicts and physical-only settlement barriers\n";
}
