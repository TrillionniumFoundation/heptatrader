#include "execution_service_protocol.h"

#include <cerrno>
#include <cmath>
#include <cstdlib>
#include <limits>
#include <locale>
#include <map>
#include <set>
#include <sstream>

namespace
{
const char kMagic[] = {'H', 'E', 'X', '1'};
// The Unix transport normally supplies a deployment-specific request cap,
// but the codec is also callable directly (and from fuzz/replay paths). Keep
// a hard ceiling and field-count bound here so a hostile peer cannot grow the
// map with millions of unique tags before the exact schema check runs.
const std::size_t kMaximumExecutionBodyBytes = 1024u * 1024u;
const std::size_t kMaximumExecutionFields = 128;

enum Field : unsigned int
{
    AgentId = 1, SessionId, ToolCallId, Strategy, Account, Venue, ExecutionDomain,
    LeaseToken, LeaseGeneration, AllowCancelAny, Instrument, ExpiresAtMs, ReferencePrice,
    Symbol, SecType, Exchange, PrimaryExchange, Currency, ContractMonth, Right, Strike,
    Multiplier, TradingClass, LocalSymbol, Action, OrderType, Quantity, LimitPrice,
    AuxPrice, OutsideRth, OrderId, Side, TargetCommandId,
    ExpectedServiceEpoch, ExpectedServiceFencingGeneration, ReadQuery,
    TimeInForce, OrderRef, PreviewPermit, RecoveryIngressFence,
    TerminalPreliminaryReceiptSha256,
    ResultStatus = 100, ResultCommandId, ResultOrderId, ResultReasonCode, ResultDetail,
    ResultTargetCommandId, ResultTargetStatus, ResultAffectedCount, ResultMutationBlocked,
    ResultServiceEpoch, ResultServiceFencingGeneration,
    ResultOwnerAuditAuthoritative, ResultOwnerAuditComplete,
    ResultOwnerActiveOrderCount, ResultOwnerUncertainCommandCount,
    ResultBrokerConnectionEpoch, ResultBrokerActiveGeneration,
    ResultBrokerTerminalGeneration, ResultOwnerAccount,
    ResultOwnerExecutionDomain, ResultBrokerRiskGeneration,
    ResultBrokerAccountGeneration, ResultBrokerPositionGeneration,
    ResultBrokerFxCashGeneration, ResultBrokerExposureGeneration,
    ResultBrokerTerminalExposureGeneration,
    ResultBrokerRiskAbsorbedExposureGeneration,
    ResultBrokerGlobalActiveOrderCount,
    ResultBrokerPostFillRiskReconciliationPending,
    ResultBrokerRecoveryAuditBarrierComplete,
    ResultBrokerRecoveryAuditNewConnectionEpochRequired,
    ResultBrokerPositionQuantity, ResultBrokerGrossAbsolutePosition,
    ResultTerminalizationServiceEpoch,
    ResultTerminalizationServiceFencingGeneration,
    ResultTerminalizationGeneration, ResultTerminalLatchSha256,
    ResultTerminalMutationGateClosed,
    ResultTerminalBrokerTransportConnected,
    ResultTerminalBrokerEventIngressHalted,
    ResultTerminalBrokerCallbackQueueDrained,
    ResultTerminalBrokerCallbacksInFlight,
    ResultTerminalBrokerReconnectPermitted,
    ResultTerminalLatchDurable, ResultTerminalRuntimeLatchLoaded,
    ResultTerminalRuntimeVerified, ResultTerminalReplay
};

void AppendU16(std::string& out, unsigned int value)
{
    out.push_back(static_cast<char>((value >> 8) & 0xff));
    out.push_back(static_cast<char>(value & 0xff));
}

void AppendU32(std::string& out, std::size_t value)
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

bool ReadU32(const std::string& in, std::size_t& offset, std::size_t& value)
{
    if (offset > in.size() || in.size() - offset < 4) return false;
    value = (static_cast<std::size_t>(static_cast<unsigned char>(in[offset])) << 24) |
        (static_cast<std::size_t>(static_cast<unsigned char>(in[offset + 1])) << 16) |
        (static_cast<std::size_t>(static_cast<unsigned char>(in[offset + 2])) << 8) |
        static_cast<unsigned char>(in[offset + 3]);
    offset += 4;
    return true;
}

void AppendField(std::string& out, unsigned int tag, const std::string& value)
{
    AppendU16(out, tag);
    AppendU32(out, value.size());
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

template <typename T>
std::string Number(T value)
{
    // Canonical floating grammar has one spelling for zero; this also keeps
    // a signed-zero value from being emitted and then rejected by the strict
    // decoder on a request/response round trip.
    if (value == 0) return "0";
    std::ostringstream out;
    // Wire numbers are ASCII and must not follow a process-global locale
    // (which may use a comma decimal separator or be changed by another
    // thread while a request is encoded).
    out.imbue(std::locale::classic());
    out.precision(17);
    out << value;
    return out.str();
}

bool DecodeEnvelope(const std::string& body, unsigned int& kind,
                    std::map<unsigned int, std::string>& fields,
                    bool allowLargeResultDetail, std::string& reason)
{
    if (body.size() > kMaximumExecutionBodyBytes)
    {
        reason = "EXECUTION_PROTOCOL_BODY_TOO_LARGE";
        return false;
    }
    if (body.size() < 8 || body.compare(0, 4, kMagic, 4) != 0)
    {
        reason = "EXECUTION_PROTOCOL_BAD_MAGIC";
        return false;
    }
    std::size_t offset = 4;
    unsigned int version = 0;
    if (!ReadU16(body, offset, version) || version != ExecutionServiceProtocol::ProtocolVersion() ||
        !ReadU16(body, offset, kind))
    {
        reason = "EXECUTION_PROTOCOL_UNSUPPORTED_VERSION";
        return false;
    }
    while (offset < body.size())
    {
        if (fields.size() >= kMaximumExecutionFields)
        {
            reason = "EXECUTION_PROTOCOL_TOO_MANY_FIELDS";
            return false;
        }
        unsigned int tag = 0;
        std::size_t length = 0;
        if (!ReadU16(body, offset, tag) || !ReadU32(body, offset, length))
        {
            reason = "EXECUTION_PROTOCOL_INVALID_FIELD";
            return false;
        }
        const std::size_t maximumLength =
            allowLargeResultDetail && tag == ResultDetail ? 32768 : 4096;
        if (length > maximumLength || offset > body.size() ||
            length > body.size() - offset ||
            fields.count(tag) != 0)
        {
            reason = "EXECUTION_PROTOCOL_INVALID_FIELD";
            return false;
        }
        fields[tag] = body.substr(offset, length);
        offset += length;
    }
    return true;
}

bool Require(const std::map<unsigned int, std::string>& fields, unsigned int tag,
             std::string& out, std::string& reason)
{
    const std::map<unsigned int, std::string>::const_iterator found = fields.find(tag);
    if (found == fields.end())
    {
        reason = "EXECUTION_PROTOCOL_MISSING_FIELD";
        return false;
    }
    out = found->second;
    return true;
}

bool HasExactFields(const std::map<unsigned int, std::string>& fields,
                    const std::set<unsigned int>& expected,
                    std::string& reason)
{
    if (fields.size() != expected.size())
    {
        reason = "EXECUTION_PROTOCOL_FIELD_SET_MISMATCH";
        return false;
    }
    for (std::set<unsigned int>::const_iterator it = expected.begin();
         it != expected.end(); ++it)
    {
        if (fields.find(*it) == fields.end())
        {
            reason = "EXECUTION_PROTOCOL_FIELD_SET_MISMATCH";
            return false;
        }
    }
    return true;
}

bool CanonicalSha256(const std::string& value)
{
    if (value.size() != 71 || value.compare(0, 7, "sha256:") != 0)
        return false;
    for (std::size_t i = 7; i < value.size(); ++i)
        if (!((value[i] >= '0' && value[i] <= '9') ||
              (value[i] >= 'a' && value[i] <= 'f')))
            return false;
    return true;
}

std::set<unsigned int> ContextFields()
{
    const unsigned int values[] = {
        AgentId, SessionId, ToolCallId, Strategy, Account, Venue,
        ExecutionDomain, AllowCancelAny};
    return std::set<unsigned int>(values, values + sizeof(values) / sizeof(values[0]));
}

void AddServiceIdentityFields(std::set<unsigned int>& fields)
{
    fields.insert(ExpectedServiceEpoch);
    fields.insert(ExpectedServiceFencingGeneration);
}

// The binary protocol carries numbers as canonical ASCII generated by
// Number().  The C strto* family intentionally accepts leading whitespace,
// a leading '+', and (for integers) leading zeroes; accepting those aliases
// here would let a peer bypass the canonical wire representation and produce
// surprising signed/unsigned conversions.  Keep the parser lexical and
// locale-independent before asking strto* to perform range/finite checks.
bool CanonicalUnsignedInteger(const std::string& value)
{
    if (value.empty() || (value.size() > 1 && value[0] == '0')) return false;
    for (std::string::const_iterator it = value.begin(); it != value.end(); ++it)
        if (*it < '0' || *it > '9') return false;
    return true;
}

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

bool ParseUnsigned(const std::string& value, std::uint64_t& out)
{
    if (!CanonicalUnsignedInteger(value)) return false;
    // Parse explicitly instead of delegating to strtoull.  Apart from making
    // the wire codec independent of the host's unsigned-long-long width,
    // this keeps the overflow check defined for every uint64_t value and
    // avoids implementation-specific errno/end-pointer behaviour.
    const std::uint64_t maximum =
        (std::numeric_limits<std::uint64_t>::max)();
    std::uint64_t parsed = 0;
    for (std::string::const_iterator it = value.begin(); it != value.end(); ++it)
    {
        const std::uint64_t digit =
            static_cast<std::uint64_t>(*it - '0');
        if (parsed > (maximum - digit) / 10u) return false;
        parsed = parsed * 10u + digit;
    }
    out = parsed;
    return true;
}

bool ParseDouble(const std::string& value, double& out)
{
    if (!CanonicalFloating(value)) return false;
    // Parse with the classic locale explicitly.  ``strtod`` consults the
    // process-global C locale, so a peer could make an otherwise canonical
    // dot-decimal request fail (or be interpreted differently) after a
    // locale change in an embedding process.
    std::istringstream input(value);
    input.imbue(std::locale::classic());
    double parsed = 0.0;
    input >> parsed;
    // Number() canonicalizes every exact zero to "0".  Reject alternate
    // zero spellings and underflow-to-zero values so a peer cannot send a
    // lexeme that changes representation (or a negative zero) on re-encode.
    if (!input || !input.eof() || !std::isfinite(parsed) ||
        (parsed == 0.0 && value != "0")) return false;
    out = parsed;
    return true;
}

bool IsCanonicalDecimal(const std::string& value)
{
    if (value.empty()) return true;
    if (value == "0") return true;
    std::size_t offset = 0;
    if (value[0] == '-')
    {
        if (value.size() == 1) return false;
        offset = 1;
    }
    if (value[offset] == '0')
    {
        ++offset;
        if (offset == value.size()) return false;
    }
    else
    {
        if (value[offset] < '1' || value[offset] > '9') return false;
        while (offset < value.size() && value[offset] >= '0' &&
               value[offset] <= '9')
            ++offset;
    }
    if (offset == value.size()) return true;
    if (value[offset++] != '.' || offset == value.size()) return false;
    for (; offset < value.size(); ++offset)
        if (value[offset] < '0' || value[offset] > '9') return false;
    return value.back() != '0';
}

// Context values cross the Gateway -> Execution authority boundary and are
// subsequently used as identity/key material.  Keep the wire representation
// bounded and locale-independent: control bytes (including the RequestKey
// delimiter used by older in-process paths), DEL and non-ASCII bytes are not
// valid identity text.  Optional fields may be empty because the legacy
// simulator contract permits an unset strategy/venue/domain, but required
// ownership fields must be present.
bool CanonicalContextText(const std::string& value, std::size_t maximum,
                          bool required)
{
    if ((required && value.empty()) || value.size() > maximum) return false;
    for (std::string::const_iterator it = value.begin(); it != value.end(); ++it)
    {
        const unsigned char byte = static_cast<unsigned char>(*it);
        if (byte < 0x21 || byte > 0x7e) return false;
    }
    return true;
}

bool CanonicalCommandId(const std::string& value, bool required)
{
    if (!CanonicalContextText(value, 128, required)) return false;
    if (value.empty()) return !required;
    bool sawAlphaNumeric = false;
    for (std::string::const_iterator it = value.begin(); it != value.end(); ++it)
    {
        const unsigned char byte = static_cast<unsigned char>(*it);
        if ((byte >= '0' && byte <= '9') ||
            (byte >= 'A' && byte <= 'Z') ||
            (byte >= 'a' && byte <= 'z'))
        {
            sawAlphaNumeric = true;
            break;
        }
    }
    return sawAlphaNumeric;
}

bool ValidateContext(const AgentExecutionContext& context, std::string& reason)
{
    if (!CanonicalContextText(context.agentId, 128, true) ||
        !CanonicalContextText(context.sessionId, 256, true) ||
        !CanonicalCommandId(context.toolCallId, true) ||
        !CanonicalContextText(context.strategy, 128, false) ||
        !CanonicalContextText(context.account, 128, false) ||
        !CanonicalContextText(context.venue, 64, false) ||
        !CanonicalContextText(context.executionDomain, 128, false))
    {
        reason = "EXECUTION_PROTOCOL_INVALID_CONTEXT";
        return false;
    }
    reason.clear();
    return true;
}

bool ValidateServiceEpoch(const std::string& value)
{
    return CanonicalContextText(value, 128, true);
}

bool ValidateTargetCommandId(const std::string& value, bool required)
{
    return CanonicalCommandId(value, required);
}

// Result text is authority-controlled but still crosses an IPC boundary and
// is later embedded in a JSON envelope by the Gateway.  Validate UTF-8 by
// byte sequence (rather than the process locale) and cap it before encoding;
// this prevents malformed bytes, NULs and length truncation from producing a
// response that the downstream client cannot parse consistently.
bool ValidUtf8Text(const std::string& value, std::size_t maximum,
                   bool required, bool rejectControls)
{
    if ((required && value.empty()) || value.size() > maximum ||
        value.find('\0') != std::string::npos)
        return false;
    std::size_t offset = 0;
    while (offset < value.size())
    {
        const unsigned char first =
            static_cast<unsigned char>(value[offset]);
        std::uint32_t codepoint = 0;
        std::size_t continuationCount = 0;
        if (first <= 0x7fu)
        {
            codepoint = first;
            ++offset;
        }
        else
        {
            if (first >= 0xc2u && first <= 0xdfu)
                continuationCount = 1;
            else if (first >= 0xe0u && first <= 0xefu)
                continuationCount = 2;
            else if (first >= 0xf0u && first <= 0xf4u)
                continuationCount = 3;
            else
                return false;
            if (value.size() - offset <= continuationCount)
                return false;
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
                if (continuation < 0x80u || continuation > 0xbfu)
                    return false;
            }
            if (continuationCount == 1)
                codepoint = (static_cast<std::uint32_t>(first & 0x1fu) << 6) |
                    static_cast<std::uint32_t>(
                        static_cast<unsigned char>(value[offset + 1]) & 0x3fu);
            else if (continuationCount == 2)
                codepoint = (static_cast<std::uint32_t>(first & 0x0fu) << 12) |
                    (static_cast<std::uint32_t>(
                        static_cast<unsigned char>(value[offset + 1]) & 0x3fu) << 6) |
                    static_cast<std::uint32_t>(
                        static_cast<unsigned char>(value[offset + 2]) & 0x3fu);
            else
                codepoint = (static_cast<std::uint32_t>(first & 0x07u) << 18) |
                    (static_cast<std::uint32_t>(
                        static_cast<unsigned char>(value[offset + 1]) & 0x3fu) << 12) |
                    (static_cast<std::uint32_t>(
                        static_cast<unsigned char>(value[offset + 2]) & 0x3fu) << 6) |
                    static_cast<std::uint32_t>(
                        static_cast<unsigned char>(value[offset + 3]) & 0x3fu);
            offset += continuationCount + 1u;
        }
        if (rejectControls &&
            (codepoint < 0x20u ||
             (codepoint >= 0x7fu && codepoint <= 0x9fu)))
            return false;
    }
    return true;
}

bool ValidRequestText(const std::string& value, std::size_t maximum,
                      bool required)
{
    return ValidUtf8Text(value, maximum, required, true);
}

void EncodeContext(const AgentExecutionContext& context, std::string& body)
{
    AppendField(body, AgentId, context.agentId);
    AppendField(body, SessionId, context.sessionId);
    AppendField(body, ToolCallId, context.toolCallId);
    AppendField(body, Strategy, context.strategy);
    AppendField(body, Account, context.account);
    AppendField(body, Venue, context.venue);
    AppendField(body, ExecutionDomain, context.executionDomain);
    AppendField(body, AllowCancelAny, context.allowCancelAny ? "1" : "0");
}

bool DecodeContext(const std::map<unsigned int, std::string>& fields,
                   AgentExecutionContext& context, std::string& reason)
{
    std::string allowCancelAny;
    if (!Require(fields, AgentId, context.agentId, reason) ||
        !Require(fields, SessionId, context.sessionId, reason) ||
        !Require(fields, ToolCallId, context.toolCallId, reason) ||
        !Require(fields, Strategy, context.strategy, reason) ||
        !Require(fields, Account, context.account, reason) ||
        !Require(fields, Venue, context.venue, reason) ||
        !Require(fields, ExecutionDomain, context.executionDomain, reason) ||
        !Require(fields, AllowCancelAny, allowCancelAny, reason) ||
        (allowCancelAny != "0" && allowCancelAny != "1"))
    {
        if (reason.empty()) reason = "EXECUTION_PROTOCOL_INVALID_CONTEXT";
        return false;
    }
    context.allowCancelAny = allowCancelAny == "1";
    return ValidateContext(context, reason);
}
}

unsigned int ExecutionServiceProtocol::ProtocolVersion()
{
    return 10;
}

bool ExecutionServiceProtocol::EncodeRequest(const ExecutionServiceRequest& request,
                                             std::string& body, std::string& reason)
{
    reason.clear();
    body.clear();
    ResetOnFailure<std::string> bodyGuard(body);
    body.assign(kMagic, sizeof(kMagic));
    AppendU16(body, ProtocolVersion());
    AppendU16(body, static_cast<unsigned int>(request.operation));
    if (request.operation != ExecutionServiceOperation::GetServiceIdentity)
    {
        if (request.expectedServiceEpoch.empty() || request.expectedServiceEpoch.size() > 128 ||
            request.expectedServiceFencingGeneration == 0 ||
            !ValidateServiceEpoch(request.expectedServiceEpoch))
        {
            reason = "EXECUTION_PROTOCOL_SERVICE_EPOCH_REQUIRED";
            return false;
        }
        AppendField(body, ExpectedServiceEpoch, request.expectedServiceEpoch);
        AppendField(body, ExpectedServiceFencingGeneration,
            Number(request.expectedServiceFencingGeneration));
    }
    if (request.operation == ExecutionServiceOperation::PlaceIbOrder ||
        request.operation == ExecutionServiceOperation::PreviewOrder)
    {
        const IbPlaceOrderCommand& command = request.place;
        if (!ValidateContext(command.context, reason) ||
            !ValidRequestText(command.instrument, 128, true) ||
            !ValidRequestText(command.contract.symbol, 128, false) ||
            !ValidRequestText(command.contract.secType, 32, false) ||
            !ValidRequestText(command.contract.exchange, 64, false) ||
            !ValidRequestText(command.contract.primaryExchange, 64, false) ||
            !ValidRequestText(command.contract.currency, 16, false) ||
            !ValidRequestText(command.contract.lastTradeDateOrContractMonth, 32, false) ||
            !ValidRequestText(command.contract.right, 8, false) ||
            !ValidRequestText(command.contract.multiplier, 32, false) ||
            !ValidRequestText(command.contract.tradingClass, 64, false) ||
            !ValidRequestText(command.contract.localSymbol, 128, false) ||
            !ValidRequestText(command.order.action, 16, false) ||
            !ValidRequestText(command.order.orderType, 16, false) ||
            !ValidRequestText(command.timeInForce, 16, false) ||
            !ValidRequestText(command.order.orderRef, 128, false) ||
            // An order/preview deadline is an absolute, exclusive expiry.
            // Keep the direct codec boundary fail-closed as well as the
            // higher-level registry; zero/negative values otherwise survive
            // a valid HEX1 round trip and can bypass the intended TTL gate.
            command.expiresAtMs <= 0 ||
            !std::isfinite(command.referencePrice) ||
            !std::isfinite(command.contract.strike) ||
            !std::isfinite(command.order.totalQuantity) ||
            !std::isfinite(command.order.lmtPrice) ||
            !std::isfinite(command.order.auxPrice))
        {
            if (reason.empty()) reason = "EXECUTION_PROTOCOL_INVALID_PLACE";
            return false;
        }
        EncodeContext(command.context, body);
        AppendField(body, Instrument, command.instrument);
        AppendField(body, ExpiresAtMs, Number(command.expiresAtMs));
        AppendField(body, ReferencePrice, Number(command.referencePrice));
        AppendField(body, Symbol, command.contract.symbol);
        AppendField(body, SecType, command.contract.secType);
        AppendField(body, Exchange, command.contract.exchange);
        AppendField(body, PrimaryExchange, command.contract.primaryExchange);
        AppendField(body, Currency, command.contract.currency);
        AppendField(body, ContractMonth, command.contract.lastTradeDateOrContractMonth);
        AppendField(body, Right, command.contract.right);
        AppendField(body, Strike, Number(command.contract.strike));
        AppendField(body, Multiplier, command.contract.multiplier);
        AppendField(body, TradingClass, command.contract.tradingClass);
        AppendField(body, LocalSymbol, command.contract.localSymbol);
        AppendField(body, Action, command.order.action);
        AppendField(body, OrderType, command.order.orderType);
        AppendField(body, Quantity, Number(command.order.totalQuantity));
        AppendField(body, LimitPrice, Number(command.order.lmtPrice));
        AppendField(body, AuxPrice, Number(command.order.auxPrice));
        AppendField(body, OutsideRth, command.order.outsideRth ? "1" : "0");
        AppendField(body, TimeInForce, command.timeInForce);
        AppendField(body, OrderRef, command.order.orderRef);
        if ((request.operation == ExecutionServiceOperation::PlaceIbOrder &&
             !command.previewPermit.empty() &&
             !CanonicalSha256(command.previewPermit)) ||
            (request.operation == ExecutionServiceOperation::PreviewOrder &&
             !command.previewPermit.empty()))
        {
            reason = "EXECUTION_PROTOCOL_INVALID_PREVIEW_PERMIT";
            return false;
        }
        AppendField(body, PreviewPermit, command.previewPermit);
    }
    else if (request.operation == ExecutionServiceOperation::CancelIbOrder)
    {
        const IbCancelOrderCommand& command = request.cancel;
        if (!ValidateContext(command.context, reason) ||
            !ValidRequestText(command.instrument, 128, false) ||
            !ValidRequestText(command.side, 16, false) ||
            command.orderId < 0)
        {
            if (reason.empty()) reason = "EXECUTION_PROTOCOL_INVALID_CANCEL";
            return false;
        }
        EncodeContext(command.context, body);
        AppendField(body, OrderId, Number(command.orderId));
        AppendField(body, Instrument, command.instrument);
        AppendField(body, Side, command.side);
    }
    else if (request.operation == ExecutionServiceOperation::FlattenPosition ||
             request.operation ==
                 ExecutionServiceOperation::PreviewFlattenPosition)
    {
        const FlattenPositionCommand& command = request.flatten;
        if (!ValidateContext(command.context, reason) ||
            !ValidRequestText(command.instrument, 128, true) ||
            !ValidRequestText(command.contract.symbol, 128, false) ||
            !ValidRequestText(command.contract.secType, 32, false) ||
            !ValidRequestText(command.contract.exchange, 64, false) ||
            !ValidRequestText(command.contract.primaryExchange, 64, false) ||
            !ValidRequestText(command.contract.currency, 16, false) ||
            !ValidRequestText(command.contract.lastTradeDateOrContractMonth, 32, false) ||
            !ValidRequestText(command.contract.right, 8, false) ||
            !ValidRequestText(command.contract.multiplier, 32, false) ||
            !ValidRequestText(command.contract.tradingClass, 64, false) ||
            !ValidRequestText(command.contract.localSymbol, 128, false) ||
            !std::isfinite(command.contract.strike))
        {
            if (reason.empty()) reason = "EXECUTION_PROTOCOL_INVALID_FLATTEN";
            return false;
        }
        EncodeContext(command.context, body);
        AppendField(body, Instrument, command.instrument);
        AppendField(body, Symbol, command.contract.symbol);
        AppendField(body, SecType, command.contract.secType);
        AppendField(body, Exchange, command.contract.exchange);
        AppendField(body, PrimaryExchange, command.contract.primaryExchange);
        AppendField(body, Currency, command.contract.currency);
        AppendField(body, ContractMonth,
            command.contract.lastTradeDateOrContractMonth);
        AppendField(body, Right, command.contract.right);
        AppendField(body, Strike, Number(command.contract.strike));
        AppendField(body, Multiplier, command.contract.multiplier);
        AppendField(body, TradingClass, command.contract.tradingClass);
        AppendField(body, LocalSymbol, command.contract.localSymbol);
        if ((request.operation == ExecutionServiceOperation::FlattenPosition &&
             !command.previewPermit.empty() &&
             !CanonicalSha256(command.previewPermit)) ||
            (request.operation ==
                 ExecutionServiceOperation::PreviewFlattenPosition &&
             !command.previewPermit.empty()))
        {
            reason = "EXECUTION_PROTOCOL_INVALID_PREVIEW_PERMIT";
            return false;
        }
        AppendField(body, PreviewPermit, command.previewPermit);
    }
    else if (request.operation == ExecutionServiceOperation::QueryCommandStatus ||
             request.operation == ExecutionServiceOperation::RecoveryQueryCommandStatus ||
             request.operation == ExecutionServiceOperation::FenceSessionOwner ||
             request.operation == ExecutionServiceOperation::ReleaseSessionOwnerFence ||
             request.operation == ExecutionServiceOperation::ReconcileAuthoritativeState ||
             request.operation == ExecutionServiceOperation::RecoveryAuditOwner ||
             request.operation ==
                 ExecutionServiceOperation::TerminalizeRecoveryOwner)
    {
        if (!ValidateContext(request.control.context, reason)) return false;
        if ((request.operation == ExecutionServiceOperation::QueryCommandStatus ||
             request.operation == ExecutionServiceOperation::RecoveryQueryCommandStatus ||
             request.operation == ExecutionServiceOperation::TerminalizeRecoveryOwner) &&
            !ValidateTargetCommandId(request.control.targetCommandId, true))
        {
            reason = "EXECUTION_PROTOCOL_INVALID_CONTROL";
            return false;
        }
        EncodeContext(request.control.context, body);
        if (request.operation == ExecutionServiceOperation::QueryCommandStatus ||
            request.operation == ExecutionServiceOperation::RecoveryQueryCommandStatus ||
            request.operation ==
                ExecutionServiceOperation::TerminalizeRecoveryOwner)
            AppendField(body, TargetCommandId, request.control.targetCommandId);
        if (request.operation == ExecutionServiceOperation::RecoveryQueryCommandStatus ||
            request.operation == ExecutionServiceOperation::RecoveryAuditOwner ||
            request.operation ==
                ExecutionServiceOperation::TerminalizeRecoveryOwner)
            AppendField(body, RecoveryIngressFence,
                Number(request.control.recoveryIngressFence));
        if (request.operation ==
                ExecutionServiceOperation::TerminalizeRecoveryOwner)
        {
            if (!CanonicalSha256(
                    request.control.terminalPreliminaryReceiptSha256))
            {
                reason = "EXECUTION_PROTOCOL_INVALID_TERMINAL_BINDING";
                return false;
            }
            AppendField(body, TerminalPreliminaryReceiptSha256,
                request.control.terminalPreliminaryReceiptSha256);
        }
    }
    else if (request.operation == ExecutionServiceOperation::ReadAuthoritativeState)
    {
        if (!ValidateContext(request.read.context, reason) ||
            !CanonicalContextText(request.read.query, 64, true) ||
            !CanonicalContextText(request.read.instrument, 128, true))
        {
            reason = "EXECUTION_PROTOCOL_INVALID_READ";
            return false;
        }
        EncodeContext(request.read.context, body);
        AppendField(body, ReadQuery, request.read.query);
        AppendField(body, Instrument, request.read.instrument);
    }
    else if (request.operation != ExecutionServiceOperation::GetServiceIdentity)
    {
        reason = "EXECUTION_PROTOCOL_INVALID_OPERATION";
        return false;
    }
    if (body.size() > kMaximumExecutionBodyBytes)
    {
        body.clear();
        reason = "EXECUTION_PROTOCOL_REQUEST_TOO_LARGE";
        return false;
    }
    reason.clear();
    bodyGuard.Commit();
    return true;
}

bool ExecutionServiceProtocol::DecodeRequest(const std::string& body,
                                             ExecutionServiceRequest& request, std::string& reason)
{
    request = ExecutionServiceRequest();
    ResetOnFailure<ExecutionServiceRequest> requestGuard(request);
    unsigned int kind = 0;
    std::map<unsigned int, std::string> fields;
    if (!DecodeEnvelope(body, kind, fields, false, reason)) return false;
    std::set<unsigned int> expectedFields;
    if (kind == static_cast<unsigned int>(ExecutionServiceOperation::PlaceIbOrder) ||
        kind == static_cast<unsigned int>(ExecutionServiceOperation::PreviewOrder))
    {
        expectedFields = ContextFields();
        AddServiceIdentityFields(expectedFields);
        const unsigned int placeFields[] = {
            Instrument, ExpiresAtMs, ReferencePrice, Symbol, SecType, Exchange,
            PrimaryExchange, Currency, ContractMonth, Right, Strike, Multiplier,
            TradingClass, LocalSymbol, Action, OrderType, Quantity, LimitPrice,
            AuxPrice, OutsideRth, TimeInForce, OrderRef, PreviewPermit};
        expectedFields.insert(placeFields,
            placeFields + sizeof(placeFields) / sizeof(placeFields[0]));
    }
    else if (kind == static_cast<unsigned int>(ExecutionServiceOperation::CancelIbOrder))
    {
        expectedFields = ContextFields();
        AddServiceIdentityFields(expectedFields);
        expectedFields.insert(OrderId);
        expectedFields.insert(Instrument);
        expectedFields.insert(Side);
    }
    else if (kind ==
                 static_cast<unsigned int>(
                     ExecutionServiceOperation::FlattenPosition) ||
             kind ==
                 static_cast<unsigned int>(
                     ExecutionServiceOperation::PreviewFlattenPosition))
    {
        expectedFields = ContextFields();
        AddServiceIdentityFields(expectedFields);
        const unsigned int flattenFields[] = {
            Instrument, Symbol, SecType, Exchange, PrimaryExchange, Currency,
            ContractMonth, Right, Strike, Multiplier, TradingClass,
            LocalSymbol, PreviewPermit};
        expectedFields.insert(
            flattenFields,
            flattenFields +
                sizeof(flattenFields) / sizeof(flattenFields[0]));
    }
    else if (kind == static_cast<unsigned int>(ExecutionServiceOperation::QueryCommandStatus) ||
             kind == static_cast<unsigned int>(ExecutionServiceOperation::RecoveryQueryCommandStatus) ||
             kind == static_cast<unsigned int>(ExecutionServiceOperation::FenceSessionOwner) ||
             kind == static_cast<unsigned int>(ExecutionServiceOperation::ReleaseSessionOwnerFence) ||
             kind == static_cast<unsigned int>(ExecutionServiceOperation::ReconcileAuthoritativeState) ||
             kind == static_cast<unsigned int>(ExecutionServiceOperation::RecoveryAuditOwner) ||
             kind == static_cast<unsigned int>(
                 ExecutionServiceOperation::TerminalizeRecoveryOwner))
    {
        expectedFields = ContextFields();
        AddServiceIdentityFields(expectedFields);
        if (kind == static_cast<unsigned int>(ExecutionServiceOperation::QueryCommandStatus) ||
            kind == static_cast<unsigned int>(ExecutionServiceOperation::RecoveryQueryCommandStatus) ||
            kind == static_cast<unsigned int>(
                ExecutionServiceOperation::TerminalizeRecoveryOwner))
            expectedFields.insert(TargetCommandId);
        if (kind == static_cast<unsigned int>(ExecutionServiceOperation::RecoveryQueryCommandStatus) ||
            kind == static_cast<unsigned int>(ExecutionServiceOperation::RecoveryAuditOwner) ||
            kind == static_cast<unsigned int>(
                ExecutionServiceOperation::TerminalizeRecoveryOwner))
            expectedFields.insert(RecoveryIngressFence);
        if (kind == static_cast<unsigned int>(
                ExecutionServiceOperation::TerminalizeRecoveryOwner))
            expectedFields.insert(TerminalPreliminaryReceiptSha256);
    }
    else if (kind == static_cast<unsigned int>(ExecutionServiceOperation::ReadAuthoritativeState))
    {
        expectedFields = ContextFields();
        AddServiceIdentityFields(expectedFields);
        expectedFields.insert(ReadQuery);
        expectedFields.insert(Instrument);
    }
    else if (kind != static_cast<unsigned int>(ExecutionServiceOperation::GetServiceIdentity))
    {
        reason = "EXECUTION_PROTOCOL_INVALID_OPERATION";
        return false;
    }
    if (!HasExactFields(fields, expectedFields, reason)) return false;
    std::string expectedServiceFencingGeneration;
    if (kind != static_cast<unsigned int>(ExecutionServiceOperation::GetServiceIdentity) &&
        (!Require(fields, ExpectedServiceEpoch, request.expectedServiceEpoch, reason) ||
         !Require(fields, ExpectedServiceFencingGeneration,
             expectedServiceFencingGeneration, reason) ||
         !ParseUnsigned(expectedServiceFencingGeneration,
             request.expectedServiceFencingGeneration) ||
         !ValidateServiceEpoch(request.expectedServiceEpoch) ||
         request.expectedServiceFencingGeneration == 0))
    {
        if (reason.empty()) reason = "EXECUTION_PROTOCOL_INVALID_SERVICE_EPOCH";
        return false;
    }
    if (kind == static_cast<unsigned int>(ExecutionServiceOperation::PlaceIbOrder) ||
        kind == static_cast<unsigned int>(ExecutionServiceOperation::PreviewOrder))
    {
        request.operation = static_cast<ExecutionServiceOperation>(kind);
        IbPlaceOrderCommand& command = request.place;
        std::string expiresAt;
        std::string reference;
        std::string strike;
        std::string quantity;
        std::string limitPrice;
        std::string auxPrice;
        std::string outsideRth;
        if (!DecodeContext(fields, command.context, reason) ||
            !Require(fields, Instrument, command.instrument, reason) ||
            !Require(fields, ExpiresAtMs, expiresAt, reason) ||
            !Require(fields, ReferencePrice, reference, reason) ||
            !Require(fields, Symbol, command.contract.symbol, reason) ||
            !Require(fields, SecType, command.contract.secType, reason) ||
            !Require(fields, Exchange, command.contract.exchange, reason) ||
            !Require(fields, PrimaryExchange, command.contract.primaryExchange, reason) ||
            !Require(fields, Currency, command.contract.currency, reason) ||
            !Require(fields, ContractMonth, command.contract.lastTradeDateOrContractMonth, reason) ||
            !Require(fields, Right, command.contract.right, reason) ||
            !Require(fields, Strike, strike, reason) ||
            !Require(fields, Multiplier, command.contract.multiplier, reason) ||
            !Require(fields, TradingClass, command.contract.tradingClass, reason) ||
            !Require(fields, LocalSymbol, command.contract.localSymbol, reason) ||
            !Require(fields, Action, command.order.action, reason) ||
            !Require(fields, OrderType, command.order.orderType, reason) ||
            !Require(fields, Quantity, quantity, reason) ||
            !Require(fields, LimitPrice, limitPrice, reason) ||
            !Require(fields, AuxPrice, auxPrice, reason) ||
            !Require(fields, OutsideRth, outsideRth, reason) ||
            !Require(fields, TimeInForce, command.timeInForce, reason) ||
            !Require(fields, OrderRef, command.order.orderRef, reason) ||
            !Require(fields, PreviewPermit, command.previewPermit, reason) ||
            !ParseLongLong(expiresAt, command.expiresAtMs) ||
            command.expiresAtMs <= 0 ||
            !ParseDouble(reference, command.referencePrice) ||
            !ParseDouble(strike, command.contract.strike) ||
            !ParseDouble(quantity, command.order.totalQuantity) ||
            !ParseDouble(limitPrice, command.order.lmtPrice) ||
            !ParseDouble(auxPrice, command.order.auxPrice) ||
            (outsideRth != "0" && outsideRth != "1") ||
            !ValidRequestText(command.instrument, 128, true) ||
            !ValidRequestText(command.contract.symbol, 128, false) ||
            !ValidRequestText(command.contract.secType, 32, false) ||
            !ValidRequestText(command.contract.exchange, 64, false) ||
            !ValidRequestText(command.contract.primaryExchange, 64, false) ||
            !ValidRequestText(command.contract.currency, 16, false) ||
            !ValidRequestText(command.contract.lastTradeDateOrContractMonth, 32, false) ||
            !ValidRequestText(command.contract.right, 8, false) ||
            !ValidRequestText(command.contract.multiplier, 32, false) ||
            !ValidRequestText(command.contract.tradingClass, 64, false) ||
            !ValidRequestText(command.contract.localSymbol, 128, false) ||
            !ValidRequestText(command.order.action, 16, false) ||
            !ValidRequestText(command.order.orderType, 16, false) ||
            !ValidRequestText(command.timeInForce, 16, false) ||
            !ValidRequestText(command.order.orderRef, 128, false) ||
            command.previewPermit.size() > 80 ||
            (request.operation == ExecutionServiceOperation::PlaceIbOrder &&
             !command.previewPermit.empty() &&
             !CanonicalSha256(command.previewPermit)) ||
            (request.operation == ExecutionServiceOperation::PreviewOrder &&
             !command.previewPermit.empty()))
        {
            if (reason.empty()) reason = "EXECUTION_PROTOCOL_INVALID_PLACE";
            return false;
        }
        command.order.outsideRth = outsideRth == "1";
    }
    else if (kind == static_cast<unsigned int>(ExecutionServiceOperation::CancelIbOrder))
    {
        request.operation = ExecutionServiceOperation::CancelIbOrder;
        std::string orderId;
        long long parsedOrderId = -1;
        if (!DecodeContext(fields, request.cancel.context, reason) ||
            !Require(fields, OrderId, orderId, reason) ||
            !Require(fields, Instrument, request.cancel.instrument, reason) ||
            !Require(fields, Side, request.cancel.side, reason) ||
            !ParseLongLong(orderId, parsedOrderId) || parsedOrderId < 0 ||
            parsedOrderId > std::numeric_limits<long>::max() ||
            !ValidRequestText(request.cancel.instrument, 128, false) ||
            !ValidRequestText(request.cancel.side, 16, false))
        {
            if (reason.empty()) reason = "EXECUTION_PROTOCOL_INVALID_CANCEL";
            return false;
        }
        request.cancel.orderId = static_cast<long>(parsedOrderId);
    }
    else if (kind ==
                 static_cast<unsigned int>(
                     ExecutionServiceOperation::FlattenPosition) ||
             kind ==
                 static_cast<unsigned int>(
                     ExecutionServiceOperation::PreviewFlattenPosition))
    {
        request.operation = static_cast<ExecutionServiceOperation>(kind);
        std::string strike;
        if (!DecodeContext(fields, request.flatten.context, reason) ||
            !Require(fields, Instrument, request.flatten.instrument, reason) ||
            !Require(fields, Symbol, request.flatten.contract.symbol, reason) ||
            !Require(fields, SecType, request.flatten.contract.secType, reason) ||
            !Require(fields, Exchange, request.flatten.contract.exchange,
                     reason) ||
            !Require(fields, PrimaryExchange,
                     request.flatten.contract.primaryExchange, reason) ||
            !Require(fields, Currency, request.flatten.contract.currency,
                     reason) ||
            !Require(fields, ContractMonth,
                     request.flatten.contract.lastTradeDateOrContractMonth,
                     reason) ||
            !Require(fields, Right, request.flatten.contract.right, reason) ||
            !Require(fields, Strike, strike, reason) ||
            !Require(fields, Multiplier, request.flatten.contract.multiplier,
                     reason) ||
            !Require(fields, TradingClass,
                     request.flatten.contract.tradingClass, reason) ||
            !Require(fields, LocalSymbol,
                     request.flatten.contract.localSymbol, reason) ||
            !Require(fields, PreviewPermit,
                     request.flatten.previewPermit, reason) ||
            !ParseDouble(strike, request.flatten.contract.strike) ||
            !ValidRequestText(request.flatten.instrument, 128, true) ||
            !ValidRequestText(request.flatten.contract.symbol, 128, false) ||
            !ValidRequestText(request.flatten.contract.secType, 32, false) ||
            !ValidRequestText(request.flatten.contract.exchange, 64, false) ||
            !ValidRequestText(request.flatten.contract.primaryExchange, 64, false) ||
            !ValidRequestText(request.flatten.contract.currency, 16, false) ||
            !ValidRequestText(request.flatten.contract.lastTradeDateOrContractMonth, 32, false) ||
            !ValidRequestText(request.flatten.contract.right, 8, false) ||
            !ValidRequestText(request.flatten.contract.multiplier, 32, false) ||
            !ValidRequestText(request.flatten.contract.tradingClass, 64, false) ||
            !ValidRequestText(request.flatten.contract.localSymbol, 128, false) ||
            request.flatten.previewPermit.size() > 80 ||
            (request.operation == ExecutionServiceOperation::FlattenPosition &&
             !request.flatten.previewPermit.empty() &&
             !CanonicalSha256(request.flatten.previewPermit)) ||
            (request.operation ==
                 ExecutionServiceOperation::PreviewFlattenPosition &&
             !request.flatten.previewPermit.empty()))
        {
            if (reason.empty())
                reason = "EXECUTION_PROTOCOL_INVALID_FLATTEN";
            return false;
        }
    }
    else if (kind == static_cast<unsigned int>(ExecutionServiceOperation::QueryCommandStatus) ||
             kind == static_cast<unsigned int>(ExecutionServiceOperation::RecoveryQueryCommandStatus) ||
             kind == static_cast<unsigned int>(ExecutionServiceOperation::FenceSessionOwner) ||
             kind == static_cast<unsigned int>(ExecutionServiceOperation::ReleaseSessionOwnerFence) ||
             kind == static_cast<unsigned int>(ExecutionServiceOperation::ReconcileAuthoritativeState) ||
             kind == static_cast<unsigned int>(ExecutionServiceOperation::RecoveryAuditOwner) ||
             kind == static_cast<unsigned int>(
                 ExecutionServiceOperation::TerminalizeRecoveryOwner))
    {
        request.operation = static_cast<ExecutionServiceOperation>(kind);
        std::string ingressFence;
        if (!DecodeContext(fields, request.control.context, reason) ||
            ((request.operation == ExecutionServiceOperation::QueryCommandStatus ||
              request.operation == ExecutionServiceOperation::RecoveryQueryCommandStatus ||
              request.operation ==
                  ExecutionServiceOperation::TerminalizeRecoveryOwner) &&
             (!Require(fields, TargetCommandId, request.control.targetCommandId, reason) ||
              !ValidateTargetCommandId(request.control.targetCommandId, true))) ||
            ((request.operation == ExecutionServiceOperation::RecoveryQueryCommandStatus ||
              request.operation == ExecutionServiceOperation::RecoveryAuditOwner ||
              request.operation ==
                  ExecutionServiceOperation::TerminalizeRecoveryOwner) &&
             (!Require(fields, RecoveryIngressFence, ingressFence, reason) ||
              !ParseUnsigned(ingressFence,
                  request.control.recoveryIngressFence))) ||
            ((request.operation == ExecutionServiceOperation::RecoveryQueryCommandStatus ||
              request.operation ==
                  ExecutionServiceOperation::TerminalizeRecoveryOwner) &&
             request.control.recoveryIngressFence == 0) ||
            (request.operation ==
                 ExecutionServiceOperation::TerminalizeRecoveryOwner &&
             (!Require(fields, TerminalPreliminaryReceiptSha256,
                 request.control.terminalPreliminaryReceiptSha256, reason) ||
              !CanonicalSha256(
                  request.control.terminalPreliminaryReceiptSha256))))
        {
            if (reason.empty()) reason = "EXECUTION_PROTOCOL_INVALID_CONTROL";
            return false;
        }
    }
    else if (kind == static_cast<unsigned int>(ExecutionServiceOperation::GetServiceIdentity))
        request.operation = ExecutionServiceOperation::GetServiceIdentity;
    else if (kind == static_cast<unsigned int>(ExecutionServiceOperation::ReadAuthoritativeState))
    {
        request.operation = ExecutionServiceOperation::ReadAuthoritativeState;
        if (!DecodeContext(fields, request.read.context, reason) ||
            !Require(fields, ReadQuery, request.read.query, reason) ||
            !Require(fields, Instrument, request.read.instrument, reason) ||
            !CanonicalContextText(request.read.query, 64, true) ||
            !CanonicalContextText(request.read.instrument, 128, true))
        {
            if (reason.empty()) reason = "EXECUTION_PROTOCOL_INVALID_READ";
            return false;
        }
    }
    else
    {
        reason = "EXECUTION_PROTOCOL_INVALID_OPERATION";
        return false;
    }
    reason.clear();
    requestGuard.Commit();
    return true;
}

bool ExecutionServiceProtocol::EncodeResponse(const ExecutionCommandResult& response,
                                              std::string& body, std::string& reason)
{
    body.clear();
    ResetOnFailure<std::string> bodyGuard(body);
    if (static_cast<int>(response.status) <
            static_cast<int>(ExecutionCommandStatus::Accepted) ||
        static_cast<int>(response.status) >
            static_cast<int>(ExecutionCommandStatus::Uncertain) ||
        !ValidateTargetCommandId(response.commandId, false) ||
        !ValidUtf8Text(response.reasonCode, 128, false, true) ||
        // Authority details are returned to an Agent/client and must not
        // carry terminal/framing controls across the IPC boundary.  Keep the
        // documented 32 KiB bound while requiring printable UTF-8.
        !ValidUtf8Text(response.detail, 32768, false, true) ||
        !ValidateServiceEpoch(response.serviceEpoch) ||
        response.serviceFencingGeneration == 0)
    {
        body.clear();
        reason = "EXECUTION_PROTOCOL_INVALID_RESPONSE";
        return false;
    }
    body.assign(kMagic, sizeof(kMagic));
    AppendU16(body, ProtocolVersion());
    AppendU16(body, 0);
    AppendField(body, ResultStatus, Number(static_cast<int>(response.status)));
    AppendField(body, ResultCommandId, response.commandId);
    AppendField(body, ResultOrderId, Number(response.orderId));
    AppendField(body, ResultReasonCode, response.reasonCode);
    AppendField(body, ResultDetail, response.detail);
    AppendField(body, ResultServiceEpoch, response.serviceEpoch);
    AppendField(body, ResultServiceFencingGeneration,
        Number(response.serviceFencingGeneration));
    if (body.size() > kMaximumExecutionBodyBytes)
    {
        body.clear();
        reason = "EXECUTION_PROTOCOL_RESPONSE_TOO_LARGE";
        return false;
    }
    reason.clear();
    bodyGuard.Commit();
    return true;
}

bool ExecutionServiceProtocol::DecodeResponse(const std::string& body,
                                              ExecutionCommandResult& response, std::string& reason)
{
    response = ExecutionCommandResult();
    ResetOnFailure<ExecutionCommandResult> responseGuard(response);
    unsigned int kind = 0;
    std::map<unsigned int, std::string> fields;
    if (!DecodeEnvelope(body, kind, fields, true, reason) || kind != 0)
        return false;
    const std::set<unsigned int> expectedFields{
        ResultStatus, ResultCommandId, ResultOrderId, ResultReasonCode,
        ResultDetail, ResultServiceEpoch, ResultServiceFencingGeneration};
    if (!HasExactFields(fields, expectedFields, reason)) return false;
    std::string status;
    std::string orderId;
    std::string serviceFencingGeneration;
    long long parsedStatus = -1;
    long long parsedOrderId = -1;
    if (!Require(fields, ResultStatus, status, reason) ||
        !Require(fields, ResultCommandId, response.commandId, reason) ||
        !Require(fields, ResultOrderId, orderId, reason) ||
        !Require(fields, ResultReasonCode, response.reasonCode, reason) ||
        !Require(fields, ResultDetail, response.detail, reason) ||
        !Require(fields, ResultServiceEpoch, response.serviceEpoch, reason) ||
        !Require(fields, ResultServiceFencingGeneration,
            serviceFencingGeneration, reason) ||
        !ParseUnsigned(serviceFencingGeneration,
            response.serviceFencingGeneration) ||
        response.serviceEpoch.empty() || response.serviceEpoch.size() > 128 ||
        !ValidateServiceEpoch(response.serviceEpoch) ||
        !ValidateTargetCommandId(response.commandId, false) ||
        !ValidUtf8Text(response.reasonCode, 128, false, true) ||
        !ValidUtf8Text(response.detail, 32768, false, true) ||
        response.serviceFencingGeneration == 0 ||
        !ParseLongLong(status, parsedStatus) || parsedStatus < 0 || parsedStatus > 3 ||
        !ParseLongLong(orderId, parsedOrderId) || parsedOrderId < std::numeric_limits<long>::min() ||
        parsedOrderId > std::numeric_limits<long>::max())
    {
        if (reason.empty()) reason = "EXECUTION_PROTOCOL_INVALID_RESPONSE";
        return false;
    }
    response.status = static_cast<ExecutionCommandStatus>(parsedStatus);
    response.orderId = static_cast<long>(parsedOrderId);
    reason.clear();
    responseGuard.Commit();
    return true;
}

bool ExecutionServiceProtocol::EncodeControlResponse(const ExecutionControlResult& response,
                                                     std::string& body, std::string& reason)
{
    body.clear();
    ResetOnFailure<std::string> bodyGuard(body);
    if (static_cast<int>(response.status) <
            static_cast<int>(ExecutionCommandStatus::Accepted) ||
        static_cast<int>(response.status) >
            static_cast<int>(ExecutionCommandStatus::Uncertain) ||
        static_cast<int>(response.targetStatus) <
            static_cast<int>(ExecutionCommandStatus::Accepted) ||
        static_cast<int>(response.targetStatus) >
            static_cast<int>(ExecutionCommandStatus::Uncertain) ||
        !ValidateTargetCommandId(response.commandId, false) ||
        !ValidateTargetCommandId(response.targetCommandId, false) ||
        !ValidUtf8Text(response.reasonCode, 128, false, true) ||
        !ValidUtf8Text(response.detail, 32768, false, true) ||
        !ValidateServiceEpoch(response.serviceEpoch) ||
        response.serviceFencingGeneration == 0 ||
        !ValidUtf8Text(response.ownerAccount, 128, false, true) ||
        !ValidUtf8Text(response.ownerExecutionDomain, 128, false, true) ||
        !ValidUtf8Text(response.terminalizationServiceEpoch, 256, false, true) ||
        !IsCanonicalDecimal(response.brokerPositionQuantity) ||
        !IsCanonicalDecimal(response.brokerGrossAbsolutePosition) ||
        (!response.terminalLatchSha256.empty() &&
         !CanonicalSha256(response.terminalLatchSha256)) ||
        (response.terminalRuntimeVerified &&
         (response.status != ExecutionCommandStatus::Accepted ||
          response.terminalizationServiceEpoch.empty() ||
          response.terminalizationServiceEpoch.size() > 128 ||
          response.terminalizationServiceFencingGeneration == 0 ||
          response.terminalizationGeneration == 0 ||
          !CanonicalSha256(response.terminalLatchSha256) ||
          !response.terminalMutationGateClosed ||
          response.terminalBrokerTransportConnected ||
          !response.terminalBrokerEventIngressHalted ||
          !response.terminalBrokerCallbackQueueDrained ||
          response.terminalBrokerCallbacksInFlight != 0 ||
          response.terminalBrokerReconnectPermitted ||
          !response.terminalLatchDurable ||
          !response.terminalRuntimeLatchLoaded)))
    {
        reason = "EXECUTION_PROTOCOL_INVALID_TERMINAL_WITNESS";
        return false;
    }
    body.assign(kMagic, sizeof(kMagic));
    AppendU16(body, ProtocolVersion());
    AppendU16(body, 0);
    AppendField(body, ResultStatus, Number(static_cast<int>(response.status)));
    AppendField(body, ResultCommandId, response.commandId);
    AppendField(body, ResultOrderId, Number(response.orderId));
    AppendField(body, ResultReasonCode, response.reasonCode);
    AppendField(body, ResultDetail, response.detail);
    AppendField(body, ResultTargetCommandId, response.targetCommandId);
    AppendField(body, ResultTargetStatus, Number(static_cast<int>(response.targetStatus)));
    AppendField(body, ResultAffectedCount, Number(response.affectedCount));
    AppendField(body, ResultMutationBlocked, response.mutationBlocked ? "1" : "0");
    AppendField(body, ResultServiceEpoch, response.serviceEpoch);
    AppendField(body, ResultServiceFencingGeneration,
        Number(response.serviceFencingGeneration));
    AppendField(body, ResultOwnerAuditAuthoritative,
        response.ownerAuditAuthoritative ? "1" : "0");
    AppendField(body, ResultOwnerAuditComplete,
        response.ownerAuditComplete ? "1" : "0");
    AppendField(body, ResultOwnerActiveOrderCount,
        Number(response.ownerActiveOrderCount));
    AppendField(body, ResultOwnerUncertainCommandCount,
        Number(response.ownerUncertainCommandCount));
    AppendField(body, ResultBrokerConnectionEpoch,
        Number(response.brokerConnectionEpoch));
    AppendField(body, ResultBrokerActiveGeneration,
        Number(response.brokerActiveGeneration));
    AppendField(body, ResultBrokerTerminalGeneration,
        Number(response.brokerTerminalGeneration));
    AppendField(body, ResultOwnerAccount, response.ownerAccount);
    AppendField(body, ResultOwnerExecutionDomain,
        response.ownerExecutionDomain);
    AppendField(body, ResultBrokerRiskGeneration,
        Number(response.brokerRiskGeneration));
    AppendField(body, ResultBrokerAccountGeneration,
        Number(response.brokerAccountGeneration));
    AppendField(body, ResultBrokerPositionGeneration,
        Number(response.brokerPositionGeneration));
    AppendField(body, ResultBrokerFxCashGeneration,
        Number(response.brokerFxCashGeneration));
    AppendField(body, ResultBrokerExposureGeneration,
        Number(response.brokerExposureGeneration));
    AppendField(body, ResultBrokerTerminalExposureGeneration,
        Number(response.brokerTerminalExposureGeneration));
    AppendField(body, ResultBrokerRiskAbsorbedExposureGeneration,
        Number(response.brokerRiskAbsorbedExposureGeneration));
    AppendField(body, ResultBrokerGlobalActiveOrderCount,
        Number(response.brokerGlobalActiveOrderCount));
    AppendField(body, ResultBrokerPostFillRiskReconciliationPending,
        response.brokerPostFillRiskReconciliationPending ? "1" : "0");
    AppendField(body, ResultBrokerRecoveryAuditBarrierComplete,
        response.brokerRecoveryAuditBarrierComplete ? "1" : "0");
    AppendField(body, ResultBrokerRecoveryAuditNewConnectionEpochRequired,
        response.brokerRecoveryAuditNewConnectionEpochRequired ? "1" : "0");
    AppendField(body, ResultBrokerPositionQuantity,
        response.brokerPositionQuantity);
    AppendField(body, ResultBrokerGrossAbsolutePosition,
        response.brokerGrossAbsolutePosition);
    AppendField(body, ResultTerminalizationServiceEpoch,
        response.terminalizationServiceEpoch);
    AppendField(body, ResultTerminalizationServiceFencingGeneration,
        Number(response.terminalizationServiceFencingGeneration));
    AppendField(body, ResultTerminalizationGeneration,
        Number(response.terminalizationGeneration));
    AppendField(body, ResultTerminalLatchSha256,
        response.terminalLatchSha256);
    AppendField(body, ResultTerminalMutationGateClosed,
        response.terminalMutationGateClosed ? "1" : "0");
    AppendField(body, ResultTerminalBrokerTransportConnected,
        response.terminalBrokerTransportConnected ? "1" : "0");
    AppendField(body, ResultTerminalBrokerEventIngressHalted,
        response.terminalBrokerEventIngressHalted ? "1" : "0");
    AppendField(body, ResultTerminalBrokerCallbackQueueDrained,
        response.terminalBrokerCallbackQueueDrained ? "1" : "0");
    AppendField(body, ResultTerminalBrokerCallbacksInFlight,
        Number(response.terminalBrokerCallbacksInFlight));
    AppendField(body, ResultTerminalBrokerReconnectPermitted,
        response.terminalBrokerReconnectPermitted ? "1" : "0");
    AppendField(body, ResultTerminalLatchDurable,
        response.terminalLatchDurable ? "1" : "0");
    AppendField(body, ResultTerminalRuntimeLatchLoaded,
        response.terminalRuntimeLatchLoaded ? "1" : "0");
    AppendField(body, ResultTerminalRuntimeVerified,
        response.terminalRuntimeVerified ? "1" : "0");
    AppendField(body, ResultTerminalReplay,
        response.terminalReplay ? "1" : "0");
    if (body.size() > kMaximumExecutionBodyBytes)
    {
        body.clear();
        reason = "EXECUTION_PROTOCOL_CONTROL_RESPONSE_TOO_LARGE";
        return false;
    }
    reason.clear();
    bodyGuard.Commit();
    return true;
}

bool ExecutionServiceProtocol::DecodeControlResponse(const std::string& body,
                                                     ExecutionControlResult& response,
                                                     std::string& reason)
{
    response = ExecutionControlResult();
    ResetOnFailure<ExecutionControlResult> responseGuard(response);
    unsigned int kind = 0;
    std::map<unsigned int, std::string> fields;
    if (!DecodeEnvelope(body, kind, fields, true, reason) || kind != 0)
        return false;
    const std::set<unsigned int> expectedFields{
        ResultStatus, ResultCommandId, ResultOrderId, ResultReasonCode,
        ResultDetail, ResultTargetCommandId, ResultTargetStatus,
        ResultAffectedCount, ResultMutationBlocked, ResultServiceEpoch,
        ResultServiceFencingGeneration, ResultOwnerAuditAuthoritative,
        ResultOwnerAuditComplete, ResultOwnerActiveOrderCount,
        ResultOwnerUncertainCommandCount, ResultBrokerConnectionEpoch,
        ResultBrokerActiveGeneration, ResultBrokerTerminalGeneration,
        ResultOwnerAccount, ResultOwnerExecutionDomain,
        ResultBrokerRiskGeneration, ResultBrokerAccountGeneration,
        ResultBrokerPositionGeneration, ResultBrokerFxCashGeneration,
        ResultBrokerExposureGeneration,
        ResultBrokerTerminalExposureGeneration,
        ResultBrokerRiskAbsorbedExposureGeneration,
        ResultBrokerGlobalActiveOrderCount,
        ResultBrokerPostFillRiskReconciliationPending,
        ResultBrokerRecoveryAuditBarrierComplete,
        ResultBrokerRecoveryAuditNewConnectionEpochRequired,
        ResultBrokerPositionQuantity,
        ResultBrokerGrossAbsolutePosition,
        ResultTerminalizationServiceEpoch,
        ResultTerminalizationServiceFencingGeneration,
        ResultTerminalizationGeneration, ResultTerminalLatchSha256,
        ResultTerminalMutationGateClosed,
        ResultTerminalBrokerTransportConnected,
        ResultTerminalBrokerEventIngressHalted,
        ResultTerminalBrokerCallbackQueueDrained,
        ResultTerminalBrokerCallbacksInFlight,
        ResultTerminalBrokerReconnectPermitted,
        ResultTerminalLatchDurable, ResultTerminalRuntimeLatchLoaded,
        ResultTerminalRuntimeVerified, ResultTerminalReplay};
    if (!HasExactFields(fields, expectedFields, reason)) return false;
    std::string status;
    std::string targetStatus;
    std::string orderId;
    std::string affectedCount;
    std::string mutationBlocked;
    std::string serviceFencingGeneration;
    std::string ownerAuditAuthoritative;
    std::string ownerAuditComplete;
    std::string ownerActiveOrderCount;
    std::string ownerUncertainCommandCount;
    std::string brokerConnectionEpoch;
    std::string brokerActiveGeneration;
    std::string brokerTerminalGeneration;
    std::string brokerRiskGeneration;
    std::string brokerAccountGeneration;
    std::string brokerPositionGeneration;
    std::string brokerFxCashGeneration;
    std::string brokerExposureGeneration;
    std::string brokerTerminalExposureGeneration;
    std::string brokerRiskAbsorbedExposureGeneration;
    std::string brokerGlobalActiveOrderCount;
    std::string brokerPostFillRiskReconciliationPending;
    std::string brokerRecoveryAuditBarrierComplete;
    std::string brokerRecoveryAuditNewConnectionEpochRequired;
    std::string terminalizationServiceFencingGeneration;
    std::string terminalizationGeneration;
    std::string terminalMutationGateClosed;
    std::string terminalBrokerTransportConnected;
    std::string terminalBrokerEventIngressHalted;
    std::string terminalBrokerCallbackQueueDrained;
    std::string terminalBrokerCallbacksInFlight;
    std::string terminalBrokerReconnectPermitted;
    std::string terminalLatchDurable;
    std::string terminalRuntimeLatchLoaded;
    std::string terminalRuntimeVerified;
    std::string terminalReplay;
    long long parsedStatus = -1;
    long long parsedTargetStatus = -1;
    long long parsedOrderId = -1;
    std::uint64_t parsedAffectedCount = 0;
    if (!Require(fields, ResultStatus, status, reason) ||
        !Require(fields, ResultCommandId, response.commandId, reason) ||
        !Require(fields, ResultOrderId, orderId, reason) ||
        !Require(fields, ResultReasonCode, response.reasonCode, reason) ||
        !Require(fields, ResultDetail, response.detail, reason) ||
        !Require(fields, ResultTargetCommandId, response.targetCommandId, reason) ||
        !Require(fields, ResultTargetStatus, targetStatus, reason) ||
        !Require(fields, ResultAffectedCount, affectedCount, reason) ||
        !Require(fields, ResultMutationBlocked, mutationBlocked, reason) ||
        !Require(fields, ResultServiceEpoch, response.serviceEpoch, reason) ||
        !Require(fields, ResultServiceFencingGeneration,
            serviceFencingGeneration, reason) ||
        !Require(fields, ResultOwnerAuditAuthoritative,
            ownerAuditAuthoritative, reason) ||
        !Require(fields, ResultOwnerAuditComplete,
            ownerAuditComplete, reason) ||
        !Require(fields, ResultOwnerActiveOrderCount,
            ownerActiveOrderCount, reason) ||
        !Require(fields, ResultOwnerUncertainCommandCount,
            ownerUncertainCommandCount, reason) ||
        !Require(fields, ResultBrokerConnectionEpoch,
            brokerConnectionEpoch, reason) ||
        !Require(fields, ResultBrokerActiveGeneration,
            brokerActiveGeneration, reason) ||
        !Require(fields, ResultBrokerTerminalGeneration,
            brokerTerminalGeneration, reason) ||
        !Require(fields, ResultOwnerAccount, response.ownerAccount, reason) ||
        !Require(fields, ResultOwnerExecutionDomain,
            response.ownerExecutionDomain, reason) ||
        !Require(fields, ResultBrokerRiskGeneration,
            brokerRiskGeneration, reason) ||
        !Require(fields, ResultBrokerAccountGeneration,
            brokerAccountGeneration, reason) ||
        !Require(fields, ResultBrokerPositionGeneration,
            brokerPositionGeneration, reason) ||
        !Require(fields, ResultBrokerFxCashGeneration,
            brokerFxCashGeneration, reason) ||
        !Require(fields, ResultBrokerExposureGeneration,
            brokerExposureGeneration, reason) ||
        !Require(fields, ResultBrokerTerminalExposureGeneration,
            brokerTerminalExposureGeneration, reason) ||
        !Require(fields, ResultBrokerRiskAbsorbedExposureGeneration,
            brokerRiskAbsorbedExposureGeneration, reason) ||
        !Require(fields, ResultBrokerGlobalActiveOrderCount,
            brokerGlobalActiveOrderCount, reason) ||
        !Require(fields, ResultBrokerPostFillRiskReconciliationPending,
            brokerPostFillRiskReconciliationPending, reason) ||
        !Require(fields, ResultBrokerRecoveryAuditBarrierComplete,
            brokerRecoveryAuditBarrierComplete, reason) ||
        !Require(fields, ResultBrokerRecoveryAuditNewConnectionEpochRequired,
            brokerRecoveryAuditNewConnectionEpochRequired, reason) ||
        !Require(fields, ResultBrokerPositionQuantity,
            response.brokerPositionQuantity, reason) ||
        !Require(fields, ResultBrokerGrossAbsolutePosition,
            response.brokerGrossAbsolutePosition, reason) ||
        !Require(fields, ResultTerminalizationServiceEpoch,
            response.terminalizationServiceEpoch, reason) ||
        !Require(fields, ResultTerminalizationServiceFencingGeneration,
            terminalizationServiceFencingGeneration, reason) ||
        !Require(fields, ResultTerminalizationGeneration,
            terminalizationGeneration, reason) ||
        !Require(fields, ResultTerminalLatchSha256,
            response.terminalLatchSha256, reason) ||
        !Require(fields, ResultTerminalMutationGateClosed,
            terminalMutationGateClosed, reason) ||
        !Require(fields, ResultTerminalBrokerTransportConnected,
            terminalBrokerTransportConnected, reason) ||
        !Require(fields, ResultTerminalBrokerEventIngressHalted,
            terminalBrokerEventIngressHalted, reason) ||
        !Require(fields, ResultTerminalBrokerCallbackQueueDrained,
            terminalBrokerCallbackQueueDrained, reason) ||
        !Require(fields, ResultTerminalBrokerCallbacksInFlight,
            terminalBrokerCallbacksInFlight, reason) ||
        !Require(fields, ResultTerminalBrokerReconnectPermitted,
            terminalBrokerReconnectPermitted, reason) ||
        !Require(fields, ResultTerminalLatchDurable,
            terminalLatchDurable, reason) ||
        !Require(fields, ResultTerminalRuntimeLatchLoaded,
            terminalRuntimeLatchLoaded, reason) ||
        !Require(fields, ResultTerminalRuntimeVerified,
            terminalRuntimeVerified, reason) ||
        !Require(fields, ResultTerminalReplay,
            terminalReplay, reason) ||
        !ParseUnsigned(serviceFencingGeneration,
            response.serviceFencingGeneration) ||
        response.serviceEpoch.empty() || response.serviceEpoch.size() > 128 ||
        !ValidateServiceEpoch(response.serviceEpoch) ||
        !ValidateTargetCommandId(response.commandId, false) ||
        !ValidateTargetCommandId(response.targetCommandId, false) ||
        !ValidUtf8Text(response.reasonCode, 128, false, true) ||
        !ValidUtf8Text(response.detail, 32768, false, true) ||
        !ValidUtf8Text(response.ownerAccount, 128, false, true) ||
        !ValidUtf8Text(response.ownerExecutionDomain, 128, false, true) ||
        !ValidUtf8Text(response.terminalizationServiceEpoch, 256, false, true) ||
        response.serviceFencingGeneration == 0 ||
        !ParseLongLong(status, parsedStatus) || parsedStatus < 0 || parsedStatus > 3 ||
        !ParseLongLong(targetStatus, parsedTargetStatus) ||
        parsedTargetStatus < 0 || parsedTargetStatus > 3 ||
        !ParseLongLong(orderId, parsedOrderId) ||
        parsedOrderId < std::numeric_limits<long>::min() ||
        parsedOrderId > std::numeric_limits<long>::max() ||
        !ParseUnsigned(affectedCount, parsedAffectedCount) ||
        !ParseUnsigned(ownerActiveOrderCount,
            response.ownerActiveOrderCount) ||
        !ParseUnsigned(ownerUncertainCommandCount,
            response.ownerUncertainCommandCount) ||
        !ParseUnsigned(brokerConnectionEpoch,
            response.brokerConnectionEpoch) ||
        !ParseUnsigned(brokerActiveGeneration,
            response.brokerActiveGeneration) ||
        !ParseUnsigned(brokerTerminalGeneration,
            response.brokerTerminalGeneration) ||
        !ParseUnsigned(brokerRiskGeneration,
            response.brokerRiskGeneration) ||
        !ParseUnsigned(brokerAccountGeneration,
            response.brokerAccountGeneration) ||
        !ParseUnsigned(brokerPositionGeneration,
            response.brokerPositionGeneration) ||
        !ParseUnsigned(brokerFxCashGeneration,
            response.brokerFxCashGeneration) ||
        !ParseUnsigned(brokerExposureGeneration,
            response.brokerExposureGeneration) ||
        !ParseUnsigned(brokerTerminalExposureGeneration,
            response.brokerTerminalExposureGeneration) ||
        !ParseUnsigned(brokerRiskAbsorbedExposureGeneration,
            response.brokerRiskAbsorbedExposureGeneration) ||
        !ParseUnsigned(brokerGlobalActiveOrderCount,
            response.brokerGlobalActiveOrderCount) ||
        !ParseUnsigned(terminalizationServiceFencingGeneration,
            response.terminalizationServiceFencingGeneration) ||
        !ParseUnsigned(terminalizationGeneration,
            response.terminalizationGeneration) ||
        !ParseUnsigned(terminalBrokerCallbacksInFlight,
            response.terminalBrokerCallbacksInFlight) ||
        (mutationBlocked != "0" && mutationBlocked != "1") ||
        (ownerAuditAuthoritative != "0" &&
         ownerAuditAuthoritative != "1") ||
        (ownerAuditComplete != "0" && ownerAuditComplete != "1") ||
        (brokerPostFillRiskReconciliationPending != "0" &&
         brokerPostFillRiskReconciliationPending != "1") ||
        (brokerRecoveryAuditBarrierComplete != "0" &&
         brokerRecoveryAuditBarrierComplete != "1") ||
        (brokerRecoveryAuditNewConnectionEpochRequired != "0" &&
         brokerRecoveryAuditNewConnectionEpochRequired != "1") ||
        (terminalMutationGateClosed != "0" &&
         terminalMutationGateClosed != "1") ||
        (terminalBrokerTransportConnected != "0" &&
         terminalBrokerTransportConnected != "1") ||
        (terminalBrokerEventIngressHalted != "0" &&
         terminalBrokerEventIngressHalted != "1") ||
        (terminalBrokerCallbackQueueDrained != "0" &&
         terminalBrokerCallbackQueueDrained != "1") ||
        (terminalBrokerReconnectPermitted != "0" &&
         terminalBrokerReconnectPermitted != "1") ||
        (terminalLatchDurable != "0" && terminalLatchDurable != "1") ||
        (terminalRuntimeLatchLoaded != "0" &&
         terminalRuntimeLatchLoaded != "1") ||
        (terminalRuntimeVerified != "0" &&
         terminalRuntimeVerified != "1") ||
        (terminalReplay != "0" && terminalReplay != "1") ||
        !IsCanonicalDecimal(response.brokerPositionQuantity) ||
        !IsCanonicalDecimal(response.brokerGrossAbsolutePosition) ||
        (!response.terminalLatchSha256.empty() &&
         !CanonicalSha256(response.terminalLatchSha256)) ||
        response.ownerAccount.size() > 128 ||
        response.ownerExecutionDomain.size() > 128)
    {
        if (reason.empty()) reason = "EXECUTION_PROTOCOL_INVALID_CONTROL_RESPONSE";
        return false;
    }
    response.status = static_cast<ExecutionCommandStatus>(parsedStatus);
    response.targetStatus = static_cast<ExecutionCommandStatus>(parsedTargetStatus);
    response.orderId = static_cast<long>(parsedOrderId);
    response.affectedCount = parsedAffectedCount;
    response.mutationBlocked = mutationBlocked == "1";
    response.ownerAuditAuthoritative = ownerAuditAuthoritative == "1";
    response.ownerAuditComplete = ownerAuditComplete == "1";
    response.brokerPostFillRiskReconciliationPending =
        brokerPostFillRiskReconciliationPending == "1";
    response.brokerRecoveryAuditBarrierComplete =
        brokerRecoveryAuditBarrierComplete == "1";
    response.brokerRecoveryAuditNewConnectionEpochRequired =
        brokerRecoveryAuditNewConnectionEpochRequired == "1";
    response.terminalMutationGateClosed =
        terminalMutationGateClosed == "1";
    response.terminalBrokerTransportConnected =
        terminalBrokerTransportConnected == "1";
    response.terminalBrokerEventIngressHalted =
        terminalBrokerEventIngressHalted == "1";
    response.terminalBrokerCallbackQueueDrained =
        terminalBrokerCallbackQueueDrained == "1";
    response.terminalBrokerReconnectPermitted =
        terminalBrokerReconnectPermitted == "1";
    response.terminalLatchDurable = terminalLatchDurable == "1";
    response.terminalRuntimeLatchLoaded =
        terminalRuntimeLatchLoaded == "1";
    response.terminalRuntimeVerified = terminalRuntimeVerified == "1";
    response.terminalReplay = terminalReplay == "1";
    if (response.terminalRuntimeVerified &&
        (response.status != ExecutionCommandStatus::Accepted ||
         response.terminalizationServiceEpoch.empty() ||
         response.terminalizationServiceEpoch.size() > 128 ||
         response.terminalizationServiceFencingGeneration == 0 ||
         response.terminalizationGeneration == 0 ||
         !CanonicalSha256(response.terminalLatchSha256) ||
         !response.terminalMutationGateClosed ||
         response.terminalBrokerTransportConnected ||
         !response.terminalBrokerEventIngressHalted ||
         !response.terminalBrokerCallbackQueueDrained ||
         response.terminalBrokerCallbacksInFlight != 0 ||
         response.terminalBrokerReconnectPermitted ||
         !response.terminalLatchDurable ||
         !response.terminalRuntimeLatchLoaded))
    {
        reason = "EXECUTION_PROTOCOL_INVALID_TERMINAL_WITNESS";
        return false;
    }
    reason.clear();
    responseGuard.Commit();
    return true;
}
