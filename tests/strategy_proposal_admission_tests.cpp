#include "strategy_runtime/strategy_proposal.h"
#include <algorithm>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <new>

namespace allocation_probe {
thread_local bool measure = false;
thread_local std::size_t maximum = 0, total = 0;
thread_local long failAt = -1, calls = 0;
}
__attribute__((noinline)) void* operator new(std::size_t n) {
    if (allocation_probe::measure) {
        allocation_probe::maximum = std::max(allocation_probe::maximum, n);
        allocation_probe::total += n;
    }
    if (allocation_probe::failAt >= 0 && allocation_probe::calls++ == allocation_probe::failAt)
        throw std::bad_alloc();
    void* p = std::malloc(n ? n : 1); if (!p) throw std::bad_alloc(); return p;
}
__attribute__((noinline)) void* operator new[](std::size_t n) { return ::operator new(n); }
__attribute__((noinline)) void operator delete(void* p) noexcept { std::free(p); }
__attribute__((noinline)) void operator delete[](void* p) noexcept { std::free(p); }
#if defined(__cpp_sized_deallocation)
__attribute__((noinline)) void operator delete(void* p, std::size_t) noexcept { std::free(p); }
__attribute__((noinline)) void operator delete[](void* p, std::size_t) noexcept { std::free(p); }
#endif
namespace {
unsigned assertions = 0;
void Check(bool ok, const char* expression, int line) {
    ++assertions;
    if (!ok) { std::cerr << "failed " << line << ": " << expression << '\n'; std::abort(); }
}
#define REQUIRE(x) Check(static_cast<bool>(x), #x, __LINE__)
constexpr DecisionMicrounits Maximum = 9000000000000000LL;
StrategyProposal Proposal() {
    StrategyProposal p;
    p.proposalId = "proposal-a"; p.moduleId = "hepta.strategy.admission"; p.moduleVersion = "1.0.0";
    p.sequence = 1; p.capitalPool = "pool"; p.accountBook = "book";
    p.snapshotDigest = "sha256:" + std::string(64, 'a');
    p.validFromMs = 1000; p.expiresAtMs = 2000; p.horizonMs = 500;
    StrategyProposalCandidate c; c.candidateId = "candidate-a"; c.utility = 1;
    c.targets.push_back({"EUR.USD", 1000000}); p.candidates.push_back(c); return p;
}
void Rejects(const StrategyProposalSealResult& r) {
    REQUIRE(!r.accepted && r.proposal.proposalDigest.empty() && r.proposal.candidates.empty());
}
StrategyProposal Oversized(int mode) {
    auto p = Proposal();
    switch (mode) {
    case 0: p.candidates[0].candidateId = std::string(2u << 20, 'a'); break;
    case 1: p.candidates[0].targets[0].instrument = std::string(2u << 20, 'A'); break;
    case 2: p.candidates[0].targets.resize(4096, p.candidates[0].targets[0]); break;
    case 3:
        p.candidates.resize(17, p.candidates[0]);
        for (unsigned i = 0; i < p.candidates.size(); ++i) {
            auto& c = p.candidates[i]; c.candidateId = "c" + std::to_string(i);
            c.targets.clear();
            for (unsigned j = 0; j < (i == 16 ? 1u : 256u); ++j)
                c.targets.push_back({"S" + std::to_string(j), 1});
        }
        break;
    case 4: p.proposalDigest = std::string(2u << 20, 'f'); break;
    case 5: p.candidates[0].utility = Maximum + 1; break;
    case 6: p.candidates[0].targets[0].targetPosition = -Maximum - 1; break;
    }
    return p;
}
std::size_t MeasureRejected(int mode, bool requireBounded) {
    const auto p = Oversized(mode);
    allocation_probe::maximum = allocation_probe::total = 0;
    allocation_probe::measure = true;
    const auto r = StrategyProposalContract::ValidateAndSeal(p, 1500);
    allocation_probe::measure = false;
    Rejects(r);
    const auto largest = allocation_probe::maximum, total = allocation_probe::total;
    if (requireBounded) REQUIRE(largest < 8192 && total < 8192);
    std::cout << "proposal_invalid_mode=" << mode << " maximum_allocation=" << largest
              << " total_allocated=" << total << " reason=" << r.reasonCode << '\n';
    return largest;
}
void TestOversizedBodiesAreRejectedBeforeCopy() {
    for (int mode = 0; mode < 7; ++mode) MeasureRejected(mode, true);
}
void TestHalfOpenLifetimeAndUnsignedBoundaries() {
    auto p = Proposal();
    REQUIRE(StrategyProposalContract::ValidateAndSeal(p, 1000).accepted);
    REQUIRE(StrategyProposalContract::ValidateAndSeal(p, 1999).accepted);
    for (std::uint64_t now : {0u, 999u, 2000u, 2001u}) {
        const auto r = StrategyProposalContract::ValidateAndSeal(p, now);
        Rejects(r); REQUIRE(r.reasonCode == "PROPOSAL_NOT_CURRENT");
    }
    const auto maximum = std::numeric_limits<std::uint64_t>::max();
    p.validFromMs = maximum - 1000; p.expiresAtMs = maximum; p.horizonMs = 1000;
    REQUIRE(StrategyProposalContract::ValidateAndSeal(p, maximum - 1).accepted);
    Rejects(StrategyProposalContract::ValidateAndSeal(p, maximum));
}
void TestExactBodyBoundsAndCanonicalization() {
    auto p = Proposal(); p.candidates.clear();
    for (unsigned i = 0; i < 16; ++i) {
        StrategyProposalCandidate c;
        const auto suffix = std::to_string(i);
        c.candidateId = std::string(128 - suffix.size(), 'c') + suffix;
        c.utility = i % 2 ? Maximum : -Maximum;
        for (unsigned j = 0; j < 256; ++j) {
            const auto index = std::to_string(j);
            c.targets.push_back({std::string(128 - index.size(), 'S') + index, j % 2 ? Maximum : -Maximum});
        }
        p.candidates.push_back(c);
    }
    const auto sealed = StrategyProposalContract::ValidateAndSeal(p, 1500);
    REQUIRE(sealed.accepted && sealed.proposal.candidates.size() == 16);
    std::reverse(p.candidates.begin(), p.candidates.end());
    for (auto& c : p.candidates) std::reverse(c.targets.begin(), c.targets.end());
    p.proposalDigest = sealed.proposal.proposalDigest;
    const auto repeated = StrategyProposalContract::ValidateAndSeal(p, 1500);
    REQUIRE(repeated.accepted && repeated.proposal.proposalDigest == p.proposalDigest);
    auto candidate = Proposal().candidates[0]; candidate.candidateId = "overflow";
    p.candidates.push_back(candidate); p.proposalDigest.clear();
    Rejects(StrategyProposalContract::ValidateAndSeal(p, 1500));
    p = Proposal(); p.candidates.resize(256, p.candidates[0]);
    for (unsigned i = 0; i < 256; ++i) p.candidates[i].candidateId = "c" + std::to_string(i);
    REQUIRE(StrategyProposalContract::ValidateAndSeal(p, 1500).accepted);
    p.candidates.push_back(candidate); Rejects(StrategyProposalContract::ValidateAndSeal(p, 1500));
}
void TestDuplicatesAndDigestsStillFailClosed() {
    auto p = Proposal(); p.candidates.push_back(p.candidates[0]);
    REQUIRE(StrategyProposalContract::ValidateAndSeal(p, 1500).reasonCode == "PROPOSAL_CANDIDATE_INVALID");
    p = Proposal(); p.candidates[0].targets.push_back(p.candidates[0].targets[0]);
    REQUIRE(StrategyProposalContract::ValidateAndSeal(p, 1500).reasonCode == "PROPOSAL_TARGET_INVALID");
    p = Proposal(); p.proposalDigest = "sha256:" + std::string(64, 'f');
    REQUIRE(StrategyProposalContract::ValidateAndSeal(p, 1500).reasonCode == "PROPOSAL_DIGEST_MISMATCH");
    p.proposalDigest = "not-canonical";
    REQUIRE(StrategyProposalContract::ValidateAndSeal(p, 1500).reasonCode == "PROPOSAL_DIGEST_MISMATCH");
}
void TestAllocationFailuresDoNotMutateInput() {
    const auto p = Proposal(); const auto before = StrategyProposalContract::Digest(p);
    bool completed = false; unsigned failures = 0;
    for (long ordinal = 0; ordinal < 128; ++ordinal) {
        StrategyProposalSealResult r; bool threw = false;
        allocation_probe::calls = 0; allocation_probe::failAt = ordinal;
        try { r = StrategyProposalContract::ValidateAndSeal(p, 1500); }
        catch (const std::bad_alloc&) { threw = true; }
        allocation_probe::failAt = -1;
        REQUIRE(StrategyProposalContract::Digest(p) == before && p.proposalDigest.empty());
        if (threw) { ++failures; Rejects(r); }
        else { REQUIRE(r.accepted); completed = true; break; }
    }
    REQUIRE(completed && failures > 0);
    std::cout << "proposal_admission_allocation_failures=" << failures << '\n';
}
int Probe() {
    unsigned large = 0;
    for (int mode = 0; mode < 7; ++mode) large += MeasureRejected(mode, false) >= 8192 ? 1u : 0u;
    const auto expiry = StrategyProposalContract::ValidateAndSeal(Proposal(), 2000);
    std::cout << "proposal_accepted_at_expiry=" << expiry.accepted << '\n';
    return large || expiry.accepted ? 1 : 0;
}
}
int main(int argc, char** argv) {
    if (argc == 2 && std::string(argv[1]) == "--admission-probe") return Probe();
    TestOversizedBodiesAreRejectedBeforeCopy(); TestHalfOpenLifetimeAndUnsignedBoundaries();
    TestExactBodyBoundsAndCanonicalization(); TestDuplicatesAndDigestsStillFailClosed();
    TestAllocationFailuresDoNotMutateInput();
    std::cout << "proposal_admission_assertions=" << assertions << '\n';
}
