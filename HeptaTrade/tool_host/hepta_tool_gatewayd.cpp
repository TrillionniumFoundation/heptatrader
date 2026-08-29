#include "agent_os_runtime_config.h"
#include "execution_gateway_runtime_config.h"
#include "tool_gateway_runtime_composition.h"
#include "tool_gateway_session_policy.h"

#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstdlib>
#include <iostream>
#include <pthread.h>
#include <string>
#include <unistd.h>

int main(int argc, char**)
{
    if (argc != 1)
    {
        std::cerr << "hepta-tool-gatewayd accepts no argv configuration or credentials\n";
        return 2;
    }

    sigset_t signals;
    ::sigemptyset(&signals);
    ::sigaddset(&signals, SIGTERM);
    ::sigaddset(&signals, SIGINT);
    if (::pthread_sigmask(SIG_BLOCK, &signals, nullptr) != 0) return 3;

    const AgentOsRuntimeConfig agentOs = AgentOsRuntimeConfig::FromEnvironment(
        static_cast<int>(::getpid()), static_cast<std::uint32_t>(::geteuid()));
    std::string agentOsReason;
    if (!agentOs.Validate(agentOsReason))
    {
        std::cerr << "tool gateway runtime configuration rejected: "
                  << agentOsReason << '\n';
        return 4;
    }
    if (std::getenv("HEPTA_TOOL_AGENT_UID") == nullptr ||
        std::getenv("HEPTA_TOOL_SUPERVISOR_UID") == nullptr)
    {
        std::cerr << "tool gateway requires explicit Agent and supervisor UID allowlists\n";
        return 4;
    }
    const ExecutionGatewayRuntimeConfig execution =
        ExecutionGatewayRuntimeConfig::FromEnvironment();
    std::string reason;
    if (!execution.Validate(reason) || !execution.Enabled())
    {
        std::cerr << "tool gateway execution configuration rejected: "
                  << (reason.empty() ? "TOOL_GATEWAY_REMOTE_EXECUTION_REQUIRED" : reason)
                  << '\n';
        return 5;
    }

    ToolGatewaySessionPolicy policy;
    if (!ToolGatewaySessionPolicy::FromEnvironment(
            execution, agentOs, policy, reason))
    {
        std::cerr << "tool gateway session policy rejected: " << reason << '\n';
        return 6;
    }

    ToolGatewayRuntimeComposition runtime(execution, agentOs, policy);
    // Keep SIGTERM/SIGINT blocked so the owner can consume them with
    // sigtimedwait, but expose a non-blocking probe to the startup state
    // machine. systemd may stop the unit while startup is still waiting for
    // the remote execution/event identities; waiting until Start() returns
    // would make that stop hit TimeoutStopSec and force a SIGKILL.
    runtime.SetStartupCancellationProbe([&signals]() {
        struct timespec timeout;
        timeout.tv_sec = 0;
        timeout.tv_nsec = 0;
        const int signal = ::sigtimedwait(&signals, nullptr, &timeout);
        return signal == SIGTERM || signal == SIGINT;
    });
    if (!runtime.Start(reason))
    {
        std::cerr << "tool gateway startup rejected: " << reason << '\n';
        // A requested service stop is a clean cancellation, not a transient
        // startup failure that should consume the bounded Restart budget.
        return reason == "TOOL_GATEWAY_STARTUP_CANCELLED" ? 0 : 7;
    }
    std::cerr << "tool gateway ready mode=" << execution.ModeName()
              << " paper_template=" << (policy.PaperEnabled() ? "enabled" : "disabled")
              << '\n';

    int received = 0;
    for (;;)
    {
        struct timespec timeout;
        timeout.tv_sec = 1;
        timeout.tv_nsec = 0;
        const int signal = ::sigtimedwait(&signals, nullptr, &timeout);
        if (signal == SIGTERM || signal == SIGINT)
        {
            received = signal;
            break;
        }
        if (signal < 0 && errno != EAGAIN && errno != EINTR)
        {
            runtime.Stop();
            return 8;
        }
        if (signal < 0 && errno == EAGAIN)
        {
            const std::uint64_t nowMs = static_cast<std::uint64_t>(
                std::chrono::duration_cast<std::chrono::milliseconds>(
                    std::chrono::system_clock::now().time_since_epoch()).count());
            std::size_t reaped = 0;
            std::string reapReason;
            if (!runtime.ReapExpired(nowMs, reaped, reapReason))
                std::cerr << "tool gateway session fence retry pending: "
                          << reapReason << '\n';
        }
    }
    runtime.Stop();
    return received == SIGTERM || received == SIGINT ? 0 : 9;
}
