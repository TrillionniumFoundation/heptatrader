// Real canonical services over local sockets. Synthetic simulator only; this is
// not a broker campaign or proof of installed, different-UID process isolation.
#include "hepta/research/native_strategy_client.h"
#include <execution/execution_service_runtime_composition.h>
#include <tool_host/tool_gateway_runtime_composition.h>
#include <tool_host/session_supervisor_audit_journal.h>
#include <tool_host/session_supervisor_protocol.h>
#include <tools/trading_tool_wire_contract.h>
#include <atomic>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstring>
#include <dirent.h>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <poll.h>
#include <set>
#include <stdexcept>
#include <string>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <thread>
#include <unistd.h>

namespace {
using namespace hepta::research;
void Require(bool value, const std::string& message) {
    if (!value) throw std::runtime_error(message);
}
class Fd {
public:
    explicit Fd(int value = -1) : value_(value) {}
    ~Fd() { if (value_ >= 0) ::close(value_); }
    Fd(const Fd&) = delete;
    Fd& operator=(const Fd&) = delete;
    int Get() const { return value_; }
    int Release() { const int result = value_; value_ = -1; return result; }
private:
    int value_;
};
void RemovePrivateTree(const std::string& path) {
    DIR* directory = ::opendir(path.c_str());
    if (!directory) return;
    while (dirent* entry = ::readdir(directory)) {
        const std::string name(entry->d_name);
        if (name == "." || name == "..") continue;
        const std::string child = path + "/" + name;
        struct stat info;
        if (::lstat(child.c_str(), &info) != 0) continue;
        if (S_ISDIR(info.st_mode)) RemovePrivateTree(child);
        else ::unlink(child.c_str()); // Never follow a link outside the private fixture.
    }
    ::closedir(directory);
    ::rmdir(path.c_str());
}
class TemporaryRoot {
public:
    TemporaryRoot() {
        char name[] = "/tmp/hepta-research-execution-XXXXXX";
        const char* made = ::mkdtemp(name);
        Require(made != nullptr, "mkdtemp failed");
        path = made;
    }
    ~TemporaryRoot() { RemovePrivateTree(path); }
    std::string path;
};
void WritePrivateFile(const std::string& path, const std::string& text, mode_t mode) {
    Fd fd(::open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600));
    Require(fd.Get() >= 0, "cannot create fixture file");
    std::size_t offset = 0;
    while (offset < text.size()) {
        const ssize_t count = ::write(fd.Get(), text.data() + offset, text.size() - offset);
        if (count < 0 && errno == EINTR) continue;
        Require(count > 0, "fixture write failed");
        offset += static_cast<std::size_t>(count);
    }
    Require(::fchmod(fd.Get(), mode) == 0 && ::fsync(fd.Get()) == 0, "fixture sync failed");
}
int Listen(const std::string& path) {
    Fd fd(::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0));
    Require(fd.Get() >= 0, "fixture socket failed");
    sockaddr_un address{};
    address.sun_family = AF_UNIX;
    Require(path.size() < sizeof(address.sun_path), "fixture socket name too long");
    std::memcpy(address.sun_path, path.c_str(), path.size() + 1);
    Require(::bind(fd.Get(), reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0 &&
            ::listen(fd.Get(), 16) == 0, "fixture listen failed");
    return fd.Release();
}
// Drop exactly one accepted placement reply between the real Native client and
// the real Gateway. All request/response bytes otherwise pass unchanged. No
// synthetic authority, risk decision, order ID or callback is introduced.
class DropReplyProxy {
public:
    DropReplyProxy(const std::string& path, const std::string& upstream,
                   const std::string& commandId)
        : listener_(Listen(path)), upstream_(upstream), commandId_(commandId) {
        thread_ = std::thread([this]() { Pump(); });
    }
    ~DropReplyProxy() { stopped_.store(true); if (thread_.joinable()) thread_.join(); }
    void CheckOneAttempt() {
        std::lock_guard<std::mutex> lock(mutex_);
        Require(error_.empty(), "reply-drop proxy failed: " + error_);
        Require(dropped_.load() && attempts_.load() == 1, "lost reply caused an automatic mutation retry");
    }
private:
    void Pump() {
        try {
            while (!stopped_.load()) {
                pollfd ready{listener_.Get(), POLLIN, 0};
                const int state = ::poll(&ready, 1, 50);
                if (state < 0 && errno == EINTR) continue;
                Require(state >= 0, "proxy poll failed");
                if (state == 0) continue;
                Fd downstream(::accept(listener_.Get(), nullptr, nullptr));
                Require(downstream.Get() >= 0, "proxy accept failed");
                std::string body, response, reason;
                Require(TypedToolProtocol::ReadFrame(downstream.Get(), 65536, 3000, body, reason), reason);
                TradingToolHostRequest request;
                Require(TypedToolProtocol::DecodeRequest(body, request, reason), reason);
                Fd upstream(::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0));
                sockaddr_un address{}; address.sun_family = AF_UNIX;
                Require(upstream_.size() < sizeof(address.sun_path), "proxy path too long");
                std::memcpy(address.sun_path, upstream_.c_str(), upstream_.size() + 1);
                Require(upstream.Get() >= 0 && ::connect(upstream.Get(),
                    reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0, "proxy connect failed");
                Require(TypedToolProtocol::WriteFrame(upstream.Get(), body, 3000, reason), reason);
                Require(TypedToolProtocol::ReadFrame(upstream.Get(), 1048576, 3000, response, reason), reason);
                if (request.call.name == "trade.place_order" && request.toolCallId == commandId_) {
                    ++attempts_;
                    if (!dropped_.load()) {
                        TypedToolResultEnvelope result;
                        Require(TypedToolProtocol::DecodeResultEnvelope(response, result, reason), reason);
                        Require(result.status == "ok", "proxy target was not actually accepted");
                        dropped_.store(true);
                        continue; // RAII closes the client socket without its accepted response.
                    }
                }
                Require(TypedToolProtocol::WriteFrame(downstream.Get(), response, 3000, reason), reason);
            }
        } catch (const std::exception& error) {
            std::lock_guard<std::mutex> lock(mutex_);
            error_ = error.what();
        }
    }
    Fd listener_;
    std::string upstream_, commandId_;
    std::atomic<bool> stopped_{false}, dropped_{false};
    std::atomic<unsigned int> attempts_{0};
    std::mutex mutex_;
    std::string error_;
    std::thread thread_;
};

SessionSupervisorResult Supervise(const std::string& path, const SessionSupervisorRequest& request) {
    Fd fd(::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0));
    Require(fd.Get() >= 0, "supervisor client socket failed");
    sockaddr_un address{}; address.sun_family = AF_UNIX;
    Require(path.size() < sizeof(address.sun_path), "supervisor path too long");
    std::memcpy(address.sun_path, path.c_str(), path.size() + 1);
    Require(::connect(fd.Get(), reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0,
            "supervisor connect failed");
    std::string body, reason, reply;
    Require(SessionSupervisorProtocol::EncodeRequest(request, body, reason), reason);
    Require(TypedToolProtocol::WriteFrame(fd.Get(), body, 5000, reason), reason);
    Require(TypedToolProtocol::ReadFrame(fd.Get(), 32768, 5000, reply, reason), reason);
    SessionSupervisorResult result;
    Require(SessionSupervisorProtocol::DecodeResult(reply, result, reason), reason);
    return result;
}
// Fixture-only extraction of ASCII identifiers from an already decoded response
// emitted by the real service. It is not an SDK JSON parser or authorization gate.
std::string ServiceString(const std::string& json, const std::string& key) {
    const std::string prefix = "\"" + key + "\":\"";
    const auto found = json.find(prefix);
    Require(found != std::string::npos && json.find(prefix, found + prefix.size()) == std::string::npos,
            "missing or repeated service field: " + key + " in " + json);
    const auto begin = found + prefix.size(), end = json.find('"', begin);
    Require(end != std::string::npos && end > begin, "empty service field: " + key);
    const auto value = json.substr(begin, end - begin);
    Require(value.find('\\') == std::string::npos, "escaped service identifier in fixture");
    return value;
}
InstrumentRef Contract() {
    InstrumentRef value; value.symbol = "EUR"; value.currency = "USD";
    value.secType = "CASH"; value.exchange = "SIM"; return value;
}
struct Authorization { std::string commandId, permit; };
Authorization Preview(const NativeStrategyClient& client, const PreparedOrder& order, const std::string& id) {
    NativeToolClientResult result; std::string reason;
    Require(client.Preview(order, id, result, reason), "preview transport: " + reason);
    Require(result.envelope.status == "ok", "preview rejected: " + result.responseJson);
    Authorization auth{ServiceString(result.envelope.payloadJson, "command_id"),
                       ServiceString(result.envelope.payloadJson, "preview_permit")};
    Require(TradingToolWireContract::IsCanonicalCommandId(auth.commandId), "service ID is not canonical");
    return auth;
}
class Fixture {
public:
    TemporaryRoot root;
    ExecutionServiceRuntimeConfig executionConfig;
    ExecutionGatewayRuntimeConfig gatewayConfig;
    AgentOsRuntimeConfig agentConfig;
    ToolGatewaySessionPolicy policy;
    std::unique_ptr<ExecutionServiceRuntimeComposition> execution;
    std::unique_ptr<ToolGatewayRuntimeComposition> gateway;
    const std::string token = "research-execution-test-token-00000001";
    const std::string agent = "research-e2e";
    const std::string session = "research-session";
    std::uint64_t leaseGeneration = 0;
    Fixture() {
        // A skipped root test must not turn this acceptance claim green.
        Require(::geteuid() != 0, "native Execution acceptance must run as a non-root user");
        const std::string state = root.path + "/execution";
        const std::string lockDirectory = root.path + "/cleanup";
        Require(::mkdir(state.c_str(), 0700) == 0 && ::mkdir(lockDirectory.c_str(), 0711) == 0,
                "private state creation failed");
        Require(::chmod(lockDirectory.c_str(), 0711) == 0, "cleanup directory mode failed");
        WritePrivateFile(root.path + "/hepta-execution-fence", "HFC1\nfencing_token=1\ngeneration=1\n", 0400);
        WritePrivateFile(root.path + "/lease-key", std::string(32, 'T'), 0400);
        WritePrivateFile(lockDirectory + "/lock", "", 0644);
        executionConfig.mode = ExecutionServiceRuntimeMode::Simulator;
        executionConfig.allowedGatewayUids.insert(static_cast<std::uint32_t>(::geteuid()));
        executionConfig.gatewayContextBinding.agentId = agent;
        executionConfig.gatewayContextBinding.account = "SIM";
        executionConfig.gatewayContextBinding.venue = "SIMULATOR";
        executionConfig.gatewayContextBinding.executionDomain = "SIM:research-e2e";
        executionConfig.stateDirectory = state;
        executionConfig.journalPath = state + "/oms-journal.jsonl";
        executionConfig.fenceCredentialPath = root.path + "/hepta-execution-fence";
        executionConfig.simulatorQuoteRefreshIntervalMs = 20;
        gatewayConfig.mode = ExecutionGatewayMode::Simulator;
        gatewayConfig.executionSocket = root.path + "/execution.sock";
        gatewayConfig.eventSocket = root.path + "/events.sock";
        gatewayConfig.executionServiceUid = static_cast<std::uint32_t>(::geteuid());
        gatewayConfig.executionServiceUidConfigured = true;
        gatewayConfig.mutationToolsEnabled = true;
        gatewayConfig.ioTimeoutMs = 1000;
        agentConfig.toolSocket = root.path + "/tools.sock";
        agentConfig.supervisorSocket = root.path + "/supervisor.sock";
        agentConfig.agentUid = agentConfig.supervisorUid = static_cast<std::uint32_t>(::geteuid());
        agentConfig.supervisorAuditJournalPath = root.path + "/audit.jsonl";
        agentConfig.supervisorLeaseStorePath = root.path + "/leases.enc";
        agentConfig.supervisorLeaseKeyPath = root.path + "/lease-key";
        agentConfig.supervisorLeaseCleanupLockPath = lockDirectory + "/lock";
        agentConfig.supervisorLeaseCleanupLockUid = static_cast<std::uint32_t>(::geteuid());
        agentConfig.supervisorLeaseCleanupLockGid = static_cast<std::uint32_t>(::getegid());
        std::map<std::string, std::string> values{
            {"HEPTA_TOOL_AGENT_ID", agent}, {"HEPTA_TOOL_ACCOUNT", "SIM"},
            {"HEPTA_EXECUTION_DOMAIN_ID", "SIM:research-e2e"},
            {"HEPTA_TOOL_SESSION_TEMPLATES", "watch,paper"},
            {"HEPTA_TOOL_CONTRACT_BINDINGS", "EUR.USD|EUR|CASH|SIM|USD"},
            {"HEPTA_TOOL_MAX_ORDER_QTY", "1000"},
            {"HEPTA_TOOL_MAX_TRADE_CALLS_PER_MIN", "1000"}};
        std::string reason;
        Require(ToolGatewaySessionPolicy::FromValues(values, gatewayConfig, agentConfig, policy, reason), reason);
        StartExecution();
        gateway.reset(new ToolGatewayRuntimeComposition(gatewayConfig, agentConfig, policy));
        // C++ test seam only: one unprivileged test identity provisions the
        // local PAPER-template session. No production/configuration bypass.
        gateway->SetRootCustodianUidForTests(static_cast<std::uint32_t>(::geteuid()));
        if (!gateway->Start(reason)) throw std::runtime_error("real Gateway start: " + reason);
        SessionSupervisorRequest request;
        request.templateId = "paper"; request.token = token;
        request.agentId = agent; request.sessionId = session;
        request.peerUid = static_cast<std::uint32_t>(::geteuid()); request.ttlMs = 600000;
        const auto provisioned = Supervise(agentConfig.supervisorSocket, request);
        Require(provisioned.accepted, "real supervisor denied session: " + provisioned.ReasonCode());
        leaseGeneration = provisioned.leaseGeneration;
    }
    void StartExecution() {
        Fd commands(Listen(gatewayConfig.executionSocket)), events(Listen(gatewayConfig.eventSocket));
        executionConfig.listenFd = commands.Get(); executionConfig.eventListenFd = events.Get();
        execution.reset(new ExecutionServiceRuntimeComposition(executionConfig));
        commands.Release(); events.Release(); // Ownership transferred to the runtime.
        std::string reason;
        if (!execution->Start(reason)) throw std::runtime_error("real Execution start: " + reason);
    }
    void RestartExecution() {
        execution->Stop(); execution.reset();
        Require(::unlink(gatewayConfig.executionSocket.c_str()) == 0 &&
                ::unlink(gatewayConfig.eventSocket.c_str()) == 0, "fixture socket cleanup failed");
        StartExecution();
    }
    void AwaitPosition(double expected, std::size_t active) {
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
        while (std::chrono::steady_clock::now() < deadline) {
            if (execution->Venue().Position("EUR.USD") == expected &&
                execution->Venue().ActiveOrderIds().size() == active) return;
            std::this_thread::sleep_for(std::chrono::milliseconds(2));
        }
        throw std::runtime_error("real simulator failed to settle the expected position/orders");
    }
};
void TestNativeExecutionLifecycle() {
    Fixture f;
    NativeToolClientConfig nativeConfig;
    nativeConfig.socketPath = f.agentConfig.toolSocket;
    nativeConfig.sessionToken = f.token; nativeConfig.timeoutMs = 5000;
    NativeToolClient native(nativeConfig);
    NativeStrategyClient client(native);
    const auto expiry = OmsJournal::NowEpochMs() + 120000;
    PreparedOrder marketable("EUR.USD", Contract(), "BUY", 10, 1.1002, 1.1001, expiry);
    NativeToolClientResult result; std::string reason;
    const auto start = std::chrono::steady_clock::now();
    const auto auth = Preview(client, marketable, "research-actual-preview-1");
    Require(client.Submit(marketable, auth.commandId, auth.permit, result, reason), reason);
    Require(result.envelope.status == "ok", "real submit rejected: " + result.responseJson);
    const long filledId = result.envelope.orderId;
    Require(filledId >= 0, "missing authoritative order ID");
    f.AwaitPosition(10, 0);
    Require(client.Submit(marketable, auth.commandId, auth.permit, result, reason), reason);
    Require(result.envelope.status == "duplicate" && result.envelope.orderId == filledId,
            "identical retry was not durably deduplicated: " + result.responseJson);
    PreparedOrder altered("EUR.USD", Contract(), "BUY", 11, 1.1002, 1.1001, expiry);
    Require(client.Submit(altered, auth.commandId, auth.permit, result, reason), reason);
    Require(result.envelope.status == "rejected", "changed payload reused a command identity");
    Require(f.execution->Venue().AdmittedOrderCount() == 1, "retry or conflict sent another order");

    PreparedOrder resting("EUR.USD", Contract(), "BUY", 7, 1.1000, 1.1001, expiry);
    const auto restingAuth = Preview(client, resting, "research-actual-preview-2");
    Require(client.Submit(resting, restingAuth.commandId, restingAuth.permit, result, reason), reason);
    Require(result.envelope.status == "ok", "resting order not accepted: " + result.responseJson);
    const long restingId = result.envelope.orderId;
    f.AwaitPosition(10, 1);
    PreparedCancellation cancel(restingId);
    Require(client.Cancel(cancel, "research-actual-cancel-1", result, reason), reason);
    Require(result.envelope.status == "ok", "real cancel rejected: " + result.responseJson);
    f.AwaitPosition(10, 0);
    Require(client.Cancel(cancel, "research-actual-cancel-1", result, reason), reason);
    Require(result.envelope.status == "duplicate", "cancel retry was not idempotent");

    // Existing simulator has no authoritative flatten implementation. The real
    // service must reject; the wrapper must not synthesize an opposite-side order.
    PreparedFlatten flatten("EUR.USD");
    Require(client.PreviewFlatten(flatten, "research-actual-preview-flatten", result, reason), reason);
    Require(result.envelope.status != "ok", "unsupported simulator flatten advertised approval");
    Require(f.execution->Venue().AdmittedOrderCount() == 2, "unsupported flatten sent an order");

    // A normal WATCH session cannot inherit the PAPER proposal or permit.
    SessionSupervisorRequest watch;
    watch.templateId = "watch"; watch.token = "research-watch-test-token-00000001";
    watch.agentId = f.agent; watch.sessionId = "research-watch-session";
    watch.peerUid = static_cast<std::uint32_t>(::geteuid()); watch.ttlMs = 600000;
    const auto watched = Supervise(f.agentConfig.supervisorSocket, watch);
    Require(watched.accepted, "WATCH session provisioning failed");
    auto watchConfig = nativeConfig; watchConfig.sessionToken = watch.token;
    NativeToolClient watchNative(watchConfig); NativeStrategyClient watchClient(watchNative);
    const bool watchDelivered = watchClient.Submit(marketable, auth.commandId, auth.permit, result, reason);
    Require(!watchDelivered || result.envelope.status == "permission_denied" ||
            result.envelope.status == "invalid_tool", "WATCH client inherited mutation authority");
    Require(f.execution->Venue().AdmittedOrderCount() == 2, "WATCH client sent an order");

    PreparedOrder lostReplyOrder("EUR.USD", Contract(), "BUY", 3, 1.1002, 1.1001, expiry);
    const auto lostAuth = Preview(client, lostReplyOrder, "research-lost-reply-preview");
    {
        const std::string proxyPath = f.root.path + "/drop.sock";
        DropReplyProxy proxy(proxyPath, f.agentConfig.toolSocket, lostAuth.commandId);
        auto proxyConfig = nativeConfig; proxyConfig.socketPath = proxyPath;
        NativeToolClient proxyNative(proxyConfig); NativeStrategyClient proxyClient(proxyNative);
        result.envelope.status = "ok"; result.envelope.orderId = 12345;
        Require(!proxyClient.Submit(lostReplyOrder, lostAuth.commandId, lostAuth.permit, result, reason),
                "dropped response was reported as delivered");
        Require(result.envelope.status.empty() && result.envelope.orderId == -1,
                "old success survived a lost response");
        proxy.CheckOneAttempt();
        f.AwaitPosition(13, 0);
        Require(f.execution->Venue().AdmittedOrderCount() == 3, "lost reply was automatically resubmitted");
        Require(client.Submit(lostReplyOrder, lostAuth.commandId, lostAuth.permit, result, reason), reason);
        Require(result.envelope.status == "duplicate", "explicit lost-reply retry created another order");
    }

    const std::string beforeEpoch = f.execution->ServiceEpoch();
    f.RestartExecution();
    Require(f.execution->ServiceEpoch() != beforeEpoch, "execution restart retained its incarnation");
    f.AwaitPosition(13, 0);
    // Retry only the idempotent read while the Gateway invalidates its previous
    // service identity. Never retry a mutation with an invented command ID.
    bool known = false;
    for (unsigned int attempt = 0; attempt < 5 && !known; ++attempt) {
        const bool delivered = client.Status(auth.commandId, "research-actual-status-" +
            std::to_string(attempt), result, reason);
        known = delivered && result.envelope.status == "ok";
    }
    Require(known && result.envelope.payloadJson.find(auth.commandId) != std::string::npos,
            "durable status unavailable after restart: " + reason + result.responseJson);
    Require(client.Submit(marketable, auth.commandId, auth.permit, result, reason), reason);
    Require(result.envelope.status == "duplicate" && result.envelope.orderId == filledId,
            "service restart lost durable command identity: " + result.responseJson);
    Require(f.execution->Venue().AdmittedOrderCount() == 3, "restart reset the admission ledger");
    Require(client.Submit(lostReplyOrder, lostAuth.commandId, lostAuth.permit, result, reason), reason);
    Require(result.envelope.status == "duplicate", "lost-reply command was forgotten on restart");
    SessionSupervisorRequest revoke;
    revoke.operation = SessionSupervisorOperation::Revoke; revoke.token = f.token;
    revoke.expectedGeneration = f.leaseGeneration;
    const auto revoked = Supervise(f.agentConfig.supervisorSocket, revoke);
    Require(revoked.accepted, "real session revocation failed: " + revoked.ReasonCode());
    Require(client.Submit(marketable, auth.commandId, auth.permit, result, reason), reason);
    Require(result.envelope.status == "permission_denied", "revoked session reached Execution");
    Require(f.execution->Venue().AdmittedOrderCount() == 3, "revoked session submitted an order");

    f.gateway->Stop(); f.gateway.reset(); f.execution->Stop();
    std::map<std::string, unsigned int> placeAttempts, cancelAttempts;
    OmsJournal journal;
    Require(journal.Init(f.executionConfig.journalPath), "cannot inspect real OMS journal");
    Require(journal.Replay([&](const OmsJournalEvent& event) {
        if (event.eventType == "place_send_attempt") ++placeAttempts[event.reqId];
        if (event.eventType == "cancel_send_attempt") ++cancelAttempts[event.reqId];
    }) > 0, "OMS replay was empty or invalid");
    Require(placeAttempts[auth.commandId] == 1 && placeAttempts[restingAuth.commandId] == 1 &&
            placeAttempts[lostAuth.commandId] == 1 && placeAttempts.size() == 3 &&
            cancelAttempts["research-actual-cancel-1"] == 1 &&
            cancelAttempts.size() == 1, "journal contains duplicate or bypass sends");
    std::uint64_t auditRecords = 0;
    Require(SessionSupervisorAuditJournal::Verify(f.agentConfig.supervisorAuditJournalPath, auditRecords, reason),
            "real Gateway audit verification: " + reason);
    Require(auditRecords > 0, "no durable Gateway decisions recorded");
    std::cout << "native_execution_lifecycle_us=" <<
        std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now() - start).count()
        << " orders=3 fills=2 cancels=1 lost_reply=1 durable_restart=1 revoked=1 audit_records=" << auditRecords
        << " (local simulator; not broker latency or process-isolation qualification)\n";
}
}
int main() {
    try { TestNativeExecutionLifecycle(); std::cout << "PASS real Native/Gateway/Execution lifecycle\n"; return 0; }
    catch (const std::exception& error) { std::cerr << "FAIL: " << error.what() << '\n'; return 1; }
}
