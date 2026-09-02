#include "../HeptaTrade/portfolio/portfolio_compiler.h"
#include "latency_fixture_common.h"

#include <chrono>
#include <cstdio>
#include <string>
#include <vector>

namespace
{
constexpr int kWarmupIterations = 100;
constexpr int kSampleCount = 2000;
constexpr int kStrategyCount = 64;
constexpr int kInstrumentCount = 16;

std::string PaddedId(const char* prefix, int value)
{
    char buffer[32];
    std::snprintf(buffer, sizeof(buffer), "%s%03d", prefix, value);
    return std::string(buffer);
}

bool CompileOnce(const std::vector<StrategyTargetIntent>& intents,
                 const AuthoritativePortfolioInput& authoritative,
                 const PortfolioCapitalPolicy& policy)
{
    const PortfolioCompileResult result =
        PortfolioCompiler::Compile(intents, authoritative, policy);
    return result.accepted && result.reasonCode == "PORTFOLIO_COMPILED" &&
        result.strategyGrossTargets.size() == kStrategyCount &&
        result.netTargets.size() == kInstrumentCount;
}
}

int main()
{
    const std::uint64_t generation = 77;
    AuthoritativePortfolioInput authoritative;
    authoritative.complete = true;
    authoritative.generation = generation;
    PortfolioCapitalPolicy policy;
    policy.maximumGrossTarget = 1000000000;
    policy.maximumStrategies = kStrategyCount;
    policy.maximumInstruments = kInstrumentCount;

    std::vector<std::string> instruments;
    instruments.reserve(kInstrumentCount);
    for (int instrument = 0; instrument < kInstrumentCount; ++instrument)
    {
        const std::string id = PaddedId("INSTRUMENT-", instrument);
        instruments.push_back(id);
        authoritative.currentPositions[id] = instrument * 100;
    }

    std::vector<StrategyTargetIntent> intents;
    intents.reserve(kStrategyCount * kInstrumentCount);
    for (int strategy = 0; strategy < kStrategyCount; ++strategy)
    {
        const std::string strategyId = PaddedId("STRATEGY-", strategy);
        StrategyCapitalBudget budget;
        budget.strategyId = strategyId;
        budget.maximumGrossTarget = 1000000;
        policy.strategyBudgets[strategyId] = budget;
        for (int instrument = 0; instrument < kInstrumentCount; ++instrument)
        {
            StrategyTargetIntent intent;
            intent.strategyId = strategyId;
            intent.instrument = instruments[instrument];
            const PortfolioMicrounits magnitude =
                static_cast<PortfolioMicrounits>(1000 + (strategy % 5) * 100);
            intent.targetPosition =
                ((strategy + instrument) % 2 == 0) ? magnitude : -magnitude;
            intent.snapshotGeneration = generation;
            intents.push_back(intent);
        }
    }

    for (int i = 0; i < kWarmupIterations; ++i)
        if (!CompileOnce(intents, authoritative, policy)) return 2;

    std::vector<long long> samples;
    samples.reserve(kSampleCount);
    for (int i = 0; i < kSampleCount; ++i)
    {
        const auto start = std::chrono::steady_clock::now();
        const bool ok = CompileOnce(intents, authoritative, policy);
        const auto end = std::chrono::steady_clock::now();
        if (!ok) return 2;
        samples.push_back(
            std::chrono::duration_cast<std::chrono::microseconds>(end - start).count());
    }
    return HeptaLatencyFixture::ReportAndCheck(
        "portfolio-compiler-limit-v1",
        "64-strategy by 16-instrument fixed-point netting, budget and delta compilation",
        kWarmupIterations,
        samples);
}
