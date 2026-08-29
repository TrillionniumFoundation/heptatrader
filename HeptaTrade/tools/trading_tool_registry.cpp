#include "trading_tool_registry.h"
#include "trading_tool_wire_contract.h"

#include <cmath>
#include <cctype>
#include <exception>
#include <algorithm>
#include <chrono>
#include <iomanip>
#include <openssl/evp.h>
#include <sstream>

namespace {

const char* kReadResultSchema =
    "{\"type\":\"object\",\"additionalProperties\":true}";

const char* const kWatchSnapshotCapabilities[] = {
    "system.read",
    "market.read",
    "account.read",
    "portfolio.read",
    "orders.read",
    "risk.read"
};

const char* const kWatchSnapshotDescriptorTools[] = {
    "system.get_health",
    "account.get_summary",
    "portfolio.list_positions",
    "orders.list",
    "risk.get_limits",
    "market.get_quote"
};

const char* const kWatchSnapshotReadTools[] = {
    "account.get_summary",
    "portfolio.list_positions",
    "orders.list",
    "risk.get_limits",
    "market.get_quote",
    "system.get_health"
};

const char* kPlaceInputSchema =
    "{\"type\":\"object\",\"required\":[\"instrument\",\"side\",\"quantity\",\"order_type\",\"tif\",\"expires_at_ms\",\"preview_permit\"],"
    "\"properties\":{\"instrument\":{\"type\":\"string\"},\"side\":{\"enum\":[\"BUY\",\"SELL\"]},"
    "\"quantity\":{\"type\":\"number\",\"exclusiveMinimum\":0},\"order_type\":{\"enum\":[\"MKT\",\"LMT\"]},"
    "\"tif\":{\"enum\":[\"DAY\"]},\"limit_price\":{\"type\":\"number\",\"exclusiveMinimum\":0},"
    "\"reference_price\":{\"type\":\"number\",\"exclusiveMinimum\":0},\"expires_at_ms\":{\"type\":\"integer\"},"
    "\"symbol\":{\"type\":\"string\"},\"currency\":{\"type\":\"string\"},"
    "\"sec_type\":{\"type\":\"string\"},\"exchange\":{\"type\":\"string\"},"
    "\"preview_permit\":{\"type\":\"string\",\"minLength\":71,\"maxLength\":71}},\"additionalProperties\":false}";

const char* kPreviewInputSchema =
    "{\"type\":\"object\",\"required\":[\"instrument\",\"side\",\"quantity\",\"order_type\",\"tif\",\"expires_at_ms\"],"
    "\"properties\":{\"instrument\":{\"type\":\"string\"},\"side\":{\"enum\":[\"BUY\",\"SELL\"]},"
    "\"quantity\":{\"type\":\"number\",\"exclusiveMinimum\":0},\"order_type\":{\"enum\":[\"MKT\",\"LMT\"]},"
    "\"tif\":{\"enum\":[\"DAY\"]},\"limit_price\":{\"type\":\"number\",\"exclusiveMinimum\":0},"
    "\"reference_price\":{\"type\":\"number\",\"exclusiveMinimum\":0},\"expires_at_ms\":{\"type\":\"integer\"},"
    "\"symbol\":{\"type\":\"string\"},\"currency\":{\"type\":\"string\"},"
    "\"sec_type\":{\"type\":\"string\"},\"exchange\":{\"type\":\"string\"}},\"additionalProperties\":false}";

const char* kCancelInputSchema =
    "{\"type\":\"object\",\"required\":[\"order_id\"],\"additionalProperties\":false}";

const char* kExecutionResultSchema =
    "{\"type\":\"object\",\"required\":[\"status\",\"command_id\",\"order_id\"],\"additionalProperties\":false}";

const char* kCommandStatusInputSchema =
    "{\"type\":\"object\",\"required\":[\"command_id\"],"
    "\"properties\":{\"command_id\":{\"type\":\"string\",\"minLength\":8,\"maxLength\":128}},"
    "\"additionalProperties\":false}";

const char* kCommandStatusResultSchema =
    "{\"type\":\"object\",\"required\":[\"authoritative\",\"command_id\",\"command_status\","
    "\"order_id\",\"reason_code\",\"execution_service_epoch\","
    "\"execution_service_fencing_generation\"],\"properties\":{"
    "\"authoritative\":{\"const\":true},\"command_id\":{\"type\":\"string\"},"
    "\"command_status\":{\"enum\":[\"accepted\",\"rejected\",\"uncertain\"]},"
    "\"order_id\":{\"type\":\"integer\"},\"reason_code\":{\"type\":\"string\"},"
    "\"execution_service_epoch\":{\"type\":\"string\"},"
    "\"execution_service_fencing_generation\":{\"type\":\"integer\",\"minimum\":1}},"
    "\"additionalProperties\":false}";

std::string EscapeJson(const std::string& value)
{
    std::ostringstream out;
    for (std::string::const_iterator it = value.begin(); it != value.end(); ++it)
    {
        const unsigned char c = static_cast<unsigned char>(*it);
        if (c == '"') out << "\\\"";
        else if (c == '\\') out << "\\\\";
        else if (c == '\n') out << "\\n";
        else if (c == '\r') out << "\\r";
        else if (c == '\t') out << "\\t";
        else if (c < 0x20) out << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                               << static_cast<unsigned int>(c) << std::dec;
        else out << *it;
    }
    return out.str();
}

std::string DescriptorJson(const TradingToolDescriptor& descriptor)
{
    std::ostringstream out;
    out << "{\"name\":\"" << EscapeJson(descriptor.name)
        << "\",\"description\":\"" << EscapeJson(descriptor.description)
        << "\",\"required_capability\":\"" << EscapeJson(descriptor.requiredCapability)
        << "\",\"effect\":\"" << (descriptor.effect == TradingToolEffect::Trade ? "trade" : "read")
        << "\",\"timeout_ms\":" << descriptor.timeoutMs
        << ",\"schema_hash\":\"" << TradingToolRegistry::DescriptorSchemaHash(descriptor) << "\""
        << ",\"input_schema\":" << descriptor.inputSchema
        << ",\"result_schema\":" << descriptor.resultSchema << "}";
    return out.str();
}

std::string Sha256(const std::string& value)
{
    unsigned char digest[EVP_MAX_MD_SIZE];
    unsigned int length = 0;
    EVP_MD_CTX* context = EVP_MD_CTX_new();
    if (context == nullptr) return std::string();
    const bool ok = EVP_DigestInit_ex(context, EVP_sha256(), nullptr) == 1 &&
        EVP_DigestUpdate(context, value.data(), value.size()) == 1 &&
        EVP_DigestFinal_ex(context, digest, &length) == 1;
    EVP_MD_CTX_free(context);
    if (!ok) return std::string();
    std::ostringstream out;
    out << "sha256:" << std::hex << std::setfill('0');
    for (unsigned int i = 0; i < length; ++i)
        out << std::setw(2) << static_cast<unsigned int>(digest[i]);
    return out.str();
}

bool IsCanonicalReasonCode(const std::string& value)
{
    if (value.empty() || value.size() > 128) return false;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const char c = value[i];
        if ((c < 'A' || c > 'Z') && (c < '0' || c > '9') && c != '_')
            return false;
    }
    return value[0] >= 'A' && value[0] <= 'Z';
}

} // namespace

TradingToolRegistry::TradingToolRegistry(ExecutionAuthority& execution,
                                         const TradingToolReadCallbacks& readCallbacks,
                                         const TradingToolTradeCallbacks& tradeCallbacks)
    : m_execution(execution), m_readCallbacks(readCallbacks), m_tradeCallbacks(tradeCallbacks)
{
    RegisterDefaults();
}

const char* TradingToolRegistry::StatusName(TradingToolCallStatus status)
{
    return TradingToolWireContract::StatusName(status);
}

void TradingToolRegistry::RegisterReadTool(const std::string& name,
                                           const std::string& description,
                                           const std::string& capability,
                                           int timeoutMs,
                                           const std::string& inputSchema,
                                           const ReadHandler& handler)
{
    TradingToolDescriptor descriptor;
    descriptor.name = name;
    descriptor.description = description;
    descriptor.requiredCapability = capability;
    descriptor.effect = TradingToolEffect::Read;
    descriptor.timeoutMs = timeoutMs;
    descriptor.inputSchema = inputSchema;
    descriptor.resultSchema = kReadResultSchema;
    m_descriptors[name] = descriptor;
    m_readHandlers[name] = handler;
}

void TradingToolRegistry::RegisterDefaults()
{
    RegisterReadTool("system.tools.list", "List versioned tools visible to this session.",
                     "system.read", 1000, "{\"type\":\"object\",\"additionalProperties\":false}",
                     ReadHandler());
    RegisterReadTool("system.tools.describe", "Describe one versioned tool visible to this session.",
                     "system.read", 1000,
                     "{\"type\":\"object\",\"required\":[\"tool_name\"],\"properties\":{\"tool_name\":{\"type\":\"string\"}},\"additionalProperties\":false}",
                     ReadHandler());
    RegisterReadTool("system.cancel_request", "Cancel one pending request owned by this session.",
                     "system.read", 1000,
                     "{\"type\":\"object\",\"required\":[\"tool_call_id\"],\"properties\":{\"tool_call_id\":{\"type\":\"string\"}},\"additionalProperties\":false}",
                     ReadHandler());
    RegisterReadTool("market.get_quote", "Read the latest normalized quote for one instrument.",
                     "market.read", 8000,
                     "{\"type\":\"object\",\"required\":[\"instrument\"],"
                     "\"properties\":{\"instrument\":{\"type\":\"string\"}},"
                     "\"additionalProperties\":false}",
                     m_readCallbacks.marketGetQuote);
    RegisterReadTool("account.get_summary", "Read the bound account summary.",
                     "account.read", 8000, "{\"type\":\"object\",\"additionalProperties\":false}",
                     m_readCallbacks.accountGetSummary);
    RegisterReadTool("portfolio.list_positions", "Read authoritative positions visible to this session.",
                     "portfolio.read", 8000, "{\"type\":\"object\",\"additionalProperties\":false}",
                     m_readCallbacks.portfolioListPositions);
    RegisterReadTool("orders.list", "Read active and recent orders visible to this session.",
                     "orders.read", 8000, "{\"type\":\"object\",\"additionalProperties\":false}",
                     m_readCallbacks.ordersList);
    RegisterReadTool("execution.get_command_status",
                     "Read one execution command result owned by this Agent session.",
                     "orders.read", 8000, kCommandStatusInputSchema,
                     m_readCallbacks.executionGetCommandStatus);
    m_descriptors["execution.get_command_status"].resultSchema =
        kCommandStatusResultSchema;
    RegisterReadTool("risk.get_limits", "Read immutable limits bound to this Agent session.",
                     "risk.read", 8000, "{\"type\":\"object\",\"additionalProperties\":false}",
                     m_readCallbacks.riskGetLimits);
    RegisterReadTool("risk.preview_order", "Evaluate an order without broker side effects.",
                     "risk.preview", 16000, kPreviewInputSchema, m_readCallbacks.riskPreviewOrder);
    RegisterReadTool("events.wait", "Wait for the next bounded order, fill, reject or market event.",
                     "events.read", 36000,
                     "{\"type\":\"object\",\"properties\":{\"after_sequence\":{\"type\":\"integer\"},\"timeout_ms\":{\"type\":\"integer\"}},\"additionalProperties\":false}",
                     m_readCallbacks.eventsWait);
    RegisterReadTool("system.get_health", "Read authoritative recovery and contract subscription health.",
                     "system.read", 6000, "{\"type\":\"object\",\"additionalProperties\":false}",
                     m_readCallbacks.systemGetHealth);
    RegisterReadTool(
        "watch.get_snapshot",
        "Read one fixed WATCH catalog, descriptor and authoritative state set.",
        "system.read", 8000,
        "{\"type\":\"object\",\"required\":[\"instrument\"],"
        "\"properties\":{\"instrument\":{\"type\":\"string\"}},"
        "\"additionalProperties\":false}",
        ReadHandler());

    TradingToolDescriptor place;
    place.name = "trade.place_order";
    place.description = "Submit a real order through the Hepta C++ execution authority.";
    place.requiredCapability = "trade.place";
    place.effect = TradingToolEffect::Trade;
    // Trade dispatch first performs the Gateway's explicit liveness/readiness
    // probe, then re-resolves the mutation/event identity pair at the
    // ExecutionAuthority boundary before the actual command RPC.
    place.timeoutMs = 16000;
    place.inputSchema = kPlaceInputSchema;
    place.resultSchema = kExecutionResultSchema;
    m_descriptors[place.name] = place;

    TradingToolDescriptor cancel;
    cancel.name = "trade.cancel_order";
    cancel.description = "Cancel an order owned by this Agent session.";
    cancel.requiredCapability = "trade.cancel";
    cancel.effect = TradingToolEffect::Trade;
    cancel.timeoutMs = 16000;
    cancel.inputSchema = kCancelInputSchema;
    cancel.resultSchema = kExecutionResultSchema;
    m_descriptors[cancel.name] = cancel;

    // Flatten is not equivalent to a client-side position read followed by a
    // place call: that construction has a state-of-check/state-of-use race and
    // can increase exposure.  Publish the descriptor only when the concrete
    // Execution composition installs an authoritative reduce-only handler.
    if (m_tradeCallbacks.flattenPosition &&
        m_readCallbacks.riskPreviewFlatten)
    {
        RegisterReadTool(
            "risk.preview_flatten",
            "Preview an authoritative reduce-only close and issue one permit.",
            "trade.flatten", 16000,
            "{\"type\":\"object\",\"required\":[\"instrument\"],"
            "\"properties\":{\"instrument\":{\"type\":\"string\"}},"
            "\"additionalProperties\":false}",
            m_readCallbacks.riskPreviewFlatten);
        TradingToolDescriptor flatten;
        flatten.name = "trade.flatten_position";
        flatten.description =
            "Close the Agent-visible position without increasing absolute exposure.";
        flatten.requiredCapability = "trade.flatten";
        flatten.effect = TradingToolEffect::Trade;
        flatten.timeoutMs = 16000;
        flatten.inputSchema =
            "{\"type\":\"object\",\"required\":[\"instrument\","
            "\"preview_permit\"],\"properties\":{"
            "\"instrument\":{\"type\":\"string\"},"
            "\"preview_permit\":{\"type\":\"string\","
            "\"minLength\":71,\"maxLength\":71}},"
            "\"additionalProperties\":false}";
        flatten.resultSchema = kExecutionResultSchema;
        m_descriptors[flatten.name] = flatten;
    }
}

bool TradingToolRegistry::ValidateCallSemantics(const TradingToolCall& call,
                                                std::string& reasonCode,
                                                std::string& detail)
{
    return TradingToolWireContract::ValidateCallSemantics(
        call, reasonCode, detail);
}

bool TradingToolRegistry::EnvironmentAllows(const TradingToolSession& session,
                                            const TradingToolDescriptor& descriptor,
                                            std::string& reasonCode)
{
    if (session.environment != "WATCH" && session.environment != "PAPER" &&
        session.environment != "LIVE_REDUCE_ONLY" && session.environment != "LIVE_CAPPED")
    {
        reasonCode = "INVALID_SESSION_ENVIRONMENT";
        return false;
    }
    if (descriptor.name == "watch.get_snapshot" &&
        session.environment != "WATCH")
    {
        reasonCode = "WATCH_SNAPSHOT_ENVIRONMENT_REQUIRED";
        return false;
    }
    if (descriptor.name == "execution.get_command_status" &&
        session.environment == "WATCH")
    {
        reasonCode = "WATCH_COMMAND_STATUS_UNAVAILABLE";
        return false;
    }
    if (descriptor.effect == TradingToolEffect::Trade && session.environment == "WATCH")
    {
        reasonCode = "WATCH_SESSION_CANNOT_TRADE";
        return false;
    }
    if (descriptor.name == "trade.place_order" && session.environment == "LIVE_REDUCE_ONLY")
    {
        reasonCode = "REDUCE_ONLY_PLACE_FORBIDDEN";
        return false;
    }
    reasonCode.clear();
    return true;
}

bool TradingToolRegistry::HasCapability(const TradingToolSession& session,
                                        const std::string& capability) const
{
    return session.capabilities.find(capability) != session.capabilities.end();
}

bool TradingToolRegistry::HasRequiredCapabilities(
    const TradingToolSession& session,
    const TradingToolDescriptor& descriptor,
    std::string& missingCapability) const
{
    if (descriptor.name != "watch.get_snapshot")
    {
        if (HasCapability(session, descriptor.requiredCapability))
        {
            missingCapability.clear();
            return true;
        }
        missingCapability = descriptor.requiredCapability;
        return false;
    }
    for (std::size_t i = 0;
         i < sizeof(kWatchSnapshotCapabilities) /
             sizeof(kWatchSnapshotCapabilities[0]); ++i)
    {
        if (HasCapability(session, kWatchSnapshotCapabilities[i])) continue;
        missingCapability = kWatchSnapshotCapabilities[i];
        return false;
    }
    missingCapability.clear();
    return true;
}

std::vector<TradingToolDescriptor> TradingToolRegistry::ListTools(const TradingToolSession& session) const
{
    std::vector<TradingToolDescriptor> result;
    for (std::unordered_map<std::string, TradingToolDescriptor>::const_iterator it = m_descriptors.begin();
         it != m_descriptors.end(); ++it)
    {
        std::string environmentReason;
        std::string missingCapability;
        if (HasRequiredCapabilities(session, it->second, missingCapability) &&
            EnvironmentAllows(session, it->second, environmentReason)) result.push_back(it->second);
    }
    return result;
}

bool TradingToolRegistry::GetDescriptor(const std::string& name, TradingToolDescriptor& out) const
{
    const std::unordered_map<std::string, TradingToolDescriptor>::const_iterator it = m_descriptors.find(name);
    if (it == m_descriptors.end()) return false;
    out = it->second;
    return true;
}

std::string TradingToolRegistry::DescriptorSchemaHash(const TradingToolDescriptor& descriptor)
{
    std::ostringstream canonical;
    canonical << descriptor.name << '\0' << descriptor.description << '\0'
              << descriptor.requiredCapability << '\0'
              << (descriptor.effect == TradingToolEffect::Trade ? "trade" : "read") << '\0'
              << descriptor.timeoutMs << '\0' << descriptor.inputSchema << '\0'
              << descriptor.resultSchema;
    return Sha256(canonical.str());
}

unsigned int TradingToolRegistry::DiscoverySchemaVersion()
{
    return 2;
}

std::string TradingToolRegistry::CatalogSchemaHash(const TradingToolSession& session) const
{
    std::vector<TradingToolDescriptor> tools = ListTools(session);
    std::sort(tools.begin(), tools.end(), [](const TradingToolDescriptor& left,
                                             const TradingToolDescriptor& right) {
        return left.name < right.name;
    });
    std::ostringstream canonical;
    for (std::size_t i = 0; i < tools.size(); ++i)
        canonical << tools[i].name << '=' << DescriptorSchemaHash(tools[i]) << '\n';
    return Sha256(canonical.str());
}

TradingToolResult TradingToolRegistry::InvokeRead(const TradingToolSession& session,
                                                  const TradingToolDescriptor& descriptor,
                                                  const TradingToolCall& call) const
{
    if (call.name == "watch.get_snapshot")
        return InvokeWatchSnapshot(session, call);
    TradingToolResult result;
    result.toolName = call.name;
    const std::unordered_map<std::string, ReadHandler>::const_iterator handlerIt = m_readHandlers.find(call.name);
    if (handlerIt == m_readHandlers.end() || !handlerIt->second)
    {
        result.status = TradingToolCallStatus::Error;
        result.reasonCode = "TOOL_HANDLER_UNAVAILABLE";
        result.detail = descriptor.name + " is registered but not wired to live C++ state";
        return result;
    }

    std::string payload;
    std::string reason;
    bool ok = false;
    bool handlerThrew = false;
    try
    {
        ok = handlerIt->second(session, call, payload, reason);
    }
    catch (const std::exception& ex)
    {
        handlerThrew = true;
        reason = ex.what();
    }
    catch (...)
    {
        handlerThrew = true;
        reason = "unknown read tool exception";
    }

    result.status = ok ? TradingToolCallStatus::Ok : TradingToolCallStatus::Error;
    const bool canonicalFailure = !ok && !handlerThrew &&
        IsCanonicalReasonCode(reason);
    result.reasonCode = ok ? "" :
        (canonicalFailure ? reason : "READ_TOOL_FAILED");
    result.detail = canonicalFailure ? std::string() : reason;
    result.payloadJson = payload;
    return result;
}

TradingToolResult TradingToolRegistry::InvokeWatchSnapshot(
    const TradingToolSession& session,
    const TradingToolCall& call) const
{
    TradingToolResult result;
    result.toolName = call.name;
    if (session.environment != "WATCH")
    {
        result.status = TradingToolCallStatus::PermissionDenied;
        result.reasonCode = "WATCH_SNAPSHOT_ENVIRONMENT_REQUIRED";
        return result;
    }
    if (session.visibleInstruments.find(call.instrument) ==
        session.visibleInstruments.end())
    {
        result.status = TradingToolCallStatus::PermissionDenied;
        result.reasonCode = "INSTRUMENT_NOT_ALLOWED";
        return result;
    }

    TradingToolCall catalogCall;
    catalogCall.name = "system.tools.list";
    const TradingToolResult catalog = InvokeDiscovery(session, catalogCall);
    if (catalog.status != TradingToolCallStatus::Ok)
    {
        result.status = catalog.status;
        result.reasonCode = catalog.reasonCode.empty() ?
            "WATCH_SNAPSHOT_DISCOVERY_FAILED" : catalog.reasonCode;
        return result;
    }

    std::vector<TradingToolResult> descriptors;
    descriptors.reserve(sizeof(kWatchSnapshotDescriptorTools) /
                        sizeof(kWatchSnapshotDescriptorTools[0]));
    for (std::size_t i = 0;
         i < sizeof(kWatchSnapshotDescriptorTools) /
             sizeof(kWatchSnapshotDescriptorTools[0]); ++i)
    {
        TradingToolCall describeCall;
        describeCall.name = "system.tools.describe";
        describeCall.targetToolName = kWatchSnapshotDescriptorTools[i];
        const TradingToolResult described =
            InvokeDiscovery(session, describeCall);
        if (described.status != TradingToolCallStatus::Ok)
        {
            result.status = described.status;
            result.reasonCode = described.reasonCode.empty() ?
                "WATCH_SNAPSHOT_DISCOVERY_FAILED" : described.reasonCode;
            return result;
        }
        descriptors.push_back(described);
    }

    std::vector<TradingToolResult> reads;
    std::vector<long long> readFinishedAtMs;
    reads.reserve(sizeof(kWatchSnapshotReadTools) /
                  sizeof(kWatchSnapshotReadTools[0]));
    readFinishedAtMs.reserve(sizeof(kWatchSnapshotReadTools) /
                            sizeof(kWatchSnapshotReadTools[0]));
    for (std::size_t i = 0;
         i < sizeof(kWatchSnapshotReadTools) /
             sizeof(kWatchSnapshotReadTools[0]); ++i)
    {
        TradingToolCall readCall;
        readCall.name = kWatchSnapshotReadTools[i];
        if (readCall.name == "market.get_quote")
            readCall.instrument = call.instrument;
        TradingToolDescriptor readDescriptor;
        if (!GetDescriptor(readCall.name, readDescriptor))
        {
            result.status = TradingToolCallStatus::Error;
            result.reasonCode = "WATCH_SNAPSHOT_TOOL_UNAVAILABLE";
            return result;
        }
        const TradingToolResult read =
            InvokeRead(session, readDescriptor, readCall);
        if (read.status != TradingToolCallStatus::Ok)
        {
            result.status = read.status;
            result.reasonCode = read.reasonCode.empty() ?
                "WATCH_SNAPSHOT_SUBREAD_FAILED" : read.reasonCode;
            result.payloadJson.clear();
            return result;
        }
        reads.push_back(read);
        readFinishedAtMs.push_back(
            std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::system_clock::now().time_since_epoch()).count());
    }

    std::ostringstream output;
    output << "{\"schema\":\"hepta.watch-read-set.v1\",\"catalog\":"
           << catalog.payloadJson << ",\"descriptors\":{";
    for (std::size_t i = 0; i < descriptors.size(); ++i)
    {
        if (i != 0) output << ',';
        output << '\"' << kWatchSnapshotDescriptorTools[i] << "\":"
               << descriptors[i].payloadJson;
    }
    output << "},\"reads\":{";
    for (std::size_t i = 0; i < reads.size(); ++i)
    {
        if (i != 0) output << ',';
        output << '\"' << kWatchSnapshotReadTools[i] << "\":"
               << reads[i].payloadJson;
    }
    output << "},\"read_finished_at_ms\":{";
    for (std::size_t i = 0; i < readFinishedAtMs.size(); ++i)
    {
        if (i != 0) output << ',';
        output << '\"' << kWatchSnapshotReadTools[i] << "\":"
               << readFinishedAtMs[i];
    }
    output << "}}";
    result.status = TradingToolCallStatus::Ok;
    result.payloadJson = output.str();
    if (TradingToolWireContract::EncodeResultEnvelope(result).size() >
        TradingToolWireLimits::MaximumResultEnvelopeBytes())
    {
        result.status = TradingToolCallStatus::Error;
        result.reasonCode = "WATCH_SNAPSHOT_RESPONSE_TOO_LARGE";
        result.payloadJson.clear();
    }
    return result;
}

TradingToolResult TradingToolRegistry::InvokeDiscovery(const TradingToolSession& session,
                                                       const TradingToolCall& call) const
{
    TradingToolResult result;
    result.status = TradingToolCallStatus::Ok;
    result.toolName = call.name;
    std::vector<TradingToolDescriptor> tools = ListTools(session);
    std::sort(tools.begin(), tools.end(), [](const TradingToolDescriptor& left,
                                             const TradingToolDescriptor& right) {
        return left.name < right.name;
    });

    std::ostringstream payload;
    payload << "{\"protocol\":\"hepta.agent-tools\",\"protocol_version\":1,"
            << "\"protocol_min_version\":1,\"protocol_max_version\":1,"
            << "\"schema_version\":" << DiscoverySchemaVersion()
            << ",\"catalog_schema_hash\":\""
            << CatalogSchemaHash(session) << "\"";
    if (call.name == "system.tools.list")
    {
        payload << ",\"tools\":[";
        for (std::size_t i = 0; i < tools.size(); ++i)
        {
            if (i != 0) payload << ',';
            payload << DescriptorJson(tools[i]);
        }
        payload << "]}";
        result.payloadJson = payload.str();
        return result;
    }

    for (std::size_t i = 0; i < tools.size(); ++i)
    {
        if (tools[i].name == call.targetToolName)
        {
            payload << ",\"tool\":" << DescriptorJson(tools[i]) << "}";
            result.payloadJson = payload.str();
            return result;
        }
    }
    result.status = TradingToolCallStatus::InvalidTool;
    result.reasonCode = "TOOL_NOT_VISIBLE";
    result.detail = call.targetToolName;
    return result;
}

TradingToolResult TradingToolRegistry::FromExecution(const std::string& toolName,
                                                     const ExecutionCommandResult& execution)
{
    TradingToolResult result;
    result.toolName = toolName;
    result.orderId = execution.orderId;
    result.reasonCode = execution.reasonCode;
    result.detail = execution.detail;
    switch (execution.status)
    {
    case ExecutionCommandStatus::Accepted: result.status = TradingToolCallStatus::Ok; break;
    case ExecutionCommandStatus::Rejected: result.status = TradingToolCallStatus::Rejected; break;
    case ExecutionCommandStatus::Duplicate: result.status = TradingToolCallStatus::Duplicate; break;
    case ExecutionCommandStatus::Uncertain: result.status = TradingToolCallStatus::Uncertain; break;
    }
    return result;
}

TradingToolResult TradingToolRegistry::Invoke(const TradingToolSession& session,
                                              const TradingToolCall& call)
{
    TradingToolDescriptor descriptor;
    if (!GetDescriptor(call.name, descriptor))
    {
        TradingToolResult result;
        result.status = TradingToolCallStatus::InvalidTool;
        result.toolName = call.name;
        result.reasonCode = "UNKNOWN_TOOL";
        return result;
    }
    std::string missingCapability;
    if (!HasRequiredCapabilities(session, descriptor, missingCapability))
    {
        TradingToolResult result;
        result.status = TradingToolCallStatus::PermissionDenied;
        result.toolName = call.name;
        result.reasonCode = "CAPABILITY_REQUIRED";
        result.detail = missingCapability;
        return result;
    }
    std::string environmentReason;
    if (!EnvironmentAllows(session, descriptor, environmentReason))
    {
        TradingToolResult result;
        result.status = TradingToolCallStatus::PermissionDenied;
        result.toolName = call.name;
        result.reasonCode = environmentReason;
        return result;
    }
    std::string semanticReason;
    std::string semanticDetail;
    if (!ValidateCallSemantics(call, semanticReason, semanticDetail))
    {
        TradingToolResult result;
        result.status = TradingToolCallStatus::Rejected;
        result.toolName = call.name;
        result.reasonCode = semanticReason;
        result.detail = semanticDetail;
        return result;
    }
    if (call.name == "system.tools.list" || call.name == "system.tools.describe")
        return InvokeDiscovery(session, call);
    if (descriptor.effect == TradingToolEffect::Read) return InvokeRead(session, descriptor, call);

    if (call.name == "trade.place_order")
    {
        IbPlaceOrderCommand command;
        command.context = session.executionContext;
        command.contract = call.ibContract;
        command.order = call.ibOrder;
        command.instrument = call.instrument;
        command.timeInForce = call.timeInForce;
        command.referencePrice = call.referencePrice;
        command.expiresAtMs = call.expiresAtMs;
        command.previewPermit = call.previewPermit;
        return FromExecution(call.name, m_execution.PlaceOrder(command));
    }
    if (call.name == "trade.cancel_order")
    {
        IbCancelOrderCommand command;
        command.context = session.executionContext;
        command.orderId = call.orderId;
        // Both values are resolved from the server-side ownership projection.
        // Agent input for either field would be spoofable and is rejected by
        // ValidateCallSemantics().
        return FromExecution(call.name, m_execution.CancelOrder(command));
    }
    if (call.name == "trade.flatten_position")
    {
        if (!m_tradeCallbacks.flattenPosition)
        {
            TradingToolResult result;
            result.status = TradingToolCallStatus::Error;
            result.toolName = call.name;
            result.reasonCode = "TOOL_HANDLER_UNAVAILABLE";
            return result;
        }
        return FromExecution(call.name, m_tradeCallbacks.flattenPosition(session, call));
    }

    TradingToolResult result;
    result.status = TradingToolCallStatus::InvalidTool;
    result.toolName = call.name;
    result.reasonCode = "TOOL_NOT_IMPLEMENTED";
    return result;
}
