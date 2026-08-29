#include "typed_tool_protocol.h"
#include "../tools/trading_tool_wire_contract.h"

#include <cerrno>
#include <cctype>
#include <cstdlib>
#include <cstring>
#include <set>

namespace {
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

class ResultJsonParser
{
public:
    explicit ResultJsonParser(const std::string& json)
        : m_json(json), m_offset(0)
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
            result.toolName.empty() || result.toolName.size() > 64 ||
            result.reasonCode.size() > 128 || result.detail.size() > 65536 ||
            result.status.find('\0') != std::string::npos ||
            result.toolName.find('\0') != std::string::npos ||
            result.reasonCode.find('\0') != std::string::npos ||
            result.detail.find('\0') != std::string::npos)
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
        if (m_offset + 4 > m_json.size()) return false;
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

    static void AppendCodePoint(unsigned int codePoint, std::string& value)
    {
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
        if (m_offset + length > m_json.size()) return false;
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
            if (c == '"') return true;
            if (c < 0x20) return false;
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
            else if (escaped == 'b') value.push_back('\b');
            else if (escaped == 'f') value.push_back('\f');
            else if (escaped == 'n') value.push_back('\n');
            else if (escaped == 'r') value.push_back('\r');
            else if (escaped == 't') value.push_back('\t');
            else if (escaped == 'u')
            {
                unsigned int codePoint = 0;
                if (!ParseHexQuad(codePoint)) return false;
                if (codePoint >= 0xd800 && codePoint <= 0xdbff)
                {
                    if (m_offset + 2 > m_json.size() ||
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
                AppendCodePoint(codePoint, value);
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
        if (m_offset < m_json.size() && m_json[m_offset] == '-') ++m_offset;
        if (m_offset >= m_json.size()) return false;
        if (m_json[m_offset] == '0')
        {
            ++m_offset;
            if (m_offset < m_json.size() &&
                std::isdigit(static_cast<unsigned char>(m_json[m_offset])))
                return false;
        }
        else
        {
            if (m_json[m_offset] < '1' || m_json[m_offset] > '9') return false;
            while (m_offset < m_json.size() &&
                   std::isdigit(static_cast<unsigned char>(m_json[m_offset])))
                ++m_offset;
        }
        return ParseLong(m_json.substr(start, m_offset - start), value);
    }

    bool ParseNumber()
    {
        if (m_offset < m_json.size() && m_json[m_offset] == '-') ++m_offset;
        if (m_offset >= m_json.size()) return false;
        if (m_json[m_offset] == '0')
        {
            ++m_offset;
            if (m_offset < m_json.size() &&
                std::isdigit(static_cast<unsigned char>(m_json[m_offset])))
                return false;
        }
        else
        {
            if (m_json[m_offset] < '1' || m_json[m_offset] > '9') return false;
            while (m_offset < m_json.size() &&
                   std::isdigit(static_cast<unsigned char>(m_json[m_offset])))
                ++m_offset;
        }
        if (m_offset < m_json.size() && m_json[m_offset] == '.')
        {
            ++m_offset;
            const std::size_t fraction = m_offset;
            while (m_offset < m_json.size() &&
                   std::isdigit(static_cast<unsigned char>(m_json[m_offset])))
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
                   std::isdigit(static_cast<unsigned char>(m_json[m_offset])))
                ++m_offset;
            if (exponent == m_offset) return false;
        }
        return true;
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
        if (depth > 64 || m_offset >= m_json.size()) return false;
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
        reason = "INVALID_RESULT_ENVELOPE";
        return false;
    }
    if (result.status != "ok" && result.status != "permission_denied" &&
        result.status != "invalid_tool" && result.status != "rejected" &&
        result.status != "duplicate" && result.status != "uncertain" &&
        result.status != "error")
    {
        reason = "UNKNOWN_RESULT_STATUS";
        return false;
    }
    reason.clear();
    return true;
}
