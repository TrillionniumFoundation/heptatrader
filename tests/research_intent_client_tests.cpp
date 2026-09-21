#include "../HeptaTrade/client/research_intent_client.h"
#include "../HeptaTrade/tool_host/unix_tool_server.h"
#include <atomic>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <map>
#include <mutex>
#include <spawn.h>
#include <stdexcept>
#include <string>
#include <sys/stat.h>
#include <sys/wait.h>
#include <vector>
#include <unistd.h>

extern char** environ;

namespace {
#define CHECK(x) do { if (!(x)) throw std::runtime_error(std::string("check failed: ") + #x); } while (false)
std::int64_t Now() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
}
// Test authority only: the production adapter never links or instantiates this.
// This fixture tests actual native-client discovery, Unix wire, host checks,
// exact retry identity and private durable outbox. Existing coordinator and
// installed-process suites own broker-uncertainty/recovery qualification.
class RecordingAuthority : public ExecutionAuthority {
public:
    std::atomic<bool> uncertain{false};
    std::size_t Count() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return commands_.size();
    }
    unsigned Calls() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return calls_;
    }
    PlaceOrderCommand Command(const std::string& id) const {
        std::lock_guard<std::mutex> lock(mutex_);
        return commands_.at(id);
    }
    void ResolveAccepted(const std::string& id) {
        std::lock_guard<std::mutex> lock(mutex_);
        CHECK(commands_.count(id) == 1);
        states_[id] = ExecutionCommandStatus::Accepted;
    }
    std::string Status(const std::string& id) const {
        std::lock_guard<std::mutex> lock(mutex_);
        return states_.at(id) == ExecutionCommandStatus::Uncertain ? "uncertain" : "accepted";
    }
    ExecutionCommandResult PlaceOrder(const PlaceOrderCommand& command) override {
        std::lock_guard<std::mutex> lock(mutex_);
        ++calls_;
        const auto inserted = commands_.emplace(command.context.toolCallId, command);
        if (inserted.second) {
            states_[command.context.toolCallId] = uncertain.load() ?
                ExecutionCommandStatus::Uncertain : ExecutionCommandStatus::Accepted;
        } else {
            const auto& original = inserted.first->second;
            CHECK(original.expiresAtMs == command.expiresAtMs);
            CHECK(original.instrument == command.instrument);
            CHECK(original.order.action == command.order.action);
            CHECK(original.order.totalQuantity == command.order.totalQuantity);
            CHECK(original.order.lmtPrice == command.order.lmtPrice);
            CHECK(original.context.account == command.context.account);
        }
        ExecutionCommandResult result;
        result.commandId = command.context.toolCallId;
        result.orderId = 41;
        const auto state = states_.at(command.context.toolCallId);
        result.status = state == ExecutionCommandStatus::Uncertain ? state :
            inserted.second ? ExecutionCommandStatus::Accepted : ExecutionCommandStatus::Duplicate;
        return result;
    }
    ExecutionCommandResult CancelOrder(const CancelOrderCommand&) override { return ExecutionCommandResult(); }
    ExecutionCommandResult FlattenPosition(const FlattenPositionCommand&) override { return ExecutionCommandResult(); }
private:
    mutable std::mutex mutex_;
    std::map<std::string, PlaceOrderCommand> commands_;
    std::map<std::string, ExecutionCommandStatus> states_;
    unsigned calls_ = 0;
};

// Fresh executable images, not forked C++ code inheriting the server's locks.
// These arguments are synthetic fixture data, never real credentials.
class WorkerProcess {
public:
    explicit WorkerProcess(std::vector<std::string> args) {
        args.insert(args.begin(), "/proc/self/exe");
        std::vector<char*> argv;
        for (auto& value : args) argv.push_back(&value[0]);
        argv.push_back(nullptr);
        CHECK(::posix_spawn(&pid_, "/proc/self/exe", nullptr, nullptr, argv.data(), environ) == 0);
    }
    ~WorkerProcess() {
        if (pid_ > 0) {
            ::kill(pid_, SIGKILL);
            int status;
            while (::waitpid(pid_, &status, 0) < 0 && errno == EINTR) {}
        }
    }
    WorkerProcess(const WorkerProcess&) = delete;
    WorkerProcess& operator=(const WorkerProcess&) = delete;
    int Wait(int options = 0) {
        int status = 0;
        pid_t found;
        do { found = ::waitpid(pid_, &status, options); } while (found < 0 && errno == EINTR);
        CHECK(found == pid_);
        if (!WIFSTOPPED(status)) pid_ = -1;
        return status;
    }
    void KillStopped() {
        CHECK(::kill(pid_, SIGKILL) == 0);
        const int status = Wait();
        CHECK(WIFSIGNALED(status) && WTERMSIG(status) == SIGKILL);
    }
private:
    pid_t pid_ = -1;
};

int Worker(int argc, char** argv) {
    CHECK(argc == 7);
    NativeToolClientConfig config;
    config.socketPath = argv[2]; config.sessionToken = argv[3]; config.timeoutMs = 2000;
    hepta::research::ProposalLimits limits;
    limits.maximumQuantity = 10; limits.maximumLifetimeMs = 60000;
    limits.maximumPriceTimesQuantity = 100;
    ResearchIntentClient client(config, limits);
    ResearchPreparedOrder prepared;
    NativeToolClientResult result;
    std::string reason;
    if (std::string(argv[1]) == "--prepare-stop") {
        const auto now = Now();
        hepta::research::BoundedOrderProposal proposal;
        proposal.proposalId = "crash-preview-0003"; proposal.instrument = "EUR.USD";
        proposal.side = 1; proposal.quantity = 2; proposal.limitPrice = 1.1002;
        proposal.expiresAtMs = now + 60000;
        InstrumentRef contract;
        contract.symbol = "EUR"; contract.currency = "USD";
        contract.secType = "CASH"; contract.exchange = "SIM";
        CHECK(client.Prepare(proposal, contract, now, prepared, result, reason));
        CHECK(prepared.CommandId() == argv[5]);
        CHECK(ResearchIntentClient::Persist(argv[4], prepared, reason));
        // Parent observes SIGSTOP only after both fsyncs, then kills this image.
        CHECK(::raise(SIGSTOP) == 0);
        return 80; // Unexpected resume is not a successful crash test.
    }
    CHECK(std::string(argv[1]) == "--recover-submit");
    CHECK(ResearchIntentClient::Load(argv[4], argv[5], prepared, reason));
    CHECK(client.Submit(prepared, result, reason));
    if (result.envelope.status != argv[6])
        std::cerr << "worker id=" << argv[5] << " expected=" << argv[6]
                  << " actual=" << result.envelope.status << " reason=" << reason
                  << " response=" << result.responseJson << std::endl;
    CHECK(result.envelope.status == argv[6]);
    return 0;
}
void RecoverInFreshProcess(const std::string& socket, const std::string& token,
                           const std::string& directory, const std::string& id,
                           const std::string& expected) {
    WorkerProcess worker({"--recover-submit", socket, token, directory, id, expected});
    const int status = worker.Wait();
    CHECK(WIFEXITED(status) && WEXITSTATUS(status) == 0);
}
std::string Receipt(const std::string& command, std::int64_t expires) {
    return "{\"approved\":true,\"single_use\":true,\"command_id\":\"" + command +
        "\",\"preview_permit\":\"sha256:" + std::string(64, 'a') +
        "\",\"permit_expires_at_ms\":" + std::to_string(expires) + "}";
}
void Exercise() {
    char temp[] = "/tmp/hepta-research-client-XXXXXX";
    CHECK(::mkdtemp(temp) != nullptr);
    const std::string directory = temp;
    CHECK(::chmod(directory.c_str(), 0700) == 0);
    const std::string socket = directory + "/tools.sock";
    const std::string token = "synthetic-research-client-session-token";
    const auto now = Now();
    RecordingAuthority authority;
    DecisionLeaseManager leases;
    std::string payload = Receipt("service-issued-0001", now + 30000);
    std::mutex payloadMutex;
    std::atomic<unsigned> previewCalls{0};
    const auto setPayload = [&](const std::string& value) {
        std::lock_guard<std::mutex> lock(payloadMutex);
        payload = value;
    };
    TradingToolReadCallbacks reads;
    reads.riskPreviewOrder = [&](const TradingToolSession&, const TradingToolCall&,
                                  std::string& out, std::string&) {
        std::lock_guard<std::mutex> lock(payloadMutex);
        ++previewCalls;
        out = payload;
        return true;
    };
    reads.executionGetCommandStatus = [&](const TradingToolSession&, const TradingToolCall& call,
                                           std::string& out, std::string&) {
        out = "{\"command_id\":\"" + call.targetCommandId + "\",\"command_status\":\"" +
            authority.Status(call.targetCommandId) + "\",\"order_id\":41}";
        return true;
    };
    TradingToolRegistry registry(authority, reads);
    TradingToolHost host(registry, leases,
        [](const TradingToolSession&, const TradingToolCall&, std::string&) { return true; });
    TradingToolHostSessionBinding binding;
    binding.token = token;
    binding.peerUid = static_cast<std::uint32_t>(::getuid());
    binding.session.executionContext.agentId = "research-fixture";
    binding.session.executionContext.sessionId = "research-fixture-session";
    binding.session.executionContext.account = "SIM-PAPER";
    binding.session.executionContext.venue = "SIM";
    binding.session.executionContext.strategy = "research-fixture";
    binding.session.environment = "PAPER";
    for (const auto* capability : {"system.read", "risk.read", "risk.preview", "orders.read", "trade.place"})
        binding.session.capabilities.insert(capability);
    binding.allowedInstruments.insert("EUR.USD");
    InstrumentRef contract;
    contract.symbol = "EUR"; contract.currency = "USD";
    contract.secType = "CASH"; contract.exchange = "SIM";
    binding.instrumentContracts["EUR.USD"] = contract;
    binding.maxOrderQuantity = 100;
    binding.maxTradeCallsPerMinute = 100;
    binding.executionDomain = "SIM-PAPER";
    binding.expiresAtMs = static_cast<std::uint64_t>(now + 60000);
    std::string reason;
    CHECK(host.RegisterSession(binding, reason));
    UnixToolServer server(host);
    server.AllowMissingDecisionAuditForTests();
    CHECK(server.Start(socket, reason));

    NativeToolClientConfig config;
    config.socketPath = socket; config.sessionToken = token; config.timeoutMs = 2000;
    hepta::research::ProposalLimits limits;
    limits.maximumQuantity = 10; limits.maximumLifetimeMs = 60000;
    limits.maximumPriceTimesQuantity = 100;
    ResearchIntentClient client(config, limits);
    hepta::research::BoundedOrderProposal proposal;
    proposal.proposalId = "research-preview-0001"; proposal.instrument = "EUR.USD";
    proposal.side = 1; proposal.quantity = 2; proposal.limitPrice = 1.1002;
    proposal.expiresAtMs = now + 60000;
    ResearchPreparedOrder prepared;
    NativeToolClientResult result;
    CHECK(!client.Submit(prepared, result, reason));
    InstrumentRef unsupported = contract;
    unsupported.strike = 100;
    CHECK(!client.Prepare(proposal, unsupported, now, prepared, result, reason));
    CHECK(client.Prepare(proposal, contract, now, prepared, result, reason));
    CHECK(prepared.Ready() && !prepared.Persisted());
    CHECK(prepared.CommandId() == "service-issued-0001");
    CHECK(authority.Calls() == 0);
    CHECK(!client.Submit(prepared, result, reason));
    CHECK(ResearchIntentClient::Persist(directory, prepared, reason));
    CHECK(ResearchIntentClient::Persist(directory, prepared, reason));
    const std::string path = directory + "/" + prepared.CommandId() + ".hro";
    std::ifstream stored(path, std::ios::binary);
    const std::string bytes((std::istreambuf_iterator<char>(stored)), std::istreambuf_iterator<char>());
    CHECK(bytes.find(token) == std::string::npos);
    CHECK(bytes.find("service-issued-0001") != std::string::npos);
    stored.close();
    CHECK(client.Submit(prepared, result, reason));
    CHECK(result.envelope.status == "ok" && authority.Count() == 1);
    CHECK(authority.Command("service-issued-0001").expiresAtMs == proposal.expiresAtMs);
    CHECK(authority.Command("service-issued-0001").order.totalQuantity == 2);
    ResearchPreparedOrder recovered;
    CHECK(ResearchIntentClient::Load(directory, "service-issued-0001", recovered, reason));
    CHECK(client.Submit(recovered, result, reason));
    CHECK(result.envelope.status == "duplicate" && authority.Count() == 1);
    CHECK(client.Status(recovered.CommandId(), "research-query-0001", result, reason));
    CHECK(result.envelope.payloadJson.find(recovered.CommandId()) != std::string::npos);

    // An unreachable Gateway cannot manufacture success or mutate the request.
    config.socketPath = directory + "/absent.sock";
    ResearchIntentClient disconnected(config, limits);
    CHECK(!disconnected.Submit(recovered, result, reason));
    CHECK(recovered.CommandId() == "service-issued-0001");
    CHECK(client.Submit(recovered, result, reason));
    CHECK(authority.Count() == 1);

    // Explicit uncertainty survives restart/reload. Retry never calls preview
    // or chooses a fresh execution command ID.
    setPayload(Receipt("service-issued-0002", now + 30000));
    proposal.proposalId = "research-preview-0002";
    CHECK(client.Prepare(proposal, contract, now, prepared, result, reason));
    CHECK(ResearchIntentClient::Persist(directory, prepared, reason));
    authority.uncertain = true;
    CHECK(client.Submit(prepared, result, reason));
    CHECK(result.envelope.status == "uncertain");
    CHECK(ResearchIntentClient::Load(directory, prepared.CommandId(), recovered, reason));
    CHECK(client.Submit(recovered, result, reason));
    // A warm Gateway caches uncertainty; a retry is NOT evidence of success.
    CHECK(result.envelope.status == "uncertain" && authority.Count() == 2);
    CHECK(authority.Calls() == 2 && previewCalls.load() == 2);
    RecoverInFreshProcess(socket, token, directory, recovered.CommandId(), "uncertain");
    CHECK(authority.Calls() == 2 && previewCalls.load() == 2);
    CHECK(client.Status(recovered.CommandId(), "uncertain-query-0002", result, reason));
    CHECK(result.envelope.payloadJson.find("\"command_status\":\"uncertain\"") != std::string::npos);

    // Only the authority can resolve uncertainty. A cold Gateway subsequently
    // forwards the SAME stored command; it cannot fabricate a replacement ID.
    authority.ResolveAccepted(recovered.CommandId());
    CHECK(client.Status(recovered.CommandId(), "resolved-query-0002", result, reason));
    CHECK(result.envelope.payloadJson.find("\"command_status\":\"accepted\"") != std::string::npos);
    server.Stop();
    // The Gateway-local lease cache is part of the restarted process image.
    // Persistent fencing/reconciliation qualification is covered by the real
    // installed-service suites, not this unprivileged client fixture.
    DecisionLeaseManager coldLeases;
    TradingToolHost coldHost(registry, coldLeases,
        [](const TradingToolSession&, const TradingToolCall&, std::string&) { return true; });
    CHECK(coldHost.RegisterSession(binding, reason));
    UnixToolServer coldServer(coldHost);
    coldServer.AllowMissingDecisionAuditForTests();
    CHECK(coldServer.Start(socket, reason));
    RecoverInFreshProcess(socket, token, directory, recovered.CommandId(), "duplicate");
    CHECK(authority.Count() == 2 && authority.Calls() == 3 && previewCalls.load() == 2);

    // Real client-process death after durable prepare and before any send.
    setPayload(Receipt("service-issued-0003", Now() + 30000));
    authority.uncertain = false;
    {
        WorkerProcess child({"--prepare-stop", socket, token, directory, "service-issued-0003", "unused"});
        const int status = child.Wait(WUNTRACED);
        CHECK(WIFSTOPPED(status) && WSTOPSIG(status) == SIGSTOP);
        CHECK(authority.Count() == 2 && authority.Calls() == 3 && previewCalls.load() == 3);
        child.KillStopped();
    }
    // Make re-preview fail: both recovery sends must exclusively load HRO1.
    setPayload("{\"approved\":false}");
    RecoverInFreshProcess(socket, token, directory, "service-issued-0003", "ok");
    RecoverInFreshProcess(socket, token, directory, "service-issued-0003", "duplicate");
    CHECK(authority.Count() == 3 && authority.Calls() == 4 && previewCalls.load() == 3);

    // Corrupt/unsafe outboxes must never become sendable prepared orders.
    CHECK(::chmod(path.c_str(), 0644) == 0);
    CHECK(!ResearchIntentClient::Load(directory, "service-issued-0001", recovered, reason));
    CHECK(!recovered.Ready());
    CHECK(::chmod(path.c_str(), 0600) == 0);
    CHECK(::link(path.c_str(), (directory + "/hardlink").c_str()) == 0);
    CHECK(!ResearchIntentClient::Load(directory, "service-issued-0001", recovered, reason));
    CHECK(::unlink((directory + "/hardlink").c_str()) == 0);
    CHECK(::symlink(path.c_str(), (directory + "/symlink-command.hro").c_str()) == 0);
    CHECK(!ResearchIntentClient::Load(directory, "symlink-command", recovered, reason));
    CHECK(!ResearchIntentClient::Load(directory, "../escape", recovered, reason));
    CHECK(::chmod(directory.c_str(), 0755) == 0);
    CHECK(!ResearchIntentClient::Load(directory, "service-issued-0001", recovered, reason));
    CHECK(::chmod(directory.c_str(), 0700) == 0);
    std::ofstream corrupt(path, std::ios::binary | std::ios::trunc);
    corrupt << "HRO1partial"; corrupt.close();
    CHECK(!ResearchIntentClient::Load(directory, "service-issued-0001", recovered, reason));

    for (const std::string& bad : {
            std::string("{\"approved\":false}"),
            std::string("{\"approved\":true,\"nested\":{\"single_use\":true,\"command_id\":\"decoy-id\"}}"),
            Receipt("../escape", now + 30000),
            Receipt("service-issued-0003", now - 1),
            std::string("{\"approved\":true,\"approved\":true}")}) {
        setPayload(bad);
        CHECK(!client.Prepare(proposal, contract, now, prepared, result, reason));
        CHECK(!prepared.Ready());
    }
    proposal.quantity = 1000;
    CHECK(!client.Prepare(proposal, contract, now, prepared, result, reason));
    CHECK(authority.Count() == 3);
    coldServer.Stop();
    CHECK(::unlink(path.c_str()) == 0);
    CHECK(::unlink((directory + "/service-issued-0002.hro").c_str()) == 0);
    CHECK(::unlink((directory + "/service-issued-0003.hro").c_str()) == 0);
    CHECK(::unlink((directory + "/symlink-command.hro").c_str()) == 0);
    CHECK(::rmdir(directory.c_str()) == 0);
}
}
int main(int argc, char** argv) {
    try { if (argc > 1) return Worker(argc, argv); Exercise(); std::cout << "PASS research client: native Unix protocol, cached uncertainty, cold-Gateway retry, SIGKILL client recovery and unsafe-input rejection\n"; return 0; }
    catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
