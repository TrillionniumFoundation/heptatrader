#pragma once

#include "trading_tool_registry.h"

#include <cctype>
#include <cmath>
#include <cstdint>
#include <locale>
#include <set>
#include <sstream>
#include <string>

namespace TradingToolWireContractDetail
{

// The result envelope has to retain payload JSON as JSON (rather than quote
// it as a string), but the payload is supplied by callbacks and therefore
// cannot be appended blindly.  This small bounded parser is deliberately
// kept in the installed wire header so direct users of
// TradingToolWireContract::EncodeResultEnvelope get the same fail-closed
// behaviour as the Unix server.  It accepts the protocol's object/null
// payload shape, validates UTF-8 and escaped controls, rejects duplicate
// object keys, and bounds recursion/node/string work before any bytes are
// emitted into the surrounding envelope.
class PayloadJsonParser
{
public:
    explicit PayloadJsonParser(const std::string& input)
        : m_input(input), m_offset(0), m_nodes(0), m_decodedStringBytes(0)
    {
    }

    bool Parse()
    {
        if (m_input.empty() ||
            m_input.size() > TradingToolWireLimits::MaximumResultEnvelopeBytes())
            return false;
        SkipWhitespace();
        // Result payloads are object-shaped by contract.  ``null`` is the
        // only scalar admitted because it is the envelope's empty-payload
        // representation; arrays/scalars would be rejected by the typed
        // result decoder and must not be emitted here.
        if (m_input.compare(m_offset, 4, "null") == 0)
            m_offset += 4;
        else if (!ParseObject(1))
            return false;
        SkipWhitespace();
        return m_offset == m_input.size();
    }

private:
    static const std::size_t kMaximumDepth = 64u;
    static const std::size_t kMaximumNodes = 100000u;

    void SkipWhitespace()
    {
        while (m_offset < m_input.size())
        {
            const char c = m_input[m_offset];
            if (c != ' ' && c != '\t' && c != '\r' && c != '\n') break;
            ++m_offset;
        }
    }

    bool Consume(char expected)
    {
        if (m_offset >= m_input.size() || m_input[m_offset] != expected)
            return false;
        ++m_offset;
        return true;
    }

    static int HexDigit(unsigned char c)
    {
        if (c >= '0' && c <= '9') return static_cast<int>(c - '0');
        if (c >= 'a' && c <= 'f') return static_cast<int>(c - 'a' + 10);
        if (c >= 'A' && c <= 'F') return static_cast<int>(c - 'A' + 10);
        return -1;
    }

    bool ParseHexQuad(std::uint32_t& value)
    {
        if (m_input.size() - m_offset < 4u) return false;
        value = 0;
        for (unsigned int i = 0; i < 4u; ++i)
        {
            const int digit = HexDigit(
                static_cast<unsigned char>(m_input[m_offset++]));
            if (digit < 0) return false;
            value = (value << 4) | static_cast<std::uint32_t>(digit);
        }
        return true;
    }

    bool AppendCodePoint(std::uint32_t codePoint, std::string& value)
    {
        if (codePoint > 0x10ffffu ||
            (codePoint >= 0xd800u && codePoint <= 0xdfffu) ||
            codePoint < 0x20u ||
            (codePoint >= 0x7fu && codePoint <= 0x9fu))
            return false;
        char encoded[4];
        std::size_t length = 0;
        if (codePoint <= 0x7fu)
            encoded[length++] = static_cast<char>(codePoint);
        else if (codePoint <= 0x7ffu)
        {
            encoded[length++] = static_cast<char>(0xc0u | (codePoint >> 6));
            encoded[length++] = static_cast<char>(0x80u | (codePoint & 0x3fu));
        }
        else if (codePoint <= 0xffffu)
        {
            encoded[length++] = static_cast<char>(0xe0u | (codePoint >> 12));
            encoded[length++] = static_cast<char>(0x80u |
                ((codePoint >> 6) & 0x3fu));
            encoded[length++] = static_cast<char>(0x80u | (codePoint & 0x3fu));
        }
        else
        {
            encoded[length++] = static_cast<char>(0xf0u | (codePoint >> 18));
            encoded[length++] = static_cast<char>(0x80u |
                ((codePoint >> 12) & 0x3fu));
            encoded[length++] = static_cast<char>(0x80u |
                ((codePoint >> 6) & 0x3fu));
            encoded[length++] = static_cast<char>(0x80u | (codePoint & 0x3fu));
        }
        if (value.size() > TradingToolWireLimits::MaximumResultEnvelopeBytes() -
                length)
            return false;
        value.append(encoded, length);
        return true;
    }

    bool ParseRawUtf8(std::string& value)
    {
        if (m_offset >= m_input.size()) return false;
        const std::size_t start = m_offset;
        const unsigned char lead =
            static_cast<unsigned char>(m_input[m_offset]);
        std::size_t length = 0;
        std::uint32_t codePoint = 0;
        if (lead >= 0xc2u && lead <= 0xdfu)
        {
            length = 2u;
            codePoint = lead & 0x1fu;
        }
        else if (lead >= 0xe0u && lead <= 0xefu)
        {
            length = 3u;
            codePoint = lead & 0x0fu;
        }
        else if (lead >= 0xf0u && lead <= 0xf4u)
        {
            length = 4u;
            codePoint = lead & 0x07u;
        }
        else
            return false;
        if (m_input.size() - m_offset < length) return false;
        const unsigned char second =
            static_cast<unsigned char>(m_input[m_offset + 1u]);
        if ((lead == 0xe0u && second < 0xa0u) ||
            (lead == 0xedu && second >= 0xa0u) ||
            (lead == 0xf0u && second < 0x90u) ||
            (lead == 0xf4u && second > 0x8fu))
            return false;
        for (std::size_t i = 1u; i < length; ++i)
        {
            const unsigned char continuation =
                static_cast<unsigned char>(m_input[m_offset + i]);
            if (continuation < 0x80u || continuation > 0xbfu) return false;
            codePoint = (codePoint << 6) | (continuation & 0x3fu);
        }
        if ((length == 2u && codePoint < 0x80u) ||
            (length == 3u && codePoint < 0x800u) ||
            (length == 4u && codePoint < 0x10000u) ||
            codePoint > 0x10ffffu ||
            (codePoint >= 0xd800u && codePoint <= 0xdfffu) ||
            codePoint < 0x20u ||
            (codePoint >= 0x7fu && codePoint <= 0x9fu))
            return false;
        m_offset += length;
        value.append(m_input, start, length);
        return value.size() <= TradingToolWireLimits::MaximumResultEnvelopeBytes();
    }

    bool ParseString(std::string& value)
    {
        value.clear();
        if (!Consume('"')) return false;
        while (m_offset < m_input.size())
        {
            const unsigned char c =
                static_cast<unsigned char>(m_input[m_offset++]);
            if (c == '"')
            {
                if (value.size() > TradingToolWireLimits::MaximumResultEnvelopeBytes() ||
                    m_decodedStringBytes >
                        TradingToolWireLimits::MaximumResultEnvelopeBytes() -
                            value.size())
                    return false;
                m_decodedStringBytes += value.size();
                return true;
            }
            if (c < 0x20u || c == 0x7fu) return false;
            if (c >= 0x80u)
            {
                --m_offset;
                if (!ParseRawUtf8(value)) return false;
                continue;
            }
            if (c != '\\')
            {
                value.push_back(static_cast<char>(c));
                continue;
            }
            if (m_offset >= m_input.size()) return false;
            const char escaped = m_input[m_offset++];
            if (escaped == '"' || escaped == '\\' || escaped == '/')
                value.push_back(escaped);
            else if (escaped == 'b' || escaped == 'f' ||
                     escaped == 'n' || escaped == 'r' || escaped == 't')
            {
                // These escapes decode to C0 controls.  The typed decoder
                // rejects them for the same reason, so reject before raw
                // payload bytes can cross the boundary.
                return false;
            }
            else if (escaped == 'u')
            {
                std::uint32_t codePoint = 0;
                if (!ParseHexQuad(codePoint)) return false;
                if (codePoint >= 0xd800u && codePoint <= 0xdbffu)
                {
                    if (m_input.size() - m_offset < 2u ||
                        m_input[m_offset] != '\\' ||
                        m_input[m_offset + 1u] != 'u')
                        return false;
                    m_offset += 2u;
                    std::uint32_t low = 0;
                    if (!ParseHexQuad(low) || low < 0xdc00u || low > 0xdfffu)
                        return false;
                    codePoint = 0x10000u +
                        ((codePoint - 0xd800u) << 10) + (low - 0xdc00u);
                }
                else if (codePoint >= 0xdc00u && codePoint <= 0xdfffu)
                    return false;
                if (!AppendCodePoint(codePoint, value)) return false;
            }
            else
                return false;
            if (value.size() > TradingToolWireLimits::MaximumResultEnvelopeBytes())
                return false;
        }
        return false;
    }

    bool ParseFiniteNumber(const std::string& token)
    {
        std::istringstream input(token);
        input.imbue(std::locale::classic());
        input >> std::noskipws;
        double value = 0.0;
        input >> value;
        if (input.fail() || !input.eof() || !std::isfinite(value)) return false;
        if (value != 0.0) return true;
        const bool negative = !token.empty() && token[0] == '-';
        bool mantissaZero = true;
        for (std::size_t i = negative ? 1u : 0u; i < token.size(); ++i)
        {
            if (token[i] == 'e' || token[i] == 'E') break;
            if (token[i] == '.') continue;
            if (token[i] != '0')
            {
                mantissaZero = false;
                break;
            }
        }
        return !negative && mantissaZero;
    }

    bool ParseNumber()
    {
        const std::size_t start = m_offset;
        if (m_offset < m_input.size() && m_input[m_offset] == '-') ++m_offset;
        if (m_offset >= m_input.size()) return false;
        if (m_input[m_offset] == '0')
        {
            ++m_offset;
            if (m_offset < m_input.size() &&
                m_input[m_offset] >= '0' && m_input[m_offset] <= '9')
                return false;
        }
        else
        {
            if (m_input[m_offset] < '1' || m_input[m_offset] > '9') return false;
            while (m_offset < m_input.size() &&
                   m_input[m_offset] >= '0' && m_input[m_offset] <= '9')
                ++m_offset;
        }
        if (m_offset < m_input.size() && m_input[m_offset] == '.')
        {
            ++m_offset;
            const std::size_t fraction = m_offset;
            while (m_offset < m_input.size() &&
                   m_input[m_offset] >= '0' && m_input[m_offset] <= '9')
                ++m_offset;
            if (fraction == m_offset) return false;
        }
        if (m_offset < m_input.size() &&
            (m_input[m_offset] == 'e' || m_input[m_offset] == 'E'))
        {
            ++m_offset;
            if (m_offset < m_input.size() &&
                (m_input[m_offset] == '+' || m_input[m_offset] == '-'))
                ++m_offset;
            const std::size_t exponent = m_offset;
            while (m_offset < m_input.size() &&
                   m_input[m_offset] >= '0' && m_input[m_offset] <= '9')
                ++m_offset;
            if (exponent == m_offset) return false;
        }
        return ParseFiniteNumber(m_input.substr(start, m_offset - start));
    }

    bool ParseLiteral(const char* literal)
    {
        const std::size_t length = std::char_traits<char>::length(literal);
        if (m_input.compare(m_offset, length, literal) != 0) return false;
        m_offset += length;
        return true;
    }

    bool ParseValue(std::size_t depth)
    {
        if (depth > kMaximumDepth || m_offset >= m_input.size() ||
            m_nodes >= kMaximumNodes)
            return false;
        ++m_nodes;
        const char c = m_input[m_offset];
        if (c == '{') return ParseObject(depth);
        if (c == '[') return ParseArray(depth);
        if (c == '"')
        {
            std::string ignored;
            return ParseString(ignored);
        }
        if (c == 't') return ParseLiteral("true");
        if (c == 'f') return ParseLiteral("false");
        if (c == 'n') return ParseLiteral("null");
        return ParseNumber();
    }

    bool ParseObject(std::size_t depth)
    {
        if (depth > kMaximumDepth || !Consume('{')) return false;
        SkipWhitespace();
        if (Consume('}')) return true;
        std::set<std::string> keys;
        while (true)
        {
            std::string key;
            if (!ParseString(key) || !keys.insert(key).second) return false;
            SkipWhitespace();
            if (!Consume(':')) return false;
            SkipWhitespace();
            if (!ParseValue(depth + 1u)) return false;
            SkipWhitespace();
            if (Consume('}')) return true;
            if (!Consume(',')) return false;
            SkipWhitespace();
        }
    }

    bool ParseArray(std::size_t depth)
    {
        if (depth > kMaximumDepth || !Consume('[')) return false;
        SkipWhitespace();
        if (Consume(']')) return true;
        while (true)
        {
            if (!ParseValue(depth + 1u)) return false;
            SkipWhitespace();
            if (Consume(']')) return true;
            if (!Consume(',')) return false;
            SkipWhitespace();
        }
    }

    const std::string& m_input;
    std::size_t m_offset;
    std::size_t m_nodes;
    std::size_t m_decodedStringBytes;
};

} // namespace TradingToolWireContractDetail

// Pure wire-side validation shared by the privileged registry and the
// unprivileged native client.  Keep this header free of authority objects and
// out-of-line project symbols so the installed native client archive is a
// complete link closure rather than a hidden dependency on Execution core.
class TradingToolWireContract
{
public:
    static const char* StatusName(TradingToolCallStatus status)
    {
        switch (status)
        {
        case TradingToolCallStatus::Ok: return "ok";
        case TradingToolCallStatus::PermissionDenied: return "permission_denied";
        case TradingToolCallStatus::InvalidTool: return "invalid_tool";
        case TradingToolCallStatus::Rejected: return "rejected";
        case TradingToolCallStatus::Duplicate: return "duplicate";
        case TradingToolCallStatus::Uncertain: return "uncertain";
        case TradingToolCallStatus::Error: return "error";
        }
        return "unknown";
    }

    // This is the single authoritative result serializer used both by the
    // Unix protocol and by bounded compound tools before they report success.
    // Keeping the preflight and transport on the same encoder makes the
    // maximum-frame proof exact rather than an estimate of JSON overhead.
    static std::string EncodeResultEnvelope(const TradingToolResult& result)
    {
        const std::string encoded = EncodeResultEnvelopeBody(result);
        if (encoded.size() <= TradingToolWireLimits::MaximumResultEnvelopeBytes())
            return encoded;
        // This API predates a fallible return type, so an oversized direct
        // caller must still receive a valid bounded envelope.  Do not try to
        // truncate arbitrary JSON (which could invalidate the document or
        // change its meaning); return a fixed canonical error instead.
        return "{\"status\":\"error\",\"tool\":\"system.error\",\"reason_code\":\"RESULT_ENVELOPE_TOO_LARGE\",\"detail\":\"\",\"order_id\":-1,\"payload\":null}";
    }

    // Return the size of the unshortened canonical envelope.  Compound
    // authority responses use this preflight to reject an oversized result;
    // calling EncodeResultEnvelope(...).size() would observe the fixed error
    // fallback above and could accidentally turn an over-limit payload into a
    // successful response.
    static std::size_t EncodedResultEnvelopeSize(const TradingToolResult& result)
    {
        return EncodeResultEnvelopeBody(result).size();
    }

    static bool IsSafePayloadJson(const std::string& payloadJson)
    {
        if (payloadJson.empty()) return true;
        return TradingToolWireContractDetail::PayloadJsonParser(
            payloadJson).Parse();
    }

    static bool IsCanonicalToolName(const std::string& value)
    {
        if (value.size() < 3 || value.size() > 64) return false;
        bool segmentStart = true;
        bool sawSeparator = false;
        for (std::string::const_iterator it = value.begin();
             it != value.end(); ++it)
        {
            const unsigned char c = static_cast<unsigned char>(*it);
            if (segmentStart)
            {
                if (c < 'a' || c > 'z') return false;
                segmentStart = false;
            }
            else if (c == '.')
            {
                sawSeparator = true;
                segmentStart = true;
            }
            else if (!((c >= 'a' && c <= 'z') ||
                       (c >= '0' && c <= '9') || c == '_'))
                return false;
        }
        return sawSeparator && !segmentStart;
    }

    static bool IsCanonicalCommandId(const std::string& value)
    {
        if (value.size() < 8 || value.size() > 128) return false;
        bool sawAlphaNumeric = false;
        for (std::string::const_iterator it = value.begin();
             it != value.end(); ++it)
        {
            const unsigned char c = static_cast<unsigned char>(*it);
            const bool alphaNumeric =
                (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
                (c >= '0' && c <= '9');
            if (!alphaNumeric && c != '.' && c != '_' && c != ':' &&
                c != '-') return false;
            sawAlphaNumeric = sawAlphaNumeric || alphaNumeric;
        }
        return sawAlphaNumeric;
    }

    static bool ValidateCallSemantics(const TradingToolCall& call,
                                      std::string& reasonCode,
                                      std::string& detail)
    {
        reasonCode.clear();
        detail.clear();

        if (call.name != "execution.get_command_status" &&
            !call.targetCommandId.empty())
            return Reject("UNEXPECTED_TOOL_FIELD", "command_id",
                          reasonCode, detail);

        if (call.name == "execution.get_command_status")
        {
            if (call.targetCommandId.empty())
                return Reject("MISSING_REQUIRED_FIELD", "command_id",
                              reasonCode, detail);
            if (!IsCanonicalCommandId(call.targetCommandId))
                return Reject("INVALID_COMMAND_ID",
                              "command_id must be a bounded canonical identifier",
                              reasonCode, detail);
            if (HasFieldsOtherThanCommandId(call))
                return Reject("UNEXPECTED_TOOL_FIELD",
                              "execution.get_command_status accepts only command_id",
                              reasonCode, detail);
            return true;
        }

        if (call.name == "market.get_quote" ||
            call.name == "watch.get_snapshot" ||
            call.name == "risk.preview_flatten")
        {
            if (call.instrument.empty())
                return Reject("MISSING_REQUIRED_FIELD", "instrument", reasonCode, detail);
            if (!IsCanonicalInstrument(call.instrument))
                return Reject("INVALID_INSTRUMENT",
                              "instrument must be a bounded canonical identifier",
                              reasonCode, detail);
            if (((call.name == "market.get_quote" ||
                  call.name == "watch.get_snapshot") &&
                 HasFieldsOtherThanInstrument(call)) ||
                (call.name == "risk.preview_flatten" &&
                 HasFieldsOtherThanInstrument(call)))
                return Reject("UNEXPECTED_TOOL_FIELD",
                              "tool accepts only instrument",
                              reasonCode, detail);
            return true;
        }

        if (call.name == "decision.get_snapshot")
        {
            if (call.instrument.empty())
                return Reject("MISSING_REQUIRED_FIELD", "instrument",
                              reasonCode, detail);
            if (!IsCanonicalInstrument(call.instrument))
                return Reject("INVALID_INSTRUMENT",
                              "instrument must be a bounded canonical identifier",
                              reasonCode, detail);
            if (HasFieldsOtherThanInstrument(call))
                return Reject("UNEXPECTED_TOOL_FIELD",
                              "decision.get_snapshot accepts only instrument",
                              reasonCode, detail);
            return true;
        }

        if (call.name == "account.get_summary" ||
            call.name == "portfolio.list_positions" ||
            call.name == "orders.list" || call.name == "risk.get_limits" ||
            call.name == "system.get_health" || call.name == "system.tools.list")
        {
            if (!call.instrument.empty() || HasFieldsOtherThanInstrument(call))
                return Reject("UNEXPECTED_TOOL_FIELD", "tool accepts no input fields",
                              reasonCode, detail);
            return true;
        }

        if (call.name == "system.tools.describe")
        {
            if (call.targetToolName.empty())
                return Reject("MISSING_REQUIRED_FIELD", "tool_name", reasonCode, detail);
            if (!IsCanonicalToolName(call.targetToolName))
                return Reject("INVALID_TARGET_TOOL_NAME", "tool_name",
                              reasonCode, detail);
            if (!call.instrument.empty() || call.orderId != -1 ||
                HasContractFields(call.ibContract) || HasOrderFields(call.ibOrder) ||
                !call.timeInForce.empty() || call.referencePrice != 0.0 ||
                call.expiresAtMs != 0 || call.waitTimeoutMs != 0 ||
                call.afterEventSequence != 0 || !call.previewPermit.empty())
                return Reject("UNEXPECTED_TOOL_FIELD",
                              "system.tools.describe accepts only tool_name",
                              reasonCode, detail);
            return true;
        }

        if (call.name == "events.wait")
        {
            if (!call.targetToolName.empty() || !call.instrument.empty() ||
                call.orderId != -1 ||
                HasContractFields(call.ibContract) || HasOrderFields(call.ibOrder) ||
                !call.timeInForce.empty() || call.referencePrice != 0.0 ||
                call.expiresAtMs != 0 || !call.previewPermit.empty())
                return Reject("UNEXPECTED_TOOL_FIELD",
                              "events.wait accepts only after_sequence and timeout_ms",
                              reasonCode, detail);
            if (call.waitTimeoutMs < 0 || call.waitTimeoutMs > 30000)
                return Reject("INVALID_WAIT_TIMEOUT",
                              "timeout_ms must be between 0 and 30000",
                              reasonCode, detail);
            return true;
        }

        // Target-position intent is deliberately a different wire shape from
        // raw order placement.  `quantity` and `reference_price` are aliases
        // for the signed target position and maximum slippage bound; side,
        // order type, limit price and contract identity are all derived by
        // the authoritative registry.  Keep this validation here (rather
        // than relying on the registry's dispatch branch) so native and MCP
        // callers cannot smuggle raw-order fields into the intent path.
        if (call.name == "intent.preview_target_position" ||
            call.name == "intent.apply_target_position")
        {
            if (call.instrument.empty())
                return Reject("MISSING_REQUIRED_FIELD", "instrument",
                              reasonCode, detail);
            if (!IsCanonicalInstrument(call.instrument))
                return Reject("INVALID_INSTRUMENT",
                              "instrument must be a bounded canonical identifier",
                              reasonCode, detail);
            if (!call.targetToolName.empty() || call.orderId != -1 ||
                HasContractFields(call.ibContract) ||
                HasNonTargetOrderFields(call.ibOrder) ||
                !call.timeInForce.empty() || call.waitTimeoutMs != 0 ||
                call.afterEventSequence != 0)
                return Reject("UNEXPECTED_TOOL_FIELD",
                              "target-position intent accepts only instrument, quantity, reference_price, expires_at_ms and (for apply) preview_permit",
                              reasonCode, detail);
            if (!std::isfinite(call.ibOrder.totalQuantity))
                return Reject("INVALID_TARGET_POSITION",
                              "target position must be finite",
                              reasonCode, detail);
            if (call.ibOrder.totalQuantity == 0.0 &&
                std::signbit(call.ibOrder.totalQuantity))
                return Reject("INVALID_TARGET_POSITION",
                              "target position must use canonical zero",
                              reasonCode, detail);
            if (!std::isfinite(call.referencePrice) ||
                call.referencePrice < 0.0 || call.referencePrice > 1000.0)
                return Reject("INVALID_MAX_SLIPPAGE",
                              "max slippage must be finite and between 0 and 1000 bps",
                              reasonCode, detail);
            if (call.referencePrice == 0.0 &&
                std::signbit(call.referencePrice))
                return Reject("INVALID_MAX_SLIPPAGE",
                              "max slippage must use canonical zero",
                              reasonCode, detail);
            if (call.expiresAtMs <= 0)
                return Reject("INVALID_EXPIRY",
                              "expires_at_ms must be positive",
                              reasonCode, detail);
            if (call.name == "intent.preview_target_position")
            {
                if (!call.previewPermit.empty())
                    return Reject("UNEXPECTED_TOOL_FIELD", "preview_permit",
                                  reasonCode, detail);
            }
            else if (!IsCanonicalPreviewPermit(call.previewPermit))
            {
                return Reject("PREVIEW_PERMIT_INVALID", "preview_permit",
                              reasonCode, detail);
            }
            return true;
        }

        if (call.name == "trade.cancel_order")
        {
            if (call.orderId < 0)
                return Reject("INVALID_ORDER_ID", "order_id must be non-negative",
                              reasonCode, detail);
            if (!call.targetToolName.empty() || !call.instrument.empty() ||
                !call.previewPermit.empty() ||
                HasContractFields(call.ibContract) ||
                HasOrderFields(call.ibOrder) || !call.timeInForce.empty() ||
                call.referencePrice != 0.0 || call.expiresAtMs != 0 ||
                call.waitTimeoutMs != 0 || call.afterEventSequence != 0)
                return Reject("UNEXPECTED_TOOL_FIELD",
                              "trade.cancel_order accepts only order_id",
                              reasonCode, detail);
            return true;
        }

        if (call.name == "trade.flatten_position")
        {
            if (call.instrument.empty())
                return Reject("MISSING_REQUIRED_FIELD", "instrument", reasonCode, detail);
            if (!IsCanonicalInstrument(call.instrument))
                return Reject("INVALID_INSTRUMENT",
                              "instrument must be a bounded canonical identifier",
                              reasonCode, detail);
            if (!IsCanonicalPreviewPermit(call.previewPermit))
                return Reject("PREVIEW_PERMIT_INVALID", "preview_permit",
                              reasonCode, detail);
            if (HasFieldsOtherThanInstrumentContractAndPreviewPermit(call))
                return Reject("UNEXPECTED_TOOL_FIELD",
                              "trade.flatten_position accepts only instrument and preview_permit",
                              reasonCode, detail);
            return true;
        }

        if (call.name == "trade.place_order" || call.name == "risk.preview_order")
        {
            if (!call.targetToolName.empty() ||
                call.ibOrder.auxPrice != 0.0 ||
                !call.ibOrder.orderRef.empty())
                return Reject("UNEXPECTED_TOOL_FIELD",
                              "target_tool_name, aux_price and order_ref are not accepted",
                              reasonCode, detail);
            if (call.instrument.empty())
                return Reject("MISSING_REQUIRED_FIELD", "instrument", reasonCode, detail);
            if (!IsCanonicalInstrument(call.instrument))
                return Reject("INVALID_INSTRUMENT",
                              "instrument must be a bounded canonical identifier",
                              reasonCode, detail);
            if (call.orderId != -1 || call.waitTimeoutMs != 0 ||
                call.afterEventSequence != 0 || call.ibOrder.outsideRth)
                return Reject("UNEXPECTED_TOOL_FIELD",
                              "order_id, wait fields and outside_rth are not accepted",
                              reasonCode, detail);
            if (call.ibOrder.action != "BUY" && call.ibOrder.action != "SELL")
                return Reject("INVALID_SIDE", "side must be BUY or SELL",
                              reasonCode, detail);
            if (!std::isfinite(call.ibOrder.totalQuantity) ||
                call.ibOrder.totalQuantity <= 0.0)
                return Reject("INVALID_QUANTITY",
                              "quantity must be finite and greater than zero",
                              reasonCode, detail);
            if (call.ibOrder.orderType != "MKT" && call.ibOrder.orderType != "LMT")
                return Reject("INVALID_ORDER_TYPE",
                              "order_type must be MKT or LMT",
                              reasonCode, detail);
            if (call.timeInForce != "DAY")
                return Reject("INVALID_TIME_IN_FORCE", "tif must be DAY",
                              reasonCode, detail);
            if (call.ibOrder.orderType == "LMT")
            {
                if (!std::isfinite(call.ibOrder.lmtPrice) ||
                    call.ibOrder.lmtPrice <= 0.0)
                    return Reject("INVALID_LIMIT_PRICE",
                                  "LMT requires a finite positive limit_price",
                                  reasonCode, detail);
            }
            else if (call.ibOrder.lmtPrice != 0.0)
                return Reject("INVALID_LIMIT_PRICE",
                              "MKT must not include limit_price",
                              reasonCode, detail);
            if (!std::isfinite(call.referencePrice) || call.referencePrice < 0.0)
                return Reject("INVALID_REFERENCE_PRICE",
                              "reference_price must be finite and non-negative",
                              reasonCode, detail);
            if (call.referencePrice == 0.0 &&
                std::signbit(call.referencePrice))
                return Reject("INVALID_REFERENCE_PRICE",
                              "reference_price must use canonical zero",
                              reasonCode, detail);
            if (call.expiresAtMs <= 0)
                return Reject("INVALID_EXPIRY", "expires_at_ms must be positive",
                              reasonCode, detail);
            if (call.name == "trade.place_order" &&
                !IsCanonicalPreviewPermit(call.previewPermit))
                return Reject("PREVIEW_PERMIT_INVALID", "preview_permit",
                              reasonCode, detail);
            if (call.name == "risk.preview_order" && !call.previewPermit.empty())
                return Reject("UNEXPECTED_TOOL_FIELD", "preview_permit",
                              reasonCode, detail);
            return true;
        }

        // Unknown names are rejected by the authoritative registry lookup.
        return true;
    }

private:
    static std::string EncodeResultEnvelopeBody(const TradingToolResult& result)
    {
        std::ostringstream out;
        // Result envelopes are serialized onto the native/MCP boundary.
        // Keep numeric formatting independent of the process-global locale;
        // a comma-decimal locale must never produce non-JSON wire text.
        out.imbue(std::locale::classic());
        out << "{\"status\":\"" << StatusName(result.status)
            << "\",\"tool\":\"" << EscapeJson(result.toolName)
            << "\",\"reason_code\":\"" << EscapeJson(result.reasonCode)
            << "\",\"detail\":\"" << EscapeJson(result.detail)
            << "\",\"order_id\":" << result.orderId << ",\"payload\":";
        // A payload is the one envelope field that intentionally remains raw
        // JSON.  Validate it before appending so a callback cannot close the
        // payload object and inject sibling fields (or place malformed UTF-8
        // directly on the frame).  There is no failure return channel on this
        // serializer, so invalid payloads fail closed as JSON null; the Unix
        // server additionally rejects the original result and emits its
        // canonical RESULT_ENVELOPE_INVALID/UNCERTAIN response.
        const bool payloadSafe = !result.payloadJson.empty() &&
            IsSafePayloadJson(result.payloadJson);
        if (!payloadSafe)
            out << "null";
        else out << result.payloadJson;
        out << "}";
        return out.str();
    }

    static std::string EscapeJson(const std::string& value)
    {
        std::string escaped;
        escaped.reserve(value.size());
        // JSON escaping alone does not make arbitrary byte strings safe:
        // C1/DEL controls and malformed UTF-8 can survive as escaped or raw
        // bytes and be interpreted differently by downstream Agent clients.
        // Preserve valid printable UTF-8, but replace malformed/control
        // scalars with U+FFFD (an ordinary printable scalar) because this
        // helper intentionally has no failure return channel.
        for (std::size_t offset = 0; offset < value.size();)
        {
            const unsigned char first =
                static_cast<unsigned char>(value[offset]);
            if (first < 0x80u)
            {
                ++offset;
                if (first == '"') escaped += "\\\"";
                else if (first == '\\') escaped += "\\\\";
                // The typed result contract does not permit C0 controls,
                // including the JSON short escapes for LF/CR/TAB.  Emit the
                // printable replacement scalar for every such byte so the
                // direct encoder cannot produce a value that its decoder
                // would reject.
                else if (first < 0x20u || first == 0x7fu)
                    escaped += "\\ufffd";
                else escaped.push_back(static_cast<char>(first));
                continue;
            }

            std::size_t continuationCount = 0;
            if (first >= 0xc2u && first <= 0xdfu)
                continuationCount = 1;
            else if (first >= 0xe0u && first <= 0xefu)
                continuationCount = 2;
            else if (first >= 0xf0u && first <= 0xf4u)
                continuationCount = 3;
            else
            {
                escaped += "\\ufffd";
                ++offset;
                continue;
            }
            if (value.size() - offset <= continuationCount)
            {
                escaped += "\\ufffd";
                ++offset;
                continue;
            }
            const unsigned char second =
                static_cast<unsigned char>(value[offset + 1]);
            if ((first == 0xe0u && second < 0xa0u) ||
                (first == 0xedu && second >= 0xa0u) ||
                (first == 0xf0u && second < 0x90u) ||
                (first == 0xf4u && second > 0x8fu))
            {
                escaped += "\\ufffd";
                ++offset;
                continue;
            }
            std::uint32_t codepoint = first &
                (continuationCount == 1 ? 0x1fu :
                 continuationCount == 2 ? 0x0fu : 0x07u);
            bool valid = true;
            for (std::size_t i = 1; i <= continuationCount; ++i)
            {
                const unsigned char continuation =
                    static_cast<unsigned char>(value[offset + i]);
                if (continuation < 0x80u || continuation > 0xbfu)
                {
                    valid = false;
                    break;
                }
                codepoint = (codepoint << 6) | (continuation & 0x3fu);
            }
            if (!valid || codepoint > 0x10ffffu ||
                (codepoint >= 0xd800u && codepoint <= 0xdfffu) ||
                codepoint < 0x20u || codepoint == 0x7fu ||
                (codepoint >= 0x80u && codepoint <= 0x9fu))
            {
                escaped += "\\ufffd";
                ++offset;
                continue;
            }
            escaped.append(value, offset, continuationCount + 1u);
            offset += continuationCount + 1u;
        }
        return escaped;
    }

    static bool HasContractFields(const InstrumentRef& contract)
    {
        return !contract.symbol.empty() || !contract.secType.empty() ||
            !contract.exchange.empty() || !contract.primaryExchange.empty() ||
            !contract.currency.empty() ||
            !contract.lastTradeDateOrContractMonth.empty() ||
            !contract.right.empty() || contract.strike != 0.0 ||
            !contract.multiplier.empty() || !contract.tradingClass.empty() ||
            !contract.localSymbol.empty();
    }

    static bool HasOrderFields(const OrderIntent& order)
    {
        return !order.action.empty() || !order.orderType.empty() ||
            order.totalQuantity != 0.0 || order.lmtPrice != 0.0 ||
            order.auxPrice != 0.0 || order.outsideRth ||
            !order.orderRef.empty();
    }

    static bool HasNonTargetOrderFields(const OrderIntent& order)
    {
        return !order.action.empty() || !order.orderType.empty() ||
            order.lmtPrice != 0.0 || order.auxPrice != 0.0 ||
            order.outsideRth || !order.orderRef.empty();
    }

    static bool IsCanonicalPreviewPermit(const std::string& permit)
    {
        if (permit.size() != 71 || permit.compare(0, 7, "sha256:") != 0)
            return false;
        for (std::size_t i = 7; i < permit.size(); ++i)
        {
            const unsigned char c = static_cast<unsigned char>(permit[i]);
            if (!((c >= '0' && c <= '9') ||
                  (c >= 'a' && c <= 'f')))
                return false;
        }
        return true;
    }

    static bool HasFieldsOtherThanInstrument(const TradingToolCall& call)
    {
        return !call.targetToolName.empty() || call.orderId != -1 ||
            HasContractFields(call.ibContract) || HasOrderFields(call.ibOrder) ||
            !call.timeInForce.empty() || call.referencePrice != 0.0 ||
            call.expiresAtMs != 0 || call.waitTimeoutMs != 0 ||
            call.afterEventSequence != 0 || !call.previewPermit.empty();
    }

    static bool HasFieldsOtherThanInstrumentAndContract(
        const TradingToolCall& call)
    {
        return !call.targetToolName.empty() || call.orderId != -1 ||
            HasOrderFields(call.ibOrder) ||
            !call.timeInForce.empty() || call.referencePrice != 0.0 ||
            call.expiresAtMs != 0 || call.waitTimeoutMs != 0 ||
            call.afterEventSequence != 0 || !call.previewPermit.empty();
    }

    static bool HasFieldsOtherThanInstrumentContractAndPreviewPermit(
        const TradingToolCall& call)
    {
        return !call.targetToolName.empty() || call.orderId != -1 ||
            HasOrderFields(call.ibOrder) || !call.timeInForce.empty() ||
            call.referencePrice != 0.0 || call.expiresAtMs != 0 ||
            call.waitTimeoutMs != 0 || call.afterEventSequence != 0;
    }

    static bool HasFieldsOtherThanCommandId(const TradingToolCall& call)
    {
        return !call.targetToolName.empty() || !call.instrument.empty() ||
            call.orderId != -1 || HasContractFields(call.ibContract) ||
            HasOrderFields(call.ibOrder) || !call.timeInForce.empty() ||
            call.referencePrice != 0.0 || call.expiresAtMs != 0 ||
            call.waitTimeoutMs != 0 || call.afterEventSequence != 0 ||
            !call.previewPermit.empty();
    }

    static bool Reject(const char* code,
                       const char* field,
                       std::string& reasonCode,
                       std::string& detail)
    {
        reasonCode = code;
        detail = field;
        return false;
    }

    static bool IsCanonicalInstrument(const std::string& instrument)
    {
        if (instrument.empty() || instrument.size() > 128) return false;
        // Instrument keys are ASCII canonical identifiers. Separators are
        // allowed inside a key (for example EUR.USD or OPT:...:1.5), but an
        // empty leading/trailing segment or repeated separator would make
        // distinct textual forms alias in allowlists and snapshot maps.
        bool previousSeparator = false;
        bool sawAlphaNumeric = false;
        for (std::string::const_iterator it = instrument.begin();
             it != instrument.end(); ++it)
        {
            const unsigned char c = static_cast<unsigned char>(*it);
            const bool alphaNumeric =
                (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
                (c >= '0' && c <= '9');
            if (alphaNumeric)
            {
                sawAlphaNumeric = true;
                previousSeparator = false;
                continue;
            }
            const bool separator = c == '.' || c == '-' || c == '_' ||
                c == '/' || c == ':';
            if (!separator || previousSeparator || it == instrument.begin() ||
                it + 1 == instrument.end())
                return false;
            previousSeparator = true;
        }
        return sawAlphaNumeric && !previousSeparator;
    }
};
