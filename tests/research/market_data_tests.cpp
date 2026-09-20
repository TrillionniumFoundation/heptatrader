#include "hepta/research/market_data.h"
#include "test_support.h"
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
int main() { return Run([] { SessionsAndBars(); CsvAndCumulative(); SeriesAndOracle(); }); }
