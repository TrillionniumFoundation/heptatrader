#ifdef connect
#undef connect
#endif
#ifdef close
#undef close
#endif
#ifdef usleep
#undef usleep
#endif

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

extern "C" int hepta_test_connect(
    int socketDescriptor,
    const struct sockaddr* address,
    socklen_t addressLength)
{
    const int result = ::connect(socketDescriptor, address, addressLength);
    if (result == 0)
    {
        std::lock_guard<std::mutex> lock(gPressureMutex);
        gConnectedClients.insert(socketDescriptor);
        gPressureCondition.notify_all();
    }
    return result;
}

extern "C" int hepta_test_close(int descriptor)
{
    {
        std::lock_guard<std::mutex> lock(gPressureMutex);
        if (gConnectedClients.erase(descriptor) != 0)
            gPressureCondition.notify_all();
    }
    return ::close(descriptor);
}

extern "C" int hepta_test_usleep(useconds_t microseconds)
{
    if (microseconds != 150000)
        return ::usleep(microseconds);

    {
        std::unique_lock<std::mutex> lock(gPressureMutex);
        if (!gPressureHoldUsed)
        {
            gPressureHoldUsed = true;

            // The first same-owner request is executing.  Hold its only owner
            // slot until all four pressure clients are connected, then until
            // one client receives and closes a backpressure response.  An
            // accepted request cannot close while this callback is held.
            const bool allClientsConnected = gPressureCondition.wait_for(
                lock,
                std::chrono::seconds(2),
                []() { return gConnectedClients.size() >= 4; });
            if (allClientsConnected)
            {
                gPressureCondition.wait_for(
                    lock,
                    std::chrono::seconds(2),
                    []() { return gConnectedClients.size() < 4; });
            }
        }
    }

    return ::usleep(microseconds);
}
