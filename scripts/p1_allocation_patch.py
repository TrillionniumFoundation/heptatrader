#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def write(path, content):
    (ROOT / path).write_text(content, encoding='utf-8')


def replace(path, old, new):
    target = ROOT / path
    text = target.read_text(encoding='utf-8')
    if old not in text:
        if new in text:
            return
        raise RuntimeError(f'missing anchor in {path}: {old[:100]!r}')
    if text.count(old) != 1:
        raise RuntimeError(f'non-unique anchor in {path}')
    target.write_text(text.replace(old, new, 1), encoding='utf-8')


write('HeptaTrade/proposal/proposal_set.h', r'''#pragma once

#include "../strategy_runtime/strategy_proposal.h"

#include <cstdint>
#include <string>
#include <vector>

struct ProposalSet
{
    std::string capitalPool;
    std::string accountBook;
    std::string snapshotDigest;
    std::uint64_t capturedAtMs = 0;
    std::uint64_t validFromMs = 0;
    std::uint64_t validUntilMs = 0;
    std::uint64_t snapshotValidUntilMs = 0;
    std::vector<StrategyProposal> proposals;
    std::string digest;
};

struct ProposalSetBuildResult
{
    bool accepted = false;
    std::string reasonCode;
    ProposalSet proposalSet;
};

class ProposalSetBuilder
{
public:
    static const char* Version();
    static ProposalSetBuildResult Build(
        const std::vector<StrategyProposal>& proposals,
        const std::vector<std::string>& expectedModules,
        std::uint64_t nowMs,
        std::uint64_t snapshotValidUntilMs);
    static std::string Digest(const ProposalSet& proposalSet);
};
''')

write('HeptaTrade/proposal/proposal_set.cpp', r'''#include "proposal_set.h"

#include <algorithm>
#include <iomanip>
#include <limits>
#include <openssl/evp.h>
#include <set>
#include <sstream>

namespace
{
bool CanonicalId(const std::string& value, std::size_t maximum)
{
    if (value.empty() || value.size() > maximum) return false;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const unsigned char c = static_cast<unsigned char>(value[i]);
        const bool alphaNumeric =
            (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
            (c >= '0' && c <= '9');
        if (!(alphaNumeric || c == '-' || c == '_' || c == '.' || c == ':'))
            return false;
    }
    return true;
}

void AppendField(std::string& out, const char* name, const std::string& value)
{
    out.append(name); out.push_back('=');
    out.append(std::to_string(value.size())); out.push_back(':');
    out.append(value); out.push_back(';');
}

std::string Sha256(const std::string& value)
{
    unsigned char digest[EVP_MAX_MD_SIZE]; unsigned int length = 0;
    EVP_MD_CTX* context = EVP_MD_CTX_new();
    if (context == nullptr) return std::string();
    const bool ok = EVP_DigestInit_ex(context, EVP_sha256(), nullptr) == 1 &&
        EVP_DigestUpdate(context, value.data(), value.size()) == 1 &&
        EVP_DigestFinal_ex(context, digest, &length) == 1;
    EVP_MD_CTX_free(context);
    if (!ok) return std::string();
    std::ostringstream out; out << "sha256:" << std::hex << std::setfill('0');
    for (unsigned int i = 0; i < length; ++i)
        out << std::setw(2) << static_cast<unsigned int>(digest[i]);
    return out.str();
}

bool CheckedAdd(std::uint64_t left, std::uint64_t right, std::uint64_t& out)
{
    if (left > std::numeric_limits<std::uint64_t>::max() - right) return false;
    out = left + right; return true;
}

ProposalSetBuildResult Reject(const char* code)
{
    ProposalSetBuildResult result; result.reasonCode = code; return result;
}
}

const char* ProposalSetBuilder::Version() { return "proposal-set.v1"; }

std::string ProposalSetBuilder::Digest(const ProposalSet& proposalSet)
{
    std::string canonical;
    AppendField(canonical, "schema", Version());
    AppendField(canonical, "capital_pool", proposalSet.capitalPool);
    AppendField(canonical, "account_book", proposalSet.accountBook);
    AppendField(canonical, "snapshot_digest", proposalSet.snapshotDigest);
    AppendField(canonical, "captured_at_ms", std::to_string(proposalSet.capturedAtMs));
    AppendField(canonical, "valid_from_ms", std::to_string(proposalSet.validFromMs));
    AppendField(canonical, "valid_until_ms", std::to_string(proposalSet.validUntilMs));
    AppendField(canonical, "snapshot_valid_until_ms",
                std::to_string(proposalSet.snapshotValidUntilMs));
    for (std::size_t i = 0; i < proposalSet.proposals.size(); ++i)
    {
        AppendField(canonical, "module_id", proposalSet.proposals[i].moduleId);
        AppendField(canonical, "proposal_id", proposalSet.proposals[i].proposalId);
        AppendField(canonical, "proposal_digest", proposalSet.proposals[i].proposalDigest);
    }
    return Sha256(canonical);
}

ProposalSetBuildResult ProposalSetBuilder::Build(
    const std::vector<StrategyProposal>& proposals,
    const std::vector<std::string>& expectedModules,
    std::uint64_t nowMs,
    std::uint64_t snapshotValidUntilMs)
{
    if (nowMs == 0 || snapshotValidUntilMs <= nowMs ||
        expectedModules.empty() || expectedModules.size() > 256u)
        return Reject("PROPOSAL_SET_EXPECTATION_INVALID");
    std::set<std::string> expected;
    for (std::size_t i = 0; i < expectedModules.size(); ++i)
        if (!CanonicalId(expectedModules[i], 128u) ||
            expectedModules[i].compare(0, 6, "hepta.") != 0 ||
            !expected.insert(expectedModules[i]).second)
            return Reject("PROPOSAL_SET_EXPECTATION_INVALID");
    if (proposals.size() != expected.size()) return Reject("PROPOSAL_SET_INCOMPLETE");

    ProposalSet normalized;
    normalized.capturedAtMs = nowMs;
    normalized.validFromMs = nowMs;
    normalized.validUntilMs = snapshotValidUntilMs;
    normalized.snapshotValidUntilMs = snapshotValidUntilMs;
    std::set<std::string> modules;
    std::set<std::string> proposalIds;
    for (std::size_t i = 0; i < proposals.size(); ++i)
    {
        const StrategyProposalSealResult sealed =
            StrategyProposalContract::ValidateAndSeal(proposals[i], nowMs);
        if (!sealed.accepted)
        {
            ProposalSetBuildResult rejected = Reject("PROPOSAL_SET_MEMBER_INVALID");
            rejected.proposalSet.proposals.push_back(sealed.proposal);
            return rejected;
        }
        const StrategyProposal& proposal = sealed.proposal;
        if (expected.find(proposal.moduleId) == expected.end())
            return Reject("PROPOSAL_SET_UNEXPECTED_MODULE");
        if (!modules.insert(proposal.moduleId).second)
            return Reject("PROPOSAL_SET_DUPLICATE_MODULE");
        if (!proposalIds.insert(proposal.proposalId).second)
            return Reject("PROPOSAL_SET_DUPLICATE_PROPOSAL");
        if (normalized.proposals.empty())
        {
            normalized.capitalPool = proposal.capitalPool;
            normalized.accountBook = proposal.accountBook;
            normalized.snapshotDigest = proposal.snapshotDigest;
        }
        else if (proposal.capitalPool != normalized.capitalPool ||
                 proposal.accountBook != normalized.accountBook)
            return Reject("PROPOSAL_SET_BOOK_MISMATCH");
        else if (proposal.snapshotDigest != normalized.snapshotDigest)
            return Reject("PROPOSAL_SET_SNAPSHOT_MISMATCH");

        std::uint64_t horizonEnd = 0;
        if (!CheckedAdd(nowMs, proposal.horizonMs, horizonEnd))
            return Reject("PROPOSAL_SET_TIME_OVERFLOW");
        const std::uint64_t memberEnd = std::min(proposal.expiresAtMs, horizonEnd);
        normalized.validFromMs = std::max(normalized.validFromMs, proposal.validFromMs);
        normalized.validUntilMs = std::min(normalized.validUntilMs, memberEnd);
        normalized.proposals.push_back(proposal);
    }
    if (modules != expected) return Reject("PROPOSAL_SET_INCOMPLETE");
    if (normalized.validFromMs > nowMs || normalized.validUntilMs <= nowMs ||
        normalized.validUntilMs > normalized.snapshotValidUntilMs)
        return Reject("PROPOSAL_SET_TIME_ENVELOPE_INVALID");
    std::sort(normalized.proposals.begin(), normalized.proposals.end(),
        [](const StrategyProposal& left, const StrategyProposal& right) {
            if (left.moduleId != right.moduleId) return left.moduleId < right.moduleId;
            return left.proposalId < right.proposalId;
        });
    normalized.digest = Digest(normalized);
    if (normalized.digest.empty()) return Reject("PROPOSAL_SET_DIGEST_FAILED");
    ProposalSetBuildResult result;
    result.accepted = true; result.reasonCode = "PROPOSAL_SET_ACCEPTED";
    result.proposalSet = normalized; return result;
}
''')

write('HeptaTrade/allocation/global_allocator.h', r'''#pragma once

#include "../proposal/proposal_set.h"

#include <cstddef>
#include <cstdint>
#include <map>
#include <string>
#include <vector>

struct AllocationTarget { std::string instrument; DecisionMicrounits targetPosition = 0; };

struct GlobalAllocationPolicy
{
    std::string policyRevision;
    DecisionMicrounits maximumGrossTarget = 0;
    std::size_t maximumInstruments = 0;
    std::uint64_t maximumExactCombinations = 0;
    std::map<std::string, DecisionMicrounits> instrumentAbsoluteLimits;
};

struct AllocationSolverResult
{
    std::string status;
    DecisionMicrounits objective = 0;
    DecisionMicrounits primalBound = 0;
    DecisionMicrounits upperBound = 0;
    DecisionMicrounits absoluteGap = 0;
    std::uint64_t combinationsExplored = 0;
    bool exact = false;
    std::string digest;
};

struct AllocationPlan
{
    std::string planId;
    std::uint64_t allocatorEpoch = 0;
    std::string capitalPool;
    std::string accountBook;
    std::string policyRevision;
    std::string proposalSetDigest;
    std::string snapshotDigest;
    std::uint64_t proposalCapturedAtMs = 0;
    std::uint64_t proposalValidUntilMs = 0;
    std::uint64_t snapshotValidUntilMs = 0;
    AllocationSolverResult solver;
    std::vector<AllocationTarget> targets;
    std::vector<std::string> acceptedCandidates;
    std::vector<std::string> rejectedProposals;
    std::uint64_t createdAtMs = 0;
    std::uint64_t validUntilMs = 0;
    std::string planDigest;
};

class GlobalAllocator;

class GlobalDecisionReceipt
{
public:
    GlobalDecisionReceipt() noexcept : m_valid(false) {}
    bool IsValid() const noexcept { return m_valid; }
    const AllocationPlan& Plan() const noexcept { return m_plan; }
private:
    explicit GlobalDecisionReceipt(const AllocationPlan& plan)
        : m_plan(plan), m_valid(true) {}
    AllocationPlan m_plan;
    bool m_valid;
    friend class GlobalAllocator;
};

struct GlobalAllocationResult
{
    bool accepted = false;
    std::string reasonCode;
    AllocationPlan plan;
    GlobalDecisionReceipt receipt;
};

class GlobalAllocator
{
public:
    static const char* Version();
    static GlobalAllocationResult Allocate(
        const ProposalSet& proposalSet,
        const GlobalAllocationPolicy& policy,
        std::uint64_t allocatorEpoch,
        std::uint64_t createdAtMs);
    static std::string SolverDigest(const AllocationSolverResult& solver);
    static std::string PlanDigest(const AllocationPlan& plan);
};
''')

replace('HeptaTrade/allocation/global_allocator.cpp',
'''namespace
{
bool CheckedAdd(''',
'''namespace
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

bool CheckedAdd(''')
replace('HeptaTrade/allocation/global_allocator.cpp',
'''    if (policy.maximumGrossTarget <= 0 ||''',
'''    if (!CanonicalId(policy.policyRevision, 128u) ||
        policy.maximumGrossTarget <= 0 ||''')
replace('HeptaTrade/allocation/global_allocator.cpp',
'''    AppendField(canonical, "account_book", plan.accountBook);
    AppendField(canonical, "proposal_set_digest", plan.proposalSetDigest);''',
'''    AppendField(canonical, "account_book", plan.accountBook);
    AppendField(canonical, "policy_revision", plan.policyRevision);
    AppendField(canonical, "proposal_set_digest", plan.proposalSetDigest);''')
replace('HeptaTrade/allocation/global_allocator.cpp',
'''    AppendField(canonical, "snapshot_digest", plan.snapshotDigest);
    AppendField(canonical, "solver_digest", plan.solver.digest);''',
'''    AppendField(canonical, "snapshot_digest", plan.snapshotDigest);
    AppendField(canonical, "proposal_captured_at_ms",
                std::to_string(plan.proposalCapturedAtMs));
    AppendField(canonical, "proposal_valid_until_ms",
                std::to_string(plan.proposalValidUntilMs));
    AppendField(canonical, "snapshot_valid_until_ms",
                std::to_string(plan.snapshotValidUntilMs));
    AppendField(canonical, "solver_digest", plan.solver.digest);''')
replace('HeptaTrade/allocation/global_allocator.cpp',
'''    std::uint64_t allocatorEpoch,
    std::uint64_t createdAtMs,
    std::uint64_t validUntilMs)
{
    if (!ValidPolicy(policy)) return Reject("ALLOCATION_POLICY_INVALID");
    if (allocatorEpoch == 0 || createdAtMs == 0 ||
        validUntilMs <= createdAtMs)
        return Reject("ALLOCATION_TIME_ENVELOPE_INVALID");
    if (proposalSet.proposals.empty() || proposalSet.digest.empty() ||
        ProposalSetBuilder::Digest(proposalSet) != proposalSet.digest)
        return Reject("ALLOCATION_PROPOSAL_SET_INVALID");''',
'''    std::uint64_t allocatorEpoch,
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
        return Reject("ALLOCATION_TIME_ENVELOPE_INVALID");''')
replace('HeptaTrade/allocation/global_allocator.cpp',
'''    plan.accountBook = proposalSet.accountBook;
    plan.proposalSetDigest = proposalSet.digest;
    plan.snapshotDigest = proposalSet.snapshotDigest;
    plan.createdAtMs = createdAtMs;
    plan.validUntilMs = validUntilMs;''',
'''    plan.accountBook = proposalSet.accountBook;
    plan.policyRevision = policy.policyRevision;
    plan.proposalSetDigest = proposalSet.digest;
    plan.snapshotDigest = proposalSet.snapshotDigest;
    plan.proposalCapturedAtMs = proposalSet.capturedAtMs;
    plan.proposalValidUntilMs = proposalSet.validUntilMs;
    plan.snapshotValidUntilMs = proposalSet.snapshotValidUntilMs;
    plan.createdAtMs = createdAtMs;
    plan.validUntilMs = proposalSet.validUntilMs;''')
replace('HeptaTrade/allocation/global_allocator.cpp',
'''    result.plan = plan;
    return result;''',
'''    result.plan = plan;
    result.receipt = GlobalDecisionReceipt(plan);
    return result;''')

write('HeptaTrade/execution/allocation_plan_revalidator.h', r'''#pragma once

#include "../allocation/global_allocator.h"
#include "../portfolio/portfolio_compiler.h"

#include <cstdint>
#include <string>

struct AllocationExecutionContext
{
    std::uint64_t allocatorEpoch = 0;
    std::string capitalPool;
    std::string accountBook;
    std::string policyRevision;
    std::string proposalSetDigest;
    std::string authoritativeSnapshotDigest;
    std::uint64_t authoritativeSnapshotValidUntilMs = 0;
};

struct AllocationPlanRevalidationResult
{
    bool accepted = false;
    std::string reasonCode;
    PortfolioCompileResult compiled;
};

class AllocationPlanRevalidator
{
public:
    static const char* Version();
    static AllocationPlanRevalidationResult ValidateShadow(
        const GlobalDecisionReceipt& receipt,
        const AllocationExecutionContext& context,
        std::uint64_t nowMs,
        const AuthoritativePortfolioInput& authoritative,
        const PortfolioCapitalPolicy& policy);
};
''')

write('HeptaTrade/execution/allocation_plan_revalidator.cpp', r'''#include "allocation_plan_revalidator.h"
#include "../numeric/fixed_decimal.h"
#include <limits>
#include <map>
#include <set>

namespace
{
AllocationPlanRevalidationResult Reject(const char* code)
{ AllocationPlanRevalidationResult result; result.reasonCode = code; return result; }

bool CheckedSubtract(DecisionMicrounits left, DecisionMicrounits right,
                     DecisionMicrounits& out)
{
    if ((right < 0 && left > std::numeric_limits<DecisionMicrounits>::max() + right) ||
        (right > 0 && left < std::numeric_limits<DecisionMicrounits>::min() + right))
        return false;
    out = left - right; return true;
}

bool SolverEvidenceValid(const AllocationSolverResult& solver)
{
    DecisionMicrounits expectedGap = 0;
    if (solver.digest.empty() || GlobalAllocator::SolverDigest(solver) != solver.digest ||
        solver.primalBound != solver.objective || solver.upperBound < solver.objective ||
        !CheckedSubtract(solver.upperBound, solver.objective, expectedGap) ||
        solver.absoluteGap != expectedGap) return false;
    if (solver.exact)
        return solver.status == "optimal" && solver.absoluteGap == 0 &&
            solver.upperBound == solver.objective;
    return solver.status == "feasible_not_proven";
}
}

const char* AllocationPlanRevalidator::Version()
{ return "allocation-plan-revalidator-v2"; }

AllocationPlanRevalidationResult AllocationPlanRevalidator::ValidateShadow(
    const GlobalDecisionReceipt& receipt,
    const AllocationExecutionContext& context,
    std::uint64_t nowMs,
    const AuthoritativePortfolioInput& authoritative,
    const PortfolioCapitalPolicy& policy)
{
    if (!receipt.IsValid()) return Reject("ALLOCATION_PLAN_PROVENANCE_INVALID");
    const AllocationPlan& plan = receipt.Plan();
    if (plan.planId.empty() || plan.allocatorEpoch == 0 || plan.planDigest.empty() ||
        GlobalAllocator::PlanDigest(plan) != plan.planDigest)
        return Reject("ALLOCATION_PLAN_INTEGRITY_INVALID");
    if (!SolverEvidenceValid(plan.solver))
        return Reject("ALLOCATION_SOLVER_EVIDENCE_INVALID");
    if (context.allocatorEpoch != plan.allocatorEpoch ||
        context.capitalPool != plan.capitalPool ||
        context.accountBook != plan.accountBook ||
        context.policyRevision != plan.policyRevision ||
        context.proposalSetDigest != plan.proposalSetDigest)
        return Reject("ALLOCATION_EXECUTION_CONTEXT_MISMATCH");
    if (context.authoritativeSnapshotDigest.empty() ||
        context.authoritativeSnapshotDigest != plan.snapshotDigest)
        return Reject("ALLOCATION_PLAN_SNAPSHOT_MISMATCH");
    if (plan.createdAtMs == 0 || plan.validUntilMs <= plan.createdAtMs ||
        plan.validUntilMs != plan.proposalValidUntilMs ||
        plan.validUntilMs > plan.snapshotValidUntilMs ||
        context.authoritativeSnapshotValidUntilMs < plan.validUntilMs ||
        nowMs < plan.createdAtMs || nowMs >= plan.validUntilMs ||
        nowMs >= context.authoritativeSnapshotValidUntilMs)
        return Reject("ALLOCATION_PLAN_NOT_CURRENT");
    if (!authoritative.complete || authoritative.generation == 0)
        return Reject("ALLOCATION_AUTHORITATIVE_SNAPSHOT_INCOMPLETE");

    std::set<std::string> instruments;
    std::map<std::string, DecisionMicrounits> expected;
    std::vector<StrategyTargetIntent> intents;
    for (std::size_t i = 0; i < plan.targets.size(); ++i)
    {
        const AllocationTarget& target = plan.targets[i];
        if (target.instrument.empty() || !instruments.insert(target.instrument).second ||
            target.targetPosition < -HeptaFixedDecimal::kMaximumRaw ||
            target.targetPosition > HeptaFixedDecimal::kMaximumRaw)
            return Reject("ALLOCATION_PLAN_TARGET_INVALID");
        if (i > 0 && plan.targets[i - 1].instrument >= target.instrument)
            return Reject("ALLOCATION_PLAN_TARGET_ORDER_INVALID");
        StrategyTargetIntent intent;
        intent.strategyId = "global-allocation";
        intent.instrument = target.instrument;
        intent.targetPosition = target.targetPosition;
        intent.snapshotGeneration = authoritative.generation;
        intents.push_back(intent); expected[target.instrument] = target.targetPosition;
    }
    const PortfolioCompileResult compiled = PortfolioCompiler::Compile(intents, authoritative, policy);
    if (!compiled.accepted)
    {
        AllocationPlanRevalidationResult rejected = Reject("ALLOCATION_EXECUTION_REVALIDATION_REJECTED");
        rejected.compiled = compiled; return rejected;
    }
    if (compiled.netTargets != expected)
    {
        AllocationPlanRevalidationResult rejected = Reject("ALLOCATION_EXECUTION_TARGET_MISMATCH");
        rejected.compiled = compiled; return rejected;
    }
    AllocationPlanRevalidationResult result;
    result.accepted = true; result.reasonCode = "ALLOCATION_PLAN_REVALIDATED_SHADOW";
    result.compiled = compiled; return result;
}
''')

replace('HeptaTrade/simulator/multi_agent_allocation.cpp',
'''        selected, expected, nowMs);''',
'''        selected, expected, nowMs, planValidUntilMs);''')
replace('HeptaTrade/simulator/multi_agent_allocation.cpp',
'''        result.proposalSet, allocationPolicy, allocatorEpoch,
        nowMs, planValidUntilMs);''',
'''        result.proposalSet, allocationPolicy, allocatorEpoch, nowMs);''')
replace('HeptaTrade/simulator/multi_agent_allocation.cpp',
'''    result.plan = allocation.plan;
    result.revalidation = AllocationPlanRevalidator::ValidateShadow(
        result.plan, authoritativeSnapshotDigest, nowMs,
        authoritative, executionPolicy);''',
'''    result.plan = allocation.plan;
    AllocationExecutionContext context;
    context.allocatorEpoch = allocatorEpoch;
    context.capitalPool = result.plan.capitalPool;
    context.accountBook = result.plan.accountBook;
    context.policyRevision = allocationPolicy.policyRevision;
    context.proposalSetDigest = result.proposalSet.digest;
    context.authoritativeSnapshotDigest = authoritativeSnapshotDigest;
    context.authoritativeSnapshotValidUntilMs = planValidUntilMs;
    result.revalidation = AllocationPlanRevalidator::ValidateShadow(
        allocation.receipt, context, nowMs, authoritative, executionPolicy);''')

for path in ('tests/global_allocator_tests.cpp', 'tests/allocation_plan_revalidator_tests.cpp'):
    target = ROOT / path
    text = target.read_text(encoding='utf-8')
    text = text.replace('proposals, expected, 1500);', 'proposals, expected, 1500, 1800);')
    text = text.replace('policy.maximumGrossTarget =', 'policy.policyRevision = "policy-v1";\n    policy.maximumGrossTarget =')
    text = text.replace(', 1, 1500, 1800)', ', 1, 1500)')
    text = text.replace(', 0, 1500, 1800)', ', 0, 1500)')
    target.write_text(text, encoding='utf-8')

replace('tests/multi_agent_allocation_tests.cpp',
'''    GlobalAllocationPolicy policy;
    policy.maximumGrossTarget = 10000000;''',
'''    GlobalAllocationPolicy policy;
    policy.policyRevision = "policy-v1";
    policy.maximumGrossTarget = 10000000;''')

write('tests/allocation_plan_revalidator_tests.cpp', r'''#include "../HeptaTrade/execution/allocation_plan_revalidator.h"
#include <cassert>
#include <string>
#include <type_traits>
#include <vector>

namespace
{
std::string Digest(char value) { return std::string("sha256:") + std::string(64, value); }

GlobalAllocationResult Allocation()
{
    StrategyProposal proposal;
    proposal.proposalId = "proposal-alpha";
    proposal.moduleId = "hepta.strategy.alpha";
    proposal.moduleVersion = "1.0.0";
    proposal.sequence = 1;
    proposal.capitalPool = "pool-a";
    proposal.accountBook = "book-a";
    proposal.snapshotDigest = Digest('a');
    proposal.validFromMs = 1000;
    proposal.expiresAtMs = 2000;
    proposal.horizonMs = 500;
    StrategyProposalCandidate candidate;
    candidate.candidateId = "candidate-a";
    candidate.utility = 10;
    candidate.targets.push_back({"EUR.USD", 8000000});
    proposal.candidates.push_back(candidate);
    ProposalSetBuildResult set = ProposalSetBuilder::Build(
        std::vector<StrategyProposal>(1, proposal),
        std::vector<std::string>(1, "hepta.strategy.alpha"), 1500, 1800);
    assert(set.accepted);
    GlobalAllocationPolicy allocation;
    allocation.policyRevision = "policy-v1";
    allocation.maximumGrossTarget = 10000000;
    allocation.maximumInstruments = 4;
    allocation.maximumExactCombinations = 100;
    allocation.instrumentAbsoluteLimits["EUR.USD"] = 10000000;
    GlobalAllocationResult result = GlobalAllocator::Allocate(set.proposalSet, allocation, 1, 1500);
    assert(result.accepted && result.receipt.IsValid());
    return result;
}

AuthoritativePortfolioInput Authoritative()
{
    AuthoritativePortfolioInput input; input.complete = true; input.generation = 7;
    input.currentPositions["EUR.USD"] = 2000000; return input;
}

PortfolioCapitalPolicy ExecutionPolicy(DecisionMicrounits gross)
{
    PortfolioCapitalPolicy policy; policy.maximumGrossTarget = gross;
    policy.maximumStrategies = 1; policy.maximumInstruments = 4;
    StrategyCapitalBudget budget; budget.strategyId = "global-allocation";
    budget.maximumGrossTarget = gross; policy.strategyBudgets[budget.strategyId] = budget;
    return policy;
}

AllocationExecutionContext Context(const GlobalAllocationResult& allocation)
{
    AllocationExecutionContext context;
    context.allocatorEpoch = allocation.plan.allocatorEpoch;
    context.capitalPool = allocation.plan.capitalPool;
    context.accountBook = allocation.plan.accountBook;
    context.policyRevision = allocation.plan.policyRevision;
    context.proposalSetDigest = allocation.plan.proposalSetDigest;
    context.authoritativeSnapshotDigest = allocation.plan.snapshotDigest;
    context.authoritativeSnapshotValidUntilMs = allocation.plan.snapshotValidUntilMs;
    return context;
}

void TestSealedShadowRevalidation()
{
    static_assert(!std::is_constructible<GlobalDecisionReceipt, AllocationPlan>::value,
                  "Execution receipt must not be publicly forgeable");
    GlobalAllocationResult allocation = Allocation();
    AllocationPlanRevalidationResult result = AllocationPlanRevalidator::ValidateShadow(
        allocation.receipt, Context(allocation), 1600, Authoritative(), ExecutionPolicy(10000000));
    assert(result.accepted && result.compiled.deltas[0].delta == 6000000);

    GlobalDecisionReceipt forged;
    assert(AllocationPlanRevalidator::ValidateShadow(
        forged, Context(allocation), 1600, Authoritative(), ExecutionPolicy(10000000)).reasonCode ==
        "ALLOCATION_PLAN_PROVENANCE_INVALID");
}

void TestContextAndLifetimeBinding()
{
    GlobalAllocationResult allocation = Allocation();
    AllocationExecutionContext context = Context(allocation);
    context.allocatorEpoch++;
    assert(AllocationPlanRevalidator::ValidateShadow(
        allocation.receipt, context, 1600, Authoritative(), ExecutionPolicy(10000000)).reasonCode ==
        "ALLOCATION_EXECUTION_CONTEXT_MISMATCH");
    context = Context(allocation); context.policyRevision = "policy-v2";
    assert(AllocationPlanRevalidator::ValidateShadow(
        allocation.receipt, context, 1600, Authoritative(), ExecutionPolicy(10000000)).reasonCode ==
        "ALLOCATION_EXECUTION_CONTEXT_MISMATCH");
    context = Context(allocation); context.accountBook = "other-book";
    assert(AllocationPlanRevalidator::ValidateShadow(
        allocation.receipt, context, 1600, Authoritative(), ExecutionPolicy(10000000)).reasonCode ==
        "ALLOCATION_EXECUTION_CONTEXT_MISMATCH");
    context = Context(allocation); context.authoritativeSnapshotDigest = Digest('b');
    assert(AllocationPlanRevalidator::ValidateShadow(
        allocation.receipt, context, 1600, Authoritative(), ExecutionPolicy(10000000)).reasonCode ==
        "ALLOCATION_PLAN_SNAPSHOT_MISMATCH");
    context = Context(allocation); context.authoritativeSnapshotValidUntilMs = 1700;
    assert(AllocationPlanRevalidator::ValidateShadow(
        allocation.receipt, context, 1600, Authoritative(), ExecutionPolicy(10000000)).reasonCode ==
        "ALLOCATION_PLAN_NOT_CURRENT");
    assert(AllocationPlanRevalidator::ValidateShadow(
        allocation.receipt, Context(allocation), 1800, Authoritative(), ExecutionPolicy(10000000)).reasonCode ==
        "ALLOCATION_PLAN_NOT_CURRENT");
}

void TestExecutionBudgetAndSnapshotRejection()
{
    GlobalAllocationResult allocation = Allocation();
    AllocationPlanRevalidationResult budget = AllocationPlanRevalidator::ValidateShadow(
        allocation.receipt, Context(allocation), 1600, Authoritative(), ExecutionPolicy(5000000));
    assert(!budget.accepted && budget.compiled.reasonCode == "PORTFOLIO_STRATEGY_BUDGET_EXCEEDED");
    AuthoritativePortfolioInput incomplete = Authoritative(); incomplete.complete = false;
    assert(AllocationPlanRevalidator::ValidateShadow(
        allocation.receipt, Context(allocation), 1600, incomplete, ExecutionPolicy(10000000)).reasonCode ==
        "ALLOCATION_AUTHORITATIVE_SNAPSHOT_INCOMPLETE");
}
}

int main()
{
    TestSealedShadowRevalidation();
    TestContextAndLifetimeBinding();
    TestExecutionBudgetAndSnapshotRejection();
    return 0;
}
''')

# Add explicit lifetime and receipt checks to allocator tests.
path = ROOT / 'tests/global_allocator_tests.cpp'
text = path.read_text(encoding='utf-8')
text = text.replace('    assert(result.accepted);\n    assert(result.reasonCode == "ALLOCATION_OPTIMAL");',
'''    assert(result.accepted);
    assert(result.receipt.IsValid());
    assert(result.receipt.Plan().planDigest == result.plan.planDigest);
    assert(result.plan.validUntilMs == 1800);
    assert(result.reasonCode == "ALLOCATION_OPTIMAL");''', 1)
text = text.replace('    assert(GlobalAllocator::Allocate(Set(), Policy(100), 0, 1500)\n               .reasonCode == "ALLOCATION_TIME_ENVELOPE_INVALID");',
'''    assert(GlobalAllocator::Allocate(Set(), Policy(100), 0, 1500)
               .reasonCode == "ALLOCATION_TIME_ENVELOPE_INVALID");
    ProposalSet expired = Set();
    expired.validUntilMs = 1500;
    expired.digest = ProposalSetBuilder::Digest(expired);
    assert(GlobalAllocator::Allocate(expired, Policy(100), 1, 1500)
               .reasonCode == "ALLOCATION_TIME_ENVELOPE_INVALID");''')
path.write_text(text, encoding='utf-8')
