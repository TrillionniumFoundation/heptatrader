// Real canonical services over local sockets. Synthetic simulator only; this is
// not a broker campaign or proof of installed, different-UID process isolation.
#include "hepta/research/native_strategy_client.h"
#include <execution/execution_service_runtime_composition.h>
#include <execution/execution_coordinator.h>
#include <tool_host/tool_gateway_runtime_composition.h>
#include <tool_host/session_supervisor_audit_journal.h>
#include <tool_host/session_supervisor_protocol.h>
#include <tools/trading_tool_wire_contract.h>
#include <atomic>
#include <array>
#include <vector>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstring>
#include <dirent.h>
#include <fcntl.h>
#include <fstream>
#include <functional>
#include <locale>
#include <signal.h>
#include <spawn.h>
#include <sstream>
#include <sys/wait.h>
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
// Drop exactly one accepted mutation reply between the real Native client and
// the real Gateway. All request/response bytes otherwise pass unchanged. No
// synthetic authority, risk decision, order ID or callback is introduced.
class DropReplyProxy {
public:
    DropReplyProxy(const std::string& path, const std::string& upstream,
                   const std::string& commandId, const std::function<void()>& beforeDrop = {},
                   const std::string& callName = "trade.place_order")
        : listener_(Listen(path)), upstream_(upstream), commandId_(commandId),
          beforeDrop_(beforeDrop), callName_(callName) {
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
                if (request.call.name == callName_ && request.toolCallId == commandId_) {
                    ++attempts_;
                    if (!dropped_.load()) {
                        TypedToolResultEnvelope result;
                        Require(TypedToolProtocol::DecodeResultEnvelope(response, result, reason), reason);
                        Require(result.status == "ok", "proxy target was not actually accepted");
                        if (beforeDrop_) beforeDrop_();
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
    std::function<void()> beforeDrop_;
    std::string callName_;
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
    TypedPreviewAuthorization decoded;
    Require(client.PreviewAuthorized(order, id, decoded, result, reason), "typed preview: " + reason);
    Require(result.envelope.status == "ok", "preview rejected: " + result.responseJson);
    // Independently compare the typed SDK values with the real service payload.
    // The fixture extractor is an oracle only, no longer a client requirement.
    Require(decoded.toolName == "risk.preview_order" &&
            decoded.commandId == ServiceString(result.envelope.payloadJson, "command_id") &&
            decoded.previewPermit == ServiceString(result.envelope.payloadJson, "preview_permit") &&
            decoded.serviceEpoch == ServiceString(result.envelope.payloadJson, "service_epoch") &&
            decoded.permitExpiresAtMs > 0 && decoded.serviceFencingGeneration > 0,
            "typed preview differs from the real Execution response");
    Authorization auth{decoded.commandId, decoded.previewPermit};
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
    explicit Fixture(bool startRuntime = true) {
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
        if (startRuntime) { StartExecution(); StartGateway(); }
    }
    void StartGateway() {
        std::string reason;
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
    TypedPreviewAuthorization unsupported;
    unsupported.commandId = auth.commandId; unsupported.previewPermit = auth.permit;
    Require(!client.PreviewAuthorized(flatten, "research-actual-typed-flatten", unsupported, result, reason) &&
            unsupported.commandId.empty() && unsupported.previewPermit.empty() &&
            !result.responseJson.empty() && result.envelope.status != "ok" && !reason.empty(),
            "typed unsupported flatten retained authority or lost its rejection");
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

// The child is exec'd, not a forked copy of a multithreaded Gateway. Descriptor
// 3 is a private test-only observation/control socket; every inherited descriptor
// above it is closed before exec. This helper has no installed entry point and
// exposes no new production mutation method. Order mutations still use HTT1.
int RunExecutionChild(const std::string& root) {
    Require(::geteuid() != 0, "Execution child must be unprivileged");
    struct stat info;
    Require(::lstat(root.c_str(), &info) == 0 && S_ISDIR(info.st_mode) &&
            info.st_uid == ::geteuid() && (info.st_mode & 0777) == 0700,
            "Execution child root is not a private owned directory");
    Fd control(3), commands(Listen(root + "/execution.sock")), events(Listen(root + "/events.sock"));
    ExecutionServiceRuntimeConfig config;
    config.mode = ExecutionServiceRuntimeMode::Simulator;
    config.allowedGatewayUids.insert(static_cast<std::uint32_t>(::geteuid()));
    config.gatewayContextBinding.agentId = "research-e2e";
    config.gatewayContextBinding.account = "SIM";
    config.gatewayContextBinding.venue = "SIMULATOR";
    config.gatewayContextBinding.executionDomain = "SIM:research-e2e";
    config.stateDirectory = root + "/execution";
    config.journalPath = config.stateDirectory + "/oms-journal.jsonl";
    config.fenceCredentialPath = root + "/hepta-execution-fence";
    config.simulatorQuoteRefreshIntervalMs = 20;
    config.listenFd = commands.Get(); config.eventListenFd = events.Get();
    ExecutionServiceRuntimeComposition runtime(config);
    commands.Release(); events.Release();
    std::string reason;
    Require(runtime.Start(reason), "Execution child start: " + reason);
    Require(TypedToolProtocol::WriteFrame(control.Get(), "READY " + runtime.ServiceEpoch(), 5000, reason), reason);
    for (;;) {
        std::string request;
        Require(TypedToolProtocol::ReadFrame(control.Get(), 64, 15000, request, reason),
                "Execution child control: " + reason);
        if (request == "STOP") {
            runtime.Stop();
            Require(TypedToolProtocol::WriteFrame(control.Get(), "STOPPED", 5000, reason), reason);
            return 0; // Normal child exit runs destructors and sanitizer leak checks.
        }
        Require(request == "OBSERVE", "unsupported test child control command");
        std::size_t fills = 0, cancellations = 0;
        for (const auto& terminal : runtime.Venue().TerminalOrderStatuses()) {
            ExecutionOrderOwner owner;
            // Owner removal happens only after the status and terminal owner
            // records are durably appended. Merely observing a position is not
            // a durable-fill barrier: the event sink runs outside the venue lock.
            if (runtime.Coordinator().GetOrderOwner(terminal.first, owner)) continue;
            if (terminal.second == "Filled") ++fills;
            if (terminal.second == "Cancelled") ++cancellations;
        }
        std::ostringstream reply;
        reply.imbue(std::locale::classic());
        reply << runtime.Venue().AdmittedOrderCount() << ' '
              << runtime.Venue().Position("EUR.USD") << ' '
              << runtime.Venue().ActiveOrderIds().size() << ' ' << fills << ' ' << cancellations;
        Require(TypedToolProtocol::WriteFrame(control.Get(), reply.str(), 5000, reason), reason);
    }
}
class ExecutionProcess {
public:
    struct Observation {
        std::size_t admitted = 0, active = 0, durableFills = 0, durableCancels = 0;
        double position = 0.0;
    };
    explicit ExecutionProcess(const std::string& root) : root_(root) { Start(); }
    ~ExecutionProcess() { AbortAndReap(); }
    ExecutionProcess(const ExecutionProcess&) = delete;
    ExecutionProcess& operator=(const ExecutionProcess&) = delete;
    const std::string& Epoch() const { return epoch_; }
    unsigned int Crashes() const { return crashes_; }
    void Crash() {
        Require(pid_ > 0 && ::kill(pid_, SIGKILL) == 0, "cannot SIGKILL owned Execution child");
        const int status = Wait();
        Require(WIFSIGNALED(status) && WTERMSIG(status) == SIGKILL,
                "Execution did not die by SIGKILL");
        control_.reset(); ++crashes_;
    }
    void Restart() {
        Require(pid_ == -1, "cannot restart a live Execution child");
        for (const auto& name : {"execution.sock", "events.sock"}) {
            const auto path = root_ + "/" + name;
            struct stat info;
            Require(::lstat(path.c_str(), &info) == 0 && S_ISSOCK(info.st_mode) &&
                    info.st_uid == ::geteuid() && ::unlink(path.c_str()) == 0,
                    "cannot remove killed child's private socket");
        }
        const auto oldEpoch = epoch_;
        Start();
        Require(epoch_ != oldEpoch, "exec restart reused a service incarnation");
    }
    Observation Observe() {
        std::string response = Exchange("OBSERVE");
        std::istringstream input(response);
        input.imbue(std::locale::classic());
        Observation value;
        Require(static_cast<bool>(input >> value.admitted >> value.position >> value.active >>
                value.durableFills >> value.durableCancels),
                "malformed child observation");
        input >> std::ws;
        Require(input.eof() && std::isfinite(value.position), "invalid child observation tail");
        return value;
    }
    void Await(std::size_t admitted, double position, std::size_t active,
               bool requireTerminals = false, std::size_t fills = 0, std::size_t cancels = 0) {
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
        Observation last;
        do {
            const auto value = Observe();
            last = value;
            if (value.admitted == admitted && value.position == position && value.active == active &&
                (!requireTerminals || (value.durableFills == fills && value.durableCancels == cancels))) return;
            std::this_thread::sleep_for(std::chrono::milliseconds(2));
        } while (std::chrono::steady_clock::now() < deadline);
        throw std::runtime_error("crashed/recovered Execution state did not converge: expected " +
            std::to_string(admitted) + "/" + std::to_string(position) + "/" + std::to_string(active) +
            " observed " + std::to_string(last.admitted) + "/" + std::to_string(last.position) + "/" + std::to_string(last.active));
    }
    void Stop() {
        Require(Exchange("STOP") == "STOPPED", "Execution child did not stop cleanly");
        const int status = Wait();
        control_.reset();
        Require(WIFEXITED(status) && WEXITSTATUS(status) == 0,
                "Execution child/sanitizer reported a nonzero exit");
    }
private:
    void AbortAndReap() noexcept {
        if (pid_ > 0) {
            ::kill(pid_, SIGKILL);
            int status = 0;
            while (::waitpid(pid_, &status, 0) < 0 && errno == EINTR) {}
            pid_ = -1;
        }
    }
    int Wait() {
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
        do {
            int status = 0;
            const auto done = ::waitpid(pid_, &status, WNOHANG);
            if (done == pid_) { pid_ = -1; return status; }
            Require(done == 0 || (done < 0 && errno == EINTR), "Execution child wait failed");
            std::this_thread::sleep_for(std::chrono::milliseconds(2));
        } while (std::chrono::steady_clock::now() < deadline);
        throw std::runtime_error("Execution child exit timed out");
    }
    std::string Exchange(const std::string& request) {
        Require(pid_ > 0 && control_.get(), "Execution child is not running");
        std::string reason, response;
        Require(TypedToolProtocol::WriteFrame(control_->Get(), request, 5000, reason), reason);
        Require(TypedToolProtocol::ReadFrame(control_->Get(), 512, 5000, response, reason), reason);
        return response;
    }
    void Start() {
        int sockets[2];
        Require(::socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, sockets) == 0,
                "Execution child socketpair failed");
        Fd parent(sockets[0]), child(sockets[1]);
        posix_spawn_file_actions_t actions;
        Require(::posix_spawn_file_actions_init(&actions) == 0, "spawn actions init failed");
        const int duplicate = ::posix_spawn_file_actions_adddup2(&actions, child.Get(), 3);
        const int closeRest = ::posix_spawn_file_actions_addclosefrom_np(&actions, 4);
        if (duplicate != 0 || closeRest != 0) {
            ::posix_spawn_file_actions_destroy(&actions);
            throw std::runtime_error("spawn descriptor isolation setup failed");
        }
        char executable[] = "/proc/self/exe";
        char mode[] = "--execution-child";
        char* arguments[] = {executable, mode, const_cast<char*>(root_.c_str()), nullptr};
        const int error = ::posix_spawn(&pid_, executable, &actions, nullptr, arguments, ::environ);
        ::posix_spawn_file_actions_destroy(&actions);
        Require(error == 0, "Execution child exec failed: " + std::to_string(error));
        ::close(child.Release()); // Parent must not keep the child endpoint alive.
        // A constructor failure must not leave an unsupervised child behind.
        try {
            control_.reset(new Fd(parent.Release()));
            std::string ready, reason;
            Require(TypedToolProtocol::ReadFrame(control_->Get(), 512, 5000, ready, reason),
                    "Execution child readiness: " + reason);
            Require(ready.compare(0, 6, "READY ") == 0 && ready.size() > 6,
                    "Execution child did not prove startup");
            epoch_ = ready.substr(6);
        } catch (...) { AbortAndReap(); throw; }
    }
    std::string root_, epoch_;
    pid_t pid_ = -1;
    std::unique_ptr<Fd> control_;
    unsigned int crashes_ = 0;
};
void AwaitRecoveredStatus(const NativeStrategyClient& client, const std::string& commandId) {
    NativeToolClientResult result; std::string reason;
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
    unsigned int query = 0;
    do {
        if (client.Status(commandId, "crash-status-" + std::to_string(++query), result, reason) &&
            result.envelope.status == "ok" &&
            result.envelope.payloadJson.find(commandId) != std::string::npos) return;
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    } while (std::chrono::steady_clock::now() < deadline);
    throw std::runtime_error("status not recovered after SIGKILL: " + reason + result.responseJson);
}
void TestNativeExecutionProcessCrashes() {
    Fixture f(false);
    ExecutionProcess execution(f.root.path);
    f.StartGateway();
    NativeToolClientConfig config;
    config.socketPath = f.agentConfig.toolSocket;
    config.sessionToken = f.token; config.timeoutMs = 5000;
    NativeToolClient native(config); NativeStrategyClient client(native);
    const auto expiry = OmsJournal::NowEpochMs() + 120000;
    PreparedOrder filled("EUR.USD", Contract(), "BUY", 10, 1.1002, 1.1001, expiry);
    PreparedOrder unsent("EUR.USD", Contract(), "BUY", 9, 1.1002, 1.1001, expiry);
    const auto auth = Preview(client, filled, "crash-placement-preview");
    const auto unusedAuth = Preview(client, unsent, "crash-unused-preview");
    NativeToolClientResult result; std::string reason;
    {
        DropReplyProxy proxy(f.root.path + "/crash-place.sock", config.socketPath,
                             auth.commandId, [&]() {
                                 execution.Await(1, 10, 0, true, 1, 0);
                                 execution.Crash();
                             });
        auto droppedConfig = config; droppedConfig.socketPath = f.root.path + "/crash-place.sock";
        NativeToolClient droppedNative(droppedConfig); NativeStrategyClient dropped(droppedNative);
        result.envelope.status = "ok"; result.envelope.orderId = 12345;
        Require(!dropped.Submit(filled, auth.commandId, auth.permit, result, reason),
                "SIGKILL/lost placement reply was reported delivered");
        Require(result.envelope.status.empty() && result.envelope.orderId == -1,
                "stale placement success survived a crash");
        proxy.CheckOneAttempt();
        Require(execution.Crashes() == 1, "placement crash was not reaped");
    }
    // Gateway remains alive. A dead service cannot fabricate acceptance. This
    // is one explicit same-ID attempt, not a client retry loop or a new order.
    const bool deliveredWhileDown = client.Submit(filled, auth.commandId, auth.permit, result, reason);
    Require(!deliveredWhileDown || (result.envelope.status != "ok" &&
            result.envelope.status != "duplicate"), "dead Execution fabricated acceptance");
    execution.Restart();
    execution.Await(1, 10, 0);
    AwaitRecoveredStatus(client, auth.commandId);
    Require(client.Submit(filled, auth.commandId, auth.permit, result, reason) &&
            result.envelope.status == "duplicate" && result.envelope.orderId >= 0,
            "lost placement identity did not survive SIGKILL: " + reason + result.responseJson);
    const long filledId = result.envelope.orderId;
    PreparedOrder changed("EUR.USD", Contract(), "BUY", 11, 1.1002, 1.1001, expiry);
    Require(client.Submit(changed, auth.commandId, auth.permit, result, reason) &&
            result.envelope.status == "rejected", "crash allowed changed-payload ID reuse");
    Require(client.Submit(unsent, unusedAuth.commandId, unusedAuth.permit, result, reason) &&
            result.envelope.status == "rejected", "unused pre-crash permit survived its service epoch");
    execution.Await(1, 10, 0);

    PreparedOrder resting("EUR.USD", Contract(), "BUY", 7, 1.1000, 1.1001, expiry);
    const auto restingAuth = Preview(client, resting, "crash-resting-preview");
    Require(client.Submit(resting, restingAuth.commandId, restingAuth.permit, result, reason) &&
            result.envelope.status == "ok", "post-crash resting order rejected: " + result.responseJson);
    const long restingId = result.envelope.orderId;
    Require(restingId >= 0 && restingId != filledId, "recovered venue reused an order ID");
    execution.Await(2, 10, 1);
    const std::string cancelId = "crash-cancel-command";
    PreparedCancellation cancellation(restingId);
    {
        DropReplyProxy proxy(f.root.path + "/crash-cancel.sock", config.socketPath,
                             cancelId, [&]() {
                                 execution.Await(2, 10, 0, true, 0, 1);
                                 execution.Crash();
                             }, "trade.cancel_order");
        auto droppedConfig = config; droppedConfig.socketPath = f.root.path + "/crash-cancel.sock";
        NativeToolClient droppedNative(droppedConfig); NativeStrategyClient dropped(droppedNative);
        result.envelope.status = "ok"; result.envelope.orderId = restingId;
        Require(!dropped.Cancel(cancellation, cancelId, result, reason),
                "SIGKILL/lost cancel reply was reported delivered");
        Require(result.envelope.status.empty() && result.envelope.orderId == -1,
                "stale cancel success survived a crash");
        proxy.CheckOneAttempt();
        Require(execution.Crashes() == 2, "cancel crash was not reaped");
    }
    execution.Restart();
    execution.Await(2, 10, 0);
    AwaitRecoveredStatus(client, cancelId);
    Require(client.Cancel(cancellation, cancelId, result, reason) &&
            result.envelope.status == "duplicate", "cancel identity did not survive SIGKILL");
    Require(client.Submit(resting, restingAuth.commandId, restingAuth.permit, result, reason) &&
            result.envelope.status == "duplicate" && result.envelope.orderId == restingId,
            "crash/retry resurrected a cancelled order");
    Require(client.Submit(filled, auth.commandId, auth.permit, result, reason) &&
            result.envelope.status == "duplicate" && result.envelope.orderId == filledId,
            "second crash forgot the filled order identity");
    execution.Await(2, 10, 0);

    // A distinct window: accepted, nonmarketable and NOT filled. The canonical
    // simulator deliberately retires in-memory active orders on restart; it must
    // neither invent a fill nor recreate a venue order on a same-ID retry.
    PreparedOrder unfilled("EUR.USD", Contract(), "BUY", 4, 1.1000, 1.1001, expiry);
    const auto unfilledAuth = Preview(client, unfilled, "crash-unfilled-preview");
    {
        DropReplyProxy proxy(f.root.path + "/crash-unfilled.sock", config.socketPath,
                             unfilledAuth.commandId, [&]() {
                                 execution.Await(3, 10, 1);
                                 execution.Crash();
                             });
        auto droppedConfig = config; droppedConfig.socketPath = f.root.path + "/crash-unfilled.sock";
        NativeToolClient droppedNative(droppedConfig); NativeStrategyClient dropped(droppedNative);
        Require(!dropped.Submit(unfilled, unfilledAuth.commandId, unfilledAuth.permit, result, reason),
                "lost unfilled-order reply was reported delivered");
        proxy.CheckOneAttempt();
        Require(execution.Crashes() == 3, "unfilled-order crash was not reaped");
    }
    execution.Restart();
    execution.Await(3, 10, 0);
    AwaitRecoveredStatus(client, unfilledAuth.commandId);
    Require(client.Submit(unfilled, unfilledAuth.commandId, unfilledAuth.permit, result, reason) &&
            result.envelope.status == "duplicate", "retry revived an unfilled pre-crash order");
    execution.Await(3, 10, 0);
    f.gateway->Stop(); f.gateway.reset();
    execution.Stop();
    std::map<std::string, unsigned int> placeAttempts, cancelAttempts;
    OmsJournal journal;
    Require(journal.Init(f.executionConfig.journalPath), "cannot open post-crash journal");
    Require(journal.Replay([&](const OmsJournalEvent& event) {
        if (event.eventType == "place_send_attempt") ++placeAttempts[event.reqId];
        if (event.eventType == "cancel_send_attempt") ++cancelAttempts[event.reqId];
    }) > 0, "post-crash journal replay failed");
    Require(placeAttempts.size() == 3 && placeAttempts[unfilledAuth.commandId] == 1 &&
            placeAttempts[auth.commandId] == 1 &&
            placeAttempts[restingAuth.commandId] == 1 && cancelAttempts.size() == 1 &&
            cancelAttempts[cancelId] == 1, "SIGKILL produced a duplicate/bypass venue send");
    std::uint64_t records = 0;
    Require(SessionSupervisorAuditJournal::Verify(f.agentConfig.supervisorAuditJournalPath, records, reason) &&
            records > 0, "post-crash Gateway audit did not verify: " + reason);
    std::cout << "native_execution_process_crashes=3 orders=3 fills=1 cancels=1"
              << " lost_replies=3 unfilled_retired=1 unused_permit_rejected=1 audit_records=" << records
              << " (SIGKILL/exec simulator; not power-loss, different-UID or broker qualification)\n";
}
int RunOutboxChild(const std::string& root, const std::string& socket,
                   const std::string& tokenFile, const std::string& operation,
                   const std::string& commandId) {
    Fd control(3);
    NativeToolClientConfig config; config.socketPath = socket;
    config.tokenFile = tokenFile; config.timeoutMs = 5000;
    NativeToolClient native(config); NativeStrategyClient client(native);
    const std::string directory = root + "/outbox";
    std::string reply, reason;
    if (operation == "prepare") {
        PreparedOrder order("EUR.USD", Contract(), "BUY", 10, 1.1002, 1.1001,
                            OmsJournal::NowEpochMs() + 120000);
        const auto auth = Preview(client, order, "outbox-child-preview");
        Require(client.Persist(directory, order, auth.commandId, auth.permit, reason),
                "child persist: " + reason);
        reply = auth.commandId;
    } else {
        Require(operation == "submit" || operation == "submit-hold", "outbox child mode invalid");
        NativeToolClientResult result;
        Require(client.SubmitStored(directory, commandId, result, reason),
                "child stored call: " + reason);
        reply = result.responseJson;
    }
    Require(TypedToolProtocol::WriteFrame(control.Get(), reply, 5000, reason), reason);
    if (operation == "prepare" || operation == "submit-hold") {
        // A test-owned pause after real durable preparation or delivery. No
        // acknowledgement/sent marker is written; the parent SIGKILLs us.
        for (;;) ::pause();
    }
    return 0;
}
class OutboxWorker {
public:
    OutboxWorker(const std::string& root, const std::string& socket,
                 const std::string& token, const std::string& operation,
                 const std::string& id = "-") {
        int sockets[2];
        Require(::socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, sockets) == 0,
                "outbox child control pair");
        Fd parent(sockets[0]), child(sockets[1]);
        posix_spawn_file_actions_t actions;
        Require(::posix_spawn_file_actions_init(&actions) == 0, "outbox spawn init");
        const int dup = ::posix_spawn_file_actions_adddup2(&actions, child.Get(), 3);
        const int closeRest = ::posix_spawn_file_actions_addclosefrom_np(&actions, 4);
        if (dup != 0 || closeRest != 0) {
            ::posix_spawn_file_actions_destroy(&actions);
            throw std::runtime_error("outbox descriptor isolation");
        }
        char executable[] = "/proc/self/exe", mode[] = "--outbox-child";
        char* args[] = {executable, mode, const_cast<char*>(root.c_str()),
            const_cast<char*>(socket.c_str()), const_cast<char*>(token.c_str()),
            const_cast<char*>(operation.c_str()), const_cast<char*>(id.c_str()), nullptr};
        const int error = ::posix_spawn(&pid_, executable, &actions, nullptr, args, ::environ);
        ::posix_spawn_file_actions_destroy(&actions);
        Require(error == 0, "outbox child exec");
        control_.reset(new Fd(parent.Release()));
    }
    ~OutboxWorker() {
        if (pid_ > 0) {
            ::kill(pid_, SIGKILL);
            int status;
            while (::waitpid(pid_, &status, 0) < 0 && errno == EINTR) {}
        }
    }
    std::string Read() {
        std::string body, reason;
        Require(TypedToolProtocol::ReadFrame(control_->Get(), 1048576, 6000, body, reason),
                "outbox child reply: " + reason);
        return body;
    }
    void Crash() {
        Require(pid_ > 0 && ::kill(pid_, SIGKILL) == 0, "outbox child SIGKILL");
        const int status = Wait();
        Require(WIFSIGNALED(status) && WTERMSIG(status) == SIGKILL, "outbox child not killed");
    }
    void Finish() {
        const int status = Wait();
        Require(WIFEXITED(status) && WEXITSTATUS(status) == 0, "outbox child/sanitizer failure");
    }
private:
    int Wait() {
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
        do {
            int status = 0;
            const auto observed = ::waitpid(pid_, &status, WNOHANG);
            if (observed == pid_) { pid_ = -1; control_.reset(); return status; }
            Require(observed == 0 || (observed < 0 && errno == EINTR), "outbox child wait failed");
            std::this_thread::sleep_for(std::chrono::milliseconds(2));
        } while (std::chrono::steady_clock::now() < deadline);
        throw std::runtime_error("outbox child timeout");
    }
    pid_t pid_ = -1;
    std::unique_ptr<Fd> control_;
};
TypedToolResultEnvelope ReadOutboxWorker(OutboxWorker& worker) {
    TypedToolResultEnvelope result; std::string reason;
    Require(TypedToolProtocol::DecodeResultEnvelope(worker.Read(), result, reason), reason);
    return result;
}
void TestDurableClientProcessRecovery() {
    Fixture f(false);
    ExecutionProcess execution(f.root.path);
    f.StartGateway();
    const auto directory = f.root.path + "/outbox", tokenFile = f.root.path + "/client.token";
    Require(::mkdir(directory.c_str(), 0700) == 0, "outbox directory setup");
    WritePrivateFile(tokenFile, f.token, 0600);
    std::string command;
    {
        OutboxWorker preparer(f.root.path, f.agentConfig.toolSocket, tokenFile, "prepare");
        command = preparer.Read();
        Require(TradingToolWireContract::IsCanonicalCommandId(command), "child did not return service command ID");
        execution.Await(0, 0, 0);
        preparer.Crash(); // The original proposal and permit die with this address space.
    }
    long filledId = -1;
    {
        OutboxWorker sender(f.root.path, f.agentConfig.toolSocket, tokenFile, "submit-hold", command);
        const auto result = ReadOutboxWorker(sender);
        Require(result.status == "ok" && result.orderId >= 0, "fresh-process stored placement rejected");
        filledId = result.orderId;
        execution.Await(1, 10, 0, true, 1, 0);
        sender.Crash(); // No application acknowledgement persisted after the fill.
    }
    {
        OutboxWorker retry(f.root.path, f.agentConfig.toolSocket, tokenFile, "submit", command);
        const auto result = ReadOutboxWorker(retry);
        Require(result.status == "duplicate" && result.orderId == filledId, "client crash replay changed identity");
        retry.Finish();
    }
    NativeToolClientConfig config; config.socketPath = f.agentConfig.toolSocket;
    config.tokenFile = tokenFile; config.timeoutMs = 5000;
    NativeToolClient native(config); NativeStrategyClient client(native);
    execution.Crash(); execution.Restart(); execution.Await(1, 10, 0);
    AwaitRecoveredStatus(client, command);
    {
        OutboxWorker retry(f.root.path, f.agentConfig.toolSocket, tokenFile, "submit", command);
        const auto result = ReadOutboxWorker(retry);
        Require(result.status == "duplicate" && result.orderId == filledId, "service restart forgot stored identity");
        retry.Finish();
    }
    const auto expiry = OmsJournal::NowEpochMs() + 120000;
    PreparedOrder resting("EUR.USD", Contract(), "BUY", 3, 1.1000, 1.1001, expiry);
    const auto auth = Preview(client, resting, "outbox-resting-preview");
    std::string reason;
    Require(client.Persist(directory, resting, auth.commandId, auth.permit, reason), reason);
    long restingId = -1;
    {
        OutboxWorker sender(f.root.path, f.agentConfig.toolSocket, tokenFile, "submit", auth.commandId);
        const auto result = ReadOutboxWorker(sender);
        Require(result.status == "ok" && result.orderId >= 0, "stored resting placement rejected");
        restingId = result.orderId; sender.Finish();
    }
    execution.Await(2, 10, 1);
    const std::string cancelId = "outbox-runtime-cancel";
    Require(client.Persist(directory, PreparedCancellation(restingId), cancelId, reason), reason);
    {
        OutboxWorker sender(f.root.path, f.agentConfig.toolSocket, tokenFile, "submit-hold", cancelId);
        const auto result = ReadOutboxWorker(sender);
        Require(result.status == "ok", "stored cancellation rejected");
        execution.Await(2, 10, 0); sender.Crash();
    }
    {
        OutboxWorker retry(f.root.path, f.agentConfig.toolSocket, tokenFile, "submit", cancelId);
        const auto result = ReadOutboxWorker(retry);
        Require(result.status == "duplicate", "stored cancellation identity lost");
        retry.Finish();
    }
    // A syntactically valid persisted permit is not a service authorization.
    const std::string flattenId = "outbox-unauthorized-flatten";
    Require(client.Persist(directory, PreparedFlatten("EUR.USD"), flattenId,
                           "sha256:" + std::string(64, 'a'), reason), reason);
    {
        OutboxWorker sender(f.root.path, f.agentConfig.toolSocket, tokenFile, "submit", flattenId);
        const auto result = ReadOutboxWorker(sender);
        Require(result.status == "rejected", "outbox manufactured flatten authority");
        sender.Finish();
    }
    execution.Await(2, 10, 0);
    f.gateway->Stop(); f.gateway.reset(); execution.Stop();
    OmsJournal journal;
    Require(journal.Init(f.executionConfig.journalPath), "outbox journal verification open");
    std::map<std::string, unsigned int> sends, cancels;
    Require(journal.Replay([&](const OmsJournalEvent& event) {
        if (event.eventType == "place_send_attempt") ++sends[event.reqId];
        if (event.eventType == "cancel_send_attempt") ++cancels[event.reqId];
    }) > 0, "outbox journal replay");
    Require(sends.size() == 2 && sends[command] == 1 && sends[auth.commandId] == 1 &&
            cancels.size() == 1 && cancels[cancelId] == 1, "durable client caused duplicate/unauthorized sends");
    std::cout << "durable_client_recovery=PASS client_SIGKILL=3 execution_SIGKILL=1"
              << " place_send_attempts=2 cancel_send_attempts=1 forged_flatten_rejected=1\n";
}


// Bounded SERIAL observations of the real HTT1 client/Gateway/exec-child path.
// This is not a broker/HFT benchmark, a different-UID isolation claim or a timing
// pass threshold. Keep setup, warmup, journal inspection and output outside the
// measured calls. All sampled commands still undergo normal service checks.
void TestNativeExecutionLatency() {
    using Clock = std::chrono::steady_clock;
    static_assert(Clock::is_steady, "latency observations require a monotonic clock");
    // The unchanged Gateway permits four cancellations per session/minute.
    // Separate, freshly initialized synthetic fixtures are independent trials,
    // NOT a sustained-throughputput test or a way to rotate a production session.
    const std::size_t fixtures = 8, warmup = 1, measured = 3, total = warmup + measured;
    std::uint64_t totalAuditRecords = 0;
    const std::array<const char*, 9> phases{{
        "preview_ns", "place_persist_ns", "submit_stored_ns", "place_pipeline_ns",
        "status_ns", "cancel_persist_ns", "cancel_stored_ns",
        "cancel_terminal_observed_ns", "duplicate_ns"}};
    std::vector<std::array<std::int64_t, 9>> samples;
    samples.reserve(fixtures * measured);
    auto Elapsed = [](Clock::time_point first, Clock::time_point last) {
        const auto ns = std::chrono::duration_cast<std::chrono::nanoseconds>(last - first).count();
        Require(ns >= 0, "monotonic latency clock reversed");
        return static_cast<std::int64_t>(ns);
    };
    for (std::size_t trial = 0; trial < fixtures; ++trial) {
        Fixture f(false);
        ExecutionProcess execution(f.root.path);
        f.StartGateway();
        const std::string directory = f.root.path + "/latency-outbox";
        Require(::mkdir(directory.c_str(), 0700) == 0, "latency outbox creation failed");
        NativeToolClientConfig config;
        config.socketPath = f.agentConfig.toolSocket;
        config.sessionToken = f.token; config.timeoutMs = 5000;
        NativeToolClient native(config); NativeStrategyClient client(native);
        std::set<std::string> expectedPlaces, expectedCancels;
        for (std::size_t cycle = 0; cycle < total; ++cycle) {
            const auto suffix = std::to_string(cycle);
            const std::string previewId = "latency-preview-" + suffix;
            const std::string statusId = "latency-status-" + suffix;
            const std::string cancelId = "latency-cancel-" + suffix;
            // Nonmarketable fixed synthetic quote. No position is supplied by the
            // client. Each cycle waits for the real cancellation to become durable.
            PreparedOrder order("EUR.USD", Contract(), "BUY", 1, 1.1000, 1.1001,
                                OmsJournal::NowEpochMs() + 120000);
            TypedPreviewAuthorization approval;
            NativeToolClientResult result; std::string reason;
            std::array<std::int64_t, 9> row{};
            const auto pipelineBegin = Clock::now();
            const bool approved = client.PreviewAuthorized(order, previewId, approval, result, reason);
            const auto previewEnd = Clock::now();
            Require(approved && result.envelope.status == "ok", "latency preview: " + reason);
            Require(expectedPlaces.insert(approval.commandId).second, "latency preview reused an identity");
            const auto persistBegin = Clock::now();
            const bool persisted = client.Persist(directory, order, approval.commandId, approval.previewPermit, reason);
            const auto persistEnd = Clock::now();
            Require(persisted, "latency placement persistence: " + reason);
            const auto submitBegin = Clock::now();
            const bool submitted = client.SubmitStored(directory, approval.commandId, result, reason);
            const auto submitEnd = Clock::now();
            Require(submitted && result.envelope.status == "ok" && result.envelope.orderId >= 0,
                    "latency placement not accepted: " + reason + result.responseJson);
            const long serverOrderId = result.envelope.orderId;
            row[0] = Elapsed(pipelineBegin, previewEnd);
            row[1] = Elapsed(persistBegin, persistEnd);
            row[2] = Elapsed(submitBegin, submitEnd);
            row[3] = Elapsed(pipelineBegin, submitEnd); // Includes client validation/bookkeeping between calls.
            execution.Await(cycle + 1, 0, 1);
            const auto statusBegin = Clock::now();
            const bool known = client.Status(approval.commandId, statusId, result, reason);
            const auto statusEnd = Clock::now();
            Require(known && result.envelope.status == "ok" &&
                    result.envelope.payloadJson.find(approval.commandId) != std::string::npos,
                    "latency status lost the submitted command");
            row[4] = Elapsed(statusBegin, statusEnd);
            const PreparedCancellation cancellation(serverOrderId);
            const auto cancelPersistBegin = Clock::now();
            const bool cancelPersisted = client.Persist(directory, cancellation, cancelId, reason);
            const auto cancelPersistEnd = Clock::now();
            Require(cancelPersisted, "latency cancellation persistence: " + reason);
            const auto cancelBegin = Clock::now();
            const bool cancelled = client.SubmitStored(directory, cancelId, result, reason);
            const auto cancelEnd = Clock::now();
            Require(cancelled && result.envelope.status == "ok", "latency cancel: " + reason + result.responseJson);
            execution.Await(cycle + 1, 0, 0, true, 0, cycle + 1);
            const auto terminalEnd = Clock::now();
            Require(expectedCancels.insert(cancelId).second, "duplicate latency cancel identity");
            row[5] = Elapsed(cancelPersistBegin, cancelPersistEnd);
            row[6] = Elapsed(cancelBegin, cancelEnd);
            // Includes the test-only observation IPC and 2ms polling, NOT a venue
            // callback latency or an authoritative application event-feed benchmark.
            row[7] = Elapsed(cancelBegin, terminalEnd);
            const auto retryBegin = Clock::now();
            const bool duplicate = client.SubmitStored(directory, approval.commandId, result, reason);
            const auto retryEnd = Clock::now();
            Require(duplicate && result.envelope.status == "duplicate" && result.envelope.orderId == serverOrderId,
                    "latency same-ID retry resurrected a cancelled order");
            row[8] = Elapsed(retryBegin, retryEnd);
            execution.Await(cycle + 1, 0, 0, true, 0, cycle + 1);
            if (cycle >= warmup) samples.push_back(row);
        }
        // Do not publish success-shaped timing output before independently proving
        // the actual authority path. Warmup sends are included in these counts.
        f.gateway->Stop(); f.gateway.reset(); execution.Stop();
        OmsJournal journal;
        Require(journal.Init(f.executionConfig.journalPath), "latency journal verification open");
        std::map<std::string, unsigned int> sends, cancels;
        Require(journal.Replay([&](const OmsJournalEvent& event) {
            if (event.eventType == "place_send_attempt") ++sends[event.reqId];
            if (event.eventType == "cancel_send_attempt") ++cancels[event.reqId];
        }) > 0, "latency journal empty/invalid");
        Require(sends.size() == total && cancels.size() == total, "latency unexpected/missing venue sends");
        for (const auto& id : expectedPlaces)
            Require(sends.at(id) == 1, "latency placement sent more than once");
        for (const auto& id : expectedCancels)
            Require(cancels.at(id) == 1, "latency cancellation sent more than once");
        std::uint64_t auditRecords = 0; std::string reason;
        Require(SessionSupervisorAuditJournal::Verify(f.agentConfig.supervisorAuditJournalPath, auditRecords, reason) &&
                auditRecords > 0, "latency Gateway audit invalid: " + reason);
        totalAuditRecords += auditRecords;
    }
    Require(samples.size() == fixtures * measured, "latency sample count changed");
    std::ostringstream output;
    output.imbue(std::locale::classic());
    output << "NATIVE_EXECUTION_LATENCY_JSON={\"schema\":\"heptatrader.native-execution-latency.v1\","
           << "\"unit\":\"ns\",\"clock\":\"steady_clock\",\"concurrency\":1,"
           << "\"venue\":\"SIMULATOR\",\"execution_process\":\"separate_exec\","
           << "\"gateway_process\":\"client_process_threads\",\"broker_authorized\":false,"
           << "\"different_uid_isolation\":false,\"cold_start_included\":false,"
           << "\"fixture_count\":" << fixtures
           << ",\"warmup_per_fixture\":" << warmup << ",\"measured_per_fixture\":" << measured
           << ",\"warmup_cycles\":" << fixtures * warmup << ",\"measured_cycles\":" << fixtures * measured
           << ",\"place_send_attempts\":" << fixtures * total << ",\"cancel_send_attempts\":" << fixtures * total
           << ",\"max_send_attempts_per_command\":1,\"final_position\":0,\"final_active_orders\":0"
           << ",\"audit_records\":" << totalAuditRecords << ",\"samples\":[";
    for (std::size_t row = 0; row < samples.size(); ++row) {
        if (row) output << ',';
        output << "{\"fixture_index\":" << row / measured
               << ",\"cycle_index\":" << warmup + row % measured;
        for (std::size_t phase = 0; phase < phases.size(); ++phase) {
            output << ',';
            output << '"' << phases[phase] << "\":" << samples[row][phase];
        }
        output << '}';
    }
    output << "]}";
    std::cout << output.str() << '\n';
    Require(static_cast<bool>(std::cout), "latency output failed");
}

}
int main(int argc, char** argv) {
    try {
        if (argc == 3 && std::string(argv[1]) == "--execution-child") return RunExecutionChild(argv[2]);
        if (argc == 7 && std::string(argv[1]) == "--outbox-child")
            return RunOutboxChild(argv[2], argv[3], argv[4], argv[5], argv[6]);
        if (argc == 2 && std::string(argv[1]) == "--latency-only") {
            TestNativeExecutionLatency();
            return 0;
        }
        Require(argc == 1, "unsupported native Execution test argument");
        TestNativeExecutionLifecycle();
        TestNativeExecutionProcessCrashes();
        TestDurableClientProcessRecovery();
        TestNativeExecutionLatency();
        std::cout << "PASS real Native/Gateway/Execution lifecycle and SIGKILL recovery\n";
        return 0;
    }
    catch (const std::exception& error) { std::cerr << "FAIL: " << error.what() << '\n'; return 1; }
}
