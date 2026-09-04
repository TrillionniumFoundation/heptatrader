#include "../HeptaTrade/oms_journal.h"

#include <cstdlib>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <string>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

namespace
{
void Require(bool condition, const char* expression, int line)
{
    if (condition) return;
    std::cerr << "requirement failed at line " << line << ": " << expression << "\n";
    std::abort();
}

#define REQUIRE(expression) Require(static_cast<bool>(expression), #expression, __LINE__)

std::string MakeTempDirectory()
{
    char pattern[] = "/tmp/hepta-oms-durability-XXXXXX";
    char* directory = ::mkdtemp(pattern);
    REQUIRE(directory != nullptr);
    return directory;
}

void WritePrivateFile(const std::string& path, const std::string& contents)
{
    std::ofstream output(path.c_str(), std::ios::out | std::ios::binary);
    REQUIRE(output.is_open());
    output << contents;
    REQUIRE(output.good());
    output.close();
    REQUIRE(output.good());
    REQUIRE(::chmod(path.c_str(), 0600) == 0);
}

bool IsEmptyFile(const std::string& path)
{
    struct stat metadata;
    return ::stat(path.c_str(), &metadata) == 0 && metadata.st_size == 0;
}

OmsJournalEvent MakeCriticalEvent(const std::string& id)
{
    OmsJournalEvent event;
    event.eventType = "order_intent";
    event.tsMs = OmsJournal::NowEpochMs();
    event.reqId = id;
    event.clientReqId = id;
    event.eventId = id;
    event.venue = "IB";
    event.account = "DU123456";
    event.executionDomain = "PAPER";
    return event;
}

void TestPathReplacementPoisonsBeforeWriting()
{
    const std::string directory = MakeTempDirectory();
    const std::string path = directory + "/journal.jsonl";
    const std::string pinnedPath = directory + "/journal-pinned.jsonl";

    ::setenv("HEPTA_OMS_ASYNC_FLUSH", "0", 1);
    ::setenv("HEPTA_OMS_SYNC_CRITICAL", "1", 1);
    {
        OmsJournal journal;
        REQUIRE(journal.Init(path));
        REQUIRE(journal.Append(MakeCriticalEvent("first")));
        OmsJournalHealthSnapshot health = journal.GetHealthSnapshot();
        REQUIRE(health.durableSyncWrites == 1);
        REQUIRE(health.durableSyncFailures == 0);
        REQUIRE(!health.writePoisoned);

        REQUIRE(::rename(path.c_str(), pinnedPath.c_str()) == 0);
        const int decoy = ::open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC,
                                 0600);
        REQUIRE(decoy >= 0);
        REQUIRE(::close(decoy) == 0);

        REQUIRE(!journal.Append(MakeCriticalEvent("second")));
        health = journal.GetHealthSnapshot();
        REQUIRE(health.writePoisoned);
        REQUIRE(health.writeFailTotal >= 1);
        REQUIRE(IsEmptyFile(path));
        REQUIRE(journal.Replay(std::function<void(const OmsJournalEvent&)>()) == -1);
    }

    REQUIRE(IsEmptyFile(path));
    REQUIRE(::unlink(path.c_str()) == 0);
    REQUIRE(::unlink(pinnedPath.c_str()) == 0);
    REQUIRE(::rmdir(directory.c_str()) == 0);
}

void TestMissingPathPoisonsBeforeWriting()
{
    const std::string directory = MakeTempDirectory();
    const std::string path = directory + "/journal.jsonl";
    {
        OmsJournal journal;
        REQUIRE(journal.Init(path));
        REQUIRE(journal.Append(MakeCriticalEvent("first")));
        REQUIRE(::unlink(path.c_str()) == 0);
        REQUIRE(!journal.Append(MakeCriticalEvent("second")));
        REQUIRE(journal.GetHealthSnapshot().writePoisoned);
    }
    REQUIRE(::rmdir(directory.c_str()) == 0);
}

void TestSymlinkReplacementPoisonsBeforeWriting()
{
    const std::string directory = MakeTempDirectory();
    const std::string path = directory + "/journal.jsonl";
    const std::string pinnedPath = directory + "/journal-pinned.jsonl";
    {
        OmsJournal journal;
        REQUIRE(journal.Init(path));
        REQUIRE(journal.Append(MakeCriticalEvent("first")));
        REQUIRE(::rename(path.c_str(), pinnedPath.c_str()) == 0);
        REQUIRE(::symlink(pinnedPath.c_str(), path.c_str()) == 0);
        REQUIRE(!journal.Append(MakeCriticalEvent("second")));
        REQUIRE(journal.GetHealthSnapshot().writePoisoned);
    }
    REQUIRE(::unlink(path.c_str()) == 0);
    REQUIRE(::unlink(pinnedPath.c_str()) == 0);
    REQUIRE(::rmdir(directory.c_str()) == 0);
}

void TestRepeatedInitFailsWithoutDisturbingOriginal()
{
    const std::string directory = MakeTempDirectory();
    const std::string firstPath = directory + "/first.jsonl";
    const std::string secondPath = directory + "/second.jsonl";
    ::setenv("HEPTA_OMS_ASYNC_FLUSH", "1", 1);
    {
        OmsJournal journal;
        REQUIRE(journal.Init(firstPath));
        REQUIRE(!journal.Init(secondPath));
        REQUIRE(journal.GetPath() == firstPath);
        REQUIRE(journal.Append(MakeCriticalEvent("still-first")));
        REQUIRE(::access(secondPath.c_str(), F_OK) != 0);
    }
    REQUIRE(::unlink(firstPath.c_str()) == 0);
    REQUIRE(::rmdir(directory.c_str()) == 0);
    ::setenv("HEPTA_OMS_ASYNC_FLUSH", "0", 1);
}

void TestStrictReplayIsCallbackAtomicAndReentrant()
{
    const std::string directory = MakeTempDirectory();
    const std::string path = directory + "/journal.jsonl";
    const std::string malformedPath = directory + "/malformed.jsonl";
    {
        OmsJournal journal;
        REQUIRE(journal.Init(path));
        OmsJournalEvent event = MakeCriticalEvent("large-time");
        event.tsMs = 5000000000000LL;
        REQUIRE(journal.Append(event));

        long long replayedTs = 0;
        REQUIRE(journal.Replay([&](const OmsJournalEvent& replayed) {
            replayedTs = replayed.tsMs;
            REQUIRE(!journal.GetHealthSnapshot().writePoisoned);
        }) == 1);
        REQUIRE(replayedTs == event.tsMs);
    }

    {
        std::ofstream corrupt(path.c_str(), std::ios::out | std::ios::app | std::ios::binary);
        REQUIRE(corrupt.is_open());
        corrupt << "{\"schema_version\":4,\"not_an_event\":true}\n";
        REQUIRE(corrupt.good());
    }
    REQUIRE(::chmod(path.c_str(), 0600) == 0);
    {
        OmsJournal journal;
        REQUIRE(journal.Init(path));
        int callbacks = 0;
        REQUIRE(journal.Replay([&](const OmsJournalEvent&) { ++callbacks; }) == -1);
        REQUIRE(callbacks == 0);
    }

    WritePrivateFile(malformedPath,
        "{\"event\":\"valid\"}\n{\"event\":\"unterminated}\n");
    {
        OmsJournal journal;
        REQUIRE(journal.Init(malformedPath));
        int callbacks = 0;
        REQUIRE(journal.Replay([&](const OmsJournalEvent&) { ++callbacks; }) == -1);
        REQUIRE(callbacks == 0);
    }

    REQUIRE(::unlink(path.c_str()) == 0);
    REQUIRE(::unlink(malformedPath.c_str()) == 0);
    REQUIRE(::rmdir(directory.c_str()) == 0);
}

void TestAsyncQueueAndBufferAccounting()
{
    const std::string directory = MakeTempDirectory();
    const std::string path = directory + "/journal.jsonl";
    ::setenv("HEPTA_OMS_ASYNC_FLUSH", "1", 1);
    ::setenv("HEPTA_OMS_BATCH_SIZE", "64", 1);
    ::setenv("HEPTA_OMS_FLUSH_INTERVAL_MS", "60000", 1);
    {
        OmsJournal journal;
        REQUIRE(journal.Init(path));
        for (int index = 0; index < 3; ++index)
        {
            OmsJournalEvent event = MakeCriticalEvent("async-" + std::to_string(index));
            event.eventType = "ack";
            REQUIRE(journal.Append(event));
        }
        REQUIRE(journal.GetHealthSnapshot().enqueuedTotal == 3);
        REQUIRE(journal.Replay(std::function<void(const OmsJournalEvent&)>()) == 3);
        const OmsJournalHealthSnapshot health = journal.GetHealthSnapshot();
        REQUIRE(health.enqueuedTotal == 3);
        REQUIRE(health.flushedTotal == 3);
        REQUIRE(health.queueDepth == 0);
        REQUIRE(health.bufferedDepth == 0);
        REQUIRE(!health.writePoisoned);
    }
    REQUIRE(::unlink(path.c_str()) == 0);
    REQUIRE(::rmdir(directory.c_str()) == 0);
    ::setenv("HEPTA_OMS_ASYNC_FLUSH", "0", 1);
    ::setenv("HEPTA_OMS_BATCH_SIZE", "8", 1);
    ::setenv("HEPTA_OMS_FLUSH_INTERVAL_MS", "250", 1);
}

void TestAsyncCriticalWritesPreserveAppendOrder()
{
    const std::string directory = MakeTempDirectory();
    const std::string path = directory + "/journal.jsonl";
    ::setenv("HEPTA_OMS_ASYNC_FLUSH", "1", 1);
    ::setenv("HEPTA_OMS_SYNC_CRITICAL", "1", 1);
    ::setenv("HEPTA_OMS_CRITICAL_FLUSH_QUEUED", "0", 1);
    ::setenv("HEPTA_OMS_BATCH_SIZE", "64", 1);
    ::setenv("HEPTA_OMS_FLUSH_INTERVAL_MS", "60000", 1);
    {
        OmsJournal journal;
        REQUIRE(journal.Init(path));

        OmsJournalEvent queued = MakeCriticalEvent("queued-before-critical");
        queued.eventType = "ack";
        REQUIRE(journal.Append(queued));

        OmsJournalEvent critical = MakeCriticalEvent("critical-after-queued");
        critical.eventType = "order_intent";
        REQUIRE(journal.Append(critical));

        std::vector<std::string> replayed;
        REQUIRE(journal.Replay([&](const OmsJournalEvent& event) {
            replayed.push_back(event.reqId);
        }) == 2);
        REQUIRE(replayed.size() == 2);
        REQUIRE(replayed[0] == "queued-before-critical");
        REQUIRE(replayed[1] == "critical-after-queued");
    }
    REQUIRE(::unlink(path.c_str()) == 0);
    REQUIRE(::rmdir(directory.c_str()) == 0);
    ::setenv("HEPTA_OMS_ASYNC_FLUSH", "0", 1);
    ::setenv("HEPTA_OMS_SYNC_CRITICAL", "1", 1);
    ::setenv("HEPTA_OMS_CRITICAL_FLUSH_QUEUED", "0", 1);
    ::setenv("HEPTA_OMS_BATCH_SIZE", "8", 1);
    ::setenv("HEPTA_OMS_FLUSH_INTERVAL_MS", "250", 1);
}

void TestBufferedAppendIsNotDurableUntilCriticalBarrier()
{
    const std::string directory = MakeTempDirectory();
    const std::string path = directory + "/journal.jsonl";
    ::setenv("HEPTA_OMS_ASYNC_FLUSH", "0", 1);
    ::setenv("HEPTA_OMS_SYNC_CRITICAL", "1", 1);
    ::setenv("HEPTA_OMS_BATCH_SIZE", "64", 1);
    ::setenv("HEPTA_OMS_FLUSH_INTERVAL_MS", "9223372036854775807", 1);
    {
        OmsJournal journal;
        REQUIRE(journal.Init(path));
        OmsJournalEvent buffered = MakeCriticalEvent("buffered");
        buffered.eventType = "ack";
        REQUIRE(journal.Append(buffered));
        OmsJournalHealthSnapshot health = journal.GetHealthSnapshot();
        REQUIRE(health.bufferedDepth == 1);
        REQUIRE(health.flushedTotal == 0);
        REQUIRE(health.durableSyncWrites == 0);
        REQUIRE(IsEmptyFile(path));

        REQUIRE(journal.Append(MakeCriticalEvent("barrier")));
        health = journal.GetHealthSnapshot();
        REQUIRE(health.bufferedDepth == 0);
        REQUIRE(health.flushedTotal == 2);
        REQUIRE(health.durableSyncWrites == 1);
        REQUIRE(!health.writePoisoned);
        std::vector<std::string> ids;
        REQUIRE(journal.Replay([&](const OmsJournalEvent& event) {
            ids.push_back(event.reqId);
        }) == 2);
        REQUIRE(ids.size() == 2);
        REQUIRE(ids[0] == "buffered");
        REQUIRE(ids[1] == "barrier");
    }
    REQUIRE(::unlink(path.c_str()) == 0);
    REQUIRE(::rmdir(directory.c_str()) == 0);
    ::setenv("HEPTA_OMS_BATCH_SIZE", "8", 1);
    ::setenv("HEPTA_OMS_FLUSH_INTERVAL_MS", "250", 1);
}

void TestRecordVersionAndRequestAliasRoundTrip()
{
    const std::string directory = MakeTempDirectory();
    const std::string path = directory + "/journal.jsonl";
    ::setenv("HEPTA_OMS_ASYNC_FLUSH", "0", 1);
    ::setenv("HEPTA_OMS_SYNC_CRITICAL", "1", 1);
    {
        OmsJournal journal;
        REQUIRE(journal.Init(path));
        OmsJournalEvent current = MakeCriticalEvent("canonical-request");
        REQUIRE(current.schemaVersion == 4);
        REQUIRE(OmsJournal::kSchemaVersion == 4);
        current.clientReqId = "not-the-authoritative-alias";
        current.brokerCallbackType = "orderStatus";
        current.brokerExecutionId = "execution-id";
        REQUIRE(journal.Append(current));
        OmsJournalEvent previous = MakeCriticalEvent("legacy-request");
        previous.schemaVersion = 3;
        previous.reqId.clear();
        REQUIRE(journal.Append(previous));
        std::vector<OmsJournalEvent> replayed;
        REQUIRE(journal.Replay([&](const OmsJournalEvent& event) {
            replayed.push_back(event);
        }) == 2);
        REQUIRE(replayed.size() == 2);
        REQUIRE(replayed[0].schemaVersion == 4);
        REQUIRE(replayed[0].reqId == "canonical-request");
        REQUIRE(replayed[0].clientReqId == "canonical-request");
        REQUIRE(replayed[0].brokerCallbackType == "orderStatus");
        REQUIRE(replayed[0].brokerExecutionId == "execution-id");
        REQUIRE(replayed[1].schemaVersion == 3);
        REQUIRE(replayed[1].reqId == "legacy-request");
        REQUIRE(replayed[1].clientReqId == "legacy-request");
    }
    REQUIRE(::unlink(path.c_str()) == 0);
    REQUIRE(::rmdir(directory.c_str()) == 0);
}

void TestJournalReplayDoesNotInventCommandDeduplication()
{
    const std::string directory = MakeTempDirectory();
    const std::string path = directory + "/journal.jsonl";
    ::setenv("HEPTA_OMS_ASYNC_FLUSH", "0", 1);
    ::setenv("HEPTA_OMS_SYNC_CRITICAL", "1", 1);
    {
        OmsJournal journal;
        REQUIRE(journal.Init(path));
        const OmsJournalEvent event = MakeCriticalEvent("same-event-identity");
        REQUIRE(journal.Append(event));
        REQUIRE(journal.Append(event));
        int callbacks = 0;
        REQUIRE(journal.Replay([&](const OmsJournalEvent& replayed) {
            ++callbacks;
            REQUIRE(replayed.eventId == event.eventId);
            REQUIRE(replayed.reqId == event.reqId);
        }) == 2);
        REQUIRE(callbacks == 2);
    }
    REQUIRE(::unlink(path.c_str()) == 0);
    REQUIRE(::rmdir(directory.c_str()) == 0);
}

void TestUnsafePermissionsLinksAndTornFilesFailClosed()
{
    const std::string directory = MakeTempDirectory();
    const std::string target = directory + "/target";
    const std::string symlinkPath = directory + "/symlink";
    const std::string tornPath = directory + "/torn";
    const std::string publicPath = directory + "/public";
    const std::string hardLinkPath = directory + "/hard-link";

    WritePrivateFile(target, "\n");
    REQUIRE(::symlink(target.c_str(), symlinkPath.c_str()) == 0);
    OmsJournal symlinkJournal;
    REQUIRE(!symlinkJournal.Init(symlinkPath));

    WritePrivateFile(tornPath, "{\"schema_version\":4,\"event\":\"order_intent\"");
    OmsJournal tornJournal;
    REQUIRE(!tornJournal.Init(tornPath));

    WritePrivateFile(publicPath, "");
    REQUIRE(::chmod(publicPath.c_str(), 0644) == 0);
    OmsJournal publicJournal;
    REQUIRE(!publicJournal.Init(publicPath));
    REQUIRE(::chmod(publicPath.c_str(), 0600) == 0);
    REQUIRE(::link(publicPath.c_str(), hardLinkPath.c_str()) == 0);
    OmsJournal hardLinkJournal;
    REQUIRE(!hardLinkJournal.Init(publicPath));

    REQUIRE(::unlink(symlinkPath.c_str()) == 0);
    REQUIRE(::unlink(target.c_str()) == 0);
    REQUIRE(::unlink(tornPath.c_str()) == 0);
    REQUIRE(::unlink(hardLinkPath.c_str()) == 0);
    REQUIRE(::unlink(publicPath.c_str()) == 0);
    REQUIRE(::rmdir(directory.c_str()) == 0);
}
}

int main()
{
    TestPathReplacementPoisonsBeforeWriting();
    TestMissingPathPoisonsBeforeWriting();
    TestSymlinkReplacementPoisonsBeforeWriting();
    TestRepeatedInitFailsWithoutDisturbingOriginal();
    TestStrictReplayIsCallbackAtomicAndReentrant();
    TestAsyncQueueAndBufferAccounting();
    TestAsyncCriticalWritesPreserveAppendOrder();
    TestUnsafePermissionsLinksAndTornFilesFailClosed();
    TestBufferedAppendIsNotDurableUntilCriticalBarrier();
    TestRecordVersionAndRequestAliasRoundTrip();
    TestJournalReplayDoesNotInventCommandDeduplication();
    return 0;
}
