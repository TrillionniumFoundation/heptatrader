#include "../HeptaTrade/execution/execution_service_runtime_composition.h"
#include "../HeptaTrade/execution/unix_execution_service_client.h"
#include "../HeptaTrade/oms_journal.h"

#include <algorithm>
#include <cassert>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <set>
#include <string>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <thread>
#include <unistd.h>

#ifndef HEPTA_SOURCE_ROOT
#error HEPTA_SOURCE_ROOT is required for the real lifecycle maintenance test
#endif

namespace {

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

InstrumentRef Contract()
{
    InstrumentRef contract;
    contract.symbol = "EUR";
    contract.currency = "USD";
    contract.secType = "CASH";
    contract.exchange = "SIM";
    return contract;
}

OrderIntent Order(double quantity, const std::string& type = "MKT")
{
    OrderIntent order;
    order.action = "BUY";
    order.orderType = type;
    order.totalQuantity = quantity;
    if (type == "LMT") order.lmtPrice = 1.0;
    return order;
}

std::string JsonString(const std::string& json, const std::string& key)
{
    const std::string prefix = "\"" + key + "\":\"";
    const std::size_t start = json.find(prefix);
    assert(start != std::string::npos);
    const std::size_t begin = start + prefix.size();
    const std::size_t end = json.find('"', begin);
    assert(end != std::string::npos);
    return json.substr(begin, end - begin);
}

ExecutionCommandResult PreviewThenPlace(UnixExecutionServiceClient& client,
                                        PlaceOrderCommand& command)
{
    const ExecutionCommandResult preview = client.PreviewOrder(command);
    assert(preview.status == ExecutionCommandStatus::Accepted);
    command.previewPermit = JsonString(preview.detail, "preview_permit");
    command.context.toolCallId = JsonString(preview.detail, "command_id");
    const ExecutionCommandResult placed = client.PlaceOrder(command);
    assert(placed.status == ExecutionCommandStatus::Accepted);
    return placed;
}

void AwaitStatus(ExecutionServiceRuntimeComposition& runtime,
                 std::uint64_t& cursor, long orderId,
                 const std::string& status)
{
    const std::chrono::steady_clock::time_point deadline =
        std::chrono::steady_clock::now() + std::chrono::seconds(3);
    while (std::chrono::steady_clock::now() < deadline)
    {
        ExecutionEvent event;
        if (runtime.EventHub().WaitNext("SIM:generation-test", "generation-test",
                                       "session", cursor, 100, event))
        {
            cursor = event.sequence;
            if (event.orderId == orderId && event.status == status) return;
        }
    }
    assert(false && "simulator lifecycle status timeout");
}

PlaceOrderCommand Command(const std::string& commandId,
                          const OrderIntent& order)
{
    PlaceOrderCommand command;
    command.context.agentId = "generation-test";
    command.context.sessionId = "session";
    command.context.toolCallId = commandId;
    command.context.account = "SIM";
    command.context.venue = "SIMULATOR";
    command.context.executionDomain = "SIM:generation-test";
    command.contract = Contract();
    command.order = order;
    command.instrument = "EUR.USD";
    command.timeInForce = "DAY";
    command.expiresAtMs = OmsJournal::NowEpochMs() + 10000;
    return command;
}

ExecutionServiceRuntimeConfig RuntimeConfig(const std::string& path,
                                            const std::string& mutationSocket,
                                            const std::string& eventSocket,
                                            const std::string& credential)
{
    ExecutionServiceRuntimeConfig config;
    config.mode = ExecutionServiceRuntimeMode::Simulator;
    config.listenFd = Listener(mutationSocket);
    config.eventListenFd = Listener(eventSocket);
    config.allowedGatewayUids.insert(static_cast<std::uint32_t>(::geteuid()));
    config.gatewayContextBinding.agentId = "generation-test";
    config.gatewayContextBinding.account = "SIM";
    config.gatewayContextBinding.venue = "SIMULATOR";
    config.gatewayContextBinding.executionDomain = "SIM:generation-test";
    config.stateDirectory = path;
    config.journalPath = path + "/oms-journal.jsonl";
    config.fenceCredentialPath = credential;
    config.simulatorQuoteRefreshIntervalMs = 20;
    return config;
}

std::string ShellQuote(const std::string& value)
{
    std::string out("'");
    for (std::string::const_iterator it = value.begin(); it != value.end(); ++it)
    {
        if (*it == '\'') out += "'\\''";
        else out.push_back(*it);
    }
    out += "'";
    return out;
}

void RunLifecycle(const std::string& action,
                  const std::string& journal,
                  const std::string& store)
{
    const std::string script = std::string(HEPTA_SOURCE_ROOT) +
        "/scripts/hepta_oms_lifecycle.py";
    std::string command = "PYTHONDONTWRITEBYTECODE=1 python3 " + ShellQuote(script) +
        " " + action + " --journal " + ShellQuote(journal) +
        " --store " + ShellQuote(store);
    if (action == "seal") command += " --stopped-state";
    const int status = std::system(command.c_str());
    assert(status == 0);
}

void TestStopSealRestart()
{
    // Production deliberately rejects a root Gateway identity. Hosted CI runs
    // this as an unprivileged user; root-only local containers skip it.
    if (::geteuid() == 0) return;

    char directoryTemplate[] = "/tmp/hepta-simulator-generation-XXXXXX";
    char* directory = ::mkdtemp(directoryTemplate);
    assert(directory != NULL);
    const std::string path(directory);
    const std::string journal = path + "/oms-journal.jsonl";
    const std::string store = journal + ".generations";
    const std::string credential = path + "/hepta-execution-fence";
    const std::string mutationSocket = path + "/mutation.sock";
    const std::string eventSocket = path + "/event.sock";
    {
        std::ofstream output(credential.c_str());
        output << "HFC1\nfencing_token=1\ngeneration=1\n";
    }
    assert(::chmod(credential.c_str(), 0400) == 0);

    long filledId = -1;
    long cancelledId = -1;
    PlaceOrderCommand original = Command("initial-fill", Order(10.0));
    {
        ExecutionServiceRuntimeConfig config = RuntimeConfig(
            path, mutationSocket, eventSocket, credential);
        ExecutionServiceRuntimeComposition runtime(config);
        std::string reason;
        assert(runtime.Start(reason));
        UnixExecutionServiceClient client(mutationSocket);
        filledId = PreviewThenPlace(client, original).orderId;
        std::uint64_t cursor = 0;
        AwaitStatus(runtime, cursor, filledId, "Filled");
        assert(runtime.Venue().Position("EUR.USD") == 10.0);

        PlaceOrderCommand resting = Command("resting", Order(1.0, "LMT"));
        cancelledId = PreviewThenPlace(client, resting).orderId;
        AwaitStatus(runtime, cursor, cancelledId, "Submitted");
        CancelOrderCommand cancel;
        cancel.context = resting.context;
        cancel.context.toolCallId = "cancel-resting";
        cancel.orderId = cancelledId;
        cancel.instrument = resting.instrument;
        cancel.side = "BUY";
        assert(client.CancelOrder(cancel).status == ExecutionCommandStatus::Accepted);
        AwaitStatus(runtime, cursor, cancelledId, "Cancelled");
        runtime.Stop();
    }

    RunLifecycle("seal", journal, store);
    RunLifecycle("verify", journal, store);
    {
        std::ifstream input(journal.c_str());
        std::string first;
        assert(std::getline(input, first));
        assert(first.find("HEPTA_OMS_ACTIVE_TAIL_V1\t") == 0);
    }

    ::unlink(mutationSocket.c_str());
    ::unlink(eventSocket.c_str());
    ExecutionServiceRuntimeConfig config = RuntimeConfig(
        path, mutationSocket, eventSocket, credential);
    {
        ExecutionServiceRuntimeComposition restarted(config);
        std::string reason;
        if (!restarted.Start(reason)) std::cerr << reason << '\n';
        assert(restarted.IsRunning());
        assert(restarted.Venue().Position("EUR.USD") == 10.0);
        assert(restarted.Venue().ActiveOrderIds().empty());

        PreTradeRiskConfig restoredRisk;
        restoredRisk.enableOrderSubmission = true;
        restoredRisk.maxOrderQuantity = 1000.0;
        restoredRisk.maxDailyOrders = 2;
        restoredRisk.maxPriceDeviationBps = 0.0;
        restoredRisk.maxOrderNotional = 2500.0;
        restoredRisk.maxWorstCaseGrossNotional = 10000.0;
        restoredRisk.maxSnapshotAgeMs = 60000;
        restarted.Venue().SetRiskConfig(restoredRisk);
        const PreTradeRiskDecision daily = restarted.Venue().PreviewRisk(
            Contract(), Order(1.0));
        assert(!daily.allow && daily.reasonCode == "RISK_DAILY_ORDER_LIMIT");

        UnixExecutionServiceClient client(mutationSocket);
        const ExecutionCommandResult replay = client.PlaceOrder(original);
        assert(replay.status == ExecutionCommandStatus::Accepted);
        assert(replay.orderId == filledId);
        assert(restarted.Venue().ActiveOrderIds().empty());
        assert(restarted.Venue().Position("EUR.USD") == 10.0);

        restoredRisk.maxDailyOrders = 100;
        restarted.Venue().SetRiskConfig(restoredRisk);
        PlaceOrderCommand next = Command("post-generation", Order(1.0, "LMT"));
        const ExecutionCommandResult placed = PreviewThenPlace(client, next);
        assert(placed.orderId > std::max(filledId, cancelledId));
        restarted.Stop();
    }

    const std::string cleanup = "rm -rf -- " + ShellQuote(path);
    assert(std::system(cleanup.c_str()) == 0);
}

} // namespace

int main()
{
    TestStopSealRestart();
    std::cout << "simulator stop/seal/restart generation test passed\n";
    return 0;
}
