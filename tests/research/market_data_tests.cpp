#include "hepta/research/market_data.h"
#include "test_support.h"
#include <algorithm>
#include <locale>
#include <utility>
#include <limits>
#include <map>
#include <sstream>
using namespace hepta::research;
namespace {
SessionWindow Window(std::int64_t a, std::int64_t b, const std::string& day) {
    SessionWindow w; w.openUs = a; w.closeUs = b; w.tradingDay = day; return w;
}
Tick T(std::int64_t time, std::uint64_t seq, double price, std::int64_t volume = 1) {
    Tick t; t.instrument = "TEST.FUT"; t.timestampUs = time; t.sequence = seq; t.price = price; t.volume = volume; return t;
}
void SessionsAndBars() {
    SessionSchedule s({Window(0, 30, "20260921"), Window(40, 65, "20260921"), Window(80, 100, "20260922")});
    Check(s.At(0).openUs == 0 && s.At(29).closeUs == 30, "session inclusivity");
    Throws([&] { s.At(30); }); Throws([&] { s.At(39); }); Throws([&] { s.At(100); });
    Throws([] { SessionSchedule s({Window(10, 20, "20260921"), Window(19, 30, "20260921")}); });
    Throws([] { SessionSchedule s({Window(0, 20, "20260229")}); });
    ValidateTradingDay("20000229"); Throws([] { ValidateTradingDay("19000229"); });
    BarBuilder b("TEST.FUT", 10, s); Bar out, partial;
    Check(b.Push(T(1, 1, 10, 3), out) == TickOutcome::Updated, "first tick");
    b.Push(T(2, 2, 12, 2), out); b.Push(T(9, 3, 9, 1), out);
    Check(b.Push(T(9, 3, 9, 1), out) == TickOutcome::Duplicate, "duplicate");
    Throws([&] { b.Push(T(9, 3, 9, 2), out); });
    Throws([&] { b.Push(T(8, 4, 11), out); });
    Check(b.Push(T(10, 4, 11), out) == TickOutcome::ClosedPrevious, "bar closure");
    Check(out.complete && out.beginUs == 0 && out.endUs == 10 && out.volume == 6 && out.tickCount == 3, "bar counts");
    Near(out.open, 10); Near(out.high, 12); Near(out.low, 9); Near(out.close, 9);
    Check(b.Current(partial) && !partial.complete, "partial flag");
    Check(!b.AdvanceWatermark(19, out), "no premature finalization");
    Throws([&] { b.Push(T(18, 5, 12), out); });
    Check(b.AdvanceWatermark(20, out), "watermark closes");
    Check(!b.AdvanceWatermark(20, out), "watermark idempotency");
    Throws([&] { b.AdvanceWatermark(19, out); });
    b.Push(T(64, 5, 15), out); Check(b.Current(partial) && partial.beginUs == 60 && partial.endUs == 65, "session clipped bucket");
    Check(b.AdvanceWatermark(65, out), "clipped close");
    BarBuilder daily("TEST.FUT", 0, s);
    daily.Push(T(1, 1, 10), out); daily.Push(T(41, 2, 12), out);
    Check(daily.Current(partial) && partial.beginUs == 0 && partial.endUs == 65 && partial.volume == 2, "daily night/day join");
    Check(daily.Push(T(81, 3, 11), out) == TickOutcome::ClosedPrevious && out.tradingDay == "20260921", "trading-day rollover");
    const auto max = std::numeric_limits<std::int64_t>::max();
    BarBuilder edge("TEST.FUT", max, SessionSchedule({Window(max - 100, max, "20260921")}));
    edge.Push(T(max - 1, 1, 1), out); Check(edge.AdvanceWatermark(max, out) && out.endUs == max, "timestamp overflow guard");
    BarBuilder vol("TEST.FUT", 10, s); vol.Push(T(1, 1, 1, max), out);
    Throws([&] { vol.Push(T(2, 2, 1, 1), out); });
    Check(vol.Current(partial) && partial.volume == max && partial.tickCount == 1, "overflow atomicity");
    vol.Push(T(2, 2, 1, 0), out); Check(vol.Current(partial) && partial.tickCount == 2, "rejected input did not consume sequence");
}
void CsvAndCumulative() {
    std::vector<Tick> input{T(0, 1, 1.1000000000000001, 0), T(10, 2, 2.7, 5)};
    std::ostringstream encoded; WriteTicksCsv(encoded, input);
    std::istringstream decoded(encoded.str()); const auto out = ReadTicksCsv(decoded);
    Check(out.size() == 2 && out[0].price == input[0].price && out[1].volume == 5, "CSV round trip");
    const std::string header = "instrument,timestamp_us,sequence,price,volume\n";
    for (const auto& row : {"A,0,1,nan,1", "A,-1,1,2,1", "A,0,1,2,1,extra", "A,0,1,2,-1", "A,0,0,2,1", "A,0,1,2,18446744073709551616", "A,0,1,2junk,1"})
        Throws([&] { std::istringstream bad(header + row); ReadTicksCsv(bad); });
    Throws([&] { std::istringstream bad(header + std::string(4097, 'x')); ReadTicksCsv(bad); });
    Throws([&] { std::istringstream bad(encoded.str()); ReadTicksCsv(bad, 1); });
    std::istringstream sessions("open_us,close_us,trading_day\r\n0,100,20260921\r\n");
    Check(ReadSessionsCsv(sessions).At(99).closeUs == 100, "session CSV");
    CumulativeVolumeDecoder cv("TEST.FUT");
    Check(cv.Decode(T(0, 1, 10, 100), "20260921").volume == 0, "first counter baseline, no invented volume");
    Check(cv.Decode(T(1, 2, 11, 105), "20260921").volume == 5, "counter delta");
    Check(cv.Decode(T(1, 2, 11, 105), "20260921").volume == 5, "decoder duplicate stable");
    Throws([&] { cv.Decode(T(2, 3, 11, 104), "20260921"); });
    Check(cv.Decode(T(2, 3, 11, 106), "20260921").volume == 1, "counter rollback after rejection");
    Check(cv.Decode(T(3, 4, 12, 2), "20260922").volume == 0, "new-day baseline");
    CumulativeVolumeDecoder complete("TEST.FUT", true);
    Check(complete.Decode(T(0, 1, 10, 3), "20260921").volume == 3, "explicit complete capture");
}
void SeriesAndOracle() {
    SessionSchedule s({Window(0, 10000, "20260921")});
    BarBuilder b("TEST.FUT", 10, s); Bar closed;
    std::vector<Bar> bars;
    for (int i = 0; i < 1000; ++i) {
        const auto result = b.Push(T(i, i + 1, 100 + (i * 17) % 23, i % 7), closed);
        if (result == TickOutcome::ClosedPrevious) bars.push_back(closed);
    }
    Check(b.AdvanceWatermark(1000, closed), "oracle close"); bars.push_back(closed);
    Check(bars.size() == 100, "bar count");
    BarSeries series(100);
    for (int j = 0; j < 100; ++j) {
        double hi = 0, lo = 1000; long volume = 0;
        for (int i = j * 10; i < j * 10 + 10; ++i) { const double p = 100 + (i * 17) % 23; hi = std::max(hi, p); lo = std::min(lo, p); volume += i % 7; }
        const auto& x = bars[j]; Near(x.high, hi); Near(x.low, lo); Check(x.volume == volume && x.tickCount == 10, "independent OHLC oracle");
        series.Append(x);
    }
    Check(MergeBars(bars).tickCount == 1000, "merge volume/count preservation");
    Throws([&] { series.Highest(2, 1); }); Throws([&] { series.MeanClose(0); });
    Bar replacement = series.At(0); replacement.high = 999;
    series.Replace(replacement); Check(series.Highest(0, 99) == 0, "series replacement");
    series.EraseAfter(20); Check(series.Size() == 3, "erase after");
    series.EraseBefore(10); Check(series.Size() == 2, "erase before");
    BarSeries bounded(1); bounded.Append(bars[0]); bounded.Append(bars[1]); Check(bounded.At(0).beginUs == 10, "bounded retention");
    BarSeries ties(3); auto a = bars[0]; a.high = 999; auto c = bars[1]; c.high = 999; ties.Append(a); ties.Append(c);
    Check(ties.Highest(0, 1) == 1 && ties.Highest(0, 1, false) == 0, "tie semantics");
    auto malformed = bars[0]; malformed.high = 1; Throws([&] { ValidateBar(malformed); });
}
}
namespace {
Bar QueryBar(std::int64_t i, double open, double high, double low, double close) {
    Bar b; b.instrument = "TEST.FUT"; b.tradingDay = "20260921";
    b.beginUs = i * 10; b.endUs = (i + 1) * 10;
    b.open = open; b.high = high; b.low = low; b.close = close;
    b.volume = i; b.tickCount = 1; b.complete = true; return b;
}
void RejectCode(const std::function<void()>& fn, const std::string& code) {
    try { fn(); } catch (const std::invalid_argument& e) {
        Check(e.what() == code, "unexpected rejection code"); return;
    }
    throw std::runtime_error("expected explicit rejection " + code);
}
void QueryBoundaries() {
    BarSeries empty(4);
    Check(empty.RetainedBarsInLatestTradingDay() == 0, "empty retained day");
    RejectCode([&] { empty.AtLatest(); }, "RESEARCH_SERIES_RANGE_INVALID");
    RejectCode([&] { empty.ConfirmedPeaks(0, 0, 1); }, "RESEARCH_SERIES_RANGE_INVALID");
    BarSeries s(8);
    for (int i = 0; i < 5; ++i) {
        const double prices[] = {2, 5, 2, 4, 3};
        s.Append(QueryBar(i, prices[i], prices[i], prices[i], prices[i]));
    }
    Check(s.AtLatest().beginUs == 40 && s.AtLatest(4).beginUs == 0, "reverse indexing");
    RejectCode([&] { s.AtLatest(5); }, "RESEARCH_SERIES_RANGE_INVALID");
    Check(s.LatestHigher(2, 0, 4).index == 4, "latest above is not highest");
    Check(!s.LatestHigher(5, 0, 4).found, "strict higher excludes equality");
    Check(s.LatestLower(3, 0, 4).index == 2, "latest below");
    Check(!s.LatestLower(2, 0, 4).found, "strict lower excludes equality");
    Check(s.LatestHigher(0, 0, 0).found && !s.LatestLower(0, 0, 0).found, "index zero no underflow");
    Check(s.ConfirmedPeaks(0, 1, 1).empty(), "no early peak without right bar");
    auto peaks = s.ConfirmedPeaks(0, 2, 1);
    Check(peaks.size() == 1 && peaks[0].index == 1 && peaks[0].beginUs == 10 &&
          peaks[0].confirmedAtUs == 30, "peak known at confirming close, not pivot");
    Check(s.ConfirmedTroughs(0, 2, 1).empty(), "unconfirmed trailing trough");
    auto troughs = s.ConfirmedTroughs(0, 3, 1);
    Check(troughs.size() == 1 && troughs[0].index == 2 && troughs[0].confirmedAtUs == 40, "trough confirmation");
    Check(s.ConfirmedPeaks(0, 4, 0).size() == 5, "radius zero");
    Check(s.ConfirmedPeaks(0, 4, std::numeric_limits<std::size_t>::max()).empty(), "radius overflow");
    RejectCode([&] { s.Highest(2, 1, BarPrice::Close); }, "RESEARCH_SERIES_RANGE_INVALID");
    RejectCode([&] { s.LatestHigher(std::numeric_limits<double>::quiet_NaN(), 0, 4); }, "RESEARCH_THRESHOLD_INVALID");
    RejectCode([&] { s.LatestLower(std::numeric_limits<double>::infinity(), 0, 4); }, "RESEARCH_THRESHOLD_INVALID");
    const auto invalidField = static_cast<BarPrice>(999);
    RejectCode([&] { s.Highest(0, 4, invalidField); }, "RESEARCH_BAR_PRICE_FIELD_INVALID");
    RejectCode([&] { s.Lowest(0, 0, invalidField); }, "RESEARCH_BAR_PRICE_FIELD_INVALID");
    RejectCode([&] { s.LatestHigher(100, 0, 4, invalidField); }, "RESEARCH_BAR_PRICE_FIELD_INVALID");
    RejectCode([&] { s.ConfirmedPeaks(0, 0, 100, invalidField); }, "RESEARCH_BAR_PRICE_FIELD_INVALID");
    auto replacement = QueryBar(1, 2, 2, 2, 2); s.Replace(replacement);
    Check(s.ConfirmedPeaks(0, 2, 1).empty(), "replace invalidates a strict peak, plateau not a peak");
    Check(s.Highest(0, 2, BarPrice::Close) == 2 && s.Highest(0, 2, BarPrice::Close, false) == 0,
          "typed tie handling");
    BarSeries days(2); auto b = QueryBar(0, 1, 1, 1, 1); days.Append(b);
    b = QueryBar(1, 1, 1, 1, 1); days.Append(b);
    b = QueryBar(2, 1, 1, 1, 1); days.Append(b);
    Check(days.RetainedBarsInLatestTradingDay() == 2, "retained count not fictional full-day count");
    b = QueryBar(3, 1, 1, 1, 1); b.tradingDay = "20260922"; days.Append(b);
    Check(days.RetainedBarsInLatestTradingDay() == 1, "day label rollover");
    days.EraseAfter(20); Check(days.RetainedBarsInLatestTradingDay() == 1, "erase recomputes latest retained day");
    days.EraseBefore(40); Check(days.RetainedBarsInLatestTradingDay() == 0, "erased series");
}
double OraclePrice(const Bar& b, int field) {
    const double values[] = {b.open, b.high, b.low, b.close}; return values[field];
}
void QueryOracle() {
    // Full stored series deliberately contains future bars. Every query is also
    // checked against an independently built prefix with no future observations.
    std::size_t rangeCases = 0, extremaCases = 0;
    for (int seed = 0; seed < 24; ++seed) {
        std::vector<Bar> data;
        BarSeries full(18);
        for (int i = 0; i < 17; ++i) {
            const double open = 20 + ((seed * 13 + i * 7 + i * i) % 9);
            const double close = 20 + ((seed * 3 + i * 11) % 9);
            data.push_back(QueryBar(i, open, std::max(open, close) + i % 3,
                                   std::min(open, close) - (seed + i) % 3, close));
            full.Append(data.back());
        }
        BarSeries prefix(18);
        for (std::size_t end = 0; end < data.size(); ++end) {
            prefix.Append(data[end]);
            for (std::size_t begin = 0; begin <= end; ++begin) {
                for (int f = 0; f < 4; ++f) {
                    const auto field = static_cast<BarPrice>(f);
                    for (bool latest : {false, true}) {
                        auto hi = begin, lo = begin;
                        for (auto i = begin; i <= end; ++i) {
                            const auto p = OraclePrice(data[i], f);
                            if (p > OraclePrice(data[hi], f) || (latest && p == OraclePrice(data[hi], f))) hi = i;
                            if (p < OraclePrice(data[lo], f) || (latest && p == OraclePrice(data[lo], f))) lo = i;
                        }
                        Check(full.Highest(begin, end, field, latest) == hi &&
                              full.Lowest(begin, end, field, latest) == lo, "field extrema oracle");
                        ++rangeCases;
                    }
                    bool above = false, below = false; std::size_t ai = 0, bi = 0;
                    const double threshold = 20 + seed % 9;
                    for (auto i = begin; i <= end; ++i) {
                        const double p = OraclePrice(data[i], f);
                        if (p > threshold) { above = true; ai = i; }
                        if (p < threshold) { below = true; bi = i; }
                    }
                    const auto a = full.LatestHigher(threshold, begin, end, field);
                    const auto b = full.LatestLower(threshold, begin, end, field);
                    Check(a.found == above && b.found == below, "crossing found oracle");
                    if (above) Check(a.index == ai && a.beginUs == data[ai].beginUs && a.price == OraclePrice(data[ai], f), "higher oracle");
                    if (below) Check(b.index == bi && b.beginUs == data[bi].beginUs && b.price == OraclePrice(data[bi], f), "lower oracle");
                    for (std::size_t radius = 0; radius < 5; ++radius) {
                        for (bool peak : {false, true}) {
                            std::vector<std::size_t> expected;
                            for (auto i = begin; i <= end; ++i) {
                                if (i - begin < radius || end - i < radius) continue;
                                bool match = true;
                                for (std::size_t d = 1; d <= radius; ++d) {
                                    const auto p = OraclePrice(data[i], f);
                                    const auto l = OraclePrice(data[i - d], f), r = OraclePrice(data[i + d], f);
                                    if (peak ? (p <= l || p <= r) : (p >= l || p >= r)) match = false;
                                }
                                if (match) expected.push_back(i);
                            }
                            const auto got = peak ? full.ConfirmedPeaks(begin, end, radius, field)
                                                  : full.ConfirmedTroughs(begin, end, radius, field);
                            const auto causal = peak ? prefix.ConfirmedPeaks(begin, end, radius, field)
                                                     : prefix.ConfirmedTroughs(begin, end, radius, field);
                            Check(got.size() == expected.size() && causal.size() == got.size(), "confirmed extrema count oracle");
                            for (std::size_t j = 0; j < got.size(); ++j) {
                                const auto i = expected[j];
                                Check(got[j].index == i && got[j].beginUs == data[i].beginUs &&
                                      got[j].price == OraclePrice(data[i], f) &&
                                      got[j].confirmedAtUs == data[i + radius].endUs &&
                                      got[j].confirmedAtUs <= data[end].endUs, "causal confirmation oracle");
                                Check(causal[j].index == got[j].index && causal[j].confirmedAtUs == got[j].confirmedAtUs,
                                      "future suffix must not change observed query");
                            }
                            ++extremaCases;
                        }
                    }
                }
            }
        }
    }
    Check(rangeCases == 29376 && extremaCases == 146880, "query oracle coverage");
    std::cout << "query_oracle_ranges=" << rangeCases << " extrema_cases=" << extremaCases << '\n';
}
struct CommaDecimal : std::numpunct<char> {
    char do_decimal_point() const override { return ','; }
};
void BarCsvContract() {
    auto first = QueryBar(0, 1.1000000000000001, 2.7, 0.5, 2.6);
    auto second = QueryBar(1, 9, 9, 9, 9); second.tradingDay = "20260922";
    second.volume = std::numeric_limits<std::int64_t>::max();
    second.tickCount = std::numeric_limits<std::uint64_t>::max();
    std::ostringstream out; out.imbue(std::locale(std::locale::classic(), new CommaDecimal));
    WriteBarsCsv(out, {first, second});
    std::istringstream in(out.str()); in.imbue(out.getloc());
    const auto round = ReadBarsCsv(in);
    Check(round.size() == 2 && round[0].open == first.open && round[0].close == first.close &&
          round[0].high == first.high && round[0].low == first.low && round[0].beginUs == first.beginUs &&
          round[1].tickCount == second.tickCount && round[1].volume == second.volume &&
          round[1].tradingDay == second.tradingDay && round[1].complete, "bar CSV exact round trip, classic locale");
    for (double extreme : {std::numeric_limits<double>::denorm_min(), std::numeric_limits<double>::min(),
                           std::numeric_limits<double>::max()}) {
        auto bar = QueryBar(0, extreme, extreme, extreme, extreme);
        std::ostringstream text; WriteBarsCsv(text, {bar});
        std::istringstream source(text.str()); Check(ReadBarsCsv(source)[0].close == extreme, "finite double round trip");
    }
    const std::string header = out.str().substr(0, out.str().find('\n') + 1);
    std::istringstream empty(header); Check(ReadBarsCsv(empty).empty(), "empty dataset is explicit");
    const std::string valid = "TEST.FUT,20260921,0,10,1,2,1,2,0,1,1";
    std::istringstream noNewline(header + valid); Check(ReadBarsCsv(noNewline).size() == 1, "last row without newline");
    std::string crlf = header; crlf.insert(crlf.size() - 1, "\r"); crlf += valid + "\r\n";
    std::istringstream windows(crlf); Check(ReadBarsCsv(windows).size() == 1, "CRLF bars");
    const std::vector<std::string> cells = {"TEST.FUT", "20260921", "0", "10", "1", "2", "1", "2", "0", "1", "1"};
    const std::vector<std::pair<std::size_t, std::string>> mutations = {
        {0,"BAD SYMBOL"}, {1,"20260229"}, {2,"-1"}, {2,"9223372036854775808"}, {3,"0"},
        {4,"nan"}, {4,"inf"}, {4," 1"}, {4,"1 "}, {4,"1junk"}, {4,"0"}, {4,"1e309"},
        {5,"0.5"}, {6,"3"}, {7,"3"}, {8,"-1"}, {8,"9223372036854775808"},
        {9,"0"}, {9,"18446744073709551616"}, {10,"0"}, {10,"true"}, {10,"2"}};
    for (const auto& m : mutations) {
        auto bad = cells; bad[m.first] = m.second;
        std::string row; for (const auto& cell : bad) { if (!row.empty()) row += ','; row += cell; }
        Throws([&] { std::istringstream source(header + row); ReadBarsCsv(source); });
    }
    for (const std::string& row : {valid + ",extra", valid.substr(0, valid.size() - 2), std::string(4097, 'x'), std::string()})
        Throws([&] { std::istringstream source(header + row + "\n"); ReadBarsCsv(source); });
    Throws([&] { std::istringstream source("bad header\n" + valid); ReadBarsCsv(source); });
    Throws([&] { std::istringstream source(header + valid); ReadBarsCsv(source, 0); });
    Throws([&] { std::istringstream source(out.str()); ReadBarsCsv(source, 1); });
    Throws([&] { std::istringstream source(header + valid + "\n" + valid); ReadBarsCsv(source); });
    for (int mode = 0; mode < 4; ++mode) {
        auto bad = second;
        if (mode == 0) bad.instrument = "OTHER";
        if (mode == 1) bad.tradingDay = "20260920";
        if (mode == 2) bad.beginUs = 5;
        if (mode == 3) bad.complete = false;
        std::ostringstream untouched; untouched << "sentinel";
        Throws([&] { WriteBarsCsv(untouched, {first, bad}); });
        Check(untouched.str() == "sentinel", "invalid bars do not write a prefix");
    }
    std::ostringstream failedOutput; failedOutput.setstate(std::ios::badbit);
    Throws([&] { WriteBarsCsv(failedOutput, {first}); });
    std::istringstream failedInput(header); failedInput.setstate(std::ios::badbit);
    Throws([&] { ReadBarsCsv(failedInput); });
}
}

namespace {
void BoundedMeans() {
    // A mean of finite positive observations is bounded by its inputs. In
    // particular, seven DBL_MAX observations must not overflow from rounding
    // separately divided terms. Constant subnormals must not become zero.
    for (double price : {std::numeric_limits<double>::denorm_min(),
                         std::numeric_limits<double>::min(), 0.1, 1.0,
                         std::numeric_limits<double>::max()}) {
        BarSeries series(257);
        for (int i = 0; i < 513; ++i) {
            series.Append(QueryBar(i, price, price, price, price));
            Check(series.MeanClose(series.Size()) == price, "constant mean preserves value");
            Check(series.MeanClose(1) == price, "single-observation mean");
            if (series.Size() >= 7)
                Check(series.MeanClose(7) == price, "constant suffix mean after retention");
        }
    }
    // Independent integer-sum oracle, scaled over the finite double range.
    // Include nonconstant suffixes so constant-value special-casing cannot pass.
    for (int exponent : {-1000, -500, 0, 500, 1000}) {
        const double scale = std::ldexp(1.0, exponent);
        BarSeries series(257);
        std::deque<unsigned> units;
        for (int i = 0; i < 513; ++i) {
            const unsigned value = 1 + static_cast<unsigned>((i * 17) % 23);
            const double price = value * scale;
            series.Append(QueryBar(i, price, price, price, price));
            units.push_back(value);
            if (units.size() > 257) units.pop_front();
            for (std::size_t count : {std::size_t(1), std::min(std::size_t(7), units.size()), units.size()}) {
                unsigned sum = 0;
                for (std::size_t j = units.size() - count; j < units.size(); ++j) sum += units[j];
                const long double expected = static_cast<long double>(sum) / count;
                const long double actual = static_cast<long double>(series.MeanClose(count)) / scale;
                Check(std::fabs(actual - expected) <= expected * 8 * std::numeric_limits<double>::epsilon(),
                      "scaled suffix mean oracle");
            }
        }
    }
}
}
int main() { return Run([] { SessionsAndBars(); CsvAndCumulative(); SeriesAndOracle();
    QueryBoundaries(); QueryOracle(); BarCsvContract(); BoundedMeans(); }); }
