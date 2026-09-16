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

// Generation/index implementation is intentionally compiled in this already
// owned Execution translation unit. The .inc file is not a second target or a
// hidden executable path; it extends the same hepta_execution_core binary and
// therefore remains covered by the existing CMake ownership inventory.
#include "execution_generation_support.inc"
