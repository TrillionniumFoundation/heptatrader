#include "execution_coordinator.h"

bool ExecutionCoordinator::EnterPaperTerminalFence(
    const AgentExecutionContext& context,
    const std::string& finalizationId,
    std::string& reason)
{
    (void)context;
    (void)finalizationId;
    reason = "IB_PAPER_TERMINAL_FENCE_V2_BINDING_REQUIRED";
    return false;
}

bool ExecutionCoordinator::EnterPaperTerminalFenceAndProject(
    const PaperTerminalFenceBinding& binding,
    PaperTerminalMutationUniverse& universe,
    std::string& reason)
{
    universe = PaperTerminalMutationUniverse();
    if (!ValidPaperTerminalFenceBinding(binding, reason)) return false;
    std::lock_guard<std::mutex> lock(m_mutex);
    return EnterPaperTerminalFenceAndProjectGenerationAwareLocked(
        binding, universe, reason);
}

// Preserve the already-accepted V1 implementation under private method names,
// then layer V2 dispatch on top. This avoids a second persistence authority:
// lookups, pinned indexes, terminal mutation enumeration and request caching are
// still the same code for both formats.
#define Prepare PrepareGenerationV1
#define Recover RecoverGenerationV1
#define RecoveryCapacity RecoveryCapacityGenerationV1
#include "execution_generation_support.inc"
#include "execution_generation_capacity_support.inc"
#undef RecoveryCapacity
#undef Recover
#undef Prepare
#include "execution_generation_v2_support.inc"
#include "execution_generation_complete_history.inc"
