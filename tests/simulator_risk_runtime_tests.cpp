#include "../HeptaTrade/simulator/deterministic_execution_venue.h"
#include "../HeptaTrade/execution/execution_service_runtime_composition.h"
#include "../HeptaTrade/execution/execution_coordinator.h"
#include "../HeptaTrade/execution/unix_execution_service_client.h"

#include <atomic>
#include <cassert>
#include <chrono>
#include <cmath>
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

struct FlattenFixture
{
    std::uint64_t now = 10000;
    DeterministicExecutionVenue venue;
    PreTradeRiskConfig risk;
    explicit FlattenFixture(double position, bool monetary = true)
        : venue([this]() { return now; }), risk(Risk())
    {
        risk.flattenOnly = true;
        risk.maxOrderQuantity = 20.0;
        risk.maxOrderNotional = monetary ? 500.0 : 0.0;
        risk.maxWorstCaseGrossNotional = monetary ? 1000.0 : 0.0;
        risk.maxSnapshotAgeMs = monetary ? 1000 : 0;
        venue.SetRiskConfig(risk);
        venue.SetQuote("EUR.USD", 1.1000, 1.1002);
        std::string reason;
        assert(venue.RestoreRiskState({{"EUR.USD", position}}, 0, reason));
    }
    OrderIntent Exit(const char* side, double quantity) const
    {
        OrderIntent order = Order(quantity);
        order.action = side;
        return order;
    }
    long Admit(const char* side, double quantity, bool activate = false)
    {
        long id = -1;
        const bool admitted = venue.PlaceOrderCorrelated(Contract(), Exit(side, quantity),
            "capacity-" + std::to_string(++sequence), &id, activate);
        if (!admitted)
            std::cerr << "capacity fixture rejected " << side << ' ' << quantity
                      << " position=" << venue.Position("EUR.USD")
                      << " reason=" << venue.LastRejectReason() << '\n';
        assert(admitted);
        return id;
    }
    void Block(const char* side, double quantity)
    {
        const auto order = Exit(side, quantity);
        const auto preview = venue.PreviewRisk(Contract(), order);
        assert(!preview.allow && preview.reasonCode == "RISK_FLATTEN_ONLY_BLOCK");
        long id = -1;
        assert(!venue.PlaceOrderCorrelated(Contract(), order, "blocked", &id, false));
        assert(id == -1 && venue.LastRejectReason() == preview.reasonCode);
    }
    unsigned sequence = 0;
};

void TestFlattenCapacityReservations()
{
    for (const bool monetary : {false, true})
    {
        for (const double sign : {1.0, -1.0})
        {
            FlattenFixture f(sign * 10.0, monetary);
            const char* side = sign > 0 ? "SELL" : "BUY";
            const long four = f.Admit(side, 4.0);
            const long six = f.Admit(side, 6.0);
            f.Block(side, 0.01); // Inactive reservations share the same quantity.
            f.venue.Process();
            assert(f.venue.Position("EUR.USD") == sign * 10.0);
            assert(f.venue.ActivateOrder(four));
            f.venue.Process();
            assert(f.venue.Position("EUR.USD") == sign * 6.0);
            f.Block(side, 0.01); // The remaining reservation still consumes six.
            assert(f.venue.ActivateOrder(six));
            f.venue.Process();
            assert(f.venue.Position("EUR.USD") == 0.0);
            assert(f.venue.ExecutionOrderIds() == std::set<long>({four, six}));
            assert(f.venue.ActiveOrderIds().empty());
        }
    }

    FlattenFixture f(10.0);
    std::atomic<bool> start(false);
    std::atomic<int> admitted(0);
    long ids[2] = {-1, -1};
    auto submit = [&](int index) {
        while (!start.load()) std::this_thread::yield();
        if (f.venue.PlaceOrderCorrelated(Contract(), f.Exit("SELL", 10.0),
                "concurrent-exit-" + std::to_string(index), &ids[index], false))
            ++admitted;
    };
    std::thread first(submit, 0), second(submit, 1);
    start.store(true);
    first.join(); second.join();
    assert(admitted.load() == 1 && "two exact exits must not both reserve ten units");
    assert(f.venue.ActiveOrderIds().size() == 1);
    assert(f.venue.ActivateOrder(ids[0] >= 0 ? ids[0] : ids[1]));
    f.venue.Process();
    assert(f.venue.Position("EUR.USD") == 0.0);
    assert(f.venue.ExecutionOrderIds().size() == 1);

    FlattenFixture separate(10.0);
    std::string reason;
    assert(separate.venue.RestoreRiskState(
        {{"EUR.USD", 10.0}, {"GBP.USD", 10.0}}, 0, reason));
    separate.venue.SetQuote("GBP.USD", 1.2500, 1.2502);
    separate.Admit("SELL", 10.0, true);
    auto sterling = Contract();
    sterling.symbol = "GBP";
    long sterlingId = -1;
    assert(separate.venue.PlaceOrder(sterling, separate.Exit("SELL", 10.0), &sterlingId));
    separate.venue.Process();
    assert(separate.venue.Position("EUR.USD") == 0.0);
    assert(separate.venue.Position("GBP.USD") == 0.0);
}

void TestFlattenCancellationAndStrictRemainder()
{
    FlattenFixture f(10.0);
    const long held = f.Admit("SELL", 10.0);
    assert(f.venue.CancelOrder(held));
    f.venue.Process(); // Unactivated cancel request is still unresolved.
    f.Block("SELL", 1.0);
    assert(f.venue.TerminalOrderStatuses().empty());
    std::string reason;
    assert(!f.venue.RestoreRiskState({{"EUR.USD", 100.0}}, 0, reason));
    assert(reason == "SIM_RISK_RESTORE_AFTER_ADMISSION");
    assert(f.venue.ActivateOrder(held));
    f.venue.Process();
    assert(f.venue.TerminalOrderStatuses().at(held) == "Cancelled");
    assert(f.venue.Position("EUR.USD") == 10.0);
    const long replacement = f.Admit("SELL", 10.0, true);
    f.venue.Process();
    assert(f.venue.Position("EUR.USD") == 0.0);
    assert(f.venue.ExecutionOrderIds() == std::set<long>({replacement}));

    // No tolerance grants 0.2 against the binary remainder of 0.3 - 0.1.
    // Admit the actual representable remainder; never normalize cash to zero.
    FlattenFixture fractional(0.3);
    const long first = fractional.Admit("SELL", 0.1);
    fractional.Block("SELL", 0.2);
    const long remainder = fractional.Admit("SELL", 0.3 - 0.1);
    assert(fractional.venue.ActivateOrder(first));
    assert(fractional.venue.ActivateOrder(remainder));
    fractional.venue.Process();
    assert(fractional.venue.Position("EUR.USD") == 0.0);
    assert(fractional.venue.ExecutionOrderIds().size() == 2);
}

void TestFlattenPolicyTransitionsAndFillBoundary()
{
    {
        FlattenFixture f(10.0);
        auto ordinary = f.risk;
        ordinary.flattenOnly = false;
        f.venue.SetRiskConfig(ordinary);
        const long existing = f.Admit("SELL", 6.0);
        f.venue.SetRiskConfig(f.risk);
        f.Block("SELL", 5.0); // Pre-policy ordinary orders consume capacity too.
        const long remaining = f.Admit("SELL", 4.0);
        f.venue.SetRiskConfig(ordinary);
        f.venue.SetRiskConfig(f.risk);
        f.Block("SELL", 0.01);
        assert(f.venue.ActivateOrder(existing));
        assert(f.venue.ActivateOrder(remaining));
        f.venue.Process();
        assert(f.venue.Position("EUR.USD") == 0.0);
    }
    {
        FlattenFixture f(10.0);
        const long reserved = f.Admit("SELL", 10.0);
        auto ordinary = f.risk;
        ordinary.flattenOnly = false;
        f.venue.SetRiskConfig(ordinary);
        const long consumed = f.Admit("SELL", 10.0, true);
        f.venue.Process();
        assert(f.venue.Position("EUR.USD") == 0.0);
        std::vector<SimulatedOrderEvent> events;
        f.venue.SetEventSink([&](const SimulatedOrderEvent& event) {
            assert(f.venue.Position("EUR.USD") == 0.0); // Sink remains reentrant.
            events.push_back(event);
        });
        const auto before = f.venue.RecoveryAuditSnapshot().generation;
        assert(f.venue.ActivateOrder(reserved));
        f.venue.Process();
        assert(f.venue.Position("EUR.USD") == 0.0);
        assert(events.size() == 2 && events[0].status == "Submitted" && events[1].status == "Rejected");
        assert(events[1].filledQuantity == 0.0 && events[1].remainingQuantity == 10.0);
        const auto audit = f.venue.RecoveryAuditSnapshot();
        assert(audit.complete && audit.generation > before && audit.activeOrderIds.empty());
        assert(audit.terminalStatuses.at(reserved) == "Rejected");
        assert(audit.executionOrderIds == std::set<long>({consumed}));
        f.venue.Process();
        assert(events.size() == 2); // No duplicate terminal event or economic fill.
    }
    {
        FlattenFixture f(10.0);
        auto ordinary = f.risk;
        ordinary.flattenOnly = false;
        f.venue.SetRiskConfig(ordinary);
        const long first = f.Admit("SELL", 10.0);
        const long duplicate = f.Admit("SELL", 10.0);
        const long increasing = f.Admit("BUY", 10.0);
        f.venue.SetRiskConfig(f.risk);
        assert(f.venue.ActivateOrder(first));
        assert(f.venue.ActivateOrder(duplicate));
        assert(f.venue.ActivateOrder(increasing));
        f.venue.Process();
        assert(f.venue.Position("EUR.USD") == 0.0);
        assert(f.venue.TerminalOrderStatuses().at(duplicate) == "Rejected");
        assert(f.venue.TerminalOrderStatuses().at(increasing) == "Rejected");
        assert(f.venue.ExecutionOrderIds() == std::set<long>({first}));
    }
    {
        FlattenFixture f(-10.0);
        auto ordinary = f.risk;
        ordinary.flattenOnly = false;
        f.venue.SetRiskConfig(ordinary);
        f.Admit("SELL", 10.0); // An unfilled short increase grants no exit capacity.
        f.venue.SetRiskConfig(f.risk);
        f.Block("BUY", 11.0);
        f.Admit("BUY", 10.0);
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
    TestFlattenCancellationAndStrictRemainder();
    TestFlattenPolicyTransitionsAndFillBoundary();
    TestPreviewAndFinalAdmissionRejectSameRisk();
    TestProductionRuntimePumpsAndJournalsEvents();
    std::cout << "simulator risk, reservation, activation and production runtime tests passed\n";
    return 0;
}
