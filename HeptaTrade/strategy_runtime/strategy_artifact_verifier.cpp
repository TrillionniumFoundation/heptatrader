#include "strategy_artifact_verifier.h"
#include "strategy_artifact_files.h"

#include <algorithm>
#include <openssl/evp.h>
#include <stdexcept>
#include <utility>

struct VerifiedStrategyArtifact::Data
{
    SignedStrategyArtifact manifest;
    std::string artifact, config, model, policyDigest;
    std::uint64_t verifiedAtMs = 0;
};

#define HEPTA_ARTIFACT_ACCESSOR(method, type, member) \
    const type& VerifiedStrategyArtifact::method() const { \
        if (!m_data) throw std::logic_error("invalid verified strategy artifact"); \
        return m_data->member; }
HEPTA_ARTIFACT_ACCESSOR(Descriptor, StrategyArtifactDescriptor, manifest.descriptor)
HEPTA_ARTIFACT_ACCESSOR(ArtifactBytes, std::string, artifact)
HEPTA_ARTIFACT_ACCESSOR(ConfigBytes, std::string, config)
HEPTA_ARTIFACT_ACCESSOR(ModelBytes, std::string, model)
HEPTA_ARTIFACT_ACCESSOR(PolicyDigest, std::string, policyDigest)
#undef HEPTA_ARTIFACT_ACCESSOR
std::uint64_t VerifiedStrategyArtifact::ReleaseSequence() const noexcept
{ return m_data ? m_data->manifest.releaseSequence : 0; }
std::uint64_t VerifiedStrategyArtifact::VerifiedAtMs() const noexcept
{ return m_data ? m_data->verifiedAtMs : 0; }
std::uint64_t VerifiedStrategyArtifact::ExpiresAtMs() const noexcept
{ return m_data ? m_data->manifest.expiresAtMs : 0; }

namespace
{
constexpr std::size_t kMaximumBytes = 64u << 20;
bool Id(const std::string& s, std::size_t maximum)
{
    if (s.empty() || s.size() > maximum) return false;
    for (unsigned char c : s)
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
              (c >= '0' && c <= '9') || c == '.' || c == ':' || c == '-' || c == '_')) return false;
    return true;
}
bool Digest(const std::string& s)
{
    if (s.size() != 71 || s.compare(0, 7, "sha256:") != 0) return false;
    for (std::size_t i = 7; i < s.size(); ++i)
        if (!((s[i] >= '0' && s[i] <= '9') || (s[i] >= 'a' && s[i] <= 'f'))) return false;
    return true;
}
bool ValidDescriptor(const StrategyArtifactDescriptor& d)
{
    return Id(d.moduleId, 128) && d.moduleId.compare(0, 6, "hepta.") == 0 && Id(d.version, 64) &&
        Digest(d.artifactDigest) && Digest(d.configDigest) && (d.modelDigest.empty() || Digest(d.modelDigest)) &&
        d.budget.maxThreads > 0 && d.budget.maxThreads <= 64 &&
        d.budget.maxFileDescriptors > 0 && d.budget.maxFileDescriptors <= 4096 &&
        d.budget.maxMemoryBytes > 0 && d.budget.maxMemoryBytes <= (16ULL << 30) &&
        d.budget.maxCheckpointBytes > 0 && d.budget.maxCheckpointBytes <= d.budget.maxMemoryBytes;
}
bool ValidPolicy(const StrategyArtifactTrustPolicy& p)
{
    return Id(p.revision, 128) && Id(p.audience, 128) && Id(p.moduleId, 128) &&
        p.moduleId.compare(0, 6, "hepta.") == 0 && Id(p.keyId, 128) && p.ed25519PublicKey.size() == 32 &&
        p.notBeforeMs > 0 && p.notAfterMs > p.notBeforeMs && p.minimumReleaseSequence > 0 &&
        p.maximumLifetimeMs > 0 && p.maximumLifetimeMs <= 86400000 &&
        p.maximumBundleBytes > 0 && p.maximumBundleBytes <= kMaximumBytes &&
        p.maximumArtifactBytes > 0 && p.maximumArtifactBytes <= p.maximumBundleBytes &&
        p.maximumConfigBytes > 0 && p.maximumConfigBytes <= p.maximumBundleBytes &&
        p.maximumModelBytes <= p.maximumBundleBytes;
}
bool ValidManifest(const SignedStrategyArtifact& m)
{
    return ValidDescriptor(m.descriptor) && Id(m.policyRevision, 128) && Id(m.audience, 128) &&
        Id(m.keyId, 128) && m.releaseSequence > 0 && m.issuedAtMs > 0 && m.expiresAtMs > m.issuedAtMs;
}
void U64(std::string& out, std::uint64_t n)
{ for (int shift = 56; shift >= 0; shift -= 8) out.push_back(static_cast<char>((n >> shift) & 255u)); }
void Field(std::string& out, const std::string& s) { U64(out, s.size()); out.append(s); }
std::string Hash(const std::string& bytes)
{
    unsigned char digest[EVP_MAX_MD_SIZE]; unsigned int size = 0;
    if (EVP_Digest(bytes.data(), bytes.size(), digest, &size, EVP_sha256(), nullptr) != 1 || size != 32)
        return std::string();
    const char* hex = "0123456789abcdef";
    std::string out = "sha256:";
    for (unsigned int i = 0; i < size; ++i) { out.push_back(hex[digest[i] >> 4]); out.push_back(hex[digest[i] & 15]); }
    return out;
}
std::string PolicyHash(const StrategyArtifactTrustPolicy& p)
{
    if (!ValidPolicy(p)) return std::string();
    std::string out = "HEPTA_STRATEGY_ARTIFACT_POLICY_V1\n";
    Field(out, p.revision); Field(out, p.audience); Field(out, p.moduleId); Field(out, p.keyId);
    Field(out, p.ed25519PublicKey); U64(out, p.revoked ? 1 : 0);
    U64(out, p.notBeforeMs); U64(out, p.notAfterMs); U64(out, p.minimumReleaseSequence);
    U64(out, p.maximumLifetimeMs); U64(out, p.maximumArtifactBytes); U64(out, p.maximumConfigBytes);
    U64(out, p.maximumModelBytes); U64(out, p.maximumBundleBytes);
    return Hash(out);
}
bool VerifySignature(const std::string& key, const std::string& signature, const std::string& message)
{
    using Key = std::unique_ptr<EVP_PKEY, decltype(&EVP_PKEY_free)>;
    using Context = std::unique_ptr<EVP_MD_CTX, decltype(&EVP_MD_CTX_free)>;
    Key publicKey(EVP_PKEY_new_raw_public_key(EVP_PKEY_ED25519, nullptr,
        reinterpret_cast<const unsigned char*>(key.data()), key.size()), EVP_PKEY_free);
    Context context(EVP_MD_CTX_new(), EVP_MD_CTX_free);
    // Pure Ed25519 is a one-shot operation; no digest algorithm is supplied.
    return publicKey && context && EVP_DigestVerifyInit(context.get(), nullptr, nullptr, nullptr, publicKey.get()) == 1 &&
        EVP_DigestVerify(context.get(), reinterpret_cast<const unsigned char*>(signature.data()), signature.size(),
                         reinterpret_cast<const unsigned char*>(message.data()), message.size()) == 1;
}
StrategyArtifactVerificationResult Reject(const char* code)
{ StrategyArtifactVerificationResult result; result.reasonCode = code; return result; }
}

StrategyArtifactVerifier::StrategyArtifactVerifier(StrategyArtifactTrustPolicy policy)
    : m_policy(std::move(policy)), m_policyDigest(PolicyHash(m_policy)) {}

std::string StrategyArtifactVerifier::SigningMessage(const SignedStrategyArtifact& m)
{
    if (!ValidManifest(m)) return std::string();
    std::string out = "HEPTA_STRATEGY_ARTIFACT_AUTHORIZATION_V1\n";
    Field(out, m.policyRevision); Field(out, m.audience); Field(out, m.keyId);
    const auto& d = m.descriptor;
    Field(out, d.moduleId); Field(out, d.version); Field(out, d.artifactDigest);
    Field(out, d.configDigest); Field(out, d.modelDigest);
    U64(out, d.budget.maxThreads); U64(out, d.budget.maxFileDescriptors);
    U64(out, d.budget.maxMemoryBytes); U64(out, d.budget.maxCheckpointBytes);
    U64(out, m.releaseSequence); U64(out, m.issuedAtMs); U64(out, m.expiresAtMs);
    return out;
}

bool StrategyArtifactVerifier::Authorizes(const VerifiedStrategyArtifact& artifact,
                                          std::uint64_t now) const noexcept
{
    return !m_policyDigest.empty() && !m_policy.revoked && artifact.m_data &&
        artifact.m_data->policyDigest == m_policyDigest && now >= artifact.m_data->verifiedAtMs &&
        now >= m_policy.notBeforeMs && now < m_policy.notAfterMs &&
        now >= artifact.m_data->manifest.issuedAtMs && now < artifact.m_data->manifest.expiresAtMs &&
        artifact.m_data->manifest.releaseSequence >= m_policy.minimumReleaseSequence;
}

StrategyArtifactVerificationResult StrategyArtifactVerifier::Load(const SignedStrategyArtifact& m,
    const StrategyArtifactPaths& paths, std::uint64_t now) const
{
    if (m_policyDigest.empty()) return Reject("STRATEGY_ARTIFACT_POLICY_INVALID");
    if (m_policy.revoked) return Reject("STRATEGY_ARTIFACT_KEY_REVOKED");
    if (!ValidManifest(m) || m.signature.size() != 64) return Reject("STRATEGY_ARTIFACT_MANIFEST_INVALID");
    if (m.policyRevision != m_policy.revision || m.audience != m_policy.audience ||
        m.keyId != m_policy.keyId || m.descriptor.moduleId != m_policy.moduleId)
        return Reject("STRATEGY_ARTIFACT_POLICY_MISMATCH");
    if (m.releaseSequence < m_policy.minimumReleaseSequence) return Reject("STRATEGY_ARTIFACT_RELEASE_STALE");
    if (now == 0 || now < m.issuedAtMs || now >= m.expiresAtMs ||
        m.issuedAtMs < m_policy.notBeforeMs || m.expiresAtMs > m_policy.notAfterMs ||
        m.expiresAtMs - m.issuedAtMs > m_policy.maximumLifetimeMs)
        return Reject("STRATEGY_ARTIFACT_TIME_INVALID");
    const std::string message = SigningMessage(m);
    if (message.empty() || !VerifySignature(m_policy.ed25519PublicKey, m.signature, message))
        return Reject("STRATEGY_ARTIFACT_SIGNATURE_INVALID");
#if !defined(__linux__)
    (void)paths;
    return Reject("STRATEGY_ARTIFACT_PLATFORM_UNSUPPORTED");
#else
    using namespace hepta_artifact_detail;
    const bool model = !m.descriptor.modelDigest.empty();
    if (!Leaf(paths.artifact) || !Leaf(paths.config) || paths.artifact == paths.config ||
        (model ? (!Leaf(paths.model) || paths.model == paths.artifact || paths.model == paths.config) : !paths.model.empty()))
        return Reject("STRATEGY_ARTIFACT_PATH_INVALID");
    if (model && m_policy.maximumModelBytes == 0) return Reject("STRATEGY_ARTIFACT_MODEL_FORBIDDEN");
    Files files;
    if (const char* error = files.Open(paths.directory)) return Reject(error);
    auto data = std::make_shared<VerifiedStrategyArtifact::Data>();
    data->manifest = m; data->policyDigest = m_policyDigest; data->verifiedAtMs = now;
    std::uint64_t remaining = std::min<std::uint64_t>(m_policy.maximumBundleBytes, m.descriptor.budget.maxMemoryBytes);
    const auto read = [&](const std::string& name, std::size_t limit, const std::string& expected, std::string& bytes) -> const char* {
        if (remaining == 0) return "STRATEGY_ARTIFACT_BUNDLE_LIMIT";
        if (const char* error = files.Read(name, static_cast<std::size_t>(std::min<std::uint64_t>(limit, remaining)), bytes)) return error;
        remaining -= bytes.size();
        if (Hash(bytes) != expected) return "STRATEGY_ARTIFACT_DIGEST_MISMATCH";
        return nullptr;
    };
    if (const char* error = read(paths.artifact, m_policy.maximumArtifactBytes, m.descriptor.artifactDigest, data->artifact)) return Reject(error);
    if (const char* error = read(paths.config, m_policy.maximumConfigBytes, m.descriptor.configDigest, data->config)) return Reject(error);
    if (model)
        if (const char* error = read(paths.model, m_policy.maximumModelBytes, m.descriptor.modelDigest, data->model)) return Reject(error);
    // Recheck every retained file after all reads/hashes, not just each leaf once.
    if (!files.Validate()) return Reject("STRATEGY_ARTIFACT_FILE_CHANGED");
    if (!files.Close()) return Reject("STRATEGY_ARTIFACT_CLOSE_FAILED");
    StrategyArtifactVerificationResult result;
    result.accepted = true; result.reasonCode = "STRATEGY_ARTIFACT_VERIFIED";
    result.artifact.m_data = std::move(data);
    return result;
#endif
}

StrategyRuntimeControlResult StrategyRuntimeControl::AdmitVerified(
    const VerifiedStrategyArtifact& artifact, const StrategyArtifactVerifier& verifier, std::uint64_t now)
{
    if (!verifier.Authorizes(artifact, now)) return Reject("STRATEGY_ARTIFACT_AUTHORIZATION_INVALID", nullptr);
    return Admit(artifact.Descriptor(), now);
}

StrategyRuntimeControlResult StrategyRuntimeControl::StartVerified(const std::string& moduleId,
    std::uint64_t expectedGeneration, const VerifiedStrategyArtifact& artifact,
    const StrategyArtifactVerifier& verifier, std::uint64_t now)
{
    if (!verifier.Authorizes(artifact, now)) return Reject("STRATEGY_ARTIFACT_AUTHORIZATION_INVALID", nullptr);
    std::lock_guard<std::mutex> lock(m_mutex);
    const auto found = m_records.find(moduleId);
    if (found == m_records.end()) return Reject("STRATEGY_NOT_FOUND", nullptr);
    auto& snapshot = found->second;
    if (!Guard(snapshot, expectedGeneration, now)) return GuardFailure(snapshot, expectedGeneration, now);
    if (snapshot.phase != StrategyRuntimePhase::Admitted) return Reject("STRATEGY_START_STATE_INVALID", &snapshot);
    if (!SameDescriptor(snapshot.descriptor, artifact.Descriptor()))
        return Reject("STRATEGY_ARTIFACT_IDENTITY_MISMATCH", &snapshot);
    auto proposed = snapshot;
    if (!Advance(proposed, now)) return Reject("STRATEGY_GENERATION_EXHAUSTED", &snapshot);
    proposed.phase = StrategyRuntimePhase::Running;
    proposed.reasonCode = "STRATEGY_RUNNING";
    return Commit(snapshot, std::move(proposed)); // Metadata only; never executes bytes.
}
