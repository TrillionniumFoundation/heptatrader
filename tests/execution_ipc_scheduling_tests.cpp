#include "../HeptaTrade/execution/unix_execution_service_server.h"
#include "../HeptaTrade/execution/unix_execution_service_client.h"
#include "../HeptaTrade/execution/execution_coordinator.h"
#include "../HeptaTrade/execution/execution_service_protocol.h"
#include "../HeptaTrade/execution/unix_execution_service_internal.h"
#include <arpa/inet.h>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstring>
#include <future>
#include <functional>
#include <iostream>
#include <mutex>
#include <stdexcept>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include <vector>

namespace {
using Clock = std::chrono::steady_clock;
void Check(bool ok, const char* detail) { if (!ok) throw std::runtime_error(detail); }
long long Micros(Clock::time_point from) {
    return std::chrono::duration_cast<std::chrono::microseconds>(Clock::now() - from).count();
}
struct Gate {
    std::mutex mutex; std::condition_variable changed;
    bool entered = false, released = false;
    void Block() {
        std::unique_lock<std::mutex> lock(mutex); entered = true; changed.notify_all();
        Check(changed.wait_for(lock, std::chrono::seconds(5), [this] { return released; }), "gate release timeout");
    }
    void Wait() {
        std::unique_lock<std::mutex> lock(mutex);
        Check(changed.wait_for(lock, std::chrono::seconds(2), [this] { return entered; }), "authority did not enter");
    }
    void Release() { std::lock_guard<std::mutex> lock(mutex); released = true; changed.notify_all(); }
};
AgentExecutionContext Owner(const std::string& id) {
    AgentExecutionContext c; c.agentId = "ipc-test"; c.sessionId = "session";
    c.toolCallId = id; c.strategy = "test"; c.account = "DU123";
    c.venue = "IB"; c.executionDomain = "PAPER"; return c;
}
ExecutionControlCommand Control(const std::string& id) {
    ExecutionControlCommand c; c.context = Owner(id); c.targetCommandId = "target"; return c;
}
CancelOrderCommand Cancel(const std::string& id, long order) {
    CancelOrderCommand c; c.context = Owner(id); c.orderId = order; return c;
}
struct Authority : ExecutionAuthority, ExecutionControlAuthority, ExecutionReadAuthority {
    Gate gate;
    Gate commandGate;
    std::atomic<int> cancels{0};
    std::atomic<int> reconciles{0};
    std::function<void()> afterBlock;
    std::atomic<bool> blockFirstCancel{true};
    std::atomic<bool> blockReconcile{false};
    std::atomic<bool> injectUnrelatedEvidence{false};
    std::mutex orderMutex; std::vector<long> order;
    ExecutionCommandResult PlaceOrder(const PlaceOrderCommand& c) override {
        ExecutionCommandResult r; r.commandId = c.context.toolCallId; return r;
    }
    ExecutionCommandResult CancelOrder(const CancelOrderCommand& c) override {
        const int number = ++cancels;
        { std::lock_guard<std::mutex> lock(orderMutex); order.push_back(c.orderId); }
        if (number == 1 && blockFirstCancel.load()) {
            gate.Block(); if (afterBlock) afterBlock();
        }
        ExecutionCommandResult r; r.commandId = c.context.toolCallId;
        r.status = ExecutionCommandStatus::Accepted; r.orderId = c.orderId; return r;
    }
    ExecutionControlResult Reply(const ExecutionControlCommand& c) {
        ExecutionControlResult r; r.status = ExecutionCommandStatus::Accepted;
        r.commandId = c.context.toolCallId; r.targetCommandId = c.targetCommandId;
        if (injectUnrelatedEvidence) {
            r.ownerAuditAuthoritative = true; r.ownerAuditComplete = true;
            r.ownerActiveOrderCount = 73; r.terminalMutationGateClosed = true;
            r.terminalLatchDurable = true; r.terminalRuntimeVerified = true;
        }
        return r;
    }
    ExecutionControlStatusResult QueryCommandStatus(const ExecutionControlCommand& c) override { return Reply(c); }
    ExecutionControlStatusResult FenceSessionOwner(const ExecutionControlCommand& c) override { return Reply(c); }
    ExecutionControlStatusResult ReleaseSessionOwnerFence(const ExecutionControlCommand& c) override { return Reply(c); }
    ExecutionControlStatusResult ReconcileAuthoritativeState(
        const ExecutionControlCommand& c) override {
        ++reconciles;
        if (blockReconcile.load()) commandGate.Block();
        return Reply(c);
    }
    ExecutionTerminalResult TerminalizeRecoveryOwner(
        const ExecutionControlCommand& c) override {
        ExecutionTerminalResult r;
        r.status = ExecutionCommandStatus::Accepted;
        r.commandId = c.context.toolCallId;
        r.targetCommandId = c.targetCommandId;
        r.mutationBlocked = true;
        r.reasonCode = "TEST_TERMINAL_HALTED";
        r.ownerAccount = c.context.account;
        r.ownerExecutionDomain = c.context.executionDomain;
        r.terminalizationServiceEpoch = "test-terminal-service";
        r.terminalizationServiceFencingGeneration = 5;
        r.terminalizationGeneration = 1;
        r.terminalLatchSha256 = "sha256:" + std::string(64, 'a');
        r.terminalMutationGateClosed = true;
        r.terminalBrokerTransportConnected = false;
        r.terminalBrokerEventIngressHalted = true;
        r.terminalBrokerCallbackQueueDrained = true;
        r.terminalBrokerCallbacksInFlight = 0;
        r.terminalBrokerReconnectPermitted = false;
        r.terminalLatchDurable = true;
        r.terminalRuntimeLatchLoaded = true;
        r.terminalRuntimeVerified = true;
        return r;
    }
    ExecutionCommandResult PreviewOrder(const PlaceOrderCommand& c) override {
        ExecutionCommandResult r; r.commandId = c.context.toolCallId;
        r.status = ExecutionCommandStatus::Accepted; r.detail = "{}"; return r;
    }
    ExecutionCommandResult ReadAuthoritativeState(const ExecutionReadCommand& c) override {
        ExecutionCommandResult r; r.status = ExecutionCommandStatus::Accepted;
        r.commandId = c.context.toolCallId; r.detail = "{}"; return r;
    }
};
struct Fixture {
    std::string directory, path; Authority authority; UnixExecutionServiceServer server;
    explicit Fixture(int timeout = 1500) : server(authority, &authority) {
        char name[] = "/tmp/hepta-ipc-scheduling-XXXXXX";
        Check(::mkdtemp(name) != nullptr, "mkdtemp"); directory = name; path = directory + "/execution.sock";
        std::string reason;
        Check(server.Start(path, {static_cast<std::uint32_t>(::geteuid())}, reason, 32768, timeout), reason.c_str());
    }
    ~Fixture() {
        authority.gate.Release();
        authority.commandGate.Release();
        server.Stop();
        ::unlink((path + ".lock").c_str());
        ::rmdir(directory.c_str());
    }
    int Connect() {
        int fd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
        Check(fd >= 0, "socket"); sockaddr_un address{}; address.sun_family = AF_UNIX;
        std::memcpy(address.sun_path, path.c_str(), path.size() + 1);
        if (::connect(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) != 0) {
            ::close(fd); throw std::runtime_error("connect");
        }
        return fd;
    }
    long long Identity() {
        UnixExecutionServiceClient client(path, 1000); ExecutionServiceIdentity identity; std::string reason;
        const auto start = Clock::now(); Check(client.GetServiceIdentity(identity, reason), reason.c_str());
        return Micros(start);
    }
};
void OrdinaryControlWireCannotCarryAuditOrTerminalEvidence() {
    Fixture f;
    f.authority.injectUnrelatedEvidence = true;
    ExecutionServiceRequest request;
    request.operation = ExecutionServiceOperation::QueryCommandStatus;
    request.control = Control("narrow-wire");
    const auto identity = f.server.ServiceIdentity();
    request.expectedServiceEpoch = identity.serviceEpoch;
    request.expectedServiceFencingGeneration = identity.serviceFencingGeneration;
    std::string body, response, reason;
    Check(ExecutionServiceProtocol::EncodeRequest(request, body, reason), "encode status request");
    const int fd = f.Connect();
    const auto deadline = Clock::now() + std::chrono::seconds(2);
    const bool transported = HeptaExecutionServiceInternal::WriteFrame(fd, body, deadline) &&
        HeptaExecutionServiceInternal::ReadFrame(fd, 32768, deadline, response);
    ::close(fd);
    Check(transported, "raw control roundtrip");
    ExecutionControlResult decoded;
    Check(ExecutionServiceProtocol::DecodeControlResponse(response, decoded, reason), "decode v11 status response");
    Check(decoded.status == ExecutionCommandStatus::Accepted && decoded.commandId == "narrow-wire",
          "narrowing changed the ordinary command result");
    Check(decoded.targetCommandId == request.control.targetCommandId &&
          decoded.serviceEpoch == identity.serviceEpoch &&
          decoded.serviceFencingGeneration == identity.serviceFencingGeneration,
          "narrowing lost command or service identity");
    Check(!decoded.ownerAuditAuthoritative && !decoded.ownerAuditComplete &&
          decoded.ownerActiveOrderCount == 0 && !decoded.terminalMutationGateClosed &&
          !decoded.terminalLatchDurable && !decoded.terminalRuntimeVerified,
          "ordinary status leaked unrelated authority into the compatibility wire");
}

void TerminalWireCarriesOnlyOwnerBoundWitness() {
    Fixture f;
    ExecutionServiceRequest request;
    request.operation = ExecutionServiceOperation::TerminalizeRecoveryOwner;
    request.control = Control("terminal-wire");
    request.control.targetCommandId = "terminal-finalization";
    request.control.recoveryIngressFence = 7;
    request.control.terminalPreliminaryReceiptSha256 =
        "sha256:" + std::string(64, 'b');
    const auto identity = f.server.ServiceIdentity();
    request.expectedServiceEpoch = identity.serviceEpoch;
    request.expectedServiceFencingGeneration = identity.serviceFencingGeneration;
    std::string body, response, reason;
    Check(ExecutionServiceProtocol::EncodeRequest(request, body, reason),
          "encode terminal request");
    const int fd = f.Connect();
    const auto deadline = Clock::now() + std::chrono::seconds(2);
    const bool transported =
        HeptaExecutionServiceInternal::WriteFrame(fd, body, deadline) &&
        HeptaExecutionServiceInternal::ReadFrame(
            fd, 32768, deadline, response);
    ::close(fd);
    Check(transported, "raw terminal roundtrip");
    ExecutionControlResult decoded;
    Check(ExecutionServiceProtocol::DecodeControlResponse(
              response, decoded, reason),
          "decode v11 terminal response");
    Check(decoded.status == ExecutionCommandStatus::Accepted &&
          decoded.commandId == "terminal-wire" &&
          decoded.targetCommandId == "terminal-finalization",
          "terminal narrowing changed command identity");
    Check(decoded.ownerAccount == request.control.context.account &&
          decoded.ownerExecutionDomain ==
              request.control.context.executionDomain,
          "terminal result lost owner binding");
    Check(decoded.terminalRuntimeVerified &&
          decoded.terminalMutationGateClosed &&
          decoded.terminalBrokerEventIngressHalted &&
          decoded.terminalBrokerCallbackQueueDrained &&
          decoded.terminalLatchDurable &&
          decoded.terminalRuntimeLatchLoaded &&
          !decoded.terminalBrokerTransportConnected &&
          !decoded.terminalBrokerReconnectPermitted &&
          decoded.terminalBrokerCallbacksInFlight == 0,
          "terminal witness did not survive compatibility encoding");
    Check(!decoded.ownerAuditAuthoritative &&
          !decoded.ownerAuditComplete &&
          decoded.ownerActiveOrderCount == 0 &&
          decoded.ownerUncertainCommandCount == 0 &&
          decoded.brokerActiveGeneration == 0 &&
          decoded.brokerTerminalGeneration == 0,
          "terminal result manufactured owner-audit authority");
}

void IdleWorkersAlwaysObserveShutdown() {
    Fixture f;
    for (unsigned cycle = 0; cycle < 100; ++cycle) {
        f.server.Stop();
        std::string reason;
        Check(f.server.Start(f.path, {static_cast<std::uint32_t>(::geteuid())}, reason),
              "idle-worker restart failed");
        if (cycle % 3 == 0) f.Identity();
        // Immediate shutdown races newly started workers entering their wait.
        // No process-global timing or test-only runtime bypass is installed.
    }
    f.server.Stop();
}

void PartialFramesDoNotOccupyWorkers() {
    Fixture f; std::vector<int> peers;
    for (int i = 0; i < 12; ++i) {
        const int fd = f.Connect(); const char byte = 0;
        Check(::write(fd, &byte, 1) == 1, "partial header"); peers.push_back(fd);
    }
    const auto latency = f.Identity();
    for (int fd : peers) ::close(fd);
    Check(latency < 500000, "identity blocked behind partial frames");
    Check(f.authority.cancels == 0, "partial frame reached authority");
    std::cout << "IPC_PARTIAL_FRAME_IDENTITY_US=" << latency << '\n';
}
void SlowAuthorityKeepsControlAvailableAndTimeoutDoesNotRetry() {
    Fixture f(250); const auto identity = f.server.ServiceIdentity();
    auto mutation = std::async(std::launch::async, [&] {
        UnixExecutionServiceClient client(
            f.path, 1500, 32768, std::set<std::uint32_t>(), 250);
        return client.CancelIbOrderWithIdentity(Cancel("slow", 1), identity);
    });
    f.authority.gate.Wait();
    const auto latency = f.Identity();
    UnixExecutionServiceClient control(f.path, 1000);
    Check(control.QueryCommandStatusWithIdentity(Control("status"), identity).status == ExecutionCommandStatus::Accepted,
          "status blocked behind authority");
    Check(control.FenceSessionOwnerWithIdentity(Control("fence"), identity).status == ExecutionCommandStatus::Accepted,
          "fence blocked behind authority");
    Check(mutation.wait_for(std::chrono::seconds(1)) == std::future_status::ready, "transport deadline not enforced");
    Check(mutation.get().status == ExecutionCommandStatus::Uncertain, "lost mutation response must stay uncertain");
    Check(f.authority.cancels == 1, "authority was retried");
    Check(f.Identity() < 500000, "identity blocked after mutation transport timeout");
    f.authority.gate.Release();
    Check(latency < 500000, "identity blocked behind slow authority");
    std::cout << "IPC_SLOW_AUTHORITY_IDENTITY_US=" << latency << '\n';
}
void ExecutingAuthorityGetsFreshResponseWindow() {
    Fixture f(100);
    const auto identity = f.server.ServiceIdentity();
    auto mutation = std::async(std::launch::async, [&] {
        UnixExecutionServiceClient client(
            f.path, 500, 32768, std::set<std::uint32_t>(), 1500);
        return client.CancelIbOrderWithIdentity(
            Cancel("slow-success", 91), identity);
    });
    f.authority.gate.Wait();
    // Exceed the server's framing/queue budget after dispatch. The executing
    // authority must not lose its reply channel because that earlier phase's
    // clock elapsed.
    std::this_thread::sleep_for(std::chrono::milliseconds(300));
    f.authority.gate.Release();
    Check(mutation.wait_for(std::chrono::seconds(2)) ==
              std::future_status::ready,
          "executing authority did not complete inside caller response budget");
    const ExecutionCommandResult result = mutation.get();
    Check(result.status == ExecutionCommandStatus::Accepted &&
              result.orderId == 91,
          "executing authority lost its fresh response window");
    Check(f.authority.cancels == 1,
          "slow successful authority was retried");
}
void SaturatedOrdinaryQueueDoesNotBlockExitLane() {
    Fixture f(3000);
    const auto identity = f.server.ServiceIdentity();
    f.authority.blockFirstCancel.store(false);
    f.authority.blockReconcile.store(true);

    auto reconcile = [&](const std::string& id) {
        UnixExecutionServiceClient client(f.path, 4000);
        return client.ReconcileAuthoritativeStateWithIdentity(
            Control(id), identity);
    };
    auto first = std::async(std::launch::async, [&] {
        return reconcile("blocked-command-0");
    });
    f.authority.commandGate.Wait();

    std::vector<std::future<ExecutionControlStatusResult>> backlog;
    for (unsigned int i = 1; i <= 30; ++i) {
        backlog.push_back(std::async(std::launch::async, [&, i] {
            return reconcile("blocked-command-" + std::to_string(i));
        }));
    }

    // One request is executing and the command lane admits only 24 queued
    // requests. Observe at least one conservative transport failure before
    // releasing the authority to prove that the ordinary queue is saturated.
    const auto saturationDeadline = Clock::now() + std::chrono::seconds(1);
    bool saturated = false;
    while (Clock::now() < saturationDeadline && !saturated) {
        for (auto& pending : backlog)
            if (pending.wait_for(std::chrono::milliseconds(0)) ==
                std::future_status::ready) {
                saturated = true;
                break;
            }
        if (!saturated)
            std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
    Check(saturated, "ordinary command queue did not reach bounded saturation");
    Check(f.authority.reconciles == 1,
          "serialized ordinary lane dispatched through the blocked authority");

    UnixExecutionServiceClient exitClient(f.path, 1000);
    const auto exitStarted = Clock::now();
    const ExecutionCommandResult cancel =
        exitClient.CancelIbOrderWithIdentity(
            Cancel("exit-during-saturation", 77), identity);
    const long long exitLatency = Micros(exitStarted);
    Check(cancel.status == ExecutionCommandStatus::Accepted,
          "cancel did not bypass saturated ordinary command queue");
    Check(exitLatency < 500000,
          "cancel was head-of-line blocked by ordinary command queue");
    Check(f.authority.cancels == 1, "cancel retried or was not dispatched");

    f.authority.commandGate.Release();
    first.get();
    for (auto& pending : backlog) pending.get();
    std::cout << "IPC_SATURATED_COMMAND_CANCEL_US=" << exitLatency << '\n';
}

void ExpiredQueuedCommandNeverDispatches() {
    Fixture f(250); const auto identity = f.server.ServiceIdentity();
    auto call = [&](const char* id, long order) {
        UnixExecutionServiceClient client(f.path, 1500);
        return client.CancelIbOrderWithIdentity(Cancel(id, order), identity);
    };
    auto first = std::async(std::launch::async, [&] { return call("first", 1); });
    f.authority.gate.Wait();
    auto second = std::async(std::launch::async, [&] { return call("expired", 2); });
    Check(second.wait_for(std::chrono::seconds(1)) == std::future_status::ready, "queued deadline not enforced");
    Check(second.get().status == ExecutionCommandStatus::Uncertain, "queued transport failure not conservative");
    first.get(); f.authority.gate.Release();
    Check(call("third", 3).status == ExecutionCommandStatus::Accepted, "healthy request did not recover");
    Check(f.authority.cancels == 2, "expired queued command reached authority");
    Check(f.authority.order == std::vector<long>({1, 3}), "ordinary lane lost order or dispatched expired work");
}
void StopDoesNotDeadlockCallbackOrAbandonAuthority() {
    Fixture f; const auto identity = f.server.ServiceIdentity();
    f.authority.afterBlock = [&] { f.server.ServiceIdentity(); f.server.Stop(); };
    auto call = std::async(std::launch::async, [&] {
        UnixExecutionServiceClient client(f.path, 1500);
        return client.CancelIbOrderWithIdentity(Cancel("stop", 1), identity);
    });
    f.authority.gate.Wait();
    auto stop1 = std::async(std::launch::async, [&] { f.server.Stop(); });
    auto stop2 = std::async(std::launch::async, [&] { f.server.Stop(); });
    Check(stop1.wait_for(std::chrono::milliseconds(50)) != std::future_status::ready,
          "Stop abandoned an in-flight authority");
    f.authority.gate.Release();
    Check(stop1.wait_for(std::chrono::seconds(2)) == std::future_status::ready, "Stop/callback lock inversion");
    Check(stop2.wait_for(std::chrono::seconds(2)) == std::future_status::ready, "concurrent Stop did not complete");
    stop1.get(); stop2.get(); call.get();
    std::string reason;
    Check(f.server.Start(f.path, {static_cast<std::uint32_t>(::geteuid())}, reason), "restart after stop failed");
    Check(f.server.ServiceIdentity().serviceEpoch != identity.serviceEpoch, "restart reused service epoch");
    Check(f.Identity() < 500000, "reactor not restarted");
}

struct CoordinatorAuthority : ExecutionAuthority, ExecutionControlAuthority, ExecutionReadAuthority {
    ExecutionCoordinator& coordinator;
    explicit CoordinatorAuthority(ExecutionCoordinator& c) : coordinator(c) {}
    ExecutionCommandResult PlaceOrder(const PlaceOrderCommand& c) override { return coordinator.PlaceOrder(c); }
    ExecutionCommandResult CancelOrder(const CancelOrderCommand& c) override { return coordinator.CancelOrder(c); }
    bool IsDurablePlaceReplay(const PlaceOrderCommand& c) const override { return coordinator.IsDurablePlaceReplay(c); }
    ExecutionCommandResult PreviewOrder(const PlaceOrderCommand& c) override {
        ExecutionCommandResult r; r.status = ExecutionCommandStatus::Accepted;
        r.commandId = c.context.toolCallId; r.detail = "{}"; return r;
    }
    ExecutionCommandResult ReadAuthoritativeState(const ExecutionReadCommand& c) override {
        ExecutionCommandResult r; r.commandId = c.context.toolCallId; return r;
    }
    ExecutionControlStatusResult QueryCommandStatus(const ExecutionControlCommand& c) override {
        ExecutionControlStatusResult r; r.commandId = c.context.toolCallId; r.targetCommandId = c.targetCommandId;
        ExecutionCommandResult target;
        if (coordinator.GetCommandStatus(c.context.agentId, c.context.sessionId, c.targetCommandId, target)) {
            r.status = ExecutionCommandStatus::Accepted; r.targetStatus = target.status; r.orderId = target.orderId;
        }
        return r;
    }
    ExecutionControlStatusResult FenceSessionOwner(const ExecutionControlCommand& c) override {
        ExecutionControlStatusResult r; r.commandId = c.context.toolCallId; r.status = ExecutionCommandStatus::Accepted;
        r.affectedCount = coordinator.FenceSessionOwner(c.context.agentId, c.context.sessionId); return r;
    }
    ExecutionControlStatusResult ReleaseSessionOwnerFence(const ExecutionControlCommand& c) override {
        ExecutionControlStatusResult r; r.commandId = c.context.toolCallId;
        if (coordinator.AuditAndReleaseSessionOwnerFence(c.context.agentId, c.context.sessionId, true, r.reasonCode))
            r.status = ExecutionCommandStatus::Accepted;
        return r;
    }
    ExecutionControlStatusResult ReconcileAuthoritativeState(const ExecutionControlCommand& c) override {
        ExecutionControlStatusResult r; r.commandId = c.context.toolCallId; return r;
    }
};
std::string JsonField(const std::string& json, const std::string& key) {
    // Test-only extraction of server-generated opaque values; never a client authority.
    const std::string marker = "\"" + key + "\":\"";
    const auto begin = json.find(marker); Check(begin != std::string::npos, "missing server-issued field");
    const auto start = begin + marker.size(), end = json.find('"', start);
    Check(end != std::string::npos, "invalid server-generated JSON"); return json.substr(start, end - start);
}
void RealCoordinatorFenceSeesInFlightAndRemainsDurable() {
    char name[] = "/tmp/hepta-ipc-owner-XXXXXX";
    Check(::mkdtemp(name) != nullptr, "coordinator mkdtemp");
    const std::string directory = name, journalPath = directory + "/journal", socketPath = directory + "/execution.sock";
    {
        OmsJournal journal; Check(journal.Init(journalPath), "journal init");
        Gate gate; std::atomic<int> sends{0};
        ExecutionCoordinatorCallbacks callbacks;
        callbacks.placement = VenuePlacement::Immediate([&](const PlaceOrderCommand&, const std::string&) {
            ++sends; gate.Block(); return VenuePlaceResult::Submitted(42);
        });
        callbacks.validateDecisionLease = [](const AgentExecutionContext&, const std::string&, std::string*) { return true; };
        ExecutionCoordinator coordinator(journal, callbacks);
        CoordinatorAuthority authority(coordinator); UnixExecutionServiceServer server(authority, &authority);
        std::string reason;
        Check(server.Start(socketPath, {static_cast<std::uint32_t>(::geteuid())}, reason), "server start");
        const auto identity = server.ServiceIdentity(); UnixExecutionServiceClient client(socketPath);
        PlaceOrderCommand place; place.context = Owner("preview"); place.contract.symbol = "EUR";
        place.contract.secType = "CASH"; place.contract.currency = "USD"; place.contract.exchange = "IDEALPRO";
        place.instrument = "EUR.USD"; place.order.action = "BUY"; place.order.orderType = "LMT";
        place.order.totalQuantity = 1000; place.order.lmtPrice = 1.1; place.timeInForce = "DAY";
        place.expiresAtMs = OmsJournal::NowEpochMs() + 60000;
        const auto preview = client.PreviewOrderWithIdentity(place, identity);
        Check(preview.status == ExecutionCommandStatus::Accepted, "preview transport failed");
        place.previewPermit = JsonField(preview.detail, "preview_permit");
        place.context.toolCallId = JsonField(preview.detail, "command_id");
        auto mutation = std::async(std::launch::async, [&] {
            UnixExecutionServiceClient caller(socketPath); return caller.PlaceIbOrderWithIdentity(place, identity);
        });
        gate.Wait();
        ExecutionControlCommand status = Control("status"); status.targetCommandId = place.context.toolCallId;
        const auto pending = client.QueryCommandStatusWithIdentity(status, identity);
        Check(pending.status == ExecutionCommandStatus::Accepted && pending.targetStatus == ExecutionCommandStatus::Uncertain,
              "durable send-attempt not queryable while venue blocked");
        const auto fenced = client.FenceSessionOwnerWithIdentity(Control("fence"), identity);
        Check(fenced.status == ExecutionCommandStatus::Accepted && fenced.affectedCount > 0, "fence lost in-flight owner");
        const auto release = client.ReleaseSessionOwnerFenceWithIdentity(Control("release"), identity);
        Check(release.status != ExecutionCommandStatus::Accepted, "fence released across unresolved venue effect");
        gate.Release();
        Check(mutation.get().status == ExecutionCommandStatus::Accepted, "in-flight receipt lost");
        Check(client.PlaceIbOrderWithIdentity(place, identity).status == ExecutionCommandStatus::Duplicate,
              "same-ID replay did not preserve durable result");
        Check(sends == 1, "mutation resent after fencing/reply replay");
        server.Stop();
        ExecutionCoordinator restarted(journal, callbacks);
        Check(restarted.RecoverFromJournal(reason), reason.c_str());
        Check(restarted.IsSessionOwnerFenced(place.context.agentId, place.context.sessionId), "restart resurrected fenced owner");
        Check(sends == 1, "recovery dispatched venue mutation");
    }
    ::unlink(socketPath.c_str()); ::unlink((socketPath + ".lock").c_str());
    ::unlink(journalPath.c_str()); ::rmdir(directory.c_str());
}
}
int main() {
    try {
        OrdinaryControlWireCannotCarryAuditOrTerminalEvidence();
        TerminalWireCarriesOnlyOwnerBoundWitness();
        IdleWorkersAlwaysObserveShutdown();
        PartialFramesDoNotOccupyWorkers();
        SlowAuthorityKeepsControlAvailableAndTimeoutDoesNotRetry();
        ExecutingAuthorityGetsFreshResponseWindow();
        SaturatedOrdinaryQueueDoesNotBlockExitLane();
        ExpiredQueuedCommandNeverDispatches();
        StopDoesNotDeadlockCallbackOrAbandonAuthority();
        RealCoordinatorFenceSeesInFlightAndRemainsDurable();
        std::cout << "Execution IPC scheduling, deadlines, shutdown and durable owner fencing passed\n";
        return 0;
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
