#include "strategy_proposal.h"

#include "../numeric/fixed_decimal.h"

#include <algorithm>
#include <iomanip>
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

bool CanonicalDigest(const std::string& value)
{
    if (value.size() != 71u || value.compare(0, 7, "sha256:") != 0)
        return false;
    for (std::size_t i = 7; i < value.size(); ++i)
    {
        const char c = value[i];
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')))
            return false;
    }
    return true;
}

bool InNumericRange(DecisionMicrounits value)
{
    return value >= -HeptaFixedDecimal::kMaximumRaw &&
        value <= HeptaFixedDecimal::kMaximumRaw;
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

StrategyProposalSealResult Reject(const char* code)
{
    StrategyProposalSealResult result;
    result.reasonCode = code;
    return result;
}
}

const char* StrategyProposalContract::Version()
{
    return "hepta.strategy-proposal.v1";
}

std::string StrategyProposalContract::Digest(const StrategyProposal& proposal)
{
    std::string canonical;
    AppendField(canonical, "schema", Version());
    AppendField(canonical, "proposal_id", proposal.proposalId);
    AppendField(canonical, "module_id", proposal.moduleId);
    AppendField(canonical, "module_version", proposal.moduleVersion);
    AppendField(canonical, "sequence", std::to_string(proposal.sequence));
    AppendField(canonical, "capital_pool", proposal.capitalPool);
    AppendField(canonical, "account_book", proposal.accountBook);
    AppendField(canonical, "snapshot_digest", proposal.snapshotDigest);
    AppendField(canonical, "valid_from_ms",
                std::to_string(proposal.validFromMs));
    AppendField(canonical, "expires_at_ms",
                std::to_string(proposal.expiresAtMs));
    AppendField(canonical, "horizon_ms",
                std::to_string(proposal.horizonMs));
    for (std::size_t i = 0; i < proposal.candidates.size(); ++i)
    {
        const StrategyProposalCandidate& candidate = proposal.candidates[i];
        AppendField(canonical, "candidate_id", candidate.candidateId);
        AppendField(canonical, "utility", std::to_string(candidate.utility));
        for (std::size_t j = 0; j < candidate.targets.size(); ++j)
        {
            AppendField(canonical, "instrument",
                        candidate.targets[j].instrument);
            AppendField(canonical, "target_position",
                        std::to_string(candidate.targets[j].targetPosition));
        }
    }
    return Sha256(canonical);
}

StrategyProposalSealResult StrategyProposalContract::ValidateAndSeal(
    const StrategyProposal& proposal,
    std::uint64_t nowMs)
{
    if (!CanonicalId(proposal.proposalId, 128u) ||
        !CanonicalId(proposal.moduleId, 128u) ||
        proposal.moduleId.compare(0, 6, "hepta.") != 0 ||
        !CanonicalId(proposal.moduleVersion, 64u) ||
        !CanonicalId(proposal.capitalPool, 128u) ||
        !CanonicalId(proposal.accountBook, 128u))
        return Reject("PROPOSAL_IDENTITY_INVALID");
    if (!CanonicalDigest(proposal.snapshotDigest))
        return Reject("PROPOSAL_SNAPSHOT_DIGEST_INVALID");
    if (proposal.sequence == 0 || proposal.validFromMs == 0 ||
        proposal.expiresAtMs <= proposal.validFromMs ||
        proposal.horizonMs == 0 ||
        proposal.horizonMs > proposal.expiresAtMs - proposal.validFromMs)
        return Reject("PROPOSAL_TIME_ENVELOPE_INVALID");
    if (nowMs < proposal.validFromMs || nowMs >= proposal.expiresAtMs)
        return Reject("PROPOSAL_NOT_CURRENT");
    if (proposal.candidates.empty() || proposal.candidates.size() > 256u)
        return Reject("PROPOSAL_CANDIDATE_COUNT_INVALID");

    // Bound the current nested body before copying, sorting or allocating
    // duplicate sets. Caller-provided values must not amplify a rejected
    // proposal into an unbounded normalization allocation.
    if (!proposal.proposalDigest.empty() && !CanonicalDigest(proposal.proposalDigest))
        return Reject("PROPOSAL_DIGEST_MISMATCH");
    std::size_t totalTargets = 0;
    for (const StrategyProposalCandidate& candidate : proposal.candidates)
    {
        if (!CanonicalId(candidate.candidateId, 128u) || !InNumericRange(candidate.utility))
            return Reject("PROPOSAL_CANDIDATE_INVALID");
        if (candidate.targets.empty() || candidate.targets.size() > 256u)
            return Reject("PROPOSAL_TARGET_COUNT_INVALID");
        if (candidate.targets.size() > 4096u - totalTargets)
            return Reject("PROPOSAL_TOTAL_TARGET_COUNT_LIMIT");
        totalTargets += candidate.targets.size();
        for (const StrategyCandidateTarget& target : candidate.targets)
            if (!CanonicalId(target.instrument, 128u) || !InNumericRange(target.targetPosition))
                return Reject("PROPOSAL_TARGET_INVALID");
    }

    StrategyProposal normalized = proposal;
    std::sort(normalized.candidates.begin(), normalized.candidates.end(),
        [](const StrategyProposalCandidate& left,
           const StrategyProposalCandidate& right) {
            return left.candidateId < right.candidateId;
        });
    std::set<std::string> candidateIds;
    std::size_t targetCount = 0;
    for (std::size_t i = 0; i < normalized.candidates.size(); ++i)
    {
        StrategyProposalCandidate& candidate = normalized.candidates[i];
        if (!CanonicalId(candidate.candidateId, 128u) ||
            !candidateIds.insert(candidate.candidateId).second ||
            !InNumericRange(candidate.utility))
            return Reject("PROPOSAL_CANDIDATE_INVALID");
        if (candidate.targets.empty() || candidate.targets.size() > 256u)
            return Reject("PROPOSAL_TARGET_COUNT_INVALID");
        targetCount += candidate.targets.size();
        if (targetCount > 4096u)
            return Reject("PROPOSAL_TOTAL_TARGET_COUNT_LIMIT");
        std::sort(candidate.targets.begin(), candidate.targets.end(),
            [](const StrategyCandidateTarget& left,
               const StrategyCandidateTarget& right) {
                return left.instrument < right.instrument;
            });
        std::set<std::string> instruments;
        for (std::size_t j = 0; j < candidate.targets.size(); ++j)
        {
            const StrategyCandidateTarget& target = candidate.targets[j];
            if (!CanonicalId(target.instrument, 128u) ||
                !instruments.insert(target.instrument).second ||
                !InNumericRange(target.targetPosition))
                return Reject("PROPOSAL_TARGET_INVALID");
        }
    }
    normalized.proposalDigest.clear();
    const std::string digest = Digest(normalized);
    if (digest.empty()) return Reject("PROPOSAL_DIGEST_FAILED");
    if (!proposal.proposalDigest.empty() && proposal.proposalDigest != digest)
        return Reject("PROPOSAL_DIGEST_MISMATCH");
    normalized.proposalDigest = digest;
    StrategyProposalSealResult result;
    result.accepted = true;
    result.reasonCode = "PROPOSAL_ACCEPTED";
    result.proposal = normalized;
    return result;
}
