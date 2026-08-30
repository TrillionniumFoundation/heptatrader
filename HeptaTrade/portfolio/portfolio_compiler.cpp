#include "portfolio_compiler.h"

#include <algorithm>
#include <limits>
#include <set>

namespace
{
bool CanonicalId(const std::string& value, std::size_t maximum)
{
    if (value.empty() || value.size() > maximum) return false;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const unsigned char c = static_cast<unsigned char>(value[i]);
        // IDs are protocol/canonical data, not locale-aware human text.
        // std::isalnum would make the accepted alphabet depend on the
        // process-global locale (and can admit non-ASCII bytes in one host
        // but not another), changing sorting, digest and duplicate checks.
        const bool asciiAlphaNumeric =
            (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
            (c >= '0' && c <= '9');
        if (!(asciiAlphaNumeric || c == '-' || c == '_' || c == '.' || c == ':'))
  return false;
    }
    return true;
}

bool CheckedAdd(PortfolioMicrounits left,
      PortfolioMicrounits right,
      PortfolioMicrounits& output)
{
    if ((right > 0 && left > std::numeric_limits<PortfolioMicrounits>::max() - right) ||
        (right < 0 && left < std::numeric_limits<PortfolioMicrounits>::min() - right))
        return false;
    output = left + right;
    return true;
}

bool CheckedSubtract(PortfolioMicrounits left,
           PortfolioMicrounits right,
           PortfolioMicrounits& output)
{
    if (right == std::numeric_limits<PortfolioMicrounits>::min())
        return false;
    return CheckedAdd(left, -right, output);
}

bool CheckedAbsolute(PortfolioMicrounits value,
           PortfolioMicrounits& output)
{
    if (value == std::numeric_limits<PortfolioMicrounits>::min()) return false;
    output = value < 0 ? -value : value;
    return true;
}

PortfolioCompileResult Reject(const char* reason)
{
    PortfolioCompileResult result;
    result.reasonCode = reason;
    return result;
}
}

const char* PortfolioCompiler::Version()
{
    return "portfolio-compiler-v1";
}

PortfolioCompileResult PortfolioCompiler::Compile(
    const std::vector<StrategyTargetIntent>& intents,
    const AuthoritativePortfolioInput& authoritative,
    const PortfolioCapitalPolicy& policy)
{
    if (!authoritative.complete || authoritative.generation == 0)
        return Reject("PORTFOLIO_SNAPSHOT_INCOMPLETE");
    if (policy.maximumGrossTarget <= 0 ||
        policy.maximumStrategies == 0 || policy.maximumInstruments == 0)
        return Reject("PORTFOLIO_POLICY_INVALID");

    // Validate the complete authoritative position map before taking the
    // no-intent fast path.  A malformed snapshot must never be treated as a
    // successful compile merely because there is nothing to trade right now;
    // the same snapshot may be reused for a later risk-increasing decision.
    // ``INT64_MIN`` cannot be represented by the signed fixed-point absolute
    // value used for delta/budget checks, so reject it at the authority
    // boundary instead of allowing a later subtraction to be ambiguous.
    for (std::map<std::string, PortfolioMicrounits>::const_iterator it =
             authoritative.currentPositions.begin();
         it != authoritative.currentPositions.end(); ++it)
    {
        if (!CanonicalId(it->first, 128))
            return Reject("PORTFOLIO_SNAPSHOT_INSTRUMENT_INVALID");
        PortfolioMicrounits absolutePosition = 0;
        if (!CheckedAbsolute(it->second, absolutePosition))
            return Reject("PORTFOLIO_ARITHMETIC_OVERFLOW");
    }
    if (intents.empty())
    {
        PortfolioCompileResult result;
        result.accepted = true;
        result.reasonCode = "PORTFOLIO_NO_INTENTS";
        return result;
    }

    std::vector<StrategyTargetIntent> ordered = intents;
    std::sort(ordered.begin(), ordered.end(),
        [](const StrategyTargetIntent& left,
 const StrategyTargetIntent& right) {
  if (left.strategyId != right.strategyId)
      return left.strategyId < right.strategyId;
  if (left.instrument != right.instrument)
      return left.instrument < right.instrument;
  return left.targetPosition < right.targetPosition;
        });

    std::set<std::string> strategies;
    std::set<std::string> instruments;
    std::set<std::string> uniqueIntents;
    PortfolioCompileResult result;
    for (std::size_t i = 0; i < ordered.size(); ++i)
    {
        const StrategyTargetIntent& intent = ordered[i];
        if (!CanonicalId(intent.strategyId, 64) ||
  !CanonicalId(intent.instrument, 128))
  return Reject("PORTFOLIO_INTENT_IDENTITY_INVALID");
        if (intent.snapshotGeneration != authoritative.generation)
  return Reject("PORTFOLIO_INTENT_GENERATION_MISMATCH");
        const std::string unique = intent.strategyId + '\x1f' + intent.instrument;
        if (!uniqueIntents.insert(unique).second)
  return Reject("PORTFOLIO_DUPLICATE_STRATEGY_INSTRUMENT");
        strategies.insert(intent.strategyId);
        instruments.insert(intent.instrument);
        if (strategies.size() > policy.maximumStrategies)
  return Reject("PORTFOLIO_STRATEGY_COUNT_LIMIT");
        if (instruments.size() > policy.maximumInstruments)
  return Reject("PORTFOLIO_INSTRUMENT_COUNT_LIMIT");

        const std::map<std::string, StrategyCapitalBudget>::const_iterator budget =
  policy.strategyBudgets.find(intent.strategyId);
        if (budget == policy.strategyBudgets.end() ||
  budget->second.strategyId != intent.strategyId ||
  budget->second.maximumGrossTarget <= 0)
  return Reject("PORTFOLIO_STRATEGY_BUDGET_MISSING");

        PortfolioMicrounits absoluteTarget = 0;
        if (!CheckedAbsolute(intent.targetPosition, absoluteTarget))
  return Reject("PORTFOLIO_ARITHMETIC_OVERFLOW");
        PortfolioMicrounits strategyGross =
  result.strategyGrossTargets[intent.strategyId];
        if (!CheckedAdd(strategyGross, absoluteTarget, strategyGross))
  return Reject("PORTFOLIO_ARITHMETIC_OVERFLOW");
        if (strategyGross > budget->second.maximumGrossTarget)
  return Reject("PORTFOLIO_STRATEGY_BUDGET_EXCEEDED");
        result.strategyGrossTargets[intent.strategyId] = strategyGross;

        PortfolioMicrounits net = result.netTargets[intent.instrument];
        if (!CheckedAdd(net, intent.targetPosition, net))
  return Reject("PORTFOLIO_ARITHMETIC_OVERFLOW");
        result.netTargets[intent.instrument] = net;
    }

    for (std::map<std::string, PortfolioMicrounits>::const_iterator it =
   result.netTargets.begin(); it != result.netTargets.end(); ++it)
    {
        PortfolioMicrounits absoluteTarget = 0;
        if (!CheckedAbsolute(it->second, absoluteTarget) ||
  !CheckedAdd(result.portfolioGrossTarget,
              absoluteTarget, result.portfolioGrossTarget))
  return Reject("PORTFOLIO_ARITHMETIC_OVERFLOW");
    }
    if (result.portfolioGrossTarget > policy.maximumGrossTarget)
        return Reject("PORTFOLIO_CAPITAL_BUDGET_EXCEEDED");

    std::set<std::string> deltaInstruments = instruments;
    for (std::map<std::string, PortfolioMicrounits>::const_iterator it =
   authoritative.currentPositions.begin();
         it != authoritative.currentPositions.end(); ++it)
    {
        if (!CanonicalId(it->first, 128))
  return Reject("PORTFOLIO_SNAPSHOT_INSTRUMENT_INVALID");
        deltaInstruments.insert(it->first);
    }
    for (std::set<std::string>::const_iterator it = deltaInstruments.begin();
         it != deltaInstruments.end(); ++it)
    {
        PortfolioTargetDelta delta;
        delta.instrument = *it;
        const std::map<std::string, PortfolioMicrounits>::const_iterator current =
  authoritative.currentPositions.find(*it);
        if (current != authoritative.currentPositions.end())
  delta.currentPosition = current->second;
        const std::map<std::string, PortfolioMicrounits>::const_iterator target =
  result.netTargets.find(*it);
        if (target != result.netTargets.end())
  delta.targetPosition = target->second;
        if (!CheckedSubtract(
      delta.targetPosition, delta.currentPosition, delta.delta))
  return Reject("PORTFOLIO_ARITHMETIC_OVERFLOW");
        if (delta.delta != 0) result.deltas.push_back(delta);
    }
    result.accepted = true;
    result.reasonCode = "PORTFOLIO_COMPILED";
    return result;
}
