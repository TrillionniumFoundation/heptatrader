#include "typed_tool_protocol.h"
#include "../tools/trading_tool_wire_contract.h"

#include <cerrno>
#include <cctype>
#include <cmath>
#include <cstdlib>
#include <iomanip>
#include <map>
#include <set>
#include <sstream>

namespace {

enum FieldId
{
    SessionToken = 1, ToolCallId = 2, ToolName = 3, Instrument = 4, OrderId = 5,
    Symbol = 6, Currency = 7, SecType = 8, Exchange = 9, Side = 10,
    OrderType = 11, Quantity = 12, LimitPrice = 13, ReferencePrice = 14,
    ExpiresAtMs = 15, WaitTimeoutMs = 16, AfterEventSequence = 17, TimeInForce = 18,
    QueueDeadlineAtMs = 19, CancelToolCallId = 20, TargetToolName = 21,
    ProtocolMinVersion = 22, ProtocolMaxVersion = 23, ExpectedSchemaHash = 24,
    PreviewPermit = 25, TargetCommandId = 26
};

const char* FieldName(unsigned int id)
{
    switch (id)
    {
    case SessionToken: return "session_token";
    case ToolCallId: return "tool_call_id";
    case ToolName: return "tool_name";
    case Instrument: return "instrument";
    case OrderId: return "order_id";
    case Symbol: return "symbol";
    case Currency: return "currency";
    case SecType: return "sec_type";
    case Exchange: return "exchange";
    case Side: return "side";
    case OrderType: return "order_type";
    case Quantity: return "quantity";
    case LimitPrice: return "limit_price";
    case ReferencePrice: return "reference_price";
    case ExpiresAtMs: return "expires_at_ms";
    case WaitTimeoutMs: return "timeout_ms";
    case AfterEventSequence: return "after_sequence";
    case TimeInForce: return "tif";
    case QueueDeadlineAtMs: return "queue_deadline_at_ms";
    case CancelToolCallId: return "cancel_tool_call_id";
    case TargetToolName: return "target_tool_name";
    case ProtocolMinVersion: return "protocol_min_version";
    case ProtocolMaxVersion: return "protocol_max_version";
    case ExpectedSchemaHash: return "expected_schema_hash";
    case PreviewPermit: return "preview_permit";
    case TargetCommandId: return "command_id";
    }
    return "unknown";
}
bool IsEnvelopeField(unsigned int id)
{
    return id == SessionToken || id == ToolCallId || id == ToolName ||
           id == QueueDeadlineAtMs || id == ProtocolMinVersion ||
           id == ProtocolMaxVersion || id == ExpectedSchemaHash;
}

bool IsToolFieldAllowed(const std::string& tool, unsigned int id)
{
    if (IsEnvelopeField(id)) return true;
    if (tool == "market.get_quote" || tool == "watch.get_snapshot" || tool == "risk.preview_flatten")
        return id == Instrument;
    if (tool == "trade.flatten_position")
        return id == Instrument || id == PreviewPermit;
    if (tool == "system.cancel_request") return id == CancelToolCallId;
    if (tool == "system.tools.describe") return id == TargetToolName;
    if (tool == "execution.get_command_status") return id == TargetCommandId;
    if (tool == "account.get_summary" || tool == "portfolio.list_positions" ||
        tool == "orders.list" || tool == "risk.get_limits" || tool == "system.get_health" ||
        tool == "system.tools.list") return false;
    if (tool == "events.wait") return id == WaitTimeoutMs || id == AfterEventSequence;
    if (tool == "trade.cancel_order") return id == OrderId;
    if (tool == "trade.place_order" || tool == "risk.preview_order")
    {
        return id == Instrument || id == Symbol || id == Currency || id == SecType ||
               id == Exchange || id == Side || id == OrderType || id == Quantity ||
               id == LimitPrice || id == ReferencePrice || id == ExpiresAtMs ||
               id == TimeInForce || (tool == "trade.place_order" && id == PreviewPermit);
    }
    return false;
}

bool IsRequiredToolField(const std::string& tool, unsigned int id)
{
    if (tool == "market.get_quote" || tool == "watch.get_snapshot" || tool == "risk.preview_flatten")
        return id == Instrument;
    if (tool == "trade.flatten_position")
        return id == Instrument || id == PreviewPermit;
    if (tool == "system.cancel_request") return id == CancelToolCallId;
    if (tool == "system.tools.describe") return id == TargetToolName;
    if (tool == "execution.get_command_status") return id == TargetCommandId;
    if (tool == "trade.cancel_order") return id == OrderId;
    if (tool == "trade.place_order" || tool == "risk.preview_order")
    {
        return id == Instrument || id == Side || id == OrderType || id == Quantity ||
               id == ExpiresAtMs || id == TimeInForce;
    }
    return false;
}

std::size_t MaxFieldLength(unsigned int id)
{
    if (id == SessionToken) return 512;
    if (id == CancelToolCallId) return 256;
    if (id == ExpectedSchemaHash || id == PreviewPermit) return 80;
    if (id == ToolCallId || id == TargetCommandId || id == Instrument ||
        (id >= Symbol && id <= OrderType) || id == TimeInForce) return 128;
    return 64;
}

bool IsSchemaHash(const std::string& value)
{
    if (value.size() != 71 || value.compare(0, 7, "sha256:") != 0) return false;
    for (std::size_t i = 7; i < value.size(); ++i)
    {
        const unsigned char c = static_cast<unsigned char>(value[i]);
        if (!std::isxdigit(c) || std::isupper(c)) return false;
    }
    return true;
}

bool ValidateFieldSet(const std::map<unsigned int, std::string>& fields, std::string& reason)
{
    const std::map<unsigned int, std::string>::const_iterator toolIt = fields.find(ToolName);
    if (toolIt == fields.end() || toolIt->second.empty())
    {
        reason = "SCHEMA_MISSING_REQUIRED_FIELD:tool_name";
        return false;
    }
    const std::string& tool = toolIt->second;
    if (!TradingToolWireContract::IsCanonicalToolName(tool))
    {
        reason = "INVALID_TOOL_NAME";
        return false;
    }
    for (std::map<unsigned int, std::string>::const_iterator it = fields.begin(); it != fields.end(); ++it)
    {
        if (it->second.empty())
        {
            reason = std::string("SCHEMA_EMPTY_FIELD:") + FieldName(it->first);
            return false;
        }
        if (it->second.size() > MaxFieldLength(it->first))
        {
            reason = std::string("SCHEMA_FIELD_TOO_LONG:") + FieldName(it->first);
            return false;
        }
        if (!IsToolFieldAllowed(tool, it->first))
        {
            reason = std::string("SCHEMA_UNEXPECTED_FIELD:") + FieldName(it->first);
            return false;
        }
    }
    const std::map<unsigned int, std::string>::const_iterator callId =
        fields.find(ToolCallId);
    if (callId == fields.end() ||
        !TradingToolWireContract::IsCanonicalCommandId(callId->second))
    {
        reason = "INVALID_TOOL_CALL_ID";
        return false;
    }
    for (unsigned int id = Instrument; id <= TargetCommandId; ++id)
    {
        if (IsRequiredToolField(tool, id) && fields.find(id) == fields.end())
        {
            reason = std::string("SCHEMA_MISSING_REQUIRED_FIELD:") + FieldName(id);
            return false;
        }
    }
    const std::map<unsigned int, std::string>::const_iterator targetTool =
        fields.find(TargetToolName);
    if (targetTool != fields.end() &&
        !TradingToolWireContract::IsCanonicalToolName(targetTool->second))
    {
        reason = "INVALID_TARGET_TOOL_NAME";
        return false;
    }
    reason.clear();
    return true;
}

void AppendU16(std::string& out, unsigned int value)
{
    out.push_back(static_cast<char>((value >> 8) & 0xff));
    out.push_back(static_cast<char>(value & 0xff));
}

void AppendU32(std::string& out, std::uint32_t value)
{
    out.push_back(static_cast<char>((value >> 24) & 0xff));
    out.push_back(static_cast<char>((value >> 16) & 0xff));
    out.push_back(static_cast<char>((value >> 8) & 0xff));
    out.push_back(static_cast<char>(value & 0xff));
}

bool ReadU16(const std::string& in, std::size_t& offset, unsigned int& value)
{
    if (offset + 2 > in.size()) return false;
    value = (static_cast<unsigned char>(in[offset]) << 8) |
            static_cast<unsigned char>(in[offset + 1]);
    offset += 2;
    return true;
}

bool ReadU32(const std::string& in, std::size_t& offset, std::uint32_t& value)
{
    if (offset + 4 > in.size()) return false;
    value = (static_cast<std::uint32_t>(static_cast<unsigned char>(in[offset])) << 24) |
            (static_cast<std::uint32_t>(static_cast<unsigned char>(in[offset + 1])) << 16) |
            (static_cast<std::uint32_t>(static_cast<unsigned char>(in[offset + 2])) << 8) |
            static_cast<std::uint32_t>(static_cast<unsigned char>(in[offset + 3]));
    offset += 4;
    return true;
}

void AddField(std::string& out, unsigned int id, const std::string& value)
{
    AppendU16(out, id);
    AppendU32(out, static_cast<std::uint32_t>(value.size()));
    out.append(value);
}

template <typename T>
std::string Number(T value)
{
    std::ostringstream out;
    out << std::setprecision(17) << value;
    return out.str();
}

void AddTargetFields(std::map<unsigned int, std::string>& fields,
                     const TradingToolCall& call)
{
    if (!call.targetToolName.empty()) fields[TargetToolName] = call.targetToolName;
    if (!call.targetCommandId.empty()) fields[TargetCommandId] = call.targetCommandId;
}

bool ParseLong(const std::string& value, long& out)
{
    if (value.empty()) return false;
    char* end = nullptr;
    errno = 0;
    const long parsed = std::strtol(value.c_str(), &end, 10);
    if (errno != 0 || end == value.c_str() || *end != '\0') return false;
    out = parsed;
    return true;
}

bool ParseLongLong(const std::string& value, long long& out)
{
    if (value.empty()) return false;
    char* end = nullptr;
    errno = 0;
    const long long parsed = std::strtoll(value.c_str(), &end, 10);
    if (errno != 0 || end == value.c_str() || *end != '\0') return false;
    out = parsed;
    return true;
}

bool ParseDouble(const std::string& value, double& out)
{
    if (value.empty()) return false;
    char* end = nullptr;
    errno = 0;
    const double parsed = std::strtod(value.c_str(), &end);
    if (errno != 0 || end == value.c_str() || *end != '\0' || !std::isfinite(parsed)) return false;
    out = parsed;
    return true;
}

typedef std::map<unsigned int, std::string> DecodedFields;

bool DecodeFields(const std::string& body, DecodedFields& fields,
                  std::string& reason)
{
    if (body.size() < 4 || body.compare(0, 4, "HTT1") != 0)
    { reason = "INVALID_PROTOCOL_MAGIC"; return false; }
    std::size_t offset = 4;
    while (offset < body.size())
    {
        if (fields.size() >= 32)
        { reason = "SCHEMA_TOO_MANY_FIELDS"; return false; }
        unsigned int id = 0;
        std::uint32_t length = 0;
        if (!ReadU16(body, offset, id) || !ReadU32(body, offset, length) ||
            length > 32768 || offset + length > body.size())
        { reason = "SCHEMA_MALFORMED_FIELD"; return false; }
        if (id < SessionToken || id > TargetCommandId || fields.count(id) != 0)
        { reason = "SCHEMA_UNKNOWN_OR_DUPLICATE_FIELD"; return false; }
        fields[id] = body.substr(offset, length);
        offset += length;
    }
    if (fields.count(SessionToken) == 0 || fields.count(ToolCallId) == 0 ||
        fields.count(ToolName) == 0)
    { reason = "SCHEMA_MISSING_PROTOCOL_ENVELOPE"; return false; }
    if (!ValidateFieldSet(fields, reason)) return false;
    for (unsigned int id = SessionToken; id <= TargetCommandId; ++id)
        fields[id];
    return true;
}

bool DecodeEnvelope(const DecodedFields& fields, unsigned int currentVersion,
                    TradingToolHostRequest& request, std::string& reason)
{
    request.sessionToken = fields.at(SessionToken);
    request.toolCallId = fields.at(ToolCallId);
    request.call.name = fields.at(ToolName);
    long minimum = 1;
    long maximum = 1;
    const std::string& minField = fields.at(ProtocolMinVersion);
    const std::string& maxField = fields.at(ProtocolMaxVersion);
    if (!minField.empty() && !ParseLong(minField, minimum))
    { reason = "INVALID_PROTOCOL_MIN_VERSION"; return false; }
    if (!maxField.empty() && !ParseLong(maxField, maximum))
    { reason = "INVALID_PROTOCOL_MAX_VERSION"; return false; }
    if (minimum < 1 || maximum < minimum ||
        minimum > static_cast<long>(currentVersion) ||
        maximum < static_cast<long>(currentVersion))
    { reason = "UNSUPPORTED_PROTOCOL_VERSION"; return false; }
    request.protocolMinVersion = static_cast<unsigned int>(minimum);
    request.protocolMaxVersion = static_cast<unsigned int>(maximum);
    request.expectedSchemaHash = fields.at(ExpectedSchemaHash);
    if (!request.expectedSchemaHash.empty() &&
        !IsSchemaHash(request.expectedSchemaHash))
    { reason = "INVALID_SCHEMA_HASH"; return false; }
    return true;
}

void DecodeTextFields(const DecodedFields& fields,
                      TradingToolHostRequest& request)
{
    request.call.targetToolName = fields.at(TargetToolName);
    request.call.targetCommandId = fields.at(TargetCommandId);
    request.call.instrument = fields.at(Instrument);
    request.call.ibContract.symbol = fields.at(Symbol);
    request.call.ibContract.currency = fields.at(Currency);
    request.call.ibContract.secType = fields.at(SecType);
    request.call.ibContract.exchange = fields.at(Exchange);
    request.call.ibOrder.action = fields.at(Side);
    request.call.ibOrder.orderType = fields.at(OrderType);
    request.call.timeInForce = fields.at(TimeInForce);
    request.call.previewPermit = fields.at(PreviewPermit);
    request.cancelToolCallId = fields.at(CancelToolCallId);
}

bool DecodeNumericFields(const DecodedFields& fields,
                         TradingToolHostRequest& request,
                         std::string& reason)
{
    long long queueDeadlineAtMs = 0;
    if (!fields.at(QueueDeadlineAtMs).empty() &&
        (!ParseLongLong(fields.at(QueueDeadlineAtMs), queueDeadlineAtMs) ||
         queueDeadlineAtMs <= 0))
    { reason = "INVALID_QUEUE_DEADLINE"; return false; }
    request.queueDeadlineAtMs = static_cast<std::uint64_t>(queueDeadlineAtMs);
    if (!fields.at(OrderId).empty() &&
        !ParseLong(fields.at(OrderId), request.call.orderId))
    { reason = "INVALID_ORDER_ID"; return false; }
    if (!fields.at(Quantity).empty() &&
        !ParseDouble(fields.at(Quantity), request.call.ibOrder.totalQuantity))
    { reason = "INVALID_QUANTITY"; return false; }
    if (!fields.at(LimitPrice).empty() &&
        !ParseDouble(fields.at(LimitPrice), request.call.ibOrder.lmtPrice))
    { reason = "INVALID_LIMIT_PRICE"; return false; }
    if (!fields.at(ReferencePrice).empty() &&
        !ParseDouble(fields.at(ReferencePrice), request.call.referencePrice))
    { reason = "INVALID_REFERENCE_PRICE"; return false; }
    if (!fields.at(ExpiresAtMs).empty() &&
        !ParseLongLong(fields.at(ExpiresAtMs), request.call.expiresAtMs))
    { reason = "INVALID_EXPIRY"; return false; }
    long waitMs = 0;
    if (!fields.at(WaitTimeoutMs).empty() &&
        (!ParseLong(fields.at(WaitTimeoutMs), waitMs) ||
         waitMs < 0 || waitMs > 30000))
    { reason = "INVALID_WAIT_TIMEOUT"; return false; }
    request.call.waitTimeoutMs = static_cast<int>(waitMs);
    long long after = 0;
    if (!fields.at(AfterEventSequence).empty() &&
        (!ParseLongLong(fields.at(AfterEventSequence), after) || after < 0))
    { reason = "INVALID_EVENT_CURSOR"; return false; }
    request.call.afterEventSequence = static_cast<std::uint64_t>(after);
    return true;
}

bool ValidateDecodedSemantics(const TradingToolHostRequest& request,
                              std::string& reason)
{
    if (request.call.name == "system.cancel_request") return true;
    std::string code;
    std::string detail;
    if (TradingToolWireContract::ValidateCallSemantics(
            request.call, code, detail)) return true;
    reason = code + ":" + detail;
    return false;
}

} // namespace
const char* TypedToolProtocol::ProtocolName()
{
    return "hepta.agent-tools";
}

unsigned int TypedToolProtocol::ProtocolVersion()
{
    return 1;
}

bool TypedToolProtocol::EncodeRequest(const TradingToolHostRequest& request, std::string& body, std::string& reason)
{
    if (request.sessionToken.empty() || request.toolCallId.empty() || request.call.name.empty())
    {
        reason = "SCHEMA_MISSING_PROTOCOL_ENVELOPE";
        return false;
    }
    if (request.protocolMinVersion < 1 || request.protocolMaxVersion < request.protocolMinVersion ||
        request.protocolMinVersion > ProtocolVersion() || request.protocolMaxVersion < ProtocolVersion())
    {
        reason = "UNSUPPORTED_PROTOCOL_VERSION";
        return false;
    }
    if (!request.expectedSchemaHash.empty() && !IsSchemaHash(request.expectedSchemaHash))
    {
        reason = "INVALID_SCHEMA_HASH";
        return false;
    }
    std::string semanticCode;
    std::string semanticDetail;
    if (request.call.name != "system.cancel_request" &&
        !TradingToolWireContract::ValidateCallSemantics(
            request.call, semanticCode, semanticDetail))
    {
        reason = semanticCode + ":" + semanticDetail;
        return false;
    }
    std::map<unsigned int, std::string> fields;
    fields[SessionToken] = request.sessionToken;
    fields[ToolCallId] = request.toolCallId;
    fields[ToolName] = request.call.name;
    fields[ProtocolMinVersion] = Number(request.protocolMinVersion);
    fields[ProtocolMaxVersion] = Number(request.protocolMaxVersion);
    if (!request.expectedSchemaHash.empty()) fields[ExpectedSchemaHash] = request.expectedSchemaHash;
    if (request.queueDeadlineAtMs != 0) fields[QueueDeadlineAtMs] = Number(request.queueDeadlineAtMs);
    if (!request.cancelToolCallId.empty()) fields[CancelToolCallId] = request.cancelToolCallId;
    AddTargetFields(fields, request.call);
    if (!request.call.instrument.empty()) fields[Instrument] = request.call.instrument;
    if (request.call.orderId >= 0) fields[OrderId] = Number(request.call.orderId);
    if (!request.call.ibContract.symbol.empty()) fields[Symbol] = request.call.ibContract.symbol;
    if (!request.call.ibContract.currency.empty()) fields[Currency] = request.call.ibContract.currency;
    if (!request.call.ibContract.secType.empty()) fields[SecType] = request.call.ibContract.secType;
    if (!request.call.ibContract.exchange.empty()) fields[Exchange] = request.call.ibContract.exchange;
    if (!request.call.ibOrder.action.empty()) fields[Side] = request.call.ibOrder.action;
    if (!request.call.ibOrder.orderType.empty()) fields[OrderType] = request.call.ibOrder.orderType;
    if (request.call.ibOrder.totalQuantity != 0.0) fields[Quantity] = Number(request.call.ibOrder.totalQuantity);
    if (request.call.ibOrder.lmtPrice != 0.0) fields[LimitPrice] = Number(request.call.ibOrder.lmtPrice);
    if (request.call.referencePrice != 0.0) fields[ReferencePrice] = Number(request.call.referencePrice);
    if (request.call.expiresAtMs != 0) fields[ExpiresAtMs] = Number(request.call.expiresAtMs);
    if (request.call.waitTimeoutMs != 0) fields[WaitTimeoutMs] = Number(request.call.waitTimeoutMs);
    if (request.call.afterEventSequence != 0) fields[AfterEventSequence] = Number(request.call.afterEventSequence);
    if (!request.call.timeInForce.empty()) fields[TimeInForce] = request.call.timeInForce;
    if (!request.call.previewPermit.empty()) fields[PreviewPermit] = request.call.previewPermit;
    if (!ValidateFieldSet(fields, reason)) return false;

    body.assign("HTT1", 4);
    for (std::map<unsigned int, std::string>::const_iterator it = fields.begin(); it != fields.end(); ++it)
        AddField(body, it->first, it->second);
    if (body.size() > 65536) { reason = "REQUEST_TOO_LARGE"; return false; }
    reason.clear();
    return true;
}

bool TypedToolProtocol::DecodeRequest(const std::string& body, TradingToolHostRequest& request, std::string& reason)
{
    DecodedFields fields;
    if (!DecodeFields(body, fields, reason)) return false;
    request = TradingToolHostRequest();
    if (!DecodeEnvelope(fields, ProtocolVersion(), request, reason)) return false;
    DecodeTextFields(fields, request);
    if (!DecodeNumericFields(fields, request, reason) ||
        !ValidateDecodedSemantics(request, reason)) return false;
    reason.clear();
    return true;
}
