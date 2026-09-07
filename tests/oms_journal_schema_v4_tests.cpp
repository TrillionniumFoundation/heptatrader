#include "../HeptaTrade/oms_journal.h"

#include <cstdlib>
#include <iostream>
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
