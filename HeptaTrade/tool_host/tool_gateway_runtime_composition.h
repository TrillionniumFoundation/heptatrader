#pragma once

#include "agent_os_runtime_composition.h"
#include "execution_gateway_runtime_composition.h"
#include "tool_gateway_session_policy.h"

#include "../agent/decision_lease_manager.h"

#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <memory>
#include <string>

// Offline-only timing seam for startup barrier tests. Production construction
// leaves these callbacks empty and always uses the real monotonic clock/sleep.
struct ToolGatewayRuntimeTestHooks
{
    std::function<std::chrono::steady_clock::time_point()> readinessNow;
    std::function<void(std::chrono::milliseconds)> readinessSleep;
    // Offline-only startup cancellation seam. Production construction leaves
    // this empty; the daemon installs its signal probe through the public
    // setter below.
    std::function<bool()> startupCancellationProbe;
};

class ToolGatewayRuntimeComposition
{
public:
    ToolGatewayRuntimeComposition(const ExecutionGatewayRuntimeConfig& execution,
                                  const AgentOsRuntimeConfig& agentOs,
                                  const ToolGatewaySessionPolicy& sessionPolicy,
                                  const ToolGatewayRuntimeTestHooks& testHooks =
                                      ToolGatewayRuntimeTestHooks());
    ~ToolGatewayRuntimeComposition();

    // Install a non-blocking owner-supplied cancellation probe before startup.
    // The gateway daemon uses this to consume a pending systemd SIGTERM while
    // startup is waiting for the remote execution/event identities. The probe
    // must not perform runtime mutation and is called only by the Start owner
    // thread.
    void SetStartupCancellationProbe(
        const std::function<bool()>& cancellationProbe);
    bool Start(std::string& reason);
    void Stop();
    bool IsRunning() const;
    bool ReapExpired(std::uint64_t nowMs,
                     std::size_t& reaped,
                     std::string& reason);
    void SetRootCustodianUidForTests(std::uint32_t uid);

    // Validates the exact execution-owned schema relayed by external PAPER
    // system.get_health.  Kept public so contract tests exercise the same
    // parser used by the live callback (there is no alternate test parser).
    static bool ValidateExternalAuthoritativeHealth(
        const std::string& payload,
        std::uint32_t& authorizedConnectorCount);

private:
    // The Gateway must not publish a ready socket or admit a session until
    // both authenticated execution and event-feed identities are available.
    // This is deliberately a bounded startup wait: the service manager may
    // bring the two Unix services up concurrently, but a permanently absent
    // or unhealthy execution service must still fail closed.
    bool WaitForRemoteReadiness(std::string& reason);
    bool StartupCancellationRequested(std::string& reason) const;
    bool AbortStartupIfCancelled(std::string& reason);

    bool FenceRevokedOwner(const TradingToolHostSessionBinding& binding,
                           const std::string& reasonCode,
                           std::string& failureReason);

    ExecutionGatewayRuntimeConfig m_executionConfig;
    AgentOsRuntimeConfig m_agentOsConfig;
    ToolGatewaySessionPolicy m_sessionPolicy;
    ToolGatewayRuntimeTestHooks m_testHooks;
    std::function<bool()> m_startupCancellationProbe;
    std::string m_gatewayEpoch;
    ExecutionEventHub m_localEvents;
    DecisionLeaseManager m_localDecisionLeases;
    std::unique_ptr<ExecutionAuthority> m_failClosedLocalAuthority;
    std::unique_ptr<ExecutionGatewayRuntimeComposition> m_executionGateway;
    std::unique_ptr<TradingToolRegistry> m_registry;
    std::unique_ptr<TradingToolHost> m_host;
    std::unique_ptr<AgentOsRuntimeComposition> m_agentOs;
    std::atomic<std::uint64_t> m_fenceSequence;
    bool m_startAttempted = false;
    std::uint32_t m_rootCustodianUidForTests = 0;
    bool m_rootCustodianUidTestOverride = false;
};
