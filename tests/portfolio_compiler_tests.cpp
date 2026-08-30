#include "../HeptaTrade/portfolio/portfolio_compiler.h"

#include <algorithm>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <string>
#include <vector>

namespace
{
void Require(bool condition, const char* expression, int line)
{
    if (condition) return;
    std::cerr << "requirement failed at line " << line << ": "
    << expression << '\n';
    std::abort();
}
#define REQUIRE(expression) Require(static_cast<bool>(expression), #expression, __LINE__)

PortfolioCapitalPolicy Policy()
{
    PortfolioCapitalPolicy policy;
    policy.maximumGrossTarget = 1000000;
    policy.maximumStrategies = 8;
    policy.maximumInstruments = 16;
    StrategyCapitalBudget alpha;
    alpha.strategyId = "alpha";
    alpha.maximumGrossTarget = 800000;
    policy.strategyBudgets[alpha.strategyId] = alpha;
    StrategyCapitalBudget hedge;
    hedge.strategyId = "hedge";
    hedge.maximumGrossTarget = 800000;
    policy.strategyBudgets[hedge.strategyId] = hedge;
    return policy;
}

AuthoritativePortfolioInput Snapshot()
{
    AuthoritativePortfolioInput snapshot;
    snapshot.complete = true;
    snapshot.generation = 7;
    snapshot.currentPositions["EUR.USD"] = 50000;
    snapshot.currentPositions["USD.JPY"] = 10000;
    return snapshot;
}

StrategyTargetIntent Intent(const char* strategy,
                  const char* instrument,
                  PortfolioMicrounits target)
{
    StrategyTargetIntent intent;
    intent.strategyId = strategy;
    intent.instrument = instrument;
    intent.targetPosition = target;
    intent.snapshotGeneration = 7;
    return intent;
}

void TestCrossStrategyNettingAndDeterminism()
{
    std::vector<StrategyTargetIntent> intents;
    intents.push_back(Intent("alpha", "EUR.USD", 400000));
    intents.push_back(Intent("hedge", "EUR.USD", -150000));
    intents.push_back(Intent("hedge", "GBP.USD", 100000));
    const PortfolioCompileResult first =
        PortfolioCompiler::Compile(intents, Snapshot(), Policy());
    REQUIRE(first.accepted);
    REQUIRE(first.netTargets.at("EUR.USD") == 250000);
    REQUIRE(first.netTargets.at("GBP.USD") == 100000);
    REQUIRE(first.portfolioGrossTarget == 350000);
    REQUIRE(first.deltas.size() == 3);

    std::reverse(intents.begin(), intents.end());
    const PortfolioCompileResult second =
        PortfolioCompiler::Compile(intents, Snapshot(), Policy());
    REQUIRE(second.accepted);
    REQUIRE(first.netTargets == second.netTargets);
    REQUIRE(first.strategyGrossTargets == second.strategyGrossTargets);
    REQUIRE(first.portfolioGrossTarget == second.portfolioGrossTarget);
}

void TestFailClosedInputsAndBudgets()
{
    AuthoritativePortfolioInput snapshot = Snapshot();
    snapshot.complete = false;
    std::vector<StrategyTargetIntent> intents(1,
        Intent("alpha", "EUR.USD", 1000));
    REQUIRE(PortfolioCompiler::Compile(intents, snapshot, Policy()).reasonCode ==
  "PORTFOLIO_SNAPSHOT_INCOMPLETE");

    snapshot = Snapshot();
    intents[0].snapshotGeneration = 8;
    REQUIRE(PortfolioCompiler::Compile(intents, snapshot, Policy()).reasonCode ==
  "PORTFOLIO_INTENT_GENERATION_MISMATCH");

    intents[0] = Intent("alpha", "EUR.USD", 900000);
    REQUIRE(PortfolioCompiler::Compile(intents, snapshot, Policy()).reasonCode ==
  "PORTFOLIO_STRATEGY_BUDGET_EXCEEDED");

    PortfolioCapitalPolicy policy = Policy();
    policy.maximumGrossTarget = 100000;
    intents[0] = Intent("alpha", "EUR.USD", 200000);
    REQUIRE(PortfolioCompiler::Compile(intents, snapshot, policy).reasonCode ==
  "PORTFOLIO_CAPITAL_BUDGET_EXCEEDED");
}

void TestDuplicatesOverflowAndMissingBudget()
{
    std::vector<StrategyTargetIntent> intents;
    intents.push_back(Intent("alpha", "EUR.USD", 1));
    intents.push_back(Intent("alpha", "EUR.USD", 2));
    REQUIRE(PortfolioCompiler::Compile(intents, Snapshot(), Policy()).reasonCode ==
  "PORTFOLIO_DUPLICATE_STRATEGY_INSTRUMENT");

    intents.clear();
    intents.push_back(Intent("missing", "EUR.USD", 1));
    REQUIRE(PortfolioCompiler::Compile(intents, Snapshot(), Policy()).reasonCode ==
  "PORTFOLIO_STRATEGY_BUDGET_MISSING");

    PortfolioCapitalPolicy policy = Policy();
    policy.strategyBudgets["alpha"].maximumGrossTarget =
        std::numeric_limits<PortfolioMicrounits>::max();
    policy.strategyBudgets["hedge"].maximumGrossTarget =
        std::numeric_limits<PortfolioMicrounits>::max();
    policy.maximumGrossTarget = std::numeric_limits<PortfolioMicrounits>::max();
    intents.clear();
    intents.push_back(Intent("alpha", "EUR.USD",
        std::numeric_limits<PortfolioMicrounits>::max()));
    intents.push_back(Intent("hedge", "EUR.USD", 1));
    REQUIRE(PortfolioCompiler::Compile(intents, Snapshot(), policy).reasonCode ==
  "PORTFOLIO_ARITHMETIC_OVERFLOW");
}

void TestAuthoritativePositionValidationPrecedesNoIntentFastPath()
{
    AuthoritativePortfolioInput invalidInstrument = Snapshot();
    invalidInstrument.currentPositions["../EUR.USD"] = 1;
    REQUIRE(PortfolioCompiler::Compile(
        {}, invalidInstrument, Policy()).reasonCode ==
        "PORTFOLIO_SNAPSHOT_INSTRUMENT_INVALID");

    AuthoritativePortfolioInput unrepresentable = Snapshot();
    unrepresentable.currentPositions["X"] =
        std::numeric_limits<PortfolioMicrounits>::min();
    REQUIRE(PortfolioCompiler::Compile(
        {}, unrepresentable, Policy()).reasonCode ==
        "PORTFOLIO_ARITHMETIC_OVERFLOW");
}
}

int main()
{
    TestCrossStrategyNettingAndDeterminism();
    TestFailClosedInputsAndBudgets();
    TestDuplicatesOverflowAndMissingBudget();
    TestAuthoritativePositionValidationPrecedesNoIntentFastPath();
    return 0;
}
