#include "../HeptaTrade/oms_journal.h"

#include <cstdlib>
#include <fstream>
#include <iostream>
#include <limits>
#include <locale>
#include <string>
#include <sys/stat.h>
#include <unistd.h>

namespace {
void Require(bool condition, const char* expression, int line)
{
    if (condition) return;
    std::cerr << "requirement failed at line " << line << ": "
              << expression << '\n';
    std::abort();
}
#define REQUIRE(expression) Require(static_cast<bool>(expression), #expression, __LINE__)

std::string MakeTempDirectory()
{
    char pattern[] = "/tmp/hepta-oms-v4-XXXXXX";
    char* directory = ::mkdtemp(pattern);
    REQUIRE(directory != nullptr);
    return directory;
}

void ReplayRecord(const std::string& record, bool valid,
                  const std::function<void(const OmsJournalEvent&)>& inspect = {})
{
    const std::string directory = MakeTempDirectory();
    const std::string path = directory + "/journal.jsonl";
    {
        std::ofstream output(path.c_str(), std::ios::binary);
        REQUIRE(output.is_open());
        // A bad later record must prevent callbacks for the valid prefix too.
        output << "{\"event\":\"prefix\"}\n";
        for (char value : record) output << (value == '\n' ? ' ' : value);
        output << '\n';
        output.close();
        REQUIRE(output.good());
        REQUIRE(::chmod(path.c_str(), 0600) == 0);
        OmsJournal journal;
        REQUIRE(journal.Init(path));
        int callbacks = 0;
        const int result = journal.Replay([&](const OmsJournalEvent& event) {
            if (++callbacks == 2 && inspect) inspect(event);
        });
        if (result != (valid ? 2 : -1)) std::cerr << "record: " << record << '\n';
        REQUIRE(result == (valid ? 2 : -1));
        REQUIRE(callbacks == (valid ? 2 : 0));
    }
    REQUIRE(::unlink(path.c_str()) == 0);
    REQUIRE(::rmdir(directory.c_str()) == 0);
}

void TestStrictTypedReplay()
{
    // A known event name from an unsupported future schema must not be
    // promoted into current mutation semantics, even after a valid prefix.
    ReplayRecord(R"({"schema_version":5,"event":"place_sent","order_id":17})", false);
    ReplayRecord(R"({"schema_version":2147483647,"event":"place_sent","order_id":17})", false);
    const char* integerFields[] = {
        "schema_version", "ts_ms", "order_id", "broker_connection_epoch",
        "broker_request_id", "broker_error_code"
    };
    const char* invalidIntegers[] = {
        "1e4", "1.5", "1.0", "null", "true", "\"123\"", "[]", "{}",
        "18446744073709551616", "-9223372036854775809"
    };
    for (const char* field : integerFields)
        for (const char* value : invalidIntegers)
            ReplayRecord(std::string("{\"event\":\"place_sent\",\"") +
                         field + "\":" + value + "}", false);
    const char* floatingFields[] = {
        "qty", "price", "broker_remaining_quantity", "broker_market_cap_price"
    };
    const char* invalidNumbers[] = {
        "1e309", "-1e309", "1e-9999", "null", "true", "\"1.25\"", "[]", "{}"
    };
    for (const char* field : floatingFields)
        for (const char* value : invalidNumbers)
            ReplayRecord(std::string("{\"event\":\"place_sent\",\"") +
                         field + "\":" + value + "}", false);
    const char* stringFields[] = {
        "event", "req_id", "client_req_id", "trace_id", "event_id", "risk_code",
        "venue", "strategy", "account", "execution_domain", "request_hash",
        "venue_correlation_id", "broker_callback_type", "broker_service_epoch",
        "broker_message", "broker_advanced_order_reject_json", "broker_why_held",
        "broker_execution_id", "instrument", "side", "status", "reason", "source"
    };
    for (const char* field : stringFields)
        ReplayRecord(std::string("{\"event\":\"place_sent\",\"") +
                     field + "\":null}", false);
    const char* corruptRecords[] = {
        R"({"event":"place_sent","order_id":9223372036854775808})",
        R"({"event":"place_sent","ts_ms":9223372036854775808})",
        R"({"event":"place_sent","broker_request_id":9223372036854775808})",
        R"({"event":"place_sent","schema_version":2147483648})",
        R"({"event":"place_sent","broker_error_code":2147483648})",
        R"({"event":"place_sent","broker_error_code":-2147483649})",
        R"({"event":"place_sent","broker_connection_epoch":-1})",
        R"({"event":"place_sent","schema_version":0})",
        R"({"event":"place_sent","event":"cancel"})",
        R"({"event":"place_sent","\u0065vent":"cancel"})",
        R"({"event":"place_sent","qty":1,"qty":2})",
        R"({"event":"place_sent","extension":{"x":1,"\u0078":2}})",
        R"({"nested":{"event":"place_sent"}})",
        R"({"event":"place_sent","req_id":"\ud800"})",
        R"({"event":"place_sent","req_id":"\udc00"})",
        R"({"event":"place_sent","req_id":"\ud800\u0041"})"
    };
    for (const char* record : corruptRecords) ReplayRecord(record, false);
    ReplayRecord(std::string("{\"event\":\"place_sent\",\"req_id\":\"") +
                 "\xc0\x80\"}", false);

    ReplayRecord(R"({"nested":{"event":"cancel","order_id":7,"qty":99},
        "\u0065vent" : "place_sent", "order_id" : 10000, "qty" : 1.25e2,
        "req_id" : "cmd\u002d\u4e2d\ud83d\ude80\/\b\f"})", true,
        [](const OmsJournalEvent& event) {
            REQUIRE(event.eventType == "place_sent");
            REQUIRE(event.orderId == 10000);
            REQUIRE(event.qty == 125);
            REQUIRE(event.reqId == "cmd-\xe4\xb8\xad\xf0\x9f\x9a\x80/\b\f");
            REQUIRE(event.clientReqId == event.reqId);
            REQUIRE(event.schemaVersion == 1);
            REQUIRE(event.brokerConnectionEpoch == 0);
        });
    ReplayRecord(R"({"event":"place_sent","client_req_id":"legacy","extension":[1,{"order_id":7}]})",
        true, [](const OmsJournalEvent& event) {
            REQUIRE(event.reqId == "legacy");
            REQUIRE(event.orderId == -1);
            REQUIRE(event.qty == 0);
        });
    ReplayRecord(R"({"event":"broker_execution","ts_ms":-9223372036854775808,
        "broker_request_id":9223372036854775807,"broker_error_code":-2147483648,
        "broker_connection_epoch":18446744073709551615})", true,
        [](const OmsJournalEvent& event) {
            REQUIRE(event.tsMs == std::numeric_limits<long long>::min());
            REQUIRE(event.brokerRequestId == std::numeric_limits<long long>::max());
            REQUIRE(event.brokerErrorCode == std::numeric_limits<int>::min());
            REQUIRE(event.brokerConnectionEpoch == std::numeric_limits<std::uint64_t>::max());
        });
}

class CommaDecimal : public std::numpunct<char>
{
    char do_decimal_point() const override { return ','; }
    char do_thousands_sep() const override { return '.'; }
    std::string do_grouping() const override { return "\3"; }
};

void TestWriterPreservesValues()
{
    const std::string directory = MakeTempDirectory();
    const std::string path = directory + "/journal.jsonl";
    const std::locale previous = std::locale();
    std::locale::global(std::locale(std::locale::classic(), new CommaDecimal));
    {
        OmsJournal journal;
        REQUIRE(journal.Init(path));
        OmsJournalEvent event;
        event.eventType = "broker_execution";
        event.tsMs = std::numeric_limits<long long>::max();
        event.brokerConnectionEpoch = std::numeric_limits<std::uint64_t>::max();
        event.qty = std::numeric_limits<double>::denorm_min();
        event.price = std::numeric_limits<double>::max();
        event.brokerRemainingQuantity = 1.234567891234567;
        event.brokerMarketCapPrice = std::numeric_limits<double>::min();
        event.reason = std::string("\0\1\b\f\n\r\t", 7) + "\xe4\xb8\xad\xf0\x9f\x9a\x80";
        REQUIRE(journal.Append(event));
        REQUIRE(journal.Replay([&](const OmsJournalEvent& replayed) {
            REQUIRE(replayed.tsMs == event.tsMs);
            REQUIRE(replayed.brokerConnectionEpoch == event.brokerConnectionEpoch);
            REQUIRE(replayed.qty == event.qty);
            REQUIRE(replayed.price == event.price);
            REQUIRE(replayed.brokerRemainingQuantity == event.brokerRemainingQuantity);
            REQUIRE(replayed.brokerMarketCapPrice == event.brokerMarketCapPrice);
            REQUIRE(replayed.reason == event.reason);
        }) == 1);
        event.reason = "\xc0\x80";
        REQUIRE(!journal.Append(event));
        event.reason.clear();
        event.qty = std::numeric_limits<double>::infinity();
        REQUIRE(!journal.Append(event));
        event.qty = std::numeric_limits<double>::quiet_NaN();
        REQUIRE(!journal.Append(event));
        event.qty = 1.0;
        event.schemaVersion = OmsJournal::kSchemaVersion + 1;
        REQUIRE(!journal.Append(event));
        REQUIRE(journal.Replay({}) == 1);
    }
    std::locale::global(previous);
    REQUIRE(::unlink(path.c_str()) == 0);
    REQUIRE(::rmdir(directory.c_str()) == 0);
}

void RequireEqual(const OmsJournalEvent& expected,
                  const OmsJournalEvent& actual)
{
    REQUIRE(actual.schemaVersion == expected.schemaVersion);
    REQUIRE(actual.eventType == expected.eventType);
    REQUIRE(actual.tsMs == expected.tsMs);
    REQUIRE(actual.orderId == expected.orderId);
    REQUIRE(actual.clientReqId == expected.clientReqId);
    REQUIRE(actual.instrument == expected.instrument);
    REQUIRE(actual.side == expected.side);
    REQUIRE(actual.qty == expected.qty);
    REQUIRE(actual.price == expected.price);
    REQUIRE(actual.status == expected.status);
    REQUIRE(actual.reason == expected.reason);
    REQUIRE(actual.source == expected.source);
    REQUIRE(actual.traceId == expected.traceId);
    REQUIRE(actual.reqId == expected.reqId);
    REQUIRE(actual.riskCode == expected.riskCode);
    REQUIRE(actual.venue == expected.venue);
    REQUIRE(actual.strategy == expected.strategy);
    REQUIRE(actual.account == expected.account);
    REQUIRE(actual.eventId == expected.eventId);
    REQUIRE(actual.executionDomain == expected.executionDomain);
    REQUIRE(actual.requestHash == expected.requestHash);
    REQUIRE(actual.venueCorrelationId == expected.venueCorrelationId);
    REQUIRE(actual.brokerCallbackType == expected.brokerCallbackType);
    REQUIRE(actual.brokerServiceEpoch == expected.brokerServiceEpoch);
    REQUIRE(actual.brokerConnectionEpoch == expected.brokerConnectionEpoch);
    REQUIRE(actual.brokerRequestId == expected.brokerRequestId);
    REQUIRE(actual.brokerErrorCode == expected.brokerErrorCode);
    REQUIRE(actual.brokerMessage == expected.brokerMessage);
    REQUIRE(actual.brokerAdvancedOrderRejectJson ==
            expected.brokerAdvancedOrderRejectJson);
    REQUIRE(actual.brokerWhyHeld == expected.brokerWhyHeld);
    REQUIRE(actual.brokerExecutionId == expected.brokerExecutionId);
    REQUIRE(actual.brokerRemainingQuantity == expected.brokerRemainingQuantity);
    REQUIRE(actual.brokerMarketCapPrice == expected.brokerMarketCapPrice);
    REQUIRE(!actual.rawLine.empty());
    REQUIRE(actual.rawLine.find("\"schema_version\":4") != std::string::npos);
    REQUIRE(actual.rawLine.find("\"broker_execution_id\":\"exec-001\"") !=
            std::string::npos);
}
}

int main()
{
    REQUIRE(OmsJournal::kSchemaVersion == 4);
    ::setenv("HEPTA_OMS_ASYNC_FLUSH", "0", 1);
    ::setenv("HEPTA_OMS_SYNC_CRITICAL", "1", 1);

    TestStrictTypedReplay();
    TestWriterPreservesValues();

    const std::string directory = MakeTempDirectory();
    const std::string path = directory + "/journal.jsonl";
    {
        OmsJournal journal;
        REQUIRE(journal.Init(path));

        OmsJournalEvent event;
        event.schemaVersion = OmsJournal::kSchemaVersion;
        event.eventType = "broker_execution";
        event.tsMs = 1800000000123LL;
        event.orderId = 101;
        event.clientReqId = "cmd-001";
        event.instrument = "EUR.USD";
        event.side = "BUY";
        event.qty = 2.5;
        event.price = 101.25;
        event.status = "Filled";
        event.reason = "quoted \"reason\"\nline";
        event.source = "ib.execDetails";
        event.traceId = "session-001";
        event.reqId = "cmd-001";
        event.riskCode = "RISK_OK";
        event.venue = "IB";
        event.strategy = "shadow-test";
        event.account = "DU000000";
        event.eventId = "event-001";
        event.executionDomain = "PAPER";
        event.requestHash =
            "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
        event.venueCorrelationId = "hepta:cmd-001";
        event.brokerCallbackType = "execDetails";
        event.brokerServiceEpoch = "ib-service-epoch-001";
        event.brokerConnectionEpoch = 7;
        event.brokerRequestId = 55;
        event.brokerErrorCode = 201;
        event.brokerMessage = "broker message";
        event.brokerAdvancedOrderRejectJson = "{\"error\":\"detail\"}";
        event.brokerWhyHeld = "locate";
        event.brokerExecutionId = "exec-001";
        event.brokerRemainingQuantity = 0.5;
        event.brokerMarketCapPrice = 102.5;

        REQUIRE(journal.Append(event));
        const OmsJournalHealthSnapshot health = journal.GetHealthSnapshot();
        REQUIRE(health.durableSyncWrites == 1);
        REQUIRE(health.durableSyncFailures == 0);
        REQUIRE(!health.writePoisoned);

        int count = 0;
        journal.Replay([&](const OmsJournalEvent& replayed) {
            ++count;
            RequireEqual(event, replayed);
        });
        REQUIRE(count == 1);
    }

    struct stat metadata;
    REQUIRE(::stat(path.c_str(), &metadata) == 0);
    REQUIRE((metadata.st_mode & 0777) == 0600);
    REQUIRE(::unlink(path.c_str()) == 0);
    REQUIRE(::rmdir(directory.c_str()) == 0);

    std::cout << "oms_journal_schema_v4_tests: PASS\n";
    return 0;
}
