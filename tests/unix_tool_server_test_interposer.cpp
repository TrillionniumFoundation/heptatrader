#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <mutex>
#include <set>

#include <sys/socket.h>
#include <unistd.h>

namespace
{
std::mutex gPressureMutex;
std::condition_variable gPressureCondition;
std::set<int> gConnectedClientSockets;
std::atomic<bool> gPressureDelayUsed(false);
}

extern "C" int __real_connect(int socket,
                               const struct sockaddr* address,
                               socklen_t addressLength);
extern "C" int __real_close(int descriptor);
extern "C" int __real_usleep(useconds_t microseconds);

extern "C" int __wrap_connect(int socket,
                               const struct sockaddr* address,
                               socklen_t addressLength)
{
    const int result = __real_connect(socket, address, addressLength);
    if (result == 0)
    {
        std::lock_guard<std::mutex> lock(gPressureMutex);
        gConnectedClientSockets.insert(socket);
        gPressureCondition.notify_all();
    }
    return result;
}

extern "C" int __wrap_close(int descriptor)
{
    {
        std::lock_guard<std::mutex> lock(gPressureMutex);
        gConnectedClientSockets.erase(descriptor);
    }
    return __real_close(descriptor);
}

extern "C" int __wrap_usleep(useconds_t microseconds)
{
    if (microseconds != 150000 || gPressureDelayUsed.load())
        return __real_usleep(microseconds);

    std::unique_lock<std::mutex> lock(gPressureMutex);
    const bool fourClientsConnected = gPressureCondition.wait_for(
        lock,
        std::chrono::seconds(2),
        []() { return gConnectedClientSockets.size() >= 4; });
    if (!fourClientsConnected || gPressureDelayUsed.exchange(true))
    {
        lock.unlock();
        return __real_usleep(microseconds);
    }
    lock.unlock();

    // All four pressure clients are connected and waiting. Keep the first
    // same-owner request active while the two ingress workers enqueue the next
    // two and deterministically reject the fourth at the owner queue limit.
    return __real_usleep(1000000);
}
