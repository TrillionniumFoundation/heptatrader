#include "native_tool_discovery_contract.h"

#include <algorithm>
#include <climits>
#include <cstdint>
#include <iomanip>
#include <locale>
#include <set>
#include <sstream>
#include <vector>

namespace
{
struct Descriptor
{
    std::string name;
    std::string description;
    std::string capability;
    std::string effect;
    int timeoutMs = 0;
    std::string advertisedHash;
    std::string inputSchema;
    std::string resultSchema;
};

class Sha256
{
public:
    Sha256() : m_bitLength(0), m_bufferLength(0)
    {
        const std::uint32_t initial[] = {
            0x6a09e667U, 0xbb67ae85U, 0x3c6ef372U, 0xa54ff53aU,
            0x510e527fU, 0x9b05688cU, 0x1f83d9abU, 0x5be0cd19U
        };
        std::copy(initial, initial + 8, m_state);
    }

    void Update(const unsigned char* data, std::size_t size)
    {
        for (std::size_t index = 0; index < size; ++index)
        {
            m_buffer[m_bufferLength++] = data[index];
            if (m_bufferLength == 64)
            {
                Transform();
                m_bitLength += 512;
                m_bufferLength = 0;
            }
        }
    }

    std::string Finish()
    {
        std::size_t index = m_bufferLength;
        m_buffer[index++] = 0x80;
        if (index > 56)
        {
            while (index < 64) m_buffer[index++] = 0;
            Transform();
            index = 0;
        }
        while (index < 56) m_buffer[index++] = 0;
        m_bitLength += static_cast<std::uint64_t>(m_bufferLength) * 8U;
        for (unsigned int shift = 0; shift < 8; ++shift)
            m_buffer[63 - shift] =
                static_cast<unsigned char>(m_bitLength >> (shift * 8U));
        Transform();

        std::ostringstream out;
        out.imbue(std::locale::classic());
        out << "sha256:" << std::hex << std::setfill('0');
        for (unsigned int word = 0; word < 8; ++word)
            out << std::setw(8) << m_state[word];
        return out.str();
    }

private:
    static std::uint32_t Rotate(std::uint32_t value, unsigned int amount)
    {
        return (value >> amount) | (value << (32U - amount));
    }

    void Transform()
    {
        static const std::uint32_t constants[64] = {
            0x428a2f98U,0x71374491U,0xb5c0fbcfU,0xe9b5dba5U,
            0x3956c25bU,0x59f111f1U,0x923f82a4U,0xab1c5ed5U,
            0xd807aa98U,0x12835b01U,0x243185beU,0x550c7dc3U,
            0x72be5d74U,0x80deb1feU,0x9bdc06a7U,0xc19bf174U,
            0xe49b69c1U,0xefbe4786U,0x0fc19dc6U,0x240ca1ccU,
            0x2de92c6fU,0x4a7484aaU,0x5cb0a9dcU,0x76f988daU,
            0x983e5152U,0xa831c66dU,0xb00327c8U,0xbf597fc7U,
            0xc6e00bf3U,0xd5a79147U,0x06ca6351U,0x14292967U,
            0x27b70a85U,0x2e1b2138U,0x4d2c6dfcU,0x53380d13U,
            0x650a7354U,0x766a0abbU,0x81c2c92eU,0x92722c85U,
            0xa2bfe8a1U,0xa81a664bU,0xc24b8b70U,0xc76c51a3U,
            0xd192e819U,0xd6990624U,0xf40e3585U,0x106aa070U,
            0x19a4c116U,0x1e376c08U,0x2748774cU,0x34b0bcb5U,
            0x391c0cb3U,0x4ed8aa4aU,0x5b9cca4fU,0x682e6ff3U,
            0x748f82eeU,0x78a5636fU,0x84c87814U,0x8cc70208U,
            0x90befffaU,0xa4506cebU,0xbef9a3f7U,0xc67178f2U
        };
        std::uint32_t schedule[64];
        for (unsigned int index = 0; index < 16; ++index)
        {
            const unsigned int offset = index * 4;
            schedule[index] =
                (static_cast<std::uint32_t>(m_buffer[offset]) << 24) |
                (static_cast<std::uint32_t>(m_buffer[offset + 1]) << 16) |
                (static_cast<std::uint32_t>(m_buffer[offset + 2]) << 8) |
                static_cast<std::uint32_t>(m_buffer[offset + 3]);
        }
        for (unsigned int index = 16; index < 64; ++index)
        {
            const std::uint32_t first =
                Rotate(schedule[index - 15], 7) ^
                Rotate(schedule[index - 15], 18) ^
                (schedule[index - 15] >> 3);
            const std::uint32_t second =
                Rotate(schedule[index - 2], 17) ^
                Rotate(schedule[index - 2], 19) ^
                (schedule[index - 2] >> 10);
            schedule[index] = schedule[index - 16] + first +
                schedule[index - 7] + second;
        }
        std::uint32_t a = m_state[0];
        std::uint32_t b = m_state[1];
        std::uint32_t c = m_state[2];
        std::uint32_t d = m_state[3];
        std::uint32_t e = m_state[4];
        std::uint32_t f = m_state[5];
        std::uint32_t g = m_state[6];
        std::uint32_t h = m_state[7];
        for (unsigned int index = 0; index < 64; ++index)
        {
            const std::uint32_t upper =
                Rotate(e, 6) ^ Rotate(e, 11) ^ Rotate(e, 25);
            const std::uint32_t choose = (e & f) ^ ((~e) & g);
            const std::uint32_t first =
                h + upper + choose + constants[index] + schedule[index];
            const std::uint32_t lower =
                Rotate(a, 2) ^ Rotate(a, 13) ^ Rotate(a, 22);
            const std::uint32_t majority = (a & b) ^ (a & c) ^ (b & c);
            const std::uint32_t second = lower + majority;
            h = g; g = f; f = e; e = d + first;
            d = c; c = b; b = a; a = first + second;
        }
        m_state[0] += a; m_state[1] += b; m_state[2] += c; m_state[3] += d;
        m_state[4] += e; m_state[5] += f; m_state[6] += g; m_state[7] += h;
    }

    std::uint32_t m_state[8];
    std::uint64_t m_bitLength;
    unsigned char m_buffer[64];
    std::size_t m_bufferLength;
};

std::string Digest(const std::string& value)
{
    Sha256 sha;
    sha.Update(reinterpret_cast<const unsigned char*>(value.data()), value.size());
    return sha.Finish();
}

bool IsDigest(const std::string& value)
{
    if (value.size() != 71 || value.compare(0, 7, "sha256:") != 0)
        return false;
    for (std::size_t index = 7; index < value.size(); ++index)
    {
        const char byte = value[index];
        if (!((byte >= '0' && byte <= '9') ||
              (byte >= 'a' && byte <= 'f')))
            return false;
    }
    return true;
}

// Keep the discovery parser independent from the privileged registry header:
// this translation unit is part of the installed, unprivileged SDK.  The
// grammar is intentionally the same ASCII dotted identifier contract used by
// the native wire encoder, so a peer cannot poison the client catalog with a
// descriptor name that can never be called on the wire.
bool IsCanonicalToolName(const std::string& value)
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

class Parser
{
public:
    explicit Parser(const std::string& json) : m_json(json), m_offset(0) {}

    bool Parse(const std::string& toolName,
               std::string& advertisedCatalog,
               std::vector<Descriptor>& descriptors,
               std::string& reason)
    {
        if (m_json.empty() || m_json.size() > 1048576)
            return Fail("DISCOVERY_PAYLOAD_SHAPE_INVALID", reason);
        if (!ConsumeObjectStart())
            return Fail("DISCOVERY_PAYLOAD_SHAPE_INVALID", reason);
        std::set<std::string> seen;
        std::string protocol;
        int protocolVersion = 0;
        int protocolMin = 0;
        int protocolMax = 0;
        int schemaVersion = 0;
        bool haveTools = false;
        bool haveTool = false;
        while (!ConsumeObjectEnd())
        {
            std::string key;
            if (!ParseMemberKey(key) || !seen.insert(key).second)
                return Fail("DISCOVERY_PAYLOAD_SHAPE_INVALID", reason);
            if (key == "protocol")
            {
                if (!ParseString(protocol))
                    return Fail("DISCOVERY_PAYLOAD_SHAPE_INVALID", reason);
            }
            else if (key == "protocol_version")
            {
                if (!ParseInteger(protocolVersion))
                    return Fail("DISCOVERY_PAYLOAD_SHAPE_INVALID", reason);
            }
            else if (key == "protocol_min_version")
            {
                if (!ParseInteger(protocolMin))
                    return Fail("DISCOVERY_PAYLOAD_SHAPE_INVALID", reason);
            }
            else if (key == "protocol_max_version")
            {
                if (!ParseInteger(protocolMax))
                    return Fail("DISCOVERY_PAYLOAD_SHAPE_INVALID", reason);
            }
            else if (key == "schema_version")
            {
                if (!ParseInteger(schemaVersion))
                    return Fail("DISCOVERY_SCHEMA_VERSION_UNSUPPORTED", reason);
            }
            else if (key == "catalog_schema_hash")
            {
                if (!ParseString(advertisedCatalog) ||
                    !IsDigest(advertisedCatalog))
                    return Fail("DISCOVERY_CATALOG_SCHEMA_HASH_INVALID", reason);
            }
            else if (key == "tools")
            {
                if (haveTool || !ParseDescriptorArray(descriptors, reason))
                    return false;
                haveTools = true;
            }
            else if (key == "tool")
            {
                Descriptor descriptor;
                if (haveTools || !ParseDescriptor(descriptor, reason))
                    return false;
                descriptors.push_back(descriptor);
                haveTool = true;
            }
            else
            {
                return Fail("DISCOVERY_PAYLOAD_SHAPE_INVALID", reason);
            }
            if (!ConsumeMemberEnd())
                return Fail("DISCOVERY_PAYLOAD_SHAPE_INVALID", reason);
        }
        SkipWhitespace();
        if (m_offset != m_json.size() || seen.size() != 7 ||
            protocol != "hepta.agent-tools" || protocolVersion != 1 ||
            protocolMin != 1 || protocolMax != 1 ||
            advertisedCatalog.empty())
            return Fail("DISCOVERY_PAYLOAD_SHAPE_INVALID", reason);
        if (schemaVersion !=
            static_cast<int>(NativeToolDiscoveryContract::kSchemaVersion))
            return Fail("DISCOVERY_SCHEMA_VERSION_UNSUPPORTED", reason);
        if ((toolName == "system.tools.list" &&
             (!haveTools || haveTool || descriptors.empty())) ||
            (toolName == "system.tools.describe" &&
             (!haveTool || haveTools || descriptors.size() != 1)) ||
            (toolName != "system.tools.list" &&
             toolName != "system.tools.describe"))
            return Fail("DISCOVERY_PAYLOAD_SHAPE_INVALID", reason);
        return true;
    }

private:
    static bool Fail(const char* value, std::string& reason)
    {
        reason = value;
        return false;
    }

    void SkipWhitespace()
    {
        while (m_offset < m_json.size() &&
               (m_json[m_offset] == ' ' || m_json[m_offset] == '\t' ||
                m_json[m_offset] == '\r' || m_json[m_offset] == '\n'))
            ++m_offset;
    }

    bool Consume(char expected)
    {
        SkipWhitespace();
        if (m_offset >= m_json.size() || m_json[m_offset] != expected)
            return false;
        ++m_offset;
        return true;
    }

    bool ConsumeObjectStart()
    {
        return Consume('{');
    }

    bool ConsumeObjectEnd()
    {
        SkipWhitespace();
        if (m_offset < m_json.size() && m_json[m_offset] == '}')
        {
            ++m_offset;
            return true;
        }
        return false;
    }

    bool ParseMemberKey(std::string& key)
    {
        return ParseString(key) && Consume(':');
    }

    bool ConsumeMemberEnd()
    {
        SkipWhitespace();
        if (m_offset < m_json.size() && m_json[m_offset] == ',')
        {
            ++m_offset;
            // A comma must be followed by another member.  The old
            // ``while (!ConsumeObjectEnd())`` loops otherwise accepted
            // ``{"key": value,}`` because the next iteration consumed the
            // closing brace as if it were an empty member.
            SkipWhitespace();
            if (m_offset >= m_json.size() || m_json[m_offset] == '}')
                return false;
            return true;
        }
        return m_offset < m_json.size() && m_json[m_offset] == '}';
    }

    bool ParseHexQuad(unsigned int& value)
    {
        if (m_offset + 4 > m_json.size()) return false;
        value = 0;
        for (unsigned int index = 0; index < 4; ++index)
        {
            const unsigned char byte =
                static_cast<unsigned char>(m_json[m_offset++]);
            value <<= 4;
            if (byte >= '0' && byte <= '9') value += byte - '0';
            else if (byte >= 'a' && byte <= 'f') value += byte - 'a' + 10;
            else if (byte >= 'A' && byte <= 'F') value += byte - 'A' + 10;
            else return false;
        }
        return true;
    }

    static void AppendCodePoint(unsigned int codePoint, std::string& value)
    {
        if (codePoint <= 0x7f)
            value.push_back(static_cast<char>(codePoint));
        else if (codePoint <= 0x7ff)
        {
            value.push_back(static_cast<char>(0xc0 | (codePoint >> 6)));
            value.push_back(static_cast<char>(0x80 | (codePoint & 0x3f)));
        }
        else if (codePoint <= 0xffff)
        {
            value.push_back(static_cast<char>(0xe0 | (codePoint >> 12)));
            value.push_back(static_cast<char>(
                0x80 | ((codePoint >> 6) & 0x3f)));
            value.push_back(static_cast<char>(0x80 | (codePoint & 0x3f)));
        }
        else
        {
            value.push_back(static_cast<char>(0xf0 | (codePoint >> 18)));
            value.push_back(static_cast<char>(
                0x80 | ((codePoint >> 12) & 0x3f)));
            value.push_back(static_cast<char>(
                0x80 | ((codePoint >> 6) & 0x3f)));
            value.push_back(static_cast<char>(0x80 | (codePoint & 0x3f)));
        }
    }

    bool ParseRawUtf8(std::string& value)
    {
        const std::size_t start = m_offset;
        const unsigned char lead =
            static_cast<unsigned char>(m_json[m_offset]);
        unsigned int length = 0;
        unsigned int codePoint = 0;
        if (lead >= 0xc2 && lead <= 0xdf)
        {
            length = 2; codePoint = lead & 0x1f;
        }
        else if (lead >= 0xe0 && lead <= 0xef)
        {
            length = 3; codePoint = lead & 0x0f;
        }
        else if (lead >= 0xf0 && lead <= 0xf4)
        {
            length = 4; codePoint = lead & 0x07;
        }
        else return false;
        if (m_offset + length > m_json.size()) return false;
        for (unsigned int index = 1; index < length; ++index)
        {
            const unsigned char next =
                static_cast<unsigned char>(m_json[m_offset + index]);
            if ((next & 0xc0) != 0x80) return false;
            codePoint = (codePoint << 6) | (next & 0x3f);
        }
        if ((length == 2 && codePoint < 0x80) ||
            (length == 3 && codePoint < 0x800) ||
            (length == 4 && codePoint < 0x10000) ||
            codePoint > 0x10ffff ||
            (codePoint >= 0xd800 && codePoint <= 0xdfff))
            return false;
        value.append(m_json, start, length);
        m_offset += length;
        return true;
    }

    bool ParseString(std::string& value)
    {
        SkipWhitespace();
        if (m_offset >= m_json.size() || m_json[m_offset++] != '"')
            return false;
        value.clear();
        while (m_offset < m_json.size())
        {
            const unsigned char byte =
                static_cast<unsigned char>(m_json[m_offset++]);
            if (byte == '"') return value.find('\0') == std::string::npos;
            if (byte < 0x20) return false;
            if (byte >= 0x80)
            {
                --m_offset;
                if (!ParseRawUtf8(value)) return false;
                continue;
            }
            if (byte != '\\')
            {
                value.push_back(static_cast<char>(byte));
                continue;
            }
            if (m_offset >= m_json.size()) return false;
            const char escape = m_json[m_offset++];
            if (escape == '"' || escape == '\\' || escape == '/')
                value.push_back(escape);
            else if (escape == 'b') value.push_back('\b');
            else if (escape == 'f') value.push_back('\f');
            else if (escape == 'n') value.push_back('\n');
            else if (escape == 'r') value.push_back('\r');
            else if (escape == 't') value.push_back('\t');
            else if (escape == 'u')
            {
                unsigned int codePoint = 0;
                if (!ParseHexQuad(codePoint)) return false;
                if (codePoint >= 0xd800 && codePoint <= 0xdbff)
                {
                    if (m_offset + 2 > m_json.size() ||
                        m_json[m_offset] != '\\' ||
                        m_json[m_offset + 1] != 'u')
                        return false;
                    m_offset += 2;
                    unsigned int low = 0;
                    if (!ParseHexQuad(low) ||
                        low < 0xdc00 || low > 0xdfff)
                        return false;
                    codePoint = 0x10000 +
                        ((codePoint - 0xd800) << 10) + (low - 0xdc00);
                }
                else if (codePoint >= 0xdc00 && codePoint <= 0xdfff)
                    return false;
                AppendCodePoint(codePoint, value);
            }
            else return false;
        }
        return false;
    }

    bool ParseInteger(int& value)
    {
        SkipWhitespace();
        const std::size_t start = m_offset;
        if (m_offset < m_json.size() && m_json[m_offset] == '-')
            ++m_offset;
        if (m_offset >= m_json.size() ||
            m_json[m_offset] < '0' || m_json[m_offset] > '9')
            return false;
        if (m_json[m_offset] == '0')
            ++m_offset;
        else
            while (m_offset < m_json.size() &&
                   m_json[m_offset] >= '0' && m_json[m_offset] <= '9')
                ++m_offset;
        const std::string number = m_json.substr(start, m_offset - start);
        if (number.empty() || number == "-" ||
            number == "-0" ||
            (number.size() > 1 && number[0] == '0') ||
            (number.size() > 2 && number[0] == '-' && number[1] == '0'))
            return false;
        std::istringstream input(number);
        input.imbue(std::locale::classic());
        long long parsed = 0;
        input >> parsed;
        if (!input || !input.eof() || parsed < INT_MIN || parsed > INT_MAX)
            return false;
        value = static_cast<int>(parsed);
        return true;
    }

    static std::string EscapeCanonical(const std::string& value)
    {
        std::ostringstream out;
        out.imbue(std::locale::classic());
        out << '"';
        for (std::size_t index = 0; index < value.size(); ++index)
        {
            const unsigned char byte =
                static_cast<unsigned char>(value[index]);
            if (byte == '"') out << "\\\"";
            else if (byte == '\\') out << "\\\\";
            else if (byte == '\b') out << "\\b";
            else if (byte == '\f') out << "\\f";
            else if (byte == '\n') out << "\\n";
            else if (byte == '\r') out << "\\r";
            else if (byte == '\t') out << "\\t";
            else if (byte < 0x20)
                out << "\\u" << std::hex << std::setw(4)
                    << std::setfill('0') << static_cast<unsigned int>(byte)
                    << std::dec;
            else if (byte < 0x80) out << static_cast<char>(byte);
            else
            {
                // Discovery contracts are presently ASCII.  Encoding each
                // non-ASCII byte is intentionally rejected by the schema
                // parser below instead of producing a non-equivalent digest.
                return std::string();
            }
        }
        out << '"';
        return out.str();
    }

    bool ParseSchemaValue(std::string& canonical, unsigned int depth)
    {
        if (depth > 64) return false;
        SkipWhitespace();
        if (m_offset >= m_json.size()) return false;
        if (m_json[m_offset] == '{')
            return ParseSchemaObject(canonical, depth + 1);
        if (m_json[m_offset] == '[')
        {
            ++m_offset;
            canonical.push_back('[');
            SkipWhitespace();
            if (m_offset < m_json.size() && m_json[m_offset] == ']')
            {
                ++m_offset;
                canonical.push_back(']');
                return true;
            }
            bool first = true;
            while (true)
            {
                if (!first) canonical.push_back(',');
                if (!ParseSchemaValue(canonical, depth + 1)) return false;
                first = false;
                SkipWhitespace();
                if (m_offset < m_json.size() && m_json[m_offset] == ']')
                {
                    ++m_offset;
                    canonical.push_back(']');
                    return true;
                }
                if (m_offset >= m_json.size() || m_json[m_offset++] != ',')
                    return false;
            }
        }
        if (m_json[m_offset] == '"')
        {
            std::string value;
            if (!ParseString(value)) return false;
            const std::string escaped = EscapeCanonical(value);
            if (escaped.empty()) return false;
            canonical += escaped;
            return true;
        }
        if (m_json.compare(m_offset, 4, "true") == 0)
        {
            m_offset += 4; canonical += "true"; return true;
        }
        if (m_json.compare(m_offset, 5, "false") == 0)
        {
            m_offset += 5; canonical += "false"; return true;
        }
        if (m_json.compare(m_offset, 4, "null") == 0)
        {
            m_offset += 4; canonical += "null"; return true;
        }
        int integer = 0;
        if (!ParseInteger(integer)) return false;
        canonical += std::to_string(integer);
        return true;
    }

    bool ParseSchemaObject(std::string& canonical, unsigned int depth = 0)
    {
        if (depth > 64 || !Consume('{')) return false;
        canonical.push_back('{');
        std::set<std::string> keys;
        SkipWhitespace();
        if (m_offset < m_json.size() && m_json[m_offset] == '}')
        {
            ++m_offset;
            canonical.push_back('}');
            return true;
        }
        bool first = true;
        while (true)
        {
            std::string key;
            if (!ParseString(key) || !keys.insert(key).second ||
                !Consume(':'))
                return false;
            const std::string escaped = EscapeCanonical(key);
            if (escaped.empty()) return false;
            if (!first) canonical.push_back(',');
            canonical += escaped;
            canonical.push_back(':');
            if (!ParseSchemaValue(canonical, depth + 1)) return false;
            first = false;
            SkipWhitespace();
            if (m_offset < m_json.size() && m_json[m_offset] == '}')
            {
                ++m_offset;
                canonical.push_back('}');
                return true;
            }
            if (m_offset >= m_json.size() || m_json[m_offset++] != ',')
                return false;
        }
    }

    bool ParseDescriptor(Descriptor& descriptor, std::string& reason)
    {
        if (!ConsumeObjectStart())
            return Fail("DISCOVERY_DESCRIPTOR_INVALID", reason);
        std::set<std::string> seen;
        while (!ConsumeObjectEnd())
        {
            std::string key;
            if (!ParseMemberKey(key) || !seen.insert(key).second)
                return Fail("DISCOVERY_DESCRIPTOR_INVALID", reason);
            if (key == "name")
            {
                if (!ParseString(descriptor.name))
                    return Fail("DISCOVERY_DESCRIPTOR_INVALID", reason);
            }
            else if (key == "description")
            {
                if (!ParseString(descriptor.description))
                    return Fail("DISCOVERY_DESCRIPTOR_INVALID", reason);
            }
            else if (key == "required_capability")
            {
                if (!ParseString(descriptor.capability))
                    return Fail("DISCOVERY_DESCRIPTOR_INVALID", reason);
            }
            else if (key == "effect")
            {
                if (!ParseString(descriptor.effect))
                    return Fail("DISCOVERY_DESCRIPTOR_INVALID", reason);
            }
            else if (key == "timeout_ms")
            {
                if (!ParseInteger(descriptor.timeoutMs))
                    return Fail("DISCOVERY_DESCRIPTOR_INVALID", reason);
            }
            else if (key == "schema_hash")
            {
                if (!ParseString(descriptor.advertisedHash) ||
                    !IsDigest(descriptor.advertisedHash))
                    return Fail(
                        "DISCOVERY_DESCRIPTOR_SCHEMA_HASH_INVALID", reason);
            }
            else if (key == "input_schema")
            {
                if (!ParseSchemaObject(descriptor.inputSchema))
                    return Fail("DISCOVERY_DESCRIPTOR_INVALID", reason);
            }
            else if (key == "result_schema")
            {
                if (!ParseSchemaObject(descriptor.resultSchema))
                    return Fail("DISCOVERY_DESCRIPTOR_INVALID", reason);
            }
            else
                return Fail("DISCOVERY_DESCRIPTOR_INVALID", reason);
            if (!ConsumeMemberEnd())
                return Fail("DISCOVERY_DESCRIPTOR_INVALID", reason);
        }
        if (seen.size() != 8 || !IsCanonicalToolName(descriptor.name) ||
            descriptor.description.size() > 65536 ||
            descriptor.capability.empty() || descriptor.capability.size() > 128 ||
            (descriptor.effect != "read" &&
             descriptor.effect != "trade") ||
            descriptor.timeoutMs < 1 || descriptor.timeoutMs > 120000 ||
            descriptor.inputSchema.empty() || descriptor.resultSchema.empty())
            return Fail("DISCOVERY_DESCRIPTOR_INVALID", reason);
        std::string canonical = descriptor.name;
        canonical.push_back('\0');
        canonical += descriptor.description;
        canonical.push_back('\0');
        canonical += descriptor.capability;
        canonical.push_back('\0');
        canonical += descriptor.effect;
        canonical.push_back('\0');
        canonical += std::to_string(descriptor.timeoutMs);
        canonical.push_back('\0');
        canonical += descriptor.inputSchema;
        canonical.push_back('\0');
        canonical += descriptor.resultSchema;
        if (Digest(canonical) != descriptor.advertisedHash)
            return Fail("DISCOVERY_DESCRIPTOR_SCHEMA_HASH_MISMATCH", reason);
        return true;
    }

    bool ParseDescriptorArray(std::vector<Descriptor>& descriptors,
                              std::string& reason)
    {
        if (!Consume('['))
            return Fail("DISCOVERY_PAYLOAD_SHAPE_INVALID", reason);
        SkipWhitespace();
        if (m_offset < m_json.size() && m_json[m_offset] == ']')
        {
            ++m_offset;
            return true;
        }
        while (true)
        {
            Descriptor descriptor;
            if (!ParseDescriptor(descriptor, reason)) return false;
            descriptors.push_back(descriptor);
            SkipWhitespace();
            if (m_offset < m_json.size() && m_json[m_offset] == ']')
            {
                ++m_offset;
                return true;
            }
            if (m_offset >= m_json.size() || m_json[m_offset++] != ',')
                return Fail("DISCOVERY_PAYLOAD_SHAPE_INVALID", reason);
        }
    }

    const std::string& m_json;
    std::size_t m_offset;
};

std::string CatalogDigest(std::vector<Descriptor> descriptors,
                          bool& duplicate)
{
    std::sort(descriptors.begin(), descriptors.end(),
        [](const Descriptor& left, const Descriptor& right) {
            return left.name < right.name;
        });
    duplicate = false;
    std::string canonical;
    for (std::size_t index = 0; index < descriptors.size(); ++index)
    {
        if (index != 0 && descriptors[index - 1].name == descriptors[index].name)
        {
            duplicate = true;
            return std::string();
        }
        canonical += descriptors[index].name;
        canonical.push_back('=');
        canonical += descriptors[index].advertisedHash;
        canonical.push_back('\n');
    }
    return Digest(canonical);
}
}

namespace NativeToolDiscoveryContract
{
bool Validate(const std::string& discoveryOperation,
              const std::string& payload,
              const std::string& requestedTargetToolName,
              const CatalogSnapshot& expectedCatalog,
              CatalogSnapshot& observedCatalog,
              std::string& reason)
{
    observedCatalog = CatalogSnapshot();
    std::vector<Descriptor> descriptors;
    Parser parser(payload);
    if (!parser.Parse(
            discoveryOperation, observedCatalog.schemaHash, descriptors, reason))
        return false;
    const auto reject = [&reason](const char* code) {
        reason = code;
        return false;
    };
    for (std::size_t index = 0; index < descriptors.size(); ++index)
    {
        if (!observedCatalog.descriptorSchemaHashes.insert(
                std::make_pair(
                    descriptors[index].name,
                    descriptors[index].advertisedHash)).second)
            return reject("DISCOVERY_DUPLICATE_TOOL");
    }
    if (discoveryOperation == "system.tools.list")
    {
        if (!requestedTargetToolName.empty())
            return reject("DISCOVERY_TARGET_UNEXPECTED");
        bool duplicate = false;
        const std::string calculated = CatalogDigest(descriptors, duplicate);
        if (duplicate) return reject("DISCOVERY_DUPLICATE_TOOL");
        if (calculated != observedCatalog.schemaHash)
            return reject("DISCOVERY_CATALOG_SCHEMA_HASH_MISMATCH");
        if (!expectedCatalog.schemaHash.empty() &&
            (expectedCatalog.schemaHash != observedCatalog.schemaHash ||
             expectedCatalog.descriptorSchemaHashes !=
                 observedCatalog.descriptorSchemaHashes))
            return reject("DISCOVERY_CATALOG_CHANGED");
    }
    else
    {
        if (requestedTargetToolName.empty())
            return reject("DISCOVERY_TARGET_REQUIRED");
        if (expectedCatalog.schemaHash.empty() ||
            expectedCatalog.descriptorSchemaHashes.empty())
            return reject("DISCOVERY_CATALOG_CONTEXT_REQUIRED");
        if (expectedCatalog.schemaHash != observedCatalog.schemaHash)
            return reject("DISCOVERY_CATALOG_CHANGED");
        if (descriptors[0].name != requestedTargetToolName)
            return reject("DISCOVERY_TARGET_MISMATCH");
        const std::map<std::string, std::string>::const_iterator expected =
            expectedCatalog.descriptorSchemaHashes.find(
                requestedTargetToolName);
        if (expected == expectedCatalog.descriptorSchemaHashes.end())
            return reject("DISCOVERY_TARGET_NOT_IN_CATALOG");
        if (expected->second != descriptors[0].advertisedHash)
            return reject("DISCOVERY_DESCRIPTOR_CHANGED");
    }
    reason.clear();
    return true;
}
}
