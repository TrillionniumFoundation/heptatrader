#include "../HeptaTrade/execution/execution_coordinator.h"
#include "../HeptaTrade/execution/execution_decision_lease_authority.h"
#include "../HeptaTrade/execution/execution_service_protocol.h"
#include "../HeptaTrade/execution/unix_execution_service.h"
#include "../HeptaTrade/execution/unix_execution_service_internal.h"
#include "../HeptaTrade/simulator/deterministic_execution_venue.h"
#include "../HeptaTrade/tools/trading_tool_registry.h"

#include <arpa/inet.h>
#include <cassert>
#include <cerrno>
#include <chrono>
#include <cstring>
#include <cstdio>
#include <cstdlib>
#include <fcntl.h>
#include <fstream>
#include <functional>
#include <iostream>
#include <limits>
#include <set>
#include <string>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <unistd.h>

namespace
{
// This process fixture performs durable journal and venue-ledger fsyncs.  Keep
// its transport deadline above transient loaded-host storage latency; the
// surrounding CTest/soak deadlines still bound an actual hang.
const int kDurableProcessE2eIoTimeoutMs = 5000;

void TestSystemdSocketActivatorCredentialBoundary()
{
    const std::set<std::uint32_t> serviceUids = {2121};
    assert(HeptaExecutionServiceInternal::AllowedServerPeerCredential(
        2121, 7001, serviceUids));
    assert(HeptaExecutionServiceInternal::AllowedServerPeerCredential(
        0, 1, serviceUids));
    assert(!HeptaExecutionServiceInternal::AllowedServerPeerCredential(
        0, 2, serviceUids));
    assert(!HeptaExecutionServiceInternal::AllowedServerPeerCredential(
        2122, 1, serviceUids));
}

std::uint32_t WrongEffectiveUid()
{
    const std::uint32_t current = static_cast<std::uint32_t>(::geteuid());
    return current == std::numeric_limits<std::uint32_t>::max() ?
        current - 1 : current + 1;
}

void HoldServerForSoakSampling()
{
    const char* value = std::getenv("HEPTA_E2E_SOAK_HOLD_MS");
    if (value == nullptr || *value == '\0') return;
    char* end = nullptr;
    const long milliseconds = std::strtol(value, &end, 10);
    if (end == value || *end != '\0' || milliseconds <= 0 || milliseconds > 1000) return;
    ::usleep(static_cast<useconds_t>(milliseconds) * 1000);
}

bool AppendDurableVenueSend(const std::string& path)
{
    if (path.empty()) return true;
    const int fd = ::open(path.c_str(), O_WRONLY | O_APPEND | O_CREAT | O_CLOEXEC, 0600);
    if (fd < 0) return false;
    const char line[] = "send\n";
    std::size_t offset = 0;
    while (offset < sizeof(line) - 1)
    {
        const ssize_t count = ::write(fd, line + offset, sizeof(line) - 1 - offset);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0)
        {
            ::close(fd);
            return false;
        }
        offset += static_cast<std::size_t>(count);
    }
    const bool synced = ::fsync(fd) == 0;
    const bool closed = ::close(fd) == 0;
    return synced && closed;
}

int CountDurableVenueSends(const std::string& path)
{
    std::ifstream input(path.c_str());
    int count = 0;
    std::string line;
    while (std::getline(input, line))
        if (line == "send") ++count;
    return count;
}

class FaultInjectingAuthority : public ExecutionAuthority
{
public:
    FaultInjectingAuthority(ExecutionAuthority& inner, const std::string& mode)
        : m_inner(inner), m_mode(mode)
    {
    }

    ExecutionCommandResult PlaceOrder(const PlaceOrderCommand& command) override
    {
        if (m_mode == "before_dispatch") ::_exit(80);
        const ExecutionCommandResult result = m_inner.PlaceIbOrder(command);
        if (m_mode == "after_receipt") ::_exit(83);
        return result;
    }

    ExecutionCommandResult CancelOrder(const CancelOrderCommand& command) override
    {
        return m_inner.CancelIbOrder(command);
    }

private:
    ExecutionAuthority& m_inner;
    std::string m_mode;
};

class TestExecutionControlAuthority : public ExecutionControlAuthority,
                                      public ExecutionReadAuthority
{
public:
    typedef std::function<std::set<long>()> ActiveOrderProvider;

    TestExecutionControlAuthority(ExecutionCoordinator& coordinator,
                                  const ActiveOrderProvider& activeOrders)
        : m_coordinator(coordinator), m_activeOrders(activeOrders)
    {
    }

    ExecutionControlResult QueryCommandStatus(
        const ExecutionControlCommand& command) override
    {
        ExecutionControlResult result = Begin(command);
        ExecutionCommandResult target;
        if (!m_coordinator.GetCommandStatus(command.context.agentId,
                command.context.sessionId, command.targetCommandId, target))
        {
            result.status = ExecutionCommandStatus::Rejected;
            result.reasonCode = "EXECUTION_COMMAND_NOT_FOUND";
            return result;
        }
        result.status = ExecutionCommandStatus::Accepted;
        result.targetCommandId = command.targetCommandId;
        result.targetStatus = target.status;
        result.orderId = target.orderId;
        result.reasonCode = target.reasonCode;
        result.detail = target.detail;
        result.mutationBlocked = m_coordinator.IsMutationBlocked(nullptr);
        return result;
    }

    ExecutionControlResult FenceSessionOwner(
        const ExecutionControlCommand& command) override
    {
        ExecutionControlResult result = Begin(command);
        result.status = ExecutionCommandStatus::Accepted;
        result.affectedCount = m_coordinator.FenceSessionOwner(
            command.context.agentId, command.context.sessionId);
        result.mutationBlocked = m_coordinator.IsMutationBlocked(nullptr);
        return result;
    }

    ExecutionControlResult ReleaseSessionOwnerFence(
        const ExecutionControlCommand& command) override
    {
        ExecutionControlResult result = ReconcileAuthoritativeState(command);
        if (result.status != ExecutionCommandStatus::Accepted) return result;
        std::string reason;
        if (!m_coordinator.AuditAndReleaseSessionOwnerFence(command.context.agentId,
                command.context.sessionId, true, reason))
        {
            result.status = ExecutionCommandStatus::Rejected;
            result.reasonCode = reason;
        }
        return result;
    }

    ExecutionControlResult ReconcileAuthoritativeState(
        const ExecutionControlCommand& command) override
    {
        ExecutionControlResult result = Begin(command);
        std::string reason;
        std::size_t removed = 0;
        if (!m_coordinator.ReconcileOrderOwners(m_activeOrders(), true, removed, reason))
        {
            result.status = ExecutionCommandStatus::Rejected;
            result.reasonCode = reason;
            return result;
        }
        result.status = ExecutionCommandStatus::Accepted;
        result.affectedCount = removed;
        result.mutationBlocked = m_coordinator.IsMutationBlocked(&reason);
        result.reasonCode = result.mutationBlocked ? reason : std::string();
        return result;
    }

    ExecutionCommandResult PreviewOrder(
        const PlaceOrderCommand& command) override
    {
        ExecutionCommandResult result;
        result.commandId = command.context.toolCallId;
        if (command.order.totalQuantity <= 0.0 ||
            command.expiresAtMs <= OmsJournal::NowEpochMs() ||
            command.timeInForce != "DAY")
        {
            result.status = ExecutionCommandStatus::Rejected;
            result.reasonCode = "INVALID_ORDER";
            return result;
        }
        result.status = ExecutionCommandStatus::Accepted;
        result.detail =
            "{\"source\":\"PROCESS_FAKE\",\"authoritative\":true,"
            "\"risk_approved\":true}";
        return result;
    }

    bool IsDurablePlaceReplay(
        const PlaceOrderCommand& command) const override
    {
        return m_coordinator.IsDurablePlaceReplay(command);
    }

    ExecutionCommandResult ReadAuthoritativeState(
        const ExecutionReadCommand& command) override
    {
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Accepted;
        result.commandId = command.context.toolCallId;
        result.detail =
            "{\"source\":\"PROCESS_FAKE\",\"authoritative\":true}";
        return result;
    }

private:
    static ExecutionControlResult Begin(const ExecutionControlCommand& command)
    {
        ExecutionControlResult result;
        result.commandId = command.context.toolCallId;
        return result;
    }

    ExecutionCoordinator& m_coordinator;
    ActiveOrderProvider m_activeOrders;
};

int RunServer(const std::string& socketPath, const std::string& journalPath,
              std::uint64_t leaseToken, std::uint64_t leaseGeneration,
              bool reconcileEmpty, int readyFd,
              const std::string& faultMode = std::string(),
              const std::string& venueSendLedgerPath = std::string())
{
    static_cast<void>(leaseToken);
    static_cast<void>(leaseGeneration);
    OmsJournal journal;
    if (!journal.Init(journalPath)) return 20;

    DeterministicExecutionVenue venue;
    venue.SetQuote("EUR.USD", 1.1000, 1.1002);
    venue.SetQuote("GBP.USD", 1.2500, 1.2502);
    long maxOrderId = 999999;
    std::set<long> recoveredActiveOrderIds;
    journal.Replay([&](const OmsJournalEvent& event) {
        if (event.orderId > maxOrderId) maxOrderId = event.orderId;
        if (event.eventType == "place_sent" && event.orderId >= 0)
            recoveredActiveOrderIds.insert(event.orderId);
        if (event.eventType == "order_owner_reconciled_terminal" && event.orderId >= 0)
            recoveredActiveOrderIds.erase(event.orderId);
    });
    venue.RestoreNextOrderIdAtLeast(maxOrderId + 1);

    const std::shared_ptr<ExecutionDecisionLeaseAuthority> decisionLeases(
        new ExecutionDecisionLeaseAuthority());
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.placeIbOrder = [&](const IBContractLite& contract, const IBOrderLite& order, long* orderId) {
        if (faultMode == "before_send") ::_exit(81);
        const bool placed = venue.PlaceOrder(contract, order, orderId);
        if (placed && !AppendDurableVenueSend(venueSendLedgerPath)) ::_exit(84);
        if (placed && faultMode == "after_venue_send") ::_exit(82);
        return placed;
    };
    callbacks.cancelIbOrder = [&](long orderId) {
        const std::set<long>::iterator recovered = recoveredActiveOrderIds.find(orderId);
        if (recovered != recoveredActiveOrderIds.end())
        {
            recoveredActiveOrderIds.erase(recovered);
            return true;
        }
        return venue.CancelOrder(orderId);
    };
    callbacks.canCancelIbOrder = [&](long orderId, std::string* reason) {
        if (recoveredActiveOrderIds.find(orderId) != recoveredActiveOrderIds.end()) return true;
        return venue.CanCancelOrder(orderId, reason);
    };
    callbacks.lastIbRejectReason = [&]() { return venue.LastRejectReason(); };
    callbacks.validateDecisionLease = [decisionLeases](
        const AgentExecutionContext& context,
        const std::string& instrument, std::string* reason) {
        return decisionLeases->Validate(context, instrument, reason);
    };

    ExecutionCoordinator coordinator(journal, callbacks);
    std::string reason;
    // Recovery failures are served in a degraded state so callers can observe
    // deterministic UNCERTAIN/MUTATION_BLOCKED outcomes without redispatch.
    coordinator.RecoverFromJournal(reason);
    if (reconcileEmpty)
    {
        std::size_t removedOwners = 0;
        if (!coordinator.ReconcileOrderOwners(std::set<long>(), true, removedOwners, reason)) return 22;
    }

    FaultInjectingAuthority faultAuthority(coordinator, faultMode);
    ExecutionAuthority& servedAuthority = faultMode.empty() ?
        static_cast<ExecutionAuthority&>(coordinator) :
        static_cast<ExecutionAuthority&>(faultAuthority);
    TestExecutionControlAuthority controlAuthority(coordinator, [&]() {
        std::set<long> active = recoveredActiveOrderIds;
        const std::set<long> venueActive = venue.ActiveOrderIds();
        active.insert(venueActive.begin(), venueActive.end());
        return active;
    });
    UnixExecutionServiceServer server(
        servedAuthority, &controlAuthority, decisionLeases);
    if (!server.Start(socketPath, std::set<std::uint32_t>{static_cast<std::uint32_t>(::geteuid())},
                      reason, 32768, kDurableProcessE2eIoTimeoutMs)) return 23;
    const char ready = 'R';
    if (::write(readyFd, &ready, 1) != 1) return 24;
    ::close(readyFd);
    for (;;) ::pause();
}

pid_t SpawnFaultServer(const char* self,
                       const std::string& socketPath,
                       const std::string& journalPath,
                       const std::string& venueSendLedgerPath,
                       const std::string& faultMode)
{
    int readyPipe[2];
    assert(::pipe(readyPipe) == 0);
    const pid_t pid = ::fork();
    assert(pid >= 0);
    if (pid == 0)
    {
        ::close(readyPipe[0]);
        const std::string readyFd = std::to_string(readyPipe[1]);
        ::execl(self, self, "--fault-server", socketPath.c_str(), journalPath.c_str(),
                "77", "9", "0", readyFd.c_str(), faultMode.c_str(),
                venueSendLedgerPath.c_str(), static_cast<char*>(nullptr));
        ::_exit(127);
    }
    ::close(readyPipe[1]);
    char ready = 0;
    assert(::read(readyPipe[0], &ready, 1) == 1);
    assert(ready == 'R');
    ::close(readyPipe[0]);
    HoldServerForSoakSampling();
    return pid;
}

pid_t SpawnServer(const char* self, const std::string& socketPath, const std::string& journalPath,
                  std::uint64_t leaseToken, std::uint64_t leaseGeneration, bool reconcileEmpty)
{
    int readyPipe[2];
    assert(::pipe(readyPipe) == 0);
    const pid_t pid = ::fork();
    assert(pid >= 0);
    if (pid == 0)
    {
        ::close(readyPipe[0]);
        const std::string token = std::to_string(leaseToken);
        const std::string generation = std::to_string(leaseGeneration);
        const std::string reconcile = reconcileEmpty ? "1" : "0";
        const std::string readyFd = std::to_string(readyPipe[1]);
        ::execl(self, self, "--server", socketPath.c_str(), journalPath.c_str(), token.c_str(),
                generation.c_str(), reconcile.c_str(), readyFd.c_str(), static_cast<char*>(nullptr));
        ::_exit(127);
    }
    ::close(readyPipe[1]);
    char ready = 0;
    assert(::read(readyPipe[0], &ready, 1) == 1);
    assert(ready == 'R');
    ::close(readyPipe[0]);
    HoldServerForSoakSampling();
    return pid;
}

void KillServer(pid_t pid)
{
    assert(::kill(pid, SIGKILL) == 0);
    int status = 0;
    assert(::waitpid(pid, &status, 0) == pid);
    assert(WIFSIGNALED(status));
}

void WaitForInjectedCrash(pid_t pid, int expectedExit)
{
    int status = 0;
    assert(::waitpid(pid, &status, 0) == pid);
    assert(WIFEXITED(status));
    assert(WEXITSTATUS(status) == expectedExit);
}

TradingToolSession MakeSession(std::uint64_t leaseToken, std::uint64_t leaseGeneration)
{
    TradingToolSession session;
    session.environment = "PAPER";
    session.capabilities.insert("trade.place");
    session.executionContext.agentId = "agent-native-e2e";
    session.executionContext.sessionId = "session-e2e";
    session.executionContext.strategy = "offline-simulator";
    session.executionContext.account = "SIM";
    session.executionContext.venue = "SIMULATOR";
    session.executionContext.executionDomain = "SIM:EURUSD";
    session.executionContext.decisionLeaseFencingToken = leaseToken;
    session.executionContext.decisionLeaseGeneration = leaseGeneration;
    return session;
}

TradingToolCall MakePlace(const std::string&)
{
    TradingToolCall call;
    call.name = "trade.place_order";
    call.instrument = "EURUSD";
    call.ibContract.symbol = "EUR";
    call.ibContract.secType = "CASH";
    call.ibContract.exchange = "IDEALPRO";
    call.ibContract.currency = "USD";
    call.ibOrder.action = "BUY";
    call.ibOrder.orderType = "LMT";
    call.ibOrder.totalQuantity = 1000.0;
    call.ibOrder.lmtPrice = 1.0990;
    call.timeInForce = "DAY";
    call.referencePrice = 1.1001;
    call.expiresAtMs = OmsJournal::NowEpochMs() + 60000;
    return call;
}

IbPlaceOrderCommand MakePlaceCommand(const TradingToolSession& session,
                                     const TradingToolCall& call)
{
    IbPlaceOrderCommand command;
    command.context = session.executionContext;
    command.contract = call.ibContract;
    command.order = call.ibOrder;
    command.instrument = call.instrument;
    command.timeInForce = call.timeInForce;
    command.referencePrice = call.referencePrice;
    command.expiresAtMs = call.expiresAtMs;
    return command;
}

FlattenPositionCommand MakeFlattenCommand(
    const TradingToolSession& session,
    const TradingToolCall& call)
{
    FlattenPositionCommand command;
    command.context = session.executionContext;
    command.contract = call.ibContract;
    command.instrument = call.instrument;
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

IbPlaceOrderCommand Previewed(
    UnixExecutionServiceClient& client,
    const IbPlaceOrderCommand& input)
{
    IbPlaceOrderCommand command = input;
    command.previewPermit.clear();
    const ExecutionCommandResult preview = client.PreviewOrder(command);
    assert(preview.status == ExecutionCommandStatus::Accepted);
    command.previewPermit = PreviewField(preview, "preview_permit");
    command.context.toolCallId = PreviewField(preview, "command_id");
    return command;
}

FlattenPositionCommand PreviewedFlatten(
    UnixExecutionServiceClient& client,
    const FlattenPositionCommand& input)
{
    FlattenPositionCommand command = input;
    command.previewPermit.clear();
    const ExecutionCommandResult preview =
        client.PreviewFlattenPosition(command);
    assert(preview.status == ExecutionCommandStatus::Accepted);
    command.previewPermit = PreviewField(preview, "preview_permit");
    command.context.toolCallId = PreviewField(preview, "command_id");
    return command;
}

ExecutionCommandResult RetryFixturePlace(
    UnixExecutionServiceClient& client,
    const IbPlaceOrderCommand& command)
{
    ExecutionCommandResult result;
    for (int attempt = 0; attempt < 3; ++attempt)
    {
        result = client.PlaceIbOrder(command);
        if (result.status == ExecutionCommandStatus::Accepted ||
            result.status == ExecutionCommandStatus::Duplicate)
            return result;
        if (result.status != ExecutionCommandStatus::Uncertain ||
            result.reasonCode != "EXECUTION_SERVICE_UNAVAILABLE")
            return result;
        ::usleep(25000);
    }
    return result;
}

ExecutionCommandResult RetryFixtureCancel(
    UnixExecutionServiceClient& client,
    const IbCancelOrderCommand& command)
{
    ExecutionCommandResult result;
    for (int attempt = 0; attempt < 3; ++attempt)
    {
        result = client.CancelIbOrder(command);
        if (result.status == ExecutionCommandStatus::Accepted ||
            result.status == ExecutionCommandStatus::Duplicate)
            return result;
        if (result.status != ExecutionCommandStatus::Uncertain ||
            result.reasonCode != "EXECUTION_SERVICE_UNAVAILABLE")
            return result;
        ::usleep(25000);
    }
    return result;
}

long RequireDurableGatewayPlace(
    UnixExecutionServiceClient& client,
    const IbPlaceOrderCommand& command,
    const TradingToolResult& initial)
{
    if (initial.status == TradingToolCallStatus::Ok)
        return initial.orderId;
    if (initial.status != TradingToolCallStatus::Uncertain ||
        initial.reasonCode != "EXECUTION_SERVICE_UNAVAILABLE")
    {
        std::cerr << "gateway place failed without a retryable transport "
                  << "outcome: status=" << static_cast<int>(initial.status)
                  << " reason=" << initial.reasonCode
                  << " detail=" << initial.detail << std::endl;
        assert(false);
    }
    const ExecutionCommandResult replay =
        RetryFixturePlace(client, command);
    if (replay.status != ExecutionCommandStatus::Accepted &&
        replay.status != ExecutionCommandStatus::Duplicate)
        std::cerr << "gateway place exact-id retry did not reach a durable "
                  << "response: status=" << static_cast<int>(replay.status)
                  << " reason=" << replay.reasonCode
                  << " detail=" << replay.detail << std::endl;
    assert(replay.status == ExecutionCommandStatus::Accepted ||
           replay.status == ExecutionCommandStatus::Duplicate);
    assert(replay.commandId == command.context.toolCallId);
    return replay.orderId;
}

int CountJournalEvents(const std::string& journalPath, const std::string& eventType)
{
    OmsJournal journal;
    assert(journal.Init(journalPath));
    int count = 0;
    assert(journal.Replay([&](const OmsJournalEvent& event) {
        if (event.eventType == eventType) ++count;
    }) >= 0);
    return count;
}

bool ReadExact(int fd, char* data, std::size_t size)
{
    std::size_t offset = 0;
    while (offset < size)
    {
        const ssize_t count = ::read(fd, data + offset, size - offset);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return false;
        offset += static_cast<std::size_t>(count);
    }
    return true;
}

bool WriteExact(int fd, const char* data, std::size_t size)
{
    std::size_t offset = 0;
    while (offset < size)
    {
        const ssize_t count = ::send(fd, data + offset, size - offset, MSG_NOSIGNAL);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return false;
        offset += static_cast<std::size_t>(count);
    }
    return true;
}

std::string ExchangeFrame(const std::string& socketPath, const std::string& requestBody)
{
    const int fd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    assert(fd >= 0);
    struct sockaddr_un address;
    std::memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    assert(socketPath.size() < sizeof(address.sun_path));
    std::memcpy(address.sun_path, socketPath.c_str(), socketPath.size() + 1);
    assert(::connect(fd, reinterpret_cast<struct sockaddr*>(&address),
                     sizeof(address)) == 0);

    std::uint32_t networkLength =
        htonl(static_cast<std::uint32_t>(requestBody.size()));
    assert(WriteExact(fd, reinterpret_cast<const char*>(&networkLength),
                      sizeof(networkLength)));
    assert(WriteExact(fd, requestBody.data(), requestBody.size()));
    assert(ReadExact(fd, reinterpret_cast<char*>(&networkLength),
                     sizeof(networkLength)));
    const std::size_t responseLength = ntohl(networkLength);
    assert(responseLength > 0 && responseLength <= 32768);
    std::string responseBody(responseLength, '\0');
    assert(ReadExact(fd, &responseBody[0], responseBody.size()));
    assert(::close(fd) == 0);
    return responseBody;
}

int RunMismatchedCommandIdResponder(const std::string& socketPath, int readyFd)
{
    const int listenFd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (listenFd < 0) return 30;
    struct sockaddr_un address;
    std::memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    std::memcpy(address.sun_path, socketPath.c_str(), socketPath.size() + 1);
    ::unlink(socketPath.c_str());
    if (::bind(listenFd, reinterpret_cast<struct sockaddr*>(&address), sizeof(address)) != 0 ||
        ::chmod(socketPath.c_str(), 0600) != 0 || ::listen(listenFd, 1) != 0)
    {
        ::close(listenFd);
        return 31;
    }
    const char ready = 'R';
    if (::write(readyFd, &ready, 1) != 1) return 32;
    ::close(readyFd);

    const int clientFd = ::accept4(listenFd, nullptr, nullptr, SOCK_CLOEXEC);
    if (clientFd < 0) return 33;
    std::uint32_t networkLength = 0;
    if (!ReadExact(clientFd, reinterpret_cast<char*>(&networkLength), sizeof(networkLength))) return 34;
    const std::size_t requestLength = ntohl(networkLength);
    if (requestLength == 0 || requestLength > 32768) return 35;
    std::string requestBody(requestLength, '\0');
    if (!ReadExact(clientFd, &requestBody[0], requestBody.size())) return 36;

    ExecutionCommandResult response;
    response.status = ExecutionCommandStatus::Accepted;
    response.commandId = "different-command-id";
    response.orderId = 4242;
    response.serviceEpoch = "hexec-v6-mismatched-command-id";
    response.serviceFencingGeneration = 9;
    std::string responseBody;
    std::string reason;
    if (!ExecutionServiceProtocol::EncodeResponse(response, responseBody, reason)) return 37;
    networkLength = htonl(static_cast<std::uint32_t>(responseBody.size()));
    const bool wrote = WriteExact(clientFd, reinterpret_cast<const char*>(&networkLength),
                                  sizeof(networkLength)) &&
        WriteExact(clientFd, responseBody.data(), responseBody.size());
    ::close(clientFd);
    ::close(listenFd);
    ::unlink(socketPath.c_str());
    return wrote ? 0 : 38;
}

pid_t SpawnMismatchedCommandIdResponder(const std::string& socketPath)
{
    int readyPipe[2];
    assert(::pipe(readyPipe) == 0);
    const pid_t pid = ::fork();
    assert(pid >= 0);
    if (pid == 0)
    {
        ::close(readyPipe[0]);
        ::_exit(RunMismatchedCommandIdResponder(socketPath, readyPipe[1]));
    }
    ::close(readyPipe[1]);
    char ready = 0;
    assert(::read(readyPipe[0], &ready, 1) == 1);
    assert(ready == 'R');
    ::close(readyPipe[0]);
    return pid;
}

class RejectingExecutionAuthority : public ExecutionAuthority
{
public:
    ExecutionCommandResult PlaceOrder(const PlaceOrderCommand& command) override
    {
        return Reject(command.context.toolCallId);
    }

    ExecutionCommandResult CancelOrder(const CancelOrderCommand& command) override
    {
        return Reject(command.context.toolCallId);
    }

private:
    static ExecutionCommandResult Reject(const std::string& commandId)
    {
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Rejected;
        result.commandId = commandId;
        result.reasonCode = "COMPETING_SERVER_SHOULD_NOT_DISPATCH";
        return result;
    }
};

class CountingExecutionAuthority : public ExecutionAuthority,
                                   public ExecutionControlAuthority,
                                   public ExecutionReadAuthority
{
public:
    ExecutionCommandResult PlaceOrder(const PlaceOrderCommand& command) override
    {
        ++placeCalls;
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Accepted;
        result.commandId = command.context.toolCallId;
        result.orderId = 7001;
        return result;
    }

    ExecutionCommandResult CancelOrder(const CancelOrderCommand& command) override
    {
        ++cancelCalls;
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Accepted;
        result.commandId = command.context.toolCallId;
        result.orderId = command.orderId;
        return result;
    }

    ExecutionCommandResult FlattenPosition(
        const FlattenPositionCommand& command) override
    {
        ++flattenCalls;
        lastFlatten = command;
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Accepted;
        result.commandId = command.context.toolCallId;
        result.orderId = 7002;
        return result;
    }

    ExecutionCommandResult PreviewOrder(
        const PlaceOrderCommand& command) override
    {
        ++previewCalls;
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Accepted;
        result.commandId = command.context.toolCallId;
        result.detail =
            "{\"source\":\"COUNTING_FAKE\",\"authoritative\":true,"
            "\"risk_approved\":true}";
        return result;
    }

    ExecutionCommandResult PreviewFlattenPosition(
        const FlattenPositionCommand& command) override
    {
        ++flattenPreviewCalls;
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Accepted;
        result.commandId = command.context.toolCallId;
        result.detail =
            "{\"source\":\"COUNTING_FAKE\",\"authoritative\":true,"
            "\"position_quantity\":100,\"reduce_only\":true}";
        result.hasAuthoritativeFlattenSnapshot = true;
        result.authoritativeFlattenPositionQuantity = 100.0;
        result.authoritativeFlattenConnectionEpoch = 77;
        result.authoritativeFlattenPositionGeneration = 9;
        result.authoritativeFlattenPlanBinding =
            "counting-authority-plan-binding";
        return result;
    }

    ExecutionCommandResult ReadAuthoritativeState(
        const ExecutionReadCommand& command) override
    {
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Accepted;
        result.commandId = command.context.toolCallId;
        result.detail = authoritativeReadDetail;
        return result;
    }

    ExecutionControlResult QueryCommandStatus(
        const ExecutionControlCommand& command) override
    {
        return RejectControl(command);
    }

    ExecutionControlResult FenceSessionOwner(
        const ExecutionControlCommand& command) override
    {
        return RejectControl(command);
    }

    ExecutionControlResult ReleaseSessionOwnerFence(
        const ExecutionControlCommand& command) override
    {
        return RejectControl(command);
    }

    ExecutionControlResult ReconcileAuthoritativeState(
        const ExecutionControlCommand& command) override
    {
        return RejectControl(command);
    }

    int placeCalls = 0;
    int cancelCalls = 0;
    int previewCalls = 0;
    int flattenCalls = 0;
    int flattenPreviewCalls = 0;
    std::string authoritativeReadDetail =
        "{\"source\":\"COUNTING_FAKE\",\"authoritative\":true}";
    FlattenPositionCommand lastFlatten;

private:
    static ExecutionControlResult RejectControl(
        const ExecutionControlCommand& command)
    {
        ExecutionControlResult result;
        result.status = ExecutionCommandStatus::Rejected;
        result.commandId = command.context.toolCallId;
        result.reasonCode = "COUNTING_CONTROL_UNAVAILABLE";
        return result;
    }
};

void TestExecutionServicePeerCredentialRejection()
{
    const std::uint32_t currentUid = static_cast<std::uint32_t>(::geteuid());
    const std::uint32_t wrongUid = WrongEffectiveUid();
    TradingToolSession session = MakeSession(77, 9);
    session.executionContext.toolCallId = "peer-credential-place";
    const IbPlaceOrderCommand command =
        MakePlaceCommand(session, MakePlace("peer-credential-place"));
    std::string reason;

    const std::string serverRejectPath = "/tmp/hepta-execution-peer-server-" +
        std::to_string(::getpid()) + ".sock";
    CountingExecutionAuthority serverRejectAuthority;
    UnixExecutionServiceServer serverReject(serverRejectAuthority);
    assert(serverReject.Start(serverRejectPath, std::set<std::uint32_t>{wrongUid},
                              reason, 32768, 250));
    UnixExecutionServiceClient normalClient(serverRejectPath, 250, 32768,
                                            std::set<std::uint32_t>{currentUid});
    const ExecutionCommandResult rejectedGateway = normalClient.PlaceIbOrder(command);
    assert(rejectedGateway.status == ExecutionCommandStatus::Uncertain);
    assert(rejectedGateway.reasonCode == "EXECUTION_SERVICE_UNAVAILABLE");
    // The rejecting server closes immediately after SO_PEERCRED validation.
    // Depending on local scheduling, that close is observed while writing the
    // identity query or while reading its response; both are the same
    // fail-closed transport outcome and neither reaches authority dispatch.
    assert(rejectedGateway.detail == "request write failed" ||
           rejectedGateway.detail == "response read failed");
    assert(serverRejectAuthority.placeCalls == 0);
    assert(serverRejectAuthority.cancelCalls == 0);
    serverReject.Stop();
    ::unlink(serverRejectPath.c_str());

    const std::string clientRejectPath = "/tmp/hepta-execution-peer-client-" +
        std::to_string(::getpid()) + ".sock";
    CountingExecutionAuthority clientRejectAuthority;
    UnixExecutionServiceServer normalServer(clientRejectAuthority);
    assert(normalServer.Start(clientRejectPath, std::set<std::uint32_t>{currentUid},
                              reason, 32768, 250));
    UnixExecutionServiceClient rejectingClient(clientRejectPath, 250, 32768,
                                                std::set<std::uint32_t>{wrongUid});
    const ExecutionCommandResult rejectedDaemon = rejectingClient.PlaceIbOrder(command);
    assert(rejectedDaemon.status == ExecutionCommandStatus::Uncertain);
    assert(rejectedDaemon.reasonCode == "EXECUTION_SERVICE_UNAVAILABLE");
    assert(rejectedDaemon.detail == "execution service peer uid rejected");
    assert(clientRejectAuthority.placeCalls == 0);
    assert(clientRejectAuthority.cancelCalls == 0);
    normalServer.Stop();
    ::unlink(clientRejectPath.c_str());
}

void TestProtocolRoundTripAndUnavailableFailUncertain()
{
    ExecutionServiceRequest request;
    request.operation = ExecutionServiceOperation::PlaceIbOrder;
    request.expectedServiceEpoch = "hexec-v6-protocol-test";
    request.expectedServiceFencingGeneration = 9;
    request.place.context = MakeSession(77, 9).executionContext;
    request.place.context.toolCallId = "protocol-round-trip";
    const TradingToolCall call = MakePlace("protocol-round-trip");
    request.place.contract = call.ibContract;
    request.place.order = call.ibOrder;
    request.place.instrument = call.instrument;
    request.place.timeInForce = call.timeInForce;
    request.place.referencePrice = call.referencePrice;
    request.place.expiresAtMs = call.expiresAtMs;
    request.place.order.orderRef = "protocol-order-ref";
    request.place.previewPermit =
        "sha256:0123456789abcdef0123456789abcdef"
        "0123456789abcdef0123456789abcdef";
    request.place.authoritativeQuoteBinding.valid = true;
    request.place.authoritativeQuoteBinding.instrument = "EUR.USD";
    request.place.authoritativeQuoteBinding.subscriptionId =
        "IB:41:7:1001";
    request.place.authoritativeQuoteBinding.bid = 1.1000;
    request.place.authoritativeQuoteBinding.ask = 1.1002;
    request.place.authoritativeQuoteBinding.observedAtMs = 1234;
    request.place.authoritativeQuoteBinding.staleAfterMs = 5678;
    std::string body;
    std::string reason;
    assert(ExecutionServiceProtocol::EncodeRequest(request, body, reason));
    assert(ExecutionServiceProtocol::ProtocolVersion() == 10);
    ExecutionServiceRequest decoded;
    assert(ExecutionServiceProtocol::DecodeRequest(body, decoded, reason));
    assert(decoded.place.context.decisionLeaseFencingToken == 0);
    assert(decoded.place.context.decisionLeaseGeneration == 0);
    assert(decoded.place.contract.currency == "USD");
    assert(decoded.place.order.totalQuantity == 1000.0);
    assert(decoded.place.order.orderRef == request.place.order.orderRef);
    assert(decoded.place.timeInForce == request.place.timeInForce);
    assert(decoded.place.previewPermit == request.place.previewPermit);
    // Privileged quote authorization is strictly process-local. An Agent or
    // wire peer cannot serialize a self-asserted final-send binding.
    assert(!decoded.place.authoritativeQuoteBinding.valid);
    assert(decoded.place.authoritativeQuoteBinding.instrument.empty());
    assert(decoded.place.authoritativeQuoteBinding.subscriptionId.empty());
    assert(decoded.place.authoritativeQuoteBinding.bid == 0.0);
    assert(decoded.place.authoritativeQuoteBinding.ask == 0.0);
    assert(decoded.place.authoritativeQuoteBinding.observedAtMs == 0);
    assert(decoded.place.authoritativeQuoteBinding.staleAfterMs == 0);
    assert(decoded.expectedServiceEpoch == request.expectedServiceEpoch);
    assert(decoded.expectedServiceFencingGeneration ==
           request.expectedServiceFencingGeneration);

    ExecutionServiceRequest oversizedRequest = request;
    oversizedRequest.place.order.orderRef.assign(4097, 'r');
    assert(ExecutionServiceProtocol::EncodeRequest(
        oversizedRequest, body, reason));
    assert(!ExecutionServiceProtocol::DecodeRequest(
        body, decoded, reason));
    assert(reason == "EXECUTION_PROTOCOL_INVALID_FIELD");

    ExecutionServiceRequest readRequest;
    readRequest.operation = ExecutionServiceOperation::ReadAuthoritativeState;
    readRequest.expectedServiceEpoch = request.expectedServiceEpoch;
    readRequest.expectedServiceFencingGeneration = 9;
    readRequest.read.context = request.place.context;
    readRequest.read.context.toolCallId = "protocol-authoritative-read";
    readRequest.read.query = "market.get_quote";
    readRequest.read.instrument = "EUR.USD";
    assert(ExecutionServiceProtocol::EncodeRequest(readRequest, body, reason));
    assert(ExecutionServiceProtocol::DecodeRequest(body, decoded, reason));
    assert(decoded.read.query == readRequest.read.query);
    assert(decoded.read.instrument == readRequest.read.instrument);

    ExecutionServiceRequest recoveryQuery;
    recoveryQuery.operation =
        ExecutionServiceOperation::RecoveryQueryCommandStatus;
    recoveryQuery.expectedServiceEpoch = request.expectedServiceEpoch;
    recoveryQuery.expectedServiceFencingGeneration =
        request.expectedServiceFencingGeneration;
    recoveryQuery.control.context = request.place.context;
    recoveryQuery.control.context.toolCallId =
        "protocol-root-recovery-query";
    recoveryQuery.control.targetCommandId = "protocol-round-trip";
    recoveryQuery.control.recoveryIngressFence = 17;
    assert(ExecutionServiceProtocol::EncodeRequest(
        recoveryQuery, body, reason));
    assert(ExecutionServiceProtocol::DecodeRequest(body, decoded, reason));
    assert(decoded.operation ==
        ExecutionServiceOperation::RecoveryQueryCommandStatus);
    assert(decoded.control.targetCommandId ==
        recoveryQuery.control.targetCommandId);
    assert(decoded.control.recoveryIngressFence == 17);

    ExecutionServiceRequest ownerAudit = recoveryQuery;
    ownerAudit.operation = ExecutionServiceOperation::RecoveryAuditOwner;
    ownerAudit.control.context.toolCallId =
        "protocol-recovery-owner-audit";
    ownerAudit.control.targetCommandId.clear();
    ownerAudit.control.recoveryIngressFence = 23;
    assert(ExecutionServiceProtocol::EncodeRequest(
        ownerAudit, body, reason));
    assert(ExecutionServiceProtocol::DecodeRequest(body, decoded, reason));
    assert(decoded.operation ==
        ExecutionServiceOperation::RecoveryAuditOwner);
    assert(decoded.control.context.agentId ==
        ownerAudit.control.context.agentId);
    assert(decoded.control.context.sessionId ==
        ownerAudit.control.context.sessionId);
    assert(decoded.control.context.account ==
        ownerAudit.control.context.account);
    assert(decoded.control.context.executionDomain ==
        ownerAudit.control.context.executionDomain);
    assert(decoded.control.targetCommandId.empty());
    assert(decoded.control.recoveryIngressFence == 23);

    recoveryQuery.control.recoveryIngressFence = 0;
    assert(ExecutionServiceProtocol::EncodeRequest(
        recoveryQuery, body, reason));
    assert(!ExecutionServiceProtocol::DecodeRequest(body, decoded, reason));
    assert(reason == "EXECUTION_PROTOCOL_INVALID_CONTROL");

    ExecutionServiceRequest previewRequest = request;
    previewRequest.operation = ExecutionServiceOperation::PreviewOrder;
    previewRequest.place.context.toolCallId = "protocol-preview-order";
    previewRequest.place.previewPermit.clear();
    assert(ExecutionServiceProtocol::EncodeRequest(
        previewRequest, body, reason));
    assert(ExecutionServiceProtocol::DecodeRequest(body, decoded, reason));
    assert(decoded.operation == ExecutionServiceOperation::PreviewOrder);
    assert(decoded.place.context.toolCallId ==
           previewRequest.place.context.toolCallId);
    assert(decoded.place.timeInForce == previewRequest.place.timeInForce);
    assert(decoded.place.order.orderRef ==
           previewRequest.place.order.orderRef);
    assert(decoded.place.previewPermit.empty());

    previewRequest.place.previewPermit = request.place.previewPermit;
    assert(ExecutionServiceProtocol::EncodeRequest(
        previewRequest, body, reason));
    assert(!ExecutionServiceProtocol::DecodeRequest(body, decoded, reason));
    assert(reason == "EXECUTION_PROTOCOL_INVALID_PLACE");

    ExecutionServiceRequest flattenRequest;
    flattenRequest.operation = ExecutionServiceOperation::FlattenPosition;
    flattenRequest.expectedServiceEpoch = request.expectedServiceEpoch;
    flattenRequest.expectedServiceFencingGeneration =
        request.expectedServiceFencingGeneration;
    flattenRequest.flatten.context = request.place.context;
    flattenRequest.flatten.context.toolCallId =
        "protocol-flatten-mutation";
    flattenRequest.flatten.contract = request.place.contract;
    flattenRequest.flatten.instrument = request.place.instrument;
    flattenRequest.flatten.previewPermit = request.place.previewPermit;
    assert(ExecutionServiceProtocol::EncodeRequest(
        flattenRequest, body, reason));
    assert(ExecutionServiceProtocol::DecodeRequest(body, decoded, reason));
    assert(decoded.operation == ExecutionServiceOperation::FlattenPosition);
    assert(decoded.flatten.instrument ==
           flattenRequest.flatten.instrument);
    assert(decoded.flatten.contract.symbol ==
           flattenRequest.flatten.contract.symbol);
    assert(decoded.flatten.previewPermit ==
           flattenRequest.flatten.previewPermit);

    ExecutionServiceRequest flattenPreview = flattenRequest;
    flattenPreview.operation =
        ExecutionServiceOperation::PreviewFlattenPosition;
    flattenPreview.flatten.context.toolCallId =
        "protocol-flatten-preview";
    flattenPreview.flatten.previewPermit.clear();
    assert(ExecutionServiceProtocol::EncodeRequest(
        flattenPreview, body, reason));
    assert(ExecutionServiceProtocol::DecodeRequest(body, decoded, reason));
    assert(decoded.operation ==
           ExecutionServiceOperation::PreviewFlattenPosition);
    assert(decoded.flatten.previewPermit.empty());
    flattenPreview.flatten.previewPermit =
        request.place.previewPermit;
    assert(ExecutionServiceProtocol::EncodeRequest(
        flattenPreview, body, reason));
    assert(!ExecutionServiceProtocol::DecodeRequest(
        body, decoded, reason));
    assert(reason == "EXECUTION_PROTOCOL_INVALID_FLATTEN");

    std::string requestWithUnknownField = body;
    requestWithUnknownField.push_back(static_cast<char>(0xfd));
    requestWithUnknownField.push_back(static_cast<char>(0xe8));
    requestWithUnknownField.push_back('\0');
    requestWithUnknownField.push_back('\0');
    requestWithUnknownField.push_back('\0');
    requestWithUnknownField.push_back('\1');
    requestWithUnknownField.push_back('x');
    assert(!ExecutionServiceProtocol::DecodeRequest(
        requestWithUnknownField, decoded, reason));
    assert(reason == "EXECUTION_PROTOCOL_FIELD_SET_MISMATCH");

    ExecutionServiceRequest missingGeneration = request;
    missingGeneration.expectedServiceFencingGeneration = 0;
    assert(!ExecutionServiceProtocol::EncodeRequest(
        missingGeneration, body, reason));
    assert(reason == "EXECUTION_PROTOCOL_SERVICE_EPOCH_REQUIRED");

    ExecutionCommandResult response;
    response.status = ExecutionCommandStatus::Accepted;
    response.commandId = request.place.context.toolCallId;
    response.orderId = 1000000;
    response.serviceEpoch = request.expectedServiceEpoch;
    response.serviceFencingGeneration = request.expectedServiceFencingGeneration;
    assert(ExecutionServiceProtocol::EncodeResponse(response, body, reason));
    ExecutionCommandResult decodedResponse;
    assert(ExecutionServiceProtocol::DecodeResponse(body, decodedResponse, reason));
    assert(decodedResponse.serviceEpoch == response.serviceEpoch);
    assert(decodedResponse.serviceFencingGeneration ==
           response.serviceFencingGeneration);

    ExecutionControlResult controlResponse;
    controlResponse.status = ExecutionCommandStatus::Accepted;
    controlResponse.commandId = "protocol-recovery-owner-audit";
    controlResponse.affectedCount = 3;
    controlResponse.mutationBlocked = true;
    controlResponse.ownerAuditAuthoritative = true;
    controlResponse.ownerAuditComplete = true;
    controlResponse.ownerActiveOrderCount = 3;
    controlResponse.ownerUncertainCommandCount = 0;
    controlResponse.brokerConnectionEpoch = 41;
    controlResponse.brokerActiveGeneration = 7;
    controlResponse.brokerTerminalGeneration = 11;
    controlResponse.brokerRiskGeneration = 13;
    controlResponse.brokerAccountGeneration = 12;
    controlResponse.brokerPositionGeneration = 13;
    controlResponse.brokerFxCashGeneration = 12;
    controlResponse.brokerExposureGeneration = 5;
    controlResponse.brokerTerminalExposureGeneration = 5;
    controlResponse.brokerRiskAbsorbedExposureGeneration = 5;
    controlResponse.brokerGlobalActiveOrderCount = 0;
    controlResponse.brokerPostFillRiskReconciliationPending = false;
    controlResponse.brokerRecoveryAuditBarrierComplete = true;
    controlResponse.brokerRecoveryAuditNewConnectionEpochRequired = false;
    controlResponse.brokerPositionQuantity = "0";
    controlResponse.brokerGrossAbsolutePosition = "0";
    controlResponse.ownerAccount = "DU123456";
    controlResponse.ownerExecutionDomain = "PAPER:alpha";
    controlResponse.reasonCode = "RECOVERY_OWNER_ACTIVE_ORDERS";
    controlResponse.serviceEpoch = request.expectedServiceEpoch;
    controlResponse.serviceFencingGeneration =
        request.expectedServiceFencingGeneration;
    assert(ExecutionServiceProtocol::EncodeControlResponse(
        controlResponse, body, reason));
    ExecutionControlResult decodedControlResponse;
    assert(ExecutionServiceProtocol::DecodeControlResponse(
        body, decodedControlResponse, reason));
    assert(decodedControlResponse.status ==
        ExecutionCommandStatus::Accepted);
    assert(decodedControlResponse.commandId == controlResponse.commandId);
    assert(decodedControlResponse.affectedCount == 3);
    assert(decodedControlResponse.mutationBlocked);
    assert(decodedControlResponse.ownerAuditAuthoritative);
    assert(decodedControlResponse.ownerAuditComplete);
    assert(decodedControlResponse.ownerActiveOrderCount == 3);
    assert(decodedControlResponse.ownerUncertainCommandCount == 0);
    assert(decodedControlResponse.brokerConnectionEpoch == 41);
    assert(decodedControlResponse.brokerActiveGeneration == 7);
    assert(decodedControlResponse.brokerTerminalGeneration == 11);
    assert(decodedControlResponse.brokerRiskGeneration == 13);
    assert(decodedControlResponse.brokerAccountGeneration == 12);
    assert(decodedControlResponse.brokerPositionGeneration == 13);
    assert(decodedControlResponse.brokerFxCashGeneration == 12);
    assert(decodedControlResponse.brokerExposureGeneration == 5);
    assert(decodedControlResponse.brokerTerminalExposureGeneration == 5);
    assert(decodedControlResponse.brokerRiskAbsorbedExposureGeneration == 5);
    assert(decodedControlResponse.brokerGlobalActiveOrderCount == 0);
    assert(!decodedControlResponse.brokerPostFillRiskReconciliationPending);
    assert(decodedControlResponse.brokerRecoveryAuditBarrierComplete);
    assert(!decodedControlResponse.brokerRecoveryAuditNewConnectionEpochRequired);
    assert(decodedControlResponse.brokerPositionQuantity == "0");
    assert(decodedControlResponse.brokerGrossAbsolutePosition == "0");
    assert(decodedControlResponse.ownerAccount == "DU123456");
    assert(decodedControlResponse.ownerExecutionDomain == "PAPER:alpha");
    assert(decodedControlResponse.reasonCode ==
        "RECOVERY_OWNER_ACTIVE_ORDERS");
    assert(decodedControlResponse.serviceEpoch ==
        controlResponse.serviceEpoch);
    assert(decodedControlResponse.serviceFencingGeneration ==
           controlResponse.serviceFencingGeneration);

    ExecutionControlResult nonCanonicalControl = controlResponse;
    nonCanonicalControl.brokerPositionQuantity = "1e0";
    assert(ExecutionServiceProtocol::EncodeControlResponse(
        nonCanonicalControl, body, reason));
    assert(!ExecutionServiceProtocol::DecodeControlResponse(
        body, decodedControlResponse, reason));
    assert(reason == "EXECUTION_PROTOCOL_INVALID_CONTROL_RESPONSE");
    nonCanonicalControl = controlResponse;
    nonCanonicalControl.brokerGrossAbsolutePosition = "-0";
    assert(ExecutionServiceProtocol::EncodeControlResponse(
        nonCanonicalControl, body, reason));
    assert(!ExecutionServiceProtocol::DecodeControlResponse(
        body, decodedControlResponse, reason));
    assert(reason == "EXECUTION_PROTOCOL_INVALID_CONTROL_RESPONSE");

    // Authoritative reads such as orders.list legitimately accumulate more
    // than 4096 bytes of owner-scoped recent-order evidence.  Keep request
    // fields at the strict 4096-byte boundary while allowing a bounded result
    // detail that still fits the configured execution response frame.
    response.detail.assign(8192, 'x');
    assert(ExecutionServiceProtocol::EncodeResponse(response, body, reason));
    assert(ExecutionServiceProtocol::DecodeResponse(
        body, decodedResponse, reason));
    assert(decodedResponse.detail == response.detail);

    response.detail.assign(32769, 'x');
    assert(ExecutionServiceProtocol::EncodeResponse(response, body, reason));
    assert(!ExecutionServiceProtocol::DecodeResponse(
        body, decodedResponse, reason));
    assert(reason == "EXECUTION_PROTOCOL_INVALID_FIELD");
    response.detail.clear();
    assert(ExecutionServiceProtocol::EncodeResponse(response, body, reason));

    std::string responseWithUnknownField = body;
    responseWithUnknownField.push_back(static_cast<char>(0xfd));
    responseWithUnknownField.push_back(static_cast<char>(0xe8));
    responseWithUnknownField.push_back('\0');
    responseWithUnknownField.push_back('\0');
    responseWithUnknownField.push_back('\0');
    responseWithUnknownField.push_back('\1');
    responseWithUnknownField.push_back('x');
    assert(!ExecutionServiceProtocol::DecodeResponse(
        responseWithUnknownField, decodedResponse, reason));
    assert(reason == "EXECUTION_PROTOCOL_FIELD_SET_MISMATCH");

    assert(ExecutionServiceProtocol::EncodeRequest(request, body, reason));
    std::string corrupt = body;
    corrupt[0] = 'X';
    assert(!ExecutionServiceProtocol::DecodeRequest(corrupt, decoded, reason));
    assert(reason == "EXECUTION_PROTOCOL_BAD_MAGIC");

    UnixExecutionServiceClient unavailable("/tmp/hepta-execution-definitely-absent.sock", 25);
    const ExecutionCommandResult unavailableResult = unavailable.PlaceIbOrder(request.place);
    assert(unavailableResult.status == ExecutionCommandStatus::Uncertain);
    assert(unavailableResult.reasonCode == "EXECUTION_SERVICE_UNAVAILABLE");
}

void TestLargeAuthoritativeReadResponseThroughUnixTransport()
{
    const std::string socketPath =
        "/tmp/hepta-execution-large-read-" + std::to_string(::getpid()) +
        ".sock";
    ::unlink(socketPath.c_str());
    ::unlink((socketPath + ".lock").c_str());

    std::string orders =
        "{\"source\":\"IB\",\"authoritative\":true,"
        "\"active_order_ids\":[],\"recent_orders\":[";
    for (int index = 0; index < 13; ++index)
    {
        if (index != 0) orders += ',';
        orders +=
            "{\"order_id\":" + std::to_string(69 + index) +
            ",\"status\":\"Filled\",\"terminal\":true,"
            "\"economic_fill\":true,\"filled_quantity\":25000,"
            "\"remaining_quantity\":0,\"average_fill_price\":1.15366,"
            "\"reason_code\":\"\",\"observed_at_ms\":" +
            std::to_string(1786471799000LL + index) +
            ",\"evidence_service_epoch\":"
            "\"hexec-v6-0123456789abcdef0123456789abcdef\","
            "\"evidence_connection_epoch\":1,"
            "\"instrument\":\"EUR.USD\",\"side\":\"SELL\"}";
    }
    orders += "]}";
    assert(orders.size() > 4096);
    assert(orders.size() < 32768);

    CountingExecutionAuthority authority;
    authority.authoritativeReadDetail = orders;
    UnixExecutionServiceServer server(authority, &authority);
    std::string reason;
    assert(server.Start(socketPath,
        std::set<std::uint32_t>{
            static_cast<std::uint32_t>(::geteuid())},
        reason, 32768, 1000));
    UnixExecutionServiceClient client(socketPath, 1000, 32768);

    ExecutionReadCommand read;
    read.context = MakeSession(77, 9).executionContext;
    read.context.toolCallId = "large-orders-list-read";
    read.query = "orders.list";
    const ExecutionCommandResult result =
        client.ReadAuthoritativeState(read);
    assert(result.status == ExecutionCommandStatus::Accepted);
    assert(result.commandId == read.context.toolCallId);
    assert(result.detail == orders);
    assert(result.detail.find("\"order_id\":81") != std::string::npos);

    server.Stop();
    ::unlink(socketPath.c_str());
    ::unlink((socketPath + ".lock").c_str());
}

void TestServiceFencingGenerationRejectedBeforeAuthority()
{
    const std::string socketPath = "/tmp/hepta-execution-service-generation-" +
        std::to_string(::getpid()) + ".sock";
    ::unlink(socketPath.c_str());
    ::unlink((socketPath + ".lock").c_str());

    CountingExecutionAuthority authority;
    UnixExecutionServiceServer server(authority, &authority);
    std::string reason;
    assert(server.Start(socketPath,
        std::set<std::uint32_t>{static_cast<std::uint32_t>(::geteuid())},
        reason, 32768, 1000));
    const ExecutionServiceIdentity identity = server.ServiceIdentity();
    assert(!identity.serviceEpoch.empty());
    assert(identity.serviceFencingGeneration != 0);

    TradingToolSession session = MakeSession(77, 9);
    session.executionContext.toolCallId = "same-epoch-wrong-service-generation";
    ExecutionServiceRequest request;
    request.operation = ExecutionServiceOperation::PlaceIbOrder;
    request.place = MakePlaceCommand(session, MakePlace(
        session.executionContext.toolCallId));
    request.expectedServiceEpoch = identity.serviceEpoch;
    request.expectedServiceFencingGeneration =
        identity.serviceFencingGeneration + 1;
    assert(request.expectedServiceFencingGeneration != 0);

    std::string requestBody;
    assert(ExecutionServiceProtocol::EncodeRequest(request, requestBody, reason));
    ExecutionCommandResult rejected;
    assert(ExecutionServiceProtocol::DecodeResponse(
        ExchangeFrame(socketPath, requestBody), rejected, reason));
    assert(rejected.status == ExecutionCommandStatus::Rejected);
    assert(rejected.commandId == request.place.context.toolCallId);
    assert(rejected.reasonCode == "EXECUTION_SERVICE_EPOCH_MISMATCH");
    assert(rejected.serviceEpoch == identity.serviceEpoch);
    assert(rejected.serviceFencingGeneration ==
           identity.serviceFencingGeneration);
    assert(authority.placeCalls == 0);
    assert(authority.cancelCalls == 0);

    UnixExecutionServiceClient client(socketPath, 1000);
    const IbPlaceOrderCommand authorized =
        Previewed(client, request.place);
    const ExecutionCommandResult accepted =
        client.PlaceIbOrder(authorized);
    assert(accepted.status == ExecutionCommandStatus::Accepted);
    assert(accepted.serviceEpoch == identity.serviceEpoch);
    assert(accepted.serviceFencingGeneration ==
           identity.serviceFencingGeneration);
    assert(authority.placeCalls == 1);
    assert(authority.previewCalls == 1);

    server.Stop();
    ::unlink(socketPath.c_str());
    ::unlink((socketPath + ".lock").c_str());
}

void TestExecutionPreviewPermitBindingAndSingleUse()
{
    const std::string socketPath =
        "/tmp/hepta-execution-preview-permit-" +
        std::to_string(::getpid()) + ".sock";
    ::unlink(socketPath.c_str());
    ::unlink((socketPath + ".lock").c_str());

    CountingExecutionAuthority authority;
    UnixExecutionServiceServer server(authority, &authority);
    std::string reason;
    assert(server.Start(socketPath,
        std::set<std::uint32_t>{
            static_cast<std::uint32_t>(::geteuid())},
        reason, 32768, 1000));
    UnixExecutionServiceClient client(socketPath, 1000);

    TradingToolSession session = MakeSession(77, 9);
    session.executionContext.toolCallId = "preview-permit-base";
    IbPlaceOrderCommand base =
        MakePlaceCommand(session, MakePlace("preview-permit-base"));
    base.order.orderRef = "agent-order-ref";

    const ExecutionCommandResult missing =
        client.PlaceIbOrder(base);
    assert(missing.status == ExecutionCommandStatus::Rejected);
    assert(missing.reasonCode ==
           "EXECUTION_PREVIEW_PERMIT_UNKNOWN_OR_CONSUMED");
    assert(authority.placeCalls == 0);

    const std::function<void(IbPlaceOrderCommand&)> mismatches[] = {
        [](IbPlaceOrderCommand& command) {
            command.context.account = "OTHER";
        },
        [](IbPlaceOrderCommand& command) {
            command.order.orderRef = "changed-order-ref";
        },
        [](IbPlaceOrderCommand& command) {
            command.timeInForce = "GTC";
        },
        [](IbPlaceOrderCommand& command) {
            command.referencePrice += 0.0001;
        },
        [](IbPlaceOrderCommand& command) {
            command.expiresAtMs += 1;
        }
    };
    for (std::size_t i = 0;
         i < sizeof(mismatches) / sizeof(mismatches[0]); ++i)
    {
        IbPlaceOrderCommand authorized = Previewed(client, base);
        IbPlaceOrderCommand changed = authorized;
        mismatches[i](changed);
        const ExecutionCommandResult mismatch =
            client.PlaceIbOrder(changed);
        assert(mismatch.status == ExecutionCommandStatus::Rejected);
        assert(mismatch.reasonCode ==
               "EXECUTION_PREVIEW_PERMIT_ORDER_MISMATCH");
        const ExecutionCommandResult consumed =
            client.PlaceIbOrder(authorized);
        assert(consumed.status == ExecutionCommandStatus::Rejected);
        assert(consumed.reasonCode ==
               "EXECUTION_PREVIEW_PERMIT_UNKNOWN_OR_CONSUMED");
        assert(authority.placeCalls == 0);
    }

    IbPlaceOrderCommand expiring = base;
    expiring.context.toolCallId = "preview-expiry-read";
    expiring.order.orderRef = "preview-expiry-ref";
    expiring.expiresAtMs = OmsJournal::NowEpochMs() + 100;
    expiring = Previewed(client, expiring);
    std::this_thread::sleep_for(std::chrono::milliseconds(150));
    const ExecutionCommandResult expired =
        client.PlaceIbOrder(expiring);
    assert(expired.status == ExecutionCommandStatus::Rejected);
    assert(expired.reasonCode == "EXECUTION_PREVIEW_PERMIT_EXPIRED");

    const std::string previewReadId = base.context.toolCallId;
    IbPlaceOrderCommand authorized = Previewed(client, base);
    assert(authorized.context.toolCallId != previewReadId);
    assert(authorized.context.toolCallId.find("hexec-command-") == 0);
    IbPlaceOrderCommand changedCommandId = authorized;
    changedCommandId.context.toolCallId = "preview-command-mismatch";
    const ExecutionCommandResult commandMismatch =
        client.PlaceIbOrder(changedCommandId);
    assert(commandMismatch.status == ExecutionCommandStatus::Rejected);
    assert(commandMismatch.reasonCode ==
           "EXECUTION_PREVIEW_PERMIT_COMMAND_ID_MISMATCH");
    assert(client.PlaceIbOrder(authorized).reasonCode ==
           "EXECUTION_PREVIEW_PERMIT_UNKNOWN_OR_CONSUMED");

    authorized = Previewed(client, base);
    authorized.context.decisionLeaseFencingToken += 100;
    authorized.context.decisionLeaseGeneration += 100;
    const ExecutionCommandResult accepted =
        client.PlaceIbOrder(authorized);
    assert(accepted.status == ExecutionCommandStatus::Accepted);
    assert(accepted.commandId == authorized.context.toolCallId);
    assert(authority.placeCalls == 1);

    const ExecutionCommandResult replayedPermit =
        client.PlaceIbOrder(authorized);
    assert(replayedPermit.status == ExecutionCommandStatus::Rejected);
    assert(replayedPermit.reasonCode ==
           "EXECUTION_PREVIEW_PERMIT_UNKNOWN_OR_CONSUMED");
    assert(authority.placeCalls == 1);

    std::vector<IbPlaceOrderCommand> outstanding;
    for (int i = 0; i < 8; ++i)
    {
        IbPlaceOrderCommand candidate = base;
        candidate.context.toolCallId =
            "preview-owner-capacity-" + std::to_string(i);
        candidate.order.orderRef =
            "preview-owner-capacity-ref-" + std::to_string(i);
        outstanding.push_back(Previewed(client, candidate));
    }
    IbPlaceOrderCommand overflow = base;
    overflow.context.toolCallId = "preview-owner-capacity-overflow";
    overflow.order.orderRef = "preview-owner-capacity-overflow-ref";
    const ExecutionCommandResult capacity =
        client.PreviewOrder(overflow);
    assert(capacity.status == ExecutionCommandStatus::Rejected);
    assert(capacity.reasonCode ==
           "EXECUTION_PREVIEW_PERMIT_OWNER_CAPACITY_EXCEEDED");

    IbPlaceOrderCommand replacementInput = outstanding.front();
    replacementInput.previewPermit.clear();
    const ExecutionCommandResult replacementPreview =
        client.PreviewOrder(replacementInput);
    assert(replacementPreview.status == ExecutionCommandStatus::Accepted);
    const std::string replacementPermit =
        PreviewField(replacementPreview, "preview_permit");
    const std::string replacementCommandId =
        PreviewField(replacementPreview, "command_id");
    assert(replacementPermit != outstanding.front().previewPermit);
    const ExecutionCommandResult superseded =
        client.PlaceIbOrder(outstanding.front());
    assert(superseded.status == ExecutionCommandStatus::Rejected);
    assert(superseded.reasonCode ==
           "EXECUTION_PREVIEW_PERMIT_UNKNOWN_OR_CONSUMED");
    outstanding.front().previewPermit = replacementPermit;
    outstanding.front().context.toolCallId = replacementCommandId;
    assert(client.PlaceIbOrder(outstanding.front()).status ==
           ExecutionCommandStatus::Accepted);
    assert(authority.placeCalls == 2);

    overflow = Previewed(client, overflow);
    assert(!overflow.previewPermit.empty());

    server.Stop();
    ::unlink(socketPath.c_str());
    ::unlink((socketPath + ".lock").c_str());
}

void TestExecutionFlattenPermitBindingAndSingleUse()
{
    const std::string socketPath =
        "/tmp/hepta-execution-flatten-permit-" +
        std::to_string(::getpid()) + ".sock";
    ::unlink(socketPath.c_str());
    ::unlink((socketPath + ".lock").c_str());

    CountingExecutionAuthority authority;
    UnixExecutionServiceServer server(authority, &authority);
    std::string reason;
    assert(server.Start(socketPath,
        std::set<std::uint32_t>{
            static_cast<std::uint32_t>(::geteuid())},
        reason, 32768, 1000));
    UnixExecutionServiceClient client(socketPath, 1000);

    TradingToolSession session = MakeSession(77, 9);
    session.executionContext.toolCallId = "flatten-preview-base";
    const TradingToolCall call = MakePlace("flatten-preview-base");
    const FlattenPositionCommand base =
        MakeFlattenCommand(session, call);

    const ExecutionCommandResult missing =
        client.FlattenPosition(base);
    assert(missing.status == ExecutionCommandStatus::Rejected);
    assert(missing.reasonCode ==
           "EXECUTION_PREVIEW_PERMIT_UNKNOWN_OR_CONSUMED");
    assert(authority.flattenCalls == 0);

    FlattenPositionCommand authorized =
        PreviewedFlatten(client, base);
    FlattenPositionCommand changed = authorized;
    changed.contract.exchange = "SMART";
    const ExecutionCommandResult mismatch =
        client.FlattenPosition(changed);
    assert(mismatch.status == ExecutionCommandStatus::Rejected);
    assert(mismatch.reasonCode ==
           "EXECUTION_PREVIEW_PERMIT_ORDER_MISMATCH");
    assert(client.FlattenPosition(authorized).reasonCode ==
           "EXECUTION_PREVIEW_PERMIT_UNKNOWN_OR_CONSUMED");
    assert(authority.flattenCalls == 0);

    authorized = PreviewedFlatten(client, base);
    FlattenPositionCommand changedCommandId = authorized;
    changedCommandId.context.toolCallId =
        "flatten-command-id-mismatch";
    assert(client.FlattenPosition(changedCommandId).reasonCode ==
           "EXECUTION_PREVIEW_PERMIT_COMMAND_ID_MISMATCH");
    assert(client.FlattenPosition(authorized).reasonCode ==
           "EXECUTION_PREVIEW_PERMIT_UNKNOWN_OR_CONSUMED");

    authorized = PreviewedFlatten(client, base);
    authorized.context.decisionLeaseFencingToken = 999;
    authorized.context.decisionLeaseGeneration = 999;
    authorized.hasAuthoritativePreviewSnapshot = true;
    authorized.previewPositionQuantity = -999.0;
    authorized.previewPositionConnectionEpoch = 999;
    authorized.previewPositionGeneration = 999;
    authorized.authoritativePreviewPlanBinding =
        "attacker-selected-plan-binding";
    const ExecutionCommandResult accepted =
        client.FlattenPosition(authorized);
    assert(accepted.status == ExecutionCommandStatus::Accepted);
    assert(accepted.commandId == authorized.context.toolCallId);
    assert(accepted.orderId == 7002);
    assert(authority.flattenCalls == 1);
    assert(authority.flattenPreviewCalls == 3);
    assert(authority.lastFlatten.hasAuthoritativePreviewSnapshot);
    assert(authority.lastFlatten.previewPositionQuantity == 100.0);
    assert(authority.lastFlatten.previewPositionConnectionEpoch == 77);
    assert(authority.lastFlatten.previewPositionGeneration == 9);
    assert(authority.lastFlatten.authoritativePreviewPlanBinding ==
           "counting-authority-plan-binding");

    const ExecutionCommandResult replayed =
        client.FlattenPosition(authorized);
    assert(replayed.status == ExecutionCommandStatus::Rejected);
    assert(replayed.reasonCode ==
           "EXECUTION_PREVIEW_PERMIT_UNKNOWN_OR_CONSUMED");
    assert(authority.flattenCalls == 1);

    server.Stop();
    ::unlink(socketPath.c_str());
    ::unlink((socketPath + ".lock").c_str());
}

void TestIdentityCompareAndInvalidatePreservesNewCache()
{
    const std::string socketPath = "/tmp/hepta-execution-identity-cache-" +
        std::to_string(::getpid()) + ".sock";
    ::unlink(socketPath.c_str());
    ::unlink((socketPath + ".lock").c_str());
    const std::set<std::uint32_t> uid{
        static_cast<std::uint32_t>(::geteuid())};
    CountingExecutionAuthority authority;
    UnixExecutionServiceClient client(socketPath, 1000);
    std::string reason;

    ExecutionServiceIdentity firstIdentity;
    {
        UnixExecutionServiceServer first(authority);
        assert(first.Start(socketPath, uid, reason, 32768, 1000));
        assert(client.GetServiceIdentity(firstIdentity, reason));
        assert(firstIdentity.serviceEpoch == first.ServiceIdentity().serviceEpoch);
        assert(firstIdentity.serviceFencingGeneration ==
               first.ServiceIdentity().serviceFencingGeneration);
        first.Stop();
    }

    ExecutionServiceIdentity secondIdentity;
    {
        UnixExecutionServiceServer second(authority);
        assert(second.Start(socketPath, uid, reason, 32768, 1000));
        client.InvalidateServiceIdentity(firstIdentity);
        assert(client.GetServiceIdentity(secondIdentity, reason));
        assert(secondIdentity.serviceEpoch == second.ServiceIdentity().serviceEpoch);
        assert(secondIdentity.serviceEpoch != firstIdentity.serviceEpoch);

        // A delayed compare-and-invalidate for the first daemon must not erase
        // the already-cached identity of the second daemon.
        client.InvalidateServiceIdentity(firstIdentity);
        second.Stop();
    }

    ExecutionServiceIdentity cachedAfterStaleInvalidation;
    assert(client.GetServiceIdentity(cachedAfterStaleInvalidation, reason));
    assert(cachedAfterStaleInvalidation.serviceEpoch == secondIdentity.serviceEpoch);
    assert(cachedAfterStaleInvalidation.serviceFencingGeneration ==
           secondIdentity.serviceFencingGeneration);

    ::unlink(socketPath.c_str());
    ::unlink((socketPath + ".lock").c_str());
}

void TestMismatchedResponseCommandIdIsUncertain()
{
    const std::string socketPath = "/tmp/hepta-execution-mismatch-" +
        std::to_string(::getpid()) + ".sock";
    ::unlink(socketPath.c_str());
    const pid_t responderPid = SpawnMismatchedCommandIdResponder(socketPath);

    UnixExecutionServiceClient client(socketPath, 1000);
    TradingToolSession session = MakeSession(77, 9);
    session.executionContext.toolCallId = "expected-command-id";
    const IbPlaceOrderCommand command = MakePlaceCommand(session, MakePlace("expected-command-id"));
    const ExecutionCommandResult result = client.PlaceIbOrder(command);
    assert(result.status == ExecutionCommandStatus::Uncertain);
    assert(result.commandId == "expected-command-id");

    int status = 0;
    assert(::waitpid(responderPid, &status, 0) == responderPid);
    assert(WIFEXITED(status));
    assert(WEXITSTATUS(status) == 0);
    ::unlink(socketPath.c_str());
}

void TestInFlightCrashMatrix(const char* self)
{
    struct CrashCase
    {
        const char* mode;
        int exitCode;
        int expectedIntents;
        int expectedPlaceReceipts;
        int expectedVenueSends;
        bool restartAccepts;
        bool restartDuplicates;
    };
    const CrashCase cases[] = {
        {"before_dispatch", 80, 0, 0, 0, true, false},
        {"before_send", 81, 1, 0, 0, false, false},
        {"after_venue_send", 82, 1, 0, 1, false, false},
        {"after_receipt", 83, 1, 1, 1, false, true}
    };

    for (std::size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i)
    {
        const std::string suffix = std::to_string(::getpid()) + "-" + cases[i].mode;
        const std::string socketPath = "/tmp/hepta-execution-inflight-" + suffix + ".sock";
        const std::string journalPath = "/tmp/hepta-execution-inflight-" + suffix + ".jsonl";
        const std::string venueLedgerPath = "/tmp/hepta-execution-inflight-" + suffix + ".venue";
        ::unlink(socketPath.c_str());
        ::unlink((socketPath + ".lock").c_str());
        ::unlink(journalPath.c_str());
        ::unlink(venueLedgerPath.c_str());

        const pid_t faultPid = SpawnFaultServer(
            self, socketPath, journalPath, venueLedgerPath, cases[i].mode);
        UnixExecutionServiceClient client(socketPath, kDurableProcessE2eIoTimeoutMs);
        TradingToolSession session = MakeSession(77, 9);
        session.executionContext.toolCallId = std::string("inflight-") + cases[i].mode;
        const IbPlaceOrderCommand command = Previewed(client, MakePlaceCommand(
            session, MakePlace(session.executionContext.toolCallId)));
        const ExecutionCommandResult uncertain = client.PlaceIbOrder(command);
        assert(uncertain.status == ExecutionCommandStatus::Uncertain);
        assert(uncertain.commandId == command.context.toolCallId);
        WaitForInjectedCrash(faultPid, cases[i].exitCode);

        assert(CountJournalEvents(journalPath, "order_intent") == cases[i].expectedIntents);
        assert(CountJournalEvents(journalPath, "place_sent") == cases[i].expectedPlaceReceipts);
        assert(CountDurableVenueSends(venueLedgerPath) == cases[i].expectedVenueSends);

        const pid_t restarted = SpawnFaultServer(
            self, socketPath, journalPath, venueLedgerPath, "record_only");
        const ExecutionCommandResult oldEpoch = client.PlaceIbOrder(command);
        assert(oldEpoch.status == ExecutionCommandStatus::Rejected);
        assert(oldEpoch.reasonCode == "EXECUTION_SERVICE_EPOCH_MISMATCH");
        // Only the pre-dispatch crash has no durable command record and may
        // obtain a new permit/Execution-issued command id.  Durable uncertain
        // and accepted records must be queried by retrying the exact original
        // command id; issuing a new preview would intentionally create a
        // different mutation identity.
        const IbPlaceOrderCommand retryCommand = cases[i].restartAccepts ?
            Previewed(client, command) : command;
        const ExecutionCommandResult retry =
            client.PlaceIbOrder(retryCommand);
        if (cases[i].restartAccepts)
        {
            if (retry.status != ExecutionCommandStatus::Accepted)
                std::cerr << "before-dispatch retry did not receive its durable response: status="
                          << static_cast<int>(retry.status) << " reason=" << retry.reasonCode
                          << " detail=" << retry.detail << '\n';
            assert(retry.status == ExecutionCommandStatus::Accepted);
            assert(CountDurableVenueSends(venueLedgerPath) ==
                   cases[i].expectedVenueSends + 1);
        }
        else if (cases[i].restartDuplicates)
        {
            assert(retry.status == ExecutionCommandStatus::Duplicate);
            assert(retry.orderId == 1000000);
        }
        else
        {
            if (retry.status != ExecutionCommandStatus::Uncertain)
                std::cerr << "in-flight retry mode=" << cases[i].mode
                          << " status=" << static_cast<int>(retry.status)
                          << " reason=" << retry.reasonCode
                          << " detail=" << retry.detail << '\n';
            assert(retry.status == ExecutionCommandStatus::Uncertain);
            IbPlaceOrderCommand fresh = command;
            fresh.context.toolCallId += "-fresh";
            fresh.previewPermit.clear();
            fresh = Previewed(client, fresh);
            const ExecutionCommandResult blocked =
                client.PlaceIbOrder(fresh);
            assert(blocked.status == ExecutionCommandStatus::Rejected);
            assert(blocked.reasonCode == "MUTATION_BLOCKED");
        }
        ExecutionControlCommand statusQuery;
        statusQuery.context = command.context;
        statusQuery.context.toolCallId = std::string("status-") + cases[i].mode;
        statusQuery.targetCommandId = cases[i].restartAccepts ?
            retryCommand.context.toolCallId : command.context.toolCallId;
        const ExecutionControlResult status = client.QueryCommandStatus(statusQuery);
        assert(status.status == ExecutionCommandStatus::Accepted);
        const ExecutionCommandStatus expectedStatus =
            (cases[i].restartAccepts || cases[i].restartDuplicates) ?
                ExecutionCommandStatus::Accepted : ExecutionCommandStatus::Uncertain;
        assert(status.targetStatus == expectedStatus);
        assert(status.mutationBlocked ==
               (!cases[i].restartAccepts && !cases[i].restartDuplicates));
        assert(CountDurableVenueSends(venueLedgerPath) ==
               cases[i].expectedVenueSends + (cases[i].restartAccepts ? 1 : 0));
        KillServer(restarted);

        ::unlink(socketPath.c_str());
        ::unlink((socketPath + ".lock").c_str());
        ::unlink(journalPath.c_str());
        ::unlink(venueLedgerPath.c_str());
    }
}

void TestServiceLeaseAcrossInstrumentsGatewayRestartAndSessionRotate(
    const char* self)
{
    const std::string suffix = std::to_string(::getpid());
    const std::string socketPath =
        "/tmp/hepta-execution-lease-rotation-" + suffix + ".sock";
    const std::string journalPath =
        "/tmp/hepta-execution-lease-rotation-" + suffix + ".jsonl";
    ::unlink(socketPath.c_str());
    ::unlink((socketPath + ".lock").c_str());
    ::unlink(journalPath.c_str());

    const pid_t serverPid = SpawnServer(
        self, socketPath, journalPath, 7001, 31, false);
    {
        UnixExecutionServiceClient firstGateway(
            socketPath, kDurableProcessE2eIoTimeoutMs);
        TradingToolSession session = MakeSession(101, 3);
        session.executionContext.toolCallId = "lease-rotation-eur-1";
        const ExecutionCommandResult first = firstGateway.PlaceIbOrder(
            Previewed(firstGateway, MakePlaceCommand(
                session, MakePlace("lease-rotation-eur-1"))));
        assert(first.status == ExecutionCommandStatus::Accepted);
        assert(first.orderId == 1000000);
    }

    UnixExecutionServiceClient restartedGateway(
        socketPath, kDurableProcessE2eIoTimeoutMs);
    TradingToolSession rotatedSession = MakeSession(9001, 77);
    rotatedSession.executionContext.toolCallId = "lease-rotation-gbp";
    TradingToolCall gbp = MakePlace("lease-rotation-gbp");
    gbp.instrument = "GBP.USD";
    gbp.ibContract.symbol = "GBP";
    gbp.ibOrder.lmtPrice = 1.2490;
    gbp.referencePrice = 1.2501;
    const ExecutionCommandResult second = restartedGateway.PlaceIbOrder(
        Previewed(restartedGateway, MakePlaceCommand(rotatedSession, gbp)));
    assert(second.status == ExecutionCommandStatus::Accepted);
    assert(second.orderId == 1000001);

    rotatedSession.executionContext.toolCallId = "lease-rotation-eur-2";
    rotatedSession.executionContext.decisionLeaseFencingToken = 12001;
    rotatedSession.executionContext.decisionLeaseGeneration = 88;
    const ExecutionCommandResult third = restartedGateway.PlaceIbOrder(
        Previewed(restartedGateway, MakePlaceCommand(
            rotatedSession, MakePlace("lease-rotation-eur-2"))));
    assert(third.status == ExecutionCommandStatus::Accepted);
    assert(third.orderId == 1000002);
    assert(CountJournalEvents(journalPath, "place_sent") == 3);

    KillServer(serverPid);
    ::unlink(socketPath.c_str());
    ::unlink((socketPath + ".lock").c_str());
    ::unlink(journalPath.c_str());
}

void TestCrashRestartFencingIdempotencyAndReplay(const char* self)
{
    const std::string suffix = std::to_string(::getpid());
    const std::string socketPath = "/tmp/hepta-execution-e2e-" + suffix + ".sock";
    const std::string journalPath = "/tmp/hepta-execution-e2e-" + suffix + ".jsonl";
    ::unlink(socketPath.c_str());
    std::remove(journalPath.c_str());

    pid_t serverPid = SpawnServer(self, socketPath, journalPath, 11, 1, false);
    UnixExecutionServiceClient client(socketPath, kDurableProcessE2eIoTimeoutMs);
    TradingToolRegistry gateway(client);
    TradingToolSession firstSession = MakeSession(11, 1);
    firstSession.executionContext.toolCallId = "place-before-crash";
    TradingToolCall firstCall = MakePlace("place-before-crash");
    IbPlaceOrderCommand firstCommand =
        MakePlaceCommand(firstSession, firstCall);
    firstCommand = Previewed(client, firstCommand);
    firstCall.previewPermit = firstCommand.previewPermit;
    firstSession.executionContext.toolCallId =
        firstCommand.context.toolCallId;
    const TradingToolResult firstResponse =
        gateway.Invoke(firstSession, firstCall);
    const long firstOrderId = RequireDurableGatewayPlace(
        client, firstCommand, firstResponse);
    assert(firstOrderId == 1000000);

    RejectingExecutionAuthority rejectingAuthority;
    UnixExecutionServiceServer competingServer(rejectingAuthority);
    std::string competingReason;
    assert(!competingServer.Start(socketPath,
        std::set<std::uint32_t>{static_cast<std::uint32_t>(::geteuid())},
        competingReason, 32768, 1000));
    const ExecutionCommandResult stillOwnedByFirstServer = client.PlaceIbOrder(firstCommand);
    assert(stillOwnedByFirstServer.status == ExecutionCommandStatus::Duplicate);
    assert(stillOwnedByFirstServer.orderId == firstOrderId);

    KillServer(serverPid);

    const ExecutionCommandResult unavailable = client.PlaceIbOrder(firstCommand);
    assert(unavailable.status == ExecutionCommandStatus::Uncertain);
    assert(unavailable.commandId == firstCommand.context.toolCallId);

    // Keep the recovered owner active so execution-domain restoration and
    // owner-authorized cancellation are exercised across the process restart.
    serverPid = SpawnServer(self, socketPath, journalPath, 22, 2, false);
    TradingToolSession staleSession = MakeSession(11, 1);
    staleSession.executionContext.toolCallId = "stale-after-restart";
    IbPlaceOrderCommand stale = MakePlaceCommand(staleSession, MakePlace("stale-after-restart"));
    stale.context.toolCallId = "stale-after-restart";
    const ExecutionCommandResult oldEpoch = client.PlaceIbOrder(stale);
    assert(oldEpoch.status == ExecutionCommandStatus::Rejected);
    assert(oldEpoch.reasonCode == "EXECUTION_SERVICE_EPOCH_MISMATCH");
    stale.order.totalQuantity = 0.0;
    stale.previewPermit.clear();
    const ExecutionCommandResult staleResult =
        client.PreviewOrder(stale);
    assert(staleResult.status == ExecutionCommandStatus::Rejected);
    assert(staleResult.reasonCode == "INVALID_ORDER");

    const ExecutionCommandResult duplicate = client.PlaceIbOrder(firstCommand);
    assert(duplicate.status == ExecutionCommandStatus::Duplicate);
    assert(duplicate.orderId == firstOrderId);

    IbPlaceOrderCommand changedQuantity = firstCommand;
    changedQuantity.order.totalQuantity += 1.0;
    const ExecutionCommandResult changedWithConsumedPermit =
        client.PlaceIbOrder(changedQuantity);
    assert(changedWithConsumedPermit.status ==
           ExecutionCommandStatus::Rejected);
    assert(changedWithConsumedPermit.reasonCode ==
           "EXECUTION_PREVIEW_PERMIT_UNKNOWN_OR_CONSUMED");
    changedQuantity.previewPermit.clear();
    changedQuantity = Previewed(client, changedQuantity);
    assert(changedQuantity.context.toolCallId != firstCommand.context.toolCallId);
    changedQuantity.context.toolCallId = firstCommand.context.toolCallId;
    const ExecutionCommandResult quantityConflict =
        client.PlaceIbOrder(changedQuantity);
    assert(quantityConflict.status == ExecutionCommandStatus::Rejected);
    assert(quantityConflict.reasonCode ==
           "EXECUTION_PREVIEW_PERMIT_COMMAND_ID_MISMATCH");

    IbPlaceOrderCommand changedPrice = Previewed(client, firstCommand);
    changedPrice.order.lmtPrice += 0.0001;
    const ExecutionCommandResult priceConflict =
        client.PlaceIbOrder(changedPrice);
    assert(priceConflict.status == ExecutionCommandStatus::Rejected);
    assert(priceConflict.reasonCode == "EXECUTION_PREVIEW_PERMIT_ORDER_MISMATCH");

    IbCancelOrderCommand operationConflict;
    operationConflict.context = firstCommand.context;
    operationConflict.orderId = firstOrderId;
    const ExecutionCommandResult placeCancelConflict = client.CancelIbOrder(operationConflict);
    assert(placeCancelConflict.status == ExecutionCommandStatus::Rejected);
    assert(placeCancelConflict.reasonCode == "IDEMPOTENCY_KEY_CONFLICT");
    assert(CountJournalEvents(journalPath, "place_sent") == 1);

    IbCancelOrderCommand recoveredOwnerCancel;
    recoveredOwnerCancel.context = MakeSession(22, 2).executionContext;
    recoveredOwnerCancel.context.toolCallId = "cancel-recovered-owner";
    recoveredOwnerCancel.orderId = firstOrderId;
    const ExecutionCommandResult cancelResult =
        RetryFixtureCancel(client, recoveredOwnerCancel);
    assert(cancelResult.status == ExecutionCommandStatus::Accepted ||
           cancelResult.status == ExecutionCommandStatus::Duplicate);
    assert(cancelResult.commandId ==
           recoveredOwnerCancel.context.toolCallId);
    assert(cancelResult.orderId == firstOrderId);

    TradingToolSession secondSession = MakeSession(22, 2);
    secondSession.executionContext.toolCallId = "place-after-restart";
    TradingToolCall secondCall = MakePlace("place-after-restart");
    IbPlaceOrderCommand secondCommand =
        MakePlaceCommand(secondSession, secondCall);
    secondCommand = Previewed(client, secondCommand);
    secondCall.previewPermit = secondCommand.previewPermit;
    secondSession.executionContext.toolCallId =
        secondCommand.context.toolCallId;
    const TradingToolResult secondResponse =
        gateway.Invoke(secondSession, secondCall);
    const long secondOrderId = RequireDurableGatewayPlace(
        client, secondCommand, secondResponse);
    assert(secondOrderId == 1000001);
    KillServer(serverPid);

    serverPid = SpawnServer(self, socketPath, journalPath, 33, 3, true);
    const ExecutionCommandResult secondOldEpoch = client.PlaceIbOrder(secondCommand);
    assert(secondOldEpoch.status == ExecutionCommandStatus::Rejected);
    assert(secondOldEpoch.reasonCode == "EXECUTION_SERVICE_EPOCH_MISMATCH");
    const ExecutionCommandResult secondDuplicate = client.PlaceIbOrder(secondCommand);
    assert(secondDuplicate.status == ExecutionCommandStatus::Duplicate);
    assert(secondDuplicate.orderId == secondOrderId);
    KillServer(serverPid);

    OmsJournal journal;
    assert(journal.Init(journalPath));
    int placeReceipts = 0;
    int reconciledOwners = 0;
    journal.Replay([&](const OmsJournalEvent& event) {
        if (event.eventType == "place_sent") ++placeReceipts;
        if (event.eventType == "order_owner_reconciled_terminal") ++reconciledOwners;
    });
    assert(placeReceipts == 2);
    assert(reconciledOwners == 2);

    ::unlink(socketPath.c_str());
    ::unlink((socketPath + ".lock").c_str());
    std::remove(journalPath.c_str());
}
}

int main(int argc, char** argv)
{
    if (argc == 8 && std::string(argv[1]) == "--server")
        return RunServer(argv[2], argv[3], std::strtoull(argv[4], nullptr, 10),
                         std::strtoull(argv[5], nullptr, 10), std::string(argv[6]) == "1",
                         std::atoi(argv[7]));
    if (argc == 10 && std::string(argv[1]) == "--fault-server")
        return RunServer(argv[2], argv[3], std::strtoull(argv[4], nullptr, 10),
                         std::strtoull(argv[5], nullptr, 10), std::string(argv[6]) == "1",
                         std::atoi(argv[7]), argv[8], argv[9]);
    TestProtocolRoundTripAndUnavailableFailUncertain();
    TestLargeAuthoritativeReadResponseThroughUnixTransport();
    TestSystemdSocketActivatorCredentialBoundary();
    TestServiceFencingGenerationRejectedBeforeAuthority();
    TestExecutionPreviewPermitBindingAndSingleUse();
    TestExecutionFlattenPermitBindingAndSingleUse();
    TestIdentityCompareAndInvalidatePreservesNewCache();
    TestExecutionServicePeerCredentialRejection();
    TestMismatchedResponseCommandIdIsUncertain();
    TestServiceLeaseAcrossInstrumentsGatewayRestartAndSessionRotate(argv[0]);
    TestInFlightCrashMatrix(argv[0]);
    TestCrashRestartFencingIdempotencyAndReplay(argv[0]);
    std::cout << "execution_service_process_e2e_evidence: crash_windows=4"
              << " venue_send_ledger=verified oms_replay=verified"
              << " service_lease=verified multi_instrument=verified"
              << " gateway_restart=verified session_rotate=verified"
              << " agent_lease_not_on_wire=verified"
              << " authoritative_read_rpc=verified"
              << " preview_permit=single_use"
              << " flatten_preview_permit=single_use"
              << " command_id=execution_issued"
              << " owner_fence_revokes_preview=verified"
              << " same_command_retry=exactly_once"
              << " owner_reconcile=verified" << std::endl;
    std::cout << "execution_service_process_e2e_tests: PASS" << std::endl;
    return 0;
}
