#include "../HeptaTrade/oms_generation_store.h"
#include "../HeptaTrade/oms_journal.h"
#include "../HeptaTrade/simulator/deterministic_execution_venue.h"

#include <cassert>
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <map>
#include <string>
#include <sys/stat.h>
#include <unistd.h>

namespace
{
std::string RequestHash(char digit)
{
    return std::string("sha256:") + std::string(64, digit);
}

OmsJournalEvent Place(long id, const char* command, double quantity, char hashDigit)
{
    OmsJournalEvent event;
    event.eventType = "place_sent";
    event.tsMs = 1000 + id;
    event.orderId = id;
    event.source = "agent.tool:risk-test";
    event.traceId = "session";
    event.reqId = command;
    event.clientReqId = command;
    event.account = "SIM";
    event.executionDomain = "SIM:risk-test";
    event.instrument = "EUR.USD";
    event.side = "BUY";
    event.qty = quantity;
    event.status = "submitted";
    event.venue = "SIMULATOR";
    event.requestHash = RequestHash(hashDigit);
    event.venueCorrelationId = std::string("sim-") + command;
    return event;
}

OmsJournalEvent Status(long id, const char* command, double quantity,
                       const char* status, double price, char hashDigit)
{
    OmsJournalEvent event = Place(id, command, quantity, hashDigit);
    event.eventType = "status";
    event.status = status;
    event.price = price;
    return event;
}

OmsJournalEvent Terminal(long id, const char* command, double quantity, char hashDigit)
{
    OmsJournalEvent event = Place(id, command, quantity, hashDigit);
    event.eventType = "order_owner_reconciled_terminal";
    event.status = "terminal";
    return event;
}

InstrumentRef Contract()
{
    InstrumentRef contract;
    contract.symbol = "EUR";
    contract.currency = "USD";
    contract.secType = "CASH";
    contract.exchange = "SIM";
    return contract;
}

OrderIntent Order(double quantity)
{
    OrderIntent order;
    order.action = "BUY";
    order.orderType = "MKT";
    order.totalQuantity = quantity;
    return order;
}

std::string Quote(const std::string& value)
{
    return std::string("'") + value + "'";
}
}

int main()
{
    char directoryTemplate[] = "/tmp/hepta-v2-risk-recovery-XXXXXX";
    char* directory = ::mkdtemp(directoryTemplate);
    assert(directory != nullptr);
    const std::string root(directory);
    assert(::chmod(root.c_str(), 0700) == 0);
    const std::string journalPath = root + "/oms.jsonl";
    const std::string storePath = journalPath + ".generations";

    {
        OmsJournal journal;
        assert(journal.Init(journalPath));
        assert(journal.Append(Place(1000000, "first", 100.0, '1')));
        assert(journal.Append(Status(1000000, "first", 100.0, "Filled", 1.1002, '1')));
        assert(journal.Append(Terminal(1000000, "first", 100.0, '1')));
        assert(journal.Append(Place(1000001, "second", 1.0, '2')));
        assert(journal.Append(Status(1000001, "second", 1.0, "Cancelled", 0.0, '2')));
        assert(journal.Append(Terminal(1000001, "second", 1.0, '2')));
    }

    const std::string helper =
        std::string(HEPTA_SOURCE_ROOT) + "/scripts/hepta_oms_lifecycle.py";
    const std::string seal =
        std::string("python3 ") + Quote(helper) + " seal --journal " + Quote(journalPath) +
        " --store " + Quote(storePath) + " --stopped-state >/dev/null";
    assert(std::system(seal.c_str()) == 0);

    long maximumOrderId = 999999;
    std::map<long, OmsJournalEvent> admitted;
    std::map<long, OmsJournalEvent> fills;
    bool valid = true;
    std::uint64_t records = 0;
    std::string reason;
    OmsGenerationStore store(journalPath);
    assert(store.ReplayCompleteHistory(1024U * 1024U,
        [&](const OmsJournalEvent& event) {
            if (event.orderId > maximumOrderId) maximumOrderId = event.orderId;
            const bool fill = event.eventType == "status" && event.status == "Filled";
            if (event.eventType != "place_sent" && !fill) return;
            if (event.orderId < 0 || event.venue != "SIMULATOR" || event.account != "SIM" ||
                event.instrument.empty() || (event.side != "BUY" && event.side != "SELL") ||
                !std::isfinite(event.qty) || event.qty <= 0.0)
            {
                valid = false;
                return;
            }
            if (!fill)
            {
                admitted[event.orderId] = event;
                return;
            }
            const std::map<long, OmsJournalEvent>::const_iterator owner = admitted.find(event.orderId);
            if (!std::isfinite(event.price) || event.price <= 0.0 ||
                owner == admitted.end() || owner->second.instrument != event.instrument ||
                owner->second.side != event.side || owner->second.qty != event.qty)
            {
                valid = false;
                return;
            }
            fills[event.orderId] = event;
        }, records, reason));
    assert(valid);
    assert(records == 6);
    assert(admitted.size() == 2);
    assert(fills.size() == 1);
    assert(maximumOrderId == 1000001);

    std::map<std::string, double> positions;
    for (std::map<long, OmsJournalEvent>::const_iterator it = fills.begin();
         it != fills.end(); ++it)
        positions[it->second.instrument] +=
            it->second.side == "BUY" ? it->second.qty : -it->second.qty;

    DeterministicExecutionVenue venue;
    assert(venue.RestoreRiskState(positions,
        static_cast<std::uint64_t>(admitted.size()), reason));
    venue.RestoreNextOrderIdAtLeast(maximumOrderId + 1);
    assert(venue.Position("EUR.USD") == 100.0);
    assert(venue.AdmittedOrderCount() == 2);

    PreTradeRiskConfig risk;
    risk.enableOrderSubmission = true;
    risk.maxOrderQuantity = 100.0;
    risk.maxDailyOrders = 2;
    risk.maxPriceDeviationBps = 0.0;
    venue.SetRiskConfig(risk);
    venue.SetQuote("EUR.USD", 1.1000, 1.1002);
    const PreTradeRiskDecision daily = venue.PreviewRisk(Contract(), Order(1.0));
    assert(!daily.allow && daily.reasonCode == "RISK_DAILY_ORDER_LIMIT");

    risk.maxDailyOrders = 100;
    venue.SetRiskConfig(risk);
    long nextOrderId = -1;
    assert(venue.PlaceOrder(Contract(), Order(1.0), &nextOrderId));
    assert(nextOrderId > maximumOrderId);
    assert(venue.AdmittedOrderCount() == 3);

    const std::string cleanup = std::string("rm -rf -- ") + Quote(root);
    assert(std::system(cleanup.c_str()) == 0);
    std::cout << "simulator_v2_risk_recovery_tests: PASS\n";
    return 0;
}
