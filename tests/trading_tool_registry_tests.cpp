#include "../HeptaTrade/tools/trading_tool_registry.h"
#include "../HeptaTrade/tools/trading_tool_wire_contract.h"
#include "../HeptaTrade/execution/execution_coordinator.h"
#include "../HeptaTrade/events/execution_event_hub.h"
#include "../HeptaTrade/agent/decision_lease_manager.h"

#include <cassert>
#include <chrono>
#include <cstdio>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <unistd.h>

namespace {

std::string TempJournalPath()
{
    char path[] = "/tmp/hepta-tool-registry-XXXXXX";
    const int fd = mkstemp(path);
    assert(fd >= 0);
    close(fd);
    return std::string(path);
}

TradingToolSession MakeSession(bool allowTrade)
{
    TradingToolSession session;
    session.executionContext.agentId = "agent-native-test";
    session.executionContext.sessionId = "session-paper";
    session.executionContext.toolCallId = "tool-call-place";
    session.executionContext.strategy = "agent_tool_test";
    session.executionContext.account = "DU123";
    session.executionContext.executionDomain = "IB-PAPER";
    session.environment = "PAPER";
    session.capabilities.insert("market.read");
    session.capabilities.insert("events.read");
    session.capabilities.insert("system.read");
    if (allowTrade)
    {
        session.capabilities.insert("risk.preview");
        session.capabilities.insert("trade.place");
        session.capabilities.insert("trade.cancel");
    }
    return session;
}

std::string ExtractPreviewPermit(const TradingToolResult& result)
{
    const std::string marker = "\"preview_permit\":\"";
    const std::size_t start = result.payloadJson.find(marker);
    assert(start != std::string::npos);
    const std::size_t valueStart = start + marker.size();
    const std::size_t end = result.payloadJson.find('"', valueStart);
    assert(end != std::string::npos);
    return result.payloadJson.substr(valueStart, end - valueStart);
}

int ToolTimeout(const std::vector<TradingToolDescriptor>& tools,
                const std::string& name)
{
    for (std::size_t i = 0; i < tools.size(); ++i)
    {
        if (tools[i].name == name)
            return tools[i].timeoutMs;
    }
    assert(false);
    return -1;
}

std::string ToolInputSchema(
    const std::vector<TradingToolDescriptor>& tools,
    const std::string& name)
{
    for (const TradingToolDescriptor& tool : tools)
    {
        if (tool.name == name)
            return tool.inputSchema;
    }
    assert(false);
    return std::string();
}

bool HasTool(const std::vector<TradingToolDescriptor>& tools,
             const std::string& name)
{
    for (std::size_t i = 0; i < tools.size(); ++i)
        if (tools[i].name == name) return true;
    return false;
}

TradingToolCall MakePlace()
{
    TradingToolCall call;
    call.name = "trade.place_order";
    call.instrument = "EUR.USD";
    call.ibContract.symbol = "EUR";
    call.ibContract.currency = "USD";
    call.ibContract.secType = "CASH";
    call.ibContract.exchange = "IDEALPRO";
    call.ibOrder.action = "BUY";
    call.ibOrder.orderType = "LMT";
    call.timeInForce = "DAY";
    call.ibOrder.totalQuantity = 1000.0;
    call.ibOrder.lmtPrice = 1.1;
    call.expiresAtMs = OmsJournal::NowEpochMs() + 60000;
    return call;
}

void TestCapabilityFilteredRegistryAndDirectTrade()
{
    const std::string path = TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));

    int placeCalls = 0;
    int cancelCalls = 0;
    DecisionLeaseManager leases;
    ExecutionCoordinatorCallbacks executionCallbacks;
    executionCallbacks.placeIbOrder = [&](const IBContractLite&, const IBOrderLite&, long* orderId) {
        ++placeCalls;
        *orderId = 701;
        return true;
    };
    executionCallbacks.cancelIbOrder = [&](long orderId) {
        ++cancelCalls;
        return orderId == 701;
    };
    executionCallbacks.validateDecisionLease = [&](const AgentExecutionContext& context,
                                                    const std::string& instrument,
                                                    std::string* reason) {
        DecisionLeaseKey key;
        key.executionDomain = context.executionDomain;
        key.account = context.account;
        key.instrument = instrument;
        DecisionLeaseOwner owner;
        owner.agentId = context.agentId;
        owner.sessionId = context.sessionId;
        DecisionLeaseCredential credential;
        credential.fencingToken = context.decisionLeaseFencingToken;
        credential.generation = context.decisionLeaseGeneration;
        const DecisionLeaseResult result = leases.Validate(key, owner, credential);
        if (reason != nullptr) *reason = DecisionLeaseManager::StatusName(result.status);
        return result.status == DecisionLeaseStatus::Valid;
    };
    ExecutionCoordinator execution(journal, executionCallbacks);
    ExecutionEventHub eventHub(8);

    TradingToolReadCallbacks readCallbacks;
    int accountReads = 0;
    int positionReads = 0;
    int orderReads = 0;
    int commandStatusReads = 0;
    int riskReads = 0;
    int quoteReads = 0;
    int healthReads = 0;
    bool failOrders = false;
    std::string ordersPayloadOverride;
    readCallbacks.marketGetQuote = [&](const TradingToolSession&,
        const TradingToolCall& call, std::string& payload, std::string&) {
        ++quoteReads;
        payload = std::string("{\"instrument\":\"") + call.instrument +
            "\",\"bid\":1.1,\"ask\":1.2}";
        return true;
    };
    readCallbacks.accountGetSummary = [&](const TradingToolSession&,
        const TradingToolCall&, std::string& payload, std::string&) {
        ++accountReads;
        payload = "{\"authoritative\":true,\"account_complete\":true}";
        return true;
    };
    readCallbacks.portfolioListPositions = [&](const TradingToolSession&,
        const TradingToolCall&, std::string& payload, std::string&) {
        ++positionReads;
        payload = "{\"authoritative\":true,\"positions\":[]}";
        return true;
    };
    readCallbacks.ordersList = [&](const TradingToolSession&,
        const TradingToolCall&, std::string& payload, std::string& reason) {
        ++orderReads;
        if (failOrders)
        {
            reason = "AUTHORITATIVE_ORDERS_UNAVAILABLE";
            return false;
        }
        payload = ordersPayloadOverride.empty() ?
            "{\"authoritative\":true,\"active_order_ids\":[]}" :
            ordersPayloadOverride;
        return true;
    };
    readCallbacks.executionGetCommandStatus =
        [&](const TradingToolSession& session, const TradingToolCall& call,
            std::string& payload, std::string& reason) {
            ++commandStatusReads;
            assert(session.executionContext.agentId == "agent-native-test");
            assert(session.executionContext.sessionId == "session-paper");
            if (call.targetCommandId == "missing-command-001")
            {
                reason = "EXECUTION_COMMAND_NOT_FOUND";
                return false;
            }
            payload = "{\"authoritative\":true,\"command_id\":\"" +
                call.targetCommandId +
                "\",\"command_status\":\"accepted\",\"order_id\":701,"
                "\"reason_code\":\"\",\"execution_service_epoch\":\"epoch-1\","
                "\"execution_service_fencing_generation\":9}";
            return true;
        };
    readCallbacks.riskGetLimits = [&](const TradingToolSession&,
        const TradingToolCall&, std::string& payload, std::string&) {
        ++riskReads;
        payload = "{\"authoritative\":true,\"gross_absolute_position\":0}";
        return true;
    };
    readCallbacks.systemGetHealth = [&](const TradingToolSession&,
        const TradingToolCall&, std::string& payload, std::string&) {
        ++healthReads;
        payload = "{\"gateway_ready\":true}";
        return true;
    };
    readCallbacks.riskPreviewOrder = [](const TradingToolSession&, const TradingToolCall&,
                                        std::string& payload, std::string&) {
        payload = "{\"approved\":true,\"preview_permit\":\"sha256:"
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"}";
        return true;
    };
    readCallbacks.eventsWait = [&eventHub](const TradingToolSession& session, const TradingToolCall& call,
                                           std::string& payload, std::string& reason) {
        ExecutionEvent event;
        if (!eventHub.WaitNext(session.executionContext.executionDomain,
                               session.executionContext.agentId, session.executionContext.sessionId,
                               call.afterEventSequence,
                               call.waitTimeoutMs, event))
        {
            reason = "event wait timed out";
            return false;
        }
        payload = ExecutionEventHub::ToJson(event);
        return true;
    };
    TradingToolRegistry registry(execution, readCallbacks);

    TradingToolSession watch = MakeSession(false);
    TradingToolCall place = MakePlace();
    TradingToolResult denied = registry.Invoke(watch, place);
    assert(denied.status == TradingToolCallStatus::PermissionDenied);
    assert(placeCalls == 0);

    TradingToolSession watchWithSpoofedTradeCapability = MakeSession(true);
    watchWithSpoofedTradeCapability.environment = "WATCH";
    const TradingToolResult watchDenied = registry.Invoke(watchWithSpoofedTradeCapability, place);
    assert(watchDenied.reasonCode == "WATCH_SESSION_CANNOT_TRADE");
    const std::vector<TradingToolDescriptor> watchTools = registry.ListTools(watchWithSpoofedTradeCapability);
    for (std::size_t i = 0; i < watchTools.size(); ++i)
        assert(watchTools[i].effect == TradingToolEffect::Read);
    assert(ToolTimeout(watchTools, "market.get_quote") == 8000);
    assert(ToolInputSchema(watchTools, "market.get_quote") ==
        "{\"type\":\"object\",\"required\":[\"instrument\"],"
        "\"properties\":{\"instrument\":{\"type\":\"string\"}},"
        "\"additionalProperties\":false}");
    assert(ToolTimeout(watchTools, "events.wait") == 36000);
    assert(ToolTimeout(watchTools, "system.get_health") == 6000);

    TradingToolCall quote;
    quote.name = "market.get_quote";
    quote.instrument = "EUR.USD";
    TradingToolResult quoteResult = registry.Invoke(watch, quote);
    assert(quoteResult.status == TradingToolCallStatus::Ok);

    TradingToolSession statusSession = MakeSession(false);
    statusSession.capabilities.insert("orders.read");
    TradingToolCall commandStatus;
    commandStatus.name = "execution.get_command_status";
    commandStatus.targetCommandId = "place-command-001";
    const TradingToolResult commandStatusResult =
        registry.Invoke(statusSession, commandStatus);
    assert(commandStatusResult.status == TradingToolCallStatus::Ok);
    assert(commandStatusResult.payloadJson.find(
        "\"command_status\":\"accepted\"") != std::string::npos);
    assert(commandStatusResult.payloadJson.find(
        "\"order_id\":701") != std::string::npos);
    assert(commandStatusReads == 1);
    TradingToolDescriptor commandStatusDescriptor;
    assert(registry.GetDescriptor(
        "execution.get_command_status", commandStatusDescriptor));
    assert(commandStatusDescriptor.effect == TradingToolEffect::Read);
    assert(commandStatusDescriptor.requiredCapability == "orders.read");
    assert(commandStatusDescriptor.inputSchema.find(
        "\"required\":[\"command_id\"]") != std::string::npos);
    assert(commandStatusDescriptor.resultSchema.find(
        "\"command_status\"") != std::string::npos);
    assert(HasTool(registry.ListTools(statusSession),
                   "execution.get_command_status"));
    TradingToolSession noStatusCapability = statusSession;
    noStatusCapability.capabilities.erase("orders.read");
    assert(registry.Invoke(noStatusCapability, commandStatus).reasonCode ==
        "CAPABILITY_REQUIRED");
    TradingToolCall missingCommandStatus = commandStatus;
    missingCommandStatus.targetCommandId = "missing-command-001";
    const TradingToolResult missingCommand =
        registry.Invoke(statusSession, missingCommandStatus);
    assert(missingCommand.status == TradingToolCallStatus::Error);
    assert(missingCommand.reasonCode == "EXECUTION_COMMAND_NOT_FOUND");
    assert(missingCommand.detail.empty());
    assert(commandStatusReads == 2);
    TradingToolCall invalidCommandStatus = commandStatus;
    invalidCommandStatus.targetCommandId = "bad id";
    assert(registry.Invoke(statusSession, invalidCommandStatus).reasonCode ==
        "INVALID_COMMAND_ID");
    invalidCommandStatus = commandStatus;
    invalidCommandStatus.instrument = "EUR.USD";
    assert(registry.Invoke(statusSession, invalidCommandStatus).reasonCode ==
        "UNEXPECTED_TOOL_FIELD");
    assert(!HasTool(registry.ListTools(watch),
                    "execution.get_command_status"));

    TradingToolCall listTools;
    listTools.name = "system.tools.list";
    const TradingToolResult listResult = registry.Invoke(watch, listTools);
    assert(listResult.status == TradingToolCallStatus::Ok);
    assert(listResult.payloadJson.find("\"protocol\":\"hepta.agent-tools\"") != std::string::npos);
    assert(listResult.payloadJson.find("\"protocol_version\":1") != std::string::npos);
    assert(listResult.payloadJson.find("\"protocol_min_version\":1") != std::string::npos);
    assert(TradingToolRegistry::DiscoverySchemaVersion() == 2);
    assert(listResult.payloadJson.find("\"schema_version\":2") != std::string::npos);
    assert(listResult.payloadJson.find("\"catalog_schema_hash\":\"sha256:") != std::string::npos);
    assert(listResult.payloadJson.find("\"schema_hash\":\"sha256:") != std::string::npos);
    assert(listResult.payloadJson.find("\"name\":\"market.get_quote\"") != std::string::npos);
    assert(listResult.payloadJson.find("\"name\":\"trade.place_order\"") == std::string::npos);

    TradingToolCall describeTool;
    describeTool.name = "system.tools.describe";
    describeTool.targetToolName = "market.get_quote";
    const TradingToolResult describeResult = registry.Invoke(watch, describeTool);
    assert(describeResult.status == TradingToolCallStatus::Ok);
    assert(describeResult.payloadJson.find("\"required_capability\":\"market.read\"") != std::string::npos);
    assert(registry.CatalogSchemaHash(watch).size() == 71);
    describeTool.targetToolName = "trade.place_order";
    assert(registry.Invoke(watch, describeTool).reasonCode == "TOOL_NOT_VISIBLE");
    assert(quoteResult.payloadJson.find("EUR.USD") != std::string::npos);

    TradingToolSession snapshotWatch = watch;
    snapshotWatch.environment = "WATCH";
    snapshotWatch.capabilities.insert("account.read");
    snapshotWatch.capabilities.insert("portfolio.read");
    snapshotWatch.capabilities.insert("orders.read");
    snapshotWatch.capabilities.insert("risk.read");
    snapshotWatch.visibleInstruments.insert("EUR.USD");
    TradingToolCall watchSnapshot;
    watchSnapshot.name = "watch.get_snapshot";
    watchSnapshot.instrument = "EUR.USD";
    assert(HasTool(registry.ListTools(snapshotWatch), "watch.get_snapshot"));
    const int readsBeforeSnapshot = accountReads + positionReads + orderReads +
        riskReads + quoteReads + healthReads;
    const TradingToolResult snapshotResult =
        registry.Invoke(snapshotWatch, watchSnapshot);
    assert(snapshotResult.status == TradingToolCallStatus::Ok);
    assert(snapshotResult.payloadJson.find(
        "\"schema\":\"hepta.watch-read-set.v1\"") != std::string::npos);
    assert(snapshotResult.payloadJson.find(
        "\"catalog\":{\"protocol\":\"hepta.agent-tools\"") !=
        std::string::npos);
    assert(snapshotResult.payloadJson.find(
        "\"watch.get_snapshot\"") != std::string::npos);
    assert(accountReads + positionReads + orderReads + riskReads + quoteReads +
        healthReads == readsBeforeSnapshot + 6);

    const char* const snapshotCapabilities[] = {
        "system.read", "market.read", "account.read", "portfolio.read",
        "orders.read", "risk.read"
    };
    for (std::size_t i = 0;
         i < sizeof(snapshotCapabilities) / sizeof(snapshotCapabilities[0]);
         ++i)
    {
        TradingToolSession missingCapabilitySession = snapshotWatch;
        missingCapabilitySession.capabilities.erase(snapshotCapabilities[i]);
        assert(!HasTool(registry.ListTools(missingCapabilitySession),
                        "watch.get_snapshot"));
        const int readsBeforeMissingCapability = accountReads + positionReads +
            orderReads + riskReads + quoteReads + healthReads;
        const TradingToolResult missingCapability =
            registry.Invoke(missingCapabilitySession, watchSnapshot);
        assert(missingCapability.status ==
               TradingToolCallStatus::PermissionDenied);
        assert(missingCapability.reasonCode == "CAPABILITY_REQUIRED");
        assert(missingCapability.detail == snapshotCapabilities[i]);
        assert(accountReads + positionReads + orderReads + riskReads +
            quoteReads + healthReads == readsBeforeMissingCapability);
    }

    TradingToolSession paperSnapshot = snapshotWatch;
    paperSnapshot.environment = "PAPER";
    assert(!HasTool(registry.ListTools(paperSnapshot), "watch.get_snapshot"));
    const TradingToolResult wrongEnvironment =
        registry.Invoke(paperSnapshot, watchSnapshot);
    assert(wrongEnvironment.status == TradingToolCallStatus::PermissionDenied);
    assert(wrongEnvironment.reasonCode ==
        "WATCH_SNAPSHOT_ENVIRONMENT_REQUIRED");

    TradingToolCall unboundSnapshot = watchSnapshot;
    unboundSnapshot.instrument = "GBP.USD";
    const TradingToolResult unbound =
        registry.Invoke(snapshotWatch, unboundSnapshot);
    assert(unbound.status == TradingToolCallStatus::PermissionDenied);
    assert(unbound.reasonCode == "INSTRUMENT_NOT_ALLOWED");

    TradingToolCall emptyInstrument = watchSnapshot;
    emptyInstrument.instrument.clear();
    assert(registry.Invoke(snapshotWatch, emptyInstrument).reasonCode ==
           "MISSING_REQUIRED_FIELD");
    TradingToolCall unexpectedArgument = watchSnapshot;
    unexpectedArgument.targetToolName = "market.get_quote";
    assert(registry.Invoke(snapshotWatch, unexpectedArgument).reasonCode ==
           "UNEXPECTED_TOOL_FIELD");

    const int riskBeforeFailure = riskReads;
    const int quoteBeforeFailure = quoteReads;
    const int healthBeforeFailure = healthReads;
    failOrders = true;
    const TradingToolResult failedSnapshot =
        registry.Invoke(snapshotWatch, watchSnapshot);
    failOrders = false;
    assert(failedSnapshot.status == TradingToolCallStatus::Error);
    assert(failedSnapshot.reasonCode == "AUTHORITATIVE_ORDERS_UNAVAILABLE");
    assert(failedSnapshot.payloadJson.empty());
    assert(riskReads == riskBeforeFailure);
    assert(quoteReads == quoteBeforeFailure);
    assert(healthReads == healthBeforeFailure);

    // The compound response is accepted through the exact maximum wire
    // envelope byte, then fails closed at limit+1 with no partial payload.
    ordersPayloadOverride = "{\"padding\":\"\"}";
    const TradingToolResult emptyPaddingSnapshot =
        registry.Invoke(snapshotWatch, watchSnapshot);
    assert(emptyPaddingSnapshot.status == TradingToolCallStatus::Ok);
    TradingToolResult emptyPayloadEnvelope;
    emptyPayloadEnvelope.status = TradingToolCallStatus::Ok;
    emptyPayloadEnvelope.toolName = watchSnapshot.name;
    emptyPayloadEnvelope.payloadJson = "{}";
    const std::size_t envelopeOverhead =
        TradingToolWireContract::EncodeResultEnvelope(
            emptyPayloadEnvelope).size() -
        emptyPayloadEnvelope.payloadJson.size();
    const std::size_t maximumPayloadBytes =
        TradingToolWireLimits::MaximumResultEnvelopeBytes() -
        envelopeOverhead;
    assert(emptyPaddingSnapshot.payloadJson.size() <= maximumPayloadBytes);
    const std::size_t exactPaddingBytes =
        maximumPayloadBytes - emptyPaddingSnapshot.payloadJson.size();
    ordersPayloadOverride = "{\"padding\":\"" +
        std::string(exactPaddingBytes, 'x') + "\"}";
    const TradingToolResult exactLimitSnapshot =
        registry.Invoke(snapshotWatch, watchSnapshot);
    assert(exactLimitSnapshot.status == TradingToolCallStatus::Ok);
    assert(TradingToolWireContract::EncodeResultEnvelope(
        exactLimitSnapshot).size() ==
        TradingToolWireLimits::MaximumResultEnvelopeBytes());

    ordersPayloadOverride = "{\"padding\":\"" +
        std::string(exactPaddingBytes + 1, 'x') + "\"}";
    const TradingToolResult overLimitSnapshot =
        registry.Invoke(snapshotWatch, watchSnapshot);
    assert(overLimitSnapshot.status == TradingToolCallStatus::Error);
    assert(overLimitSnapshot.reasonCode ==
        "WATCH_SNAPSHOT_RESPONSE_TOO_LARGE");
    assert(overLimitSnapshot.detail.empty());
    assert(overLimitSnapshot.payloadJson.empty());
    assert(TradingToolWireContract::EncodeResultEnvelope(
        overLimitSnapshot).size() <=
        TradingToolWireLimits::MaximumResultEnvelopeBytes());
    ordersPayloadOverride.clear();

    TradingToolSession paper = MakeSession(true);
    TradingToolCall malformed = place;
    malformed.timeInForce.clear();
    assert(registry.Invoke(paper, malformed).reasonCode == "INVALID_TIME_IN_FORCE");
    malformed = place;
    malformed.ibOrder.action = "HOLD";
    assert(registry.Invoke(paper, malformed).reasonCode == "INVALID_SIDE");
    malformed = place;
    malformed.ibOrder.totalQuantity = std::numeric_limits<double>::quiet_NaN();
    assert(registry.Invoke(paper, malformed).reasonCode == "INVALID_QUANTITY");
    malformed = place;
    malformed.ibOrder.orderType = "MKT";
    assert(registry.Invoke(paper, malformed).reasonCode == "INVALID_LIMIT_PRICE");
    assert(placeCalls == 0);
    DecisionLeaseKey leaseKey;
    leaseKey.executionDomain = paper.executionContext.executionDomain;
    leaseKey.account = paper.executionContext.account;
    leaseKey.instrument = place.instrument;
    DecisionLeaseOwner leaseOwner;
    leaseOwner.agentId = paper.executionContext.agentId;
    leaseOwner.sessionId = paper.executionContext.sessionId;
    const DecisionLeaseResult lease = leases.Acquire(leaseKey, leaseOwner, std::chrono::seconds(5));
    assert(lease.status == DecisionLeaseStatus::Acquired);
    paper.executionContext.decisionLeaseFencingToken = lease.credential.fencingToken;
    paper.executionContext.decisionLeaseGeneration = lease.credential.generation;
    TradingToolCall preview = place;
    preview.name = "risk.preview_order";
    const TradingToolResult previewResult = registry.Invoke(paper, preview);
    assert(previewResult.status == TradingToolCallStatus::Ok);
    place.previewPermit = ExtractPreviewPermit(previewResult);
    TradingToolResult accepted = registry.Invoke(paper, place);
    assert(accepted.status == TradingToolCallStatus::Ok);
    assert(accepted.orderId == 701);
    assert(placeCalls == 1);
    assert(registry.Invoke(paper, place).status == TradingToolCallStatus::Duplicate);

    TradingToolCall spoofedCancel;
    spoofedCancel.name = "trade.cancel_order";
    spoofedCancel.orderId = 701;
    spoofedCancel.instrument = "EUR.USD";
    paper.executionContext.toolCallId = "cancel-spoof";
    assert(registry.Invoke(paper, spoofedCancel).reasonCode == "UNEXPECTED_TOOL_FIELD");
    assert(cancelCalls == 0);

    TradingToolCall cancel;
    cancel.name = "trade.cancel_order";
    cancel.orderId = 701;
    paper.executionContext.toolCallId = "cancel-owned-order";
    const TradingToolResult cancelResult = registry.Invoke(paper, cancel);
    assert(cancelResult.status == TradingToolCallStatus::Ok);
    assert(cancelCalls == 1);

    ExecutionEvent event;
    event.executionDomain = paper.executionContext.executionDomain;
    event.agentId = paper.executionContext.agentId;
    event.sessionId = paper.executionContext.sessionId;
    event.type = "order.status";
    event.venue = "IB";
    event.orderId = accepted.orderId;
    event.status = "Submitted";
    eventHub.Publish(event);
    TradingToolCall waitCall;
    waitCall.name = "events.wait";
    waitCall.waitTimeoutMs = 30001;
    assert(registry.Invoke(paper, waitCall).reasonCode == "INVALID_WAIT_TIMEOUT");
    waitCall.waitTimeoutMs = 0;
    TradingToolResult eventResult = registry.Invoke(paper, waitCall);
    assert(eventResult.status == TradingToolCallStatus::Ok);
    assert(eventResult.payloadJson.find("\"order_id\":701") != std::string::npos);

    const std::vector<TradingToolDescriptor> paperTools = registry.ListTools(paper);
    bool sawPlace = false;
    bool sawCancel = false;
    for (std::size_t i = 0; i < paperTools.size(); ++i)
    {
        if (paperTools[i].name == "trade.place_order") sawPlace = true;
        if (paperTools[i].name == "trade.cancel_order") sawCancel = true;
    }
    assert(sawPlace && sawCancel);
    assert(ToolTimeout(paperTools, "risk.preview_order") == 16000);
    assert(ToolTimeout(paperTools, "trade.place_order") == 16000);
    assert(ToolTimeout(paperTools, "trade.cancel_order") == 16000);

    TradingToolSession flattenCapable = paper;
    flattenCapable.capabilities.insert("trade.flatten");
    TradingToolCall flatten;
    flatten.name = "trade.flatten_position";
    flatten.instrument = "EUR.USD";
    assert(registry.Invoke(flattenCapable, flatten).reasonCode == "UNKNOWN_TOOL");
    const std::vector<TradingToolDescriptor> noHandlerTools =
        registry.ListTools(flattenCapable);
    for (std::size_t i = 0; i < noHandlerTools.size(); ++i)
        assert(noHandlerTools[i].name != "trade.flatten_position");

    int flattenCalls = 0;
    TradingToolTradeCallbacks tradeCallbacks;
    tradeCallbacks.flattenPosition =
        [&flattenCalls](const TradingToolSession& session,
                        const TradingToolCall& call) {
            ++flattenCalls;
            ExecutionCommandResult result;
            result.status = ExecutionCommandStatus::Accepted;
            result.commandId = session.executionContext.toolCallId;
            result.orderId = call.instrument == "EUR.USD" ? 702 : -1;
            return result;
        };
    TradingToolRegistry flattenRegistry(
        execution, readCallbacks, tradeCallbacks);
    assert(flattenRegistry.Invoke(flattenCapable, flatten).reasonCode ==
           "UNKNOWN_TOOL");
    for (const TradingToolDescriptor& tool :
         flattenRegistry.ListTools(flattenCapable))
    {
        assert(tool.name != "risk.preview_flatten");
        assert(tool.name != "trade.flatten_position");
    }

    readCallbacks.riskPreviewFlatten =
        [](const TradingToolSession&, const TradingToolCall&,
           std::string& payload, std::string&) {
            payload = "{\"approved\":true,\"preview_permit\":\"sha256:"
                "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\"}";
            return true;
        };
    TradingToolRegistry authoritativeFlattenRegistry(
        execution, readCallbacks, tradeCallbacks);
    TradingToolCall flattenPreview = flatten;
    flattenPreview.name = "risk.preview_flatten";
    const TradingToolResult flattenPreviewResult =
        authoritativeFlattenRegistry.Invoke(
            flattenCapable, flattenPreview);
    assert(flattenPreviewResult.status == TradingToolCallStatus::Ok);
    flatten.previewPermit = ExtractPreviewPermit(flattenPreviewResult);
    const TradingToolResult flattened =
        authoritativeFlattenRegistry.Invoke(flattenCapable, flatten);
    assert(flattened.status == TradingToolCallStatus::Ok);
    assert(flattened.orderId == 702);
    assert(flattenCalls == 1);
    assert(ToolTimeout(
        authoritativeFlattenRegistry.ListTools(flattenCapable),
        "risk.preview_flatten") == 16000);
    assert(ToolInputSchema(
        authoritativeFlattenRegistry.ListTools(flattenCapable),
        "risk.preview_flatten") ==
        "{\"type\":\"object\",\"required\":[\"instrument\"],"
        "\"properties\":{\"instrument\":{\"type\":\"string\"}},"
        "\"additionalProperties\":false}");
    assert(ToolTimeout(
        authoritativeFlattenRegistry.ListTools(flattenCapable),
        "trade.flatten_position") == 16000);

    TradingToolSession liveCapped = paper;
    liveCapped.environment = "LIVE_CAPPED";
    const std::vector<TradingToolDescriptor> liveTools = registry.ListTools(liveCapped);
    bool liveSawPlace = false;
    bool liveSawCancel = false;
    for (std::size_t i = 0; i < liveTools.size(); ++i)
    {
        if (liveTools[i].name == "trade.place_order") liveSawPlace = true;
        if (liveTools[i].name == "trade.cancel_order") liveSawCancel = true;
    }
    assert(liveSawPlace && liveSawCancel);

    TradingToolSession reduceOnly = paper;
    reduceOnly.environment = "LIVE_REDUCE_ONLY";
    const std::vector<TradingToolDescriptor> reduceTools = registry.ListTools(reduceOnly);
    for (std::size_t i = 0; i < reduceTools.size(); ++i)
        assert(reduceTools[i].name != "trade.place_order");
    assert(registry.Invoke(reduceOnly, place).reasonCode == "REDUCE_ONLY_PLACE_FORBIDDEN");

    std::remove(path.c_str());
}

void TestReadFailureReasonCodePropagation()
{
    const std::string path = TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));
    ExecutionCoordinator execution(journal, ExecutionCoordinatorCallbacks());

    TradingToolReadCallbacks codedFailure;
    codedFailure.marketGetQuote =
        [](const TradingToolSession&, const TradingToolCall&,
           std::string&, std::string& reason) {
            reason = "IB_PAPER_KILL_SWITCH_ENGAGED";
            return false;
        };
    TradingToolRegistry codedRegistry(execution, codedFailure);
    TradingToolCall quote;
    quote.name = "market.get_quote";
    quote.instrument = "EUR.USD";
    const TradingToolResult coded =
        codedRegistry.Invoke(MakeSession(false), quote);
    assert(coded.status == TradingToolCallStatus::Error);
    assert(coded.reasonCode == "IB_PAPER_KILL_SWITCH_ENGAGED");
    assert(coded.detail.empty());

    TradingToolReadCallbacks humanFailure;
    humanFailure.marketGetQuote =
        [](const TradingToolSession&, const TradingToolCall&,
           std::string&, std::string& reason) {
            reason = "quote source is warming up";
            return false;
        };
    TradingToolRegistry humanRegistry(execution, humanFailure);
    const TradingToolResult human =
        humanRegistry.Invoke(MakeSession(false), quote);
    assert(human.reasonCode == "READ_TOOL_FAILED");
    assert(human.detail == "quote source is warming up");

    TradingToolReadCallbacks throwingFailure;
    throwingFailure.marketGetQuote =
        [](const TradingToolSession&, const TradingToolCall&,
           std::string&, std::string&) -> bool {
            throw std::runtime_error("IB_PAPER_KILL_SWITCH_ENGAGED");
        };
    TradingToolRegistry throwingRegistry(execution, throwingFailure);
    const TradingToolResult throwing =
        throwingRegistry.Invoke(MakeSession(false), quote);
    assert(throwing.reasonCode == "READ_TOOL_FAILED");
    assert(throwing.detail == "IB_PAPER_KILL_SWITCH_ENGAGED");

    std::remove(path.c_str());
}

} // namespace

int main()
{
    TestCapabilityFilteredRegistryAndDirectTrade();
    TestReadFailureReasonCodePropagation();
    std::cout << "trading_tool_registry_tests: PASS" << std::endl;
    return 0;
}
