#pragma once
#include <cmath>
#include <cstdint>
#include <string>

// Optional additive Broker callback receipt. Version zero means ABSENT, never
// zero commission or an inferred individual fill. Legacy qty/price keep their
// existing cumulative semantics. Invalid SDK sentinels are explicitly unusable.
struct IbEconomicEvidence {
    int version=0, valid=0;
    std::string kind, contractIdentity;
    std::int64_t contractId=0, permanentOrderId=0;
    double shares=0, executionPrice=0, cumulativeQuantity=0, cumulativeAverage=0;
    int priceRevisionPending=0;
    double commission=0, realizedPnl=0;
    std::string commissionCurrency;
    int realizedPnlKnown=0;
    bool Absent() const {
        return version==0 && valid==0 && kind.empty() && contractIdentity.empty() &&
            contractId==0 && permanentOrderId==0 && shares==0 && executionPrice==0 &&
            cumulativeQuantity==0 && cumulativeAverage==0 && !priceRevisionPending &&
            commission==0 && realizedPnl==0 && commissionCurrency.empty() && !realizedPnlKnown;
    }
    bool ValidShape() const {
        if(version!=1 || (kind!="EXECUTION" && kind!="COMMISSION") ||
           (valid!=0 && valid!=1) || (priceRevisionPending!=0 && priceRevisionPending!=1) ||
           (realizedPnlKnown!=0 && realizedPnlKnown!=1) || contractId<0 || permanentOrderId<0 ||
           !std::isfinite(shares) || !std::isfinite(executionPrice) || !std::isfinite(cumulativeQuantity) ||
           !std::isfinite(cumulativeAverage) || !std::isfinite(commission) || !std::isfinite(realizedPnl))return false;
        if(kind=="EXECUTION") {
            if(valid && (contractIdentity.empty() || contractId<=0 || shares<=0 || executionPrice<=0 ||
                cumulativeQuantity<shares || cumulativeAverage<=0 || shares>=1e100 || executionPrice>=1e100 ||
                cumulativeQuantity>=1e100 || cumulativeAverage>=1e100))return false;
            return commission==0 && commissionCurrency.empty() && realizedPnl==0 && !realizedPnlKnown;
        }
        if(valid && (std::fabs(commission)>1000000 || commissionCurrency.size()!=3 ||
            commissionCurrency.find_first_not_of("ABCDEFGHIJKLMNOPQRSTUVWXYZ")!=std::string::npos))return false;
        return contractIdentity.empty() && !contractId && !permanentOrderId && shares==0 && executionPrice==0 &&
            cumulativeQuantity==0 && cumulativeAverage==0 && !priceRevisionPending && (realizedPnlKnown || realizedPnl==0);
    }
};
