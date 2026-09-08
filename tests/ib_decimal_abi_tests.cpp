#include "Decimal.h"

#include <cmath>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <string>

namespace {
void Require(bool condition, const char* message) {
    if (condition) return;
    std::cerr << "IB Decimal ABI failure: " << message << '\n';
    std::exit(1);
}
}

int main() {
    static_assert(sizeof(Decimal) == 8, "IB Decimal must be 64-bit BID");
    // IEEE decimal64 BID interchange constants, independent of this library's
    // encoder. A binary64 bit-copy shim cannot pass these interoperability cases.
    const Decimal one = 0x31c0000000000001ULL;
    const Decimal tenth = 0x31a0000000000001ULL;
    const Decimal negativeOne = 0xb1c0000000000001ULL;
    Require(DecimalFunctions::decimalToDouble(one) == 1.0, "BID64 one decode");
    Require(DecimalFunctions::decimalToDouble(tenth) == 0.1, "BID64 tenth decode");
    Require(DecimalFunctions::decimalToDouble(negativeOne) == -1.0, "BID64 negative decode");
    Require(DecimalFunctions::stringToDecimal("1") == one, "BID64 one encode");
    Require(DecimalFunctions::stringToDecimal("0.1") == tenth, "BID64 fractional encode");
    Require(DecimalFunctions::stringToDecimal("-1") == negativeOne, "BID64 negative encode");
    const Decimal twoTenths = DecimalFunctions::stringToDecimal("0.2");
    Require(DecimalFunctions::decimalToDouble(DecimalFunctions::add(tenth, twoTenths)) == 0.3,
            "decimal addition");
    Require(DecimalFunctions::decimalToDouble(DecimalFunctions::sub(one, tenth)) == 0.9,
            "decimal subtraction");
    Require(DecimalFunctions::decimalToDouble(DecimalFunctions::mul(tenth, twoTenths)) == 0.02,
            "decimal multiplication");
    Require(DecimalFunctions::decimalToDouble(DecimalFunctions::div(one, twoTenths)) == 5.0,
            "decimal division");
    for (double quantity : {0.0, 0.1, 1.25, 1000.0, -2.5}) {
        const Decimal encoded = DecimalFunctions::doubleToDecimal(quantity);
        Require(DecimalFunctions::decimalToDouble(encoded) == quantity, "binary/decimal roundtrip");
        const std::string wire = DecimalFunctions::decimalToString(encoded);
        Require(!wire.empty() && DecimalFunctions::decimalToDouble(
                    DecimalFunctions::stringToDecimal(wire)) == quantity,
                "SDK wire-string roundtrip");
    }
    unsigned int flags = 0;
    Require(__bid64_to_binary64(tenth, 1, &flags) < 0.1 && flags != 0,
            "explicit rounding argument and local exception flags ABI");
    flags = 0;
    const Decimal infinity = __bid64_div(one, DecimalFunctions::stringToDecimal("0"), 0, &flags);
    Require(flags != 0 && std::isinf(DecimalFunctions::decimalToDouble(infinity)),
            "division by zero preserves infinity and exception flag");
    char invalidText[] = "not-a-number";
    flags = 0;
    const Decimal invalid = __bid64_from_string(invalidText, 0, &flags);
    Require(std::isnan(DecimalFunctions::decimalToDouble(invalid)),
            "malformed decimal never becomes numeric zero");
    Require(DecimalFunctions::decimalToString(invalid).find("NaN") != std::string::npos,
            "invalid decimal remains invalid on wire");
    Require(std::isnan(DecimalFunctions::decimalToDouble(UNSET_DECIMAL)),
            "SDK unset sentinel never becomes numeric zero");
    Require(std::isinf(DecimalFunctions::decimalToDouble(DecimalFunctions::doubleToDecimal(
                std::numeric_limits<double>::infinity()))), "infinity conversion");
    Require(std::isnan(DecimalFunctions::decimalToDouble(DecimalFunctions::doubleToDecimal(
                std::numeric_limits<double>::quiet_NaN()))), "NaN conversion");
    std::cout << "ib_decimal_abi_tests: PASS\n";
    return 0;
}
