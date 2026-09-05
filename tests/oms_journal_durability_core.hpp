#include "../HeptaTrade/oms_journal.h"

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <csignal>
#include <stdexcept>
#include <sys/resource.h>
#include <sys/wait.h>
#include <cstdlib>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <limits>
#include <locale>
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
std::vector<OmsJournalEvent> ReplayRecords(const std::string& contents)
{
    const std::string directory = MakeTempDirectory();
    const std::string path = directory + "/journal.jsonl";
    WritePrivateFile(path, contents);
    std::vector<OmsJournalEvent> events;
    {
        OmsJournal journal;
        REQUIRE(journal.Init(path));
        const int count = journal.Replay([&](const OmsJournalEvent& event) {
            events.push_back(event);
        });
        REQUIRE(count >= 0);
        REQUIRE(static_cast<std::size_t>(count) == events.size());
    }
    REQUIRE(::unlink(path.c_str()) == 0);
    REQUIRE(::rmdir(directory.c_str()) == 0);
    return events;
}

void RequireRejectedRecord(const std::string& record)
{
    const std::string directory = MakeTempDirectory();
    const std::string path = directory + "/journal.jsonl";
    // Even a valid prefix must not be published when a later record rejects.
    const std::string contents = "{\"event\":\"prefix\"}\n" + record + "\n";
    WritePrivateFile(path, contents);
    {
        OmsJournal journal;
        REQUIRE(journal.Init(path));
        unsigned int callbacks = 0;
        REQUIRE(journal.Replay([&](const OmsJournalEvent&) { ++callbacks; }) == -1);
        REQUIRE(callbacks == 0);
    }
    std::ifstream saved(path, std::ios::binary);
    REQUIRE(std::string(std::istreambuf_iterator<char>(saved), {}) == contents);
    saved.close();
    REQUIRE(::unlink(path.c_str()) == 0);
    REQUIRE(::rmdir(directory.c_str()) == 0);
}

void TestTypedWhitespaceUnicodeAndHistoricalVersions()
{
    const auto events = ReplayRecords(
        "{\"event\":\"fill\",\"qty\":5.25,\"order_id\":123,\"req_id\":\"A\"}\n"
        " { \"order_id\" :\t123 , \"qty\" : 5.25 , \"ev\\u0065nt\" : \"fill\","
        " \"client_req_id\" : \"\\u0041\" } \r\n"
        "{\"event\":\"fill\",\"broker_message\":\"\\u4ea4\\u6613 \\ud83d\\ude00\"}\n"
        "{\"event\":\"fill\",\"broker_message\":\"交易 😀\"}\n");
    REQUIRE(events.size() == 4);
    for (std::size_t index = 0; index < 2; ++index)
    {
        REQUIRE(events[index].eventType == "fill");
        REQUIRE(events[index].qty == 5.25);
        REQUIRE(events[index].orderId == 123);
        REQUIRE(events[index].reqId == "A");
        REQUIRE(events[index].clientReqId == "A");
        REQUIRE(events[index].schemaVersion == 1);
    }
    REQUIRE(events[2].brokerMessage == events[3].brokerMessage);
    REQUIRE(events[2].brokerMessage == "交易 😀");
    const auto controls = ReplayRecords(
        "{\"event\":\"fill\",\"broker_message\":\"\\u0000\\b\\f\\n\\r\\t\\\\\\\"\\/\"}\n");
    REQUIRE(controls[0].brokerMessage == std::string("\0\b\f\n\r\t\\\"/", 9));
    for (int version = 1; version <= 4; ++version)
    {
        const auto old = ReplayRecords("{\"schema_version\":" + std::to_string(version) +
            ",\"event\":\"status\",\"client_req_id\":\"legacy\"}\n");
        REQUIRE(old[0].schemaVersion == version);
        REQUIRE(old[0].qty == 0 && old[0].price == 0 && old[0].orderId == -1);
        REQUIRE(old[0].reqId == "legacy" && old[0].clientReqId == "legacy");
    }
}

void TestInvalidFieldsNeverBecomeDefaults()
{
    const std::vector<std::string> records = {
        R"({"event":"fill","qty": 1e999})",
        R"({"event":"fill","qty": 1e-999})",
        R"({"event":"fill","qty": null})",
        R"({"event":"fill","qty": "5"})",
        R"({"event":"fill","qty": true})",
        R"({"event":"fill","qty": [5]})",
        R"({"event":"fill","qty": {"qty":5}})",
        R"({"event":"fill","qty": 5,"qty":6})",
        R"({"event":"fill","qty": 5,"q\u0074y":6})",
        R"({"event":"fill","event":"other"})",
        R"({"event": null})", R"({"event": ""})",
        R"({"event":"fill","unknown":{"order_id":99}})",
        R"({"event":"fill","quantitiy":5})",
        R"({"event":"fill","order_id":9223372036854775808})",
        R"({"event":"fill","order_id":-9223372036854775809})",
        R"({"event":"fill","order_id":1.5})",
        R"({"event":"fill","order_id":1e2})",
        R"({"event":"fill","ts_ms":"123"})",
        R"({"event":"fill","broker_connection_epoch":18446744073709551616})",
        R"({"event":"fill","broker_connection_epoch":-1})",
        R"({"event":"fill","broker_error_code":2147483648})",
        R"({"event":"fill","schema_version":0})",
        R"({"event":"fill","schema_version":5})",
        R"({"event":"fill","schema_version":"4"})",
        R"({"event":"fill","req_id":"first","client_req_id":"second"})",
        R"({"event":"fill","broker_message":"\ud800"})",
        R"({"event":"fill","broker_message":"\udc00"})",
        R"({"event":"fill","broker_message":"\ud800\u0041"})",
        R"({"event":"fill","qty":01})", R"({"event":"fill","qty":+1})",
        R"({"event":"fill","qty":1.})", R"({"event":"fill","qty":1e})",
        R"({"event":"fill","qty":NaN})", R"({"event":"fill","qty":Infinity})",
        R"({"event":"fill"} trailing)", R"({"event":"fill",})",
        R"([{"event":"fill"}])", R"({"event":"fill","broker_message":"\x41"})"
    };
    for (const auto& record : records) RequireRejectedRecord(record);
    for (const std::string& bytes : {std::string("\xc0\xaf", 2), std::string("\xed\xa0\x80", 3),
                                   std::string("\xf4\x90\x80\x80", 4), std::string("\xff", 1),
                                   std::string("\xe2\x82", 2), std::string("\x01", 1)})
        RequireRejectedRecord("{\"event\":\"fill\",\"broker_message\":\"" + bytes + "\"}");
}

void TestEveryPhysicalFieldRejectsDuplicateKeys()
{
    const char* const names[] = {
        "event", "req_id", "client_req_id", "trace_id", "event_id", "risk_code", "venue",
        "strategy", "account", "execution_domain", "request_hash", "venue_correlation_id",
        "broker_callback_type", "broker_service_epoch", "broker_message",
        "broker_advanced_order_reject_json", "broker_why_held", "broker_execution_id",
        "instrument", "side", "status", "reason", "source"
    };
    for (const char* name : names)
    {
        const std::string first = std::string(name) == "event" ? "{" : "{\"event\":\"fill\",";
        RequireRejectedRecord(first + "\"" + name + "\":\"x\",\"" + name + "\":\"x\"}");
    }
    for (const char* name : {"schema_version", "ts_ms", "order_id", "broker_connection_epoch",
                            "broker_request_id", "broker_error_code", "qty", "price",
                            "broker_remaining_quantity", "broker_market_cap_price"})
        RequireRejectedRecord("{\"event\":\"fill\",\"" + std::string(name) +
                              "\":1,\"" + name + "\":1}");
}

void TestAllTextFieldsAndIntegerLimitsRoundTrip()
{
    const std::string directory = MakeTempDirectory();
    const std::string path = directory + "/journal.jsonl";
    std::string allControls;
    for (char value = 0; value < 32; ++value) allControls += value;
    const std::string sample = allControls + "交易 😀 \\\" /";
    OmsJournalEvent event = MakeCriticalEvent("identity");
    std::string OmsJournalEvent::* const fields[] = {
        &OmsJournalEvent::instrument, &OmsJournalEvent::side, &OmsJournalEvent::status,
        &OmsJournalEvent::reason, &OmsJournalEvent::source, &OmsJournalEvent::traceId,
        &OmsJournalEvent::riskCode, &OmsJournalEvent::venue, &OmsJournalEvent::strategy,
        &OmsJournalEvent::account, &OmsJournalEvent::eventId, &OmsJournalEvent::executionDomain,
        &OmsJournalEvent::requestHash, &OmsJournalEvent::venueCorrelationId,
        &OmsJournalEvent::brokerCallbackType, &OmsJournalEvent::brokerServiceEpoch,
        &OmsJournalEvent::brokerMessage, &OmsJournalEvent::brokerAdvancedOrderRejectJson,
        &OmsJournalEvent::brokerWhyHeld, &OmsJournalEvent::brokerExecutionId
    };
    // Preserve the existing writer's explicit C0-to-space compatibility policy.
    std::string normalizedControls(32, ' ');
    normalizedControls[9] = '\t';
    normalizedControls[10] = '\n';
    normalizedControls[13] = '\r';
    unsigned int index = 0;
    for (auto field : fields) event.*field = std::to_string(index++) + sample;
    event.tsMs = std::numeric_limits<long long>::min();
    event.orderId = std::numeric_limits<long>::max();
    event.brokerConnectionEpoch = std::numeric_limits<std::uint64_t>::max();
    event.brokerRequestId = std::numeric_limits<long long>::max();
    event.brokerErrorCode = std::numeric_limits<int>::min();
    event.qty = 5.25;
    event.price = -1.5;
    event.brokerRemainingQuantity = 2.75;
    event.brokerMarketCapPrice = 100.125;
    {
        OmsJournal journal;
        REQUIRE(journal.Init(path));
        REQUIRE(journal.Append(event));
    }
    {
        OmsJournal journal;
        REQUIRE(journal.Init(path));
        REQUIRE(journal.Replay([&](const OmsJournalEvent& restored) {
            unsigned int restoredIndex = 0;
            for (auto field : fields)
                REQUIRE(restored.*field == std::to_string(restoredIndex++) +
                        normalizedControls + sample.substr(32));
            REQUIRE(restored.eventType == event.eventType);
            REQUIRE(restored.reqId == event.reqId && restored.clientReqId == event.reqId);
            REQUIRE(restored.tsMs == event.tsMs && restored.orderId == event.orderId);
            REQUIRE(restored.brokerConnectionEpoch == event.brokerConnectionEpoch);
            REQUIRE(restored.brokerRequestId == event.brokerRequestId);
            REQUIRE(restored.brokerErrorCode == event.brokerErrorCode);
            REQUIRE(restored.qty == event.qty && restored.price == event.price);
            REQUIRE(restored.brokerRemainingQuantity == event.brokerRemainingQuantity);
            REQUIRE(restored.brokerMarketCapPrice == event.brokerMarketCapPrice);
        }) == 1);
    }
    REQUIRE(::unlink(path.c_str()) == 0);
    REQUIRE(::rmdir(directory.c_str()) == 0);
}

void TestRecordSizeBoundariesAndRejectedAppendPreserveState()
{
    const std::string prefix = "{\"event\":\"fill\",\"broker_message\":\"";
    const std::size_t limit = OmsJournal::kMaximumRecordBytes;
    const std::string exact = prefix + std::string(limit - prefix.size() - 2, 'x') + "\"}";
    const auto events = ReplayRecords(exact + "\n");
    REQUIRE(events.size() == 1 && events[0].rawLine.size() == limit);
    RequireRejectedRecord(exact + " ");

    const std::string directory = MakeTempDirectory();
    const std::string path = directory + "/journal.jsonl";
    ::setenv("HEPTA_OMS_ASYNC_FLUSH", "0", 1);
    ::setenv("HEPTA_OMS_BATCH_SIZE", "64", 1);
    ::setenv("HEPTA_OMS_FLUSH_INTERVAL_MS", "9223372036854775807", 1);
    {
        OmsJournal journal;
        REQUIRE(journal.Init(path));
        OmsJournalEvent before = MakeCriticalEvent("before");
        before.eventType = "ack";
        REQUIRE(journal.Append(before));
        REQUIRE(journal.GetHealthSnapshot().bufferedDepth == 1);
        OmsJournalEvent bad = MakeCriticalEvent("bad");
        bad.brokerMessage.assign(limit + 1, 'x');
        REQUIRE(!journal.Append(bad));
        bad.brokerMessage = std::string("\xff", 1);
        REQUIRE(!journal.Append(bad));
        bad.brokerMessage.clear();
        bad.schemaVersion = 5;
        REQUIRE(!journal.Append(bad));
        bad.schemaVersion = 4;
        bad.qty = std::numeric_limits<double>::infinity();
        REQUIRE(!journal.Append(bad));
        const auto unchanged = journal.GetHealthSnapshot();
        REQUIRE(unchanged.bufferedDepth == 1 && unchanged.flushedTotal == 0);
        REQUIRE(!unchanged.writePoisoned && IsEmptyFile(path));
        REQUIRE(journal.Append(MakeCriticalEvent("after")));
        std::vector<std::string> ids;
        REQUIRE(journal.Replay([&](const OmsJournalEvent& item) { ids.push_back(item.reqId); }) == 2);
        REQUIRE(ids[0] == "before" && ids[1] == "after");
    }
    REQUIRE(::unlink(path.c_str()) == 0);
    REQUIRE(::rmdir(directory.c_str()) == 0);
    ::setenv("HEPTA_OMS_BATCH_SIZE", "8", 1);
    ::setenv("HEPTA_OMS_FLUSH_INTERVAL_MS", "250", 1);
}

void TestNumericTokensAreLocaleIndependent()
{
    struct CommaDecimal : std::numpunct<char>
    {
        char do_decimal_point() const override { return ','; }
    };
    const std::locale previous = std::locale();
    std::locale::global(std::locale(previous, new CommaDecimal));
    const auto values = ReplayRecords(
        "{\"event\":\"fill\",\"qty\":5.25e1,\"price\":-0.125e+2,"
        "\"broker_connection_epoch\":18446744073709551615}\n");
    std::locale::global(previous);
    REQUIRE(values[0].qty == 52.5 && values[0].price == -12.5);
    REQUIRE(values[0].brokerConnectionEpoch == std::numeric_limits<std::uint64_t>::max());
    const auto limits = ReplayRecords(
        "{\"event\":\"fill\",\"broker_error_code\":2147483647,"
        "\"ts_ms\":9223372036854775807,\"broker_request_id\":-9223372036854775808,"
        "\"broker_connection_epoch\":0,\"qty\":-0.0,\"price\":1e-300}\n");
    REQUIRE(limits[0].tsMs == std::numeric_limits<long long>::max());
    REQUIRE(limits[0].brokerRequestId == std::numeric_limits<long long>::min());
    REQUIRE(limits[0].brokerErrorCode == std::numeric_limits<int>::max());
    REQUIRE(limits[0].qty == 0 && limits[0].price == 1e-300);
}

void ResetBudgetTestEnvironment(bool async = false)
{
    REQUIRE(::setenv("HEPTA_OMS_ASYNC_FLUSH", async ? "1" : "0", 1) == 0);
    REQUIRE(::setenv("HEPTA_OMS_SYNC_CRITICAL", "1", 1) == 0);
    REQUIRE(::setenv("HEPTA_OMS_BATCH_SIZE", "64", 1) == 0);
    REQUIRE(::setenv("HEPTA_OMS_FLUSH_INTERVAL_MS", "9223372036854775807", 1) == 0);
}

OmsJournalEvent BudgetEvent(const std::string& id)
{
    auto event = MakeCriticalEvent(id);
    event.eventType = "ack";
    event.tsMs = 100;
    return event;
}

std::string ReadFileBytes(const std::string& path)
{
    std::ifstream file(path, std::ios::binary);
    REQUIRE(file.is_open());
    return std::string(std::istreambuf_iterator<char>(file), {});
}

std::size_t EncodedBudgetEventBytes(const std::string& path)
{
    {
        OmsJournal journal;
        REQUIRE(journal.Init(path));
        REQUIRE(journal.Append(BudgetEvent("q0")));
        REQUIRE(journal.Replay({}) == 1);
    }
    const auto result = ReadFileBytes(path).size();
    REQUIRE(result > 0);
    REQUIRE(::unlink(path.c_str()) == 0);
    return result;
}

void TestQueueRecordBudgetAndCriticalDrain()
{
    ResetBudgetTestEnvironment();
    const auto directory = MakeTempDirectory();
    const auto path = directory + "/records";
    OmsJournalLimits limits;
    limits.maximumQueuedRecords = 2;
    {
        OmsJournal journal(limits);
        REQUIRE(journal.Init(path));
        REQUIRE(journal.Append(BudgetEvent("q0")));
        REQUIRE(journal.Append(BudgetEvent("q1")));
        const auto full = journal.GetHealthSnapshot();
        REQUIRE(full.bufferedDepth == 2 && full.queueDepth == 0);
        REQUIRE(full.retainedBytes > 0 && full.queueCapacityRejects == 0);
        REQUIRE(!journal.Append(BudgetEvent("q2")));
        const auto rejected = journal.GetHealthSnapshot();
        REQUIRE(rejected.bufferedDepth == 2 && rejected.retainedBytes == full.retainedBytes);
        REQUIRE(rejected.enqueuedTotal == 2 && rejected.flushedTotal == 0);
        REQUIRE(rejected.queueCapacityRejects == 1 && !rejected.writePoisoned);
        REQUIRE(IsEmptyFile(path));
        // Critical synchronous records drain older work without needing a
        // third queue slot. Queue saturation does not remove that path.
        REQUIRE(journal.Append(MakeCriticalEvent("critical")));
        const auto drained = journal.GetHealthSnapshot();
        REQUIRE(drained.retainedBytes == 0 && drained.bufferedDepth == 0);
        REQUIRE(drained.flushedTotal == 3 && drained.durableSyncWrites == 1);
        REQUIRE(journal.Append(BudgetEvent("q3")));
        std::vector<std::string> ids;
        REQUIRE(journal.Replay([&](const auto& event) { ids.push_back(event.reqId); }) == 4);
        REQUIRE((ids == std::vector<std::string>{"q0", "q1", "critical", "q3"}));
        REQUIRE(journal.GetHealthSnapshot().retainedBytes == 0);
    }
    REQUIRE(::unlink(path.c_str()) == 0);
    REQUIRE(::rmdir(directory.c_str()) == 0);
}

void TestQueueByteBudgetEndpointsAndAsyncRejection()
{
    ResetBudgetTestEnvironment();
    const auto directory = MakeTempDirectory();
    const auto bytes = EncodedBudgetEventBytes(directory + "/measure");
    const auto path = directory + "/bytes";
    for (std::size_t capacity : {bytes - 1, bytes, bytes * 2 - 1, bytes * 2})
    {
        OmsJournalLimits limits;
        limits.maximumQueuedBytes = capacity;
        {
            OmsJournal journal(limits);
            REQUIRE(journal.Init(path));
            REQUIRE(journal.Append(BudgetEvent("q0")) == (capacity >= bytes));
            REQUIRE(journal.Append(BudgetEvent("q1")) == (capacity >= bytes * 2));
            const auto state = journal.GetHealthSnapshot();
            REQUIRE(state.retainedBytes == (capacity / bytes) * bytes);
            REQUIRE(state.queueCapacityRejects == 2 - capacity / bytes);
            REQUIRE(state.retainedBytes <= state.limits.maximumQueuedBytes);
            REQUIRE(journal.Replay({}) == static_cast<int>(capacity / bytes));
            REQUIRE(journal.GetHealthSnapshot().retainedBytes == 0);
        }
        REQUIRE(::unlink(path.c_str()) == 0);
    }
    // This async rejection is deterministic: one record cannot fit even in
    // an empty queue. No timing assumption about the worker is necessary.
    ResetBudgetTestEnvironment(true);
    {
        OmsJournalLimits limits;
        limits.maximumQueuedBytes = bytes - 1;
        OmsJournal journal(limits);
        REQUIRE(journal.Init(path));
        REQUIRE(!journal.Append(BudgetEvent("q0")));
        REQUIRE(journal.GetHealthSnapshot().retainedBytes == 0);
        REQUIRE(journal.Append(MakeCriticalEvent("critical")));
        REQUIRE(journal.Replay({}) == 1);
    }
    REQUIRE(::unlink(path.c_str()) == 0);
    REQUIRE(::rmdir(directory.c_str()) == 0);
    ResetBudgetTestEnvironment();
}

void TestInvalidLimitsRejectBeforeFileCreationAndBatchCannotReserveUnbounded()
{
    ResetBudgetTestEnvironment();
    const auto directory = MakeTempDirectory();
    const auto path = directory + "/invalid";
    std::size_t OmsJournalLimits::* const fields[] = {
        &OmsJournalLimits::maximumQueuedRecords, &OmsJournalLimits::maximumQueuedBytes,
        &OmsJournalLimits::maximumReplayRecords, &OmsJournalLimits::maximumReplayBytes
    };
    for (auto field : fields)
        for (std::size_t value : {std::size_t{0}, OmsJournalLimits{}.*field + 1,
                                  std::numeric_limits<std::size_t>::max()})
        {
            OmsJournalLimits limits;
            limits.*field = value;
            OmsJournal journal(limits);
            REQUIRE(!journal.Init(path));
            REQUIRE(::access(path.c_str(), F_OK) != 0);
        }
    REQUIRE(::setenv("HEPTA_OMS_BATCH_SIZE", "18446744073709551615", 1) == 0);
    {
        OmsJournalLimits limits;
        limits.maximumQueuedRecords = 1;
        OmsJournal journal(limits);
        limits.maximumQueuedRecords = 0; // Construction takes an immutable copy.
        // A legacy batch threshold must not request SIZE_MAX string slots.
        REQUIRE(journal.Init(path));
        REQUIRE(journal.Append(BudgetEvent("q0")));
        REQUIRE(!journal.Append(BudgetEvent("q1")));
        REQUIRE(journal.Replay({}) == 1);
    }
    REQUIRE(::unlink(path.c_str()) == 0);
    REQUIRE(::rmdir(directory.c_str()) == 0);
    ResetBudgetTestEnvironment();
}

void TestReplayBudgetsAndCallbackAtomicRejection()
{
    ResetBudgetTestEnvironment();
    const auto directory = MakeTempDirectory();
    const auto path = directory + "/replay";
    const std::string record = "{\"event\":\"fill\"}\n";
    const std::string contents = record + record + record;
    WritePrivateFile(path, contents);
    for (std::size_t records : {std::size_t{2}, std::size_t{3}})
        for (std::size_t bytes : {contents.size() - 1, contents.size()})
        {
            OmsJournalLimits limits;
            limits.maximumReplayRecords = records;
            limits.maximumReplayBytes = bytes;
            OmsJournal journal(limits);
            REQUIRE(journal.Init(path));
            int callbacks = 0;
            const bool allowed = records == 3 && bytes == contents.size();
            for (int repeat = 0; repeat < 2; ++repeat)
            {
                REQUIRE(journal.Replay([&](const auto&) { ++callbacks; }) == (allowed ? 3 : -1));
                REQUIRE(callbacks == (allowed ? 3 * (repeat + 1) : 0));
                const auto health = journal.GetHealthSnapshot();
                REQUIRE(health.replayCapacityRejects == (allowed ? 0 : static_cast<unsigned>(repeat + 1)));
                REQUIRE(!health.writePoisoned && health.replayBusyRejects == 0);
            }
            REQUIRE(ReadFileBytes(path) == contents);
        }
    REQUIRE(::unlink(path.c_str()) == 0);
    REQUIRE(::rmdir(directory.c_str()) == 0);
}

void TestReplayReservationRejectsNestedAndOverlappingBatches()
{
    ResetBudgetTestEnvironment();
    const auto directory = MakeTempDirectory();
    const auto path = directory + "/single-replay";
    {
        OmsJournal journal;
        REQUIRE(journal.Init(path));
        REQUIRE(journal.Append(MakeCriticalEvent("before")));
        bool entered = false;
        bool release = false;
        std::mutex mutex;
        std::condition_variable condition;
        std::thread reader([&]() {
            REQUIRE(journal.Replay([&](const auto&) {
                // Nested replay is rejected, while ordinary diagnostic
                // access is still safe outside the journal mutex.
                REQUIRE(journal.Replay({}) == -1);
                REQUIRE(!journal.GetHealthSnapshot().writePoisoned);
                std::unique_lock<std::mutex> lock(mutex);
                entered = true;
                condition.notify_all();
                condition.wait(lock, [&]() { return release; });
            }) == 1);
        });
        {
            std::unique_lock<std::mutex> lock(mutex);
            REQUIRE(condition.wait_for(lock, std::chrono::seconds(5), [&]() { return entered; }));
        }
        REQUIRE(journal.Replay({}) == -1);
        REQUIRE(journal.Append(MakeCriticalEvent("after")));
        {
            std::lock_guard<std::mutex> lock(mutex);
            release = true;
            condition.notify_all();
        }
        reader.join();
        REQUIRE(journal.Replay({}) == 2);
        bool thrown = false;
        try { journal.Replay([](const auto&) { throw std::runtime_error("callback failure"); }); }
        catch (const std::runtime_error&) { thrown = true; }
        REQUIRE(thrown);
        REQUIRE(journal.Replay({}) == 2); // Reservation released on exception.
        REQUIRE(journal.GetHealthSnapshot().replayBusyRejects == 2);
    }
    REQUIRE(::unlink(path.c_str()) == 0);
    REQUIRE(::rmdir(directory.c_str()) == 0);
}

// The watchdog detects a hung fixture; it is not a target-host latency SLA.
void RequireChildCompletes(const std::function<void()>& body)
{
    const pid_t child = ::fork();
    REQUIRE(child >= 0);
    if (child == 0) { body(); ::_exit(0); }
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);
    int status = 0;
    while (std::chrono::steady_clock::now() < deadline)
    {
        const pid_t result = ::waitpid(child, &status, WNOHANG);
        if (result == child)
        {
            REQUIRE(WIFEXITED(status) && WEXITSTATUS(status) == 0);
            return;
        }
        REQUIRE(result == 0 || (result == -1 && errno == EINTR));
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    ::kill(child, SIGKILL);
    ::waitpid(child, &status, 0);
    REQUIRE(false);
}

void RestrictFileGrowth(rlim_t bytes)
{
    struct rlimit limits;
    REQUIRE(::getrlimit(RLIMIT_FSIZE, &limits) == 0);
    limits.rlim_cur = bytes;
    REQUIRE(::setrlimit(RLIMIT_FSIZE, &limits) == 0);
    REQUIRE(::signal(SIGXFSZ, SIG_IGN) != SIG_ERR);
}

void TestPartialFlushRetainsOnlyUnconfirmedRecords()
{
    ResetBudgetTestEnvironment();
    const auto directory = MakeTempDirectory();
    const auto path = directory + "/partial";
    const auto bytes = EncodedBudgetEventBytes(directory + "/measure");
    RequireChildCompletes([&]() {
        OmsJournal journal;
        REQUIRE(journal.Init(path));
        REQUIRE(journal.Append(BudgetEvent("q0")));
        REQUIRE(journal.Append(BudgetEvent("q1")));
        REQUIRE(journal.Append(BudgetEvent("q2")));
        RestrictFileGrowth(bytes + bytes / 2);
        REQUIRE(journal.Replay({}) == -1);
        const auto health = journal.GetHealthSnapshot();
        REQUIRE(health.writePoisoned && health.flushedTotal == 1);
        REQUIRE(health.bufferedDepth == 2 && health.retainedBytes == 2 * bytes);
        REQUIRE(!journal.Append(MakeCriticalEvent("must-not-retry")));
    });
    const auto contents = ReadFileBytes(path);
    REQUIRE(contents.size() == bytes + bytes / 2);
    REQUIRE(contents[bytes - 1] == '\n' && contents.back() != '\n');
    REQUIRE(::unlink(path.c_str()) == 0);
    REQUIRE(::rmdir(directory.c_str()) == 0);
}

void TestAsyncWriteFailureStopsWorkerWithoutLockSpin()
{
    ResetBudgetTestEnvironment(true);
    REQUIRE(::setenv("HEPTA_OMS_BATCH_SIZE", "1", 1) == 0);
    const auto directory = MakeTempDirectory();
    const auto path = directory + "/worker-failed";
    RequireChildCompletes([&]() {
        OmsJournal journal;
        REQUIRE(journal.Init(path));
        RestrictFileGrowth(1);
        REQUIRE(journal.Append(BudgetEvent("q0")));
        OmsJournalHealthSnapshot health;
        do
        {
            health = journal.GetHealthSnapshot();
            std::this_thread::yield();
        } while (!health.workerStoppedOnFailure);
        REQUIRE(health.writePoisoned && health.writeFailTotal == 1);
        REQUIRE(health.queueDepth + health.bufferedDepth == 1 && health.retainedBytes > 1);
        REQUIRE(!journal.Append(MakeCriticalEvent("no-retry")));
        REQUIRE(journal.Replay({}) == -1);
        REQUIRE(journal.GetHealthSnapshot().writeFailTotal == 1);
        // The actual destructor runs before child exit and must join the worker.
    });
    REQUIRE(ReadFileBytes(path).size() == 1);
    REQUIRE(::unlink(path.c_str()) == 0);
    REQUIRE(::rmdir(directory.c_str()) == 0);
    ResetBudgetTestEnvironment();
}

void TestConcurrentAsyncQueueAccounting()
{
    ResetBudgetTestEnvironment(true);
    const auto directory = MakeTempDirectory();
    const auto path = directory + "/concurrent";
    {
        OmsJournalLimits limits;
        limits.maximumQueuedRecords = 4;
        limits.maximumQueuedBytes = 8192;
        OmsJournal journal(limits);
        REQUIRE(journal.Init(path));
        std::atomic<unsigned int> accepted{0}, rejected{0};
        std::vector<std::vector<std::string>> acceptedIds(4);
        std::vector<std::thread> producers;
        for (int worker = 0; worker < 4; ++worker)
            producers.emplace_back([&, worker]() {
                for (int index = 0; index < 200; ++index)
                {
                    const auto id = std::to_string(worker) + ":" + std::to_string(index);
                    if (journal.Append(BudgetEvent(id)))
                    {
                        ++accepted;
                        acceptedIds[worker].push_back(id);
                    }
                    else ++rejected;
                    const auto health = journal.GetHealthSnapshot();
                    REQUIRE(health.queueDepth + health.bufferedDepth <= 4);
                    REQUIRE(health.retainedBytes <= 8192 && !health.writePoisoned);
                }
            });
        for (auto& thread : producers) thread.join();
        REQUIRE(accepted + rejected == 800);
        std::vector<std::string> expected, replayed;
        for (const auto& ids : acceptedIds) expected.insert(expected.end(), ids.begin(), ids.end());
        REQUIRE(journal.Replay([&](const auto& event) { replayed.push_back(event.reqId); }) ==
                static_cast<int>(accepted.load()));
        std::sort(expected.begin(), expected.end());
        std::sort(replayed.begin(), replayed.end());
        REQUIRE(expected == replayed);
        const auto health = journal.GetHealthSnapshot();
        REQUIRE(health.retainedBytes == 0 && health.queueDepth == 0 && health.bufferedDepth == 0);
        REQUIRE(health.enqueuedTotal == accepted && health.flushedTotal == accepted);
        REQUIRE(health.queueCapacityRejects == rejected);
    }
    REQUIRE(::unlink(path.c_str()) == 0);
    REQUIRE(::rmdir(directory.c_str()) == 0);
    ResetBudgetTestEnvironment();
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
    TestTypedWhitespaceUnicodeAndHistoricalVersions();
    TestInvalidFieldsNeverBecomeDefaults();
    TestEveryPhysicalFieldRejectsDuplicateKeys();
    TestAllTextFieldsAndIntegerLimitsRoundTrip();
    TestRecordSizeBoundariesAndRejectedAppendPreserveState();
    TestNumericTokensAreLocaleIndependent();
    TestQueueRecordBudgetAndCriticalDrain();
    TestQueueByteBudgetEndpointsAndAsyncRejection();
    TestInvalidLimitsRejectBeforeFileCreationAndBatchCannotReserveUnbounded();
    TestReplayBudgetsAndCallbackAtomicRejection();
    TestReplayReservationRejectsNestedAndOverlappingBatches();
    TestPartialFlushRetainsOnlyUnconfirmedRecords();
    TestAsyncWriteFailureStopsWorkerWithoutLockSpin();
    TestConcurrentAsyncQueueAccounting();
    return 0;
}
