#pragma once
#include "hepta/research/analytics.h"
#include "hepta/research/market_data.h"
#include "test_support.h"
#include <cstdint>
#include <limits>
#include <random>
#include <sstream>
#include <vector>

namespace portable_numeric_cases {
inline void RunAll() {
    using namespace hepta::research;
    using detail::ResearchWide;
    // Independent exact integer/dyadic oracles, including both signs. These
    // run even on x87 hosts where ResearchLedger uses native long double.
    for (int exponent : {-100, -10, 0, 10, 100}) {
        for (std::int64_t i = -37; i <= 37; ++i) {
            const std::int64_t j = i * 13 + 7;
            const double a = std::ldexp(static_cast<double>(i), exponent);
            const double b = std::ldexp(static_cast<double>(j), exponent);
            const ResearchWide x(a), y(b);
            Check(static_cast<double>(x + y) == std::ldexp(static_cast<double>(i + j), exponent),
                  "portable addition dyadic oracle");
            Check(static_cast<double>(x - y) == std::ldexp(static_cast<double>(i - j), exponent),
                  "portable subtraction dyadic oracle");
            Check(static_cast<double>(x * y) == std::ldexp(static_cast<double>(i * j), 2 * exponent),
                  "portable product dyadic oracle");
            Check(static_cast<double>((x + y) - y) == a, "portable reversible contribution");
            Check(static_cast<double>(x / ResearchWide(8)) == std::ldexp(a, -3),
                  "portable division dyadic oracle");
        }
    }
#if LDBL_MANT_DIG == 64
    // A genuinely separate native extended-precision oracle where available.
    // Products stay in the common exponent domain. The dyadic and ledger
    // oracles below remain active on narrow-long-double platforms as well.
    std::mt19937_64 generator(9129821);
    for (unsigned sample = 0; sample < 50000; ++sample) {
        const auto number = [&]() -> long double {
            return std::ldexp(static_cast<long double>(static_cast<std::int64_t>(generator())),
                              static_cast<int>(generator() % 801) - 463);
        };
        const long double a = number(), b = number();
        const ResearchWide x(a), y(b);
        Check(x + y == ResearchWide(a + b), "portable sum/native 64-bit oracle");
        Check(x - y == ResearchWide(a - b), "portable difference/native 64-bit oracle");
        Check(x * y == ResearchWide(a * b), "portable product/native 64-bit oracle");
        Check(x / y == ResearchWide(a / b), "portable quotient/native 64-bit oracle");
    }
#endif
    const ResearchWide huge(std::ldexp(1.0, 63));
    Check(static_cast<double>((huge + ResearchWide(1)) - huge) == 1,
          "64-bit significand retains integer at 2^63");
    const ResearchWide tooWide(std::ldexp(1.0, 64));
    Check(static_cast<double>((tooWide + ResearchWide(1)) - tooWide) == 0,
          "fixed 64-bit precision is not arbitrary exact arithmetic");
    Check(static_cast<double>(ResearchWide(1e200) - (ResearchWide(1e200) - ResearchWide(1))) != 1,
          "destructive rebase stays detectable");
    for (double price : {1.0, 2.0, 64.0, 128.0}) {
        for (double sample : {price, std::nextafter(price, 0.0),
                              std::nextafter(price, std::numeric_limits<double>::infinity())}) {
            const ResearchWide settlement(2 * price);
            Check(static_cast<double>(settlement - (settlement - ResearchWide(sample))) == sample,
                  "nearby entry survives ordinary settlement without tolerance");
        }
    }
    for (int exponent : {-1074, -1073, -1050, -1023, -1022, -1000, -10, 0, 1000, 1023}) {
        const double value = std::ldexp(1.0, exponent);
        for (double price : {value, std::nextafter(value, std::numeric_limits<double>::infinity())}) {
            Tick tick; tick.instrument = "NUMERIC"; tick.sequence = 1; tick.price = price;
            std::ostringstream encoded; WriteTicksCsv(encoded, {tick});
            std::istringstream input(encoded.str()); TickCsvReader reader(input); Tick decoded;
            Check(reader.Next(decoded) && decoded.price == price && !reader.Next(decoded),
                  "normal/subnormal Tick CSV exact round trip");
            Bar bar; bar.instrument = "NUMERIC"; bar.tradingDay = "20260921"; bar.endUs = 1;
            bar.open = bar.high = bar.low = bar.close = price; bar.tickCount = 1; bar.complete = true;
            std::ostringstream bars; WriteBarsCsv(bars, {bar}); std::istringstream source(bars.str());
            Check(ReadBarsCsv(source).at(0).close == price, "normal/subnormal Bar CSV exact round trip");
        }
    }
    const std::string header = "instrument,timestamp_us,sequence,price,volume\n";
    for (const std::string text : {"1e-9999", "1e309", "4.9406564584124654e-324x", "1e-310e",
                                    "1e-310 ", " 1e-310", "0x1p-1023", "nan", "inf", "+", ".", "1e+"}) {
        std::istringstream input(header + "NUMERIC,0,1," + text + ",0\n");
        TickCsvReader reader(input); Tick output; output.instrument = "sentinel";
        Throws([&] { reader.Next(output); });
        Check(reader.RowsRead() == 0 && output.instrument == "sentinel", "bad numeric row is atomic");
        input.clear(); Throws([&] { reader.Next(output); });
    }
    for (auto basis : {CostBasis::WeightedAverage, CostBasis::Fifo}) {
        ResearchLedger ledger("NUMERIC", 1000, 1, 8, basis);
        ResearchFill fill; fill.fillId = "open"; fill.orderId = "order"; fill.instrument = "NUMERIC";
        fill.timestampUs = 1; fill.side = 1; fill.quantity = 1; fill.price = 1;
        Check(ledger.Apply(fill), "portable ledger open");
        ResearchSettlement settlement; settlement.settlementId = "settle"; settlement.instrument = "NUMERIC";
        settlement.timestampUs = 2; settlement.price = 1e200;
        Throws([&] { ledger.Settle(settlement); });
        Check(ledger.Mark(2).equity == 1001, "destructive settlement did not mutate equity");
        settlement.price = 2;
        Check(ledger.Settle(settlement) && !ledger.Settle(settlement), "valid settlement and exact retry");
        Check(ledger.Mark(3).equity == 1002, "settlement preserves subsequent marked equity");
    }
}
} // namespace portable_numeric_cases
