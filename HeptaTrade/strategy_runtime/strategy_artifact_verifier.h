#pragma once

#include "strategy_runtime_control.h"
#include <memory>

// All policy inputs, including key/revocation/time/sequence selection, belong
// to a trusted supervisor, never the candidate package. No keys are fetched.
struct StrategyArtifactTrustPolicy
{
    std::string revision, audience, moduleId, keyId;
    std::string ed25519PublicKey; // Exactly 32 raw bytes, not PEM/base64.
    bool revoked = false;
    std::uint64_t notBeforeMs = 0, notAfterMs = 0;
    std::uint64_t minimumReleaseSequence = 1;
    std::uint64_t maximumLifetimeMs = 86400000;
    std::size_t maximumArtifactBytes = 16u << 20;
    std::size_t maximumConfigBytes = 1u << 20;
    std::size_t maximumModelBytes = 32u << 20;
    std::size_t maximumBundleBytes = 64u << 20;
};

// A typed manifest, not a wire parser. The canonical signing message covers
// every field except signature. The descriptor includes all four budgets.
struct SignedStrategyArtifact
{
    StrategyArtifactDescriptor descriptor;
    std::string policyRevision, audience, keyId;
    std::uint64_t releaseSequence = 0, issuedAtMs = 0, expiresAtMs = 0;
    std::string signature; // Exactly 64 raw Ed25519 bytes.
};

struct StrategyArtifactPaths
{
    std::string directory; // Existing absolute private directory, mode 0700.
    std::string artifact, config, model; // Plain distinct leaf names; model optional.
};

// Verified historical bytes, not executable process or Broker authority.
class VerifiedStrategyArtifact
{
public:
    bool IsValid() const noexcept { return static_cast<bool>(m_data); }
    const StrategyArtifactDescriptor& Descriptor() const;
    const std::string& ArtifactBytes() const;
    const std::string& ConfigBytes() const;
    const std::string& ModelBytes() const;
    const std::string& PolicyDigest() const;
    std::uint64_t ReleaseSequence() const noexcept;
    std::uint64_t VerifiedAtMs() const noexcept;
    std::uint64_t ExpiresAtMs() const noexcept;
private:
    struct Data;
    std::shared_ptr<const Data> m_data;
    friend class StrategyArtifactVerifier;
};

struct StrategyArtifactVerificationResult
{
    bool accepted = false;
    const char* reasonCode = "STRATEGY_ARTIFACT_UNVERIFIED";
    VerifiedStrategyArtifact artifact;
};

// Policy is copied once and immutable; replace this object to change policy.
// Revocation and monotonic sequence state must be selected independently by
// the supervisor. A package cannot select the active policy or observation time.
class StrategyArtifactVerifier
{
public:
    explicit StrategyArtifactVerifier(StrategyArtifactTrustPolicy policy);
    static const char* Version() noexcept { return "hepta.strategy-artifact-verifier.v1"; }
    static std::string SigningMessage(const SignedStrategyArtifact& manifest);
    const std::string& PolicyDigest() const noexcept { return m_policyDigest; }
    StrategyArtifactVerificationResult Load(const SignedStrategyArtifact& manifest,
        const StrategyArtifactPaths& paths, std::uint64_t observedAtMs) const;
    bool Authorizes(const VerifiedStrategyArtifact& artifact, std::uint64_t observedAtMs) const noexcept;
private:
    const StrategyArtifactTrustPolicy m_policy;
    const std::string m_policyDigest;
};
