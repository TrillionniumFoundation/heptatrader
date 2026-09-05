#pragma once

#include "strategy_artifact_verifier.h"
#include "strategy_bytecode_admission.h"
#include "strategy_checkpoint_store.h"
#include "strategy_proposal.h"
#include <atomic>
#include <mutex>

// Trusted invocation context. Inputs are already normalized signed microunits;
// neither their presence nor a snapshot digest authenticates a market issuer.
struct StrategyBytecodeInvocation
{
    std::string proposalId, candidateId, capitalPool, accountBook, instrument, snapshotDigest;
    std::uint64_t sequence = 0, observedAtMs = 0, expiresAtMs = 0, horizonMs = 0;
    std::vector<DecisionMicrounits> inputs;
};

struct StrategyBytecodeLimits
{
    std::uint64_t maximumSteps = 100000;
    std::uint32_t wallTimeMs = 1000;
    std::uint64_t childAddressSpaceBytes = 64u << 20;
};

struct StrategyBytecodeResult
{
    bool accepted = false;
    const char* reasonCode = "STRATEGY_VM_NOT_RUN";
    std::uint64_t steps = 0;
    StrategyProposal proposal;
    std::string checkpointPayload;
};

// Executes only the fixed Hepta integer bytecode ISA, never ELF/native/Python.
// Linux x86-64 only. One concurrent child per object plus shared reservations.
// This is not a cross-process scheduler or an OS memory/CPU accounting service.
// The supervisor must exclusively reap this runner's children and select the
// current verifier, controller, trusted clock/context and checkpoint itself.
class StrategyBytecodeRuntime
{
public:
    explicit StrategyBytecodeRuntime(std::shared_ptr<StrategyBytecodeAdmission> admission =
        StrategyBytecodeAdmission::Default()) : m_admission(std::move(admission)) {}
    static const char* Version() noexcept { return "hepta.strategy-bytecode.v1"; }
    StrategyBytecodeResult Run(StrategyRuntimeControl& controller,
        const std::string& moduleId, std::uint64_t expectedGeneration,
        const VerifiedStrategyArtifact& artifact, const StrategyArtifactVerifier& verifier,
        const StrategyBytecodeInvocation& invocation,
        const VerifiedStrategyCheckpoint& checkpoint = VerifiedStrategyCheckpoint(),
        const StrategyBytecodeLimits& limits = StrategyBytecodeLimits(),
        const std::atomic<bool>* cancelled = nullptr);
private:
    const std::shared_ptr<StrategyBytecodeAdmission> m_admission;
    std::mutex m_mutex;
};
