#pragma once

#include <cstddef>
#include <cstdint>
#include <map>
#include <string>
#include <vector>

using PortfolioMicrounits = std::int64_t;

struct StrategyTargetIntent
{
    std::string strategyId;
    std::string instrument;
    PortfolioMicrounits targetPosition = 0;
    std::uint64_t snapshotGeneration = 0;
};

struct StrategyCapitalBudget
{
    std::string strategyId;
    PortfolioMicrounits maximumGrossTarget = 0;
};

struct PortfolioCapitalPolicy
{
    PortfolioMicrounits maximumGrossTarget = 0;
    std::size_t maximumStrategies = 0;
    std::size_t maximumInstruments = 0;
    std::map<std::string, StrategyCapitalBudget> strategyBudgets;
};

struct AuthoritativePortfolioInput
{
    bool complete = false;
    std::uint64_t generation = 0;
    std::map<std::string, PortfolioMicrounits> currentPositions;
};

struct PortfolioTargetDelta
{
    std::string instrument;
    PortfolioMicrounits currentPosition = 0;
    PortfolioMicrounits targetPosition = 0;
    PortfolioMicrounits delta = 0;
};

struct PortfolioCompileResult
{
    bool accepted = false;
    std::string reasonCode;
    std::map<std::string, PortfolioMicrounits> netTargets;
    std::map<std::string, PortfolioMicrounits> strategyGrossTargets;
    PortfolioMicrounits portfolioGrossTarget = 0;
    std::vector<PortfolioTargetDelta> deltas;
};

// Deterministic cross-strategy netting and capital authority. All arithmetic
// is exact signed fixed-point microunits. Conversion from venue quantity or
// money is an explicit boundary concern and never occurs silently here.
class PortfolioCompiler
{
public:
    static const char* Version();

    static PortfolioCompileResult Compile(
        const std::vector<StrategyTargetIntent>& intents,
        const AuthoritativePortfolioInput& authoritative,
        const PortfolioCapitalPolicy& policy);
};
