#include "../HeptaTrade/oms_journal.h"
#include "latency_fixture_common.h"

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <unistd.h>
#include <vector>

namespace
{
constexpr int kWarmupIterations = 8;
constexpr int kSampleCount = 128;

OmsJournalEvent Event(int sequence)
{
    OmsJournalEvent event;
    event.eventType = "order_intent";
    event.tsMs = OmsJournal::NowEpochMs();
    event.clientReqId = "performance-request-" + std::to_string(sequence);
    event.reqId = event.clientReqId;
    event.eventId = "performance-event-" + std::to_string(sequence);
    event.instrument = "EUR.USD";
    event.side = "BUY";
    event.qty = 1000.0;
    event.price = 1.1002;
    event.status = "PENDING";
    event.reason = "PERFORMANCE_FIXTURE";
    event.source = "repository-ci";
    event.executionDomain = "SIM-PERFORMANCE";
    return event;
}
}

int main()
{
    if (::setenv("HEPTA_OMS_ASYNC_FLUSH", "0", 1) != 0 ||
        ::setenv("HEPTA_OMS_SYNC_CRITICAL", "1", 1) != 0 ||
        ::setenv("HEPTA_OMS_BATCH_SIZE", "8", 1) != 0 ||
        ::setenv("HEPTA_OMS_FLUSH_INTERVAL_MS", "250", 1) != 0)
        return 2;

    char rawPath[] = "/tmp/hepta-oms-performance-XXXXXX";
    const int fd = ::mkstemp(rawPath);
    if (fd < 0) return 2;
    if (::close(fd) != 0)
    {
        ::unlink(rawPath);
        return 2;
    }
    const std::string path(rawPath);
    std::vector<long long> samples;
    samples.reserve(kSampleCount);
    int resultCode = 0;
    {
        OmsJournal journal;
        if (!journal.Init(path)) resultCode = 2;
        for (int i = 0; resultCode == 0 && i < kWarmupIterations; ++i)
            if (!journal.Append(Event(i))) resultCode = 2;
        for (int i = 0; resultCode == 0 && i < kSampleCount; ++i)
        {
            const OmsJournalEvent event = Event(kWarmupIterations + i);
            const auto start = std::chrono::steady_clock::now();
            const bool ok = journal.Append(event);
            const auto end = std::chrono::steady_clock::now();
            if (!ok)
            {
                resultCode = 2;
                break;
            }
            samples.push_back(
                std::chrono::duration_cast<std::chrono::microseconds>(end - start).count());
        }
        const OmsJournalHealthSnapshot health = journal.GetHealthSnapshot();
        if (resultCode == 0 &&
            (health.writePoisoned || health.durableSyncFailures != 0 ||
             health.durableSyncWrites < kWarmupIterations + kSampleCount))
            resultCode = 2;
    }
    if (::unlink(path.c_str()) != 0 && resultCode == 0) resultCode = 2;
    if (resultCode != 0) return resultCode;
    return HeptaLatencyFixture::ReportAndCheck(
        "oms-critical-durable-append-ci-v1",
        "critical OMS path-identity validation, append and fdatasync on hosted runner temporary storage",
        kWarmupIterations,
        samples);
}
