#ifdef connect
#undef connect
#endif
#ifdef usleep
#undef usleep
#endif
#ifdef UnixToolServer
#undef UnixToolServer
#endif

#include "../HeptaTrade/tool_host/unix_tool_server.h"

#include <atomic>
#include <chrono>
#include <thread>

#include <sys/socket.h>
#include <unistd.h>

namespace
{
std::atomic<bool> gPressureHoldUsed(false);
std::atomic<UnixToolServer*> gServer(nullptr);

bool WaitUntil(const std::chrono::steady_clock::time_point& deadline,
               const std::function<bool()>& predicate)
{
    while (std::chrono::steady_clock::now() < deadline)
    {
        if (predicate()) return true;
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    return predicate();
}
}

extern "C" void hepta_test_set_unix_tool_server(UnixToolServer* server)
{
    gServer.store(server, std::memory_order_release);
}

extern "C" int hepta_test_connect(
    int socketDescriptor,
    const struct sockaddr* address,
    socklen_t addressLength)
{
    return ::connect(socketDescriptor, address, addressLength);
}

extern "C" int hepta_test_usleep(useconds_t microseconds)
{
    if (microseconds == 150000 &&
        !gPressureHoldUsed.exchange(true, std::memory_order_acq_rel))
    {
        const std::chrono::steady_clock::time_point deadline =
            std::chrono::steady_clock::now() + std::chrono::seconds(5);

        UnixToolServer* server = nullptr;
        WaitUntil(deadline, [&]() {
            server = gServer.load(std::memory_order_acquire);
            return server != nullptr;
        });

        if (server != nullptr)
        {
            // The callback containing this sleep already owns the sole active
            // slot for its owner.  Two pending requests fill the configured
            // owner queue; the fourth request must then produce the observable
            // owner-backpressure counter before the active callback is released.
            WaitUntil(deadline, [&]() {
                const UnixToolServerHealth health = server->GetHealth();
                return health.activeRequests >= 1 &&
                       health.pendingConnections >= 2 &&
                       health.readyOwners >= 1;
            });
            WaitUntil(deadline, [&]() {
                return server->GetHealth().ownerBackpressureRejections >= 1;
            });
        }
        return 0;
    }

    return ::usleep(microseconds);
}
