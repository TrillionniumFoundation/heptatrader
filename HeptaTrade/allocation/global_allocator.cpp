#include "global_allocator.h"

#include "../numeric/fixed_decimal.h"

#include <algorithm>
#include <functional>
#include <iomanip>
#include <limits>
#include <openssl/evp.h>
#include <sstream>

namespace
{
bool CanonicalId(const std::string& value, std::size_t maximum)
{
    if (value.empty() || value.size() > maximum) return false;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const unsigned char c = static_cast<unsigned char>(value[i]);
        const bool alphaNumeric = (c >= 'A' && c <= 'Z') ||
            (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9');
        if (!(alphaNumeric || c == '-' || c == '_' || c == '.' || c == ':'))
            return false;
    }
    return true;
}

bool CheckedAdd(
    DecisionMicrounits left,
    DecisionMicrounits right,
    DecisionMicrounits& out)
{
    if ((right > 0 && left >
            std::numeric_limits<DecisionMicrounits>::max() - right) ||
        (right < 0 && left <
            std::numeric_limits<DecisionMicrounits>::min() - right))
        return false;
    out = left + right;
    return true;
}

bool CheckedAbsolute(DecisionMicrounits value, DecisionMicrounits& out)
{
    if (value == std::numeric_limits<DecisionMicrounits>::min()) return false;
    out = value < 0 ? -value : value;
    return true;
}

void AppendField(std::string& out, const char* name, const std::string& value)
{
    out.append(name);
    out.push_back('=');
    out.append(std::to_string(value.size()));
    out.push_back(':');
    out.append(value);
    out.push_back(';');
}

std::string Sha256(const std::string& value)
{
    unsigned char digest[EVP_MAX_MD_SIZE];
    unsigned int length = 0;
    EVP_MD_CTX* context = EVP_MD_CTX_new();
    if (context == nullptr) return std::string();
    const bool ok = EVP_DigestInit_ex(context, EVP_sha256(), nullptr) == 1 &&
        EVP_DigestUpdate(context, value.data(), value.size()) == 1 &&
        EVP_DigestFinal_ex(context, digest, &length) == 1;
    EVP_MD_CTX_free(context);
    if (!ok) return std::string();
    std::ostringstream out;
    out << "sha256:" << std::hex << std::setfill('0');
    for (unsigned int i = 0; i < length; ++i)
        out << std::setw(2) << static_cast<unsigned int>(digest[i]);
    return out.str();
}

bool ValidPolicy(const GlobalAllocationPolicy& policy)
{
    if (!CanonicalId(policy.policyRevision, 128u) ||
        policy.maximumGrossTarget <= 0 ||
        policy.maximumGrossTarget > HeptaFixedDecimal::kMaximumRaw ||
        policy.maximumInstruments == 0 || policy.maximumInstruments > 4096u ||
        policy.maximumExactCombinations == 0 ||
        policy.maximumExactCombinations > 1000000u)
        return false;
    for (std::map<std::string, DecisionMicrounits>::const_iterator it =
             policy.instrumentAbsoluteLimits.begin();
         it != policy.instrumentAbsoluteLimits.end(); ++it)
    {
        if (it->first.empty() || it->second <= 0 ||
            it->second > HeptaFixedDecimal::kMaximumRaw)
            return false;
    }
    return true;
}

bool CandidateFeasible(
    const std::map<std::string, DecisionMicrounits>& current,
    const StrategyProposalCandidate& candidate,
    const GlobalAllocationPolicy& policy,
    std::map<std::string, DecisionMicrounits>& projected)
{
    projected = current;
    for (std::size_t i = 0; i < candidate.targets.size(); ++i)
    {
        const StrategyCandidateTarget& target = candidate.targets[i];
        const std::map<std::string, DecisionMicrounits>::const_iterator limit =
            policy.instrumentAbsoluteLimits.find(target.instrument);
        if (limit == policy.instrumentAbsoluteLimits.end()) return false;
        DecisionMicrounits next = 0;
        if (!CheckedAdd(projected[target.instrument],
                        target.targetPosition, next))
            return false;
        DecisionMicrounits absolute = 0;
        if (!CheckedAbsolute(next, absolute) || absolute > limit->second)
            return false;
        projected[target.instrument] = next;
    }
    std::size_t active = 0;
    DecisionMicrounits gross = 0;
    for (std::map<std::string, DecisionMicrounits>::const_iterator it =
             projected.begin(); it != projected.end(); ++it)
    {
        if (it->second == 0) continue;
        ++active;
        DecisionMicrounits absolute = 0;
        if (!CheckedAbsolute(it->second, absolute) ||
            !CheckedAdd(gross, absolute, gross))
            return false;
    }
    return active <= policy.maximumInstruments &&
        gross <= policy.maximumGrossTarget;
}

std::string ChoiceKey(
    const ProposalSet& proposalSet,
    const std::vector<int>& choices)
{
    std::string key;
    for (std::size_t i = 0; i < choices.size(); ++i)
    {
        AppendField(key, "module", proposalSet.proposals[i].moduleId);
        AppendField(key, "candidate", choices[i] < 0 ? "!" :
            proposalSet.proposals[i].candidates[
                static_cast<std::size_t>(choices[i])].candidateId);
    }
    return key;
}

GlobalAllocationResult Reject(const char* code)
{
    GlobalAllocationResult result;
    result.reasonCode = code;
    return result;
}
}

const char* GlobalAllocator::Version()
{
    return "hepta.global-optimization.v1";
}

std::string GlobalAllocator::SolverDigest(
    const AllocationSolverResult& solver)
{
    std::string canonical;
    AppendField(canonical, "schema", "hepta.solver-result.v1");
    AppendField(canonical, "status", solver.status);
    AppendField(canonical, "objective", std::to_string(solver.objective));
    AppendField(canonical, "primal_bound",
                std::to_string(solver.primalBound));
    AppendField(canonical, "upper_bound",
                std::to_string(solver.upperBound));
    AppendField(canonical, "absolute_gap",
                std::to_string(solver.absoluteGap));
    AppendField(canonical, "combinations_explored",
                std::to_string(solver.combinationsExplored));
    AppendField(canonical, "exact", solver.exact ? "1" : "0");
    return Sha256(canonical);
}

std::string GlobalAllocator::PlanDigest(const AllocationPlan& plan)
{
    std::string canonical;
    AppendField(canonical, "schema", "hepta.allocation-plan.v1");
    AppendField(canonical, "plan_id", plan.planId);
    AppendField(canonical, "allocator_epoch",
                std::to_string(plan.allocatorEpoch));
    AppendField(canonical, "capital_pool", plan.capitalPool);
    AppendField(canonical, "account_book", plan.accountBook);
    AppendField(canonical, "policy_revision", plan.policyRevision);
    AppendField(canonical, "proposal_set_digest", plan.proposalSetDigest);
    AppendField(canonical, "snapshot_digest", plan.snapshotDigest);
    AppendField(canonical, "proposal_captured_at_ms",
                std::to_string(plan.proposalCapturedAtMs));
    AppendField(canonical, "proposal_valid_until_ms",
                std::to_string(plan.proposalValidUntilMs));
    AppendField(canonical, "snapshot_valid_until_ms",
                std::to_string(plan.snapshotValidUntilMs));
    AppendField(canonical, "solver_digest", plan.solver.digest);
    for (std::size_t i = 0; i < plan.targets.size(); ++i)
    {
        AppendField(canonical, "instrument", plan.targets[i].instrument);
        AppendField(canonical, "target_position",
                    std::to_string(plan.targets[i].targetPosition));
    }
    for (std::size_t i = 0; i < plan.acceptedCandidates.size(); ++i)
        AppendField(canonical, "accepted", plan.acceptedCandidates[i]);
    for (std::size_t i = 0; i < plan.rejectedProposals.size(); ++i)
        AppendField(canonical, "rejected", plan.rejectedProposals[i]);
    AppendField(canonical, "created_at_ms",
                std::to_string(plan.createdAtMs));
    AppendField(canonical, "valid_until_ms",
                std::to_string(plan.validUntilMs));
    AppendField(canonical, "numeric_policy", "hepta.numeric.fixed-v1");
    return Sha256(canonical);
}

GlobalAllocationResult GlobalAllocator::Allocate(
    const ProposalSet& proposalSet,
    const GlobalAllocationPolicy& policy,
    std::uint64_t allocatorEpoch,
    std::uint64_t createdAtMs)
{
    if (!ValidPolicy(policy)) return Reject("ALLOCATION_POLICY_INVALID");
    if (allocatorEpoch == 0 || createdAtMs == 0)
        return Reject("ALLOCATION_TIME_ENVELOPE_INVALID");
    if (proposalSet.proposals.empty() || proposalSet.digest.empty() ||
        ProposalSetBuilder::Digest(proposalSet) != proposalSet.digest)
        return Reject("ALLOCATION_PROPOSAL_SET_INVALID");
    if (proposalSet.capturedAtMs == 0 || proposalSet.validUntilMs <= createdAtMs ||
        createdAtMs < proposalSet.capturedAtMs ||
        createdAtMs < proposalSet.validFromMs ||
        proposalSet.validUntilMs > proposalSet.snapshotValidUntilMs)
        return Reject("ALLOCATION_TIME_ENVELOPE_INVALID");

    bool exact = true;
    std::uint64_t combinations = 1;
    DecisionMicrounits upperBound = 0;
    for (std::size_t i = 0; i < proposalSet.proposals.size(); ++i)
    {
        const std::uint64_t factor =
            static_cast<std::uint64_t>(
                proposalSet.proposals[i].candidates.size()) + 1u;
        if (combinations > policy.maximumExactCombinations / factor)
            exact = false;
        else
            combinations *= factor;
        DecisionMicrounits bestUtility = 0;
        for (std::size_t j = 0;
             j < proposalSet.proposals[i].candidates.size(); ++j)
            bestUtility = std::max(
                bestUtility,
                proposalSet.proposals[i].candidates[j].utility);
        if (!CheckedAdd(upperBound, bestUtility, upperBound))
            return Reject("ALLOCATION_ARITHMETIC_OVERFLOW");
    }
    if (combinations > policy.maximumExactCombinations) exact = false;

    std::vector<int> bestChoices(proposalSet.proposals.size(), -1);
    std::map<std::string, DecisionMicrounits> bestTargets;
    DecisionMicrounits bestObjective = 0;
    std::string bestKey = ChoiceKey(proposalSet, bestChoices);
    std::uint64_t explored = 0;

    if (exact)
    {
        std::vector<int> choices(proposalSet.proposals.size(), -1);
        std::function<void(std::size_t,
                           const std::map<std::string, DecisionMicrounits>&,
                           DecisionMicrounits)> visit;
        visit = [&](std::size_t index,
                    const std::map<std::string, DecisionMicrounits>& targets,
                    DecisionMicrounits objective) {
            if (index == proposalSet.proposals.size())
            {
                ++explored;
                const std::string key = ChoiceKey(proposalSet, choices);
                if (objective > bestObjective ||
                    (objective == bestObjective && key < bestKey))
                {
                    bestObjective = objective;
                    bestChoices = choices;
                    bestTargets = targets;
                    bestKey = key;
                }
                return;
            }
            choices[index] = -1;
            visit(index + 1u, targets, objective);
            const StrategyProposal& proposal = proposalSet.proposals[index];
            for (std::size_t candidateIndex = 0;
                 candidateIndex < proposal.candidates.size(); ++candidateIndex)
            {
                std::map<std::string, DecisionMicrounits> projected;
                if (!CandidateFeasible(targets,
                        proposal.candidates[candidateIndex], policy, projected))
                    continue;
                DecisionMicrounits nextObjective = 0;
                if (!CheckedAdd(objective,
                        proposal.candidates[candidateIndex].utility,
                        nextObjective))
                    continue;
                choices[index] = static_cast<int>(candidateIndex);
                visit(index + 1u, projected, nextObjective);
            }
            choices[index] = -1;
        };
        const std::map<std::string, DecisionMicrounits> empty;
        visit(0, empty, 0);
        upperBound = bestObjective;
    }
    else
    {
        std::map<std::string, DecisionMicrounits> targets;
        for (std::size_t index = 0;
             index < proposalSet.proposals.size(); ++index)
        {
            const StrategyProposal& proposal = proposalSet.proposals[index];
            std::vector<std::size_t> order;
            for (std::size_t i = 0; i < proposal.candidates.size(); ++i)
                order.push_back(i);
            std::sort(order.begin(), order.end(),
                [&](std::size_t left, std::size_t right) {
                    if (proposal.candidates[left].utility !=
                        proposal.candidates[right].utility)
                        return proposal.candidates[left].utility >
                            proposal.candidates[right].utility;
                    return proposal.candidates[left].candidateId <
                        proposal.candidates[right].candidateId;
                });
            for (std::size_t rank = 0; rank < order.size(); ++rank)
            {
                ++explored;
                const std::size_t candidateIndex = order[rank];
                const StrategyProposalCandidate& candidate =
                    proposal.candidates[candidateIndex];
                if (candidate.utility <= 0) continue;
                std::map<std::string, DecisionMicrounits> projected;
                if (!CandidateFeasible(targets, candidate, policy, projected))
                    continue;
                DecisionMicrounits nextObjective = 0;
                if (!CheckedAdd(bestObjective, candidate.utility,
                                nextObjective))
                    return Reject("ALLOCATION_ARITHMETIC_OVERFLOW");
                targets.swap(projected);
                bestObjective = nextObjective;
                bestChoices[index] = static_cast<int>(candidateIndex);
                break;
            }
        }
        bestTargets = targets;
    }

    AllocationPlan plan;
    plan.allocatorEpoch = allocatorEpoch;
    plan.capitalPool = proposalSet.capitalPool;
    plan.accountBook = proposalSet.accountBook;
    plan.policyRevision = policy.policyRevision;
    plan.proposalSetDigest = proposalSet.digest;
    plan.snapshotDigest = proposalSet.snapshotDigest;
    plan.proposalCapturedAtMs = proposalSet.capturedAtMs;
    plan.proposalValidUntilMs = proposalSet.validUntilMs;
    plan.snapshotValidUntilMs = proposalSet.snapshotValidUntilMs;
    plan.createdAtMs = createdAtMs;
    plan.validUntilMs = proposalSet.validUntilMs;
    for (std::map<std::string, DecisionMicrounits>::const_iterator it =
             bestTargets.begin(); it != bestTargets.end(); ++it)
    {
        if (it->second == 0) continue;
        AllocationTarget target;
        target.instrument = it->first;
        target.targetPosition = it->second;
        plan.targets.push_back(target);
    }
    for (std::size_t i = 0; i < bestChoices.size(); ++i)
    {
        if (bestChoices[i] < 0)
            plan.rejectedProposals.push_back(
                proposalSet.proposals[i].proposalId);
        else
            plan.acceptedCandidates.push_back(
                proposalSet.proposals[i].proposalId + ":" +
                proposalSet.proposals[i].candidates[
                    static_cast<std::size_t>(bestChoices[i])].candidateId);
    }
    plan.solver.status = exact ? "optimal" : "feasible_not_proven";
    plan.solver.objective = bestObjective;
    plan.solver.primalBound = bestObjective;
    plan.solver.upperBound = upperBound;
    if (upperBound < bestObjective ||
        !CheckedAdd(upperBound, -bestObjective, plan.solver.absoluteGap))
        return Reject("ALLOCATION_BOUND_INVALID");
    plan.solver.combinationsExplored = explored;
    plan.solver.exact = exact;
    plan.solver.digest = SolverDigest(plan.solver);
    if (plan.solver.digest.empty())
        return Reject("ALLOCATION_SOLVER_DIGEST_FAILED");
    std::string planIdentity;
    AppendField(planIdentity, "proposal_set", plan.proposalSetDigest);
    AppendField(planIdentity, "solver", plan.solver.digest);
    AppendField(planIdentity, "epoch", std::to_string(allocatorEpoch));
    AppendField(planIdentity, "created", std::to_string(createdAtMs));
    const std::string identityDigest = Sha256(planIdentity);
    if (identityDigest.size() < 23u)
        return Reject("ALLOCATION_PLAN_ID_FAILED");
    plan.planId = "plan-" + identityDigest.substr(7, 16);
    plan.planDigest = PlanDigest(plan);
    if (plan.planDigest.empty()) return Reject("ALLOCATION_PLAN_DIGEST_FAILED");

    GlobalAllocationResult result;
    result.accepted = true;
    result.reasonCode = exact
        ? "ALLOCATION_OPTIMAL" : "ALLOCATION_FEASIBLE_NOT_PROVEN";
    result.plan = plan;
    result.receipt = GlobalDecisionReceipt(plan);
    return result;
}
