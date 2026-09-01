#include "proposal_set.h"

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
