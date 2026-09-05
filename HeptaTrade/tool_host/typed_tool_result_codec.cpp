#include "typed_tool_protocol.h"
#include "../tools/trading_tool_wire_contract.h"

#include <cerrno>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <locale>
#include <set>
#include <sstream>

namespace {

// Payload JSON is opaque to the typed result codec, but it still crosses an
// IPC boundary and is parsed by downstream clients.  Validate every numeric
// token as a finite classic-locale double before retaining its lexical form;
// otherwise values such as 1e999999 would pass the structural parser and
// fail later (or turn into an implementation-defined infinity).
bool ParseFiniteNumber(const std::string& token)
{
    std::istringstream input(token);
    input.imbue(std::locale::classic());
    input >> std::noskipws;
    double value = 0.0;
    input >> value;
    if (input.fail() || !input.eof() || !std::isfinite(value)) return false;
    // Never admit signed zero.  Also reject a non-zero mantissa that
    // underflowed to zero during conversion; otherwise two distinct payload
    // numbers would become indistinguishable to downstream consumers.  Keep
    // positive spellings such as 0.0 valid JSON (they are finite and carry no
    // authority-sensitive sign).
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

const std::size_t kMaximumResultNodes = 100000;
const std::size_t kMaximumDecodedStringBytes =
    TradingToolWireLimits::MaximumResultEnvelopeBytes();

// Envelope text is forwarded across the Agent IPC boundary and is consumed
// by terminals/loggers that treat C0/C1 controls and DEL as framing or
// formatting bytes.  ParseString already enforces UTF-8 for raw input, but a
// JSON escape (for example ``\u007f`` or ``\u0085``) can decode to the same
// controls after parsing.  Revalidate the decoded top-level strings so those
// aliases cannot bypass the boundary contract.
bool IsSafeEnvelopeText(const std::string& value)
{
    if (value.find('\0') != std::string::npos) return false;
    std::size_t offset = 0;
    while (offset < value.size())
    {
        const unsigned char first =
            static_cast<unsigned char>(value[offset]);
        std::uint32_t codePoint = 0;
        std::size_t continuationCount = 0;
        if (first <= 0x7fu)
        {
            codePoint = first;
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
            if (value.size() - offset <= continuationCount) return false;
            const unsigned char second =
                static_cast<unsigned char>(value[offset + 1]);
            if ((first == 0xe0u && second < 0xa0u) ||
                (first == 0xedu && second >= 0xa0u) ||
                (first == 0xf0u && second < 0x90u) ||
                (first == 0xf4u && second > 0x8fu))
                return false;
            codePoint = first & (continuationCount == 1 ? 0x1fu :
                                 continuationCount == 2 ? 0x0fu : 0x07u);
            for (std::size_t i = 1; i <= continuationCount; ++i)
            {
                const unsigned char continuation =
                    static_cast<unsigned char>(value[offset + i]);
                if (continuation < 0x80u || continuation > 0xbfu)
                    return false;
                codePoint = (codePoint << 6) | (continuation & 0x3fu);
            }
            if ((continuationCount == 1 && codePoint < 0x80u) ||
                (continuationCount == 2 && codePoint < 0x800u) ||
                (continuationCount == 3 && codePoint < 0x10000u) ||
                (codePoint >= 0xd800u && codePoint <= 0xdfffu) ||
                codePoint > 0x10ffffu)
                return false;
            offset += continuationCount + 1u;
        }
        if (codePoint < 0x20u ||
            (codePoint >= 0x7fu && codePoint <= 0x9fu))
            return false;
    }
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

class ResultJsonParser
{
public:
    explicit ResultJsonParser(const std::string& json)
        : m_json(json), m_offset(0), m_nodes(0), m_decodedStringBytes(0)
    {
    }

    bool ParseEnvelope(TypedToolResultEnvelope& result)
    {
        if (m_json.empty() ||
            m_json.size() >
                TradingToolWireLimits::MaximumResultEnvelopeBytes())
            return false;
        SkipWhitespace();
        if (!Consume('{')) return false;
        SkipWhitespace();

        std::set<std::string> seen;
        if (Consume('}')) return false;
        while (true)
        {
            std::string key;
            if (!ParseString(key) || !seen.insert(key).second) return false;
            SkipWhitespace();
            if (!Consume(':')) return false;
            SkipWhitespace();

            if (key == "status")
            {
                if (!ParseString(result.status)) return false;
            }
            else if (key == "tool")
            {
                if (!ParseString(result.toolName)) return false;
            }
            else if (key == "reason_code")
            {
                if (!ParseString(result.reasonCode)) return false;
            }
            else if (key == "detail")
            {
                if (!ParseString(result.detail)) return false;
            }
            else if (key == "order_id")
            {
                if (!ParseInteger(result.orderId) || result.orderId < -1) return false;
            }
            else if (key == "payload")
            {
                const std::size_t start = m_offset;
                if (m_json.compare(m_offset, 4, "null") == 0)
                {
                    m_offset += 4;
                }
                else if (!ParseObject(1))
                {
                    return false;
                }
                result.payloadJson = m_json.substr(start, m_offset - start);
            }
            else
            {
                return false;
            }

            SkipWhitespace();
            if (Consume('}')) break;
            if (!Consume(',')) return false;
            SkipWhitespace();
        }

        SkipWhitespace();
        if (m_offset != m_json.size() || seen.size() != 6 ||
            seen.count("status") != 1 || seen.count("tool") != 1 ||
            seen.count("reason_code") != 1 || seen.count("detail") != 1 ||
            seen.count("order_id") != 1 || seen.count("payload") != 1)
            return false;
        if (result.status.empty() || result.status.size() > 32 ||
            !TradingToolWireContract::IsCanonicalToolName(result.toolName) ||
            result.reasonCode.size() > 128 || result.detail.size() > 65536 ||
            !IsSafeEnvelopeText(result.status) ||
            !IsSafeEnvelopeText(result.toolName) ||
            !IsSafeEnvelopeText(result.reasonCode) ||
            !IsSafeEnvelopeText(result.detail))
            return false;
        return true;
    }

private:
    void SkipWhitespace()
    {
        while (m_offset < m_json.size())
        {
            const char c = m_json[m_offset];
            if (c != ' ' && c != '\t' && c != '\r' && c != '\n') break;
            ++m_offset;
        }
    }

    bool Consume(char expected)
    {
        if (m_offset >= m_json.size() || m_json[m_offset] != expected) return false;
        ++m_offset;
        return true;
    }

    bool ParseHexQuad(unsigned int& value)
    {
        if (m_offset > m_json.size() || m_json.size() - m_offset < 4)
            return false;
        value = 0;
        for (unsigned int i = 0; i < 4; ++i)
        {
            const unsigned char c = static_cast<unsigned char>(m_json[m_offset++]);
            value <<= 4;
            if (c >= '0' && c <= '9') value += c - '0';
            else if (c >= 'a' && c <= 'f') value += c - 'a' + 10;
            else if (c >= 'A' && c <= 'F') value += c - 'A' + 10;
            else return false;
        }
        return true;
    }

    static bool AppendCodePoint(unsigned int codePoint, std::string& value)
    {
        // Result payload strings are still transported over the Agent wire.
        // Reject C0/C1 controls and DEL at decode time, including values
        // represented by JSON ``\u`` escapes.  Checking only raw bytes (or
        // only the top-level envelope fields) would let a nested payload
        // string smuggle framing/log controls through the opaque payload.
        if (codePoint < 0x20u ||
            (codePoint >= 0x7fu && codePoint <= 0x9fu))
            return false;
        if (codePoint <= 0x7f)
        {
            value.push_back(static_cast<char>(codePoint));
        }
        else if (codePoint <= 0x7ff)
        {
            value.push_back(static_cast<char>(0xc0 | (codePoint >> 6)));
            value.push_back(static_cast<char>(0x80 | (codePoint & 0x3f)));
        }
        else if (codePoint <= 0xffff)
        {
            value.push_back(static_cast<char>(0xe0 | (codePoint >> 12)));
            value.push_back(static_cast<char>(0x80 | ((codePoint >> 6) & 0x3f)));
            value.push_back(static_cast<char>(0x80 | (codePoint & 0x3f)));
        }
        else
        {
            value.push_back(static_cast<char>(0xf0 | (codePoint >> 18)));
            value.push_back(static_cast<char>(0x80 | ((codePoint >> 12) & 0x3f)));
            value.push_back(static_cast<char>(0x80 | ((codePoint >> 6) & 0x3f)));
            value.push_back(static_cast<char>(0x80 | (codePoint & 0x3f)));
        }
        return true;
    }

    bool ParseRawUtf8(std::string& value)
    {
        const std::size_t start = m_offset;
        const unsigned char lead = static_cast<unsigned char>(m_json[m_offset]);
        unsigned int length = 0;
        unsigned int codePoint = 0;
        if (lead >= 0xc2 && lead <= 0xdf)
        {
            length = 2;
            codePoint = lead & 0x1f;
        }
        else if (lead >= 0xe0 && lead <= 0xef)
        {
            length = 3;
            codePoint = lead & 0x0f;
        }
        else if (lead >= 0xf0 && lead <= 0xf4)
        {
            length = 4;
            codePoint = lead & 0x07;
        }
        else
        {
            return false;
        }
        if (m_offset > m_json.size() ||
            static_cast<std::size_t>(length) > m_json.size() - m_offset)
            return false;
        for (unsigned int i = 1; i < length; ++i)
        {
            const unsigned char next =
                static_cast<unsigned char>(m_json[m_offset + i]);
            if ((next & 0xc0) != 0x80) return false;
            codePoint = (codePoint << 6) | (next & 0x3f);
        }
        if ((length == 2 && codePoint < 0x80) ||
            (length == 3 && codePoint < 0x800) ||
            (length == 4 && codePoint < 0x10000) ||
            (codePoint >= 0xd800 && codePoint <= 0xdfff) ||
            codePoint > 0x10ffff)
            return false;
        if (codePoint < 0x20u ||
            (codePoint >= 0x7fu && codePoint <= 0x9fu))
            return false;
        m_offset += length;
        value.append(m_json, start, length);
        return true;
    }

    bool ParseString(std::string& value)
    {
        value.clear();
        if (!Consume('"')) return false;
        while (m_offset < m_json.size())
        {
            const unsigned char c = static_cast<unsigned char>(m_json[m_offset++]);
            if (c == '"')
            {
                if (value.size() > kMaximumDecodedStringBytes ||
                    m_decodedStringBytes >
                        kMaximumDecodedStringBytes - value.size())
                    return false;
                m_decodedStringBytes += value.size();
                return true;
            }
            if (c < 0x20 || c == 0x7f) return false;
            if (c >= 0x80)
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
            if (m_offset >= m_json.size()) return false;
            const char escaped = m_json[m_offset++];
            if (escaped == '"' || escaped == '\\' || escaped == '/')
                value.push_back(escaped);
            else if (escaped == 'b' || escaped == 'f' || escaped == 'n' ||
                     escaped == 'r' || escaped == 't')
            {
                // The short JSON escapes decode to C0 controls.  They are
                // intentionally not admitted on this IPC boundary; callers
                // can carry structured line breaks in a dedicated payload
                // field if the contract permits them.
                return false;
            }
            else if (escaped == 'u')
            {
                unsigned int codePoint = 0;
                if (!ParseHexQuad(codePoint)) return false;
                if (codePoint >= 0xd800 && codePoint <= 0xdbff)
                {
                    if (m_offset > m_json.size() || m_json.size() - m_offset < 2 ||
                        m_json[m_offset] != '\\' || m_json[m_offset + 1] != 'u')
                        return false;
                    m_offset += 2;
                    unsigned int low = 0;
                    if (!ParseHexQuad(low) || low < 0xdc00 || low > 0xdfff)
                        return false;
                    codePoint = 0x10000 +
                        ((codePoint - 0xd800) << 10) + (low - 0xdc00);
                }
                else if (codePoint >= 0xdc00 && codePoint <= 0xdfff)
                {
                    return false;
                }
                if (!AppendCodePoint(codePoint, value)) return false;
            }
            else
            {
                return false;
            }
        }
        return false;
    }

    bool ParseInteger(long& value)
    {
        const std::size_t start = m_offset;
        const bool negative =
            m_offset < m_json.size() && m_json[m_offset] == '-';
        if (negative) ++m_offset;
        if (m_offset >= m_json.size()) return false;
        if (m_json[m_offset] == '0')
        {
            ++m_offset;
            if (m_offset < m_json.size() &&
                m_json[m_offset] >= '0' && m_json[m_offset] <= '9')
                return false;
        }
        else
        {
            if (m_json[m_offset] < '1' || m_json[m_offset] > '9') return false;
            while (m_offset < m_json.size() &&
                   m_json[m_offset] >= '0' && m_json[m_offset] <= '9')
                ++m_offset;
        }
        const std::string token = m_json.substr(start, m_offset - start);
        // Keep integer result fields canonical too.  ``strtol`` maps -0 to
        // the same value as 0, but accepting both spellings would let a peer
        // create distinct wire envelopes for one order-id identity (and
        // diverge from the Python bridge validator).
        if (negative && token == "-0") return false;
        return ParseLong(token, value);
    }

    bool ParseNumber()
    {
        const std::size_t start = m_offset;
        if (m_offset < m_json.size() && m_json[m_offset] == '-') ++m_offset;
        if (m_offset >= m_json.size()) return false;
        if (m_json[m_offset] == '0')
        {
            ++m_offset;
            if (m_offset < m_json.size() &&
                m_json[m_offset] >= '0' && m_json[m_offset] <= '9')
                return false;
        }
        else
        {
            if (m_json[m_offset] < '1' || m_json[m_offset] > '9') return false;
            while (m_offset < m_json.size() &&
                   m_json[m_offset] >= '0' && m_json[m_offset] <= '9')
                ++m_offset;
        }
        if (m_offset < m_json.size() && m_json[m_offset] == '.')
        {
            ++m_offset;
            const std::size_t fraction = m_offset;
            while (m_offset < m_json.size() &&
                   m_json[m_offset] >= '0' && m_json[m_offset] <= '9')
                ++m_offset;
            if (fraction == m_offset) return false;
        }
        if (m_offset < m_json.size() &&
            (m_json[m_offset] == 'e' || m_json[m_offset] == 'E'))
        {
            ++m_offset;
            if (m_offset < m_json.size() &&
                (m_json[m_offset] == '+' || m_json[m_offset] == '-'))
                ++m_offset;
            const std::size_t exponent = m_offset;
            while (m_offset < m_json.size() &&
                   m_json[m_offset] >= '0' && m_json[m_offset] <= '9')
                ++m_offset;
            if (exponent == m_offset) return false;
        }
        return ParseFiniteNumber(m_json.substr(start, m_offset - start));
    }

    bool ParseLiteral(const char* literal)
    {
        const std::size_t length = std::strlen(literal);
        if (m_json.compare(m_offset, length, literal) != 0) return false;
        m_offset += length;
        return true;
    }

    bool ParseObject(unsigned int depth)
    {
        if (depth > 64 || !Consume('{')) return false;
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
            if (!ParseValue(depth + 1)) return false;
            SkipWhitespace();
            if (Consume('}')) return true;
            if (!Consume(',')) return false;
            SkipWhitespace();
        }
    }

    bool ParseArray(unsigned int depth)
    {
        if (depth > 64 || !Consume('[')) return false;
        SkipWhitespace();
        if (Consume(']')) return true;
        while (true)
        {
            if (!ParseValue(depth + 1)) return false;
            SkipWhitespace();
            if (Consume(']')) return true;
            if (!Consume(',')) return false;
            SkipWhitespace();
        }
    }

    bool ParseValue(unsigned int depth)
    {
        if (depth > 64 || m_offset >= m_json.size() ||
            m_nodes >= kMaximumResultNodes)
            return false;
        ++m_nodes;
        const char c = m_json[m_offset];
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

    const std::string& m_json;
    std::size_t m_offset;
    std::size_t m_nodes;
    std::size_t m_decodedStringBytes;
};

} // namespace

std::string TypedToolProtocol::EncodeResultJson(const TradingToolResult& result)
{
    return TradingToolWireContract::EncodeResultEnvelope(result);
}

bool TypedToolProtocol::DecodeResultEnvelope(const std::string& json,
                                             TypedToolResultEnvelope& result,
                                             std::string& reason)
{
    result = TypedToolResultEnvelope();
    ResultJsonParser parser(json);
    if (!parser.ParseEnvelope(result))
    {
        result = TypedToolResultEnvelope();
        reason = "INVALID_RESULT_ENVELOPE";
        return false;
    }
    if (result.status != "ok" && result.status != "permission_denied" &&
        result.status != "invalid_tool" && result.status != "rejected" &&
        result.status != "duplicate" && result.status != "uncertain" &&
        result.status != "error")
    {
        result = TypedToolResultEnvelope();
        reason = "UNKNOWN_RESULT_STATUS";
        return false;
    }
    reason.clear();
    return true;
}
