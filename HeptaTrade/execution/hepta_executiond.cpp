#include "execution_service_runtime_composition.h"
#include "execution_service_runtime_config.h"

#include <csignal>
#include <cstdint>
#include <iostream>
#include <pthread.h>
#include <string>
#include <unistd.h>

int main(int argc, char**)
{
    if (argc != 1)
    {
        std::cerr << "hepta-executiond accepts no command-line configuration or credentials" << std::endl;
        return 2;
    }

    sigset_t terminationSignals;
    ::sigemptyset(&terminationSignals);
    ::sigaddset(&terminationSignals, SIGTERM);
    ::sigaddset(&terminationSignals, SIGINT);
    if (::pthread_sigmask(SIG_BLOCK, &terminationSignals, nullptr) != 0)
    {
        std::cerr << "execution runtime failed to block termination signals" << std::endl;
        return 3;
    }

    ExecutionServiceRuntimeConfig config;
    std::string reason;
    if (!ExecutionServiceRuntimeConfig::FromEnvironment(
            static_cast<int>(::getpid()), config, reason))
    {
        std::cerr << "execution runtime configuration rejected: " << reason << std::endl;
        return 4;
    }
    if (!config.Enabled())
    {
        std::cerr << "execution runtime is disabled by default" << std::endl;
        return 5;
    }

    ExecutionServiceRuntimeComposition runtime(config);
    if (!runtime.Start(reason))
    {
        std::cerr << "execution runtime startup rejected: " << reason << std::endl;
        return 6;
    }
    if (!runtime.RecoveryReason().empty())
        std::cerr << "execution runtime ready in degraded fail-closed state: "
                  << runtime.RecoveryReason() << std::endl;
    else
        std::cerr << "execution runtime ready mode="
                  << ExecutionServiceRuntimeConfig::ModeName(config.mode) << std::endl;

    int receivedSignal = 0;
    if (::sigwait(&terminationSignals, &receivedSignal) != 0)
    {
        std::cerr << "execution runtime signal wait failed" << std::endl;
        runtime.Stop();
        return 7;
    }
    runtime.Stop();
    return receivedSignal == SIGTERM || receivedSignal == SIGINT ? 0 : 8;
}
