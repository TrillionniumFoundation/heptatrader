#include "../HeptaTrade/proposal/proposal_set.h"

#include <algorithm>
#include <cassert>
#include <string>
#include <vector>

namespace
{
std::string Digest(char value)
{
    return std::string("sha256:") + std::string(64, value);
}

StrategyProposal Proposal(const std::string& module,
                          const std::string& proposalId)
{
    StrategyProposal proposal;
    proposal.proposalId = proposalId;
    proposal.moduleId = module;
    proposal.moduleVersion = "1.0.0";
    proposal.sequence = 1;
    proposal.capitalPool = "pool-a";
    proposal.accountBook = "book-a";
    proposal.snapshotDigest = Digest('a');
    proposal.validFromMs = 1000;
    proposal.expiresAtMs = 2000;
    proposal.horizonMs = 500;

    StrategyProposalCandidate second;
    second.candidateId = "candidate-b";
    second.utility = 20;
    second.targets.push_back({"USD.JPY", -1000000});
    second.targets.push_back({"EUR.USD", 2000000});
    StrategyProposalCandidate first;
    first.candidateId = "candidate-a";
    first.utility = 10;
    first.targets.push_back({"EUR.USD", 1000000});
    proposal.candidates.push_back(second);
    proposal.candidates.push_back(first);
    return proposal;
}

void TestSealAndCanonicalization()
{
    StrategyProposal input = Proposal("hepta.strategy.alpha", "proposal-a");
    StrategyProposalSealResult sealed =
        StrategyProposalContract::ValidateAndSeal(input, 1500);
    assert(sealed.accepted);
    assert(sealed.reasonCode == "PROPOSAL_ACCEPTED");
    assert(sealed.proposal.candidates[0].candidateId == "candidate-a");
    assert(sealed.proposal.candidates[1].targets[0].instrument == "EUR.USD");
    assert(!sealed.proposal.proposalDigest.empty());

    std::reverse(input.candidates.begin(), input.candidates.end());
    StrategyProposalSealResult repeated =
        StrategyProposalContract::ValidateAndSeal(input, 1500);
    assert(repeated.accepted);
    assert(repeated.proposal.proposalDigest == sealed.proposal.proposalDigest);

    input.proposalDigest = Digest('f');
    assert(StrategyProposalContract::ValidateAndSeal(input, 1500).reasonCode ==
           "PROPOSAL_DIGEST_MISMATCH");
}

void TestProposalNegativeCases()
{
    StrategyProposal proposal = Proposal("bad module", "proposal-a");
    assert(StrategyProposalContract::ValidateAndSeal(proposal, 1500).reasonCode ==
           "PROPOSAL_IDENTITY_INVALID");
    proposal = Proposal("hepta.strategy.alpha", "proposal-a");
    proposal.snapshotDigest = "bad";
    assert(StrategyProposalContract::ValidateAndSeal(proposal, 1500).reasonCode ==
           "PROPOSAL_SNAPSHOT_DIGEST_INVALID");
    proposal = Proposal("hepta.strategy.alpha", "proposal-a");
    assert(StrategyProposalContract::ValidateAndSeal(proposal, 2500).reasonCode ==
           "PROPOSAL_NOT_CURRENT");
    proposal = Proposal("hepta.strategy.alpha", "proposal-a");
    proposal.candidates.push_back(proposal.candidates[0]);
    assert(StrategyProposalContract::ValidateAndSeal(proposal, 1500).reasonCode ==
           "PROPOSAL_CANDIDATE_INVALID");
    proposal = Proposal("hepta.strategy.alpha", "proposal-a");
    proposal.candidates[0].targets.push_back(
        proposal.candidates[0].targets[0]);
    assert(StrategyProposalContract::ValidateAndSeal(proposal, 1500).reasonCode ==
           "PROPOSAL_TARGET_INVALID");
}

void TestProposalSetCompleteness()
{
    StrategyProposal alpha = Proposal("hepta.strategy.alpha", "proposal-a");
    StrategyProposal beta = Proposal("hepta.strategy.beta", "proposal-b");
    std::vector<StrategyProposal> proposals;
    proposals.push_back(beta);
    proposals.push_back(alpha);
    std::vector<std::string> expected;
    expected.push_back("hepta.strategy.alpha");
    expected.push_back("hepta.strategy.beta");
    ProposalSetBuildResult set = ProposalSetBuilder::Build(
        proposals, expected, 1500, 1800);
    assert(set.accepted);
    assert(set.proposalSet.proposals[0].moduleId == "hepta.strategy.alpha");
    assert(!set.proposalSet.digest.empty());

    proposals.pop_back();
    assert(ProposalSetBuilder::Build(proposals, expected, 1500, 1800).reasonCode ==
           "PROPOSAL_SET_INCOMPLETE");
    proposals.push_back(beta);
    assert(ProposalSetBuilder::Build(proposals, expected, 1500, 1800).reasonCode ==
           "PROPOSAL_SET_DUPLICATE_MODULE");

    proposals.clear();
    alpha = Proposal("hepta.strategy.alpha", "proposal-a");
    beta = Proposal("hepta.strategy.beta", "proposal-b");
    beta.snapshotDigest = Digest('b');
    proposals.push_back(alpha);
    proposals.push_back(beta);
    assert(ProposalSetBuilder::Build(proposals, expected, 1500, 1800).reasonCode ==
           "PROPOSAL_SET_SNAPSHOT_MISMATCH");
}
}

int main()
{
    TestSealAndCanonicalization();
    TestProposalNegativeCases();
    TestProposalSetCompleteness();
    return 0;
}
