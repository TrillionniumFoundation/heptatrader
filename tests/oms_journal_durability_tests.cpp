#include "../HeptaTrade/oms_journal.h"
#include "../HeptaTrade/oms_archive_codec.h"
#include <sys/file.h>
#include "../HeptaTrade/oms_capacity_observation.h"
#include "oms_runtime_observation_cases.h"

#include <cstdlib>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <string>
#include <sys/stat.h>
#include <unistd.h>

// Syscall faults are confined to this test executable and one exact inode.
// Production has no fault switch, replacement path or alternate sync provider.
#include <atomic>
#include <cerrno>
#include <limits>
#include <sys/syscall.h>
#include <sys/wait.h>

namespace JournalFault {
enum Mode { None, SecondWriteFailure, SyncFailure, ReplaceDuringSync,
            CrashAfterFirstWrite, CrashBeforeSync, TornSecondWrite };
std::atomic<int> mode{None};
std::atomic<unsigned> writes{0};
dev_t device = 0;
ino_t inode = 0;
std::string path, moved;
bool Matches(int fd) {
    struct stat value;
    return ::fstat(fd, &value) == 0 && value.st_dev == device && value.st_ino == inode;
}
void Arm(const std::string& name, Mode selected) {
    struct stat value;
    if (::lstat(name.c_str(), &value) != 0) std::abort();
    path = name; moved = name + ".moved"; device = value.st_dev; inode = value.st_ino;
    writes.store(0); mode.store(selected);
}
}
extern "C" ssize_t write(int fd, const void* buffer, size_t size) {
    const int mode = JournalFault::mode.load();
    if (mode != JournalFault::None && JournalFault::Matches(fd)) {
        const unsigned ordinal = ++JournalFault::writes;
        if (mode == JournalFault::SecondWriteFailure && ordinal == 2) {
            errno = ENOSPC; return -1;
        }
        if (mode == JournalFault::TornSecondWrite && ordinal >= 2) {
            if (ordinal == 2) return ::syscall(SYS_write, fd, buffer, std::min(size, size_t(7)));
            errno = EIO; return -1;
        }
        const ssize_t result = ::syscall(SYS_write, fd, buffer, size);
        if (mode == JournalFault::CrashAfterFirstWrite && result > 0) ::_exit(91);
        return result;
    }
    return ::syscall(SYS_write, fd, buffer, size);
}
extern "C" int fdatasync(int fd) {
    const int mode = JournalFault::mode.load();
    if (mode != JournalFault::None && JournalFault::Matches(fd)) {
        if (mode == JournalFault::CrashBeforeSync) ::_exit(92);
        if (mode == JournalFault::SyncFailure) { errno = EIO; return -1; }
        if (mode == JournalFault::ReplaceDuringSync) {
            JournalFault::mode.store(JournalFault::None);
            const int synced = ::syscall(SYS_fdatasync, fd);
            if (synced != 0 || ::rename(JournalFault::path.c_str(), JournalFault::moved.c_str()) != 0)
                return -1;
            const int decoy = ::open(JournalFault::path.c_str(), O_CREAT | O_EXCL | O_WRONLY | O_CLOEXEC, 0600);
            if (decoy < 0) return -1;
            return ::close(decoy);
        }
    }
    return ::syscall(SYS_fdatasync, fd);
}

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

void TestPairValidationAndSingleBarrier()
{
    for (int async = 0; async < 2; ++async) {
        const std::string directory = MakeTempDirectory(), path = directory + "/journal";
        ::setenv("HEPTA_OMS_ASYNC_FLUSH", async ? "1" : "0", 1);
        ::setenv("HEPTA_OMS_SYNC_CRITICAL", "0", 1);
        {
            OmsJournal journal; REQUIRE(journal.Init(path));
            OmsJournalEvent first = MakeCriticalEvent("intent"), second = MakeCriticalEvent("attempt");
            second.eventType = "place_send_attempt";
            OmsJournalEvent invalid = second;
            invalid.qty = std::numeric_limits<double>::quiet_NaN();
            REQUIRE(journal.AppendDurablePair(first, invalid) == OmsJournal::DurablePairResult::SecondFailed);
            REQUIRE(IsEmptyFile(path));
            invalid = second; invalid.eventType = "not_a_critical_event";
            REQUIRE(journal.AppendDurablePair(first, invalid) == OmsJournal::DurablePairResult::SecondFailed);
            REQUIRE(IsEmptyFile(path));
            REQUIRE(!journal.GetHealthSnapshot().writePoisoned);
            REQUIRE(journal.AppendDurablePair(first, second) == OmsJournal::DurablePairResult::Committed);
            const auto health = journal.GetHealthSnapshot();
            REQUIRE(health.durableSyncWrites == 1);
            REQUIRE(health.criticalSyncWrites == 2);
            REQUIRE(health.criticalAsyncWrites == 0);
            REQUIRE(health.currentRecords == 2);
            std::vector<std::string> events;
            REQUIRE(journal.Replay([&](const OmsJournalEvent& event) { events.push_back(event.eventType); }) == 2);
            REQUIRE(events == std::vector<std::string>({"order_intent", "place_send_attempt"}));
        }
        REQUIRE(::unlink(path.c_str()) == 0); REQUIRE(::rmdir(directory.c_str()) == 0);
    }
    ::setenv("HEPTA_OMS_ASYNC_FLUSH", "0", 1); ::setenv("HEPTA_OMS_SYNC_CRITICAL", "1", 1);
}

void TestPairWriteAndSyncFailuresRemainUncommitted()
{
    for (auto mode : {JournalFault::SecondWriteFailure, JournalFault::TornSecondWrite, JournalFault::SyncFailure}) {
        const std::string directory = MakeTempDirectory(), path = directory + "/journal";
        {
            OmsJournal journal; REQUIRE(journal.Init(path));
            auto first = MakeCriticalEvent("intent"), second = MakeCriticalEvent("attempt");
            second.eventType = "place_send_attempt";
            JournalFault::Arm(path, mode);
            const auto result = journal.AppendDurablePair(first, second);
            JournalFault::mode.store(JournalFault::None);
            REQUIRE(result == OmsJournal::DurablePairResult::SecondFailed);
            REQUIRE(journal.GetHealthSnapshot().writePoisoned);
            REQUIRE(journal.GetHealthSnapshot().durableSyncWrites == 0);
            REQUIRE(!journal.Append(MakeCriticalEvent("must-not-follow-failure")));
        }
        {
            OmsJournal recovered;
            if (mode == JournalFault::TornSecondWrite) {
                REQUIRE(!recovered.Init(path)); // A torn suffix is not an empty journal.
            } else {
                REQUIRE(recovered.Init(path));
                int events = 0;
                const int count = recovered.Replay([&](const OmsJournalEvent&) { ++events; });
                REQUIRE(count == (mode == JournalFault::SyncFailure ? 2 : 1));
                REQUIRE(events == count); // Preserve the whole readable prefix, never erase it.
            }
        }
        REQUIRE(::unlink(path.c_str()) == 0); REQUIRE(::rmdir(directory.c_str()) == 0);
    }
}

void TestSyncPathReplacementCannotBeAcknowledged()
{
    for (bool pair : {false, true}) {
        const std::string directory = MakeTempDirectory(), path = directory + "/journal";
        {
            OmsJournal journal; REQUIRE(journal.Init(path));
            auto first = MakeCriticalEvent("intent"), second = MakeCriticalEvent("attempt");
            second.eventType = "place_send_attempt";
            JournalFault::Arm(path, JournalFault::ReplaceDuringSync);
            if (pair) REQUIRE(journal.AppendDurablePair(first, second) == OmsJournal::DurablePairResult::SecondFailed);
            else REQUIRE(!journal.Append(first));
            JournalFault::mode.store(JournalFault::None);
            REQUIRE(journal.GetHealthSnapshot().writePoisoned);
            REQUIRE(journal.GetHealthSnapshot().durableSyncWrites == 0);
            REQUIRE(IsEmptyFile(path));
        }
        REQUIRE(::unlink(path.c_str()) == 0); REQUIRE(::unlink((path + ".moved").c_str()) == 0);
        REQUIRE(::rmdir(directory.c_str()) == 0);
    }
}

void TestPairCrashCutsKeepOriginalRecords()
{
    for (auto mode : {JournalFault::CrashAfterFirstWrite, JournalFault::CrashBeforeSync}) {
        const std::string directory = MakeTempDirectory(), path = directory + "/journal";
        const pid_t child = ::fork(); REQUIRE(child >= 0);
        if (child == 0) {
            OmsJournal journal; if (!journal.Init(path)) ::_exit(90);
            auto first = MakeCriticalEvent("intent"), second = MakeCriticalEvent("attempt");
            second.eventType = "place_send_attempt";
            JournalFault::Arm(path, mode);
            journal.AppendDurablePair(first, second);
            ::_exit(99); // No successful acknowledgement is reachable at either cut.
        }
        int status = 0; REQUIRE(::waitpid(child, &status, 0) == child);
        REQUIRE(WIFEXITED(status));
        REQUIRE(WEXITSTATUS(status) == (mode == JournalFault::CrashAfterFirstWrite ? 91 : 92));
        {
            OmsJournal recovered; REQUIRE(recovered.Init(path));
            std::vector<std::string> events;
            const int count = recovered.Replay([&](const OmsJournalEvent& event) { events.push_back(event.eventType); });
            REQUIRE(count == (mode == JournalFault::CrashAfterFirstWrite ? 1 : 2));
            REQUIRE(events.front() == "order_intent");
            if (count == 2) REQUIRE(events.back() == "place_send_attempt");
        }
        REQUIRE(::unlink(path.c_str()) == 0); REQUIRE(::rmdir(directory.c_str()) == 0);
    }
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

void ClearReplayBudgets()
{
    ::unsetenv("HEPTA_OMS_REPLAY_MAX_BYTES");
    ::unsetenv("HEPTA_OMS_REPLAY_MAX_RECORDS");
    ::unsetenv("HEPTA_OMS_REPLAY_MAX_RECORD_BYTES");
}

void TestReplayLimitsAreInclusiveAndCallbackAtomic()
{
    ClearReplayBudgets();
    const std::string directory = MakeTempDirectory();
    const std::string path = directory + "/bounded.jsonl";
    const std::string record = "{\"schema_version\":4,\"event\":\"place_send_attempt\","
        "\"ts_ms\":1,\"req_id\":\"stable-identity\"}";
    const std::string bytes = record + "\n" + record + "\n";
    WritePrivateFile(path, bytes);
    const std::string byteBudget = std::to_string(bytes.size());
    const std::string recordBudget = std::to_string(record.size());
    ::setenv("HEPTA_OMS_REPLAY_MAX_BYTES", byteBudget.c_str(), 1);
    ::setenv("HEPTA_OMS_REPLAY_MAX_RECORDS", "2", 1);
    ::setenv("HEPTA_OMS_REPLAY_MAX_RECORD_BYTES", recordBudget.c_str(), 1);
    {
        OmsJournal journal;
        REQUIRE(journal.Init(path));
        int callbacks = 0;
        REQUIRE(journal.Replay([&](const OmsJournalEvent& event) {
            ++callbacks;
            REQUIRE(event.reqId == "stable-identity");
            REQUIRE(event.eventType == "place_send_attempt");
            REQUIRE(journal.GetHealthSnapshot().replayReasonCode == "OMS_REPLAY_VALIDATED");
        }) == 2);
        REQUIRE(callbacks == 2); // no deduplication or mutation identity deletion
        const OmsJournalHealthSnapshot health = journal.GetHealthSnapshot();
        REQUIRE(health.replayObservedBytes == bytes.size());
        REQUIRE(health.replayValidatedRecords == 2);
    }
    const char* variables[] = {"HEPTA_OMS_REPLAY_MAX_BYTES",
        "HEPTA_OMS_REPLAY_MAX_RECORDS", "HEPTA_OMS_REPLAY_MAX_RECORD_BYTES"};
    const char* reasons[] = {"OMS_REPLAY_BYTE_LIMIT", "OMS_REPLAY_RECORD_COUNT_LIMIT",
        "OMS_REPLAY_RECORD_BYTE_LIMIT"};
    const std::size_t exact[] = {bytes.size(), 2, record.size()};
    for (int i = 0; i < 3; ++i)
    {
        const std::string lower = std::to_string(exact[i] - 1);
        ::setenv(variables[i], lower.c_str(), 1);
        {
            OmsJournal journal;
            REQUIRE(journal.Init(path));
            int callbacks = 0;
            REQUIRE(journal.Replay([&](const OmsJournalEvent&) { ++callbacks; }) == -1);
            REQUIRE(callbacks == 0);
            REQUIRE(journal.GetHealthSnapshot().replayReasonCode == reasons[i]);
            REQUIRE(!journal.GetHealthSnapshot().writePoisoned);
        }
        ::setenv(variables[i], std::to_string(exact[i]).c_str(), 1);
    }
    ClearReplayBudgets();
    {
        OmsJournal journal;
        REQUIRE(journal.Init(path));
        REQUIRE(journal.Replay({}) == 2); // rejection never truncates the journal
    }
    REQUIRE(::unlink(path.c_str()) == 0);
    REQUIRE(::rmdir(directory.c_str()) == 0);
}

void TestMalformedReplayBudgetsDoNotCreateFiles()
{
    const char* values[] = {"", "0", "-1", "+1", " 1", "1 ", "1x", "9999999999999999999999999"};
    const char* names[] = {"HEPTA_OMS_REPLAY_MAX_BYTES", "HEPTA_OMS_REPLAY_MAX_RECORDS",
        "HEPTA_OMS_REPLAY_MAX_RECORD_BYTES"};
    const std::string directory = MakeTempDirectory();
    const std::string path = directory + "/not-created.jsonl";
    for (const char* name : names)
        for (const char* value : values)
        {
            ClearReplayBudgets();
            ::setenv(name, value, 1);
            OmsJournal journal;
            REQUIRE(!journal.Init(path));
            REQUIRE(journal.GetHealthSnapshot().replayReasonCode == "OMS_REPLAY_INVALID_BUDGET");
            REQUIRE(::access(path.c_str(), F_OK) != 0);
        }
    ClearReplayBudgets();
    REQUIRE(::rmdir(directory.c_str()) == 0);
}

void TestOversizedAndTornRecordsDoNotApplyValidPrefix()
{
    ClearReplayBudgets();
    const std::string directory = MakeTempDirectory();
    const std::string path = directory + "/prefix.jsonl";
    const std::string first = "{\"event\":\"place_send_attempt\",\"ts_ms\":1}\n";
    // A line split across more than one 8192-byte read must obey the same limit.
    ::setenv("HEPTA_OMS_REPLAY_MAX_RECORD_BYTES", "16384", 1);
    WritePrivateFile(path, first + std::string(16385, 'x') + "\n");
    {
        OmsJournal journal;
        REQUIRE(journal.Init(path));
        int callbacks = 0;
        REQUIRE(journal.Replay([&](const OmsJournalEvent&) { ++callbacks; }) == -1);
        REQUIRE(callbacks == 0);
        REQUIRE(journal.GetHealthSnapshot().replayReasonCode == "OMS_REPLAY_RECORD_BYTE_LIMIT");
    }
    WritePrivateFile(path, first);
    {
        OmsJournal journal;
        REQUIRE(journal.Init(path));
        // External truncation/partial write after Init, not a fixture that Init rejects.
        { std::ofstream file(path, std::ios::app); file << "{\"event\":"; }
        int callbacks = 0;
        REQUIRE(journal.Replay([&](const OmsJournalEvent&) { ++callbacks; }) == -1);
        REQUIRE(callbacks == 0);
    }
    ClearReplayBudgets();
    REQUIRE(::unlink(path.c_str()) == 0);
    REQUIRE(::rmdir(directory.c_str()) == 0);
}

void TestAppendRejectsOversizedRecordWithoutIdentityLoss()
{
    ClearReplayBudgets();
    const std::string directory = MakeTempDirectory();
    const std::string path = directory + "/append.jsonl";
    ::setenv("HEPTA_OMS_REPLAY_MAX_RECORD_BYTES", "2048", 1);
    {
        OmsJournal journal;
        REQUIRE(journal.Init(path));
        REQUIRE(journal.Append(MakeCriticalEvent("stable-first")));
        OmsJournalEvent large = MakeCriticalEvent("not-sent");
        large.brokerMessage.assign(4096, 'x');
        REQUIRE(!journal.Append(large));
        REQUIRE(journal.Append(MakeCriticalEvent("stable-second")));
        int index = 0;
        REQUIRE(journal.Replay([&](const OmsJournalEvent& e) {
            REQUIRE(e.reqId == (index++ == 0 ? "stable-first" : "stable-second"));
        }) == 2);
    }
    ClearReplayBudgets();
    REQUIRE(::unlink(path.c_str()) == 0);
    REQUIRE(::rmdir(directory.c_str()) == 0);
}

void TestCountBudgetBoundsManySmallEventsAndDoesNotBlockExitAppend()
{
    ClearReplayBudgets();
    const std::string directory = MakeTempDirectory();
    const std::string path = directory + "/many.jsonl";
    const std::string record = "{\"event\":\"place_send_attempt\",\"ts_ms\":1}\n";
    std::string data;
    for (int i = 0; i < 4097; ++i) data += record;
    WritePrivateFile(path, data);
    ::setenv("HEPTA_OMS_REPLAY_MAX_RECORDS", "4096", 1);
    {
        OmsJournal journal;
        REQUIRE(journal.Init(path));
        int callbacks = 0;
        REQUIRE(journal.Replay([&](const OmsJournalEvent&) { ++callbacks; }) == -1);
        REQUIRE(callbacks == 0);
        REQUIRE(journal.GetHealthSnapshot().replayValidatedRecords == 4096);
        OmsJournalEvent exit = MakeCriticalEvent("guarded-exit");
        exit.eventType = "cancel_send_attempt";
        REQUIRE(journal.Append(exit)); // budget is NOT a blanket journal write cutoff
    }
    ClearReplayBudgets();
    {
        OmsJournal journal;
        REQUIRE(journal.Init(path));
        REQUIRE(journal.Replay({}) == 4098);
    }
    REQUIRE(::unlink(path.c_str()) == 0);
    REQUIRE(::rmdir(directory.c_str()) == 0);
}

#include "oms_live_capacity_cases.h"
#include "oms_archive_cases.h"
#include "oms_queue_budget_cases.h"

}

int main()
{
    TestArchiveReplayAppendAndLogicalBudgets();
    TestArchiveCorruptionIsCallbackAtomic();
    TestArchiveMaintenanceLockExcludesRuntime();
    hepta_observation_test::Run();
    TestReplayLimitsAreInclusiveAndCallbackAtomic();
    TestMalformedReplayBudgetsDoNotCreateFiles();
    TestOversizedAndTornRecordsDoNotApplyValidPrefix();
    TestAppendRejectsOversizedRecordWithoutIdentityLoss();
    TestCountBudgetBoundsManySmallEventsAndDoesNotBlockExitAppend();
    TestPairValidationAndSingleBarrier();
    TestPairWriteAndSyncFailuresRemainUncommitted();
    TestSyncPathReplacementCannotBeAcknowledged();
    TestPairCrashCutsKeepOriginalRecords();
    TestPathReplacementPoisonsBeforeWriting();
    TestMissingPathPoisonsBeforeWriting();
    TestSymlinkReplacementPoisonsBeforeWriting();
    TestRepeatedInitFailsWithoutDisturbingOriginal();
    TestStrictReplayIsCallbackAtomicAndReentrant();
    TestAsyncQueueAndBufferAccounting();
    TestUnsafePermissionsLinksAndTornFilesFailClosed();
    TestLiveCapacityAndCheckpointRecovery();
    TestCapacityUnknownAndPendingAreNotHealthyZero();
    TestCapacitySerializationAndCadenceBoundaries();
    TestQueueBudgetsPreserveAdmittedRecordsAndCriticalExit();
    TestQueueByteBudgetAndMalformedConfiguration();
    return 0;
}
