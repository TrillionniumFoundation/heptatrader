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
    ExecutionCommandResult CancelOrder(const CancelOrderCommand& command) override {
        std::lock_guard<std::mutex> lock(mutex);
        ++cancelCalls; cancelId = command.context.toolCallId; cancelOrderId = command.orderId;
        cancelAny = command.context.allowCancelAny; cancelOwner = command.context.agentId;
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Uncertain;
        result.commandId = cancelId;
        result.reasonCode = "RESEARCH_FIXTURE_CANCEL_UNCERTAIN";
        return result;
    }
    bool PreviewFlatten(const TradingToolSession&, const TradingToolCall&,
                        std::string&, std::string& reason) {
        std::lock_guard<std::mutex> lock(mutex);
        ++previewCalls; reason = "RESEARCH_FIXTURE_PREVIEW_DENIED";
        return false; // No fixture issues a success-shaped risk permit.
    }
    ExecutionCommandResult Flatten(const TradingToolSession& session, const TradingToolCall& call) {
        std::lock_guard<std::mutex> lock(mutex);
        ++flattenCalls; flattenId = session.executionContext.toolCallId;
        flattenInstrument = call.instrument; flattenPermit = call.previewPermit;
        clientPositionAbsent = call.ibOrder.action.empty() && call.ibOrder.totalQuantity == 0 &&
            call.ibOrder.lmtPrice == 0 && call.referencePrice == 0 && call.ibOrder.orderRef.empty();
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Uncertain;
        result.commandId = flattenId;
        result.reasonCode = "RESEARCH_FIXTURE_FLATTEN_UNCERTAIN";
        return result;
    }
    void AssertExits(const std::string& expectedCancel, const std::string& expectedFlatten,
                     const std::string& expectedPermit) {
        std::lock_guard<std::mutex> lock(mutex);
        Check(cancelCalls == 1 && cancelId == expectedCancel && cancelOrderId == 42 &&
              !cancelAny && cancelOwner == "research-fixture-agent", "cancel authority or identity drift");
        Check(previewCalls == 1 && flattenCalls == 1 && flattenId == expectedFlatten &&
              flattenInstrument == "EUR.USD" && flattenPermit == expectedPermit && clientPositionAbsent,
              "flatten retried, bypassed preview binding or supplied a local position");
    }
    void AssertSingleSubmission(const std::string& expected) {
        std::lock_guard<std::mutex> lock(mutex);
        Check(calls == 1 && observedId == expected, "request retried or command identity changed");
    }
private:
    std::mutex mutex;
    unsigned int calls = 0;
    std::string observedId;
    unsigned int cancelCalls = 0, flattenCalls = 0, previewCalls = 0;
    long cancelOrderId = -1;
    bool cancelAny = false, clientPositionAbsent = false;
    std::string cancelId, cancelOwner, flattenId, flattenInstrument, flattenPermit;
};

void TestGatewayForwarding(bool borrowedInputs) {
    TemporaryDirectory directory;
    UncertainFixtureAuthority authority;
    TradingToolReadCallbacks reads;
    reads.riskPreviewFlatten = [&](const TradingToolSession& session, const TradingToolCall& call,
                                   std::string& json, std::string& why) {
        return authority.PreviewFlatten(session, call, json, why);
    };
    TradingToolTradeCallbacks trades;
    trades.flattenPosition = [&](const TradingToolSession& session, const TradingToolCall& call) {
        return authority.Flatten(session, call);
    };
    TradingToolRegistry registry(authority, reads, trades);
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
    binding.session.capabilities.insert("trade.cancel");
    binding.session.capabilities.insert("trade.flatten");
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
    if (borrowedInputs) {
        result.envelope.detail = id; result.envelope.payloadJson = permit;
        Check(client.Submit(proposal, result.envelope.detail, result.envelope.payloadJson, result, reason),
              "borrowed placement inputs did not reach the real Gateway");
    } else {
        Check(client.Submit(proposal, id, permit, result, reason), "real Gateway response not transported");
    }
    const auto elapsed = std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now() - start).count();
    Check(result.envelope.status == "uncertain", "adapter hid an uncertain outcome");
    Check(result.envelope.reasonCode == "RESEARCH_FIXTURE_UNCERTAIN", "adapter changed the execution reason");
    authority.AssertSingleSubmission(id);
    PreparedCancellation cancellation(42);
    PreparedFlatten flatten("EUR.USD");
    const std::string cancelId = "research-fixture-cancel-0001";
    const std::string flattenId = "execution-fixture-flatten-0001";
    if (borrowedInputs) {
        result.envelope.detail = cancelId;
        Check(client.Cancel(cancellation, result.envelope.detail, result, reason), "borrowed cancel ID lost");
    } else {
        Check(client.Cancel(cancellation, cancelId, result, reason), "cancel response not transported");
    }
    Check(result.envelope.status == "uncertain" &&
          result.envelope.reasonCode == "RESEARCH_FIXTURE_CANCEL_UNCERTAIN", "cancel outcome rewritten");
    if (borrowedInputs) {
        reason = "research-fixture-flatten-preview";
        Check(client.PreviewFlatten(flatten, reason, result, reason), "borrowed preview ID lost");
    } else {
        Check(client.PreviewFlatten(flatten, "research-fixture-flatten-preview", result, reason),
              "flatten preview rejection not transported");
    }
    Check(result.envelope.status != "ok", "fixture preview fabricated approval");
    // A syntactically valid dummy permit reaches only a negative fixture, never
    // a broker. This verifies forwarding, not actual risk/permit authorization.
    if (borrowedInputs) {
        result.envelope.detail = flattenId; result.envelope.payloadJson = permit;
        Check(client.Flatten(flatten, result.envelope.detail, result.envelope.payloadJson, result, reason),
              "borrowed flatten binding lost");
    } else {
        Check(client.Flatten(flatten, flattenId, permit, result, reason), "flatten response not transported");
    }
    Check(result.envelope.status == "uncertain" &&
          result.envelope.reasonCode == "RESEARCH_FIXTURE_FLATTEN_UNCERTAIN", "flatten outcome rewritten");
    authority.AssertExits(cancelId, flattenId, permit);

    // Missing capability must not be bypassed merely because this is an exit.
    auto restricted = binding;
    restricted.token = "research-fixture-restricted-token-0001";
    restricted.session.executionContext.agentId = "research-fixture-restricted-agent";
    restricted.session.executionContext.sessionId = "research-fixture-restricted-session";
    restricted.session.capabilities.erase("trade.cancel");
    restricted.session.capabilities.erase("trade.flatten");
    Check(host.RegisterSession(restricted, reason), "restricted registration failed");
    auto restrictedConfig = config; restrictedConfig.sessionToken = restricted.token;
    NativeToolClient restrictedNative(restrictedConfig);
    NativeStrategyClient restrictedClient(restrictedNative);
    const bool cancelDelivered = restrictedClient.Cancel(cancellation, "research-denied-cancel-0001", result, reason);
    Check(!cancelDelivered || result.envelope.status == "permission_denied" ||
          result.envelope.status == "invalid_tool", "missing cancel capability was accepted");
    const bool flattenDelivered = restrictedClient.Flatten(flatten, "research-denied-flatten-0001", permit, result, reason);
    Check(!flattenDelivered || result.envelope.status == "permission_denied" ||
          result.envelope.status == "invalid_tool", "missing flatten capability was accepted");
    authority.AssertExits(cancelId, flattenId, permit);
    host.RevokeSession(restricted.token);
    host.RevokeSession(binding.token);
    Check(client.Submit(proposal, id, permit, result, reason), "revocation response not transported");
    Check(result.envelope.status == "permission_denied", "revoked identity reached execution");
    authority.AssertSingleSubmission(id);
    Check(client.Cancel(cancellation, cancelId, result, reason), "cancel revocation not transported");
    Check(result.envelope.status == "permission_denied", "revoked cancel reached execution");
    Check(client.Flatten(flatten, flattenId, permit, result, reason), "flatten revocation not transported");
    Check(result.envelope.status == "permission_denied", "revoked flatten reached execution");
    Check(client.PreviewFlatten(flatten, "research-revoked-flatten-preview", result, reason),
          "preview revocation not transported");
    Check(result.envelope.status == "permission_denied", "revoked preview reached its callback");
    authority.AssertExits(cancelId, flattenId, permit);
    server.Stop();
    std::cout << "local_fixture_gateway_roundtrip_us=" << elapsed
              << " (includes discovery; not broker latency)\n";
}
}
int main() { return Run([] { TestGatewayForwarding(false); TestGatewayForwarding(true); }); }
