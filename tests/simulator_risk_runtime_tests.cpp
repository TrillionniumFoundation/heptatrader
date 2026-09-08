#include "../HeptaTrade/simulator/deterministic_execution_venue.h"
#include "../HeptaTrade/execution/execution_service_runtime_composition.h"
#include "../HeptaTrade/execution/execution_coordinator.h"
#include "../HeptaTrade/execution/unix_execution_service_client.h"

#include <atomic>
#include <cassert>
#include <chrono>
#include <cstring>
#include <fstream>
#include <iostream>
#include <set>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <thread>
#include <unistd.h>

namespace {
InstrumentRef Contract()
{
    InstrumentRef contract;
    contract.symbol = "EUR";
    contract.currency = "USD";
    contract.secType = "CASH";
    contract.exchange = "SIM";
    return contract;
}

OrderIntent Order(double quantity = 100.0)
{
    OrderIntent order;
    order.action = "BUY";
    order.orderType = "MKT";
    order.totalQuantity = quantity;
    return order;
}

PreTradeRiskConfig Risk()
{
    PreTradeRiskConfig config;
    config.enableOrderSubmission = true;
    config.maxOrderQuantity = 100.0;
    config.maxDailyOrders = 100;
    config.maxPriceDeviationBps = 0.0;
    config.maxOrderNotional = 200.0;
    config.maxWorstCaseGrossNotional = 150.0;
    config.maxSnapshotAgeMs = 1000;
    return config;
}

void TestDeterministicRiskReservationAndActivation()
{
    std::uint64_t now = 10000;
    DeterministicExecutionVenue venue([&]() { return now; });
    venue.SetRiskConfig(Risk());
    venue.SetQuote("EUR.USD", 1.1000, 1.1002);
    const auto contract = Contract();
    const auto order = Order();
    assert(venue.PreviewRisk(contract, order).allow);
    std::atomic<bool> start(false);
    std::atomic<int> accepted(0);
    long ids[2] = {-1, -1};
    auto submit = [&](int index) {
        while (!start.load()) std::this_thread::yield();
        if (venue.PlaceOrderCorrelated(contract, order,
                "reservation-" + std::to_string(index), &ids[index], false))
            ++accepted;
    };
    std::thread first(submit, 0);
    std::thread second(submit, 1);
    start.store(true);
    first.join();
    second.join();
    assert(accepted.load() == 1);
    assert(venue.ActiveOrderIds().size() == 1);
    const long id = ids[0] >= 0 ? ids[0] : ids[1];
    const auto pendingRisk = venue.PreviewRisk(contract, order);
    assert(!pendingRisk.allow);
    assert(pendingRisk.reasonCode == "RISK_WORST_CASE_GROSS_LIMIT");
    std::vector<std::string> statuses;
    venue.SetEventSink([&](const SimulatedOrderEvent& event) {
        // Re-entrant reads prove callbacks run outside the venue mutex.
        assert(venue.Position(event.instrument) == 100.0);
        statuses.push_back(event.status);
    });
    venue.Process();
    assert(statuses.empty());
    assert(venue.Position("EUR.USD") == 0.0);
    assert(venue.ActivateOrder(id));
    venue.Process();
    assert(statuses == std::vector<std::string>({"Submitted", "Filled"}));
    assert(venue.Position("EUR.USD") == 100.0);
    assert(venue.ActiveOrderIds().empty());
    assert(!venue.ActivateOrder(id));
    venue.Process();
    assert(statuses.size() == 2);

    auto cfg = Risk();
    cfg.flattenOnly = true;
    cfg.maxOrderQuantity = 200.0;
    cfg.maxOrderNotional = 500.0;
    cfg.maxWorstCaseGrossNotional = 1000.0;
    venue.SetRiskConfig(cfg);
    auto reverse = Order(120.0);
    reverse.action = "SELL";
    assert(!venue.PreviewRisk(contract, reverse).allow);
    reverse.totalQuantity = 100.0;
    assert(venue.PreviewRisk(contract, reverse).allow);

    cfg.flattenOnly = false;
    venue.SetRiskConfig(cfg);
    now += 1001;
    assert(!venue.PreviewRisk(contract, reverse).allow);
}


void TestFlattenCapacityReservations()
{
    const InstrumentRef contract = Contract();
    auto flattenRisk = Risk();
    flattenRisk.flattenOnly = true;
    flattenRisk.maxOrderQuantity = 20.0;
    flattenRisk.maxOrderNotional = 0.0;
    flattenRisk.maxWorstCaseGrossNotional = 0.0;
    flattenRisk.maxDailyLoss = 0.0;
    flattenRisk.maxDrawdown = 0.0;
    flattenRisk.maxSnapshotAgeMs = 0;
    const auto exitOrder = [](const char* action, double quantity) {
        OrderIntent order = Order(quantity);
        order.action = action;
        return order;
    };

    {
        std::uint64_t now = 10000;
        DeterministicExecutionVenue venue([&]() { return now; });
        venue.SetRiskConfig(flattenRisk);
        venue.SetQuote("EUR.USD", 1.1000, 1.1002);
        std::string reason;
        assert(venue.RestoreRiskState({{"EUR.USD", 10.0}}, 0, reason));
        long four = -1, six = -1, extra = -1;
        assert(venue.PlaceOrderCorrelated(
            contract, exitOrder("SELL", 4.0), "long-four", &four, false));
        assert(venue.PlaceOrderCorrelated(
            contract, exitOrder("SELL", 6.0), "long-six", &six, false));
        assert(!venue.PlaceOrderCorrelated(
            contract, exitOrder("SELL", 0.01), "long-extra", &extra, false));
        assert(venue.LastRejectReason() == "RISK_FLATTEN_ONLY_BLOCK");
        assert(venue.ActivateOrder(four));
        assert(venue.ActivateOrder(six));
        venue.Process();
        assert(venue.Position("EUR.USD") == 0.0 &&
               "aggregate exact-capacity long exits must reach zero");
    }

    {
        std::uint64_t now = 11000;
        DeterministicExecutionVenue venue([&]() { return now; });
        venue.SetRiskConfig(flattenRisk);
        venue.SetQuote("EUR.USD", 1.1000, 1.1002);
        std::string reason;
        assert(venue.RestoreRiskState({{"EUR.USD", -10.0}}, 0, reason));
        long four = -1, six = -1, extra = -1;
        assert(venue.PlaceOrderCorrelated(
            contract, exitOrder("BUY", 4.0), "short-four", &four, false));
        assert(venue.PlaceOrderCorrelated(
            contract, exitOrder("BUY", 6.0), "short-six", &six, false));
        assert(!venue.PlaceOrderCorrelated(
            contract, exitOrder("BUY", 0.01), "short-extra", &extra, false));
        assert(venue.LastRejectReason() == "RISK_FLATTEN_ONLY_BLOCK");
        assert(venue.ActivateOrder(four));
        assert(venue.ActivateOrder(six));
        venue.Process();
        assert(venue.Position("EUR.USD") == 0.0 &&
               "aggregate exact-capacity short exits must reach zero");
    }

    {
        std::uint64_t now = 12000;
        DeterministicExecutionVenue venue([&]() { return now; });
        venue.SetRiskConfig(flattenRisk);
        venue.SetQuote("EUR.USD", 1.1000, 1.1002);
        std::string reason;
        assert(venue.RestoreRiskState({{"EUR.USD", 10.0}}, 0, reason));
        std::atomic<bool> start(false);
        std::atomic<int> accepted(0);
        long ids[2] = {-1, -1};
        const OrderIntent exact = exitOrder("SELL", 10.0);
        auto submit = [&](int index) {
            while (!start.load()) std::this_thread::yield();
            if (venue.PlaceOrderCorrelated(contract, exact,
                    "concurrent-flatten-" + std::to_string(index),
                    &ids[index], false))
                ++accepted;
        };
        std::thread first(submit, 0);
        std::thread second(submit, 1);
        start.store(true);
        first.join();
        second.join();
        assert(accepted.load() == 1 &&
               "only one concurrent exact flatten may reserve the position");
    }

    {
        std::uint64_t now = 13000;
        DeterministicExecutionVenue venue([&]() { return now; });
        venue.SetRiskConfig(flattenRisk);
        venue.SetQuote("EUR.USD", 1.1000, 1.1002);
        std::string reason;
        assert(venue.RestoreRiskState({{"EUR.USD", 10.0}}, 0, reason));
        long cancelling = -1, blocked = -1, replacement = -1;
        assert(venue.PlaceOrderCorrelated(
            contract, exitOrder("SELL", 10.0), "cancel-held",
            &cancelling, true));
        assert(venue.CancelOrder(cancelling));
        assert(!venue.PlaceOrderCorrelated(
            contract, exitOrder("SELL", 1.0), "before-cancel-terminal",
            &blocked, false));
        assert(venue.LastRejectReason() == "RISK_FLATTEN_ONLY_BLOCK" &&
               "cancel request must not refund capacity before terminal confirmation");
        venue.Process();
        assert(venue.Position("EUR.USD") == 10.0);
        assert(venue.TerminalOrderStatuses().at(cancelling) == "Cancelled");
        assert(venue.PlaceOrderCorrelated(
            contract, exitOrder("SELL", 10.0), "after-cancel-terminal",
            &replacement, false));
    }

    {
        std::uint64_t now = 14000;
        DeterministicExecutionVenue venue([&]() { return now; });
        venue.SetRiskConfig(flattenRisk);
        venue.SetQuote("EUR.USD", 1.1000, 1.1002);
        std::string reason;
        assert(venue.RestoreRiskState({{"EUR.USD", 10.0}}, 0, reason));
        long six = -1, five = -1, four = -1;
        assert(venue.PlaceOrderCorrelated(
            contract, exitOrder("SELL", 6.0), "policy-six", &six, false));
        auto unrestricted = flattenRisk;
        unrestricted.flattenOnly = false;
        venue.SetRiskConfig(unrestricted);
        venue.SetRiskConfig(flattenRisk);
        assert(!venue.PlaceOrderCorrelated(
            contract, exitOrder("SELL", 5.0), "policy-five", &five, false));
        assert(venue.PlaceOrderCorrelated(
            contract, exitOrder("SELL", 4.0), "policy-four", &four, false));
    }

    {
        std::uint64_t now = 15000;
        DeterministicExecutionVenue venue([&]() { return now; });
        venue.SetRiskConfig(flattenRisk);
        venue.SetQuote("EUR.USD", 1.1000, 1.1002);
        std::string reason;
        assert(venue.RestoreRiskState({{"EUR.USD", 10.0}}, 0, reason));
        long reserved = -1, ordinary = -1;
        assert(venue.PlaceOrderCorrelated(
            contract, exitOrder("SELL", 10.0), "guarded-reservation",
            &reserved, false));
        auto unrestricted = flattenRisk;
        unrestricted.flattenOnly = false;
        venue.SetRiskConfig(unrestricted);
        assert(venue.PlaceOrderCorrelated(
            contract, exitOrder("SELL", 10.0), "ordinary-reduction",
            &ordinary, true));
        venue.Process();
        assert(venue.Position("EUR.USD") == 0.0);
        assert(venue.ActivateOrder(reserved));
        venue.Process();
        assert(venue.Position("EUR.USD") == 0.0 &&
               "fill-time guard must prevent a stale flatten reservation crossing zero");
        assert(venue.TerminalOrderStatuses().at(reserved) == "Rejected");
    }

    {
        std::uint64_t now = 16000;
        DeterministicExecutionVenue venue([&]() { return now; });
        auto monetary = Risk();
        monetary.flattenOnly = true;
        monetary.maxOrderQuantity = 20.0;
        monetary.maxOrderNotional = 1000.0;
        monetary.maxWorstCaseGrossNotional = 1000.0;
        monetary.maxSnapshotAgeMs = 1000;
        venue.SetRiskConfig(monetary);
        venue.SetQuote("EUR.USD", 1.1000, 1.1002);
        std::string reason;
        assert(venue.RestoreRiskState({{"EUR.USD", 10.0}}, 7, reason));
        long six = -1, five = -1;
        assert(venue.PlaceOrderCorrelated(
            contract, exitOrder("SELL", 6.0), "monetary-six", &six, false));
        assert(!venue.PlaceOrderCorrelated(
            contract, exitOrder("SELL", 5.0), "monetary-five", &five, false));
        assert(venue.LastRejectReason() == "RISK_FLATTEN_ONLY_BLOCK");
    }
}

void TestPreviewAndFinalAdmissionRejectSameRisk()
{
    std::uint64_t now = 50000;
    DeterministicExecutionVenue venue([&]() { return now; });
    venue.SetRiskConfig(Risk());
    venue.SetQuote("EUR.USD", 1.1000, 1.1002);
    auto tooLarge = Order(101.0);
    const auto preview = venue.PreviewRisk(Contract(), tooLarge);
    assert(!preview.allow);
    long id = -1;
    assert(!venue.PlaceOrder(Contract(), tooLarge, &id));
    assert(venue.LastRejectReason() == preview.reasonCode);
    assert(id == -1 && venue.ActiveOrderIds().empty());

    auto invalid = Order();
    invalid.action = "INVALID";
    const auto invalidPreview = venue.PreviewRisk(Contract(), invalid);
    assert(!invalidPreview.allow);
    assert(!venue.PlaceOrder(Contract(), invalid, &id));
    assert(venue.LastRejectReason() == invalidPreview.reasonCode);

    auto unsupported = Contract();
    unsupported.secType = "FUT";
    assert(!venue.PreviewRisk(unsupported, Order()).allow);
    assert(!venue.PlaceOrder(unsupported, Order(), &id));
    unsupported = Contract();
    unsupported.multiplier = "100";
    assert(!venue.PreviewRisk(unsupported, Order()).allow);
    unsupported = Contract();
    unsupported.currency = "JPY";
    venue.SetQuote("EUR.JPY", 160.0, 160.1);
    assert(!venue.PreviewRisk(unsupported, Order()).allow);

    // A previously allowed preview conveys no mutable admission authority.
    assert(venue.PreviewRisk(Contract(), Order()).allow);
    auto killed = Risk();
    killed.globalKillSwitch = true;
    venue.SetRiskConfig(killed);
    assert(!venue.PlaceOrder(Contract(), Order(), &id));
    assert(venue.ActiveOrderIds().empty());

    venue.SetRiskConfig(Risk());
    now += 60001;
    const auto stale = venue.PreviewRisk(Contract(), Order());
    assert(!stale.allow && stale.reasonCode == "SIM_QUOTE_STALE");
    assert(!venue.PlaceOrder(Contract(), Order(), &id));
    assert(venue.LastRejectReason() == stale.reasonCode);
}

int Listener(const std::string& path)
{
    const int fd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    assert(fd >= 0);
    sockaddr_un address;
    std::memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    assert(path.size() < sizeof(address.sun_path));
    std::strcpy(address.sun_path, path.c_str());
    assert(::bind(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0);
    assert(::listen(fd, 8) == 0);
    return fd;
}

std::string JsonString(const std::string& json, const std::string& key)
{
    const std::string prefix = "\"" + key + "\":\"";
    const auto start = json.find(prefix);
    assert(start != std::string::npos);
    const auto begin = start + prefix.size();
    const auto end = json.find('"', begin);
    assert(end != std::string::npos);
    return json.substr(begin, end - begin);
}

ExecutionCommandResult PreviewAndPlace(UnixExecutionServiceClient& client,
                                       PlaceOrderCommand command)
{
    const auto preview = client.PreviewOrder(command);
    if (preview.status != ExecutionCommandStatus::Accepted)
        std::cerr << "preview rejected " << preview.reasonCode << " " << preview.detail << '\n';
    assert(preview.status == ExecutionCommandStatus::Accepted);
    command.previewPermit = JsonString(preview.detail, "preview_permit");
    command.context.toolCallId = JsonString(preview.detail, "command_id");
    const auto placed = client.PlaceOrder(command);
    if (placed.status != ExecutionCommandStatus::Accepted)
        std::cerr << "place rejected " << placed.reasonCode << " " << placed.detail << '\n';
    assert(placed.status == ExecutionCommandStatus::Accepted);
    return placed;
}

void AwaitStatus(ExecutionServiceRuntimeComposition& runtime,
                 std::uint64_t& cursor, long orderId, const std::string& status)
{
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(3);
    while (std::chrono::steady_clock::now() < deadline)
    {
        ExecutionEvent event;
        if (runtime.EventHub().WaitNext("SIM:risk-test", "risk-test", "session", cursor, 100, event))
        {
            cursor = event.sequence;
            if (event.orderId == orderId && event.status == status) return;
        }
    }
    assert(false && "production simulator pump did not deliver status");
}

void TestProductionRuntimePumpsAndJournalsEvents()
{
    // The production server deliberately rejects a root Gateway identity.
    // Root-only containers still execute all deterministic venue tests above.
    if (::geteuid() == 0) return;
    char directoryTemplate[] = "/tmp/hepta-simulator-runtime-XXXXXX";
    char* directory = ::mkdtemp(directoryTemplate);
    assert(directory != nullptr);
    const std::string path(directory);
    const std::string credential = path + "/hepta-execution-fence";
    {
        std::ofstream output(credential);
        output << "HFC1\nfencing_token=1\ngeneration=1\n";
    }
    assert(::chmod(credential.c_str(), 0400) == 0);
    const std::string mutationSocket = path + "/mutation.sock";
    const std::string eventSocket = path + "/event.sock";
    ExecutionServiceRuntimeConfig config;
    config.mode = ExecutionServiceRuntimeMode::Simulator;
    config.listenFd = Listener(mutationSocket);
    config.eventListenFd = Listener(eventSocket);
    config.allowedGatewayUids.insert(static_cast<std::uint32_t>(::geteuid()));
    config.gatewayContextBinding.agentId = "risk-test";
    config.gatewayContextBinding.account = "SIM";
    config.gatewayContextBinding.venue = "SIMULATOR";
    config.gatewayContextBinding.executionDomain = "SIM:risk-test";
    config.stateDirectory = path;
    config.journalPath = path + "/oms-journal.jsonl";
    config.fenceCredentialPath = credential;
    config.simulatorQuoteTtlMs = 100;
    config.simulatorQuoteRefreshIntervalMs = 20;
    long filledId = -1;
    long cancelledId = -1;
    {
        ExecutionServiceRuntimeComposition runtime(config);
        std::string reason;
        if (!runtime.Start(reason)) std::cerr << reason << '\n';
        assert(runtime.IsRunning());
        UnixExecutionServiceClient client(mutationSocket);
        PlaceOrderCommand command;
        command.context.agentId = "risk-test";
        command.context.sessionId = "session";
        command.context.toolCallId = "preview-market";
        command.context.account = "SIM";
        command.context.venue = "SIMULATOR";
        command.context.executionDomain = "SIM:risk-test";
        command.contract = Contract();
        command.order = Order();
        command.instrument = "EUR.USD";
        command.timeInForce = "DAY";
        command.expiresAtMs = OmsJournal::NowEpochMs() + 10000;
        filledId = PreviewAndPlace(client, command).orderId;
        std::uint64_t cursor = 0;
        AwaitStatus(runtime, cursor, filledId, "Filled");
        assert(runtime.Venue().Position("EUR.USD") == 100.0);
        // Quote refresh remains live after multiple original quote TTLs.
        std::this_thread::sleep_for(std::chrono::milliseconds(220));
        command.context.toolCallId = "preview-resting";
        command.order.orderType = "LMT";
        command.order.lmtPrice = 1.0;
        cancelledId = PreviewAndPlace(client, command).orderId;
        AwaitStatus(runtime, cursor, cancelledId, "Submitted");
        CancelOrderCommand cancel;
        cancel.context = command.context;
        cancel.context.toolCallId = "cancel-resting";
        cancel.orderId = cancelledId;
        cancel.instrument = command.instrument;
        cancel.side = "BUY";
        assert(client.CancelOrder(cancel).status == ExecutionCommandStatus::Accepted);
        AwaitStatus(runtime, cursor, cancelledId, "Cancelled");
        assert(runtime.Venue().ActiveOrderIds().empty());
        assert(!runtime.IsMutationBlocked());
        runtime.Stop();
    }
    std::set<long> terminalOwners;
    std::set<long> terminalStatuses;
    OmsJournalEvent filledRecord;
    {
        OmsJournal journal;
        assert(journal.Init(config.journalPath));
        assert(journal.Replay([&](const OmsJournalEvent& event) {
            if (event.eventType == "order_owner_reconciled_terminal")
                terminalOwners.insert(event.orderId);
            if (event.eventType == "status" &&
                (event.status == "Filled" || event.status == "Cancelled"))
                terminalStatuses.insert(event.orderId);
            if (event.eventType == "status" && event.status == "Filled")
                filledRecord = event;
        }) > 0);
        ExecutionCoordinator recovered(journal, ExecutionCoordinatorCallbacks());
        std::string reason;
        assert(recovered.RecoverFromJournal(reason));
        ExecutionOrderOwner owner;
        assert(!recovered.GetOrderOwner(filledId, owner));
        assert(!recovered.GetOrderOwner(cancelledId, owner));
        assert(journal.Append(filledRecord)); // Exact duplicate must not double the restored position.
    }
    assert(terminalOwners == std::set<long>({filledId, cancelledId}));
    assert(terminalStatuses == terminalOwners);
    ::unlink(mutationSocket.c_str());
    ::unlink(eventSocket.c_str());
    config.listenFd = Listener(mutationSocket);
    config.eventListenFd = Listener(eventSocket);
    {
        ExecutionServiceRuntimeComposition restarted(config);
        std::string reason;
        assert(restarted.Start(reason));
        assert(restarted.Venue().Position("EUR.USD") == 100.0);
        assert(restarted.Venue().ActiveOrderIds().empty());
        auto risk = Risk();
        risk.maxDailyOrders = 2;
        risk.maxWorstCaseGrossNotional = 1000.0;
        restarted.Venue().SetRiskConfig(risk);
        assert(restarted.Venue().PreviewRisk(Contract(), Order(10.0)).reasonCode ==
            "RISK_DAILY_ORDER_LIMIT");
        risk.maxDailyOrders = 100;
        risk.maxWorstCaseGrossNotional = 115.0;
        restarted.Venue().SetRiskConfig(risk);
        const auto gross = restarted.Venue().PreviewRisk(Contract(), Order(10.0));
        assert(!gross.allow && gross.reasonCode == "RISK_WORST_CASE_GROSS_LIMIT");
        long unexpected = -1;
        assert(!restarted.Venue().PlaceOrder(Contract(), Order(10.0), &unexpected));
        assert(restarted.Venue().ActiveOrderIds().empty());
        restarted.Stop();
    }
    {
        OmsJournal journal;
        assert(journal.Init(config.journalPath));
        filledRecord.qty += 1.0;
        assert(journal.Append(filledRecord));
    }
    ::unlink(mutationSocket.c_str());
    ::unlink(eventSocket.c_str());
    config.listenFd = Listener(mutationSocket);
    config.eventListenFd = Listener(eventSocket);
    {
        ExecutionServiceRuntimeComposition conflicted(config);
        std::string reason;
        assert(!conflicted.Start(reason));
        assert(reason == "EXECUTION_SIMULATOR_RISK_REPLAY_CONFLICT");
    }
    for (const auto& name : {"mutation.sock", "event.sock", "hepta-execution-fence",
                             "execution-runtime.lock", "oms-journal.jsonl"})
        ::unlink((path + "/" + name).c_str());
    assert(::rmdir(path.c_str()) == 0);
}
} // namespace

int main()
{
    TestDeterministicRiskReservationAndActivation();
    TestFlattenCapacityReservations();
    TestPreviewAndFinalAdmissionRejectSameRisk();
    TestProductionRuntimePumpsAndJournalsEvents();
    std::cout << "simulator risk, reservation, activation and production runtime tests passed\n";
    return 0;
}
