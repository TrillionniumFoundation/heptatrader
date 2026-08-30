#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <mutex>
#include <string>

#include <sys/socket.h>
#include <unistd.h>

namespace
{
std::mutex gPressureMutex;
std::condition_variable gPressureCondition;
bool gBackpressureResponseObserved = false;
std::string gSendTail;

bool ContainsBackpressureReason(const void* buffer, std::size_t size)
{
    static const std::string needle = "OWNER_QUEUE_BACKPRESSURE";
    const char* bytes = static_cast<const char*>(buffer);
    std::lock_guard<std::mutex> lock(gPressureMutex);
    gSendTail.append(bytes, size);
    if (gSendTail.find(needle) != std::string::npos)
    {
        gBackpressureResponseObserved = true;
        gPressureCondition.notify_all();
        return true;
    }
    const std::size_t keep = needle.size() > 1 ? needle.size() - 1 : 0;
    if (gSendTail.size() > keep)
        gSendTail.erase(0, gSendTail.size() - keep);
    return false;
}
}

extern "C" ssize_t __real_send(int socket,
                                 const void* buffer,
                                 std::size_t length,
                                 int flags);
extern "C" int __real_usleep(useconds_t microseconds);

extern "C" ssize_t __wrap_send(int socket,
                                 const void* buffer,
                                 std::size_t length,
                                 int flags)
{
    const ssize_t result = __real_send(socket, buffer, length, flags);
    if (result > 0)
        ContainsBackpressureReason(buffer, static_cast<std::size_t>(result));
    return result;
}

extern "C" int __wrap_usleep(useconds_t microseconds)
{
    if (microseconds != 150000)
        return __real_usleep(microseconds);

    std::unique_lock<std::mutex> lock(gPressureMutex);
    const bool observed = gPressureCondition.wait_for(
        lock,
        std::chrono::seconds(4),
        []() { return gBackpressureResponseObserved; });
    lock.unlock();

    // Falling back to the original bounded delay keeps a genuine server
    // regression visible through the existing pressureRejected assertion.
    return observed ? 0 : __real_usleep(microseconds);
}
