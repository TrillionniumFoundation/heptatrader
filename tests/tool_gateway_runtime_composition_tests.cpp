#include "../HeptaTrade/execution/execution_service_runtime_composition.h"
#include "../HeptaTrade/execution/execution_event_feed_server.h"
#include "../HeptaTrade/execution/unix_execution_service_server.h"
#include "../HeptaTrade/client/native_tool_client.h"
#include "../HeptaTrade/tool_host/tool_gateway_runtime_composition.h"
#include "../HeptaTrade/tool_host/session_supervisor_audit_journal.h"
#include "../HeptaTrade/tool_host/unix_session_supervisor_client.h"
#include "../HeptaTrade/tool_host/unix_tool_client.h"
#include "../HeptaTrade/tool_host/typed_tool_protocol.h"
#include "../HeptaTrade/oms_journal.h"

#include <cassert>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <string>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <thread>
#include <unistd.h>
#include <vector>

namespace
{
class BarrierAuthority : public ExecutionAuthority,
                         public ExecutionControlAuthority,
                         public ExecutionReadAuthority
{
public:
    BarrierAuthority() : mutationCalls(0) {}

    ExecutionCommandResult PlaceOrder(
        const PlaceOrderCommand& command) override
    {
        mutationCalls.fetch_add(1);
        return AcceptedCommand(command.context.toolCallId);
    }

    ExecutionCommandResult CancelOrder(
        const CancelOrderCommand& command) override
    {
        mutationCalls.fetch_add(1);
        return AcceptedCommand(command.context.toolCallId);
    }

    ExecutionControlResult QueryCommandStatus(
        const ExecutionControlCommand& command) override
    {
        return AcceptedControl(command);
    }

    ExecutionControlResult FenceSessionOwner(
        const ExecutionControlCommand& command) override
    {
        mutationCalls.fetch_add(1);
        return AcceptedControl(command);
    }

    ExecutionControlResult ReleaseSessionOwnerFence(
        const ExecutionControlCommand& command) override
    {
        mutationCalls.fetch_add(1);
        return AcceptedControl(command);
    }

    ExecutionControlResult ReconcileAuthoritativeState(
        const ExecutionControlCommand& command) override
    {
        mutationCalls.fetch_add(1);
        return AcceptedControl(command);
    }

    ExecutionCommandResult PreviewOrder(
        const PlaceOrderCommand& command) override
    {
        ExecutionCommandResult result = AcceptedCommand(
            command.context.toolCallId);
        result.detail = "{\"authoritative\":true}";
        return result;
    }

    ExecutionCommandResult ReadAuthoritativeState(
        const ExecutionReadCommand& command) override
    {
        ExecutionCommandResult result = AcceptedCommand(
            command.context.toolCallId);
        result.detail = "{\"authoritative\":true}";
        return result;
    }

    std::atomic<int> mutationCalls;

private:
    static ExecutionCommandResult AcceptedCommand(const std::string& commandId)
    {
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Accepted;
        result.commandId = commandId;
        return result;
    }

    static ExecutionControlResult AcceptedControl(
        const ExecutionControlCommand& command)
    {
        ExecutionControlResult result;
        result.status = ExecutionCommandStatus::Accepted;
        result.commandId = command.context.toolCallId;
        result.targetCommandId = command.targetCommandId;
        return result;
    }
};

std::string TempDirectory(const char* pattern)
{
    std::string value(pattern);
    std::vector<char> buffer(value.begin(), value.end());
    buffer.push_back('\0');
    assert(::mkdtemp(buffer.data()) != nullptr);
    assert(::chmod(buffer.data(), 0700) == 0);
    return std::string(buffer.data());
}

std::string TempSocket(const char* pattern)
{
    std::string value(pattern);
    std::vector<char> buffer(value.begin(), value.end());
    buffer.push_back('\0');
    const int descriptor = ::mkstemp(buffer.data());
    assert(descriptor >= 0);
    ::close(descriptor);
    ::unlink(buffer.data());
    return std::string(buffer.data()) + ".sock";
}

int ActivatedSocket(const std::string& path)
{
    const int descriptor = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    assert(descriptor >= 0);
    sockaddr_un address;
    std::memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    std::strncpy(address.sun_path, path.c_str(), sizeof(address.sun_path) - 1);
    assert(::bind(descriptor, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0);
    assert(::chmod(path.c_str(), 0600) == 0);
    assert(::listen(descriptor, 8) == 0);
    return descriptor;
}

void WriteCredential(const std::string& directory)
{
    const std::string path = directory + "/hepta-execution-fence";
    std::ofstream output(path.c_str());
    assert(output.is_open());
    output << "HFC1\nfencing_token=77\ngeneration=9\n";
    output.close();
    assert(::chmod(path.c_str(), 0400) == 0);
}

void WriteLeaseKey(const std::string& path)
{
    const int descriptor = ::open(
        path.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
    assert(descriptor >= 0);
    const std::string key(32, 'L');
    assert(::write(descriptor, key.data(), key.size()) ==
           static_cast<ssize_t>(key.size()));
    assert(::fsync(descriptor) == 0);
    ::close(descriptor);
}

std::string ToolCall(const std::string& socketPath,
                     const std::string& token,
                     const std::string& callId,
                     const TradingToolCall& call,
                     TypedToolResultEnvelope& envelope)
{
    static std::map<std::string, std::unique_ptr<NativeToolClient> > clients;
    const std::string clientKey = socketPath + "\n" + token;
    std::map<std::string, std::unique_ptr<NativeToolClient> >::iterator client =
        clients.find(clientKey);
    if (client == clients.end())
    {
        NativeToolClientConfig config;
        config.socketPath = socketPath;
        config.sessionToken = token;
        config.timeoutMs = 3000;
        client = clients.insert(std::make_pair(
            clientKey, std::unique_ptr<NativeToolClient>(
                new NativeToolClient(config)))).first;
    }
    TradingToolHostRequest request;
    request.toolCallId = callId;
    request.call = call;
    std::string reason;
    NativeToolClientResult result;
    const bool called = client->second->Call(request, result, reason);
    if (!called)
        std::cerr << "ToolCall failed call_id=" << callId << " reason=" << reason << '\n';
    assert(called);
    envelope = result.envelope;
    return result.responseJson;
}

std::string PreviewField(const std::string& response,
                         const std::string& name)
{
    const std::string marker = "\"" + name + "\":\"";
    const std::size_t start = response.find(marker);
    assert(start != std::string::npos);
    const std::size_t valueStart = start + marker.size();
    const std::size_t end = response.find('"', valueStart);
    assert(end != std::string::npos);
    return response.substr(valueStart, end - valueStart);
}

void TestGatewayReadinessWaitsPastReconnectBudget()
{
    const std::string executionSocket =
        TempSocket("/tmp/hepta-gateway-readiness-execution-XXXXXX");
    const std::string eventSocket =
        TempSocket("/tmp/hepta-gateway-readiness-events-XXXXXX");
    const std::string toolSocket =
        TempSocket("/tmp/hepta-gateway-readiness-tools-XXXXXX");
    const std::string supervisorSocket =
        TempSocket("/tmp/hepta-gateway-readiness-supervisor-XXXXXX");
    const int executionFd = ActivatedSocket(executionSocket);
    const int eventFd = ActivatedSocket(eventSocket);
    const std::set<std::uint32_t> uid{
        static_cast<std::uint32_t>(::geteuid())};
    const ExecutionServiceIdentity identity{
        "gateway-readiness-test", 1};
    const std::shared_ptr<ExecutionServiceLifecycleGate> gate(
        new ExecutionServiceLifecycleGate());
    BarrierAuthority authority;
    UnixExecutionServiceServer executionServer(authority, &authority);
    ExecutionEventHub eventHub(8, identity.serviceEpoch);
    UnixExecutionEventFeedServer eventServer(eventHub, identity, gate);
    std::string reason;
    assert(executionServer.StartFromFd(
        executionFd, uid, identity, gate, reason));
    assert(eventServer.StartFromFd(eventFd, uid, reason));

    ExecutionGatewayRuntimeConfig executionConfig;
    executionConfig.mode = ExecutionGatewayMode::Simulator;
    executionConfig.executionSocket = executionSocket;
    executionConfig.eventSocket = eventSocket;
    executionConfig.executionServiceUid = static_cast<std::uint32_t>(::geteuid());
    executionConfig.executionServiceUidConfigured = true;
    executionConfig.ioTimeoutMs = 1000;

    AgentOsRuntimeConfig agentConfig;
    agentConfig.toolSocket = toolSocket;
    agentConfig.supervisorSocket = supervisorSocket;
    agentConfig.supervisorUid = static_cast<std::uint32_t>(::geteuid());
    agentConfig.agentUid = static_cast<std::uint32_t>(::geteuid());
    agentConfig.allowMissingAuditForTests = true;
    std::map<std::string, std::string> values;
    values["HEPTA_TOOL_AGENT_ID"] = "readiness-agent";
    values["HEPTA_TOOL_ACCOUNT"] = "SIM";
    values["HEPTA_EXECUTION_DOMAIN_ID"] = "SIM:SIM";
    values["HEPTA_TOOL_SESSION_TEMPLATES"] = "watch";
    values["HEPTA_TOOL_MAX_ORDER_QTY"] = "1";
    values["HEPTA_TOOL_MAX_TRADE_CALLS_PER_MIN"] = "1";
    values["HEPTA_TOOL_CONTRACT_BINDINGS"] =
        "EUR.USD|EUR|CASH|IDEALPRO|USD";
    ToolGatewaySessionPolicy policy;
    assert(ToolGatewaySessionPolicy::FromValues(
        values, executionConfig, agentConfig, policy, reason));

    // Advance a fake monotonic clock by ten seconds per poll. This reaches
    // 181 seconds without a real long wait, proving the old 30-second barrier
    // (and the 180-second reconnect budget) would fail while the 240-second
    // initial-start bound still waits.
    std::mutex readinessMutex;
    std::condition_variable readinessChanged;
    bool firstReadinessSleep = false;
    bool releaseReadinessSleep = false;
    std::uint64_t fakeReadinessMs = 0;
    ToolGatewayRuntimeTestHooks hooks;
    hooks.readinessNow = [&]() {
        std::lock_guard<std::mutex> lock(readinessMutex);
        return std::chrono::steady_clock::time_point(
            std::chrono::milliseconds(fakeReadinessMs));
    };
    hooks.readinessSleep = [&](std::chrono::milliseconds duration) {
        std::unique_lock<std::mutex> lock(readinessMutex);
        assert(duration.count() == 100);
        fakeReadinessMs += 10000;
        if (fakeReadinessMs >= 181000) gate->ready.store(true);
        if (!firstReadinessSleep)
        {
            firstReadinessSleep = true;
            readinessChanged.notify_all();
            readinessChanged.wait(lock, [&]() { return releaseReadinessSleep; });
        }
    };
    ToolGatewayRuntimeComposition gateway(
        executionConfig, agentConfig, policy, hooks);

    std::atomic<bool> startReturned(false);
    std::string startReason;
    const std::chrono::steady_clock::time_point wallStart =
        std::chrono::steady_clock::now();
    std::thread starter([&]() {
        const bool started = gateway.Start(startReason);
        assert(started);
        startReturned.store(true);
    });
    {
        std::unique_lock<std::mutex> lock(readinessMutex);
        assert(readinessChanged.wait_for(lock, std::chrono::seconds(2),
            [&]() { return firstReadinessSleep; }));
    }
    assert(!startReturned.load());
    // Agent OS sockets are created only after the readiness barrier.  Check
    // the path, rather than racing IsRunning() against the starter thread.
    assert(::access(toolSocket.c_str(), F_OK) != 0);
    {
        std::lock_guard<std::mutex> lock(readinessMutex);
        releaseReadinessSleep = true;
    }
    readinessChanged.notify_all();
    starter.join();
    assert(startReturned.load());
    assert(startReason.empty());
    assert(gateway.IsRunning());
    {
        std::lock_guard<std::mutex> lock(readinessMutex);
        assert(fakeReadinessMs >= 181000);
    }
    assert(std::chrono::duration_cast<std::chrono::seconds>(
        std::chrono::steady_clock::now() - wallStart).count() < 5);

    gateway.Stop();
    eventServer.Stop();
    executionServer.Stop();
    ::unlink(executionSocket.c_str());
    ::unlink(eventSocket.c_str());
    ::unlink(toolSocket.c_str());
    ::unlink(supervisorSocket.c_str());
}

// A service stop can arrive while the Gateway is waiting for authenticated
// execution/event identities. The owner-supplied cancellation probe must
// abort that bounded wait promptly, tear down the remote IPC clients, and
// never publish either Agent OS socket (or enter any mutation surface).
void TestGatewayStartupCancellationProbeAbortsReadinessWait()
{
    const std::string executionSocket =
        TempSocket("/tmp/hepta-gateway-startup-cancel-execution-XXXXXX");
    const std::string eventSocket =
        TempSocket("/tmp/hepta-gateway-startup-cancel-events-XXXXXX");
    const std::string toolSocket =
        TempSocket("/tmp/hepta-gateway-startup-cancel-tools-XXXXXX");
    const std::string supervisorSocket =
        TempSocket("/tmp/hepta-gateway-startup-cancel-supervisor-XXXXXX");
    const int executionFd = ActivatedSocket(executionSocket);
    const int eventFd = ActivatedSocket(eventSocket);
    const std::set<std::uint32_t> uid{
        static_cast<std::uint32_t>(::geteuid())};
    const ExecutionServiceIdentity identity{
        "gateway-startup-cancel-test", 1};
    const std::shared_ptr<ExecutionServiceLifecycleGate> gate(
        new ExecutionServiceLifecycleGate());
    BarrierAuthority authority;
    UnixExecutionServiceServer executionServer(authority, &authority);
    ExecutionEventHub eventHub(8, identity.serviceEpoch);
    UnixExecutionEventFeedServer eventServer(eventHub, identity, gate);
    std::string reason;
    assert(executionServer.StartFromFd(
        executionFd, uid, identity, gate, reason));
    assert(eventServer.StartFromFd(eventFd, uid, reason));

    ExecutionGatewayRuntimeConfig executionConfig;
    executionConfig.mode = ExecutionGatewayMode::Simulator;
    executionConfig.executionSocket = executionSocket;
    executionConfig.eventSocket = eventSocket;
    executionConfig.executionServiceUid = static_cast<std::uint32_t>(::geteuid());
    executionConfig.executionServiceUidConfigured = true;
    executionConfig.ioTimeoutMs = 1000;

    AgentOsRuntimeConfig agentConfig;
    agentConfig.toolSocket = toolSocket;
    agentConfig.supervisorSocket = supervisorSocket;
    agentConfig.supervisorUid = static_cast<std::uint32_t>(::geteuid());
    agentConfig.agentUid = static_cast<std::uint32_t>(::geteuid());
    agentConfig.allowMissingAuditForTests = true;
    std::map<std::string, std::string> values;
    values["HEPTA_TOOL_AGENT_ID"] = "startup-cancel-agent";
    values["HEPTA_TOOL_ACCOUNT"] = "SIM";
    values["HEPTA_EXECUTION_DOMAIN_ID"] = "SIM:SIM";
    values["HEPTA_TOOL_SESSION_TEMPLATES"] = "watch";
    values["HEPTA_TOOL_MAX_ORDER_QTY"] = "1";
    values["HEPTA_TOOL_MAX_TRADE_CALLS_PER_MIN"] = "1";
    values["HEPTA_TOOL_CONTRACT_BINDINGS"] =
        "EUR.USD|EUR|CASH|IDEALPRO|USD";
    ToolGatewaySessionPolicy policy;
    assert(ToolGatewaySessionPolicy::FromValues(
        values, executionConfig, agentConfig, policy, reason));

    std::mutex readinessMutex;
    std::condition_variable readinessChanged;
    bool firstReadinessSleep = false;
    bool releaseReadinessSleep = false;
    std::uint64_t fakeReadinessMs = 0;
    std::atomic<bool> cancelRequested(false);
    ToolGatewayRuntimeTestHooks hooks;
    hooks.readinessNow = [&]() {
        std::lock_guard<std::mutex> lock(readinessMutex);
        return std::chrono::steady_clock::time_point(
            std::chrono::milliseconds(fakeReadinessMs));
    };
    hooks.readinessSleep = [&](std::chrono::milliseconds duration) {
        std::unique_lock<std::mutex> lock(readinessMutex);
        assert(duration.count() == 100);
        fakeReadinessMs += static_cast<std::uint64_t>(duration.count());
        if (!firstReadinessSleep)
        {
            firstReadinessSleep = true;
            readinessChanged.notify_all();
            readinessChanged.wait(lock, [&]() {
                return releaseReadinessSleep;
            });
        }
    };
    ToolGatewayRuntimeComposition gateway(
        executionConfig, agentConfig, policy, hooks);
    gateway.SetStartupCancellationProbe([&cancelRequested]() {
        return cancelRequested.load();
    });

    std::atomic<bool> startReturned(false);
    bool started = true;
    std::string startReason;
    const std::chrono::steady_clock::time_point wallStart =
        std::chrono::steady_clock::now();
    std::thread starter([&]() {
        started = gateway.Start(startReason);
        startReturned.store(true);
    });
    {
        std::unique_lock<std::mutex> lock(readinessMutex);
        assert(readinessChanged.wait_for(lock, std::chrono::seconds(2),
            [&]() { return firstReadinessSleep; }));
    }
    assert(!startReturned.load());
    assert(::access(toolSocket.c_str(), F_OK) != 0);
    assert(::access(supervisorSocket.c_str(), F_OK) != 0);

    cancelRequested.store(true);
    {
        std::lock_guard<std::mutex> lock(readinessMutex);
        releaseReadinessSleep = true;
    }
    readinessChanged.notify_all();
    starter.join();

    assert(!started);
    assert(startReason == "TOOL_GATEWAY_STARTUP_CANCELLED");
    assert(!gateway.IsRunning());
    assert(::access(toolSocket.c_str(), F_OK) != 0);
    assert(::access(supervisorSocket.c_str(), F_OK) != 0);
    assert(authority.mutationCalls.load() == 0);
    assert(std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - wallStart).count() < 2000);

    gateway.Stop();
    eventServer.Stop();
    executionServer.Stop();
    ::unlink(executionSocket.c_str());
    ::unlink(eventSocket.c_str());
    ::unlink(toolSocket.c_str());
    ::unlink(supervisorSocket.c_str());
}
}

int main(int argc, char** argv)
{
    if (argc == 2 &&
        std::string(argv[1]) == "--startup-cancellation-only")
    {
        TestGatewayStartupCancellationProbeAbortsReadinessWait();
        return 0;
    }
    assert(argc == 1);
    TestGatewayReadinessWaitsPastReconnectBudget();
    TestGatewayStartupCancellationProbeAbortsReadinessWait();

    std::uint32_t connectorCount = 99;
    assert(ToolGatewayRuntimeComposition::
        ValidateExternalAuthoritativeHealth(
            "{\"source\":\"IB\",\"authoritative\":true,"
            "\"paper_order_mode\":\"EXTERNAL_P1_CANARY_LMT_DAY\","
            "\"authorized_connector_count\":0}", connectorCount));
    assert(connectorCount == 0);
    assert(ToolGatewayRuntimeComposition::
        ValidateExternalAuthoritativeHealth(
            "{\"source\":\"IB\",\"authoritative\":true,"
            "\"paper_order_mode\":\"EXTERNAL_P1_CANARY_LMT_DAY\","
            "\"authorized_connector_count\":1}", connectorCount));
    assert(connectorCount == 1);
    const char* invalidExternalHealth[] = {
        "{\"source\":\"IB\",\"authoritative\":true,"
            "\"paper_order_mode\":\"EXTERNAL_P1_CANARY_LMT_DAY\"}",
        "{\"source\":\"IB\",\"authoritative\":true,"
            "\"paper_order_mode\":\"EXTERNAL_P1_CANARY_LMT_DAY\","
            "\"authorized_connector_count\":\"1\"}",
        "{\"source\":\"IB\",\"authoritative\":true,"
            "\"paper_order_mode\":\"EXTERNAL_P1_CANARY_LMT_DAY\","
            "\"authorized_connector_count\":2}",
        "{\"source\":\"IB\",\"authoritative\":true,"
            "\"paper_order_mode\":\"LOCAL_MKT_DAY\","
            "\"authorized_connector_count\":1}",
        "{\"source\":\"IB\",\"authoritative\":true,"
            "\"paper_order_mode\":\"EXTERNAL_P1_CANARY_LMT_DAY\","
            "\"authorized_connector_count\":1,"
            "\"authorized_connector_count\":0}"
    };
    for (std::size_t i = 0;
         i < sizeof(invalidExternalHealth) /
             sizeof(invalidExternalHealth[0]); ++i)
        assert(!ToolGatewayRuntimeComposition::
            ValidateExternalAuthoritativeHealth(
                invalidExternalHealth[i], connectorCount));

    const std::string stateDirectory = TempDirectory("/tmp/hepta-gateway-state-XXXXXX");
    const std::string credentialDirectory = TempDirectory("/tmp/hepta-gateway-credential-XXXXXX");
    const std::string lockDirectory =
        TempDirectory("/tmp/hepta-gateway-lock-XXXXXX");
    assert(::chmod(lockDirectory.c_str(), 0711) == 0);
    const std::string executionSocket = TempSocket("/tmp/hepta-gateway-execution-XXXXXX");
    const std::string eventSocket = TempSocket("/tmp/hepta-gateway-events-XXXXXX");
    const std::string toolSocket = TempSocket("/tmp/hepta-gateway-tools-XXXXXX");
    const std::string supervisorSocket = TempSocket("/tmp/hepta-gateway-supervisor-XXXXXX");
    const int executionFd = ActivatedSocket(executionSocket);
    const int eventFd = ActivatedSocket(eventSocket);
    WriteCredential(credentialDirectory);

    ExecutionServiceRuntimeConfig serviceConfig;
    serviceConfig.mode = ExecutionServiceRuntimeMode::Simulator;
    serviceConfig.listenFd = executionFd;
    serviceConfig.eventListenFd = eventFd;
    serviceConfig.allowedGatewayUids.insert(static_cast<std::uint32_t>(::geteuid()));
    serviceConfig.gatewayContextBinding.agentId = "codex-agent";
    serviceConfig.gatewayContextBinding.account = "SIM";
    serviceConfig.gatewayContextBinding.venue = "SIMULATOR";
    serviceConfig.gatewayContextBinding.executionDomain = "SIM:codex-agent";
    serviceConfig.stateDirectory = stateDirectory;
    serviceConfig.journalPath = stateDirectory + "/oms-journal.jsonl";
    serviceConfig.fenceCredentialPath = credentialDirectory + "/hepta-execution-fence";
    serviceConfig.ioTimeoutMs = 3000;
    ExecutionServiceRuntimeComposition service(serviceConfig);
    std::string reason;
    assert(service.Start(reason));
    service.Venue().SetQuote("EUR.USD", 1.1000, 1.1002);

    ExecutionGatewayRuntimeConfig executionConfig;
    executionConfig.mode = ExecutionGatewayMode::Simulator;
    executionConfig.executionSocket = executionSocket;
    executionConfig.eventSocket = eventSocket;
    executionConfig.executionServiceUid = static_cast<std::uint32_t>(::geteuid());
    executionConfig.executionServiceUidConfigured = true;
    executionConfig.ioTimeoutMs = 2500;
    executionConfig.mutationToolsEnabled = true;

    AgentOsRuntimeConfig agentConfig;
    agentConfig.toolSocket = toolSocket;
    agentConfig.supervisorSocket = supervisorSocket;
    agentConfig.supervisorUid = static_cast<std::uint32_t>(::geteuid());
    agentConfig.agentUid = static_cast<std::uint32_t>(::geteuid());
    agentConfig.supervisorMaxTtlMs = 3600000;
    agentConfig.supervisorAuditJournalPath =
        stateDirectory + "/tool-decision-audit.hja2";
    agentConfig.supervisorLeaseStorePath =
        stateDirectory + "/session-leases.hsl2";
    agentConfig.supervisorLeaseCleanupLockPath =
        lockDirectory + "/session-lease-terminal-cleanup.lock";
    agentConfig.supervisorLeaseCleanupLockUid =
        static_cast<std::uint32_t>(::geteuid());
    agentConfig.supervisorLeaseCleanupLockGid =
        static_cast<std::uint32_t>(::getegid());
    agentConfig.supervisorLeaseKeyPath =
        credentialDirectory + "/session-lease-key";
    WriteLeaseKey(agentConfig.supervisorLeaseKeyPath);
    {
        const int lockFd = ::open(
            agentConfig.supervisorLeaseCleanupLockPath.c_str(),
            O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0644);
        assert(lockFd >= 0);
        assert(::fchmod(lockFd, 0644) == 0);
        assert(::fsync(lockFd) == 0);
        assert(::close(lockFd) == 0);
    }

    std::map<std::string, std::string> values;
    values["HEPTA_TOOL_AGENT_ID"] = "codex-agent";
    values["HEPTA_TOOL_ACCOUNT"] = "SIM";
    values["HEPTA_EXECUTION_DOMAIN_ID"] = "SIM:codex-agent";
    values["HEPTA_TOOL_SESSION_TEMPLATES"] = "watch,paper";
    values["HEPTA_TOOL_MAX_ORDER_QTY"] = "1000";
    values["HEPTA_TOOL_MAX_TRADE_CALLS_PER_MIN"] = "4";
    values["HEPTA_TOOL_CONTRACT_BINDINGS"] =
        "EUR.USD|EUR|CASH|IDEALPRO|USD;GBP.USD|GBP|CASH|IDEALPRO|USD";
    ToolGatewaySessionPolicy policy;
    assert(ToolGatewaySessionPolicy::FromValues(
        values, executionConfig, agentConfig, policy, reason));

    ToolGatewayRuntimeComposition gateway(executionConfig, agentConfig, policy);
    gateway.SetRootCustodianUidForTests(
        static_cast<std::uint32_t>(::geteuid()));
    assert(gateway.Start(reason));
    assert(gateway.IsRunning());

    SessionSupervisorRequest provision;
    provision.operation = SessionSupervisorOperation::Provision;
    provision.templateId = "paper";
    provision.token = std::string(32, 'P');
    provision.agentId = "codex-agent";
    provision.sessionId = "round23-session";
    provision.peerUid = static_cast<std::uint32_t>(::geteuid());
    provision.ttlMs = 120000;
    SessionSupervisorResult provisioned;
    assert(UnixSessionSupervisorClient::Call(
        supervisorSocket, provision, provisioned, reason, 3000));
    assert(provisioned.accepted && provisioned.leaseGeneration == 1);

    TradingToolCall health;
    health.name = "system.get_health";
    TypedToolResultEnvelope envelope;
    const std::string healthy = ToolCall(
        toolSocket, provision.token, "round23-health", health, envelope);
    assert(envelope.status == "ok");
    assert(healthy.find("\"remote_execution\":true") != std::string::npos);
    assert(healthy.find("\"remote_execution_configured\":true") != std::string::npos);
    assert(healthy.find("\"remote_execution_ready\":true") != std::string::npos);
    assert(healthy.find("\"tool_gateway_epoch\":\"htgw-v1-") !=
           std::string::npos);
    assert(healthy.find("\"execution_service_epoch\":\"\"") == std::string::npos);
    assert(healthy.find("\"read_model\":\"execution_authoritative_v1\"") !=
           std::string::npos);
    assert(healthy.find("\"authorized_connector_count\"") ==
           std::string::npos);
    assert(healthy.find("\"execution_authoritative_health\"") ==
           std::string::npos);

    TradingToolCall quote;
    quote.name = "market.get_quote";
    quote.instrument = "EUR.USD";
    const std::string quoted = ToolCall(
        toolSocket, provision.token, "round24-quote", quote, envelope);
    if (envelope.status != "ok") std::cerr << quoted << '\n';
    assert(envelope.status == "ok");
    assert(quoted.find("\"source\":\"SIMULATOR\"") != std::string::npos);
    assert(quoted.find("\"bid\":1.1") != std::string::npos);
    assert(quoted.find("\"subscription_state\":\"active\"") != std::string::npos);
    assert(quoted.find("\"stale\":false") != std::string::npos);

    TradingToolCall positions;
    positions.name = "portfolio.list_positions";
    const std::string positionSnapshot = ToolCall(
        toolSocket, provision.token, "round24-positions", positions, envelope);
    assert(envelope.status == "ok");
    assert(positionSnapshot.find("\"authoritative\":true") != std::string::npos);

    TradingToolCall place;
    place.name = "trade.place_order";
    place.instrument = "EUR.USD";
    place.ibOrder.action = "BUY";
    place.ibOrder.orderType = "LMT";
    place.ibOrder.totalQuantity = 100.0;
    place.ibOrder.lmtPrice = 1.0990;
    place.timeInForce = "DAY";
    place.referencePrice = 1.1001;
    place.expiresAtMs = OmsJournal::NowEpochMs() + 60000;
    const std::string unpreviewed = ToolCall(
        toolSocket, provision.token, "round32-unpreviewed", place, envelope);
    assert(envelope.status == "rejected");
    assert(envelope.reasonCode == "EXECUTION_PREVIEW_PERMIT_UNKNOWN_OR_CONSUMED");
    assert(unpreviewed.find("EXECUTION_PREVIEW_PERMIT_UNKNOWN_OR_CONSUMED") !=
           std::string::npos);
    TradingToolCall preview = place;
    preview.name = "risk.preview_order";
    const std::string previewed = ToolCall(
        toolSocket, provision.token, "round34-preview", preview, envelope);
    if (envelope.status != "ok") std::cerr << previewed << '\n';
    assert(envelope.status == "ok");
    place.previewPermit = PreviewField(previewed, "preview_permit");
    const std::string mutationCommandId =
        PreviewField(previewed, "command_id");
    const std::string placed = ToolCall(
        toolSocket, provision.token, mutationCommandId, place, envelope);
    if (envelope.status != "ok") std::cerr << placed << '\n';
    assert(envelope.status == "ok");
    assert(placed.find("\"order_id\":1000000") != std::string::npos);
    const std::string replayed = ToolCall(
        toolSocket, provision.token, mutationCommandId, place, envelope);
    assert(envelope.status == "duplicate");
    assert(replayed.find("\"order_id\":1000000") != std::string::npos);

    SessionSupervisorRequest recoveryQuery;
    recoveryQuery.operation = SessionSupervisorOperation::RecoveryQuery;
    recoveryQuery.token = provision.token;
    recoveryQuery.expectedGeneration = provisioned.leaseGeneration;
    recoveryQuery.targetCommandId = mutationCommandId;
    SessionSupervisorResult recoveryStatus;
    assert(UnixSessionSupervisorClient::Call(
        supervisorSocket, recoveryQuery, recoveryStatus, reason, 3000));
    assert(recoveryStatus.accepted);
    assert(recoveryStatus.authoritativeCommandStatus);
    assert(recoveryStatus.TargetCommandId() == mutationCommandId);
    assert(recoveryStatus.CommandStatus() == "accepted");
    assert(recoveryStatus.CommandReasonCode() == "NONE");
    assert(recoveryStatus.orderId == 1000000);
    assert(recoveryStatus.recoveryOnly);
    assert(!recoveryStatus.ownerFenced);
    assert(recoveryStatus.ReasonCode() ==
           "RECOVERY_QUERY_CANNOT_FULL_FENCE");
    assert(recoveryStatus.ExecutionServiceEpoch().find("hexec-v6-") == 0);
    assert(recoveryStatus.executionServiceFencingGeneration == 9);

    // Restart persistence is part of the safety contract: an HSL5 recovery
    // record may restore owned cancel/flatten/read, never entry authority.
    gateway.Stop();
    assert(gateway.Start(reason));
    TradingToolCall blockedPreview = preview;
    ToolCall(toolSocket, provision.token, "round-recovery-entry-blocked",
             blockedPreview, envelope);
    assert(envelope.status == "permission_denied");
    assert(envelope.reasonCode == "SESSION_RECOVERY_ONLY");
    TradingToolCall ownedCancel;
    ownedCancel.name = "trade.cancel_order";
    ownedCancel.orderId = 1000000;
    const std::string cancelled = ToolCall(
        toolSocket, provision.token, "round-recovery-owned-cancel",
        ownedCancel, envelope);
    assert(envelope.status == "ok");
    assert(cancelled.find("\"order_id\":1000000") != std::string::npos);
    service.Venue().Process();

    SessionSupervisorRequest cancelRecoveryQuery = recoveryQuery;
    cancelRecoveryQuery.targetCommandId = "round-recovery-owned-cancel";
    SessionSupervisorResult cancelRecoveryStatus;
    assert(UnixSessionSupervisorClient::Call(
        supervisorSocket, cancelRecoveryQuery, cancelRecoveryStatus,
        reason, 3000));
    assert(cancelRecoveryStatus.accepted);
    assert(cancelRecoveryStatus.ownerActiveOrderCount == 0);
    assert(cancelRecoveryStatus.ownerUncertainCommandCount == 0);
    assert(cancelRecoveryStatus.CommandStatus() == "accepted");

    TradingToolCall commandStatus;
    commandStatus.name = "execution.get_command_status";
    commandStatus.targetCommandId = mutationCommandId;
    const std::string queried = ToolCall(
        toolSocket, provision.token, "round32-command-status",
        commandStatus, envelope);
    assert(envelope.status == "ok");
    assert(queried.find("\"authoritative\":true") != std::string::npos);
    assert(queried.find("\"command_id\":\"" + mutationCommandId +
        "\"") != std::string::npos);
    assert(queried.find("\"command_status\":\"accepted\"") !=
        std::string::npos);
    assert(queried.find("\"order_id\":1000000") != std::string::npos);
    assert(queried.find("\"execution_service_epoch\":\"\"") ==
        std::string::npos);
    assert(queried.find(
        "\"execution_service_fencing_generation\":9") !=
        std::string::npos);

    TradingToolCall missingStatus = commandStatus;
    missingStatus.targetCommandId = "round32-missing-command";
    ToolCall(toolSocket, provision.token, "round32-status-missing",
             missingStatus, envelope);
    assert(envelope.status == "error");
    assert(envelope.reasonCode == "EXECUTION_COMMAND_NOT_FOUND");

    SessionSupervisorRequest otherProvision = provision;
    otherProvision.token = std::string(32, 'Q');
    otherProvision.sessionId = "round32-other-owner";
    SessionSupervisorResult otherProvisioned;
    assert(UnixSessionSupervisorClient::Call(
        supervisorSocket, otherProvision, otherProvisioned, reason, 3000));
    assert(otherProvisioned.accepted);
    ToolCall(toolSocket, otherProvision.token, "round32-status-other-owner",
             commandStatus, envelope);
    assert(envelope.status == "error");
    assert(envelope.reasonCode == "EXECUTION_COMMAND_NOT_FOUND");

    SessionSupervisorRequest revoke;
    revoke.operation = SessionSupervisorOperation::Revoke;
    revoke.token = provision.token;
    revoke.expectedGeneration = provisioned.leaseGeneration;
    SessionSupervisorResult revoked;
    assert(UnixSessionSupervisorClient::Call(
        supervisorSocket, revoke, revoked, reason, 3000));
    assert(revoked.accepted);
    assert(revoked.leaseGeneration == provisioned.leaseGeneration);
    ToolCall(toolSocket, provision.token, "round23-after-revoke", health, envelope);
    assert(envelope.status == "permission_denied");
    assert(envelope.reasonCode == "SESSION_NOT_FOUND");

    SessionSupervisorRequest watchProvision = provision;
    watchProvision.templateId = "watch";
    watchProvision.token = std::string(32, 'W');
    watchProvision.sessionId = "round34-health-session";
    SessionSupervisorResult watchProvisioned;
    assert(UnixSessionSupervisorClient::Call(
        supervisorSocket, watchProvision, watchProvisioned, reason, 3000));
    assert(watchProvisioned.accepted);

    SessionSupervisorRequest expiryProvision = watchProvision;
    expiryProvision.token = std::string(32, 'X');
    expiryProvision.sessionId = "round34-expiry-session";
    expiryProvision.ttlMs = 60000;
    SessionSupervisorResult expiryProvisioned;
    assert(UnixSessionSupervisorClient::Call(
        supervisorSocket, expiryProvision, expiryProvisioned, reason, 3000));
    assert(expiryProvisioned.accepted);
    ToolCall(toolSocket, expiryProvision.token,
        "round34-before-expiry", health, envelope);
    assert(envelope.status == "ok");
    std::size_t reaped = 0;
    assert(gateway.ReapExpired(
        static_cast<std::uint64_t>(OmsJournal::NowEpochMs()) + 70000,
        reaped, reason));
    assert(reaped == 1);
    ToolCall(toolSocket, expiryProvision.token,
        "round34-after-expiry", health, envelope);
    assert(envelope.status == "permission_denied");
    assert(envelope.reasonCode == "SESSION_NOT_FOUND");

    service.Stop();
    const std::string degraded = ToolCall(
        toolSocket, watchProvision.token, "round34-health-degraded",
        health, envelope);
    assert(envelope.status == "ok");
    assert(degraded.find("\"gateway_ready\":true") != std::string::npos);
    assert(degraded.find("\"tool_gateway_epoch\":\"htgw-v1-") !=
           std::string::npos);
    assert(degraded.find("\"remote_execution_configured\":true") !=
           std::string::npos);
    assert(degraded.find("\"remote_execution_ready\":false") !=
           std::string::npos);
    assert(degraded.find("\"execution_service_epoch\":\"\"") !=
           std::string::npos);
    assert(degraded.find("\"remote_execution_reason\":\"\"") ==
           std::string::npos);

    gateway.Stop();
    std::uint64_t auditRecords = 0;
    assert(SessionSupervisorAuditJournal::Verify(
        agentConfig.supervisorAuditJournalPath, auditRecords, reason));
    assert(auditRecords > 0);
    ::close(executionFd);
    ::close(eventFd);
    ::unlink(executionSocket.c_str());
    ::unlink(eventSocket.c_str());
    ::unlink(toolSocket.c_str());
    ::unlink(supervisorSocket.c_str());
    ::unlink((stateDirectory + "/oms-journal.jsonl").c_str());
    ::unlink((stateDirectory + "/execution-runtime.lock").c_str());
    ::unlink(agentConfig.supervisorAuditJournalPath.c_str());
    ::unlink(agentConfig.supervisorLeaseStorePath.c_str());
    ::unlink(agentConfig.supervisorLeaseCleanupLockPath.c_str());
    ::unlink(agentConfig.supervisorLeaseKeyPath.c_str());
    ::unlink((credentialDirectory + "/hepta-execution-fence").c_str());
    ::rmdir(stateDirectory.c_str());
    ::rmdir(credentialDirectory.c_str());
    ::rmdir(lockDirectory.c_str());

    std::cout << "tool_gateway_runtime_composition_tests: "
              << "remote_only=verified sessionctl=verified authoritative_read=verified"
              << " place=verified owner_fence=verified liveness=verified\n";
    return 0;
}
