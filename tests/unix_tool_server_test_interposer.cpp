#ifdef usleep
#undef usleep
#endif

#include <atomic>
#include <unistd.h>

namespace
{
std::atomic<bool> gPressureHoldUsed(false);
}

extern "C" int hepta_test_usleep(useconds_t microseconds)
{
    if (microseconds == 150000 &&
        !gPressureHoldUsed.exchange(true, std::memory_order_acq_rel))
    {
        // Four same-owner clients are launched together and this callback is
        // reached by the first active request. Hold that sole owner slot for a
        // bounded interval so the two ingress workers can fill the configured
        // two-entry owner queue and reject the fourth request. The five-second
        // client timeout still leaves ample room for all accepted requests.
        return ::usleep(2000000);
    }
    return ::usleep(microseconds);
}
