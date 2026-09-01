#include "typed_tool_protocol.h"
#include "../tools/trading_tool_wire_contract.h"
#include "../numeric/fixed_decimal.h"

#include <cerrno>
#include <cctype>
#include <cmath>
#include <cstdlib>
#include <iomanip>
#include <limits>
#include <locale>
#include <map>
#include <set>
#include <sstream>

namespace {

// HEPTA-GENERATED-WIRE-CATALOG-BEGIN
enum FieldId
{
    SessionToken = 1,
    ToolCallId = 2,
    ToolName = 3,
    Instrument = 4,
    OrderId = 5,
    Symbol = 6,
    Currency = 7,
    SecType = 8,
    Exchange = 9,
    Side = 10,
    OrderType = 11,
    Quantity = 12,
    LimitPrice = 13,
    ReferencePrice = 14,
    ExpiresAtMs = 15,
    WaitTimeoutMs = 16,
    AfterEventSequence = 17,
    TimeInForce = 18,
    QueueDeadlineAtMs = 19,
    CancelToolCallId = 20,
    TargetToolName = 21,
    ProtocolMinVersion = 22,
    ProtocolMaxVersion = 23,
    ExpectedSchemaHash = 24,
    PreviewPermit = 25,
    TargetCommandId = 26
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

const long long kWireNumericScale = 1000000LL;
const char* const kWireNumericPolicy = "hepta.numeric.fixed-v1";
// HEPTA-GENERATED-WIRE-CATALOG-END
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
    if (tool == "decision.get_snapshot") return id == Instrument;
    if (tool == "account.get_summary" || tool == "portfolio.list_positions" ||
        tool == "orders.list" || tool == "risk.get_limits" || tool == "system.get_health" ||
        tool == "system.tools.list") return false;
    if (tool == "events.wait") return id == WaitTimeoutMs || id == AfterEventSequence;
    if (tool == "trade.cancel_order") return id == OrderId;
    if (tool == "intent.preview_target_position")
        return id == Instrument || id == Quantity || id == ReferencePrice ||
               id == ExpiresAtMs;
    if (tool == "intent.apply_target_position")
        return id == Instrument || id == Quantity || id == ReferencePrice ||
               id == ExpiresAtMs || id == PreviewPermit;
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
    if (tool == "decision.get_snapshot") return id == Instrument;
    if (tool == "trade.cancel_order") return id == OrderId;
    if (tool == "intent.preview_target_position" ||
        tool == "intent.apply_target_position")
    {
        return id == Instrument || id == Quantity || id == ReferencePrice ||
               id == ExpiresAtMs ||
               (tool == "intent.apply_target_position" && id == PreviewPermit);
    }
    if (tool == "trade.place_order" || tool == "risk.preview_order")
    {
        return id == Instrument || id == Side || id == OrderType || id == Quantity ||
               id == ExpiresAtMs || id == TimeInForce ||
               (tool == "trade.place_order" && id == PreviewPermit);
    }
    return false;
}

std::size_t MaxFieldLength(unsigned int id)
{
    if (id == SessionToken) return 512;
    if (id == CancelToolCallId) return 128;
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
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')))
            return false;
    }
    return true;
}

bool IsValidUtf8Text(const std::string& value)
{
    if (value.find('\0') != std::string::npos) return false;
    std::size_t offset = 0;
    while (offset < value.size())
    {
        const unsigned char first =
            static_cast<unsigned char>(value[offset]);
        if (first <= 0x7fu)
        {
            if (first < 0x20u || first == 0x7fu) return false;
            ++offset;
            continue;
        }
        std::size_t continuationCount = 0;
        if (first >= 0xc2u && first <= 0xdfu) continuationCount = 1;
        else if (first >= 0xe0u && first <= 0xefu) continuationCount = 2;
        else if (first >= 0xf0u && first <= 0xf4u) continuationCount = 3;
        else return false;
        if (value.size() - offset <= continuationCount) return false;
        const unsigned char second =
            static_cast<unsigned char>(value[offset + 1]);
        if ((first == 0xe0u && second < 0xa0u) ||
            (first == 0xedu && second >= 0xa0u) ||
            (first == 0xf0u && second < 0x90u) ||
            (first == 0xf4u && second > 0x8fu))
            return false;
        for (std::size_t i = 1; i <= continuationCount; ++i)
        {
            const unsigned char continuation =
                static_cast<unsigned char>(value[offset + i]);
            if (continuation < 0x80u || continuation > 0xbfu) return false;
        }
        const std::uint32_t codepoint =
            continuationCount == 1 ?
                ((static_cast<std::uint32_t>(first & 0x1fu) << 6) |
                 (static_cast<std::uint32_t>(second) & 0x3fu)) :
            continuationCount == 2 ?
                ((static_cast<std::uint32_t>(first & 0x0fu) << 12) |
                 (static_cast<std::uint32_t>(second & 0x3fu) << 6) |
                 (static_cast<std::uint32_t>(
                     static_cast<unsigned char>(value[offset + 2])) & 0x3fu)) :
                ((static_cast<std::uint32_t>(first & 0x07u) << 18) |
                 (static_cast<std::uint32_t>(second & 0x3fu) << 12) |
                 (static_cast<std::uint32_t>(
                     static_cast<unsigned char>(value[offset + 2]) & 0x3fu) << 6) |
                 (static_cast<std::uint32_t>(
                     static_cast<unsigned char>(value[offset + 3])) & 0x3fu));
        if (codepoint >= 0x7fu && codepoint <= 0x9fu) return false;
        offset += continuationCount + 1u;
    }
    return true;
}

// Definitions follow the field decoder below; declarations here let the
// shared field-set validator enforce numeric grammar for encoder output too.
bool CanonicalSignedInteger(const std::string& value);
bool CanonicalFloating(const std::string& value);

bool IsIntegerField(unsigned int id)
{
    return id == OrderId || id == ExpiresAtMs || id == WaitTimeoutMs ||
        id == AfterEventSequence || id == QueueDeadlineAtMs ||
        id == ProtocolMinVersion || id == ProtocolMaxVersion;
}

bool IsFloatingField(unsigned int id)
{
    return id == Quantity || id == LimitPrice || id == ReferencePrice;
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
    const std::map<unsigned int, std::string>::const_iterator targetTool =
        fields.find(TargetToolName);
    if (targetTool != fields.end() &&
        !TradingToolWireContract::IsCanonicalToolName(targetTool->second))
    {
        reason = "INVALID_TARGET_TOOL_NAME";
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
        if (!IsValidUtf8Text(it->second))
        {
            reason = std::string("INVALID_UTF8_FIELD:") + FieldName(it->first);
            return false;
        }
        if ((IsIntegerField(it->first) &&
             !CanonicalSignedInteger(it->second)) ||
            (IsFloatingField(it->first) &&
             !CanonicalFloating(it->second)))
        {
            reason = std::string("INVALID_NUMERIC_FIELD:") +
                FieldName(it->first);
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
    const std::map<unsigned int, std::string>::const_iterator session =
        fields.find(SessionToken);
    if (session == fields.end() || session->second.size() > MaxFieldLength(SessionToken) ||
        session->second.find('\0') != std::string::npos)
    {
        reason = "INVALID_SESSION_TOKEN";
        return false;
    }
    const std::map<unsigned int, std::string>::const_iterator cancel =
        fields.find(CancelToolCallId);
    if (cancel != fields.end() &&
        !TradingToolWireContract::IsCanonicalCommandId(cancel->second))
    {
        reason = "INVALID_CANCEL_TOOL_CALL_ID";
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
    if (offset > in.size() || in.size() - offset < 2) return false;
    value = (static_cast<unsigned char>(in[offset]) << 8) |
            static_cast<unsigned char>(in[offset + 1]);
    offset += 2;
    return true;
}

bool ReadU32(const std::string& in, std::size_t& offset, std::uint32_t& value)
{
    if (offset > in.size() || in.size() - offset < 4) return false;
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
class ResetOnFailure
{
public:
    explicit ResetOnFailure(T& value) : m_value(value), m_committed(false) {}
    ~ResetOnFailure()
    {
        if (!m_committed) m_value = T();
    }
    void Commit() { m_committed = true; }

private:
    T& m_value;
    bool m_committed;
};

std::string Number(double value)
{
    HeptaFixedDecimal fixed;
    std::string reason;
    if (!HeptaFixedDecimal::FromDoubleExact(value, fixed, reason))
        return "invalid";
    return fixed.ToCanonicalString();
}

template <typename T>
std::string Number(T value)
{
    if (value == 0) return "0";
    std::ostringstream out;
    // Keep the typed wire grammar locale-independent even when an embedding
    // process has installed a non-C global locale.
    out.imbue(std::locale::classic());
    out << std::setprecision(17) << value;
    return out.str();
}

// Typed-tool numeric fields are serialized by Number() and therefore have a
// canonical ASCII spelling.  strto* accepts leading whitespace, '+', and
// integer leading zeroes; reject those aliases before conversion so a peer
// cannot rely on locale or conversion quirks at the gateway boundary.
bool CanonicalSignedInteger(const std::string& value)
{
    if (value.empty()) return false;
    std::size_t offset = value[0] == '-' ? 1u : 0u;
    if (offset == value.size() ||
        (value[offset] == '0' &&
         (offset != 0 || offset + 1u < value.size()))) return false;
    for (; offset < value.size(); ++offset)
        if (value[offset] < '0' || value[offset] > '9') return false;
    return true;
}

bool CanonicalFloating(const std::string& value)
{
    if (value.empty()) return false;
    const bool negative = value[0] == '-';
    std::size_t offset = negative ? 1u : 0u;
    if (offset == value.size()) return false;
    if (value[offset] == '0')
    {
        ++offset;
        if (offset < value.size() && value[offset] >= '0' &&
            value[offset] <= '9') return false;
    }
    else
    {
        if (value[offset] < '1' || value[offset] > '9') return false;
        while (offset < value.size() && value[offset] >= '0' &&
               value[offset] <= '9') ++offset;
    }
    if (offset < value.size() && value[offset] == '.')
    {
        ++offset;
        const std::size_t fractionStart = offset;
        while (offset < value.size() && value[offset] >= '0' &&
               value[offset] <= '9') ++offset;
        if (offset == fractionStart) return false;
    }
    if (offset < value.size() &&
        (value[offset] == 'e' || value[offset] == 'E'))
    {
        ++offset;
        if (offset < value.size() &&
            (value[offset] == '+' || value[offset] == '-')) ++offset;
        const std::size_t exponentStart = offset;
        while (offset < value.size() && value[offset] >= '0' &&
               value[offset] <= '9') ++offset;
        if (offset == exponentStart) return false;
    }
    if (offset != value.size()) return false;
    // Avoid two wire spellings for the same signed-zero value.  Negative
    // finite numbers remain valid; only a mantissa made entirely of zeroes
    // (optionally with a decimal point) is rejected.
    if (negative)
    {
        bool allZero = true;
        for (std::size_t i = 1; i < value.size(); ++i)
        {
            if (value[i] == '.') continue;
            if (value[i] == 'e' || value[i] == 'E') break;
            if (value[i] != '0') { allZero = false; break; }
        }
        if (allZero) return false;
    }
    return true;
}

void AddTargetFields(std::map<unsigned int, std::string>& fields,
                     const TradingToolCall& call)
{
    if (!call.targetToolName.empty()) fields[TargetToolName] = call.targetToolName;
    if (!call.targetCommandId.empty()) fields[TargetCommandId] = call.targetCommandId;
}

bool ParseLong(const std::string& value, long& out)
{
    if (!CanonicalSignedInteger(value)) return false;
    char* end = nullptr;
    errno = 0;
    const long parsed = std::strtol(value.c_str(), &end, 10);
    if (errno != 0 || end == value.c_str() || *end != '\0') return false;
    out = parsed;
    return true;
}

bool ParseLongLong(const std::string& value, long long& out)
{
    if (!CanonicalSignedInteger(value)) return false;
    char* end = nullptr;
    errno = 0;
    const long long parsed = std::strtoll(value.c_str(), &end, 10);
    if (errno != 0 || end == value.c_str() || *end != '\0') return false;
    out = parsed;
    return true;
}

// Cursor and queue-deadline fields are unsigned protocol quantities.  Do not
// parse them through signed long long (which silently excludes the upper half
// of uint64_t) or through strtoull (whose accepted grammar is locale/libc
// dependent).  The wire spelling is decimal, with only the single digit zero
// permitted as a leading-zero form.
bool CanonicalUnsignedInteger(const std::string& value)
{
    if (value.empty()) return false;
    if (value.size() > 1 && value[0] == '0') return false;
    for (std::size_t i = 0; i < value.size(); ++i)
        if (value[i] < '0' || value[i] > '9') return false;
    return true;
}

bool ParseUint64(const std::string& value, std::uint64_t& out)
{
    if (!CanonicalUnsignedInteger(value)) return false;
    const std::uint64_t maximum =
        std::numeric_limits<std::uint64_t>::max();
    std::uint64_t parsed = 0;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const std::uint64_t digit =
            static_cast<std::uint64_t>(value[i] - '0');
        if (parsed > (maximum - digit) / 10u) return false;
        parsed = parsed * 10u + digit;
    }
    out = parsed;
    return true;
}

bool ParseDouble(const std::string& value, double& out)
{
    if (!CanonicalFloating(value)) return false;
    HeptaFixedDecimal fixed;
    std::string reason;
    if (!HeptaFixedDecimal::ParseCanonical(value, fixed, reason))
        return false;
    out = fixed.ToDouble();
    return true;
}

typedef std::map<unsigned int, std::string> DecodedFields;

bool DecodeFields(const std::string& body, DecodedFields& fields,
                  std::string& reason)
{
    if (body.size() > 65536)
    { reason = "REQUEST_TOO_LARGE"; return false; }
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
            length > 32768 || offset > body.size() ||
            static_cast<std::size_t>(length) > body.size() - offset)
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
    const DecodedFields::const_iterator session = fields.find(SessionToken);
    const DecodedFields::const_iterator callId = fields.find(ToolCallId);
    const DecodedFields::const_iterator tool = fields.find(ToolName);
    if (session == fields.end() || callId == fields.end() ||
        tool == fields.end() || session->second.empty() ||
        callId->second.empty() || tool->second.empty())
    {
        reason = "SCHEMA_MISSING_PROTOCOL_ENVELOPE";
        return false;
    }
    request.sessionToken = session->second;
    request.toolCallId = callId->second;
    request.call.name = tool->second;
    long minimum = 1;
    long maximum = 1;
    const DecodedFields::const_iterator min = fields.find(ProtocolMinVersion);
    const DecodedFields::const_iterator max = fields.find(ProtocolMaxVersion);
    const std::string minField = min == fields.end() ? std::string() : min->second;
    const std::string maxField = max == fields.end() ? std::string() : max->second;
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
    const DecodedFields::const_iterator schema =
        fields.find(ExpectedSchemaHash);
    request.expectedSchemaHash =
        schema == fields.end() ? std::string() : schema->second;
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
    std::uint64_t queueDeadlineAtMs = 0;
    long long expiresAtMs = 0;
    if (!fields.at(QueueDeadlineAtMs).empty() &&
        (!ParseUint64(fields.at(QueueDeadlineAtMs), queueDeadlineAtMs) ||
         queueDeadlineAtMs <= 0))
    { reason = "INVALID_QUEUE_DEADLINE"; return false; }
    request.queueDeadlineAtMs = queueDeadlineAtMs;
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
        !ParseLongLong(fields.at(ExpiresAtMs), expiresAtMs))
    { reason = "INVALID_EXPIRY"; return false; }
    request.call.expiresAtMs = static_cast<std::int64_t>(expiresAtMs);
    long waitMs = 0;
    if (!fields.at(WaitTimeoutMs).empty() &&
        (!ParseLong(fields.at(WaitTimeoutMs), waitMs) ||
         waitMs < 0 || waitMs > 30000))
    { reason = "INVALID_WAIT_TIMEOUT"; return false; }
    request.call.waitTimeoutMs = static_cast<int>(waitMs);
    std::uint64_t after = 0;
    if (!fields.at(AfterEventSequence).empty() &&
        !ParseUint64(fields.at(AfterEventSequence), after))
    { reason = "INVALID_EVENT_CURSOR"; return false; }
    request.call.afterEventSequence = after;
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
    reason.clear();
    body.clear();
    ResetOnFailure<std::string> bodyGuard(body);
    if (request.sessionToken.empty() || request.toolCallId.empty() || request.call.name.empty())
    {
        reason = "SCHEMA_MISSING_PROTOCOL_ENVELOPE";
        return false;
    }
    if (request.sessionToken.size() > MaxFieldLength(SessionToken) ||
        request.sessionToken.find('\0') != std::string::npos)
    {
        reason = "INVALID_SESSION_TOKEN";
        return false;
    }
    if (!TradingToolWireContract::IsCanonicalCommandId(request.toolCallId))
    {
        reason = "INVALID_TOOL_CALL_ID";
        return false;
    }
    if (!TradingToolWireContract::IsCanonicalToolName(request.call.name))
    {
        reason = "INVALID_TOOL_NAME";
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
    // Zero is a valid signed target position (a no-op), so intent calls must
    // serialize quantity even when it is numerically zero.
    if (request.call.name == "intent.preview_target_position" ||
        request.call.name == "intent.apply_target_position" ||
        request.call.ibOrder.totalQuantity != 0.0)
        fields[Quantity] = Number(request.call.ibOrder.totalQuantity);
    if (request.call.ibOrder.lmtPrice != 0.0) fields[LimitPrice] = Number(request.call.ibOrder.lmtPrice);
    if (request.call.name == "intent.preview_target_position" ||
        request.call.name == "intent.apply_target_position" ||
        request.call.referencePrice != 0.0)
        fields[ReferencePrice] = Number(request.call.referencePrice);
    if (request.call.name == "intent.preview_target_position" ||
        request.call.name == "intent.apply_target_position" ||
        request.call.expiresAtMs != 0)
        fields[ExpiresAtMs] = Number(request.call.expiresAtMs);
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
    bodyGuard.Commit();
    return true;
}

bool TypedToolProtocol::DecodeRequest(const std::string& body, TradingToolHostRequest& request, std::string& reason)
{
    request = TradingToolHostRequest();
    ResetOnFailure<TradingToolHostRequest> requestGuard(request);
    DecodedFields fields;
    if (!DecodeFields(body, fields, reason)) return false;
    if (!DecodeEnvelope(fields, ProtocolVersion(), request, reason)) return false;
    DecodeTextFields(fields, request);
    if (!DecodeNumericFields(fields, request, reason) ||
        !ValidateDecodedSemantics(request, reason)) return false;
    reason.clear();
    requestGuard.Commit();
    return true;
}
