#pragma once

#include <cmath>
#include <cstdint>
#include <string>

class HeptaFixedDecimal
{
public:
    typedef std::int64_t Rep;
    static const Rep kScale = 1000000;
    // Stay below 2^53 so a compatibility double preserves one-microunit
    // integer identity after canonical normalization.
    static const Rep kMaximumRaw = 9000000000000000LL;

    HeptaFixedDecimal() noexcept : m_raw(0) {}
    explicit HeptaFixedDecimal(Rep raw) noexcept : m_raw(raw) {}

    static bool ParseCanonical(
        const std::string& text,
        HeptaFixedDecimal& out,
        std::string& reason) noexcept;

    static bool FromDoubleExact(
        double value,
        HeptaFixedDecimal& out,
        std::string& reason) noexcept;

    static bool IsExactlyRepresentable(double value) noexcept
    {
        if (!std::isfinite(value)) return false;
        const long double scaled =
            static_cast<long double>(value) *
            static_cast<long double>(kScale);
        if (scaled < -static_cast<long double>(kMaximumRaw) ||
            scaled > static_cast<long double>(kMaximumRaw))
            return false;
        const long double nearest = std::round(scaled);
        return std::fabs(scaled - nearest) <= 0.000001L;
    }

    static bool CheckedAdd(
        HeptaFixedDecimal left,
        HeptaFixedDecimal right,
        HeptaFixedDecimal& out) noexcept;

    static bool CheckedSubtract(
        HeptaFixedDecimal left,
        HeptaFixedDecimal right,
        HeptaFixedDecimal& out) noexcept;

    Rep Raw() const noexcept { return m_raw; }
    double ToDouble() const noexcept
    {
        return static_cast<double>(m_raw) /
            static_cast<double>(kScale);
    }
    std::string ToCanonicalString() const;

    friend bool operator==(HeptaFixedDecimal left, HeptaFixedDecimal right)
    {
        return left.m_raw == right.m_raw;
    }
    friend bool operator!=(HeptaFixedDecimal left, HeptaFixedDecimal right)
    {
        return !(left == right);
    }
    friend bool operator<(HeptaFixedDecimal left, HeptaFixedDecimal right)
    {
        return left.m_raw < right.m_raw;
    }

private:
    Rep m_raw;
};
