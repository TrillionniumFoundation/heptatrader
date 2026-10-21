#include "../HeptaTrade/tool_host/execution_gateway_runtime_composition.h"
#include "../HeptaTrade/execution/execution_event_feed_server.h"
#include "../HeptaTrade/execution/execution_service_protocol.h"
#include "../HeptaTrade/execution/unix_execution_service_server.h"

#include <cassert>
#include <algorithm>
#include <arpa/inet.h>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstring>
#include <iostream>
#include <memory>
#include <mutex>
#include <set>
#include <sys/socket.h>
#include <sys/un.h>
#include <thread>
#include <unistd.h>

namespace
{
int ActivatedSocket(const std::string& path)
{
    ::unlink(path.c_str());
    const int fd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    assert(fd >= 0);
    struct sockaddr_un address;
    std::memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    std::memcpy(address.sun_path, path.c_str(), path.size() + 1);
    assert(::bind(fd, reinterpret_cast<struct sockaddr*>(&address), sizeof(address)) == 0);
    assert(::listen(fd, 16) == 0);
    return fd;
}

void WriteFrameBlocking(int fd, const std::string& body)
{
    const std::uint32_t length = htonl(static_cast<std::uint32_t>(body.size()));
    const char* header = reinterpret_cast<const char*>(&length);
    std::size_t offset = 0;
    while (offset < sizeof(length))
    {
        const ssize_t count = ::write(fd, header + offset, sizeof(length) - offset);
        assert(count > 0);
        offset += static_cast<std::size_t>(count);
    }
    offset = 0;
    while (offset < body.size())
    {
        const ssize_t count = ::write(fd, body.data() + offset, body.size() - offset);
        assert(count > 0);
        offset += static_cast<std::size_t>(count);
    }
}

std::string ReadFrameBlocking(int fd)
{
    std::uint32_t length = 0;
    char* header = reinterpret_cast<char*>(&length);
    std::size_t offset = 0;
    while (offset < sizeof(length))
    {
        const ssize_t count = ::read(fd, header + offset, sizeof(length) - offset);
        assert(count > 0);
        offset += static_cast<std::size_t>(count);
    }
    std::string body(ntohl(length), '\0');
    offset = 0;
    while (offset < body.size())
    {
        const ssize_t count = ::read(fd, &body[0] + offset, body.size() - offset);
        assert(count > 0);
        offset += static_cast<std::size_t>(count);
    }
    return body;
}

class FakeAuthority : public ExecutionAuthority,
                      public ExecutionControlAuthority,
                      public ExecutionReadAuthority
{
public:
    int places = 0;
    int cancels = 0;
    int previews = 0;
    int fences = 0;
    int reconciles = 0;
    int sessionAudits = 0;

    ExecutionCommandResult PlaceOrder(const PlaceOrderCommand& command) override
    {
        ++places;
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Accepted;
        result.commandId = command.context.toolCallId;
        result.orderId = 700 + places;
        return result;
    }
    ExecutionCommandResult CancelOrder(const CancelOrderCommand& command) override
    {
        ++cancels;
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Accepted;
        result.commandId = command.context.toolCallId;
        result.orderId = command.orderId;
        return result;
    }
    ExecutionControlStatusResult QueryCommandStatus(const ExecutionControlCommand& command) override
    {
        return Control(command, 0);
    }
    ExecutionControlStatusResult FenceSessionOwner(const ExecutionControlCommand& command) override
    {
        ++fences;
        return Control(command, 2);
    }
    ExecutionControlStatusResult ReleaseSessionOwnerFence(const ExecutionControlCommand& command) override
    {
        return Control(command, 0);
    }
    ExecutionControlStatusResult ReconcileAuthoritativeState(const ExecutionControlCommand& command) override
    {
        ++reconciles;
        return Control(command, 1);
    }
    ExecutionOwnerAuditResult AuditSessionLease(const ExecutionControlCommand& command) override
    {
        auto result=AuditSessionOwner(command);
        result.reasonCode="SESSION_LEASE_CONTINUATION_CONFIRMED";
        result.brokerPositionQuantity="-25000";
        result.brokerGrossAbsolutePosition="25000";
        result.brokerPositionGeneration=9;
        return result;
    }
    ExecutionOwnerAuditResult AuditSessionOwner(const ExecutionControlCommand& command) override
    {
        ++sessionAudits; assert(command.recoveryIngressFence == 0);
        ExecutionOwnerAuditResult result;
        result.status = ExecutionCommandStatus::Accepted;
        result.commandId = command.context.toolCallId;
        result.ownerAuditAuthoritative = true; result.ownerAuditComplete = true;
        result.ownerAccount = command.context.account;
        result.ownerExecutionDomain = command.context.executionDomain;
        result.brokerConnectionEpoch = 7; result.brokerActiveGeneration = 8;
        result.brokerTerminalGeneration = 9;
        result.reasonCode = "RECOVERY_OWNER_ZERO_CONFIRMED";
        return result;
    }
    ExecutionCommandResult PreviewOrder(const PlaceOrderCommand& command) override
    {
        ++previews;
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Accepted;
        result.commandId = command.context.toolCallId;
        result.detail = "{\"authoritative\":true}";
        return result;
    }
    ExecutionCommandResult ReadAuthoritativeState(
        const ExecutionReadCommand& command) override
    {
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Accepted;
        result.commandId = command.context.toolCallId;
        result.detail = "{\"authoritative\":true}";
        return result;
    }

private:
    ExecutionControlResult Control(const ExecutionControlCommand& command,
                                   std::uint64_t affected)
    {
        ExecutionControlResult result;
        result.status = ExecutionCommandStatus::Accepted;
        result.commandId = command.context.toolCallId;
        result.affectedCount = affected;
        return result;
    }
};

ExecutionServiceIdentity Identity(const std::string& epoch,
                                  std::uint64_t generation)
{
    ExecutionServiceIdentity identity;
    identity.serviceEpoch = epoch;
    identity.serviceFencingGeneration = generation;
    return identity;
}

class CountingEventSource : public ExecutionEventFeedSource
{
public:
    explicit CountingEventSource(ExecutionEventHub& hub)
        : m_hub(hub), reads(0)
    {
    }

    ExecutionEventReadResult ReadNext(
        const std::string& executionDomain,
        const std::string& agentId,
        const std::string& sessionId,
        const std::string& expectedEpoch,
        std::uint64_t afterSequence,
        int timeoutMs) override
    {
        reads.fetch_add(1);
        return m_hub.ReadNext(executionDomain, agentId, sessionId,
            expectedEpoch, afterSequence, timeoutMs);
    }

    const std::string& StreamEpoch() const override
    {
        return m_hub.StreamEpoch();
    }

    ExecutionEventHub& m_hub;
    std::atomic<int> reads;
};

AgentExecutionContext Owner(const std::string& commandId)
{
    AgentExecutionContext owner;
    owner.agentId = "gateway-agent";
    owner.sessionId = "gateway-session";
    owner.toolCallId = commandId;
    owner.account = "SIM";
    owner.venue = "SIMULATOR";
    owner.executionDomain = "SIM:EURUSD";
    return owner;
}

AgentExecutionContext PaperOwner(
    const std::string& commandId,
    const std::string& executionDomain = "PAPER")
{
    AgentExecutionContext owner = Owner(commandId);
    owner.account = "DU123456";
    owner.venue = "IB";
    owner.executionDomain = executionDomain;
    return owner;
}

IbPlaceOrderCommand Place(const AgentExecutionContext& owner)
{
    IbPlaceOrderCommand command;
    command.context = owner;
    command.instrument = "EUR.USD";
    command.contract.symbol = "EUR";
    command.expiresAtMs = static_cast<long long>(
        std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count()) + 60000;
    return command;
}

std::string PreviewField(const ExecutionCommandResult& preview,
                         const std::string& name)
{
    const std::string marker = "\"" + name + "\":\"";
    const std::size_t begin = preview.detail.find(marker);
    assert(begin != std::string::npos);
    const std::size_t value = begin + marker.size();
    const std::size_t end = preview.detail.find('"', value);
    assert(end != std::string::npos);
    return preview.detail.substr(value, end - value);
}

template <typename PreviewAuthority>
IbPlaceOrderCommand Previewed(
    PreviewAuthority& authority,
    const IbPlaceOrderCommand& input)
{
    IbPlaceOrderCommand command = input;
    command.previewPermit.clear();
    const ExecutionCommandResult preview = authority.PreviewOrder(command);
    assert(preview.status == ExecutionCommandStatus::Accepted);
    command.previewPermit = PreviewField(preview, "preview_permit");
    command.context.toolCallId = PreviewField(preview, "command_id");
    return command;
}

ExecutionGatewayRuntimeConfig RemoteConfig(const std::string& executionSocket,
                                           const std::string& eventSocket)
{
    ExecutionGatewayRuntimeConfig config;
    config.mode = ExecutionGatewayMode::Simulator;
    config.executionSocket = executionSocket;
    config.eventSocket = eventSocket;
    config.executionServiceUid = static_cast<std::uint32_t>(::geteuid());
    config.executionServiceUidConfigured = true;
    // Match the production default. A 500 ms test-only deadline produced
    // false transport rejections when the two certification soaks shared a
    // heavily loaded host; identity mismatches must still be exact, not
    // broadened to accept transport failures.
    config.ioTimeoutMs = 1000;
    return config;
}

void TestIdentityPreflightBudget(int ioTimeoutMs, bool silentMutation,
                                 int callerBudgetMs = 0)
{
    const std::string prefix = "/tmp/hepta-identity-budget-" +
        std::to_string(::getpid()) + "-" + std::to_string(ioTimeoutMs) +
        (silentMutation ? "-mutation" : "-events") +
        std::to_string(callerBudgetMs);
    const std::string executionSocket = prefix + ".sock";
    const std::string eventSocket = prefix + ".events";
    const auto identity = Identity("identity-budget", 1);
    const std::set<std::uint32_t> uid{static_cast<std::uint32_t>(::geteuid())};
    const auto gate = std::make_shared<ExecutionServiceLifecycleGate>();
    FakeAuthority local, remote;
    UnixExecutionServiceServer executionServer(remote, &remote);
    std::string reason;
    int silentExecutionFd = -1;
    if (silentMutation) silentExecutionFd = ActivatedSocket(executionSocket);
    else assert(executionServer.StartFromFd(ActivatedSocket(executionSocket),
        uid, identity, gate, reason));
    // A private socket backlog models an unavailable/starting peer without
    // sending an order or making the test depend on a live service.
    const int silentEventFd = ActivatedSocket(eventSocket);
    gate->ready.store(true);
    ExecutionEventHub localHub(8, "identity-budget-local");
    auto config = RemoteConfig(executionSocket, eventSocket);
    config.ioTimeoutMs = ioTimeoutMs;
    config.responseTimeoutMs = 4000;
    ExecutionGatewayRuntimeComposition gateway(local, localHub, config);
    assert(gateway.Start(reason));
    const auto start = std::chrono::steady_clock::now();
    ExecutionCommandStatus status;
    if (callerBudgetMs)
    {
        ExecutionControlCommand command;
        command.context = Owner("budgeted-status");
        command.targetCommandId = "unavailable-command";
        command.localDeadline = start + std::chrono::milliseconds(callerBudgetMs);
        const auto result = gateway.QueryCommandStatus(command);
        status = result.status;
        reason = result.reasonCode;
    }
    else
    {
        IbCancelOrderCommand command;
        command.context = Owner("budgeted-unsent-cancel");
        command.orderId = 71;
        const auto result = gateway.CancelOrder(command);
        status = result.status;
        reason = result.reasonCode;
    }
    const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - start).count();
    const int budgetMs = callerBudgetMs ? callerBudgetMs :
        std::min(ioTimeoutMs, 1000);
    assert(status == ExecutionCommandStatus::Rejected);
    assert(reason == (silentMutation ? "response read failed" :
        "EXECUTION_EVENT_RESPONSE_READ_FAILED"));
    assert(elapsed >= budgetMs - 50 && elapsed < budgetMs + 400);
    assert(remote.cancels == 0 && local.cancels == 0);
    executionServer.Stop();
    if (silentExecutionFd >= 0) ::close(silentExecutionFd);
    ::close(silentEventFd);
    ::unlink(executionSocket.c_str());
    ::unlink(eventSocket.c_str());
}

void TestIdentityRefreshSharesAbsoluteBudget()
{
    const std::string prefix = "/tmp/hepta-identity-refresh-budget-" +
        std::to_string(::getpid());
    const std::string executionSocket = prefix + ".sock";
    const std::string eventSocket = prefix + ".events";
    const int executionFd = ActivatedSocket(executionSocket);
    const int eventFd = ActivatedSocket(eventSocket);
    const auto mutationIdentity = Identity("identity-before-refresh", 1);
    const auto eventIdentity = Identity("identity-after-refresh", 2);
    std::atomic<int> mutationRequests(0), eventRequests(0);
    std::thread mutationPeer([&]() {
        for (int attempt = 0; attempt < 2; ++attempt)
        {
            const int fd = ::accept(executionFd, nullptr, nullptr);
            assert(fd >= 0);
            ExecutionServiceRequest request;
            std::string reason, body;
            assert(ExecutionServiceProtocol::DecodeRequest(
                ReadFrameBlocking(fd), request, reason));
            assert(request.operation == ExecutionServiceOperation::GetServiceIdentity);
            ++mutationRequests;
            ExecutionCommandResult result;
            result.commandId = "__service_identity__";
            result.status = ExecutionCommandStatus::Accepted;
            result.serviceEpoch = mutationIdentity.serviceEpoch;
            result.serviceFencingGeneration = mutationIdentity.serviceFencingGeneration;
            assert(ExecutionServiceProtocol::EncodeResponse(result, body, reason));
            WriteFrameBlocking(fd, body);
            ::close(fd);
        }
    });
    std::thread eventPeer([&]() {
        for (int attempt = 0; attempt < 2; ++attempt)
        {
            const int fd = ::accept(eventFd, nullptr, nullptr);
            assert(fd >= 0);
            ExecutionEventFeedRequest request;
            std::string reason, body;
            assert(ExecutionEventFeedProtocol::DecodeRequest(
                ReadFrameBlocking(fd), request, reason));
            assert(request.operation == ExecutionEventFeedOperation::GetServiceIdentity);
            ++eventRequests;
            if (attempt == 0)
            {
                std::this_thread::sleep_for(std::chrono::milliseconds(700));
                ExecutionEventReadResult result;
                result.status = ExecutionEventReadStatus::ServiceIdentity;
                result.reasonCode = "EXECUTION_EVENT_SERVICE_IDENTITY";
                result.serviceIdentity = eventIdentity;
                result.streamEpoch = eventIdentity.serviceEpoch;
                assert(ExecutionEventFeedProtocol::EncodeResponse(result, body, reason));
                WriteFrameBlocking(fd, body);
            }
            else
            {
                char ignored;
                assert(::read(fd, &ignored, 1) == 0); // caller closes at its deadline
            }
            ::close(fd);
        }
    });
    FakeAuthority local;
    ExecutionEventHub localHub(8, "identity-refresh-budget-local");
    auto config = RemoteConfig(executionSocket, eventSocket);
    config.ioTimeoutMs = 2500;
    ExecutionGatewayRuntimeComposition gateway(local, localHub, config);
    std::string reason;
    assert(gateway.Start(reason));
    ExecutionServiceIdentity observed;
    const auto start = std::chrono::steady_clock::now();
    assert(!gateway.ProbeRemoteService(observed, reason));
    const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - start).count();
    mutationPeer.join();
    eventPeer.join();
    assert(reason == "EXECUTION_EVENT_RESPONSE_READ_FAILED");
    assert(elapsed >= 900 && elapsed < 1400);
    assert(mutationRequests == 2 && eventRequests == 2);
    assert(local.places == 0 && local.cancels == 0);
    ::close(executionFd);
    ::close(eventFd);
    ::unlink(executionSocket.c_str());
    ::unlink(eventSocket.c_str());
}

void TestIdentityInvalidationHonorsCacheLockBudget()
{
    const std::string path = "/tmp/hepta-identity-cache-budget-" +
        std::to_string(::getpid());
    const int listener = ActivatedSocket(path);
    UnixExecutionServiceClient client(path, 2500, 32768,
        {static_cast<std::uint32_t>(::geteuid())}, 4000);
    std::thread pendingIdentity([&]() {
        ExecutionServiceIdentity ignored;
        std::string reason;
        assert(!client.GetServiceIdentity(ignored, reason,
            std::chrono::steady_clock::now() + std::chrono::milliseconds(500)));
    });
    const int fd = ::accept(listener, nullptr, nullptr);
    assert(fd >= 0);
    ReadFrameBlocking(fd); // the caller now holds the cache lock during socket I/O
    const auto start = std::chrono::steady_clock::now();
    assert(!client.InvalidateServiceIdentity(Identity("cached", 1),
        start + std::chrono::milliseconds(100)));
    const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - start).count();
    assert(elapsed < 350);
    pendingIdentity.join();
    ::close(fd);
    ::close(listener);
    ::unlink(path.c_str());
}

void TestIdentityBudgetDoesNotShortenAuthorityOrEventWait()
{
    class SlowAuthority : public FakeAuthority
    {
    public:
        ExecutionCommandResult CancelOrder(const CancelOrderCommand& command) override
        {
            std::this_thread::sleep_for(std::chrono::milliseconds(1300));
            return FakeAuthority::CancelOrder(command);
        }
    } remote;
    FakeAuthority local;
    const std::string prefix = "/tmp/hepta-identity-operation-budget-" +
        std::to_string(::getpid());
    const std::string executionSocket = prefix + ".sock";
    const std::string eventSocket = prefix + ".events";
    const auto identity = Identity("identity-operation-budget", 1);
    const auto gate = std::make_shared<ExecutionServiceLifecycleGate>();
    const std::set<std::uint32_t> uid{static_cast<std::uint32_t>(::geteuid())};
    ExecutionEventHub upstream(8, identity.serviceEpoch), localHub(8, "operation-budget-local");
    UnixExecutionServiceServer executionServer(remote, &remote);
    UnixExecutionEventFeedServer eventServer(upstream, identity, gate);
    std::string reason;
    assert(executionServer.StartFromFd(ActivatedSocket(executionSocket), uid,
        identity, gate, reason));
    assert(eventServer.StartFromFd(ActivatedSocket(eventSocket), uid, reason));
    gate->ready.store(true);
    auto config = RemoteConfig(executionSocket, eventSocket);
    config.ioTimeoutMs = 2500;
    config.responseTimeoutMs = 4000;
    ExecutionGatewayRuntimeComposition gateway(local, localHub, config);
    assert(gateway.Start(reason));
    IbCancelOrderCommand command;
    command.context = Owner("slow-authority-budget");
    command.orderId = 72;
    const auto start = std::chrono::steady_clock::now();
    assert(gateway.CancelOrder(command).status == ExecutionCommandStatus::Accepted);
    assert(std::chrono::steady_clock::now() - start >= std::chrono::milliseconds(1200));
    assert(remote.cancels == 1 && local.cancels == 0);
    ExecutionEvent ignored;
    const auto waitStart = std::chrono::steady_clock::now();
    assert(!gateway.WaitNext(command.context, 0, 1300, ignored, reason));
    assert(reason == "EVENT_WAIT_TIMEOUT");
    assert(std::chrono::steady_clock::now() - waitStart >= std::chrono::milliseconds(1200));
    eventServer.Stop();
    executionServer.Stop();
    ::unlink(executionSocket.c_str());
    ::unlink(eventSocket.c_str());
}

void TestConfigIsHardOffAndStrict()
{
    std::string reason;
    ExecutionGatewayRuntimeConfig disabled;
    assert(disabled.Validate(reason));
    assert(!disabled.Enabled());

    std::map<std::string, std::string> mutationWithoutRemote;
    mutationWithoutRemote["HEPTA_TOOL_ALLOW_TRADE"] = "1";
    assert(!ExecutionGatewayRuntimeConfig::FromValues(
        mutationWithoutRemote).Validate(reason));
    assert(reason ==
        "EXECUTION_GATEWAY_REMOTE_REQUIRED_FOR_MUTATION_TOOLS");

    mutationWithoutRemote["HEPTA_TOOL_ALLOW_TRADE"] = "sometimes";
    assert(!ExecutionGatewayRuntimeConfig::FromValues(
        mutationWithoutRemote).Validate(reason));
    assert(reason == "EXECUTION_GATEWAY_FLAG_INVALID");

    std::map<std::string, std::string> partial;
    partial["HEPTA_EXECUTION_SOCKET"] = "/tmp/execution.sock";
    assert(!ExecutionGatewayRuntimeConfig::FromValues(partial).Validate(reason));
    assert(reason == "EXECUTION_GATEWAY_DISABLED_WITH_REMOTE_CONFIGURATION");

    std::map<std::string, std::string> paperValues;
    paperValues["HEPTA_EXECUTION_REMOTE_MODE"] = "PAPER";
    paperValues["HEPTA_EXECUTION_SOCKET"] = "/tmp/ib-execution.sock";
    paperValues["HEPTA_EXECUTION_EVENT_SOCKET"] = "/tmp/ib-events.sock";
    paperValues["HEPTA_EXECUTION_SERVICE_UID"] = std::to_string(::geteuid());
    paperValues["HEPTA_TOOL_ALLOW_TRADE"] = "1";
    const ExecutionGatewayRuntimeConfig paper =
        ExecutionGatewayRuntimeConfig::FromValues(paperValues);
    assert(paper.Validate(reason));
    assert(paper.Enabled());
    assert(!paper.externalP1CanaryLimitDay);
    assert(std::string(paper.ModeName()) == "PAPER");

    std::map<std::string, std::string> externalPaper = paperValues;
    externalPaper["HEPTA_EXECUTION_EXTERNAL_P1_CANARY_LMT_DAY"] = "1";
    const ExecutionGatewayRuntimeConfig external =
        ExecutionGatewayRuntimeConfig::FromValues(externalPaper);
    assert(external.Validate(reason));
    assert(external.externalP1CanaryLimitDay);

    const char* invalidExternalValues[] = {"", "0", "true", "2"};
    for (std::size_t i = 0;
         i < sizeof(invalidExternalValues) /
             sizeof(invalidExternalValues[0]); ++i)
    {
        std::map<std::string, std::string> invalidExternal = paperValues;
        invalidExternal["HEPTA_EXECUTION_EXTERNAL_P1_CANARY_LMT_DAY"] =
            invalidExternalValues[i];
        assert(!ExecutionGatewayRuntimeConfig::FromValues(
            invalidExternal).Validate(reason));
        assert(reason == "EXECUTION_GATEWAY_FLAG_INVALID");
    }

    std::map<std::string, std::string> simulatorExternal = externalPaper;
    simulatorExternal["HEPTA_EXECUTION_REMOTE_MODE"] = "SIMULATOR";
    assert(!ExecutionGatewayRuntimeConfig::FromValues(
        simulatorExternal).Validate(reason));
    assert(reason ==
        "EXECUTION_GATEWAY_EXTERNAL_P1_CANARY_REQUIRES_PAPER");

    std::map<std::string, std::string> invalidMode = paperValues;
    invalidMode["HEPTA_EXECUTION_REMOTE_MODE"] = "LIVE";
    assert(!ExecutionGatewayRuntimeConfig::FromValues(invalidMode).Validate(reason));
    assert(reason == "EXECUTION_GATEWAY_MODE_UNSUPPORTED");

    std::map<std::string, std::string> invalidLimit;
    invalidLimit["HEPTA_EXECUTION_REMOTE_MODE"] = "SIMULATOR";
    invalidLimit["HEPTA_EXECUTION_SOCKET"] = "/tmp/execution.sock";
    invalidLimit["HEPTA_EXECUTION_EVENT_SOCKET"] = "/tmp/events.sock";
    invalidLimit["HEPTA_EXECUTION_SERVICE_UID"] = std::to_string(::geteuid());
    invalidLimit["HEPTA_EXECUTION_IO_TIMEOUT_MS"] = "0";
    assert(!ExecutionGatewayRuntimeConfig::FromValues(invalidLimit).Validate(reason));
    assert(reason == "EXECUTION_GATEWAY_LIMIT_INVALID");
    invalidLimit["HEPTA_EXECUTION_IO_TIMEOUT_MS"] = "2501";
    assert(!ExecutionGatewayRuntimeConfig::FromValues(invalidLimit).Validate(reason));
    assert(reason == "EXECUTION_GATEWAY_LIMIT_INVALID");
    invalidLimit["HEPTA_EXECUTION_IO_TIMEOUT_MS"] = "1000";
    invalidLimit["HEPTA_EXECUTION_RESPONSE_TIMEOUT_MS"] = "999";
    assert(!ExecutionGatewayRuntimeConfig::FromValues(invalidLimit).Validate(reason));
    assert(reason == "EXECUTION_GATEWAY_LIMIT_INVALID");
    invalidLimit["HEPTA_EXECUTION_RESPONSE_TIMEOUT_MS"] = "30001";
    assert(!ExecutionGatewayRuntimeConfig::FromValues(invalidLimit).Validate(reason));
    assert(reason == "EXECUTION_GATEWAY_LIMIT_INVALID");
    invalidLimit["HEPTA_EXECUTION_IO_TIMEOUT_MS"] = "2000";
    invalidLimit["HEPTA_EXECUTION_RESPONSE_TIMEOUT_MS"] = "1500";
    assert(!ExecutionGatewayRuntimeConfig::FromValues(invalidLimit).Validate(reason));
    assert(reason == "EXECUTION_GATEWAY_LIMIT_INVALID");

    ExecutionGatewayRuntimeConfig remote = RemoteConfig(
        "/tmp/execution.sock", "/tmp/events.sock");
    assert(remote.Validate(reason));
    remote.eventSocket = remote.executionSocket;
    assert(!remote.Validate(reason));
    assert(reason == "EXECUTION_GATEWAY_SOCKETS_MUST_BE_DISTINCT");
}

void TestDisabledDelegatesLocal()
{
    FakeAuthority local;
    ExecutionEventHub localHub(8, "local");
    ExecutionGatewayRuntimeComposition gateway(local, localHub,
        ExecutionGatewayRuntimeConfig());
    std::string reason;
    assert(gateway.Start(reason));
    IbPlaceOrderCommand command = Place(Owner("local-place"));
    assert(gateway.Authority().PlaceIbOrder(command).orderId == 701);
    assert(local.places == 1);
    ExecutionControlCommand control;
    control.context = Owner("local-fence");
    assert(gateway.FenceSessionOwner(control).reasonCode ==
        "EXECUTION_GATEWAY_REMOTE_DISABLED");
}

void TestMutationToolsNeverFallBackToLocalAuthority()
{
    FakeAuthority local;
    ExecutionEventHub localHub(8, "local");
    ExecutionGatewayRuntimeConfig config;
    config.mutationToolsEnabled = true;
    ExecutionGatewayRuntimeComposition gateway(local, localHub, config);
    IbPlaceOrderCommand place = Place(Owner("required-remote-place"));
    const ExecutionCommandResult placeResult = gateway.Authority().PlaceIbOrder(place);
    assert(placeResult.status == ExecutionCommandStatus::Rejected);
    assert(placeResult.reasonCode ==
        "EXECUTION_GATEWAY_REMOTE_REQUIRED_FOR_MUTATION_TOOLS");
    IbCancelOrderCommand cancel;
    cancel.context = Owner("required-remote-cancel");
    cancel.orderId = 42;
    const ExecutionCommandResult cancelResult = gateway.Authority().CancelIbOrder(cancel);
    assert(cancelResult.status == ExecutionCommandStatus::Rejected);
    assert(cancelResult.reasonCode ==
        "EXECUTION_GATEWAY_REMOTE_REQUIRED_FOR_MUTATION_TOOLS");
    assert(local.places == 0);
}

void TestActivatedBacklogRejectsOldServiceEpoch()
{
    const std::string socketPath = "/tmp/hepta-execution-epoch-" +
        std::to_string(::getpid()) + ".sock";
    const int managerFd = ActivatedSocket(socketPath);
    const std::set<std::uint32_t> uid{
        static_cast<std::uint32_t>(::geteuid())};
    FakeAuthority authority;
    std::string reason;
    UnixExecutionServiceServer first(authority, &authority);
    assert(first.StartFromFd(::dup(managerFd), uid, reason));
    const ExecutionServiceIdentity oldIdentity = first.ServiceIdentity();
    assert(!oldIdentity.serviceEpoch.empty());
    assert(oldIdentity.serviceFencingGeneration != 0);
    first.Stop();

    ExecutionServiceRequest staleRequest;
    staleRequest.operation = ExecutionServiceOperation::PlaceIbOrder;
    staleRequest.expectedServiceEpoch = oldIdentity.serviceEpoch;
    staleRequest.expectedServiceFencingGeneration =
        oldIdentity.serviceFencingGeneration;
    staleRequest.place = Place(Owner("queued-old-epoch"));
    std::string requestBody;
    assert(ExecutionServiceProtocol::EncodeRequest(staleRequest, requestBody, reason));
    const int queuedFd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    assert(queuedFd >= 0);
    struct sockaddr_un address;
    std::memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    std::memcpy(address.sun_path, socketPath.c_str(), socketPath.size() + 1);
    assert(::connect(queuedFd, reinterpret_cast<struct sockaddr*>(&address),
        sizeof(address)) == 0);
    WriteFrameBlocking(queuedFd, requestBody);

    UnixExecutionServiceServer second(authority, &authority);
    assert(second.StartFromFd(::dup(managerFd), uid, reason));
    assert(second.ServiceEpoch() != oldIdentity.serviceEpoch);
    ExecutionCommandResult staleResult;
    assert(ExecutionServiceProtocol::DecodeResponse(
        ReadFrameBlocking(queuedFd), staleResult, reason));
    assert(staleResult.status == ExecutionCommandStatus::Rejected);
    assert(staleResult.reasonCode == "EXECUTION_SERVICE_EPOCH_MISMATCH");
    assert(authority.places == 0);
    ::close(queuedFd);

    UnixExecutionServiceClient current(socketPath, 500, 32768, uid);
    IbPlaceOrderCommand currentCommand = Place(Owner("current-epoch"));
    const ExecutionCommandResult unpreviewed =
        current.PlaceIbOrder(currentCommand);
    assert(unpreviewed.status == ExecutionCommandStatus::Rejected);
    assert(unpreviewed.reasonCode ==
        "EXECUTION_PREVIEW_PERMIT_UNKNOWN_OR_CONSUMED");
    assert(authority.places == 0);
    currentCommand = Previewed(current, currentCommand);
    assert(current.PlaceIbOrder(currentCommand).status == ExecutionCommandStatus::Accepted);
    assert(authority.previews == 1);
    assert(authority.places == 1);
    second.Stop();
    ::close(managerFd);
    ::unlink(socketPath.c_str());
}

void TestRemoteAuthorityControlsAndRelay()
{
    const std::string prefix = "/tmp/hepta-gateway-" + std::to_string(::getpid());
    const std::string executionSocket = prefix + ".sock";
    const std::string eventSocket = prefix + ".events.sock";
    const ExecutionServiceIdentity identity = Identity("gateway-daemon-1", 31);
    const std::shared_ptr<ExecutionServiceLifecycleGate> gate(
        new ExecutionServiceLifecycleGate());
    FakeAuthority local;
    FakeAuthority remote;
    ExecutionEventHub upstream(2, identity.serviceEpoch);
    ExecutionEventHub localHub(16, "gateway-local");
    std::unique_ptr<UnixExecutionServiceServer> executionServer(
        new UnixExecutionServiceServer(remote, &remote));
    std::unique_ptr<UnixExecutionEventFeedServer> eventServer(
        new UnixExecutionEventFeedServer(upstream, identity, gate));
    std::string reason;
    const std::set<std::uint32_t> uid{static_cast<std::uint32_t>(::geteuid())};
    assert(executionServer->StartFromFd(
        ActivatedSocket(executionSocket), uid, identity, gate, reason));
    assert(eventServer->StartFromFd(ActivatedSocket(eventSocket), uid, reason));
    gate->ready.store(true);

    ExecutionGatewayRuntimeComposition gateway(local, localHub,
        RemoteConfig(executionSocket, eventSocket));
    assert(gateway.Start(reason));
    assert(gateway.Enabled());
    ExecutionServiceIdentity probedIdentity;
    assert(gateway.ProbeRemoteService(probedIdentity, reason));
    assert(probedIdentity.serviceEpoch == identity.serviceEpoch);
    assert(probedIdentity.serviceFencingGeneration ==
        identity.serviceFencingGeneration);
    IbPlaceOrderCommand command = Place(Owner("remote-place"));
    command = Previewed(gateway, command);
    const ExecutionCommandResult placed = gateway.Authority().PlaceIbOrder(command);
    assert(placed.status == ExecutionCommandStatus::Accepted && placed.orderId == 701);
    assert(local.places == 0 && remote.places == 1);
    ExecutionControlCommand fence;
    fence.context = Owner("remote-fence");
    assert(gateway.FenceSessionOwner(fence).affectedCount == 2);
    assert(remote.fences == 1);
    // Exercise the new operation through both real service identity sockets and
    // the real HEX request/result codecs, not only an in-process mock call.
    const auto audit = gateway.AuditSessionOwner(fence);
    assert(audit.status == ExecutionCommandStatus::Accepted);
    assert(audit.ownerAuditAuthoritative && audit.ownerAuditComplete);
    assert(audit.ownerAccount == fence.context.account);
    assert(audit.ownerExecutionDomain == fence.context.executionDomain);
    assert(audit.serviceEpoch == identity.serviceEpoch);
    assert(audit.serviceFencingGeneration == identity.serviceFencingGeneration);
    assert(remote.sessionAudits == 1 && local.sessionAudits == 0 && remote.fences == 1);
    const auto heldAudit=gateway.AuditSessionLease(fence);
    assert(heldAudit.status==ExecutionCommandStatus::Accepted);
    assert(heldAudit.reasonCode=="SESSION_LEASE_CONTINUATION_CONFIRMED");
    assert(heldAudit.brokerPositionQuantity=="-25000" && heldAudit.brokerPositionGeneration==9);
    assert(heldAudit.serviceEpoch==identity.serviceEpoch && heldAudit.serviceFencingGeneration==identity.serviceFencingGeneration);
    assert(remote.sessionAudits==2 && remote.fences==1 && local.sessionAudits==0);



    ExecutionEvent localEvent;
    localEvent.executionDomain = command.context.executionDomain;
    localEvent.agentId = command.context.agentId;
    localEvent.sessionId = command.context.sessionId;
    localEvent.type = "gateway.health";
    localEvent.venue = "GATEWAY";
    const std::uint64_t localSequence = localHub.Publish(localEvent);
    ExecutionEvent received;
    assert(gateway.WaitNext(command.context, 0, 0, received, reason));
    assert(received.sequence == localSequence && received.type == "gateway.health");

    ExecutionEvent upstreamEvent = localEvent;
    upstreamEvent.type = "order.accepted";
    upstreamEvent.venue = "SIMULATOR";
    upstream.Publish(upstreamEvent);
    assert(gateway.WaitNext(command.context, localSequence, 100, received, reason));
    assert(received.type == "order.accepted");
    assert(received.upstreamServiceEpoch == identity.serviceEpoch);
    assert(received.upstreamServiceFencingGeneration ==
        identity.serviceFencingGeneration);
    assert(received.upstreamStreamEpoch == identity.serviceEpoch);
    assert(received.upstreamSequence > 0);
    const std::uint64_t relayedLocalSequence = received.sequence;
    upstreamEvent.type = "order.update.1";
    upstream.Publish(upstreamEvent);
    upstreamEvent.type = "order.update.2";
    upstream.Publish(upstreamEvent);
    upstreamEvent.type = "order.update.3";
    upstream.Publish(upstreamEvent);
    assert(gateway.WaitNext(command.context, relayedLocalSequence, 100, received, reason));
    assert(received.type == "system.execution_stream_gap");
    assert(received.status == "AuthoritativeResyncRequired");
    const std::uint64_t gapLocalSequence = received.sequence;

    // A same-identity event cannot impersonate a resync control merely by
    // choosing a system.* type and AuthoritativeResyncRequired status.
    ExecutionEvent spoofedControl = upstreamEvent;
    spoofedControl.type = "system.untrusted_resync";
    spoofedControl.venue = "SIMULATOR";
    spoofedControl.status = "AuthoritativeResyncRequired";
    spoofedControl.reasonCode = "EXECUTION_EVENT_GAP";
    spoofedControl.upstreamServiceEpoch = identity.serviceEpoch;
    spoofedControl.upstreamServiceFencingGeneration =
        identity.serviceFencingGeneration;
    spoofedControl.upstreamStreamEpoch = identity.serviceEpoch;
    spoofedControl.upstreamSequence = 999;
    assert(localHub.Publish(spoofedControl) > gapLocalSequence);
    assert(!gateway.WaitNext(command.context, gapLocalSequence, 0, received, reason));
    assert(reason == "EXECUTION_EVENT_AUTHORITATIVE_RESYNC_REQUIRED");

    ExecutionGatewayRuntimeConfig paperConfig = RemoteConfig(executionSocket, eventSocket);
    paperConfig.mode = ExecutionGatewayMode::Paper;
    ExecutionGatewayRuntimeComposition paperGateway(local, localHub, paperConfig);
    assert(paperGateway.Start(reason));
    assert(std::string(paperGateway.ModeName()) == "PAPER");
    IbPlaceOrderCommand wrongContext = Place(Owner("paper-wrong-context"));
    const ExecutionCommandResult rejected = paperGateway.PlaceIbOrder(wrongContext);
    assert(rejected.status == ExecutionCommandStatus::Rejected);
    assert(rejected.reasonCode == "EXECUTION_GATEWAY_CONTEXT_MISMATCH");
    assert(remote.places == 1);
    ExecutionControlCommand wrongFence;
    wrongFence.context = wrongContext.context;
    assert(paperGateway.FenceSessionOwner(wrongFence).reasonCode ==
        "EXECUTION_GATEWAY_CONTEXT_MISMATCH");
    ExecutionEvent ignored;
    assert(!paperGateway.WaitNext(wrongContext.context, 0, 0, ignored, reason));
    assert(reason == "EXECUTION_GATEWAY_CONTEXT_MISMATCH");
    IbPlaceOrderCommand paperCommand = Place(PaperOwner("paper-place"));
    paperCommand = Previewed(paperGateway, paperCommand);
    assert(paperGateway.PlaceIbOrder(paperCommand).status == ExecutionCommandStatus::Accepted);
    assert(remote.places == 2);
    IbPlaceOrderCommand domainPaperCommand = Place(
        PaperOwner("domain-paper-place", "PAPER:codex-a"));
    domainPaperCommand = Previewed(paperGateway, domainPaperCommand);
    assert(paperGateway.PlaceIbOrder(domainPaperCommand).status ==
        ExecutionCommandStatus::Accepted);
    assert(remote.places == 3);
    const std::vector<std::string> invalidPaperDomains{
        "PAPER:", "PAPER:Codex-a", "PAPER:codex_a",
        "PAPER:1codex", "PAPER:codex/a",
        "PAPER:abcdefghijklmnopqrstuvwxyzabcdefg"};
    for (std::size_t i = 0; i < invalidPaperDomains.size(); ++i)
    {
        IbPlaceOrderCommand invalid = Place(
            PaperOwner("invalid-paper-domain-" + std::to_string(i),
                       invalidPaperDomains[i]));
        const ExecutionCommandResult invalidResult =
            paperGateway.PlaceIbOrder(invalid);
        assert(invalidResult.status == ExecutionCommandStatus::Rejected);
        assert(invalidResult.reasonCode ==
            "EXECUTION_GATEWAY_CONTEXT_MISMATCH");
        assert(remote.places == 3);
    }
    eventServer->Stop();
    assert(!gateway.ProbeRemoteService(probedIdentity, reason));
    assert(!reason.empty());
    executionServer->Stop();
    ::unlink(executionSocket.c_str());
    ::unlink(eventSocket.c_str());
}

void RunDualSocketIdentityMismatch(
    const std::string& suffix,
    const ExecutionServiceIdentity& mutationIdentity,
    const ExecutionServiceIdentity& eventIdentity)
{
    const std::string prefix = "/tmp/hepta-gateway-identity-" +
        std::to_string(::getpid()) + "-" + suffix;
    const std::string executionSocket = prefix + ".sock";
    const std::string eventSocket = prefix + ".events.sock";
    const std::shared_ptr<ExecutionServiceLifecycleGate> gate(
        new ExecutionServiceLifecycleGate());
    const std::set<std::uint32_t> uid{static_cast<std::uint32_t>(::geteuid())};
    FakeAuthority local;
    FakeAuthority remote;
    ExecutionEventHub upstream(8, eventIdentity.serviceEpoch);
    CountingEventSource source(upstream);
    ExecutionEventHub localHub(8, "gateway-local-identity-" + suffix);
    UnixExecutionServiceServer executionServer(remote, &remote);
    UnixExecutionEventFeedServer eventServer(source, eventIdentity, gate);
    std::string reason;
    assert(executionServer.StartFromFd(ActivatedSocket(executionSocket), uid,
        mutationIdentity, gate, reason));
    assert(eventServer.StartFromFd(ActivatedSocket(eventSocket), uid, reason));
    gate->ready.store(true);

    ExecutionGatewayRuntimeComposition gateway(local, localHub,
        RemoteConfig(executionSocket, eventSocket));
    assert(gateway.Start(reason));
    IbPlaceOrderCommand command = Place(Owner("dual-identity-" + suffix));
    const ExecutionCommandResult mutation = gateway.PlaceIbOrder(command);
    assert(mutation.status == ExecutionCommandStatus::Rejected);
    if (mutation.reasonCode != "EXECUTION_GATEWAY_DAEMON_IDENTITY_MISMATCH")
    {
        std::cerr << "dual_socket_identity_mismatch_unexpected:"
                  << " suffix=" << suffix
                  << " reason=" << mutation.reasonCode
                  << " detail=" << mutation.detail << '\n';
    }
    assert(mutation.reasonCode == "EXECUTION_GATEWAY_DAEMON_IDENTITY_MISMATCH");
    assert(remote.places == 0);
    assert(source.reads.load() == 0);

    ExecutionEvent ignored;
    assert(!gateway.WaitNext(command.context, 0, 0, ignored, reason));
    assert(reason == "EXECUTION_GATEWAY_DAEMON_IDENTITY_MISMATCH");
    assert(remote.places == 0);
    assert(source.reads.load() == 0);
    assert(localHub.Pending(command.context.executionDomain,
        command.context.agentId, command.context.sessionId, 0) == 0);

    eventServer.Stop();
    executionServer.Stop();
    ::unlink(executionSocket.c_str());
    ::unlink(eventSocket.c_str());
}

void TestDualSocketIdentityMismatchIsFailClosed()
{
    RunDualSocketIdentityMismatch("epoch",
        Identity("mutation-daemon", 41), Identity("event-daemon", 41));
    RunDualSocketIdentityMismatch("generation",
        Identity("shared-daemon", 51), Identity("shared-daemon", 52));
}

void TestEventRestartRequiresIdentityRefreshAndReconcile()
{
    const std::string prefix = "/tmp/hepta-gateway-restart-" +
        std::to_string(::getpid());
    const std::string executionSocket = prefix + ".sock";
    const std::string eventSocket = prefix + ".events.sock";
    const ExecutionServiceIdentity firstIdentity = Identity("daemon-before-restart", 61);
    const ExecutionServiceIdentity secondIdentity = Identity("daemon-after-restart", 61);
    const std::set<std::uint32_t> uid{static_cast<std::uint32_t>(::geteuid())};
    FakeAuthority local;
    FakeAuthority remote;
    ExecutionEventHub localHub(16, "gateway-local-restart");
    std::unique_ptr<ExecutionEventHub> upstream(
        new ExecutionEventHub(8, firstIdentity.serviceEpoch));
    std::unique_ptr<CountingEventSource> source(
        new CountingEventSource(*upstream));
    std::shared_ptr<ExecutionServiceLifecycleGate> gate(
        new ExecutionServiceLifecycleGate());
    std::unique_ptr<UnixExecutionServiceServer> executionServer(
        new UnixExecutionServiceServer(remote, &remote));
    std::unique_ptr<UnixExecutionEventFeedServer> eventServer(
        new UnixExecutionEventFeedServer(*source, firstIdentity, gate));
    std::string reason;
    assert(executionServer->StartFromFd(ActivatedSocket(executionSocket), uid,
        firstIdentity, gate, reason));
    assert(eventServer->StartFromFd(ActivatedSocket(eventSocket), uid, reason));
    gate->ready.store(true);

    ExecutionGatewayRuntimeComposition gateway(local, localHub,
        RemoteConfig(executionSocket, eventSocket));
    assert(gateway.Start(reason));
    AgentExecutionContext owner = Owner("event-before-restart");
    ExecutionEvent upstreamEvent;
    upstreamEvent.executionDomain = owner.executionDomain;
    upstreamEvent.agentId = owner.agentId;
    upstreamEvent.sessionId = owner.sessionId;
    upstreamEvent.type = "order.before.restart";
    upstreamEvent.venue = "SIMULATOR";
    assert(upstream->Publish(upstreamEvent) != 0);
    ExecutionEvent received;
    assert(gateway.WaitNext(owner, 0, 100, received, reason));
    assert(received.type == "order.before.restart");
    assert(received.upstreamServiceEpoch == firstIdentity.serviceEpoch);
    const std::uint64_t oldRelayedLocalSequence = received.sequence;
    assert(source->reads.load() == 1);

    // Model a previously relayed but not yet delivered A event. This is the
    // actual local-backlog case: after B is observed it must be skipped and a
    // B resync control must be delivered first.
    ExecutionEvent staleRelayed = upstreamEvent;
    staleRelayed.type = "order.stale.local.backlog";
    staleRelayed.upstreamServiceEpoch = firstIdentity.serviceEpoch;
    staleRelayed.upstreamServiceFencingGeneration =
        firstIdentity.serviceFencingGeneration;
    staleRelayed.upstreamStreamEpoch = firstIdentity.serviceEpoch;
    staleRelayed.upstreamSequence = 2;
    const std::uint64_t staleLocalSequence = localHub.Publish(staleRelayed);
    assert(staleLocalSequence > oldRelayedLocalSequence);

    eventServer->Stop();
    executionServer->Stop();
    eventServer.reset();
    executionServer.reset();
    source.reset();
    upstream.reset(new ExecutionEventHub(8, secondIdentity.serviceEpoch));
    source.reset(new CountingEventSource(*upstream));
    gate.reset(new ExecutionServiceLifecycleGate());
    executionServer.reset(new UnixExecutionServiceServer(remote, &remote));
    eventServer.reset(new UnixExecutionEventFeedServer(
        *source, secondIdentity, gate));
    assert(executionServer->StartFromFd(ActivatedSocket(executionSocket), uid,
        secondIdentity, gate, reason));
    assert(eventServer->StartFromFd(ActivatedSocket(eventSocket), uid, reason));
    gate->ready.store(true);

    upstreamEvent.type = "order.after.restart";
    assert(upstream->Publish(upstreamEvent) != 0);

    ExecutionServiceIdentity refreshedIdentity;
    assert(gateway.ProbeRemoteService(refreshedIdentity, reason));
    assert(refreshedIdentity.serviceEpoch == secondIdentity.serviceEpoch);
    assert(refreshedIdentity.serviceFencingGeneration ==
        secondIdentity.serviceFencingGeneration);

    assert(gateway.WaitNext(owner, oldRelayedLocalSequence, 0, received, reason));
    assert(received.type == "system.execution_service_identity_changed");
    assert(received.status == "AuthoritativeResyncRequired");
    assert(received.reasonCode == "EXECUTION_EVENT_SERVICE_IDENTITY_CHANGED");
    assert(received.upstreamServiceEpoch == secondIdentity.serviceEpoch);
    assert(received.upstreamServiceFencingGeneration ==
        secondIdentity.serviceFencingGeneration);
    assert(received.sequence > staleLocalSequence);
    assert(source->reads.load() == 0);
    const std::uint64_t resyncLocalSequence = received.sequence;
    assert(localHub.Pending(owner.executionDomain, owner.agentId, owner.sessionId,
        oldRelayedLocalSequence) == 2);

    ExecutionControlCommand reconcile;
    reconcile.context = owner;
    reconcile.context.toolCallId = "event-restart-reconcile";
    const ExecutionControlStatusResult reconciled = gateway.ReconcileAuthoritativeState(reconcile);
    assert(reconciled.status == ExecutionCommandStatus::Accepted);
    assert(!reconciled.mutationBlocked);
    assert(remote.reconciles == 1);

    assert(gateway.WaitNext(owner, resyncLocalSequence, 100, received, reason));
    assert(received.type == "order.after.restart");
    assert(received.upstreamServiceEpoch == secondIdentity.serviceEpoch);
    assert(received.upstreamServiceFencingGeneration ==
        secondIdentity.serviceFencingGeneration);
    assert(received.sequence > resyncLocalSequence);
    assert(source->reads.load() == 1);
    assert(localHub.Pending(owner.executionDomain, owner.agentId, owner.sessionId,
        oldRelayedLocalSequence) == 3);

    eventServer->Stop();
    executionServer->Stop();
    ::unlink(executionSocket.c_str());
    ::unlink(eventSocket.c_str());
}

void TestValidatedPairIsPinnedAcrossConcurrentRefresh()
{
    const std::string prefix = "/tmp/hepta-gateway-pair-race-" +
        std::to_string(::getpid());
    const std::string executionSocket = prefix + ".sock";
    const std::string eventSocket = prefix + ".events.sock";
    const ExecutionServiceIdentity firstIdentity = Identity("pair-race-a", 71);
    const ExecutionServiceIdentity secondIdentity = Identity("pair-race-b", 71);
    const std::set<std::uint32_t> uid{static_cast<std::uint32_t>(::geteuid())};
    FakeAuthority local;
    FakeAuthority remote;
    ExecutionEventHub localHub(16, "gateway-local-pair-race");

    std::mutex hookMutex;
    std::condition_variable hookChanged;
    bool blockFirstPlace = false;
    bool releaseFirstPlace = false;
    int resolvedPlaces = 0;
    ExecutionGatewayRuntimeTestHooks hooks;
    hooks.onStage = [&](const char* stage) {
        if (std::string(stage) != "after_place_identity_resolved") return;
        std::unique_lock<std::mutex> lock(hookMutex);
        if (!blockFirstPlace) return;
        ++resolvedPlaces;
        hookChanged.notify_all();
        if (resolvedPlaces == 1)
            hookChanged.wait(lock, [&]() { return releaseFirstPlace; });
    };

    std::shared_ptr<ExecutionServiceLifecycleGate> gate(
        new ExecutionServiceLifecycleGate());
    std::unique_ptr<ExecutionEventHub> upstream(
        new ExecutionEventHub(8, firstIdentity.serviceEpoch));
    std::unique_ptr<UnixExecutionServiceServer> executionServer(
        new UnixExecutionServiceServer(remote, &remote));
    std::unique_ptr<UnixExecutionEventFeedServer> eventServer(
        new UnixExecutionEventFeedServer(*upstream, firstIdentity, gate));
    std::string reason;
    assert(executionServer->StartFromFd(ActivatedSocket(executionSocket), uid,
        firstIdentity, gate, reason));
    assert(eventServer->StartFromFd(ActivatedSocket(eventSocket), uid, reason));
    gate->ready.store(true);

    ExecutionGatewayRuntimeComposition gateway(local, localHub,
        RemoteConfig(executionSocket, eventSocket), hooks);
    assert(gateway.Start(reason));
    blockFirstPlace = true;
    IbPlaceOrderCommand oldCommand = Place(Owner("pair-race-old"));
    ExecutionCommandResult oldResult;
    std::thread oldCall([&]() { oldResult = gateway.PlaceIbOrder(oldCommand); });
    {
        std::unique_lock<std::mutex> lock(hookMutex);
        assert(hookChanged.wait_for(lock, std::chrono::seconds(2),
            [&]() { return resolvedPlaces == 1; }));
    }

    eventServer->Stop();
    executionServer->Stop();
    eventServer.reset();
    executionServer.reset();
    upstream.reset(new ExecutionEventHub(8, secondIdentity.serviceEpoch));
    gate.reset(new ExecutionServiceLifecycleGate());
    executionServer.reset(new UnixExecutionServiceServer(remote, &remote));
    eventServer.reset(new UnixExecutionEventFeedServer(
        *upstream, secondIdentity, gate));
    assert(executionServer->StartFromFd(ActivatedSocket(executionSocket), uid,
        secondIdentity, gate, reason));
    assert(eventServer->StartFromFd(ActivatedSocket(eventSocket), uid, reason));
    gate->ready.store(true);

    IbPlaceOrderCommand refreshCommand = Place(Owner("pair-race-refresh"));
    const ExecutionCommandResult splitPair = gateway.PlaceIbOrder(refreshCommand);
    assert(splitPair.status == ExecutionCommandStatus::Rejected);
    assert(splitPair.reasonCode == "EXECUTION_PREVIEW_PERMIT_UNKNOWN_OR_CONSUMED");
    assert(remote.places == 0);
    refreshCommand.context.toolCallId = "pair-race-current-preview";
    refreshCommand = Previewed(gateway, refreshCommand);
    const ExecutionCommandResult current = gateway.PlaceIbOrder(refreshCommand);
    assert(current.status == ExecutionCommandStatus::Accepted);
    assert(remote.places == 1);

    {
        std::lock_guard<std::mutex> lock(hookMutex);
        releaseFirstPlace = true;
    }
    hookChanged.notify_all();
    oldCall.join();
    assert(oldResult.status == ExecutionCommandStatus::Rejected);
    assert(oldResult.reasonCode == "EXECUTION_SERVICE_EPOCH_MISMATCH");
    assert(remote.places == 1);

    eventServer->Stop();
    executionServer->Stop();
    ::unlink(executionSocket.c_str());
    ::unlink(eventSocket.c_str());
}

void TestSameOwnerWaitIdentityObservationIsSerialized()
{
    const std::string prefix = "/tmp/hepta-gateway-wait-race-" +
        std::to_string(::getpid());
    const std::string executionSocket = prefix + ".sock";
    const std::string eventSocket = prefix + ".events.sock";
    const ExecutionServiceIdentity firstIdentity = Identity("wait-race-a", 81);
    const ExecutionServiceIdentity secondIdentity = Identity("wait-race-b", 81);
    const std::set<std::uint32_t> uid{static_cast<std::uint32_t>(::geteuid())};
    FakeAuthority local;
    FakeAuthority remote;
    ExecutionEventHub localHub(16, "gateway-local-wait-race");

    std::mutex hookMutex;
    std::condition_variable hookChanged;
    bool blockWait = false;
    bool releaseFirstWait = false;
    int resolvedWaits = 0;
    ExecutionGatewayRuntimeTestHooks hooks;
    hooks.onStage = [&](const char* stage) {
        if (std::string(stage) != "after_wait_identity_resolved") return;
        std::unique_lock<std::mutex> lock(hookMutex);
        if (!blockWait) return;
        ++resolvedWaits;
        hookChanged.notify_all();
        if (resolvedWaits == 1)
            hookChanged.wait(lock, [&]() { return releaseFirstWait; });
    };

    std::shared_ptr<ExecutionServiceLifecycleGate> gate(
        new ExecutionServiceLifecycleGate());
    std::unique_ptr<ExecutionEventHub> upstream(
        new ExecutionEventHub(8, firstIdentity.serviceEpoch));
    std::unique_ptr<UnixExecutionServiceServer> executionServer(
        new UnixExecutionServiceServer(remote, &remote));
    std::unique_ptr<UnixExecutionEventFeedServer> eventServer(
        new UnixExecutionEventFeedServer(*upstream, firstIdentity, gate));
    std::string reason;
    assert(executionServer->StartFromFd(ActivatedSocket(executionSocket), uid,
        firstIdentity, gate, reason));
    assert(eventServer->StartFromFd(ActivatedSocket(eventSocket), uid, reason));
    gate->ready.store(true);

    ExecutionGatewayRuntimeComposition gateway(local, localHub,
        RemoteConfig(executionSocket, eventSocket), hooks);
    assert(gateway.Start(reason));
    AgentExecutionContext owner = Owner("wait-race-owner");
    blockWait = true;
    bool oldOk = true;
    bool newOk = false;
    ExecutionEvent oldEvent;
    ExecutionEvent newEvent;
    std::string oldReason;
    std::string newReason;
    std::thread oldWait([&]() {
        oldOk = gateway.WaitNext(owner, 0, 0, oldEvent, oldReason);
    });
    {
        std::unique_lock<std::mutex> lock(hookMutex);
        assert(hookChanged.wait_for(lock, std::chrono::seconds(2),
            [&]() { return resolvedWaits == 1; }));
    }

    eventServer->Stop();
    executionServer->Stop();
    eventServer.reset();
    executionServer.reset();
    upstream.reset(new ExecutionEventHub(8, secondIdentity.serviceEpoch));
    gate.reset(new ExecutionServiceLifecycleGate());
    executionServer.reset(new UnixExecutionServiceServer(remote, &remote));
    eventServer.reset(new UnixExecutionEventFeedServer(
        *upstream, secondIdentity, gate));
    assert(executionServer->StartFromFd(ActivatedSocket(executionSocket), uid,
        secondIdentity, gate, reason));
    assert(eventServer->StartFromFd(ActivatedSocket(eventSocket), uid, reason));
    gate->ready.store(true);

    std::thread newWait([&]() {
        newOk = gateway.WaitNext(owner, 0, 0, newEvent, newReason);
    });
    {
        std::unique_lock<std::mutex> lock(hookMutex);
        assert(!hookChanged.wait_for(lock, std::chrono::milliseconds(100),
            [&]() { return resolvedWaits > 1; }));
        releaseFirstWait = true;
    }
    hookChanged.notify_all();
    oldWait.join();
    newWait.join();

    assert(!oldOk);
    assert(oldReason == "EXECUTION_EVENT_SERVICE_IDENTITY_MISMATCH");
    assert(newOk);
    assert(newEvent.type == "system.execution_service_identity_changed");
    assert(newEvent.upstreamServiceEpoch == secondIdentity.serviceEpoch);
    assert(resolvedWaits == 2);

    eventServer->Stop();
    executionServer->Stop();
    ::unlink(executionSocket.c_str());
    ::unlink(eventSocket.c_str());
}
}

int main()
{
    TestConfigIsHardOffAndStrict();
    TestIdentityPreflightBudget(2500, false);
    TestIdentityPreflightBudget(2500, true);
    TestIdentityPreflightBudget(200, false);
    TestIdentityPreflightBudget(2500, false, 100);
    TestIdentityRefreshSharesAbsoluteBudget();
    TestIdentityInvalidationHonorsCacheLockBudget();
    TestIdentityBudgetDoesNotShortenAuthorityOrEventWait();
    TestDisabledDelegatesLocal();
    TestMutationToolsNeverFallBackToLocalAuthority();
    TestActivatedBacklogRejectsOldServiceEpoch();
    TestRemoteAuthorityControlsAndRelay();
    TestDualSocketIdentityMismatchIsFailClosed();
    TestEventRestartRequiresIdentityRefreshAndReconcile();
    TestValidatedPairIsPinnedAcrossConcurrentRefresh();
    TestSameOwnerWaitIdentityObservationIsSerialized();
    std::cout << "execution_gateway_runtime_evidence: remote_reconnect=verified"
              << " event_gap=verified local_remote_merge=verified"
              << " session_fence_control=verified paper_context_isolation=verified"
              << " old_epoch_backlog_rejected=verified activated_fd_preserved=verified"
              << " dual_socket_identity_mismatch_rejected=verified"
              << " event_restart_identity_refresh=verified"
              << " old_event_identity_backlog_rejected=verified"
              << " first_post_restart_identity_mismatch=verified"
              << " explicit_identity_refresh_before_reconcile=verified"
              << " validated_pair_dispatch_pinned=verified"
              << " owner_wait_identity_serialized=verified"
              << " mutation_tools_remote_only=verified"
              << " resync_control_exact_match=verified"
              << " identity_phase_absolute_budget=verified"
              << " short_io_and_caller_budgets_preserved=verified"
              << " identity_cache_contention_bounded=verified"
              << " authority_response_and_event_wait_windows_preserved=verified"
              << std::endl;
    std::cout << "execution_gateway_runtime_composition_tests: PASS" << std::endl;
    return 0;
}
