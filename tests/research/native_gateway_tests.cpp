#include "hepta/research/native_strategy_client.h"
#include "test_support.h"
#include <tool_host/unix_tool_server.h>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <mutex>
#include <string>
#include <unistd.h>

using namespace hepta::research;
namespace {
class TemporaryDirectory {
public:
    TemporaryDirectory() {
        char pattern[] = "/tmp/hepta-research-gateway-XXXXXX";
        char* created = ::mkdtemp(pattern);
        Check(created != nullptr, "cannot create fixture directory");
        path = created;
    }
    ~TemporaryDirectory() { std::remove((path + "/tool.sock").c_str()); ::rmdir(path.c_str()); }
    std::string path;
};

// Test-only negative authority. It has no venue or order-success implementation.
// This fixture checks real client/codec/socket/Gateway forwarding, not broker
// qualification, actual risk approval, durable replay, or installed isolation.
class UncertainFixtureAuthority : public ExecutionAuthority {
public:
    ExecutionCommandResult PlaceOrder(const PlaceOrderCommand& command) override {
        std::lock_guard<std::mutex> lock(mutex);
        ++calls; observedId = command.context.toolCallId;
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Uncertain;
        result.commandId = observedId;
        result.reasonCode = "RESEARCH_FIXTURE_UNCERTAIN";
        return result;
    }
    ExecutionCommandResult CancelOrder(const CancelOrderCommand&) override {
        return ExecutionCommandResult();
    }
    void AssertSingleSubmission(const std::string& expected) {
        std::lock_guard<std::mutex> lock(mutex);
        Check(calls == 1 && observedId == expected, "request retried or command identity changed");
    }
private:
    std::mutex mutex;
    unsigned int calls = 0;
    std::string observedId;
};

void TestGatewayForwarding() {
    TemporaryDirectory directory;
    UncertainFixtureAuthority authority;
    TradingToolRegistry registry(authority);
    DecisionLeaseManager leases;
    TradingToolHost host(registry, leases,
        [](const TradingToolSession&, const TradingToolCall&, std::string&) { return true; });
    const std::uint64_t now = static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count());
    TradingToolHostSessionBinding binding;
    binding.token = "research-fixture-session-token-0001";
    binding.peerUid = static_cast<std::uint32_t>(::getuid());
    binding.session.executionContext.agentId = "research-fixture-agent";
    binding.session.executionContext.sessionId = "research-fixture-session";
    binding.session.executionContext.strategy = "research-fixture";
    binding.session.executionContext.account = "SIM-PAPER";
    binding.session.executionContext.venue = "SIM";
    binding.session.environment = "PAPER";
    binding.session.capabilities.insert("system.read");
    binding.session.capabilities.insert("trade.place");
    binding.allowedInstruments.insert("EUR.USD");
    InstrumentRef contract;
    contract.symbol = "EUR"; contract.currency = "USD";
    contract.secType = "CASH"; contract.exchange = "SIM";
    binding.instrumentContracts["EUR.USD"] = contract;
    binding.maxOrderQuantity = 1000;
    binding.maxTradeCallsPerMinute = 20;
    binding.executionDomain = "SIM-PAPER";
    binding.expiresAtMs = now + 60000;
    std::string reason;
    Check(host.RegisterSession(binding, reason), "fixture session registration failed");
    UnixToolServer server(host);
    server.AllowMissingDecisionAuditForTests();
    Check(server.Start(directory.path + "/tool.sock", reason), "fixture Gateway start failed");
    NativeToolClientConfig config;
    config.socketPath = directory.path + "/tool.sock";
    config.sessionToken = binding.token;
    config.timeoutMs = 5000;
    NativeToolClient native(config);
    NativeStrategyClient client(native);
    PreparedOrder proposal("EUR.USD", contract, "BUY", 100, 1.10, 1.09,
                           static_cast<std::int64_t>(now + 30000));
    const std::string id = "execution-fixture-command-0001";
    const std::string permit = "sha256:" + std::string(64, 'a');
    NativeToolClientResult result;
    const auto start = std::chrono::steady_clock::now();
    Check(client.Submit(proposal, id, permit, result, reason), "real Gateway response not transported");
    const auto elapsed = std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now() - start).count();
    Check(result.envelope.status == "uncertain", "adapter hid an uncertain outcome");
    Check(result.envelope.reasonCode == "RESEARCH_FIXTURE_UNCERTAIN", "adapter changed the execution reason");
    authority.AssertSingleSubmission(id);
    host.RevokeSession(binding.token);
    Check(client.Submit(proposal, id, permit, result, reason), "revocation response not transported");
    Check(result.envelope.status == "permission_denied", "revoked identity reached execution");
    authority.AssertSingleSubmission(id);
    server.Stop();
    std::cout << "local_fixture_gateway_roundtrip_us=" << elapsed
              << " (includes discovery; not broker latency)\n";
}
}
int main() { return Run(TestGatewayForwarding); }
