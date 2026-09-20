#include "../HeptaTrade/client/research_intent_client.h"
#include "../HeptaTrade/tool_host/unix_tool_server.h"
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <map>
#include <stdexcept>
#include <string>
#include <sys/stat.h>
#include <unistd.h>

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
    std::map<std::string, PlaceOrderCommand> commands;
    unsigned calls = 0;
    bool uncertain = false;
    ExecutionCommandResult PlaceOrder(const PlaceOrderCommand& command) override {
        ++calls;
        const auto inserted = commands.emplace(command.context.toolCallId, command);
        ExecutionCommandResult result;
        result.commandId = command.context.toolCallId;
        result.orderId = 41;
        result.status = !inserted.second ? ExecutionCommandStatus::Duplicate :
            uncertain ? ExecutionCommandStatus::Uncertain : ExecutionCommandStatus::Accepted;
        return result;
    }
    ExecutionCommandResult CancelOrder(const CancelOrderCommand&) override { return ExecutionCommandResult(); }
    ExecutionCommandResult FlattenPosition(const FlattenPositionCommand&) override { return ExecutionCommandResult(); }
};
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
    TradingToolReadCallbacks reads;
    reads.riskPreviewOrder = [&](const TradingToolSession&, const TradingToolCall&,
                                  std::string& out, std::string&) {
        out = payload;
        return true;
    };
    reads.executionGetCommandStatus = [&](const TradingToolSession&, const TradingToolCall& call,
                                           std::string& out, std::string&) {
        out = "{\"command_id\":\"" + call.targetCommandId + "\",\"fixture\":true}";
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
    CHECK(authority.calls == 0);
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
    CHECK(result.envelope.status == "ok" && authority.commands.size() == 1);
    CHECK(authority.commands.begin()->second.expiresAtMs == proposal.expiresAtMs);
    CHECK(authority.commands.begin()->second.order.totalQuantity == 2);
    ResearchPreparedOrder recovered;
    CHECK(ResearchIntentClient::Load(directory, "service-issued-0001", recovered, reason));
    CHECK(client.Submit(recovered, result, reason));
    CHECK(result.envelope.status == "duplicate" && authority.commands.size() == 1);
    CHECK(client.Status(recovered.CommandId(), "research-query-0001", result, reason));
    CHECK(result.envelope.payloadJson.find(recovered.CommandId()) != std::string::npos);

    // An unreachable Gateway cannot manufacture success or mutate the request.
    config.socketPath = directory + "/absent.sock";
    ResearchIntentClient disconnected(config, limits);
    CHECK(!disconnected.Submit(recovered, result, reason));
    CHECK(recovered.CommandId() == "service-issued-0001");
    CHECK(client.Submit(recovered, result, reason));
    CHECK(authority.commands.size() == 1);

    // Explicit uncertainty survives restart/reload. Retry never calls preview
    // or chooses a fresh execution command ID.
    payload = Receipt("service-issued-0002", now + 30000);
    proposal.proposalId = "research-preview-0002";
    CHECK(client.Prepare(proposal, contract, now, prepared, result, reason));
    CHECK(ResearchIntentClient::Persist(directory, prepared, reason));
    authority.uncertain = true;
    CHECK(client.Submit(prepared, result, reason));
    CHECK(result.envelope.status == "uncertain");
    CHECK(ResearchIntentClient::Load(directory, prepared.CommandId(), recovered, reason));
    CHECK(client.Submit(recovered, result, reason));
    CHECK(result.envelope.status == "duplicate" && authority.commands.size() == 2);

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
        payload = bad;
        CHECK(!client.Prepare(proposal, contract, now, prepared, result, reason));
        CHECK(!prepared.Ready());
    }
    proposal.quantity = 1000;
    CHECK(!client.Prepare(proposal, contract, now, prepared, result, reason));
    CHECK(authority.commands.size() == 2);
    server.Stop();
    CHECK(::unlink(path.c_str()) == 0);
    CHECK(::unlink((directory + "/service-issued-0002.hro").c_str()) == 0);
    CHECK(::unlink((directory + "/symlink-command.hro").c_str()) == 0);
    CHECK(::rmdir(directory.c_str()) == 0);
}
}
int main() {
    try { Exercise(); std::cout << "PASS research client: native Unix protocol, durable identity, uncertainty and unsafe-input rejection\n"; return 0; }
    catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
