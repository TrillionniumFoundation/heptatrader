#include "ib_paper_execution_runtime_composition.h"
#include "ib_paper_execution_runtime_config.h"

#include <csignal>
#include <cerrno>
#include <iostream>
#include <pthread.h>
#include <string>
#include <unistd.h>

int main(int argc, char**)
{
    if (argc != 1)
    {
        std::cerr << "hepta-ib-executiond accepts no argv configuration or credentials\n";
        return 2;
    }
    sigset_t signals;
    ::sigemptyset(&signals);
    ::sigaddset(&signals, SIGTERM);
    ::sigaddset(&signals, SIGINT);
    if (::pthread_sigmask(SIG_BLOCK, &signals, nullptr) != 0) return 3;

    IbPaperExecutionRuntimeConfig config;
    std::string reason;
    if (!IbPaperExecutionRuntimeConfig::FromEnvironment(
            static_cast<int>(::getpid()), config, reason))
    {
        std::cerr << "IB PAPER runtime configuration rejected: " << reason << '\n';
        return 4;
    }
    if (!config.Enabled())
    {
        std::cerr << "IB PAPER runtime is disabled by default\n";
        return 5;
    }
    IbPaperExecutionRuntimeComposition runtime(config);
    // Keep SIGTERM/SIGINT blocked so the owner can consume them with
    // sigtimedwait, but expose a non-blocking probe to the startup state
    // machine.  systemd may stop the unit while startup is still waiting for
    // IB's farm/snapshot witnesses; waiting until Start() returns would make
    // that stop hit TimeoutStopSec and force a SIGKILL.
    runtime.SetStartupCancellationProbe([&signals]() {
        struct timespec timeout;
        timeout.tv_sec = 0;
        timeout.tv_nsec = 0;
        const int signal = ::sigtimedwait(&signals, nullptr, &timeout);
        return signal == SIGTERM || signal == SIGINT;
    });
    if (!runtime.Start(reason))
    {
        std::cerr << "IB PAPER runtime startup rejected: " << reason << '\n';
        // A requested service stop is a clean cancellation, not a transient
        // startup failure that should consume the bounded Restart budget.
        return reason == "IB_PAPER_STARTUP_CANCELLED" ? 0 : 6;
    }
    std::cerr << "IB PAPER execution runtime ready" << '\n';
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
            return 7;
        }
        std::string fatalReason;
        if (runtime.HasFatalRuntimeError(&fatalReason))
        {
            std::cerr << "IB PAPER runtime fatal: " << fatalReason << '\n';
            runtime.Stop();
            return 9;
        }
    }
    runtime.Stop();
    return received == SIGTERM || received == SIGINT ? 0 : 8;
}
