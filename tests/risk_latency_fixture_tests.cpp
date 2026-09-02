#include "../HeptaTrade/risk/deterministic_risk_policy.h"

#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <vector>

#ifndef HEPTA_RISK_P99_BASELINE_US
#define HEPTA_RISK_P99_BASELINE_US 50000
#endif

#ifndef HEPTA_RISK_MAX_REGRESSION_PERCENT
#define HEPTA_RISK_MAX_REGRESSION_PERCENT 10
#endif

namespace
{
long long Percentile(const std::vector<long long>& samples, std::size_t permille)
{
    const std::size_t index =
        ((samples.size() - 1u) * permille + 999u) / 1000u;
    return samples[index];
}
}

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

    // Warm instruction/data caches before collecting the bounded distribution.
    for (int i = 0; i < 500; ++i)
    {
        const DeterministicRiskDecision decision =
            DeterministicRiskPolicy::Evaluate(limits, context);
        if (!decision.allow) return 2;
    }

    std::vector<long long> samples;
    samples.reserve(10000);
    for (int i = 0; i < 10000; ++i)
    {
        const std::chrono::steady_clock::time_point start =
            std::chrono::steady_clock::now();
        const DeterministicRiskDecision decision =
            DeterministicRiskPolicy::Evaluate(limits, context);
        const std::chrono::steady_clock::time_point end =
            std::chrono::steady_clock::now();
        if (!decision.allow) return 2;
        samples.push_back(std::chrono::duration_cast<std::chrono::microseconds>(
            end - start).count());
    }

    std::sort(samples.begin(), samples.end());
    const long long p50 = Percentile(samples, 500u);
    const long long p95 = Percentile(samples, 950u);
    const long long p99 = Percentile(samples, 990u);
    const long long p999 = Percentile(samples, 999u);
    const long long maximum = samples.back();
    const long long allowed = HEPTA_RISK_P99_BASELINE_US *
        (100 + HEPTA_RISK_MAX_REGRESSION_PERCENT) / 100;

    std::cout
        << "{\"fixture\":\"risk-evaluate-v1\","
        << "\"numeric_policy\":\""
        << DeterministicRiskPolicy::NumericPolicy() << "\","
        << "\"samples\":" << samples.size() << ','
        << "\"p50_us\":" << p50 << ','
        << "\"p95_us\":" << p95 << ','
        << "\"p99_us\":" << p99 << ','
        << "\"p999_us\":" << p999 << ','
        << "\"max_us\":" << maximum << ','
        << "\"baseline_p99_us\":" << HEPTA_RISK_P99_BASELINE_US << ','
        << "\"maximum_regression_percent\":"
        << HEPTA_RISK_MAX_REGRESSION_PERCENT << ','
        << "\"allowed_p99_us\":" << allowed << "}\n";
    return p99 <= allowed ? 0 : 3;
}
