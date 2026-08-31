#include "../HeptaTrade/execution/execution_coordinator.h"
#include "../HeptaTrade/tools/trading_tool_registry.h"
#include "../HeptaTrade/tool_host/trading_tool_host.h"
#include "../HeptaTrade/tool_host/unix_tool_client.h"
#include "../HeptaTrade/tool_host/unix_tool_server.h"

#include <atomic>
#include <cassert>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include <unistd.h>

namespace
{
std::string TempPath(const char* pattern)
{
    std::string value(pattern);
    std::vector<char> buffer(value.begin(), value.end());
    buffer.push_back('\0');
    const int fd = mkstemp(buffer.data());
    assert(fd >= 0);
    close(fd);
    unlink(buffer.data());
    return std::string(buffer.data());
}

void BindSchemaHash(const TradingToolRegistry& registry,
                    TradingToolHostRequest& request)
{
    TradingToolDescriptor descriptor;
    assert(registry.GetDescriptor(request.call.name, descriptor));
    request.expectedSchemaHash =
        TradingToolRegistry::DescriptorSchemaHash(descriptor);
}

bool Call(const std::string& socketPath,
          const TradingToolHostRequest& request,
          std::string& response)
{
    std::string reason;
    return UnixToolClient::Call(
        socketPath, request, response, reason, 6000, 65536);
}

template <typename Predicate>
bool WaitUntil(Predicate predicate, int timeoutMs)
{
    const std::chrono::steady_clock::time_point deadline =
        std::chrono::steady_clock::now() +
        std::chrono::milliseconds(timeoutMs);
    while (std::chrono::steady_clock::now() < deadline)
    {
        if (predicate()) return true;
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    return predicate();
}

struct ActiveRequestGate
{
    std::mutex mutex;
    std::condition_variable changed;
    bool entered = false;
    bool release = false;
};

void TestDeterministicOwnerBackpressureAndCrossOwnerProgress()
{
    const std::string journalPath =
        TempPath("/tmp/hepta-owner-pressure-journal-XXXXXX");
    const std::string socketPath =
        TempPath("/tmp/hepta-owner-pressure-socket-XXXXXX");

    OmsJournal journal;
    assert(journal.Init(journalPath));
    ExecutionCoordinator execution(journal, ExecutionCoordinatorCallbacks());

    ActiveRequestGate gate;
    std::atomic<int> unrelatedExecutions(0);
    TradingToolReadCallbacks reads;
    reads.marketGetQuote = [&](const TradingToolSession& session,
                               const TradingToolCall& call,
                               std::string& payload,
                               std::string&) {
        const std::string& callId = session.executionContext.toolCallId;
        if (callId == "owner-pressure-active")
        {
            std::unique_lock<std::mutex> lock(gate.mutex);
            gate.entered = true;
            gate.changed.notify_all();
            gate.changed.wait(lock, [&]() { return gate.release; });
        }
        else if (callId == "owner-pressure-unrelated")
        {
            ++unrelatedExecutions;
        }
        payload = std::string("{\"agent\":\"") +
            session.executionContext.agentId +
            "\",\"instrument\":\"" + call.instrument + "\"}";
        return true;
    };

    TradingToolRegistry registry(execution, reads);
    TradingToolHost host(registry);
    std::string reason;

    TradingToolHostSessionBinding primary;
    primary.token = "owner-pressure-primary-token-0001";
    primary.peerUid = static_cast<std::uint32_t>(getuid());
    primary.session.executionContext.agentId = "owner-pressure-agent-a";
    primary.session.executionContext.sessionId = "owner-pressure-session-a";
    primary.session.executionContext.account = "DU123";
    primary.session.environment = "WATCH";
    primary.session.capabilities.insert("market.read");
    primary.executionDomain = "IB-PAPER";
    primary.expiresAtMs =
        static_cast<std::uint64_t>(OmsJournal::NowEpochMs()) + 60000;
    assert(host.RegisterSession(primary, reason));

    TradingToolHostSessionBinding unrelated = primary;
    unrelated.token = "owner-pressure-unrelated-token-01";
    unrelated.session.executionContext.agentId = "owner-pressure-agent-b";
    unrelated.session.executionContext.sessionId = "owner-pressure-session-b";
    assert(host.RegisterSession(unrelated, reason));

    UnixToolServer server(host);
    server.AllowMissingDecisionAuditForTests();
    std::atomic<int> observerEvents(0);
    std::atomic<bool> observerMismatch(false);
    server.SetBackpressureObserver(
        [&](const TradingToolHostSessionBinding& observed,
            const std::string& reasonCode) {
            if (observed.session.executionContext.agentId !=
                    primary.session.executionContext.agentId ||
                reasonCode != "OWNER_QUEUE_BACKPRESSURE")
            {
                observerMismatch.store(true);
            }
            ++observerEvents;
        });

    // Two execution workers permit owner B to progress while owner A is held.
    // Owner A has one active slot and exactly one pending queue slot.
    assert(server.Start(socketPath, reason,
        4096, 2000, 2, 16, 1, 1, 2, 5000));

    auto request = [&](const TradingToolHostSessionBinding& binding,
                       const std::string& callId) {
        TradingToolHostRequest value;
        value.sessionToken = binding.token;
        value.toolCallId = callId;
        value.call.name = "market.get_quote";
        value.call.instrument = "EUR.USD";
        BindSchemaHash(registry, value);
        return value;
    };

    std::string activeResponse;
    bool activeCallOk = false;
    std::thread active([&]() {
        activeCallOk = Call(socketPath,
            request(primary, "owner-pressure-active"), activeResponse);
    });

    bool activeEntered = false;
    {
        std::unique_lock<std::mutex> lock(gate.mutex);
        activeEntered = gate.changed.wait_for(
            lock, std::chrono::seconds(2), [&]() { return gate.entered; });
    }

    std::string pendingResponse;
    bool pendingCallOk = false;
    std::thread pending;
    if (activeEntered)
    {
        pending = std::thread([&]() {
            pendingCallOk = Call(socketPath,
                request(primary, "owner-pressure-pending"),
                pendingResponse);
        });
    }

    const bool queueIsProvablyFull = activeEntered && WaitUntil([&]() {
        const UnixToolServerHealth health = server.GetHealth();
        return health.activeRequests == 1 &&
               health.pendingConnections == 1 &&
               health.readyOwners >= 1;
    }, 2000);

    std::string rejectedResponse;
    const bool rejectedCallOk = queueIsProvablyFull && Call(
        socketPath,
        request(primary, "owner-pressure-rejected"),
        rejectedResponse);

    const bool rejectionObserved = rejectedCallOk && WaitUntil([&]() {
        return server.GetHealth().ownerBackpressureRejections == 1;
    }, 2000);

    std::string unrelatedResponse;
    const bool unrelatedCallOk = rejectionObserved && Call(
        socketPath,
        request(unrelated, "owner-pressure-unrelated"),
        unrelatedResponse);

    // Release and join before any assertion so a failed observation can never
    // strand a worker, socket or test thread.
    {
        std::lock_guard<std::mutex> lock(gate.mutex);
        gate.release = true;
    }
    gate.changed.notify_all();
    if (pending.joinable()) pending.join();
    if (active.joinable()) active.join();
    server.Stop();

    const UnixToolServerHealth finalHealth = server.GetHealth();
    std::remove(socketPath.c_str());
    std::remove(journalPath.c_str());

    assert(activeEntered);
    assert(queueIsProvablyFull);
    assert(rejectedCallOk);
    assert(rejectionObserved);
    assert(rejectedResponse.find("OWNER_QUEUE_BACKPRESSURE") !=
        std::string::npos);
    assert(unrelatedCallOk);
    assert(unrelatedResponse.find("\"agent\":\"owner-pressure-agent-b\"") !=
        std::string::npos);
    assert(unrelatedExecutions.load() == 1);
    assert(activeCallOk);
    assert(pendingCallOk);
    assert(activeResponse.find("\"status\":\"ok\"") !=
        std::string::npos);
    assert(pendingResponse.find("\"status\":\"ok\"") !=
        std::string::npos);
    assert(observerEvents.load() == 1);
    assert(!observerMismatch.load());
    assert(finalHealth.ownerBackpressureRejections == 1);
    assert(finalHealth.pendingConnections == 0);
    assert(finalHealth.activeRequests == 0);
}
}

int main()
{
    TestDeterministicOwnerBackpressureAndCrossOwnerProgress();
    return 0;
}
