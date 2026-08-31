#include "../HeptaTrade/tools/trading_tool_registry.h"
#include "../HeptaTrade/intent/bounded_json.h"

#include <chrono>
#include <cstdint>
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>

namespace
{
void Require(bool condition, const char* expression, int line)
{
    if (condition) return;
    std::cerr << "requirement failed at line " << line << ": "
    << expression << '\n';
    std::abort();
}
#define REQUIRE(expression) Require(static_cast<bool>(expression), #expression, __LINE__)

std::int64_t NowMs()
{
    return std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
}

std::string StringField(const std::string& json, const std::string& key)
{
    BoundedJsonValue value;
    std::string reason;
    REQUIRE(ParseBoundedJson(json, value, reason));
    const BoundedJsonValue* field = value.Find(key);
    std::string result;
    REQUIRE(field != nullptr && field->String(result));
    return result;
}

class FakeExecution : public ExecutionAuthority
{
public:
    ExecutionCommandResult PlaceOrder(const PlaceOrderCommand& command) override
    {
        ++placeCalls;
        lastPlace = command;
        if (throwPlace) throw std::runtime_error("test authority failure");
        ExecutionCommandResult result;
        result.status = rejectPlace ? ExecutionCommandStatus::Rejected :
            ExecutionCommandStatus::Accepted;
        result.commandId = command.context.toolCallId;
        result.orderId = rejectPlace ? -1 : 77;
        result.reasonCode = rejectPlace ? "EXECUTION_DECISION_LEASE_BUSY" :
            std::string();
        return result;
    }
    ExecutionCommandResult CancelOrder(const CancelOrderCommand& command) override
    {
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Accepted;
        result.commandId = command.context.toolCallId;
        result.orderId = command.orderId;
        return result;
    }
    ExecutionCommandResult FlattenPosition(
        const FlattenPositionCommand& command) override
    {
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Accepted;
        result.commandId = command.context.toolCallId;
        return result;
    }
    int placeCalls = 0;
    bool throwPlace = false;
    bool rejectPlace = false;
    PlaceOrderCommand lastPlace;
};

struct Fixture
{
    Fixture()
    {
        callbacks.systemGetHealth = [this](const TradingToolSession&,
      const TradingToolCall&, std::string& payload, std::string&) {
  payload = "{\"event_watermark\":" + std::to_string(eventWatermark) +
      ",\"execution_service_fencing_generation\":9,"
      "\"gateway_ready\":true,"
      "\"remote_execution_ready\":true,"
      "\"execution_service_epoch\":\"epoch-a\"}";
  return true;
        };
        callbacks.marketGetQuote = [this](const TradingToolSession&,
      const TradingToolCall& call, std::string& payload, std::string&) {
  // The snapshot codec binds quote freshness to the collection window. Stamp
  // the fixture on its first read (after `started`) and keep that authoritative
  // quote identity stable for the apply re-read used by permit binding.
  const std::string quoteStateKey =
      std::to_string(position) + ":" + std::to_string(eventWatermark) +
      ":" + std::to_string(bid) + ":" + std::to_string(ask);
  if (!quoteStamped || quoteStateKey != quoteState) {
      quoteObservedAtMs = NowMs();
      quoteState = quoteStateKey;
      quoteStamped = true;
  }
  payload = "{\"ask\":" + std::to_string(ask) +
      ",\"authoritative\":true,\"stale\":false,"
      "\"observed_at_ms\":" + std::to_string(quoteObservedAtMs) +
      ",\"instrument\":\"" + call.instrument +
      "\",\"bid\":" + std::to_string(bid) + "}";
  return true;
        };
        callbacks.accountGetSummary = [](const TradingToolSession&,
      const TradingToolCall&, std::string& payload, std::string&) {
  payload = "{\"authoritative\":true}";
  return true;
        };
        callbacks.portfolioListPositions = [this](const TradingToolSession&,
      const TradingToolCall&, std::string& payload, std::string&) {
  payload = "{\"positions\":[{\"quantity\":" +
      std::to_string(position) +
      ",\"instrument\":\"EUR.USD\"}],\"authoritative\":true}";
  return true;
        };
        callbacks.ordersList = [](const TradingToolSession&,
      const TradingToolCall&, std::string& payload, std::string&) {
  payload = "{\"orders\":[],\"authoritative\":true}";
  return true;
        };
        callbacks.riskGetLimits = [](const TradingToolSession&,
      const TradingToolCall&, std::string& payload, std::string&) {
  payload = "{\"max_order_quantity\":25000,\"authoritative\":true}";
  return true;
        };
        callbacks.riskPreviewOrder = [this](const TradingToolSession& session,
      const TradingToolCall& call, std::string& payload, std::string&) {
  ++previewCalls;
  previewOrder = call;
  mutationId = "mutation-" + std::to_string(previewCalls) + "-000000";
  payload = "{\"authoritative\":true,\"preview_permit\":\""
      "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
      "\",\"mutation_command_id\":\"" + mutationId +
      "\",\"expires_at_ms\":" + std::to_string(call.expiresAtMs) + "}";
  return session.executionContext.account == "SIM";
        };

        session.executionContext.agentId = "intent-agent";
        session.executionContext.sessionId = "intent-session";
        session.executionContext.toolCallId = "initial-call";
        session.executionContext.account = "SIM";
        session.executionContext.venue = "SIMULATOR";
        session.executionContext.executionDomain = "SIM:intent-agent";
        session.environment = "PAPER";
        const char* capabilities[] = {
  "system.read", "market.read", "account.read", "portfolio.read",
  "orders.read", "risk.read", "intent.apply", "trade.cancel"
        };
        for (std::size_t i = 0; i < sizeof(capabilities) / sizeof(capabilities[0]); ++i)
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

    FakeExecution execution;
    TradingToolReadCallbacks callbacks;
    TradingToolSession session;
    std::uint64_t eventWatermark = 5;
    double position = 10.0;
    double bid = 1.1000;
    double ask = 1.1002;
    std::int64_t quoteObservedAtMs = 0;
    bool quoteStamped = false;
    std::string quoteState;
    int previewCalls = 0;
    std::string mutationId;
    TradingToolCall previewOrder;
};

bool Visible(const TradingToolRegistry& registry,
   const TradingToolSession& session,
   const std::string& name)
{
    const std::vector<TradingToolDescriptor> tools = registry.ListTools(session);
    for (std::size_t i = 0; i < tools.size(); ++i)
        if (tools[i].name == name) return true;
    return false;
}

TradingToolCall Target(const char* name, double target, std::int64_t expires)
{
    TradingToolCall call;
    call.name = name;
    call.instrument = "EUR.USD";
    call.ibOrder.totalQuantity = target;
    call.referencePrice = 5.0;
    call.expiresAtMs = expires;
    return call;
}

void TestIntentToolsAndRawOperatorBoundary()
{
    Fixture fixture;
    TradingToolRegistry registry(fixture.execution, fixture.callbacks);
    REQUIRE(Visible(registry, fixture.session, "decision.get_snapshot"));
    REQUIRE(Visible(registry, fixture.session, "intent.preview_target_position"));
    REQUIRE(Visible(registry, fixture.session, "intent.apply_target_position"));
    REQUIRE(!Visible(registry, fixture.session, "risk.preview_order"));
    REQUIRE(!Visible(registry, fixture.session, "trade.place_order"));

    TradingToolSession operatorSession = fixture.session;
    operatorSession.capabilities.insert("operator.risk.preview");
    operatorSession.capabilities.insert("operator.trade.place");
    REQUIRE(Visible(registry, operatorSession, "risk.preview_order"));
    REQUIRE(Visible(registry, operatorSession, "trade.place_order"));

    TradingToolSession watch = fixture.session;
    watch.environment = "WATCH";
    REQUIRE(!Visible(registry, watch, "intent.apply_target_position"));
}

void TestPreviewApplySingleUseAndGenerationBinding()
{
    Fixture fixture;
    TradingToolRegistry registry(fixture.execution, fixture.callbacks);
    const std::int64_t expires = NowMs() + 30000;
    TradingToolCall preview = Target(
        "intent.preview_target_position", 100.0, expires);
    fixture.session.executionContext.toolCallId = "preview-target-1";
    TradingToolResult result = registry.Invoke(fixture.session, preview);
    REQUIRE(result.status == TradingToolCallStatus::Ok);
    const std::string targetPermit = StringField(result.payloadJson, "preview_permit");
    const std::string mutationId = StringField(result.payloadJson, "mutation_command_id");
    REQUIRE(fixture.previewCalls == 1);
    REQUIRE(fixture.previewOrder.ibOrder.action == "BUY");
    REQUIRE(fixture.previewOrder.ibOrder.totalQuantity == 90.0);

    // Force the apply re-read to start after the cached quote timestamp.  An
    // unchanged authority generation must still revalidate successfully by
    // reusing its original collection window floor.
    std::this_thread::sleep_for(std::chrono::milliseconds(3));

    TradingToolCall apply = Target(
        "intent.apply_target_position", 100.0, expires);
    apply.previewPermit = targetPermit;
    fixture.session.executionContext.toolCallId = mutationId;
    result = registry.Invoke(fixture.session, apply);
    REQUIRE(result.status == TradingToolCallStatus::Ok);
    REQUIRE(fixture.execution.placeCalls == 1);
    REQUIRE(fixture.execution.lastPlace.context.toolCallId == mutationId);
    REQUIRE(fixture.execution.lastPlace.previewPermit.find("sha256:") == 0);
    REQUIRE(fixture.execution.lastPlace.order.totalQuantity == 90.0);
    REQUIRE(fixture.execution.lastPlace.order.action == "BUY");
    REQUIRE(fixture.execution.lastPlace.order.lmtPrice > fixture.ask);

    result = registry.Invoke(fixture.session, apply);
    REQUIRE(result.status == TradingToolCallStatus::Duplicate);
    REQUIRE(result.reasonCode == "DUPLICATE_TOOL_CALL");
    REQUIRE(fixture.execution.placeCalls == 1);

    // A changed target request is a validation failure, not a permit
    // consumption.  The exact normalized request remains retryable.
    fixture.session.executionContext.toolCallId = "preview-target-2";
    preview = Target("intent.preview_target_position", 120.0, expires);
    result = registry.Invoke(fixture.session, preview);
    REQUIRE(result.status == TradingToolCallStatus::Ok);
    const std::string changedPermit =
        StringField(result.payloadJson, "preview_permit");
    const std::string changedMutation =
        StringField(result.payloadJson, "mutation_command_id");
    TradingToolCall changedApply = Target(
        "intent.apply_target_position", 121.0, expires);
    changedApply.previewPermit = changedPermit;
    fixture.session.executionContext.toolCallId = changedMutation;
    result = registry.Invoke(fixture.session, changedApply);
    REQUIRE(result.status == TradingToolCallStatus::Rejected);
    REQUIRE(result.reasonCode == "TARGET_PREVIEW_REQUEST_CHANGED");
    TradingToolCall exactApply = Target(
        "intent.apply_target_position", 120.0, expires);
    exactApply.previewPermit = changedPermit;
    result = registry.Invoke(fixture.session, exactApply);
    REQUIRE(result.status == TradingToolCallStatus::Ok);
    REQUIRE(fixture.execution.placeCalls == 2);

    fixture.session.executionContext.toolCallId = "preview-target-3";
    preview = Target("intent.preview_target_position", -20.0, expires);
    result = registry.Invoke(fixture.session, preview);
    REQUIRE(result.status == TradingToolCallStatus::Ok);
    const std::string permit2 = StringField(result.payloadJson, "preview_permit");
    const std::string mutation2 = StringField(result.payloadJson, "mutation_command_id");
    fixture.position = 11.0;
    fixture.session.executionContext.toolCallId = mutation2;
    apply = Target("intent.apply_target_position", -20.0, expires);
    apply.previewPermit = permit2;
    result = registry.Invoke(fixture.session, apply);
    REQUIRE(result.status == TradingToolCallStatus::Rejected);
    REQUIRE(result.reasonCode == "TARGET_PREVIEW_GENERATION_CHANGED");
    // Restoring the component values does not restore the monotonic
    // collection generation.  Once authoritative state changed, this permit
    // remains stale (and must not be mistaken for a fresh generation).
    fixture.position = 10.0;
    result = registry.Invoke(fixture.session, apply);
    REQUIRE(result.status == TradingToolCallStatus::Rejected);
    REQUIRE(result.reasonCode == "TARGET_PREVIEW_GENERATION_CHANGED");
    REQUIRE(fixture.execution.placeCalls == 2);
}

void TestPreviewApplyOwnerBinding()
{
    Fixture fixture;
    TradingToolRegistry registry(fixture.execution, fixture.callbacks);
    const std::int64_t expires = NowMs() + 30000;
    fixture.session.executionContext.toolCallId = "owner-preview-1";
    TradingToolResult result = registry.Invoke(
        fixture.session,
        Target("intent.preview_target_position", 100.0, expires));
    REQUIRE(result.status == TradingToolCallStatus::Ok);
    const std::string permit = StringField(result.payloadJson, "preview_permit");
    const std::string mutation = StringField(
        result.payloadJson, "mutation_command_id");

    TradingToolCall apply = Target(
        "intent.apply_target_position", 100.0, expires);
    apply.previewPermit = permit;
    fixture.session.executionContext.toolCallId = mutation;
    // A permit issued to one account must not be usable after the session is
    // rebound to another owner, even when all target fields are unchanged.
    fixture.session.executionContext.account = "OTHER";
    result = registry.Invoke(fixture.session, apply);
    REQUIRE(result.status == TradingToolCallStatus::Rejected);
    REQUIRE(result.reasonCode == "TARGET_PREVIEW_PERMIT_BINDING_MISMATCH");
    fixture.session.executionContext.account = "SIM";

    // The failed owner check does not consume the permit; the exact original
    // owner can still apply it once.
    result = registry.Invoke(fixture.session, apply);
    REQUIRE(result.status == TradingToolCallStatus::Ok);
    REQUIRE(fixture.execution.placeCalls == 1);
}

void TestTargetDerivedQuantityUsesSessionLimit()
{
    Fixture fixture;
    fixture.session.maxOrderQuantity = 50.0;
    TradingToolRegistry registry(fixture.execution, fixture.callbacks);
    const std::int64_t expires = NowMs() + 30000;
    TradingToolResult result = registry.Invoke(
        fixture.session,
        Target("intent.preview_target_position", 100.0, expires));
    REQUIRE(result.status == TradingToolCallStatus::Rejected);
    REQUIRE(result.reasonCode == "INTENT_ORDER_QUANTITY_LIMIT");

    // A no-op target does not create an order and therefore remains valid even
    // when the absolute target is larger than one individual order limit.
    result = registry.Invoke(
        fixture.session,
        Target("intent.preview_target_position", 10.0, expires));
    REQUIRE(result.status == TradingToolCallStatus::Ok);
}

void TestDecisionSnapshotAcceptsReorderedFieldsAndRejectsDuplicatePosition()
{
    Fixture fixture;
    TradingToolRegistry registry(fixture.execution, fixture.callbacks);
    TradingToolCall snapshotCall;
    snapshotCall.name = "decision.get_snapshot";
    snapshotCall.instrument = "EUR.USD";
    TradingToolResult result = registry.Invoke(fixture.session, snapshotCall);
    REQUIRE(result.status == TradingToolCallStatus::Ok);
    REQUIRE(result.payloadJson.find("\"current_position\":10") != std::string::npos);

    // A newly started authoritative event feed can legitimately report
    // latestSequence=0 until its first publication.  The typed snapshot and
    // target contract preserve that value instead of fabricating a readiness
    // event or rejecting an otherwise coherent snapshot.
    Fixture emptyFeedFixture;
    emptyFeedFixture.eventWatermark = 0;
    TradingToolRegistry emptyFeedRegistry(
        emptyFeedFixture.execution, emptyFeedFixture.callbacks);
    result = emptyFeedRegistry.Invoke(emptyFeedFixture.session, snapshotCall);
    REQUIRE(result.status == TradingToolCallStatus::Ok);
    REQUIRE(result.payloadJson.find("\"event_watermark\":0") !=
        std::string::npos);

    fixture.callbacks.portfolioListPositions = [](const TradingToolSession&,
  const TradingToolCall&, std::string& payload, std::string&) {
        payload = "{\"authoritative\":true,\"positions\":["
  "{\"instrument\":\"EUR.USD\",\"quantity\":1},"
  "{\"quantity\":2,\"instrument\":\"EUR.USD\"}]}";
        return true;
    };
    // This is a fresh registry/collection.  Do not reuse the first
    // collection's cached quote timestamp: an independent authority read
    // must stamp a quote inside its own [started, completed] window.
    fixture.quoteStamped = false;
    fixture.quoteObservedAtMs = 0;
    fixture.quoteState.clear();
    TradingToolRegistry duplicateRegistry(fixture.execution, fixture.callbacks);
    result = duplicateRegistry.Invoke(fixture.session, snapshotCall);
    REQUIRE(result.status == TradingToolCallStatus::Rejected);
    REQUIRE(result.reasonCode == "DECISION_SNAPSHOT_POSITION_INVALID");
}

void TestTargetPreviewRequiresCanonicalAuthorityBinding()
{
    Fixture fixture;
    const std::int64_t expires = NowMs() + 30000;
    TradingToolCall preview = Target(
        "intent.preview_target_position", 100.0, expires);
    fixture.session.executionContext.toolCallId = "binding-check-1";

    // Missing callback expiry must not silently inherit the Agent request's
    // expiry.  The response is an authority-bound error, not a usable permit.
    fixture.callbacks.riskPreviewOrder = [](
        const TradingToolSession&, const TradingToolCall&,
        std::string& payload, std::string&) {
        payload = "{\"authoritative\":true,\"preview_permit\":\"sha256:"
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
            "\",\"mutation_command_id\":\"binding-command-1\"}";
        return true;
    };
    TradingToolRegistry missingExpiryRegistry(
        fixture.execution, fixture.callbacks);
    TradingToolResult result =
        missingExpiryRegistry.Invoke(fixture.session, preview);
    REQUIRE(result.status == TradingToolCallStatus::Error);
    REQUIRE(result.reasonCode == "TARGET_PREVIEW_RESPONSE_INVALID");

    // A non-canonical permit (uppercase hex) is rejected at the same boundary.
    fixture.callbacks.riskPreviewOrder = [expires](
        const TradingToolSession&, const TradingToolCall&,
        std::string& payload, std::string&) {
        payload = "{\"authoritative\":true,\"preview_permit\":\"sha256:"
            "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
            "\",\"mutation_command_id\":\"binding-command-2\","
            "\"expires_at_ms\":" + std::to_string(expires) + "}";
        return true;
    };
    // This is another fresh registry/collection.  Reset the fixture's cached
    // quote witness so this case reaches the authority-response validation
    // boundary rather than failing earlier on a stale quote timestamp.
    fixture.session.executionContext.toolCallId = "binding-check-2";
    fixture.quoteStamped = false;
    fixture.quoteObservedAtMs = 0;
    fixture.quoteState.clear();
    TradingToolRegistry nonCanonicalRegistry(
        fixture.execution, fixture.callbacks);
    result = nonCanonicalRegistry.Invoke(fixture.session, preview);
    REQUIRE(result.status == TradingToolCallStatus::Error);
    REQUIRE(result.reasonCode == "TARGET_PREVIEW_RESPONSE_INVALID");
}

void TestTargetPreviewAndSnapshotExceptionsDoNotLeakDetails()
{
    Fixture fixture;
    const std::int64_t expires = NowMs() + 30000;
    fixture.session.executionContext.toolCallId = "exception-boundary-1";
    fixture.callbacks.riskPreviewOrder = [](
        const TradingToolSession&, const TradingToolCall&,
        std::string&, std::string&) -> bool {
        throw std::runtime_error("/private/venue/socket credential=secret");
    };
    TradingToolRegistry registry(fixture.execution, fixture.callbacks);
    TradingToolResult result = registry.Invoke(
        fixture.session,
        Target("intent.preview_target_position", 100.0, expires));
    REQUIRE(result.status == TradingToolCallStatus::Rejected);
    REQUIRE(result.reasonCode == "TARGET_PREVIEW_AUTHORITY_EXCEPTION");
    REQUIRE(result.detail.empty());
    REQUIRE(result.detail.find("secret") == std::string::npos);

    Fixture snapshotFixture;
    snapshotFixture.callbacks.systemGetHealth = [](
        const TradingToolSession&, const TradingToolCall&,
        std::string&, std::string&) -> bool {
        throw std::runtime_error("/private/health/path credential=secret");
    };
    TradingToolRegistry snapshotRegistry(
        snapshotFixture.execution, snapshotFixture.callbacks);
    TradingToolCall snapshot;
    snapshot.name = "decision.get_snapshot";
    snapshot.instrument = "EUR.USD";
    result = snapshotRegistry.Invoke(snapshotFixture.session, snapshot);
    REQUIRE(result.status == TradingToolCallStatus::Rejected);
    REQUIRE(result.reasonCode == "DECISION_SNAPSHOT_SUBREAD_EXCEPTION");
    REQUIRE(result.detail.empty());
}

void TestTargetApplyUsesExactNumericBinding()
{
    Fixture fixture;
    TradingToolRegistry registry(fixture.execution, fixture.callbacks);
    const std::int64_t expires = NowMs() + 30000;
    fixture.session.executionContext.toolCallId = "exact-number-preview";
    TradingToolResult result = registry.Invoke(
        fixture.session,
        Target("intent.preview_target_position", 100.0, expires));
    REQUIRE(result.status == TradingToolCallStatus::Ok);
    const std::string permit = StringField(result.payloadJson, "preview_permit");
    const std::string mutation = StringField(
        result.payloadJson, "mutation_command_id");

    fixture.session.executionContext.toolCallId = mutation;
    TradingToolCall changed = Target(
        "intent.apply_target_position", std::nextafter(100.0, 101.0), expires);
    changed.previewPermit = permit;
    result = registry.Invoke(fixture.session, changed);
    REQUIRE(result.status == TradingToolCallStatus::Rejected);
    REQUIRE(result.reasonCode == "TARGET_PREVIEW_REQUEST_CHANGED");
    REQUIRE(fixture.execution.placeCalls == 0);

    TradingToolCall exact = Target(
        "intent.apply_target_position", 100.0, expires);
    exact.previewPermit = permit;
    result = registry.Invoke(fixture.session, exact);
    REQUIRE(result.status == TradingToolCallStatus::Ok);
    REQUIRE(fixture.execution.placeCalls == 1);
}

void TestNoOpApplyIsSingleUseAndReplayable()
{
    Fixture fixture;
    TradingToolRegistry registry(fixture.execution, fixture.callbacks);
    const std::int64_t expires = NowMs() + 30000;
    fixture.session.executionContext.toolCallId = "noop-preview-1";
    TradingToolResult result = registry.Invoke(
        fixture.session,
        Target("intent.preview_target_position", fixture.position, expires));
    REQUIRE(result.status == TradingToolCallStatus::Ok);
    const std::string permit = StringField(result.payloadJson, "preview_permit");
    const std::string mutation = StringField(
        result.payloadJson, "mutation_command_id");

    fixture.session.executionContext.toolCallId = mutation;
    TradingToolCall apply = Target(
        "intent.apply_target_position", fixture.position, expires);
    apply.previewPermit = permit;
    result = registry.Invoke(fixture.session, apply);
    REQUIRE(result.status == TradingToolCallStatus::Ok);
    REQUIRE(result.reasonCode == "INTENT_NO_CHANGE");
    REQUIRE(fixture.execution.placeCalls == 0);

    result = registry.Invoke(fixture.session, apply);
    REQUIRE(result.status == TradingToolCallStatus::Duplicate);
    REQUIRE(result.reasonCode == "DUPLICATE_TOOL_CALL");
    REQUIRE(fixture.execution.placeCalls == 0);
}

void TestAuthorityExceptionBecomesDeterministicUncertainReplay()
{
    Fixture fixture;
    TradingToolRegistry registry(fixture.execution, fixture.callbacks);
    const std::int64_t expires = NowMs() + 30000;
    fixture.session.executionContext.toolCallId = "exception-preview-1";
    TradingToolResult result = registry.Invoke(
        fixture.session,
        Target("intent.preview_target_position", 100.0, expires));
    REQUIRE(result.status == TradingToolCallStatus::Ok);
    const std::string permit = StringField(result.payloadJson, "preview_permit");
    const std::string mutation = StringField(
        result.payloadJson, "mutation_command_id");
    TradingToolCall apply = Target(
        "intent.apply_target_position", 100.0, expires);
    apply.previewPermit = permit;
    fixture.session.executionContext.toolCallId = mutation;
    fixture.execution.throwPlace = true;
    result = registry.Invoke(fixture.session, apply);
    REQUIRE(result.status == TradingToolCallStatus::Uncertain);
    REQUIRE(result.reasonCode == "EXECUTION_AUTHORITY_EXCEPTION");

    // The target permit has already crossed the dispatch boundary; retries
    // return the cached uncertain result instead of invoking the authority a
    // second time.
    fixture.execution.throwPlace = false;
    result = registry.Invoke(fixture.session, apply);
    REQUIRE(result.status == TradingToolCallStatus::Uncertain);
    REQUIRE(result.reasonCode == "EXECUTION_AUTHORITY_EXCEPTION");
    REQUIRE(fixture.execution.placeCalls == 1);
}

void TestPreDispatchRejectionLeavesTargetPermitRetryable()
{
    Fixture fixture;
    TradingToolRegistry registry(fixture.execution, fixture.callbacks);
    const std::int64_t expires = NowMs() + 30000;
    fixture.session.executionContext.toolCallId = "retryable-reject-preview";
    TradingToolResult result = registry.Invoke(
        fixture.session,
        Target("intent.preview_target_position", 100.0, expires));
    REQUIRE(result.status == TradingToolCallStatus::Ok);
    const std::string permit = StringField(result.payloadJson, "preview_permit");
    const std::string mutation = StringField(
        result.payloadJson, "mutation_command_id");
    TradingToolCall apply = Target(
        "intent.apply_target_position", 100.0, expires);
    apply.previewPermit = permit;
    fixture.session.executionContext.toolCallId = mutation;

    // A rejection returned before the authority's commit point must not
    // strand the Agent-facing target permit or create a terminal replay.
    fixture.execution.rejectPlace = true;
    result = registry.Invoke(fixture.session, apply);
    REQUIRE(result.status == TradingToolCallStatus::Rejected);
    REQUIRE(result.reasonCode == "EXECUTION_DECISION_LEASE_BUSY");
    REQUIRE(fixture.execution.placeCalls == 1);

    fixture.execution.rejectPlace = false;
    result = registry.Invoke(fixture.session, apply);
    REQUIRE(result.status == TradingToolCallStatus::Ok);
    REQUIRE(fixture.execution.placeCalls == 2);
    result = registry.Invoke(fixture.session, apply);
    REQUIRE(result.status == TradingToolCallStatus::Duplicate);
    REQUIRE(fixture.execution.placeCalls == 2);
}

void TestOwnerFenceRevokesTargetPermitAndReplayWitness()
{
    Fixture fixture;
    TradingToolRegistry registry(fixture.execution, fixture.callbacks);
    const std::int64_t expires = NowMs() + 30000;

    // A permit issued before an owner fence must not remain usable after the
    // host rotates/revokes the bearer.  This exercises the registry-side
    // state, which is intentionally separate from the host replay cache.
    fixture.session.executionContext.toolCallId = "owner-fence-preview-1";
    TradingToolResult result = registry.Invoke(
        fixture.session,
        Target("intent.preview_target_position", 100.0, expires));
    REQUIRE(result.status == TradingToolCallStatus::Ok);
    const std::string permit = StringField(result.payloadJson, "preview_permit");
    const std::string mutation = StringField(
        result.payloadJson, "mutation_command_id");
    fixture.session.executionContext.toolCallId = mutation;
    TradingToolCall apply = Target(
        "intent.apply_target_position", 100.0, expires);
    apply.previewPermit = permit;

    registry.RevokeTargetPermitsForIdentity(
        fixture.session.executionContext.agentId,
        fixture.session.executionContext.sessionId);
    result = registry.Invoke(fixture.session, apply);
    REQUIRE(result.status == TradingToolCallStatus::Rejected);
    REQUIRE(result.reasonCode == "TARGET_PREVIEW_PERMIT_UNKNOWN");
    REQUIRE(fixture.execution.placeCalls == 0);

    // Also verify that a completed target apply's local replay witness is
    // removed by the exact-owner lifecycle hook.
    fixture.session.executionContext.toolCallId = "owner-fence-preview-2";
    result = registry.Invoke(
        fixture.session,
        Target("intent.preview_target_position", 100.0, expires));
    REQUIRE(result.status == TradingToolCallStatus::Ok);
    const std::string permit2 = StringField(result.payloadJson, "preview_permit");
    const std::string mutation2 = StringField(
        result.payloadJson, "mutation_command_id");
    fixture.session.executionContext.toolCallId = mutation2;
    apply.previewPermit = permit2;
    result = registry.Invoke(fixture.session, apply);
    REQUIRE(result.status == TradingToolCallStatus::Ok);
    REQUIRE(fixture.execution.placeCalls == 1);
    registry.RevokeTargetPermitsForOwner(fixture.session);
    result = registry.Invoke(fixture.session, apply);
    REQUIRE(result.status == TradingToolCallStatus::Rejected);
    REQUIRE(result.reasonCode == "TARGET_PREVIEW_PERMIT_UNKNOWN");
    REQUIRE(fixture.execution.placeCalls == 1);
}
}

int main()
{
    TestIntentToolsAndRawOperatorBoundary();
    TestPreviewApplySingleUseAndGenerationBinding();
    TestPreviewApplyOwnerBinding();
    TestTargetDerivedQuantityUsesSessionLimit();
    TestDecisionSnapshotAcceptsReorderedFieldsAndRejectsDuplicatePosition();
    TestTargetPreviewRequiresCanonicalAuthorityBinding();
    TestTargetPreviewAndSnapshotExceptionsDoNotLeakDetails();
    TestTargetApplyUsesExactNumericBinding();
    TestNoOpApplyIsSingleUseAndReplayable();
    TestAuthorityExceptionBecomesDeterministicUncertainReplay();
    TestPreDispatchRejectionLeavesTargetPermitRetryable();
    TestOwnerFenceRevokesTargetPermitAndReplayWitness();
    return 0;
}
