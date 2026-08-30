#include <array>
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
std::array<bool, 4> gPressureFrames = {{false, false, false, false}};
std::size_t gPressureFrameCount = 0;
bool gPressureHoldUsed = false;
std::string gSendTail;

void RecordPressureFrame(const void* buffer, std::size_t size)
{
    const char* bytes = static_cast<const char*>(buffer);
    std::lock_guard<std::mutex> lock(gPressureMutex);
    gSendTail.append(bytes, size);
    bool changed = false;
    for (std::size_t index = 0; index < gPressureFrames.size(); ++index)
    {
        const std::string needle = "pressure-" + std::to_string(index);
        if (!gPressureFrames[index] &&
            gSendTail.find(needle) != std::string::npos)
        {
            gPressureFrames[index] = true;
            ++gPressureFrameCount;
            changed = true;
        }
    }
    if (gSendTail.size() > 256)
        gSendTail.erase(0, gSendTail.size() - 256);
    if (changed) gPressureCondition.notify_all();
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
        RecordPressureFrame(buffer, static_cast<std::size_t>(result));
    return result;
}

extern "C" int __wrap_usleep(useconds_t microseconds)
{
    if (microseconds != 150000)
        return __real_usleep(microseconds);

    bool hold = false;
    {
        std::unique_lock<std::mutex> lock(gPressureMutex);
        if (!gPressureHoldUsed)
        {
            const bool allFramesWritten = gPressureCondition.wait_for(
                lock,
                std::chrono::seconds(2),
                []() { return gPressureFrameCount == gPressureFrames.size(); });
            if (allFramesWritten && !gPressureHoldUsed)
            {
                gPressureHoldUsed = true;
                hold = true;
            }
        }
    }

    // Once all four request bodies are in their socket buffers, hold the first
    // same-owner callback long enough for the two ingress workers to fill the
    // configured two-entry owner queue and reject the fourth request. If the
    // frame barrier is not reached, retain the original delay so the existing
    // assertion exposes the regression.
    return __real_usleep(hold ? 1000000 : microseconds);
}
