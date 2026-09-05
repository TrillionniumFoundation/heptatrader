#pragma once

#include <cmath>
#include <cstdint>
#include <string>

class HeptaFixedDecimal
{
public:
    typedef std::int64_t Rep;
    static const Rep kScale = 1000000;
    // Fixed raw units are authoritative. Compatibility conversion to binary64
    // is explicitly fallible and must round-trip to the identical raw value.
    static const Rep kMaximumRaw = 9000000000000000LL;

    HeptaFixedDecimal() noexcept : m_raw(0) {}

    static bool ParseCanonical(
        const std::string& text,
        HeptaFixedDecimal& out,
        std::string& reason) noexcept;

    static bool FromRawExact(
        Rep raw,
        HeptaFixedDecimal& out,
        std::string& reason) noexcept;

    static bool FromDoubleExact(
        double value,
        HeptaFixedDecimal& out,
        std::string& reason) noexcept;

    static bool IsExactlyRepresentable(double value) noexcept;

    static bool CheckedAdd(
        HeptaFixedDecimal left,
        HeptaFixedDecimal right,
        HeptaFixedDecimal& out) noexcept;

    static bool CheckedSubtract(
        HeptaFixedDecimal left,
        HeptaFixedDecimal right,
        HeptaFixedDecimal& out) noexcept;

    bool IsValid() const noexcept
    {
        return m_raw >= -kMaximumRaw && m_raw <= kMaximumRaw;
    }
    Rep Raw() const noexcept { return m_raw; }

    // Produce a compatibility binary64 value only when it maps back to the
    // exact same microunit. This prevents adjacent high-magnitude fixed values
    // from collapsing onto one double at a wire/venue seam.
    bool ToDoubleExact(double& out, std::string& reason) const noexcept;
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
    explicit HeptaFixedDecimal(Rep raw, bool) noexcept : m_raw(raw) {}
    Rep m_raw;
};
