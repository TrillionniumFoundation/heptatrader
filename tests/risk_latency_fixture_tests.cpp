#include "../HeptaTrade/risk/deterministic_risk_policy.h"

#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <vector>

#ifndef HEPTA_RISK_P99_BASELINE_US
#define HEPTA_RISK_P99_BASELINE_US 50000
#endif

int main()
{
    DeterministicRiskLimits limits;
    DeterministicRiskContext context;
    context.action = "BUY";
    context.orderType = "LMT";
    context.quantity = 1000.0;
    context.valuationPrice = 1.1002;
    context.submittedPrice = 1.1002;
    context.referencePrice = 1.1002;
    context.grossAbsolutePosition = 10000.0;
    context.projectedGrossAbsolutePosition = 11000.0;
    context.netPosition = 10000.0;
    context.projectedNetPosition = 11000.0;
    context.strategyGrossPosition = 10000.0;
    context.projectedStrategyGrossPosition = 11000.0;
    context.quoteFresh = true;
    context.portfolioSnapshotComplete = true;
    std::vector<long long> samples;
    samples.reserve(2000);
    for (int i = 0; i < 2000; ++i)
    {
        const std::chrono::steady_clock::time_point start =
  std::chrono::steady_clock::now();
        const DeterministicRiskDecision decision =
  DeterministicRiskPolicy::Evaluate(limits, context);
        if (!decision.allow) return 2;
        samples.push_back(std::chrono::duration_cast<std::chrono::microseconds>(
  std::chrono::steady_clock::now() - start).count());
    }
    std::sort(samples.begin(), samples.end());
    const long long p99 = samples[(samples.size() * 99) / 100];
    const long long allowed = HEPTA_RISK_P99_BASELINE_US * 120 / 100;
    std::cout << "{\"fixture\":\"risk-evaluate-v1\",\"p99_us\":"
    << p99 << ",\"baseline_us\":" << HEPTA_RISK_P99_BASELINE_US
    << ",\"allowed_us\":" << allowed << "}\n";
    return p99 <= allowed ? 0 : 3;
}
