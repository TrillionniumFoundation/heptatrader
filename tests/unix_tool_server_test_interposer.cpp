#ifdef connect
#undef connect
#endif
#ifdef usleep
#undef usleep
#endif

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <map>
#include <mutex>

#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>

namespace
{
struct SocketIdentity
{
    dev_t device = 0;
    ino_t inode = 0;
};

std::atomic<bool> gPressureHoldUsed(false);
std::mutex gClientMutex;
std::condition_variable gClientChanged;
std::map<int, SocketIdentity> gConnectedClients;

std::size_t LiveClientCountLocked()
{
    for (std::map<int, SocketIdentity>::iterator it = gConnectedClients.begin();
         it != gConnectedClients.end();)
    {
        struct stat current;
        if (::fstat(it->first, &current) != 0 ||
            current.st_dev != it->second.device ||
            current.st_ino != it->second.inode)
        {
            it = gConnectedClients.erase(it);
        }
        else
        {
            ++it;
        }
    }
    return gConnectedClients.size();
}
}

extern "C" int hepta_test_connect(
    int socketDescriptor,
    const struct sockaddr* address,
    socklen_t addressLength)
{
    const int result = ::connect(socketDescriptor, address, addressLength);
    if (result == 0)
    {
        struct stat identity;
        if (::fstat(socketDescriptor, &identity) == 0)
        {
            std::lock_guard<std::mutex> lock(gClientMutex);
            SocketIdentity observed;
            observed.device = identity.st_dev;
            observed.inode = identity.st_ino;
            gConnectedClients[socketDescriptor] = observed;
            gClientChanged.notify_all();
        }
    }
    return result;
}

extern "C" int hepta_test_usleep(useconds_t microseconds)
{
    if (microseconds == 150000 &&
        !gPressureHoldUsed.exchange(true, std::memory_order_acq_rel))
    {
        std::unique_lock<std::mutex> lock(gClientMutex);

        // Earlier test clients have already been joined and closed. Identity
        // checks discard stale descriptor numbers even when the process has
        // reused them for an accepted server socket or another file.
        const std::chrono::steady_clock::time_point connectDeadline =
            std::chrono::steady_clock::now() + std::chrono::seconds(2);
        while (LiveClientCountLocked() < 4 &&
               std::chrono::steady_clock::now() < connectDeadline)
        {
            gClientChanged.wait_until(lock, connectDeadline);
        }

        if (LiveClientCountLocked() >= 4)
        {
            // This callback owns the sole active slot for the owner. Accepted
            // requests therefore cannot close while it is held. The first
            // observed client identity to disappear is the concrete witness
            // that the fourth request received a backpressure response and its
            // test thread closed the socket after recording that response.
            const std::chrono::steady_clock::time_point rejectionDeadline =
                std::chrono::steady_clock::now() + std::chrono::seconds(2);
            while (LiveClientCountLocked() >= 4 &&
                   std::chrono::steady_clock::now() < rejectionDeadline)
            {
                gClientChanged.wait_for(lock, std::chrono::milliseconds(10));
            }
        }
    }

    return ::usleep(microseconds);
}
