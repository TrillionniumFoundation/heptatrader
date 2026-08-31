#include "../HeptaTrade/tools/trading_tool_registry.h"

#include <cassert>
#include <chrono>
#include <cstdint>
#include <string>

namespace
{
std::int64_t NowMs()
{
    return std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
}

class FakeExecution final : public ExecutionAuthority
{
public:
    ExecutionCommandResult PlaceOrder(const PlaceOrderCommand&) override
    {
        ++placeCalls;
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Accepted;
        return result;
    }

    ExecutionCommandResult CancelOrder(const CancelOrderCommand&) override
    {
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Accepted;
        return result;
    }

    ExecutionCommandResult FlattenPosition(
        const FlattenPositionCommand&) override
    {
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Accepted;
        return result;
    }

    int placeCalls = 0;
};

struct Fixture
{
    explicit Fixture(const std::string& positions)
        : positionsPayload(positions)
    {
        callbacks.systemGetHealth = [](
            const TradingToolSession&, const TradingToolCall&,
            std::string& payload, std::string&) {
            payload =
                "{\"gateway_ready\":true,"
                "\"remote_execution_ready\":true,"
                "\"execution_service_epoch\":\"epoch-malformed-test\","
                "\"execution_service_fencing_generation\":3,"
                "\"event_watermark\":1}";
            return true;
        };
        callbacks.marketGetQuote = [](
            const TradingToolSession&, const TradingToolCall& call,
            std::string& payload, std::string&) {
            payload =
                "{\"authoritative\":true,\"instrument\":\"" +
                call.instrument + "\",\"observed_at_ms\":" +
                std::to_string(NowMs()) +
                ",\"stale\":false,\"bid\":1.1,\"ask\":1.2}";
            return true;
        };
        callbacks.accountGetSummary = [](
            const TradingToolSession&, const TradingToolCall&,
            std::string& payload, std::string&) {
            payload = "{\"authoritative\":true}";
            return true;
        };
        callbacks.portfolioListPositions = [this](
            const TradingToolSession&, const TradingToolCall&,
            std::string& payload, std::string&) {
            payload = positionsPayload;
            return true;
        };
        callbacks.ordersList = [](
            const TradingToolSession&, const TradingToolCall&,
            std::string& payload, std::string&) {
            payload = "{\"authoritative\":true,\"orders\":[]}";
            return true;
        };
        callbacks.riskGetLimits = [](
            const TradingToolSession&, const TradingToolCall&,
            std::string& payload, std::string&) {
            payload =
                "{\"authoritative\":true,\"max_order_quantity\":1000}";
            return true;
        };
        callbacks.riskPreviewOrder = [this](
            const TradingToolSession&, const TradingToolCall& call,
            std::string& payload, std::string&) {
            ++previewCalls;
            payload =
                "{\"authoritative\":true,\"preview_permit\":\"sha256:"
                "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
                "\",\"mutation_command_id\":\"malformed-preview-command\","
                "\"expires_at_ms\":" + std::to_string(call.expiresAtMs) +
                "}";
            return true;
        };

        session.executionContext.agentId = "malformed-agent";
        session.executionContext.sessionId = "malformed-session";
        session.executionContext.toolCallId = "malformed-snapshot-call";
        session.executionContext.account = "SIM";
        session.executionContext.venue = "SIMULATOR";
        session.executionContext.executionDomain = "SIM:malformed";
        session.environment = "PAPER";
        const char* capabilities[] = {
            "system.read", "market.read", "account.read", "portfolio.read",
            "orders.read", "risk.read", "intent.apply"
        };
        for (std::size_t i = 0;
             i < sizeof(capabilities) / sizeof(capabilities[0]); ++i)
            session.capabilities.insert(capabilities[i]);
        session.visibleInstruments.insert("EUR.USD");
        InstrumentRef contract;
        contract.symbol = "EUR";
        contract.currency = "USD";
        contract.secType = "CASH";
        contract.exchange = "IDEALPRO";
        session.boundInstrumentContracts["EUR.USD"] = contract;
        session.maxOrderQuantity = 1000.0;
    }

    std::string positionsPayload;
    FakeExecution execution;
    TradingToolReadCallbacks callbacks;
    TradingToolSession session;
    int previewCalls = 0;
};

void ExpectSnapshotAndPreviewRejected(const std::string& positionsPayload)
{
    Fixture fixture(positionsPayload);
    TradingToolRegistry registry(fixture.execution, fixture.callbacks);

    TradingToolCall snapshot;
    snapshot.name = "decision.get_snapshot";
    snapshot.instrument = "EUR.USD";
    TradingToolResult result = registry.Invoke(fixture.session, snapshot);
    assert(result.status == TradingToolCallStatus::Rejected);
    assert(result.reasonCode == "DECISION_SNAPSHOT_POSITION_INVALID");
    assert(result.payloadJson.empty());

    fixture.session.executionContext.toolCallId = "malformed-preview-call";
    TradingToolCall preview;
    preview.name = "intent.preview_target_position";
    preview.instrument = "EUR.USD";
    preview.ibOrder.totalQuantity = 100.0;
    preview.referencePrice = 5.0;
    preview.expiresAtMs = NowMs() + 30000;
    result = registry.Invoke(fixture.session, preview);
    assert(result.status == TradingToolCallStatus::Rejected);
    assert(result.reasonCode == "DECISION_SNAPSHOT_POSITION_INVALID");
    assert(result.payloadJson.empty());
    assert(fixture.previewCalls == 0);
    assert(fixture.execution.placeCalls == 0);
}

void TestMissingCollectionCannotAssertFlat()
{
    ExpectSnapshotAndPreviewRejected(
        "{\"authoritative\":true,\"not_positions\":[]}");
}

void TestMalformedIrrelevantRecordCannotBeIgnored()
{
    ExpectSnapshotAndPreviewRejected(
        "{\"authoritative\":true,\"positions\":["
        "{\"instrument\":\"USD.JPY\"},"
        "{\"instrument\":\"EUR.USD\",\"quantity\":10}]}" );
}
}

int main()
{
    TestMissingCollectionCannotAssertFlat();
    TestMalformedIrrelevantRecordCannotBeIgnored();
    return 0;
}
