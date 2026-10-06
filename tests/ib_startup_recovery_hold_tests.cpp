#include "execution/ib_paper_startup_recovery.h"
#include <cassert>
#include <iostream>
int main() {
    IBAuthoritativeRecoveryAuditSnapshot r;
    r.risk.reasonCode="IB_FX_CASH_ATTESTED_BALANCE_MISMATCH";
    r.risk.accountComplete=false; r.risk.fxCashComplete=false;
    r.risk.positionsComplete=true; r.risk.accountGeneration=2;
    r.risk.positionsGeneration=2; r.risk.connectionEpoch=7;
    r.active.complete=true; r.active.generation=3; r.active.connectionEpoch=7;
    r.terminal.complete=true; r.terminal.generation=4; r.terminal.connectionEpoch=7;
    const auto good=r;
    assert(CanKeepFxCashMismatchReadOnly(true,true,true,7,r));
    assert(!r.risk.accountComplete && !r.risk.fxCashComplete && !r.risk.complete);
    assert(!CanKeepFxCashMismatchReadOnly(false,true,true,7,r));
    assert(!CanKeepFxCashMismatchReadOnly(true,false,true,7,r));
    assert(!CanKeepFxCashMismatchReadOnly(true,true,false,7,r));
    assert(!CanKeepFxCashMismatchReadOnly(true,true,true,0,r));
    assert(!CanKeepFxCashMismatchReadOnly(true,true,true,8,r));
    for (int k=0;k<13;++k) {
        r=good;
        switch(k) {
        case 0:r.risk.reasonCode="IB_FX_CASH_BALANCE_INVALID";break;
        case 1:r.risk.accountComplete=true;break;
        case 2:r.risk.fxCashComplete=true;break;
        case 3:r.risk.positionsComplete=false;break;
        case 4:r.risk.accountGeneration=0;break;
        case 5:r.risk.positionsGeneration=0;break;
        case 6:r.risk.connectionEpoch=6;break;
        case 7:r.active.complete=false;break;
        case 8:r.active.generation=0;break;
        case 9:r.active.connectionEpoch=6;break;
        case 10:r.terminal.complete=false;break;
        case 11:r.terminal.generation=0;break;
        case 12:r.terminal.connectionEpoch=6;break;
        }
        assert(!CanKeepFxCashMismatchReadOnly(true,true,true,7,r));
    }
    r=good; r.risk.reasonCode.clear(); r.risk.accountComplete=true; r.risk.fxCashComplete=true;
    assert(!CanKeepFxCashMismatchReadOnly(true,true,true,7,r));
    std::cout<<"PASS: mismatch retains diagnostics only; no complete cash proof, epoch bypass, or automatic promotion\n";
}
