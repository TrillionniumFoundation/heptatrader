#include "fixed_decimal.h"

#include <cerrno>
#include <cstdlib>
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

bool NormalizeScaled(
    long double scaled,
    HeptaFixedDecimal& out,
    std::string& reason) noexcept
{
    if (!std::isfinite(scaled))
    {
        reason = "NUMERIC_NONFINITE";
        return false;
    }
    if (scaled < -static_cast<long double>(HeptaFixedDecimal::kMaximumRaw) ||
        scaled > static_cast<long double>(HeptaFixedDecimal::kMaximumRaw))
    {
        reason = "NUMERIC_RANGE_EXCEEDED";
        return false;
    }
    const long double nearest = std::round(scaled);
    if (std::fabs(scaled - nearest) > 0.000001L)
    {
        reason = "NUMERIC_SCALE_MISMATCH";
        return false;
    }
    const HeptaFixedDecimal::Rep raw =
        static_cast<HeptaFixedDecimal::Rep>(nearest);
    out = HeptaFixedDecimal(raw == 0 ? 0 : raw);
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
    errno = 0;
    char* end = nullptr;
    const long double value = std::strtold(text.c_str(), &end);
    if (errno == ERANGE || end == text.c_str() || end == nullptr ||
        *end != '\0' || !std::isfinite(value))
    {
        reason = "NUMERIC_VALUE_INVALID";
        return false;
    }
    return NormalizeScaled(
        value * static_cast<long double>(kScale), out, reason);
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
    return NormalizeScaled(
        static_cast<long double>(value) *
            static_cast<long double>(kScale),
        out,
        reason);
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
