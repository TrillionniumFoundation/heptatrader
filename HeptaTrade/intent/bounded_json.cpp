#include "bounded_json.h"

#include <cmath>
#include <limits>
#include <locale>
#include <sstream>
#include <utility>

BoundedJsonValue::BoundedJsonValue()
    : m_type(Type::Null), m_boolean(false), m_number(0.0)
{
}

namespace
{

const std::size_t kMaximumJsonStringBytes = 1024u * 1024u;

bool ParseExactUnsignedNumber(const std::string& token,
                              std::uint64_t& out)
{
    // JSON numbers with a sign are not unsigned values.  In particular,
    // reject -0 rather than relying on its double representation (which would
    // compare equal to +0 and obscure the caller's malformed input).
    if (token.empty() || token[0] == '-') return false;

    std::size_t offset = 0;
    std::string digits;
    digits.reserve(token.size());
    while (offset < token.size() &&
           token[offset] >= '0' && token[offset] <= '9')
    {
        digits.push_back(token[offset++]);
    }

    std::size_t fractionalDigits = 0;
    if (offset < token.size() && token[offset] == '.')
    {
        ++offset;
        const std::size_t fractionStart = offset;
        while (offset < token.size() &&
               token[offset] >= '0' && token[offset] <= '9')
        {
            digits.push_back(token[offset++]);
        }
        fractionalDigits = offset - fractionStart;
    }

    bool exponentNegative = false;
    bool exponentTooLarge = false;
    std::uint64_t exponentMagnitude = 0;
    if (offset < token.size() &&
        (token[offset] == 'e' || token[offset] == 'E'))
    {
        ++offset;
        if (offset < token.size() &&
            (token[offset] == '+' || token[offset] == '-'))
        {
            exponentNegative = token[offset] == '-';
            ++offset;
        }
        // We only need to distinguish an exponent larger than the scale that
        // could still produce a uint64.  Basing this on token.size() is
        // incorrect: a compact token such as 1e5 has an exponent larger than
        // its byte length but is perfectly representable.  Saturating at a
        // semantic budget also avoids integer overflow on hostile inputs such
        // as 1e999999999999999999999.
        const std::uint64_t maximum =
            std::numeric_limits<std::uint64_t>::max();
        std::uint64_t budget = 0;
        if (exponentNegative)
            budget = static_cast<std::uint64_t>(digits.size());
        else if (fractionalDigits > maximum - 20u)
            budget = maximum;
        else
            budget = static_cast<std::uint64_t>(fractionalDigits) + 20u;
        while (offset < token.size() &&
               token[offset] >= '0' && token[offset] <= '9')
        {
            const unsigned int digit =
                static_cast<unsigned int>(token[offset++] - '0');
            if (exponentMagnitude > budget / 10u ||
                (exponentMagnitude == budget / 10u &&
                 digit > budget % 10u))
                exponentTooLarge = true;
            else if (!exponentTooLarge)
                exponentMagnitude = exponentMagnitude * 10u + digit;
        }
    }
    if (offset != token.size() || digits.empty()) return false;

    // A zero mantissa is exactly zero for every finite exponent, including a
    // very large one.  This check also avoids constructing a huge scaled
    // representation for 0e+N.
    bool allZero = true;
    for (std::string::const_iterator it = digits.begin();
         it != digits.end(); ++it)
    {
        if (*it != '0')
        {
            allZero = false;
            break;
        }
    }
    if (allZero)
    {
        out = 0;
        return true;
    }
    if (exponentTooLarge) return false;

    // Remove insignificant leading zeroes before applying the decimal scale.
    const std::size_t firstSignificant = digits.find_first_not_of('0');
    digits.erase(0, firstSignificant);

    // Apply the decimal scale without converting to a signed type.  A
    // hostile exponent can be larger than LLONG_MAX even though the token is
    // bounded by the caller; doing the arithmetic in a signed type would
    // invoke undefined behaviour before we could reject it.  The resulting
    // unsigned integer can contain at most twenty decimal digits, so all
    // scale arithmetic is checked against that small budget.
    if (exponentNegative)
    {
        if (exponentMagnitude >
            std::numeric_limits<std::uint64_t>::max() -
                static_cast<std::uint64_t>(fractionalDigits))
            return false;
        const std::uint64_t removedMagnitude = exponentMagnitude +
            static_cast<std::uint64_t>(fractionalDigits);
        if (removedMagnitude >= static_cast<std::uint64_t>(digits.size()))
            return false;
        const std::size_t removed =
            static_cast<std::size_t>(removedMagnitude);
        if (removed != 0u)
        {
            for (std::size_t i = digits.size() - removed; i < digits.size(); ++i)
                if (digits[i] != '0') return false;
            digits.erase(digits.size() - removed);
        }
    }
    else if (exponentMagnitude >=
             static_cast<std::uint64_t>(fractionalDigits))
    {
        const std::uint64_t appendedMagnitude = exponentMagnitude -
            static_cast<std::uint64_t>(fractionalDigits);
        if (appendedMagnitude > 20u ||
            digits.size() > 20u - static_cast<std::size_t>(appendedMagnitude))
            return false;
        const std::size_t appended =
            static_cast<std::size_t>(appendedMagnitude);
        digits.append(appended, '0');
    }
    else
    {
        const std::size_t removed = fractionalDigits -
            static_cast<std::size_t>(exponentMagnitude);
        if (removed >= digits.size()) return false;
        if (removed != 0u)
        {
            for (std::size_t i = digits.size() - removed; i < digits.size(); ++i)
                if (digits[i] != '0') return false;
            digits.erase(digits.size() - removed);
        }
    }

    if (digits.empty()) return false;
    std::uint64_t parsed = 0;
    const std::uint64_t maximum =
        std::numeric_limits<std::uint64_t>::max();
    for (std::string::const_iterator it = digits.begin();
         it != digits.end(); ++it)
    {
        const std::uint64_t digit =
            static_cast<std::uint64_t>(*it - '0');
        if (parsed > (maximum - digit) / 10u) return false;
        parsed = parsed * 10u + digit;
    }
    out = parsed;
    return true;
}

} // namespace

namespace
{

bool ParseFiniteNumber(const std::string& token, double& parsed)
{
    // std::strtod follows the process-global C locale.  A host can install a
    // locale whose decimal separator is a comma, causing a valid JSON token
    // such as 1.25 to be truncated at the dot (or interpreted differently).
    // Stream extraction with an explicitly imbued classic locale keeps the
    // JSON grammar invariant regardless of embedding-process locale.
    std::istringstream input(token);
    input.imbue(std::locale::classic());
    input >> std::noskipws >> parsed;
    if (input.fail() || !input.eof() || !std::isfinite(parsed)) return false;
    // Do not admit non-zero values that silently underflow to zero.  A JSON
    // number is parsed as a double for convenience, but underflowing a
    // non-zero token is data loss.  Both signed-zero spellings are valid JSON
    // numbers; callers that require an unsigned/canonical value use
    // BoundedJsonValue::Unsigned(), which preserves the original lexical
    // token and rejects a leading minus.
    if (parsed == 0.0)
    {
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
        if (!mantissaZero) return false;
    }
    return true;
}

} // namespace

bool BoundedJsonValue::Boolean(bool& out) const
{
    if (!IsBoolean()) return false;
    out = m_boolean;
    return true;
}

bool BoundedJsonValue::Number(double& out) const
{
    if (!IsNumber()) return false;
    out = m_number;
    return true;
}

bool BoundedJsonValue::Unsigned(std::uint64_t& out) const
{
    if (!IsNumber() || !std::isfinite(m_number) ||
        m_numberText.empty())
        return false;
    return ParseExactUnsignedNumber(m_numberText, out);
}

bool BoundedJsonValue::String(std::string& out) const
{
    if (!IsString()) return false;
    out = m_string;
    return true;
}

const BoundedJsonValue* BoundedJsonValue::Find(const std::string& key) const
{
    if (!IsObject()) return nullptr;
    const std::map<std::string, BoundedJsonValue>::const_iterator found =
        m_object.find(key);
    return found == m_object.end() ? nullptr : &found->second;
}

class BoundedJsonParser
{
public:
    BoundedJsonParser(const std::string& input,
            std::size_t maximumDepth,
            std::size_t maximumNodes,
            std::size_t maximumStringBytes)
        : m_input(input), m_maximumDepth(maximumDepth),
m_maximumNodes(maximumNodes), m_maximumStringBytes(maximumStringBytes)
    {
    }

    bool Parse(BoundedJsonValue& value, std::string& reason)
    {
        SkipWhitespace();
        if (!ParseValue(0, value, reason)) return false;
        SkipWhitespace();
        if (m_offset != m_input.size())
        {
  reason = "JSON_TRAILING_DATA";
  return false;
        }
        reason.clear();
        return true;
    }

private:
    static bool IsContinuation(unsigned char value)
    {
        return value >= 0x80u && value <= 0xbfu;
    }

    static int HexDigit(char value)
    {
        if (value >= '0' && value <= '9') return value - '0';
        if (value >= 'a' && value <= 'f') return value - 'a' + 10;
        if (value >= 'A' && value <= 'F') return value - 'A' + 10;
        return -1;
    }

    bool AppendCodePoint(std::uint32_t codepoint,
                         std::string& output,
                         std::string& reason)
    {
        // Unicode scalar values exclude UTF-16 surrogate code points and
        // anything beyond the Unicode maximum.  Rejecting these here keeps
        // parsed strings valid UTF-8 even when they came from a JSON escape.
        if (codepoint > 0x10ffffu ||
            (codepoint >= 0xd800u && codepoint <= 0xdfffu))
        {
            reason = "JSON_UTF8_INVALID";
            return false;
        }
        // Keep decoded strings safe for logs, cache keys and downstream
        // framing.  Escaped controls otherwise bypass ParseString's raw-byte
        // check (for example ``\u007f`` or ``\u0085``).
        if (codepoint < 0x20u ||
            (codepoint >= 0x7fu && codepoint <= 0x9fu))
        {
            reason = "JSON_CONTROL_CHARACTER";
            return false;
        }
        char encoded[4];
        std::size_t length = 0;
        if (codepoint <= 0x7fu)
        {
            encoded[length++] = static_cast<char>(codepoint);
        }
        else if (codepoint <= 0x7ffu)
        {
            encoded[length++] = static_cast<char>(0xc0u | (codepoint >> 6));
            encoded[length++] = static_cast<char>(0x80u | (codepoint & 0x3fu));
        }
        else if (codepoint <= 0xffffu)
        {
            encoded[length++] = static_cast<char>(0xe0u | (codepoint >> 12));
            encoded[length++] = static_cast<char>(0x80u | ((codepoint >> 6) & 0x3fu));
            encoded[length++] = static_cast<char>(0x80u | (codepoint & 0x3fu));
        }
        else
        {
            encoded[length++] = static_cast<char>(0xf0u | (codepoint >> 18));
            encoded[length++] = static_cast<char>(0x80u | ((codepoint >> 12) & 0x3fu));
            encoded[length++] = static_cast<char>(0x80u | ((codepoint >> 6) & 0x3fu));
            encoded[length++] = static_cast<char>(0x80u | (codepoint & 0x3fu));
        }
        if (output.size() > m_maximumStringBytes ||
            length > m_maximumStringBytes - output.size())
        {
            reason = "JSON_STRING_LIMIT";
            return false;
        }
        output.append(encoded, length);
        return true;
    }

    bool ParseUnicodeEscape(std::uint32_t& codepoint, std::string& reason)
    {
        if (m_input.size() - m_offset < 4u)
        {
            reason = "JSON_UNICODE_ESCAPE_TRUNCATED";
            return false;
        }
        unsigned int first = 0;
        for (int i = 0; i < 4; ++i)
        {
            const int digit = HexDigit(m_input[m_offset++]);
            if (digit < 0)
            {
                reason = "JSON_UNICODE_ESCAPE_INVALID";
                return false;
            }
            first = (first << 4) | static_cast<unsigned int>(digit);
        }
        // A high surrogate must be followed immediately by a low surrogate.
        // Do not accept an unpaired UTF-16 code unit or silently replace it;
        // replacement would make two distinct authority payloads compare as
        // the same string at the snapshot boundary.
        if (first >= 0xd800u && first <= 0xdbffu)
        {
            if (m_input.size() - m_offset < 6u ||
                m_input[m_offset] != '\\' ||
                m_input[m_offset + 1] != 'u')
            {
                reason = "JSON_UNICODE_SURROGATE_INVALID";
                return false;
            }
            m_offset += 2;
            if (m_input.size() - m_offset < 4u)
            {
                reason = "JSON_UNICODE_ESCAPE_TRUNCATED";
                return false;
            }
            unsigned int second = 0;
            for (int i = 0; i < 4; ++i)
            {
                const int digit = HexDigit(m_input[m_offset++]);
                if (digit < 0)
                {
                    reason = "JSON_UNICODE_ESCAPE_INVALID";
                    return false;
                }
                second = (second << 4) | static_cast<unsigned int>(digit);
            }
            if (second < 0xdc00u || second > 0xdfffu)
            {
                reason = "JSON_UNICODE_SURROGATE_INVALID";
                return false;
            }
            codepoint = 0x10000u +
                ((first - 0xd800u) << 10) + (second - 0xdc00u);
            return true;
        }
        if (first >= 0xdc00u && first <= 0xdfffu)
        {
            reason = "JSON_UNICODE_SURROGATE_INVALID";
            return false;
        }
        codepoint = first;
        return true;
    }

    bool ParseRawUtf8(unsigned char first,
                      std::size_t start,
                      std::string& output,
                      std::string& reason)
    {
        std::size_t continuationCount = 0;
        if (first >= 0xc2u && first <= 0xdfu) continuationCount = 1;
        else if (first >= 0xe0u && first <= 0xefu) continuationCount = 2;
        else if (first >= 0xf0u && first <= 0xf4u) continuationCount = 3;
        else
        {
            reason = "JSON_UTF8_INVALID";
            return false;
        }
        if (m_input.size() - m_offset < continuationCount)
        {
            reason = "JSON_UTF8_INVALID";
            return false;
        }
        for (std::size_t i = 0; i < continuationCount; ++i)
        {
            if (!IsContinuation(static_cast<unsigned char>(m_input[m_offset + i])))
            {
                reason = "JSON_UTF8_INVALID";
                return false;
            }
        }
        const unsigned char second =
            static_cast<unsigned char>(m_input[m_offset]);
        // Exclude overlong encodings, UTF-16 surrogate encodings and values
        // above U+10FFFF at the byte sequence boundary.
        if ((first == 0xe0u && second < 0xa0u) ||
            (first == 0xedu && second >= 0xa0u) ||
            (first == 0xf0u && second < 0x90u) ||
            (first == 0xf4u && second > 0x8fu))
        {
            reason = "JSON_UTF8_INVALID";
            return false;
        }
        std::uint32_t codepoint =
            first & (continuationCount == 1u ? 0x1fu :
                     continuationCount == 2u ? 0x0fu : 0x07u);
        for (std::size_t i = 0; i < continuationCount; ++i)
            codepoint = (codepoint << 6) |
                (static_cast<unsigned char>(m_input[m_offset + i]) & 0x3fu);
        if (codepoint < 0x20u ||
            (codepoint >= 0x7fu && codepoint <= 0x9fu))
        {
            reason = "JSON_CONTROL_CHARACTER";
            return false;
        }
        m_offset += continuationCount;
        const std::size_t length = continuationCount + 1u;
        if (output.size() > m_maximumStringBytes ||
            length > m_maximumStringBytes - output.size())
        {
            reason = "JSON_STRING_LIMIT";
            return false;
        }
        output.append(m_input, start, length);
        return true;
    }

    void SkipWhitespace()
    {
        while (m_offset < m_input.size())
        {
  const char c = m_input[m_offset];
  if (c != ' ' && c != '\t' && c != '\r' && c != '\n') break;
  ++m_offset;
        }
    }

    bool Node(std::string& reason)
    {
        if (++m_nodes > m_maximumNodes)
        {
  reason = "JSON_NODE_LIMIT";
  return false;
        }
        return true;
    }

    bool Consume(char value)
    {
        if (m_offset >= m_input.size() || m_input[m_offset] != value)
  return false;
        ++m_offset;
        return true;
    }

    bool ParseString(std::string& output, std::string& reason)
    {
        if (!Consume('"'))
        {
  reason = "JSON_STRING_REQUIRED";
  return false;
        }
        output.clear();
        while (m_offset < m_input.size())
        {
  const std::size_t start = m_offset;
  const unsigned char c = static_cast<unsigned char>(m_input[m_offset++]);
  if (c == '"') return true;
  if (c < 0x20 || c == 0x7fu)
  {
      reason = "JSON_CONTROL_CHARACTER";
      return false;
  }
  if (c >= 0x80u)
  {
      if (!ParseRawUtf8(c, start, output, reason)) return false;
      if (output.size() > m_maximumStringBytes)
      {
          reason = "JSON_STRING_LIMIT";
          return false;
      }
      continue;
  }
  if (c != '\\')
  {
      output.push_back(static_cast<char>(c));
      if (output.size() > m_maximumStringBytes)
      {
          reason = "JSON_STRING_LIMIT";
          return false;
      }
      continue;
  }
  if (m_offset >= m_input.size())
  {
      reason = "JSON_ESCAPE_TRUNCATED";
      return false;
  }
  const char escaped = m_input[m_offset++];
  if (escaped == '"' || escaped == '\\' || escaped == '/')
      output.push_back(escaped);
  else if (escaped == 'b' || escaped == 'f' || escaped == 'n' ||
           escaped == 'r' || escaped == 't')
  {
      // Escaped C0 controls are still controls after JSON decoding; do not
      // let the short escape forms bypass the code-point validation below.
      reason = "JSON_CONTROL_CHARACTER";
      return false;
  }
  else if (escaped == 'u')
  {
      std::uint32_t codepoint = 0;
      if (!ParseUnicodeEscape(codepoint, reason) ||
          !AppendCodePoint(codepoint, output, reason))
          return false;
  }
  else
  {
      reason = "JSON_ESCAPE_INVALID";
      return false;
  }
  if (output.size() > m_maximumStringBytes)
  {
      reason = "JSON_STRING_LIMIT";
      return false;
  }
        }
        reason = "JSON_STRING_UNTERMINATED";
        return false;
    }

    bool ParseNumber(BoundedJsonValue& value, std::string& reason)
    {
        const std::size_t start = m_offset;
        if (m_offset < m_input.size() && m_input[m_offset] == '-') ++m_offset;
        if (m_offset >= m_input.size())
        {
  reason = "JSON_NUMBER_INVALID";
  return false;
        }
        if (m_input[m_offset] == '0')
        {
  ++m_offset;
  if (m_offset < m_input.size() &&
      m_input[m_offset] >= '0' && m_input[m_offset] <= '9')
  {
      reason = "JSON_NUMBER_INVALID";
      return false;
  }
        }
        else
        {
  if (m_input[m_offset] < '1' || m_input[m_offset] > '9')
  {
      reason = "JSON_NUMBER_INVALID";
      return false;
  }
  while (m_offset < m_input.size() &&
         m_input[m_offset] >= '0' && m_input[m_offset] <= '9')
      ++m_offset;
        }
        if (m_offset < m_input.size() && m_input[m_offset] == '.')
        {
  ++m_offset;
  const std::size_t digits = m_offset;
  while (m_offset < m_input.size() &&
         m_input[m_offset] >= '0' && m_input[m_offset] <= '9')
      ++m_offset;
  if (digits == m_offset)
  {
      reason = "JSON_NUMBER_INVALID";
      return false;
  }
        }
        if (m_offset < m_input.size() &&
  (m_input[m_offset] == 'e' || m_input[m_offset] == 'E'))
        {
  ++m_offset;
  if (m_offset < m_input.size() &&
      (m_input[m_offset] == '+' || m_input[m_offset] == '-'))
      ++m_offset;
  const std::size_t digits = m_offset;
  while (m_offset < m_input.size() &&
         m_input[m_offset] >= '0' && m_input[m_offset] <= '9')
      ++m_offset;
  if (digits == m_offset)
  {
      reason = "JSON_NUMBER_INVALID";
      return false;
  }
        }
        const std::string token = m_input.substr(start, m_offset - start);
        double parsed = 0.0;
        if (!ParseFiniteNumber(token, parsed))
        {
  reason = "JSON_NUMBER_NONFINITE";
  return false;
        }
        value.m_type = BoundedJsonValue::Type::Number;
        value.m_number = parsed;
        value.m_numberText = token;
        return true;
    }

    bool ParseArray(std::size_t depth,
          BoundedJsonValue& value,
          std::string& reason)
    {
        if (!Consume('[')) return false;
        value.m_type = BoundedJsonValue::Type::Array;
        SkipWhitespace();
        if (Consume(']')) return true;
        for (;;)
        {
  BoundedJsonValue member;
  if (!ParseValue(depth + 1, member, reason)) return false;
  value.m_array.push_back(member);
  SkipWhitespace();
  if (Consume(']')) return true;
  if (!Consume(','))
  {
      reason = "JSON_ARRAY_SEPARATOR_REQUIRED";
      return false;
  }
  SkipWhitespace();
        }
    }

    bool ParseObject(std::size_t depth,
           BoundedJsonValue& value,
           std::string& reason)
    {
        if (!Consume('{')) return false;
        value.m_type = BoundedJsonValue::Type::Object;
        SkipWhitespace();
        if (Consume('}')) return true;
        for (;;)
        {
  std::string key;
  if (!ParseString(key, reason)) return false;
  SkipWhitespace();
  if (!Consume(':'))
  {
      reason = "JSON_OBJECT_COLON_REQUIRED";
      return false;
  }
  SkipWhitespace();
  BoundedJsonValue member;
  if (!ParseValue(depth + 1, member, reason)) return false;
  if (!value.m_object.insert(std::make_pair(key, member)).second)
  {
      reason = "JSON_DUPLICATE_KEY";
      return false;
  }
  SkipWhitespace();
  if (Consume('}')) return true;
  if (!Consume(','))
  {
      reason = "JSON_OBJECT_SEPARATOR_REQUIRED";
      return false;
  }
  SkipWhitespace();
        }
    }

    bool ParseValue(std::size_t depth,
          BoundedJsonValue& value,
          std::string& reason)
    {
        if (depth > m_maximumDepth)
        {
  reason = "JSON_DEPTH_LIMIT";
  return false;
        }
        if (!Node(reason) || m_offset >= m_input.size())
        {
  if (reason.empty()) reason = "JSON_VALUE_REQUIRED";
  return false;
        }
        const char c = m_input[m_offset];
        if (c == '{') return ParseObject(depth, value, reason);
        if (c == '[') return ParseArray(depth, value, reason);
        if (c == '"')
        {
  value.m_type = BoundedJsonValue::Type::String;
  return ParseString(value.m_string, reason);
        }
        if (m_input.compare(m_offset, 4, "true") == 0)
        {
  m_offset += 4;
  value.m_type = BoundedJsonValue::Type::Boolean;
  value.m_boolean = true;
  return true;
        }
        if (m_input.compare(m_offset, 5, "false") == 0)
        {
  m_offset += 5;
  value.m_type = BoundedJsonValue::Type::Boolean;
  value.m_boolean = false;
  return true;
        }
        if (m_input.compare(m_offset, 4, "null") == 0)
        {
  m_offset += 4;
  value.m_type = BoundedJsonValue::Type::Null;
  return true;
        }
        return ParseNumber(value, reason);
    }

    const std::string& m_input;
    std::size_t m_maximumDepth;
    std::size_t m_maximumNodes;
    std::size_t m_maximumStringBytes;
    std::size_t m_offset = 0;
    std::size_t m_nodes = 0;
};

bool ParseBoundedJson(const std::string& input,
            BoundedJsonValue& value,
            std::string& reason,
            std::size_t maximumBytes,
            std::size_t maximumDepth,
            std::size_t maximumNodes)
{
    value = BoundedJsonValue();
    if (input.empty() || input.size() > maximumBytes ||
        maximumDepth == 0 || maximumNodes == 0)
    {
        reason = "JSON_INPUT_LIMIT";
        return false;
    }
    // Keep one hard ceiling even when a caller supplies a larger aggregate
    // byte limit.  This bounds decoded string allocations independently of
    // the number of values in the document.
    const std::size_t maximumStringBytes =
        maximumBytes < kMaximumJsonStringBytes ?
            maximumBytes : kMaximumJsonStringBytes;
    BoundedJsonParser parser(input, maximumDepth, maximumNodes,
                             maximumStringBytes);
    return parser.Parse(value, reason);
}
