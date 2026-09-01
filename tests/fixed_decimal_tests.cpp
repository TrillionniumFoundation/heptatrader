#include "../HeptaTrade/numeric/fixed_decimal.h"

#include <cassert>
#include <limits>
#include <string>

namespace
{
HeptaFixedDecimal Parse(const char* value)
{
    HeptaFixedDecimal out;
    std::string reason;
    assert(HeptaFixedDecimal::ParseCanonical(value, out, reason));
    assert(reason.empty());
    return out;
}

void TestCanonicalVectors()
{
    assert(Parse("0").Raw() == 0);
    assert(Parse("1").Raw() == 1000000);
    assert(Parse("-1.25").Raw() == -1250000);
    assert(Parse("0.000001").Raw() == 1);
    assert(Parse("1e-6").Raw() == 1);
    assert(Parse("12.340000").ToCanonicalString() == "12.34");
    assert(Parse("-0.000001").ToCanonicalString() == "-0.000001");
}

void TestInvalidVectors()
{
    const char* invalid[] = {
        "", "+1", "01", ".1", "1.", "-0", "-0.0", "nan", "inf",
        "1e", "1.0000001", "9000000000.000001"
    };
    for (std::size_t i = 0; i < sizeof(invalid) / sizeof(invalid[0]); ++i)
    {
        HeptaFixedDecimal out;
        std::string reason;
        assert(!HeptaFixedDecimal::ParseCanonical(invalid[i], out, reason));
        assert(!reason.empty());
        assert(out.Raw() == 0);
    }
}

void TestDoubleBoundary()
{
    HeptaFixedDecimal out;
    std::string reason;
    assert(HeptaFixedDecimal::FromDoubleExact(0.1, out, reason));
    assert(out.Raw() == 100000);
    assert(out.ToCanonicalString() == "0.1");
    assert(!HeptaFixedDecimal::FromDoubleExact(0.1234567, out, reason));
    assert(reason == "NUMERIC_SCALE_MISMATCH");
    assert(!HeptaFixedDecimal::FromDoubleExact(
        std::numeric_limits<double>::infinity(), out, reason));
    assert(!HeptaFixedDecimal::FromDoubleExact(-0.0, out, reason));
}

void TestCheckedArithmetic()
{
    HeptaFixedDecimal result;
    assert(HeptaFixedDecimal::CheckedAdd(
        Parse("1.25"), Parse("2.75"), result));
    assert(result.ToCanonicalString() == "4");
    assert(HeptaFixedDecimal::CheckedSubtract(
        Parse("1.25"), Parse("2.75"), result));
    assert(result.ToCanonicalString() == "-1.5");

    const HeptaFixedDecimal maximum(HeptaFixedDecimal::kMaximumRaw);
    assert(!HeptaFixedDecimal::CheckedAdd(
        maximum, HeptaFixedDecimal(1), result));
    const HeptaFixedDecimal minimum(-HeptaFixedDecimal::kMaximumRaw);
    assert(!HeptaFixedDecimal::CheckedSubtract(
        minimum, HeptaFixedDecimal(1), result));
}
}

int main()
{
    TestCanonicalVectors();
    TestInvalidVectors();
    TestDoubleBoundary();
    TestCheckedArithmetic();
    return 0;
}
