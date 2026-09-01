#include "fixed_decimal.h"

#include <iomanip>
#include <locale>
#include <sstream>

namespace
{
bool CanonicalFloatingGrammar(const std::string& value) noexcept
{
    if (value.empty() || value.size() > 64u) return false;
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
        const std::size_t fraction = offset;
        while (offset < value.size() && value[offset] >= '0' &&
               value[offset] <= '9') ++offset;
        if (offset == fraction) return false;
    }
    if (offset < value.size() &&
        (value[offset] == 'e' || value[offset] == 'E'))
    {
        ++offset;
        if (offset < value.size() &&
            (value[offset] == '+' || value[offset] == '-')) ++offset;
        const std::size_t exponent = offset;
        while (offset < value.size() && value[offset] >= '0' &&
               value[offset] <= '9') ++offset;
        if (offset == exponent) return false;
    }
    if (offset != value.size()) return false;
    if (negative)
    {
        bool zero = true;
        for (std::size_t i = 1; i < value.size(); ++i)
        {
            if (value[i] == 'e' || value[i] == 'E') break;
            if (value[i] == '.') continue;
            if (value[i] != '0')
            {
                zero = false;
                break;
            }
        }
        if (zero) return false;
    }
    return true;
}

long long ParseBoundedExponent(
    const std::string& text,
    std::size_t exponentOffset) noexcept
{
    if (exponentOffset == std::string::npos) return 0;
    std::size_t offset = exponentOffset + 1u;
    bool negative = false;
    if (text[offset] == '+' || text[offset] == '-')
    {
        negative = text[offset] == '-';
        ++offset;
    }
    // Values outside this bound are far beyond both the representable raw
    // range and the six-decimal scale. Saturating keeps the parser free from
    // integer overflow while preserving the correct fail-closed class.
    const long long limit = 1000000;
    long long value = 0;
    for (; offset < text.size(); ++offset)
    {
        const int digit = text[offset] - '0';
        if (value > (limit - digit) / 10)
        {
            value = limit;
            break;
        }
        value = value * 10 + digit;
    }
    return negative ? -value : value;
}

bool ParseExactRaw(
    const std::string& text,
    HeptaFixedDecimal::Rep& raw,
    std::string& reason) noexcept
{
    const bool negative = text[0] == '-';
    const std::size_t begin = negative ? 1u : 0u;
    const std::size_t exponentOffset = text.find_first_of("eE", begin);
    const std::size_t mantissaEnd = exponentOffset == std::string::npos
        ? text.size() : exponentOffset;
    const std::size_t decimalOffset = text.find('.', begin);
    const std::size_t fractionDigits =
        decimalOffset == std::string::npos || decimalOffset >= mantissaEnd
            ? 0u : mantissaEnd - decimalOffset - 1u;

    std::string digits;
    digits.reserve(mantissaEnd - begin);
    for (std::size_t offset = begin; offset < mantissaEnd; ++offset)
    {
        if (text[offset] != '.') digits.push_back(text[offset]);
    }
    const std::size_t first = digits.find_first_not_of('0');
    if (first == std::string::npos)
    {
        raw = 0;
        reason.clear();
        return true;
    }
    digits.erase(0, first);

    const long long exponent = ParseBoundedExponent(text, exponentOffset);
    const long long power = exponent -
        static_cast<long long>(fractionDigits) + 6LL;
    std::string rawDigits;
    if (power >= 0)
    {
        if (power > 32LL ||
            digits.size() + static_cast<std::size_t>(power) > 16u)
        {
            reason = "NUMERIC_RANGE_EXCEEDED";
            return false;
        }
        rawDigits = digits;
        rawDigits.append(static_cast<std::size_t>(power), '0');
    }
    else
    {
        const long long requiredZerosSigned = -power;
        if (requiredZerosSigned >
            static_cast<long long>(digits.size()))
        {
            reason = "NUMERIC_SCALE_MISMATCH";
            return false;
        }
        const std::size_t requiredZeros =
            static_cast<std::size_t>(requiredZerosSigned);
        for (std::size_t offset = digits.size() - requiredZeros;
             offset < digits.size(); ++offset)
        {
            if (digits[offset] != '0')
            {
                reason = "NUMERIC_SCALE_MISMATCH";
                return false;
            }
        }
        rawDigits.assign(digits, 0, digits.size() - requiredZeros);
    }

    const std::string maximum = "9000000000000000";
    if (rawDigits.empty() || rawDigits.size() > maximum.size() ||
        (rawDigits.size() == maximum.size() && rawDigits > maximum))
    {
        reason = rawDigits.empty()
            ? "NUMERIC_SCALE_MISMATCH"
            : "NUMERIC_RANGE_EXCEEDED";
        return false;
    }
    std::uint64_t magnitude = 0;
    for (std::size_t offset = 0; offset < rawDigits.size(); ++offset)
        magnitude = magnitude * 10u +
            static_cast<unsigned int>(rawDigits[offset] - '0');
    raw = negative
        ? -static_cast<HeptaFixedDecimal::Rep>(magnitude)
        : static_cast<HeptaFixedDecimal::Rep>(magnitude);
    reason.clear();
    return true;
}
}

bool HeptaFixedDecimal::ParseCanonical(
    const std::string& text,
    HeptaFixedDecimal& out,
    std::string& reason) noexcept
{
    out = HeptaFixedDecimal();
    if (!CanonicalFloatingGrammar(text))
    {
        reason = "NUMERIC_GRAMMAR_INVALID";
        return false;
    }
    Rep raw = 0;
    if (!ParseExactRaw(text, raw, reason)) return false;
    out = HeptaFixedDecimal(raw);
    return true;
}

bool HeptaFixedDecimal::FromDoubleExact(
    double value,
    HeptaFixedDecimal& out,
    std::string& reason) noexcept
{
    out = HeptaFixedDecimal();
    if (!std::isfinite(value))
    {
        reason = "NUMERIC_NONFINITE";
        return false;
    }
    if (value == 0.0 && std::signbit(value))
    {
        reason = "NUMERIC_NEGATIVE_ZERO";
        return false;
    }
    const long double scaled =
        static_cast<long double>(value) *
        static_cast<long double>(kScale);
    if (scaled < -static_cast<long double>(kMaximumRaw) ||
        scaled > static_cast<long double>(kMaximumRaw))
    {
        reason = "NUMERIC_RANGE_EXCEEDED";
        return false;
    }
    const long double nearest = std::round(scaled);
    if ((nearest == 0.0L && value != 0.0) ||
        std::fabs(scaled - nearest) > 0.000001L)
    {
        reason = "NUMERIC_SCALE_MISMATCH";
        return false;
    }
    const Rep raw = static_cast<Rep>(nearest);
    const double canonical = static_cast<double>(raw) /
        static_cast<double>(kScale);
    if (canonical != value)
    {
        reason = "NUMERIC_SCALE_MISMATCH";
        return false;
    }
    out = HeptaFixedDecimal(raw == 0 ? 0 : raw);
    reason.clear();
    return true;
}

bool HeptaFixedDecimal::CheckedAdd(
    HeptaFixedDecimal left,
    HeptaFixedDecimal right,
    HeptaFixedDecimal& out) noexcept
{
    if ((right.m_raw > 0 && left.m_raw > kMaximumRaw - right.m_raw) ||
        (right.m_raw < 0 && left.m_raw < -kMaximumRaw - right.m_raw))
        return false;
    out = HeptaFixedDecimal(left.m_raw + right.m_raw);
    return true;
}

bool HeptaFixedDecimal::CheckedSubtract(
    HeptaFixedDecimal left,
    HeptaFixedDecimal right,
    HeptaFixedDecimal& out) noexcept
{
    if ((right.m_raw < 0 && left.m_raw > kMaximumRaw + right.m_raw) ||
        (right.m_raw > 0 && left.m_raw < -kMaximumRaw + right.m_raw))
        return false;
    out = HeptaFixedDecimal(left.m_raw - right.m_raw);
    return true;
}

std::string HeptaFixedDecimal::ToCanonicalString() const
{
    if (m_raw == 0) return "0";
    const bool negative = m_raw < 0;
    const std::uint64_t magnitude = negative
        ? static_cast<std::uint64_t>(-(m_raw + 1)) + 1u
        : static_cast<std::uint64_t>(m_raw);
    const std::uint64_t whole =
        magnitude / static_cast<std::uint64_t>(kScale);
    const std::uint64_t fraction =
        magnitude % static_cast<std::uint64_t>(kScale);
    std::ostringstream out;
    out.imbue(std::locale::classic());
    if (negative) out << '-';
    out << whole;
    if (fraction != 0)
    {
        out << '.' << std::setw(6) << std::setfill('0') << fraction;
        std::string value = out.str();
        while (!value.empty() && value[value.size() - 1] == '0')
            value.erase(value.size() - 1);
        return value;
    }
    return out.str();
}
