#include <chrono>
#include <condition_variable>
#include <mutex>
#include <set>

#include <sys/socket.h>
#include <unistd.h>

namespace
{
std::mutex gPressureMutex;
std::condition_variable gPressureCondition;
std::set<int> gConnectedClients;
bool gPressureHoldUsed = false;
}

extern "C" int __real_connect(
    int socket,
    const struct sockaddr* address,
    socklen_t addressLength);
extern "C" int __real_close(int descriptor);
extern "C" int __real_usleep(useconds_t microseconds);

extern "C" int __wrap_connect(
    int socket,
    const struct sockaddr* address,
    socklen_t addressLength)
{
    const int result = __real_connect(socket, address, addressLength);
    if (result == 0)
    {
        std::lock_guard<std::mutex> lock(gPressureMutex);
        gConnectedClients.insert(socket);
        gPressureCondition.notify_all();
    }
    return result;
}

extern "C" int __wrap_close(int descriptor)
{
    {
        std::lock_guard<std::mutex> lock(gPressureMutex);
        if (gConnectedClients.erase(descriptor) != 0)
            gPressureCondition.notify_all();
    }
    return __real_close(descriptor);
}

extern "C" int __wrap_usleep(useconds_t microseconds)
{
    if (microseconds != 150000)
        return __real_usleep(microseconds);

    {
        std::unique_lock<std::mutex> lock(gPressureMutex);
        if (!gPressureHoldUsed)
        {
            gPressureHoldUsed = true;

            // At this point the first same-owner request is executing. While
            // it is held, the remaining clients can connect, send and enter
            // the two-entry owner queue. The fourth request must be rejected.
            const bool allClientsConnected = gPressureCondition.wait_for(
                lock,
                std::chrono::seconds(2),
                []() { return gConnectedClients.size() >= 4; });

            if (allClientsConnected)
            {
                // An accepted request cannot close while this callback owns
                // the sole per-owner execution slot. A client disappearing
                // here is therefore the observable backpressure response.
                gPressureCondition.wait_for(
                    lock,
                    std::chrono::seconds(2),
                    []() { return gConnectedClients.size() < 4; });
            }
        }
    }

    return __real_usleep(microseconds);
}
