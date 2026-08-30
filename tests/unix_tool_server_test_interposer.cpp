#include <atomic>

#include <unistd.h>

namespace
{
std::atomic<bool> gPressureDelayUsed(false);
}

extern "C" int __real_usleep(useconds_t microseconds);

extern "C" int __wrap_usleep(useconds_t microseconds)
{
    if (microseconds == 150000 && !gPressureDelayUsed.exchange(true))
    {
        // Hold only the first owner-pressure callback long enough for all
        // peer client threads to enter the bounded queue. Later callbacks
        // retain their original delay, so every accepted client remains well
        // inside the test's five-second wire timeout.
        return __real_usleep(2000000);
    }
    return __real_usleep(microseconds);
}
