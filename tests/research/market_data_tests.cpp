#include "hepta/research/market_data.h"
#include "test_support.h"
#include <algorithm>
#include <iomanip>
#include <locale>
#include <utility>
#include <limits>
#include <map>
#include <sstream>
#include <type_traits>
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
// Generates a non-seekable stream lazily; neither the fixture nor the reader
// has a vector of historical ticks. Underflow exposes at most one row.
class GeneratedTickBuffer : public std::streambuf {
public:
    explicit GeneratedTickBuffer(std::size_t rows, std::string instrument = "TEST.FUT",
                                 std::size_t stride = 1, std::size_t offset = 0)
        : rows_(rows), instrument_(std::move(instrument)), stride_(stride), offset_(offset) {
        line_ = "instrument,timestamp_us,sequence,price,volume\n";
        setg(&line_[0], &line_[0], &line_[0] + line_.size());
    }
    std::size_t Produced() const { return produced_; }
protected:
    int_type underflow() override {
        if (gptr() < egptr()) return traits_type::to_int_type(*gptr());
        if (produced_ == rows_) return traits_type::eof();
        ++produced_;
        line_ = instrument_ + "," + std::to_string(offset_ + (produced_ - 1) * stride_) + "," +
                std::to_string(produced_) + ",100,1\n";
        setg(&line_[0], &line_[0], &line_[0] + line_.size());
        return traits_type::to_int_type(*gptr());
    }
private:
    std::size_t rows_, produced_ = 0;
    std::string instrument_;
    std::size_t stride_, offset_;
    std::string line_;
};
void StreamingCsv() {
    static_assert(!std::is_copy_constructible<TickCsvReader>::value,
                  "one stream cursor cannot be copied");
    static_assert(!std::is_move_constructible<TickCsvReader>::value,
                  "moving a borrowed cursor must not leave a second live cursor");
    const std::string header = "instrument,timestamp_us,sequence,price,volume\n";
    Tick output = T(900, 900, 999, 7);
    std::istringstream empty(header); TickCsvReader noRows(empty, 1);
    Check(!noRows.Next(output) && !noRows.Next(output) && noRows.RowsRead() == 0 &&
          output.sequence == 900, "empty EOF is stable and leaves output unchanged");
    std::istringstream good("instrument,timestamp_us,sequence,price,volume\r\nA,0,1,100,0\r\nA,1,2,101,2");
    TickCsvReader exact(good, 2);
    Check(exact.Next(output) && output.sequence == 1 && output.volume == 0, "CRLF streaming row");
    Check(exact.Next(output) && output.sequence == 2 && output.price == 101, "unterminated final row");
    Check(!exact.Next(output) && exact.RowsRead() == 2 && output.sequence == 2,
          "exact row quota allows clean EOF, not an extra row");
    for (const auto& bad : {std::string("A,1,2,nan,1\n"), std::string(4097, 'x') + "\n",
                            std::string("A,1,2,100,-1\n"), std::string("\n")}) {
        std::istringstream input(header + "A,0,1,100,1\n" + bad + "A,2,3,102,1\n");
        TickCsvReader reader(input);
        Check(reader.Next(output), "valid prefix is readable without parsing the suffix");
        Throws([&] { reader.Next(output); });
        Check(output.sequence == 1 && output.price == 100 && reader.RowsRead() == 1,
              "malformed/oversized input cannot publish a partial tick or advance row count");
        input.clear(); const auto position = input.tellg();
        bool poisoned = false;
        try { reader.Next(output); }
        catch (const std::invalid_argument& e) {
            poisoned = std::string(e.what()) == "RESEARCH_CSV_READER_FAILED";
        }
        Check(poisoned && input.tellg() == position && reader.RowsRead() == 1,
              "clearing a stream cannot skip a failed row and resume the cursor");
    }
    std::istringstream over(header + "A,0,1,100,1\nA,1,2,101,1\n");
    TickCsvReader quota(over, 1); Check(quota.Next(output), "first quota row");
    Throws([&] { quota.Next(output); }); Throws([&] { quota.Next(output); });
    Check(quota.RowsRead() == 1 && output.sequence == 1, "quota rejection is atomic and permanent");
    std::istringstream broken(header + "A,0,1,100,1\n"); TickCsvReader io(broken);
    Check(io.Next(output), "I/O prefix");
    broken.setstate(std::ios::badbit); Throws([&] { io.Next(output); });
    broken.clear(); Throws([&] { io.Next(output); });
    Check(io.RowsRead() == 1 && output.sequence == 1, "I/O error is not successful EOF");
    std::istringstream wrong(header);
    Throws([&] { TickCsvReader invalid(wrong, 0); });
    Check(wrong.tellg() == 0, "invalid quota does not consume the header");
    GeneratedTickBuffer source(50000); std::istream stream(&source);
    TickCsvReader incremental(stream, 50000);
    Check(source.Produced() == 0, "constructor reads only the header");
    for (std::size_t i = 1; i <= 50000; ++i) {
        Check(incremental.Next(output) && output.sequence == i &&
              output.timestampUs == static_cast<std::int64_t>(i - 1) && output.price == 100 &&
              incremental.RowsRead() == i && source.Produced() == i,
              "incremental read must not consume a future row or retain the input history");
    }
    Check(!incremental.Next(output) && !incremental.Next(output) && output.sequence == 50000,
          "non-seekable stream completes exactly once");
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
// The fixture produces one completed candle at a time on a non-seekable stream.
// A reader must neither prefetch the following row nor retain a history vector.
class GeneratedBarBuffer : public std::streambuf {
public:
    explicit GeneratedBarBuffer(std::size_t rows) : rows_(rows) {
        line_ = "instrument,trading_day,begin_us,end_us,open,high,low,close,volume,tick_count,complete\n";
        setg(&line_[0], &line_[0], &line_[0] + line_.size());
    }
    std::size_t Produced() const { return produced_; }
protected:
    int_type underflow() override {
        if (gptr() < egptr()) return traits_type::to_int_type(*gptr());
        if (produced_ == rows_) return traits_type::eof();
        const auto i = produced_++;
        const auto price = 100 + i % 19;
        line_ = "TEST.FUT,20260921," + std::to_string(i * 10) + "," +
            std::to_string((i + 1) * 10) + "," + std::to_string(price) + "," +
            std::to_string(price + 2) + "," + std::to_string(price - 1) + "," +
            std::to_string(price + 1) + "," + std::to_string(i % 7) + ",2,1\n";
        setg(&line_[0], &line_[0], &line_[0] + line_.size());
        return traits_type::to_int_type(*gptr());
    }
private:
    std::size_t rows_, produced_ = 0;
    std::string line_;
};
bool SameBar(const Bar& a, const Bar& b) {
    return a.instrument == b.instrument && a.tradingDay == b.tradingDay &&
        a.beginUs == b.beginUs && a.endUs == b.endUs && a.open == b.open &&
        a.high == b.high && a.low == b.low && a.close == b.close &&
        a.volume == b.volume && a.tickCount == b.tickCount && a.complete == b.complete;
}
void StreamingBarsCsv() {
    static_assert(!std::is_copy_constructible<BarCsvReader>::value, "one bar cursor cannot be copied");
    static_assert(!std::is_move_constructible<BarCsvReader>::value, "one bar cursor cannot be moved");
    const std::string header = "instrument,trading_day,begin_us,end_us,open,high,low,close,volume,tick_count,complete\n";
    const std::string first = "TEST.FUT,20260921,0,10,100,102,99,101,3,2,1\n";
    const std::string second = "TEST.FUT,20260921,10,20,101,103,100,102,0,1,1";
    Bar output; output.instrument = "sentinel"; output.beginUs = 91; output.endUs = 99;
    const Bar sentinel = output;
    std::istringstream empty(header); BarCsvReader noRows(empty, 1);
    Check(!noRows.Next(output) && !noRows.Next(output) && noRows.RowsRead() == 0 &&
          SameBar(output, sentinel), "empty bar EOF preserves every output field");
    std::istringstream input(header + first + second); BarCsvReader reader(input, 2);
    Check(reader.Next(output) && output.complete && output.volume == 3 && output.tickCount == 2,
          "first completed bar is parsed");
    // Output is caller-owned. Mutating it must not rewrite the cursor's previous
    // instrument/day/interval authority used for validation of the next row.
    output.instrument = "OTHER"; output.tradingDay = "99991231"; output.endUs = 999;
    Check(reader.Next(output) && output.instrument == "TEST.FUT" && output.endUs == 20 &&
          output.volume == 0 && reader.RowsRead() == 2, "bar history is independent of output");
    const Bar last = output;
    Check(!reader.Next(output) && !reader.Next(output) && SameBar(last, output),
          "unterminated final row and exact row quota permit stable EOF");
    std::string crlf = header + first + second + "\n";
    std::string converted; for (char c : crlf) { if (c == '\n') converted += '\r'; converted += c; }
    std::istringstream crInput(converted); BarCsvReader cr(crInput);
    Check(cr.Next(output) && cr.Next(output) && !cr.Next(output), "completed bar CRLF input");
    const std::vector<std::string> invalid = {
        "OTHER,20260921,10,20,101,103,100,102,0,1,1\n",
        "TEST.FUT,20260920,10,20,101,103,100,102,0,1,1\n",
        "TEST.FUT,20260921,9,20,101,103,100,102,0,1,1\n",
        "TEST.FUT,20260921,10,20,101,103,100,102,0,1,0\n",
        "TEST.FUT,20260921,10,20,101,103,100,102,0,1,true\n",
        "TEST.FUT,20260229,10,20,101,103,100,102,0,1,1\n",
        "TEST.FUT,20260921,10,20,101,100,99,102,0,1,1\n",
        "TEST.FUT,20260921,10,20,101,103,100,nan,0,1,1\n",
        "TEST.FUT,20260921,10,20,101,103,100,102,-1,1,1\n",
        "TEST.FUT,20260921,10,20,101,103,100,102,0,0,1\n",
        "TEST.FUT,20260921,10,20,101,103,100,102,0,18446744073709551616,1\n",
        "TEST.FUT,20260921,10,9223372036854775808,101,103,100,102,0,1,1\n",
        "TEST.FUT,20260921,10,20,101,103,100,102,0,1,1,extra\n",
        "TEST.FUT,20260921,10,20,101,103,100,102,0,1\n",
        "\n", std::string(4097, 'x') + "\n"
    };
    for (const auto& row : invalid) {
        std::istringstream bad(header + first + row + second); BarCsvReader cursor(bad);
        Check(cursor.Next(output), "valid bar prefix remains streamable");
        const Bar before = output;
        Throws([&] { cursor.Next(output); });
        Check(SameBar(before, output) && cursor.RowsRead() == 1,
              "invalid bar cannot publish output or increment successful count");
        bad.clear(); const auto position = bad.tellg();
        bool poisoned = false;
        try { cursor.Next(output); } catch (const std::invalid_argument& e) {
            poisoned = std::string(e.what()) == "RESEARCH_BAR_CSV_READER_FAILED";
        }
        Check(poisoned && bad.tellg() == position && SameBar(before, output) && cursor.RowsRead() == 1,
              "clearing input cannot skip a rejected completed bar");
        Throws([&] { std::istringstream eager(header + first + row + second); ReadBarsCsv(eager); });
    }
    std::istringstream overlap(header + first + first); BarCsvReader separate(overlap);
    Check(separate.Next(output), "overlap fixture prefix"); output.endUs = 0;
    Throws([&] { separate.Next(output); });
    Check(output.endUs == 0, "caller cannot lower the previous interval via output mutation");
    std::istringstream limited(header + first + second); BarCsvReader quota(limited, 1);
    Check(quota.Next(output), "quota prefix"); const Bar beforeQuota = output;
    Throws([&] { quota.Next(output); }); Throws([&] { quota.Next(output); });
    Check(quota.RowsRead() == 1 && SameBar(beforeQuota, output), "bar row quota poisons without publication");
    std::istringstream broken(header + first + second); BarCsvReader io(broken);
    Check(io.Next(output), "I/O prefix"); const Bar beforeIo = output;
    broken.setstate(std::ios::badbit); Throws([&] { io.Next(output); });
    broken.clear(); Throws([&] { io.Next(output); });
    Check(io.RowsRead() == 1 && SameBar(beforeIo, output), "bar I/O failure is not clean EOF");
    std::istringstream untouched(header);
    Throws([&] { BarCsvReader zero(untouched, 0); });
    Check(untouched.tellg() == 0, "invalid bar quota must not consume header");
    for (const std::string& h : {std::string(), std::string("bad header\n"), std::string(4097, 'x')})
        Throws([&] { std::istringstream bad(h); BarCsvReader wrong(bad); });

    GeneratedBarBuffer generated(50000); std::istream stream(&generated);
    BarCsvReader streaming(stream, 50000);
    Check(generated.Produced() == 0, "bar construction reads no future row");
    BarSeries bounded(7);
    for (std::size_t i = 0; i < 50000; ++i) {
        Check(streaming.Next(output) && generated.Produced() == i + 1 && streaming.RowsRead() == i + 1,
              "one call consumes exactly one generated bar");
        Check(output.instrument == "TEST.FUT" && output.tradingDay == "20260921" &&
              output.beginUs == static_cast<std::int64_t>(i * 10) &&
              output.endUs == static_cast<std::int64_t>((i + 1) * 10) &&
              output.open == 100 + i % 19 && output.high == 102 + i % 19 &&
              output.low == 99 + i % 19 && output.close == 101 + i % 19 &&
              output.volume == static_cast<std::int64_t>(i % 7) && output.tickCount == 2 && output.complete,
              "independent generated OHLCV/identity oracle");
        bounded.Append(output);
        Check(bounded.Size() == std::min(i + 1, std::size_t(7)), "consumer history remains bounded");
    }
    Check(!streaming.Next(output) && !streaming.Next(output) && output.endUs == 500000,
          "non-seekable bar EOF does not replace last observed value");
    GeneratedBarBuffer eagerSource(31); std::istream eagerInput(&eagerSource);
    const auto eager = ReadBarsCsv(eagerInput, 31);
    GeneratedBarBuffer cursorSource(31); std::istream cursorInput(&cursorSource);
    BarCsvReader cursor(cursorInput, 31);
    for (const auto& expected : eager) Check(cursor.Next(output) && SameBar(expected, output),
                                           "eager and incremental contracts agree");
    Check(!cursor.Next(output), "eager-equivalence terminal state");
    std::cout << "streaming completed-bar oracle rows=50000\n";
}

// The offline merge uses an explicit synthetic event-time order. It does not
// infer cross-feed historical availability. The independent oracle below sorts
// only test data; the production cursor must not retain the historical rows.
void MergedCsv() {
    static_assert(!std::is_copy_constructible<MergedTickCsvReader>::value,
                  "a merged cursor must exclusively own its source cursors");
    static_assert(!std::is_move_constructible<MergedTickCsvReader>::value,
                  "moving must not leave another cursor over the same inputs");
    const std::string header = "instrument,timestamp_us,sequence,price,volume\n";
    Tick output = T(900, 900, 999, 7);
    const auto same = [](const Tick& a, const Tick& b) {
        return a.instrument == b.instrument && a.timestampUs == b.timestampUs &&
            a.sequence == b.sequence && a.price == b.price && a.volume == b.volume;
    };
    std::istringstream a(header + "A,0,1,100,1\nA,2,2,101,2\nA,2,2,101,2\nA,2,3,102,3\n");
    std::istringstream b(header + "B,0,1,200,1\nB,1,2,201,2\nB,2,3,202,3");
    std::istringstream empty(header);
    std::vector<std::istream*> inputs{&a, &empty, &b};
    MergedTickCsvReader reader(inputs, 7);
    Check(a.tellg() == static_cast<std::streamoff>(header.size()) &&
          b.tellg() == static_cast<std::streamoff>(header.size()), "merge construction consumes only headers");
    inputs.assign(1, nullptr); // Original vector storage is not a live dependency.
    const char* names[] = {"A", "B", "B", "A", "A", "A", "B"};
    const std::int64_t times[] = {0, 0, 1, 2, 2, 2, 2};
    const std::uint64_t sequences[] = {1, 1, 2, 2, 2, 3, 3};
    for (std::size_t i = 0; i < 7; ++i) {
        Check(reader.Next(output) && output.instrument == names[i] &&
              output.timestampUs == times[i] && output.sequence == sequences[i] &&
              reader.RowsRead() == i + 1, "stable source-index ties and exact duplicates");
        output.instrument = "CALLER-CHANGED"; output.timestampUs = -1;
        output.sequence = 0; output.price = 0; output.volume = -1;
    }
    const Tick atEof = output;
    Check(!reader.Next(output) && !reader.Next(output) && same(output, atEof) &&
          reader.RowsRead() == 7, "clean merged EOF leaves output unchanged");
    std::istringstream e1(header), e2(header);
    MergedTickCsvReader allEmpty({&e1, &e2}, 1);
    Check(!allEmpty.Next(output) && !allEmpty.Next(output) && same(output, atEof) &&
          allEmpty.RowsRead() == 0, "all header-only sources end without invented records");

    std::istringstream untouched(header + "A,0,1,100,1\n");
    Throws([&] { MergedTickCsvReader invalid({}, 1); });
    Throws([&] { MergedTickCsvReader invalid({nullptr}, 1); });
    Throws([&] { MergedTickCsvReader invalid({&untouched, &untouched}, 1); });
    Throws([&] { MergedTickCsvReader invalid({&untouched}, 0); });
    Throws([&] { MergedTickCsvReader invalid(std::vector<std::istream*>(1025, &untouched), 1); });
    Check(untouched.tellg() == 0, "invalid merge configuration must consume no input");
    Throws([&] { std::istringstream wrong("bad header\n"); MergedTickCsvReader invalid({&wrong}); });
    std::istringstream repeatedA(header + "A,0,1,100,1\n"), repeatedB(header + "A,1,1,101,1\n");
    MergedTickCsvReader repeated({&repeatedA, &repeatedB});
    const Tick beforeRepeated = output;
    Throws([&] { repeated.Next(output); });
    Check(repeated.RowsRead() == 0 && same(output, beforeRepeated),
          "two sources may not share an instrument/sequence namespace");
    Throws([&] { repeated.Next(output); });

    // Read one valid prefix. A bad refill must fail before any later cached
    // source head can be emitted, and clearing the stream cannot resume it.
    for (const std::string& bad : {
            std::string("A,0,2,100,1\n"), // reversed time
            std::string("A,2,1,100,1\n"), // reused sequence with changed time
            std::string("A,1,1,101,1\n"), // conflicting duplicate
            std::string("B,2,2,100,1\n"), // instrument switch
            std::string("A,2,0,100,1\n"),
            std::string("A,2,2,nan,1\n"),
            std::string(4097, 'x') + "\n", std::string("\n")}) {
        std::istringstream first(header + "A,1,1,100,1\n" + bad + "A,3,3,100,1\n");
        std::istringstream later(header + "B,10,1,200,1\n");
        MergedTickCsvReader failed({&first, &later});
        Check(failed.Next(output) && output.instrument == "A", "valid prefix is available before suffix read");
        const Tick prefix = output;
        Throws([&] { failed.Next(output); });
        Check(failed.RowsRead() == 1 && same(output, prefix), "failed merge cannot publish or count a partial tick");
        first.clear(); later.clear(); const auto where = first.tellg(), other = later.tellg();
        bool poisoned = false;
        try { failed.Next(output); }
        catch (const std::invalid_argument& error) {
            poisoned = std::string(error.what()) == "RESEARCH_MERGED_CSV_READER_FAILED";
        }
        Check(poisoned && first.tellg() == where && later.tellg() == other &&
              same(output, prefix) && failed.RowsRead() == 1, "merged failure is permanent and consumes no more input");
    }
    std::istringstream decreasing(header + "A,1,2,100,1\nA,2,1,100,1\n");
    MergedTickCsvReader reversedSequence({&decreasing});
    Check(reversedSequence.Next(output), "sequence reversal prefix");
    Throws([&] { reversedSequence.Next(output); });
    Check(output.sequence == 2 && reversedSequence.RowsRead() == 1, "strict per-source sequence order");

    std::istringstream q1(header + "A,0,1,100,1\n"), q2(header + "B,1,1,100,1\n");
    MergedTickCsvReader quota({&q1, &q2}, 1);
    Check(quota.Next(output), "global merge quota prefix"); const Tick beforeQuota = output;
    Throws([&] { quota.Next(output); }); Throws([&] { quota.Next(output); });
    Check(quota.RowsRead() == 1 && same(output, beforeQuota), "row limit is global, not per file");
    std::istringstream broken(header + "A,0,1,100,1\nA,1,2,100,1\n");
    MergedTickCsvReader io({&broken}); Check(io.Next(output), "I/O prefix"); const Tick beforeIo = output;
    broken.setstate(std::ios::badbit); Throws([&] { io.Next(output); });
    broken.clear(); Throws([&] { io.Next(output); });
    Check(io.RowsRead() == 1 && same(output, beforeIo), "I/O failure is not merged EOF");
    const auto lastTime = std::to_string(std::numeric_limits<std::int64_t>::max());
    const auto lastSequence = std::to_string(std::numeric_limits<std::uint64_t>::max());
    const std::string edgeRow = "A," + lastTime + "," + lastSequence + ",100,0\n";
    std::istringstream edgeA(header + edgeRow + edgeRow), edgeB(header + "B,0,1,100,0\n");
    MergedTickCsvReader edges({&edgeA, &edgeB}, std::numeric_limits<std::size_t>::max());
    Check(edges.Next(output) && output.instrument == "B" && output.timestampUs == 0,
          "merge compares extreme timestamps without subtraction overflow");
    Check(edges.Next(output) && output.timestampUs == std::numeric_limits<std::int64_t>::max() &&
          output.sequence == std::numeric_limits<std::uint64_t>::max(), "full-width tick identity is preserved");
    Check(edges.Next(output) && !edges.Next(output) && edges.RowsRead() == 3,
          "maximum sequence exact retry and maximum quota terminate normally");
    std::istringstream goodHead(header + "A,0,1,100,1\n"), badHead(header + "B,1,1,nan,1\n");
    MergedTickCsvReader prime({&goodHead, &badHead}); const Tick beforePrime = output;
    Throws([&] { prime.Next(output); });
    Check(prime.RowsRead() == 0 && same(output, beforePrime), "initial source validation precedes first publication");
}

void MergedCsvOracle() {
    std::size_t oracleRows = 0;
    for (std::size_t trial = 0; trial < 64; ++trial) {
        struct Expected { Tick tick; std::size_t source; };
        std::vector<Expected> expected;
        std::vector<std::unique_ptr<std::istringstream>> streams;
        std::vector<std::istream*> inputs;
        const auto count = trial % 7 + 1;
        for (std::size_t source = 0; source < count; ++source) {
            std::vector<Tick> ticks;
            std::int64_t timestamp = static_cast<std::int64_t>((source * 7 + trial) % 4);
            for (std::size_t row = 0; row < (trial + source * 3) % 14; ++row) {
                timestamp += static_cast<std::int64_t>((row * 3 + source + trial) % 4);
                Tick tick = T(timestamp, row + 1, 100 + source + row,
                              static_cast<std::int64_t>((row + trial) % 5));
                tick.instrument = "S" + std::to_string(source);
                ticks.push_back(tick); expected.push_back({tick, source});
                if (row % 5 == 0) { ticks.push_back(tick); expected.push_back({tick, source}); }
            }
            std::ostringstream csv; WriteTicksCsv(csv, ticks);
            streams.emplace_back(new std::istringstream(csv.str()));
            inputs.push_back(streams.back().get());
        }
        std::stable_sort(expected.begin(), expected.end(), [](const Expected& a, const Expected& b) {
            if (a.tick.timestampUs != b.tick.timestampUs) return a.tick.timestampUs < b.tick.timestampUs;
            return a.source < b.source;
        });
        MergedTickCsvReader merged(inputs, std::max(std::size_t(1), expected.size()));
        Tick output;
        for (const auto& item : expected) {
            const auto& tick = item.tick;
            Check(merged.Next(output) && output.instrument == tick.instrument &&
                  output.timestampUs == tick.timestampUs && output.sequence == tick.sequence &&
                  output.price == tick.price && output.volume == tick.volume,
                  "merge agrees with independently stable-sorted heterogeneous oracle");
            ++oracleRows;
        }
        Check(!merged.Next(output) && merged.RowsRead() == expected.size(), "oracle exact-limit terminal state");
    }
    // Actual non-seekable large sources. At most one private current record per
    // source, not a concatenation sorted after materializing the entire input.
    std::vector<std::unique_ptr<GeneratedTickBuffer>> buffers;
    std::vector<std::unique_ptr<std::istream>> streams;
    std::vector<std::istream*> inputs;
    for (std::size_t source = 0; source < 4; ++source) {
        buffers.emplace_back(new GeneratedTickBuffer(25000, "S" + std::to_string(source), 4, source));
        streams.emplace_back(new std::istream(buffers.back().get())); inputs.push_back(streams.back().get());
    }
    MergedTickCsvReader merged(inputs, 100000);
    for (const auto& buffer : buffers) Check(buffer->Produced() == 0, "merged construction does not prime data rows");
    Tick output;
    for (std::size_t row = 0; row < 100000; ++row) {
        Check(merged.Next(output) && output.instrument == "S" + std::to_string(row % 4) &&
              output.timestampUs == static_cast<std::int64_t>(row) && output.sequence == row / 4 + 1 &&
              output.price == 100 && output.volume == 1 && merged.RowsRead() == row + 1,
              "non-seekable interleave follows independently computed field oracle");
        std::size_t produced = 0;
        for (std::size_t source = 0; source < 4; ++source) {
            const auto delivered = row / 4 + (source <= row % 4 ? 1 : 0);
            Check(buffers[source]->Produced() >= delivered && buffers[source]->Produced() <= delivered + 1,
                  "a source is read ahead by at most one row");
            produced += buffers[source]->Produced();
        }
        Check(produced <= row + 4, "global lookahead is bounded by source count");
    }
    Check(!merged.Next(output) && !merged.Next(output) && output.timestampUs == 99999,
          "all large non-seekable inputs reach stable EOF");
    std::vector<std::unique_ptr<std::istringstream>> many;
    inputs.clear();
    for (std::size_t source = 0; source < 1024; ++source) {
        many.emplace_back(new std::istringstream("instrument,timestamp_us,sequence,price,volume\nS" +
                                                std::to_string(source) + ",0,1,100,1\n"));
        inputs.push_back(many.back().get());
    }
    MergedTickCsvReader maximum(inputs, 1024);
    for (std::size_t source = 0; source < 1024; ++source)
        Check(maximum.Next(output) && output.instrument == "S" + std::to_string(source),
              "maximum supported fan-in retains numeric source-index tie order");
    Check(!maximum.Next(output), "maximum fan-in exact quota EOF");
    std::cout << "merged CSV oracle rows=" << oracleRows << ", lazy rows=100000, maximum sources=1024\n";
}

struct LegacyTestColumns {
    LegacyTickCsvLayout layout;
    std::size_t count, instrument, day, time, fraction, price, volume, turnover, interest;
    int action;
};
const LegacyTestColumns legacyProfiles[] = {
    {LegacyTickCsvLayout::Hepta32,32,0,1,2,3,4,5,7,29,-1},
    {LegacyTickCsvLayout::Immsg34,34,2,3,4,5,6,7,9,31,-1},
    {LegacyTickCsvLayout::Immsg35,35,2,3,5,6,7,8,10,32,4},
    {LegacyTickCsvLayout::Zs58,58,3,0,1,2,37,38,46,39,-1}
};
std::vector<std::string> LegacyCells(const LegacyTestColumns& c, const std::string& instrument = "A",
    const std::string& day = "20260921", const std::string& action = "20260921",
    const std::string& time = "00:00:00", const std::string& fraction = "0",
    const std::string& price = "100", const std::string& volume = "100") {
    std::vector<std::string> cells(c.count, "0");
    cells[c.instrument] = instrument; cells[c.day] = day;
    cells[c.time] = time;
    if(c.layout == LegacyTickCsvLayout::Zs58) {
        cells[c.time].erase(std::remove(cells[c.time].begin(), cells[c.time].end(), ':'), cells[c.time].end());
    }
    cells[c.fraction] = fraction; cells[c.price] = price; cells[c.volume] = volume;
    cells[c.turnover] = "1000.25"; cells[c.interest] = "25.5";
    if(c.action >= 0) cells[static_cast<std::size_t>(c.action)] = action;
    if(c.layout == LegacyTickCsvLayout::Immsg34 || c.layout == LegacyTickCsvLayout::Immsg35) {
        cells[0] = "2026-09-21 00:00:00"; cells[1] = "IMMSG";
    }
    return cells;
}
std::string LegacyLine(const std::vector<std::string>& cells, const std::string& ending = "\n") {
    std::string out;
    for (std::size_t i = 0; i < cells.size(); ++i) { if(i) out += ','; out += cells[i]; }
    return out + ending;
}
std::string LegacyTestHeader(const LegacyTestColumns& c) {
    std::vector<std::string> cells(c.count, "raw");
    cells[c.instrument] = "InstrumentID"; cells[c.day] = "TradingDay"; cells[c.time] = "UpdateTime";
    cells[c.fraction] = c.layout == LegacyTickCsvLayout::Zs58 ? "UpdateMicrosec" : "UpdateMillisec";
    cells[c.price] = "LastPrice"; cells[c.volume] = "Volume"; cells[c.turnover] = "TurnOver";
    cells[c.interest] = "OpenInterest";
    if(c.action >= 0) cells[static_cast<std::size_t>(c.action)] = "ActionDay";
    if(c.layout == LegacyTickCsvLayout::Immsg34 || c.layout == LegacyTickCsvLayout::Immsg35) {
        cells[0] = "Localtime"; cells[1] = "MsgType";
    }
    return LegacyLine(cells, "\r\n");
}
LegacyTickClock Clock(const std::string& day, int offset = 0) {
    LegacyTickClock c; c.actionDay = day; c.utcOffsetMinutes = offset; return c;
}
using LegacyBindings = std::map<std::string, SessionSchedule>;
LegacyBindings AnyLegacyTime(const std::string& day = "20260921") {
    const SessionSchedule s({Window(0, 400000000000000000LL, day)});
    return {{"A",s},{"B",s}};
}
LegacyTickClockResolver FixedLegacyClock(const std::string& day = "20260921", int offset = 0) {
    return [day,offset](std::size_t, const std::string&, const std::string&, const std::string&) {
        return Clock(day, offset);
    };
}
bool SameLegacyRecord(const LegacyTickRecord& a, const LegacyTickRecord& b) {
    return a.tick.instrument == b.tick.instrument && a.tick.timestampUs == b.tick.timestampUs &&
        a.tick.sequence == b.tick.sequence && a.tick.price == b.tick.price && a.tick.volume == b.tick.volume &&
        a.tradingDay == b.tradingDay && a.actionDay == b.actionDay && a.utcOffsetMinutes == b.utcOffsetMinutes &&
        a.cumulativeVolume == b.cumulativeVolume && a.turnover == b.turnover && a.openInterest == b.openInterest &&
        a.sourceFields == b.sourceFields;
}
void LegacyCsvProfiles() {
    static_assert(!std::is_copy_constructible<LegacyTickCsvReader>::value, "legacy cursor must not copy");
    static_assert(!std::is_move_constructible<LegacyTickCsvReader>::value, "legacy cursor must not move");
    // 2026-09-20 15:59:59 UTC, independently calculated using a Gregorian UTC fixture.
    const std::int64_t begin = 1789919999000000LL;
    const SessionSchedule calendar({Window(begin,begin+86400000000LL,"20260921"),
                                    Window(begin+86400000000LL,begin+172800000000LL,"20260922")});
    for(const auto& c : legacyProfiles) for(bool first : {false,true}) for(bool header : {false,true}) {
        const bool micro = c.layout == LegacyTickCsvLayout::Zs58;
        std::vector<std::vector<std::string>> rows{
            LegacyCells(c,"A","20260921","20260920","23:59:59","0","100","100"),
            LegacyCells(c,"B","20260921","20260920","23:59:59","0","50","200"),
            LegacyCells(c,"A","20260921","20260921","00:00:00",micro?"125001":"125","101","103"),
            LegacyCells(c,"B","20260921","20260921","00:00:00",micro?"125001":"125","51","205"),
            LegacyCells(c,"A","20260921","20260921","00:00:00",micro?"125001":"125","101","103"),
            LegacyCells(c,"A","20260922","20260921","23:59:59","0","102","4"),
            LegacyCells(c,"B","20260922","20260921","23:59:59","0","52","2"),
            LegacyCells(c,"A","20260922","20260922","00:00:00","0","103","6")};
        std::string data = header ? LegacyTestHeader(c) : "";
        for (const auto& row : rows) data += LegacyLine(row,"\r\n");
        std::istringstream input(data); std::size_t resolutions = 0;
        const std::vector<std::string> dates{"20260920","20260920","20260921","20260921",
                                             "20260921","20260921","20260921","20260922"};
        LegacyTickCsvReader reader(input,c.layout,{{"A",calendar},{"B",calendar}},
            [&](std::size_t row,const std::string& instrument,const std::string& day,const std::string& supplied) {
                Check(row == ++resolutions && instrument == rows[row-1][c.instrument] && day == rows[row-1][c.day],"clock binding");
                Check(supplied == (c.action >= 0 ? dates[row-1] : ""),"source civil day identity");
                return Clock(dates[row-1],480);
            },first,header,rows.size());
        Check(resolutions==0 && reader.RowsRead()==0,"constructor invokes no clock");
        const std::int64_t delta[] = {first?100:0,first?200:0,3,5,0,first?4:0,first?2:0,2};
        const std::int64_t times[] = {begin,begin,begin+1125000+(micro?1:0),begin+1125000+(micro?1:0),
            begin+1125000+(micro?1:0),begin+86400000000LL,begin+86400000000LL,begin+86401000000LL};
        LegacyTickRecord record;
        for(std::size_t i=0;i<rows.size();++i) {
            Check(reader.Next(record),"legacy row missing");
            Check(record.tick.instrument==rows[i][c.instrument] && record.tick.sequence==i+1 &&
                  record.tick.volume==delta[i] && record.tick.timestampUs==times[i] &&
                  record.sourceFields==rows[i] && record.actionDay==dates[i] && record.utcOffsetMinutes==480,
                  "legacy independent conversion oracle");
            Near(record.turnover,1000.25); Near(record.openInterest,25.5);
            // Caller edits cannot alter future cumulative state or session bindings.
            if(i==0) { record.tick.volume=-100; record.cumulativeVolume=999999; record.sourceFields.clear(); }
        }
        const auto before=record;
        Check(!reader.Next(record) && !reader.Next(record) && SameLegacyRecord(record,before) &&
              reader.RowsRead()==8 && resolutions==8,"legacy stable checked EOF");
    }
}
void LegacyCsvRejection() {
    for (const auto& c : legacyProfiles) {
        const auto valid=LegacyCells(c);
        std::vector<std::vector<std::string>> bad;
        auto add=[&](std::size_t column,const std::string& value) { auto row=valid; row[column]=value; bad.push_back(row); };
        auto shortRow=valid; shortRow.pop_back(); bad.push_back(shortRow);
        auto extra=valid; extra.push_back("0"); bad.push_back(extra);
        for(const auto& price : {"0","-1","nan","inf","1e9999"," 10","10 ","10x",""}) add(c.price,price);
        for(const auto& volume : {"-1","+1","1.0","9223372036854775808","18446744073709551616"}) add(c.volume,volume);
        for(const auto& day : {"20260229","19000229","20261301","00000101","20260900","2026092X"}) add(c.day,day);
        add(c.instrument,"FOREIGN"); add(c.turnover,"-1"); add(c.interest,"NaN");
        add(c.fraction,c.layout==LegacyTickCsvLayout::Zs58?"1000000":"1000"); add(c.fraction,"-1");
        add(c.time,c.layout==LegacyTickCsvLayout::Zs58?"240000":"24:00:00");
        add(c.time,c.layout==LegacyTickCsvLayout::Zs58?"236000":"23:60:00");
        add(c.time,c.layout==LegacyTickCsvLayout::Zs58?"235960":"23:59:60");
        add(c.time,"0:00:00");
        const std::size_t raw=c.count-1;
        add(raw,std::string(129,'x')); add(raw,"\"quoted\""); add(raw,std::string("a\0b",3));
        add(raw,"\t"); add(raw,std::string(1,static_cast<char>(0x80)));
        if(c.action>=0) add(static_cast<std::size_t>(c.action),"20260229");
        if(c.layout==LegacyTickCsvLayout::Immsg34 || c.layout==LegacyTickCsvLayout::Immsg35) add(1,"OTHER");
        for(const auto& row : bad) {
            std::istringstream input(LegacyLine(valid)+LegacyLine(row)+LegacyLine(valid));
            std::size_t calls=0;
            LegacyTickCsvReader reader(input,c.layout,AnyLegacyTime(),
                [&](std::size_t,const std::string&,const std::string&,const std::string&) { ++calls; return Clock("20260921"); },false);
            LegacyTickRecord record; Check(reader.Next(record),"valid prefix"); const auto before=record;
            Throws([&] { reader.Next(record); }); const auto callsAfter=calls;
            input.clear(); Throws([&] { reader.Next(record); });
            Check(SameLegacyRecord(record,before) && reader.RowsRead()==1 && calls==callsAfter,"fail-stop output and callback isolation");
        }
        std::string wrongHeader=LegacyTestHeader(c); auto at=wrongHeader.find("LastPrice");
        wrongHeader.replace(at,9,"Price"); std::istringstream input(wrongHeader+LegacyLine(valid));
        Throws([&] { LegacyTickCsvReader reader(input,c.layout,AnyLegacyTime(),FixedLegacyClock(),false,true); });
        std::istringstream quota(LegacyLine(valid)+LegacyLine(valid));
        LegacyTickCsvReader bounded(quota,c.layout,AnyLegacyTime(),FixedLegacyClock(),false,false,1);
        LegacyTickRecord record; Check(bounded.Next(record),"quota first"); const auto before=record;
        Throws([&] { bounded.Next(record); }); Throws([&] { bounded.Next(record); });
        Check(bounded.RowsRead()==1 && SameLegacyRecord(record,before),"global row quota includes duplicates");
        std::istringstream empty; LegacyTickCsvReader noRows(empty,c.layout,AnyLegacyTime(),FixedLegacyClock(),false);
        Check(!noRows.Next(record) && SameLegacyRecord(record,before),"headerless empty EOF");
        std::istringstream headerOnly(LegacyTestHeader(c));
        LegacyTickCsvReader noData(headerOnly,c.layout,AnyLegacyTime(),FixedLegacyClock(),false,true);
        Check(!noData.Next(record),"checked header-only EOF");
    }
    const auto& c=legacyProfiles[0]; const auto valid=LegacyCells(c);
    std::istringstream input(LegacyLine(valid)); const auto pos=input.tellg();
    Throws([&] { LegacyTickCsvReader bad(input,static_cast<LegacyTickCsvLayout>(100),AnyLegacyTime(),FixedLegacyClock(),false); });
    Throws([&] { LegacyTickCsvReader bad(input,c.layout,{},FixedLegacyClock(),false); });
    Throws([&] { LegacyTickCsvReader bad(input,c.layout,AnyLegacyTime(),{},false); });
    Throws([&] { LegacyTickCsvReader bad(input,c.layout,AnyLegacyTime(),FixedLegacyClock(),false,false,0); });
    auto many=AnyLegacyTime(); for(int i=0;i<63;++i) many.emplace("K"+std::to_string(i),many.begin()->second);
    Throws([&] { LegacyTickCsvReader bad(input,c.layout,many,FixedLegacyClock(),false); });
    auto invalid=AnyLegacyTime(); invalid.emplace("bad id",invalid.begin()->second);
    Throws([&] { LegacyTickCsvReader bad(input,c.layout,invalid,FixedLegacyClock(),false); });
    Check(input.tellg()==pos,"invalid configuration consumes nothing");
    auto fails=[&](std::string data,LegacyBindings bindings,LegacyTickClockResolver resolver) {
        std::istringstream stream(data); LegacyTickCsvReader reader(stream,c.layout,bindings,resolver,false);
        LegacyTickRecord out; out.tick.instrument="sentinel"; const auto before=out;
        Throws([&] {reader.Next(out);}); Throws([&] {reader.Next(out);});
        Check(reader.RowsRead()==0 && SameLegacyRecord(out,before),"clock or session failure is atomic");
    };
    fails(LegacyLine(valid),AnyLegacyTime(),FixedLegacyClock(""));
    fails(LegacyLine(valid),AnyLegacyTime(),FixedLegacyClock("20260229"));
    fails(LegacyLine(valid),AnyLegacyTime(),FixedLegacyClock("20260921",841));
    fails(LegacyLine(valid),AnyLegacyTime(),FixedLegacyClock("20260921",-841));
    fails(LegacyLine(valid),AnyLegacyTime(),FixedLegacyClock("19700101",1));
    fails(LegacyLine(valid),AnyLegacyTime("20260922"),FixedLegacyClock());
    fails(LegacyLine(valid),{{"A",SessionSchedule({Window(0,1,"20260921")})}},FixedLegacyClock());
    fails(LegacyLine(valid),AnyLegacyTime(),[](std::size_t,const std::string&,const std::string&,const std::string&)->LegacyTickClock { throw std::runtime_error("clock mapping absent"); });
    auto changed=valid; changed[c.volume]="99";
    std::istringstream decreasing(LegacyLine(valid)+LegacyLine(changed));
    LegacyTickCsvReader decrease(decreasing,c.layout,AnyLegacyTime(),FixedLegacyClock(),false);
    LegacyTickRecord out; Check(decrease.Next(out),"counter baseline"); const auto before=out;
    Throws([&] {decrease.Next(out);}); Check(SameLegacyRecord(out,before),"no same-day counter reset");
    auto later=LegacyCells(c,"A","20260921","20260921","00:00:01");
    auto early=LegacyCells(c,"B");
    std::istringstream reversed(LegacyLine(later)+LegacyLine(early));
    LegacyTickCsvReader reverse(reversed,c.layout,AnyLegacyTime(),FixedLegacyClock(),false);
    Check(reverse.Next(out),"global first time"); Throws([&] {reverse.Next(out);});
    std::istringstream overrideDay(LegacyLine(LegacyCells(legacyProfiles[2])));
    LegacyTickCsvReader overriding(overrideDay,LegacyTickCsvLayout::Immsg35,AnyLegacyTime(),FixedLegacyClock("20260920"),false);
    Throws([&] {overriding.Next(out);});
    // Calendar arithmetic table is independent of the parser's day algorithm.
    struct CivilCase { const char* day; const char* time; int offset; std::int64_t utc; };
    const CivilCase cases[] = {
        {"19700101","00:00:00",0,0}, {"19691231","23:00:00",-60,0},
        {"20000229","12:34:56",0,951827696000000LL},
        {"20260921","00:00:00",480,1789920000000000LL},
        {"20260921","00:00:00",840,1789898400000000LL},
        {"20260921","00:00:00",-840,1789999200000000LL},
        {"99991231","23:59:59",0,253402300799000000LL}
    };
    for(const auto& x : cases) {
        std::istringstream stream(LegacyLine(LegacyCells(c,"A","20260921",x.day,x.time)));
        LegacyTickCsvReader reader(stream,c.layout,AnyLegacyTime(),FixedLegacyClock(x.day,x.offset),true);
        Check(reader.Next(out) && out.tick.timestampUs==x.utc && out.tick.volume==100,"civil-to-UTC independent table");
    }
    // Real I/O error is not clean EOF, and cannot be cleared to resume.
    std::istringstream broken(LegacyLine(valid)); LegacyTickCsvReader brokenReader(broken,c.layout,AnyLegacyTime(),FixedLegacyClock(),false);
    broken.setstate(std::ios::badbit); Throws([&] {brokenReader.Next(out);}); broken.clear(); Throws([&] {brokenReader.Next(out);});
}
class LegacyGeneratedBuffer : public std::streambuf {
public:
    explicit LegacyGeneratedBuffer(std::size_t count):count_(count) {}
    std::size_t Generated() const {return generated_;}
protected:
    int_type underflow() override {
        if(gptr() && gptr()<egptr()) return traits_type::to_int_type(*gptr());
        if(generated_==count_) return traits_type::eof();
        const auto n=generated_++; const auto seconds=n/1000;
        const auto part=n%1000;
        const auto digits=[](std::size_t value) {return (value<10?"0":"")+std::to_string(value);};
        const std::string time=digits(seconds/3600)+":"+digits(seconds/60%60)+":"+digits(seconds%60);
        line_=LegacyLine(LegacyCells(legacyProfiles[0],n%2?"B":"A","20260921","20260921",time,
                                    std::to_string(part),std::to_string(100+n%17),std::to_string(100+n/2)));
        char* p=&line_[0]; setg(p,p,p+line_.size()); return traits_type::to_int_type(*gptr());
    }
    pos_type seekoff(off_type,std::ios_base::seekdir,std::ios_base::openmode) override {throw std::runtime_error("must not seek");}
    pos_type seekpos(pos_type,std::ios_base::openmode) override {throw std::runtime_error("must not seek");}
private:
    std::size_t count_,generated_=0; std::string line_;
};
void LegacyCsvStreamingOracle() {
    const std::size_t count=100000;
    LegacyGeneratedBuffer buffer(count); std::istream input(&buffer);
    auto bindings=AnyLegacyTime();
    // Exercise all 64 allowed bindings without retaining rows per binding.
    for(int i=0;i<62;++i) bindings.emplace("UNUSED"+std::to_string(i),bindings.begin()->second);
    LegacyTickCsvReader reader(input,LegacyTickCsvLayout::Hepta32,bindings,FixedLegacyClock(),false,false,count);
    Check(buffer.Generated()==0,"headerless reader has no prefetch"); LegacyTickRecord record;
    for(std::size_t n=0;n<count;++n) {
        Check(reader.Next(record) && buffer.Generated()==n+1 && reader.RowsRead()==n+1,
              "bounded non-seekable one-row read-ahead");
        Check(record.tick.timestampUs==1789948800000000LL+static_cast<std::int64_t>(n)*1000 &&
              record.tick.instrument==(n%2?"B":"A") && record.tick.sequence==n+1 &&
              record.tick.volume==(n<2?0:1) && record.cumulativeVolume==static_cast<std::int64_t>(100+n/2) &&
              record.tick.price==static_cast<double>(100+n%17),"large mixed legacy independent oracle");
    }
    const auto before=record;
    Check(!reader.Next(record) && !reader.Next(record) && SameLegacyRecord(record,before),"streaming legacy EOF");
}

const std::int64_t kLegacyBarDay = 1789948800000000LL; // 2026-09-21 00:00 UTC, independent fixture.
const std::uint64_t kFileEpoch = 11644473600000000ULL;
std::vector<std::string> LegacyBarCells(LegacyBarCsvLayout layout, int minute = 0) {
    const bool stock = layout == LegacyBarCsvLayout::Stock7;
    std::ostringstream clock; clock << std::setfill('0');
    if (stock) clock << "2026-09-21 00:" << std::setw(2) << minute+3 << ":00";
    else clock << "20260921_00" << std::setw(2) << minute << "00";
    if (stock) return {clock.str(),"100","104","99","102","12","1200"};
    const auto start = kFileEpoch + static_cast<std::uint64_t>(kLegacyBarDay) + minute*60000000ULL;
    std::vector<std::string> fields = {std::to_string(start),clock.str(),"100","104","99","102",
        std::to_string(1000+minute*12),"12",std::to_string(100000+minute*1200),"1200","77"};
    if (layout == LegacyBarCsvLayout::Futures13) {
        fields.push_back(std::to_string(start+1000000)); fields.push_back(std::to_string(start+59000000));
    }
    return fields;
}
std::string LegacyBarHeader(LegacyBarCsvLayout layout) {
    if (layout == LegacyBarCsvLayout::Stock7) return "datetime,open,high,low,close,volume,turnover";
    const std::string basic = "TimeStamp,time,Open,High,Low,Close,Volume,LastVolume,TurnOver,LastTurnOver,OpenInterest";
    return layout == LegacyBarCsvLayout::Futures13 ? basic+",HighTimeStamp,LowTimeStamp" : basic;
}
LegacyBarEvidenceResolver BarEvidence(int offset = 0) {
    return [offset](std::size_t row, const std::string& instrument, const std::string& civilDay) {
        Check(instrument=="TEST.FUT" && civilDay=="20260921", "bar resolver source identity");
        LegacyBarEvidence e; e.utcOffsetMinutes=offset; e.tickCount=7+row;
        e.observedAtUs=kLegacyBarDay+86400000000LL; e.complete=true; return e;
    };
}
bool SameLegacyBarRecord(const LegacyBarRecord& a, const LegacyBarRecord& b) {
    return a.bar.instrument==b.bar.instrument && a.bar.tradingDay==b.bar.tradingDay &&
        a.bar.beginUs==b.bar.beginUs && a.bar.endUs==b.bar.endUs && a.bar.open==b.bar.open &&
        a.bar.high==b.bar.high && a.bar.low==b.bar.low && a.bar.close==b.bar.close &&
        a.bar.volume==b.bar.volume && a.bar.tickCount==b.bar.tickCount && a.bar.complete==b.bar.complete &&
        a.sourceCivilDay==b.sourceCivilDay && a.observedAtUs==b.observedAtUs &&
        a.utcOffsetMinutes==b.utcOffsetMinutes && a.turnover==b.turnover &&
        a.hasCumulativeTotals==b.hasCumulativeTotals && a.hasOpenInterest==b.hasOpenInterest &&
        a.cumulativeVolume==b.cumulativeVolume && a.cumulativeTurnover==b.cumulativeTurnover &&
        a.openInterest==b.openInterest && a.hasHighTime==b.hasHighTime && a.hasLowTime==b.hasLowTime &&
        a.highTimeUs==b.highTimeUs && a.lowTimeUs==b.lowTimeUs && a.sourceFields==b.sourceFields;
}
void LegacyBarProfiles() {
    static_assert(!std::is_copy_constructible<LegacyBarCsvReader>::value &&
                  !std::is_move_constructible<LegacyBarCsvReader>::value, "bar cursor borrows input");
    const LegacyBarCsvLayout layouts[] = {LegacyBarCsvLayout::Futures11,LegacyBarCsvLayout::Futures13,LegacyBarCsvLayout::Stock7};
    for (auto layout:layouts) for (int offset:{-840,-480,0,480,840}) for (bool header:{false,true}) {
        const bool stock=layout==LegacyBarCsvLayout::Stock7;
        const auto fields=LegacyBarCells(layout); auto second=LegacyBarCells(layout,stock?3:1);
        std::istringstream input((header?LegacyBarHeader(layout)+"\r\n":"")+LegacyLine(fields)+LegacyLine(second));
        SessionSchedule schedule({Window(kLegacyBarDay-86400000000LL,kLegacyBarDay+86400000000LL,"20260922")});
        LegacyBarCsvReader reader(input,layout,"TEST.FUT",schedule,BarEvidence(offset),header?LegacyBarHeader(layout):"",2);
        LegacyBarRecord out; Check(reader.RowsRead()==0 && reader.Next(out),"first legacy bar");
        const auto begin=kLegacyBarDay-static_cast<std::int64_t>(offset)*60000000LL;
        Check(out.bar.beginUs==begin && out.bar.endUs==begin+(stock?180000000:60000000) &&
              out.bar.tradingDay=="20260922" && out.sourceCivilDay=="20260921", "source label vs UTC vs trading day");
        Check(out.bar.volume==12 && out.turnover==1200 && out.bar.tickCount==8 && out.bar.complete &&
              out.observedAtUs==kLegacyBarDay+86400000000LL && out.sourceFields==fields,"explicit bar values and evidence");
        Check(out.bar.open==100 && out.bar.high==104 && out.bar.low==99 && out.bar.close==102,"legacy OHLC");
        Check(out.hasCumulativeTotals==!stock && out.hasOpenInterest==!stock &&
              out.cumulativeVolume==(stock?0:1000) && out.cumulativeTurnover==(stock?0:100000) &&
              out.openInterest==(stock?0:77),"bar amounts are NOT daily totals");
        Check(out.hasHighTime==(layout==LegacyBarCsvLayout::Futures13) && out.hasLowTime==out.hasHighTime,"optional extrema");
        if (out.hasHighTime) Check(out.highTimeUs==begin+1000000 && out.lowTimeUs==begin+59000000,"extremum epoch and offset");
        out.bar.endUs=std::numeric_limits<std::int64_t>::max(); out.cumulativeVolume=99999999; out.observedAtUs=0;
        Check(reader.Next(out) && out.bar.beginUs==begin+(stock?180000000:60000000) && out.bar.tickCount==9,
              "private previous record does not borrow output");
        const auto before=out;
        Check(!reader.Next(out) && !reader.Next(out) && reader.RowsRead()==2 && SameLegacyBarRecord(before,out),"bar immutable EOF");
    }
    // Numeric zero is the reviewed text fallback. Optional zero extrema stay unknown.
    auto fallback=LegacyBarCells(LegacyBarCsvLayout::Futures13);fallback[0]="0";fallback[11]="0";fallback[12]="0";
    std::istringstream input(LegacyLine(fallback));
    LegacyBarCsvReader reader(input,LegacyBarCsvLayout::Futures13,"TEST.FUT",
        SessionSchedule({Window(kLegacyBarDay,kLegacyBarDay+60000000,"20260921")}),BarEvidence());
    LegacyBarRecord out;Check(reader.Next(out) && out.bar.beginUs==kLegacyBarDay && !out.hasHighTime && !out.hasLowTime,"zero is unknown, not epoch");
    // Subsecond numeric precision is retained, but must agree with civil text.
    auto precise=LegacyBarCells(LegacyBarCsvLayout::Futures13);
    precise[0]=std::to_string(kFileEpoch+static_cast<std::uint64_t>(kLegacyBarDay)+999999);
    std::istringstream p(LegacyLine(precise));LegacyBarCsvReader pr(p,LegacyBarCsvLayout::Futures13,"TEST.FUT",
        SessionSchedule({Window(kLegacyBarDay,kLegacyBarDay+120000000,"20260921")}),BarEvidence());
    Check(pr.Next(out) && out.bar.beginUs==kLegacyBarDay+999999 && out.bar.endUs==kLegacyBarDay+60999999,"numeric microseconds preserved");
}
void LegacyBarRejections() {
    const auto layout=LegacyBarCsvLayout::Futures13;
    const auto good=LegacyBarCells(layout);
    SessionSchedule schedule({Window(kLegacyBarDay-86400000000LL,kLegacyBarDay+86400000000LL,"20260921")});
    auto reject=[&](const std::vector<std::string>& fields,LegacyBarEvidenceResolver evidence) {
        std::istringstream input(LegacyLine(fields));LegacyBarCsvReader r(input,layout,"TEST.FUT",schedule,evidence);
        LegacyBarRecord out;out.bar.instrument="KEEP";out.sourceFields={"unchanged"};const auto before=out;
        Throws([&]{r.Next(out);});Check(r.RowsRead()==0 && SameLegacyBarRecord(out,before),"bad bar atomic publication");
        input.clear();input.str(LegacyLine(good));Throws([&]{r.Next(out);});
        Check(r.RowsRead()==0 && SameLegacyBarRecord(out,before),"bad bar cannot be skipped by stream repair");
    };
    for (std::size_t i:{0u,2u,3u,4u,5u,6u,7u,8u,9u,10u,11u,12u})
        for (const std::string invalid:{"","NaN","inf","-1","1x"," 1","1 ","\"1\""}) {
            auto f=good;f[i]=invalid;reject(f,BarEvidence());
        }
    for (const std::string invalid:{"20260229_000000","20260921_240000","20260921_006000","20260921_000060",
                                   "20260921 000000","2026-09-21 00:00:00","00000101_000000","20260921_00000"}) {
        auto f=good;f[1]=invalid;reject(f,BarEvidence());
    }
    for (const std::string& invalid:std::vector<std::string>{std::to_string(kLegacyBarDay),std::to_string(kFileEpoch-1),
                                   "18446744073709551615","18446744073709551616",
                                   std::to_string(kFileEpoch+static_cast<std::uint64_t>(kLegacyBarDay)+1000000)}) {
        auto f=good;f[0]=invalid;reject(f,BarEvidence());
    }
    for (const auto& pair:std::vector<std::pair<std::size_t,std::string>>{{3,"98"},{4,"103"},{6,"11"},{8,"1199"},
            {11,std::to_string(kFileEpoch+static_cast<std::uint64_t>(kLegacyBarDay)-1)},
            {12,std::to_string(kFileEpoch+static_cast<std::uint64_t>(kLegacyBarDay)+60000000)}}) {
        auto f=good;f[pair.first]=pair.second;reject(f,BarEvidence());
    }
    auto extra=good;extra.push_back("unexpected");reject(extra,BarEvidence());
    auto shortRow=good;shortRow.pop_back();reject(shortRow,BarEvidence());
    auto oversized=good;oversized[1]=std::string(4097,'x');reject(oversized,BarEvidence());
    for (int mode=0;mode<6;++mode) reject(good,[mode](std::size_t,const std::string&,const std::string&) {
        LegacyBarEvidence e;e.complete=true;e.tickCount=1;e.observedAtUs=kLegacyBarDay+60000000;
        if (mode == 0) e.complete = false;
        if (mode == 1) e.tickCount = 0;
        if (mode == 2) --e.observedAtUs;
        if (mode == 3) e.utcOffsetMinutes = 841;
        if (mode == 4) e.utcOffsetMinutes = -841;
        if (mode == 5) throw std::runtime_error("evidence unavailable");
        return e;
    });
    // Missing data on the second row must not inherit OHLC from the first.
    for (int mode=0;mode<6;++mode) {
        auto next=LegacyBarCells(layout,1);
        if (mode == 0) next[2] = "";
        if (mode == 1) next = good;
        if (mode == 2) next[6] = "999";
        if (mode == 3) next[6] = "1001";
        if (mode == 4) next[8] = "99999";
        std::istringstream input(LegacyLine(good)+LegacyLine(next));
        auto resolver=[mode](std::size_t row,const std::string& i,const std::string& d){
            auto e=BarEvidence()(row,i,d);if(mode==5 && row==2)--e.observedAtUs;return e;};
        LegacyBarCsvReader r(input,layout,"TEST.FUT",schedule,resolver);LegacyBarRecord out;
        Check(r.Next(out),"valid bar before failure");const auto before=out;
        Throws([&]{r.Next(out);});Check(r.RowsRead()==1 && SameLegacyBarRecord(out,before),"failed continuation unchanged");
    }
    std::istringstream configInput(LegacyLine(good));const auto pos=configInput.tellg();
    Throws([&]{LegacyBarCsvReader r(configInput,static_cast<LegacyBarCsvLayout>(99),"TEST.FUT",schedule,BarEvidence());});
    Throws([&]{LegacyBarCsvReader r(configInput,layout,"",schedule,BarEvidence());});
    Throws([&]{LegacyBarCsvReader r(configInput,layout,"TEST.FUT",schedule,{});});
    Throws([&]{LegacyBarCsvReader r(configInput,layout,"TEST.FUT",schedule,BarEvidence(),"",0);});
    Throws([&]{LegacyBarCsvReader r(configInput,layout,"TEST.FUT",schedule,BarEvidence(),"wrong,header");});
    Check(configInput.tellg()==pos,"configuration errors do not read input");
    std::istringstream header("wrong\n"+LegacyLine(good));
    Throws([&]{LegacyBarCsvReader r(header,layout,"TEST.FUT",schedule,BarEvidence(),LegacyBarHeader(layout));});
    std::istringstream empty;LegacyBarCsvReader emptyReader(empty,layout,"TEST.FUT",schedule,BarEvidence());LegacyBarRecord out;
    Check(!emptyReader.Next(out) && emptyReader.RowsRead()==0,"empty headerless stream");
    std::istringstream headerOnly(LegacyBarHeader(layout));LegacyBarCsvReader headerReader(headerOnly,layout,"TEST.FUT",schedule,BarEvidence(),LegacyBarHeader(layout));
    Check(!headerReader.Next(out),"header-only stream");
    std::istringstream quota(LegacyLine(good)+LegacyLine(LegacyBarCells(layout,1)));
    LegacyBarCsvReader limited(quota,layout,"TEST.FUT",schedule,BarEvidence(),"",1);
    Check(limited.Next(out),"quota first bar");const auto before=out;Throws([&]{limited.Next(out);});
    Check(limited.RowsRead()==1 && SameLegacyBarRecord(out,before),"bar quota is global published count");
    for (bool gap:{false,true}) {
        std::istringstream input(LegacyLine(good));
        SessionSchedule split({Window(kLegacyBarDay,kLegacyBarDay+30000000,"20260921"),
            Window(kLegacyBarDay+(gap?40000000:30000000),kLegacyBarDay+60000000,"20260921")});
        LegacyBarCsvReader r(input,layout,"TEST.FUT",split,BarEvidence());Throws([&]{r.Next(out);});
    }
    std::istringstream badIo(LegacyLine(good));LegacyBarCsvReader io(badIo,layout,"TEST.FUT",schedule,BarEvidence());
    badIo.setstate(std::ios::badbit);Throws([&]{io.Next(out);});badIo.clear();Throws([&]{io.Next(out);});
    // The stock profile has a distinct end label and no cumulative fields.
    const auto stockGood = LegacyBarCells(LegacyBarCsvLayout::Stock7);
    auto rejectStock = [&](const std::vector<std::string>& cells) {
        std::istringstream source(LegacyLine(cells));
        LegacyBarCsvReader reader(source, LegacyBarCsvLayout::Stock7,
                                  "TEST.FUT", schedule, BarEvidence());
        LegacyBarRecord record; record.sourceFields = {"unchanged"};
        const auto saved = record;
        Throws([&] { reader.Next(record); });
        Check(reader.RowsRead() == 0 && SameLegacyBarRecord(record, saved),
              "stock rejection preserves output and emitted count");
        Throws([&] { reader.Next(record); });
    };
    for (std::size_t column = 1; column < 7; ++column) {
        for (const std::string invalid : {"", "NaN", "inf", "-1", "1x", " 1", "1 ", "\"1\""}) {
            auto cells = stockGood; cells[column] = invalid; rejectStock(cells);
        }
    }
    for (const std::string invalid : {"2026-02-29 00:03:00", "2026-09-21 24:03:00",
            "2026-09-21 00:03:60", "2026/09/21 00:03:00", "20260921_000300"}) {
        auto cells = stockGood; cells[0] = invalid; rejectStock(cells);
    }
    auto stockExtra = stockGood; stockExtra.push_back("unexpected"); rejectStock(stockExtra);
    auto stockShort = stockGood; stockShort.pop_back(); rejectStock(stockShort);
    auto stockOhlc = stockGood; stockOhlc[2] = "98"; rejectStock(stockOhlc);
    auto stock=LegacyBarCells(LegacyBarCsvLayout::Stock7);stock[0]="1970-01-01 00:02:59";
    std::istringstream negativeStart(LegacyLine(stock));
    LegacyBarCsvReader negative(negativeStart,LegacyBarCsvLayout::Stock7,"TEST.FUT",
        SessionSchedule({Window(0,86400000000LL,"19700101")}),
        [](std::size_t,const std::string&,const std::string&){LegacyBarEvidence e;e.complete=true;e.tickCount=1;e.observedAtUs=86400000000LL;return e;});
    Throws([&]{negative.Next(out);});
}
class LegacyBarGeneratedBuffer : public std::streambuf {
public:
    explicit LegacyBarGeneratedBuffer(std::size_t count):count_(count) {}
    std::size_t Generated() const {return generated_;}
protected:
    int_type underflow() override {
        if(gptr()!=egptr())return traits_type::to_int_type(*gptr());
        if(generated_==count_)return traits_type::eof();
        const auto n=generated_++;const auto minute=n%1440;
        std::ostringstream clock;clock<<"202609"<<std::setfill('0')<<std::setw(2)<<21+n/1440<<'_'
            <<std::setw(2)<<minute/60<<std::setw(2)<<minute%60<<"00";
        const auto price=std::to_string(100+n%17);
        line_=LegacyLine({std::to_string(kFileEpoch+static_cast<std::uint64_t>(kLegacyBarDay)+n*60000000ULL),
            clock.str(),price,price,price,price,std::to_string(2*(minute+1)),"2",
            std::to_string(200*(minute+1)),"200","10"});
        setg(&line_[0],&line_[0],&line_[0]+line_.size());return traits_type::to_int_type(*gptr());
    }
private:
    std::size_t count_,generated_=0;std::string line_;
};
void LegacyBarStreamingOracle() {
    const std::size_t count=10000;
    std::vector<SessionWindow> windows;
    for(int day=0;day<7;++day)windows.push_back(Window(kLegacyBarDay+day*86400000000LL,
        kLegacyBarDay+(day+1)*86400000000LL,"202609"+std::to_string(21+day)));
    LegacyBarGeneratedBuffer buffer(count);std::istream input(&buffer);
    LegacyBarCsvReader reader(input,LegacyBarCsvLayout::Futures11,"TEST.FUT",SessionSchedule(windows),
        [](std::size_t row,const std::string&,const std::string&){LegacyBarEvidence e;e.complete=true;e.tickCount=3;
            e.observedAtUs=kLegacyBarDay+static_cast<std::int64_t>(row)*60000000LL+123;return e;},"",count);
    Check(buffer.Generated()==0,"bar constructor has no prefetch");LegacyBarRecord out;
    for(std::size_t n=0;n<count;++n) {
        Check(reader.Next(out) && buffer.Generated()==n+1 && reader.RowsRead()==n+1,"bounded one-row streaming bars");
        Check(out.bar.beginUs==kLegacyBarDay+static_cast<std::int64_t>(n)*60000000LL &&
              out.bar.endUs==out.bar.beginUs+60000000 && out.bar.volume==2 && out.bar.tickCount==3 &&
              out.bar.close==static_cast<double>(100+n%17) && out.cumulativeVolume==static_cast<std::int64_t>(2*(n%1440+1)) &&
              out.observedAtUs==out.bar.endUs+123 && out.bar.tradingDay=="202609"+std::to_string(21+n/1440),
              "independent bar clock/counter/price oracle across trading-day resets");
    }
    const auto before=out;Check(!reader.Next(out) && !reader.Next(out) && SameLegacyBarRecord(out,before),"nonseekable bar EOF");
}

// CSV consumers commonly enable stream exceptions. An EOF signal from get()
// must not turn a valid final row (or an exhausted cursor) into a poisoned run.
// All schemas call the same bounded reader; exercise each public entry point.
void CsvExceptionMasks() {
    const std::string tickHeader = "instrument,timestamp_us,sequence,price,volume";
    const std::string barHeader = "instrument,trading_day,begin_us,end_us,open,high,low,close,volume,tick_count,complete";
    const SessionSchedule schedule({Window(kLegacyBarDay, kLegacyBarDay+86400000000LL,"20260921")});
    std::size_t cases = 0;
    for (unsigned bits = 0; bits != 8; ++bits) {
        std::ios::iostate mask = std::ios::goodbit;
        if (bits & 1) mask |= std::ios::eofbit;
        if (bits & 2) mask |= std::ios::failbit;
        if (bits & 4) mask |= std::ios::badbit;
        for (const std::string& ending : {std::string(""), std::string("\n"), std::string("\r\n")}) {
            {
                std::istringstream input(tickHeader+"\nA,1,1,100,2"+ending); input.exceptions(mask);
                TickCsvReader reader(input,1); Tick out;
                Check(reader.Next(out) && out.price==100 && out.volume==2,"throwing Tick final record");
                const auto saved=out;
                Check(!reader.Next(out) && !reader.Next(out) && reader.RowsRead()==1 &&
                      out.instrument==saved.instrument && out.sequence==saved.sequence &&
                      out.price==saved.price && out.volume==saved.volume,"throwing Tick stable EOF");
                Check(input.exceptions()==mask && input.eof() && !input.bad(),"Tick stream state is not cleared");
                ++cases;
            }
            {
                std::istringstream input(barHeader+"\nA,20260921,0,10,100,100,100,100,2,1,1"+ending);
                input.exceptions(mask); BarCsvReader reader(input,1); Bar out;
                Check(reader.Next(out) && out.complete && out.volume==2,"throwing Bar final record");
                const auto saved=out;
                Check(!reader.Next(out) && !reader.Next(out) && reader.RowsRead()==1 && SameBar(out,saved),
                      "throwing Bar stable EOF");
                Check(input.exceptions()==mask && input.eof() && !input.bad(),"Bar stream state is not cleared");
                ++cases;
            }
            {
                std::istringstream first(tickHeader+"\nA,1,1,100,2"+ending);
                std::istringstream second(tickHeader+"\nB,2,1,101,3"+ending);
                std::istringstream empty(tickHeader+ending);
                first.exceptions(mask); second.exceptions(mask); empty.exceptions(mask);
                MergedTickCsvReader reader({&first,&empty,&second},2); Tick out;
                Check(reader.Next(out) && out.instrument=="A","throwing merge first source");
                Check(reader.Next(out) && out.instrument=="B","throwing merge final source");
                Check(!reader.Next(out) && !reader.Next(out) && reader.RowsRead()==2 && out.instrument=="B",
                      "throwing merge preserves terminal output");
                Check(first.exceptions()==mask && second.exceptions()==mask && empty.exceptions()==mask,
                      "merge preserves borrowed input exception policies");
                ++cases;
            }
            {
                std::istringstream input("open_us,close_us,trading_day\n0,100,20260921"+ending);
                input.exceptions(mask); const auto windows=ReadSessionsCsv(input,1);
                Check(windows.At(99).closeUs==100 && input.exceptions()==mask && input.eof() && !input.bad(),
                      "throwing session final record"); ++cases;
            }
            // Header-only and completely empty files remain distinct. A valid
            // header at EOF is accepted; a missing header is still rejected.
            for (const bool bars : {false,true}) {
                std::istringstream empty((bars?barHeader:tickHeader)+ending); empty.exceptions(mask);
                if (bars) {
                    BarCsvReader reader(empty); Bar out; out.instrument="unchanged";
                    Check(!reader.Next(out) && !reader.Next(out) && reader.RowsRead()==0 && out.instrument=="unchanged",
                          "header-only Bar dataset");
                } else {
                    TickCsvReader reader(empty); Tick out; out.instrument="unchanged";
                    Check(!reader.Next(out) && !reader.Next(out) && reader.RowsRead()==0 && out.instrument=="unchanged",
                          "header-only Tick dataset");
                }
                Check(empty.exceptions()==mask && empty.eof() && !empty.bad(),"header-only stream policy retained");
                ++cases;
            }
            for (const auto& profile : legacyProfiles) for (const bool withHeader : {false,true}) {
                std::istringstream input((withHeader?LegacyTestHeader(profile):"")+
                    LegacyLine(LegacyCells(profile),ending)); input.exceptions(mask);
                LegacyTickCsvReader reader(input,profile.layout,AnyLegacyTime(),FixedLegacyClock(),true,withHeader,1);
                LegacyTickRecord out; Check(reader.Next(out) && out.tick.volume==100,"throwing legacy Tick final row");
                const auto saved=out;
                Check(!reader.Next(out) && !reader.Next(out) && reader.RowsRead()==1 && SameLegacyRecord(out,saved),
                      "throwing legacy Tick stable EOF");
                Check(input.exceptions()==mask && input.eof() && !input.bad(),"legacy Tick policy retained");
                ++cases;
            }
            for (const auto layout : {LegacyBarCsvLayout::Futures11,LegacyBarCsvLayout::Futures13,LegacyBarCsvLayout::Stock7})
                for (const bool withHeader : {false,true}) {
                    const auto header=withHeader?LegacyBarHeader(layout):"";
                    std::istringstream input((withHeader?header+"\n":"")+LegacyLine(LegacyBarCells(layout),ending));
                    input.exceptions(mask);
                    LegacyBarCsvReader reader(input,layout,"TEST.FUT",schedule,BarEvidence(),header,1);
                    LegacyBarRecord out; Check(reader.Next(out) && out.bar.volume==12,"throwing legacy Bar final row");
                    const auto saved=out;
                    Check(!reader.Next(out) && !reader.Next(out) && reader.RowsRead()==1 && SameLegacyBarRecord(out,saved),
                          "throwing legacy Bar stable EOF");
                    Check(input.exceptions()==mask && input.eof() && !input.bad(),"legacy Bar policy retained");
                    ++cases;
                }
        }
        {
            std::istringstream empty; empty.exceptions(mask);
            Throws([&]{TickCsvReader reader(empty);});
        }
        {
            std::istringstream input(tickHeader+"\nA,1,1,100,2"); input.exceptions(mask);
            Check(ReadTicksCsv(input,1).size()==1,"eager Tick delegates clean EOF semantics");
            std::istringstream bars(barHeader+"\nA,20260921,0,10,100,100,100,100,2,1,1"); bars.exceptions(mask);
            Check(ReadBarsCsv(bars,1).size()==1,"eager Bar delegates clean EOF semantics");
        }
        // Invalid final data remains invalid, regardless of whether get() also
        // raises the caller-requested EOF exception. No malformed row is skipped.
        for (const std::string& suffix : {std::string("A,2,2,NaN,1"),std::string(4097,'x'),
                                          std::string("A,2,2,100,1\nA,3,3,101,1")}) {
            std::istringstream input(tickHeader+"\nA,1,1,100,2\n"+suffix); input.exceptions(mask);
            TickCsvReader reader(input,suffix.find('\n')==std::string::npos?2:1); Tick out; Check(reader.Next(out),"exception policy valid prefix");
            Throws([&]{reader.Next(out);});
            Check(out.sequence==1 && out.volume==2 && reader.RowsRead()==1 && input.exceptions()==mask,
                  "corruption/line bound/quota rejects without output publication");
            input.clear(); Throws([&]{reader.Next(out);});
        }
    }
    std::cout << "CSV clean-EOF schema/exception-mask/line-ending cases=" << cases << '\n';
}

// Non-seekable input that throws after a selected prefix, rather than ending.
// Even a syntactically complete but unterminated row must NOT be published when
// the backing source fails. Test both ios_base::failure and other exceptions.
class FaultingCsvBuffer : public std::streambuf {
public:
    FaultingCsvBuffer(std::string prefix,bool iosFailure):prefix_(std::move(prefix)),iosFailure_(iosFailure) {
        setg(&prefix_[0],&prefix_[0],&prefix_[0]+prefix_.size());
    }
protected:
    int_type underflow() override {
        if (iosFailure_) throw std::ios_base::failure("injected CSV source failure");
        throw std::runtime_error("injected CSV source failure");
    }
private:
    std::string prefix_;
    bool iosFailure_;
};
void CsvThrowingSourceFailures() {
    const std::string header="instrument,timestamp_us,sequence,price,volume\n";
    for(unsigned bits=0;bits!=8;++bits) for(bool iosFailure:{false,true}) {
        std::ios::iostate mask=std::ios::goodbit;
        if(bits&1) mask|=std::ios::eofbit;
        if(bits&2) mask|=std::ios::failbit;
        if(bits&4) mask|=std::ios::badbit;
        for(const std::string& suffix:{std::string(""),std::string("A,2,2,100"),std::string("A,2,2,100,1")}) {
            FaultingCsvBuffer buffer(header+"A,1,1,100,1\n"+suffix,iosFailure);
            std::istream input(&buffer); input.exceptions(mask); TickCsvReader reader(input); Tick out;
            Check(reader.Next(out),"fault input valid prefix"); Throws([&]{reader.Next(out);});
            Check(reader.RowsRead()==1 && out.sequence==1 && input.bad() && input.exceptions()==mask,
                  "real source failure is never successful EOF or a salvaged final row");
            input.clear(); Throws([&]{reader.Next(out);});
        }
        FaultingCsvBuffer barBuffer(LegacyLine(LegacyBarCells(LegacyBarCsvLayout::Futures11),""),iosFailure);
        std::istream barInput(&barBuffer); barInput.exceptions(mask);
        LegacyBarCsvReader barReader(barInput,LegacyBarCsvLayout::Futures11,"TEST.FUT",
            SessionSchedule({Window(kLegacyBarDay,kLegacyBarDay+86400000000LL,"20260921")}),BarEvidence());
        LegacyBarRecord out; out.sourceCivilDay="unchanged"; const auto saved=out;
        Throws([&]{barReader.Next(out);});
        Check(barInput.bad() && SameLegacyBarRecord(out,saved) && barReader.RowsRead()==0,
              "legacy source error cannot publish syntactically complete row");
        barInput.clear(); Throws([&]{barReader.Next(out);});
    }
    // A previously set badbit may coexist with eofbit; EOF must not mask it.
    std::istringstream input(header); TickCsvReader reader(input); Tick out; out.instrument="unchanged";
    input.setstate(std::ios::eofbit|std::ios::badbit);
    try {input.exceptions(std::ios::eofbit|std::ios::badbit|std::ios::failbit);} catch(const std::ios_base::failure&) {}
    Throws([&]{reader.Next(out);});
    Check(out.instrument=="unchanged" && reader.RowsRead()==0 && input.bad(),"badbit plus EOF remains an error");
}

// Independent, explicitly timed fixtures; the parser never derives duration
// from these labels or from spacing. All clocks stay within this civil day.
std::vector<std::string> TimedLegacyBarCells(LegacyBarCsvLayout layout,
    std::int64_t labelOffsetUs, std::int64_t periodUs, std::size_t row) {
    auto cells = LegacyBarCells(layout);
    const bool stock = layout == LegacyBarCsvLayout::Stock7;
    const auto seconds = labelOffsetUs / 1000000;
    std::ostringstream time; time.imbue(std::locale::classic()); time << std::setfill('0');
    time << (stock ? "2026-09-21 " : "20260921_") << std::setw(2) << seconds/3600;
    if (stock) time << ':';
    time << std::setw(2) << seconds/60%60;
    if (stock) time << ':';
    time << std::setw(2) << seconds%60;
    cells[stock ? 0 : 1] = time.str();
    if (!stock) {
        const auto stamp = kFileEpoch + static_cast<std::uint64_t>(kLegacyBarDay + labelOffsetUs);
        cells[0] = std::to_string(stamp);
        cells[6] = std::to_string(1000+12*row); cells[8] = std::to_string(100000+1200*row);
        if (layout == LegacyBarCsvLayout::Futures13) {
            cells[11] = std::to_string(stamp);
            cells[12] = std::to_string(stamp+static_cast<std::uint64_t>(periodUs)-1);
        }
    }
    return cells;
}
void LegacyExplicitBarPeriods() {
    const std::int64_t periods[] = {1,999999,1000000,7000000,30000000,60000000,
                                   180000000,300000000,3600000000LL};
    for (const auto layout : {LegacyBarCsvLayout::Futures11,LegacyBarCsvLayout::Futures13,
                              LegacyBarCsvLayout::Stock7})
      for (const auto period : periods) for (int offset : {-840,0,840}) for (bool header : {false,true}) {
        const bool stock = layout == LegacyBarCsvLayout::Stock7;
        const auto stride = ((period+999999)/1000000)*1000000;
        // Futures keep numeric subsecond precision. Stock text ends are second
        // labelled; a shorter explicit interval creates a gap, not fake ticks.
        const std::int64_t origin = 3600000000LL + (stock ? 0 : 123456);
        const auto utcOrigin = kLegacyBarDay+origin-static_cast<std::int64_t>(offset)*60000000;
        std::vector<std::vector<std::string>> rows;
        std::string data = header ? LegacyBarHeader(layout)+"\r\n" : "";
        for (std::size_t n=0; n<4; ++n) {
            rows.push_back(TimedLegacyBarCells(layout,origin+static_cast<std::int64_t>(n)*stride,period,n));
            data += LegacyLine(rows.back(), n==3 ? "" : "\r\n");
        }
        std::istringstream input(data); input.exceptions(std::ios::badbit|std::ios::failbit|std::ios::eofbit);
        const SessionSchedule schedule({Window(kLegacyBarDay-86400000000LL,
            kLegacyBarDay+172800000000LL,"20260922")});
        std::size_t calls=0;
        LegacyBarEvidenceResolver evidence = [&](std::size_t row,const std::string& symbol,const std::string& day) {
            Check(row==++calls && symbol=="TEST.FUT" && day=="20260921","explicit-period evidence identity");
            LegacyBarEvidence e; e.complete=true; e.tickCount=7+row; e.utcOffsetMinutes=offset;
            e.observedAtUs=utcOrigin+static_cast<std::int64_t>(row-1)*stride+(stock?0:period)+1000000;
            return e;
        };
        LegacyBarCsvReader reader(input,layout,"TEST.FUT",schedule,evidence,period,
                                   header?LegacyBarHeader(layout):"",4);
        Check(calls==0 && reader.RowsRead()==0,"duration configuration consumes no data row");
        LegacyBarRecord out;
        for (std::size_t n=0;n<4;++n) {
            const auto label=utcOrigin+static_cast<std::int64_t>(n)*stride;
            Check(reader.Next(out) && out.bar.beginUs==label-(stock?period:0) &&
                  out.bar.endUs==label+(stock?0:period) && out.bar.endUs-out.bar.beginUs==period,
                  "explicit duration and unchanged label convention");
            Check(out.bar.volume==12 && out.bar.tickCount==8+n && out.bar.complete && out.turnover==1200 &&
                  out.bar.tradingDay=="20260922" && out.sourceCivilDay=="20260921" && out.sourceFields==rows[n] &&
                  out.observedAtUs==out.bar.endUs+1000000,"duration invents neither quantity nor availability");
            if (layout==LegacyBarCsvLayout::Futures13)
                Check(out.highTimeUs==out.bar.beginUs && out.lowTimeUs==out.bar.endUs-1,"exact extremum interval");
        }
        const auto saved=out;
        Check(!reader.Next(out) && !reader.Next(out) && calls==4 && reader.RowsRead()==4 &&
              SameLegacyBarRecord(saved,out),"explicit-period throwing-stream EOF is stable");
        // Both constructor symbols remain usable; an explicit default must
        // agree field-for-field with the retained original SDK overload.
        if (period==(stock?180000000:60000000)) {
            std::istringstream oldInput(data); calls=0;
            LegacyBarCsvReader original(oldInput,layout,"TEST.FUT",schedule,evidence,
                                         header?LegacyBarHeader(layout):"",4);
            for (std::size_t n=0;n<4;++n) Check(original.Next(out),"old duration overload remains available");
            Check(SameLegacyBarRecord(saved,out),"explicit default and original overload disagree");
        }
      }
}
void LegacyExplicitBarPeriodFailures() {
    const auto layout=LegacyBarCsvLayout::Futures13;
    const SessionSchedule full({Window(kLegacyBarDay,kLegacyBarDay+86400000000LL,"20260921")});
    const auto valid=TimedLegacyBarCells(layout,0,7000000,0);
    const std::int64_t invalidPeriods[]={0,-1,std::numeric_limits<std::int64_t>::min()};
    for (const auto period:invalidPeriods) {
        std::istringstream input(LegacyBarHeader(layout)+"\n"+LegacyLine(valid));
        Throws([&]{LegacyBarCsvReader reader(input,layout,"TEST.FUT",full,BarEvidence(),period,LegacyBarHeader(layout));});
        Check(input.tellg()==0,"invalid duration consumed borrowed input/header");
    }
    auto rejects=[&](const std::vector<std::string>& fields, std::int64_t period,
                     const SessionSchedule& schedule, const LegacyBarEvidenceResolver& evidence) {
        std::istringstream input(LegacyLine(fields));
        LegacyBarCsvReader reader(input,layout,"TEST.FUT",schedule,evidence,period);
        LegacyBarRecord out; out.sourceFields={"UNCHANGED"}; const auto saved=out;
        Throws([&]{reader.Next(out);});
        Check(reader.RowsRead()==0 && SameLegacyBarRecord(saved,out),"invalid explicit interval published state");
        input.clear(); input.str(LegacyLine(valid)); Throws([&]{reader.Next(out);});
        Check(reader.RowsRead()==0 && SameLegacyBarRecord(saved,out),"failed period cursor resumed");
    };
    rejects(valid,std::numeric_limits<std::int64_t>::max(),full,BarEvidence());
    auto outside=valid; outside[12]=std::to_string(kFileEpoch+static_cast<std::uint64_t>(kLegacyBarDay)+7000000);
    rejects(outside,7000000,full,BarEvidence());
    rejects(valid,7000000,full,[](std::size_t,const std::string&,const std::string&){
        LegacyBarEvidence e;e.complete=true;e.tickCount=1;e.observedAtUs=kLegacyBarDay+6999999;return e;});
    for (bool gap:{false,true}) {
        const SessionSchedule split({Window(kLegacyBarDay,kLegacyBarDay+3000000,"20260921"),
            Window(kLegacyBarDay+(gap?4000000:3000000),kLegacyBarDay+7000000,"20260921")});
        rejects(valid,7000000,split,BarEvidence());
    }
    // Positive duration cannot underflow a stock end label at the Unix epoch.
    auto stock=LegacyBarCells(LegacyBarCsvLayout::Stock7);stock[0]="1970-01-01 00:00:00";
    std::istringstream early(LegacyLine(stock));
    LegacyBarCsvReader underflow(early,LegacyBarCsvLayout::Stock7,"TEST.FUT",
        SessionSchedule({Window(0,1000000,"19700101")}),
        [](std::size_t,const std::string&,const std::string&){LegacyBarEvidence e;e.complete=true;e.tickCount=1;e.observedAtUs=1;return e;},1);
    LegacyBarRecord out;Throws([&]{underflow.Next(out);});Check(underflow.RowsRead()==0,"stock interval underflow");
    // An extended duration may overlap the next labelled row: never infer a
    // replacement interval from spacing or quietly truncate the first bar.
    std::istringstream overlap(LegacyLine(LegacyBarCells(layout))+LegacyLine(LegacyBarCells(layout,1)));
    LegacyBarCsvReader overlapping(overlap,layout,"TEST.FUT",full,BarEvidence(),90000000);
    Check(overlapping.Next(out) && out.bar.endUs==kLegacyBarDay+90000000,"explicit longer first interval");
    const auto saved=out;Throws([&]{overlapping.Next(out);});
    Check(overlapping.RowsRead()==1 && SameLegacyBarRecord(saved,out),"overlap changed previous output");
    std::istringstream quota(LegacyLine(valid)+LegacyLine(TimedLegacyBarCells(layout,7000000,7000000,1)));
    LegacyBarCsvReader limited(quota,layout,"TEST.FUT",full,BarEvidence(),7000000,"",1);
    Check(limited.Next(out),"explicit-period quota first row");const auto first=out;
    Throws([&]{limited.Next(out);});Check(limited.RowsRead()==1 && SameLegacyBarRecord(first,out),"explicit-period quota bypass");
    // Existing non-seekable generator with deliberately spaced 30-second bars.
    // The reader retains one prior record, not a history or a resampling queue.
    LegacyBarGeneratedBuffer buffer(1000); std::istream stream(&buffer);
    LegacyBarCsvReader sparse(stream,LegacyBarCsvLayout::Futures11,"TEST.FUT",full,BarEvidence(),30000000,"",1000);
    Check(buffer.Generated()==0,"explicit-period constructor prefetched data");
    for(std::size_t n=0;n<1000;++n) {
        Check(sparse.Next(out) && buffer.Generated()==n+1 && out.bar.beginUs==kLegacyBarDay+static_cast<std::int64_t>(n)*60000000 &&
              out.bar.endUs-out.bar.beginUs==30000000,"explicit-period bounded sparse stream");
    }
    Check(!sparse.Next(out) && sparse.RowsRead()==1000,"sparse stream EOF");
}

void EndOfInputLifecycle() {
    const SessionSchedule schedule({Window(0,10,"20260921"),Window(20,30,"20260921"),Window(40,50,"20260922")});
    Bar out; out.instrument="UNCHANGED";
    BarBuilder empty("TEST.FUT",0,schedule);
    Check(!empty.Finish(out) && empty.Finished() && out.instrument=="UNCHANGED","empty EOF");
    Check(!empty.Finish(out),"repeated empty EOF");
    Throws([&]{empty.Push(T(1,1,100),out);});
    Throws([&]{empty.AdvanceWatermark(100,out);});
    for (std::int64_t period : {0,5,50}) {
        BarBuilder builder("TEST.FUT",period,schedule);
        builder.Push(T(1,1,100,7),out);
        Bar before;Check(builder.Current(before),"current before EOF");
        Check(builder.Finish(out) && builder.Finished() && !out.complete && out.volume==7 && out.tickCount==1 &&
              out.beginUs==before.beginUs && out.endUs==before.endUs && out.close==before.close,"EOF tail changed");
        out.instrument="UNCHANGED";
        Check(!builder.Finish(out) && out.instrument=="UNCHANGED" && !builder.Current(out),"EOF tail repeated");
        Throws([&]{builder.Push(T(1,1,100,7),out);});
        Throws([&]{builder.Push(T(2,2,101,2),out);});
        Throws([&]{builder.AdvanceWatermark(30,out);});
    }
    BarBuilder daily("TEST.FUT",0,schedule);
    daily.Push(T(1,1,100,4),out);
    Check(!daily.AdvanceWatermark(10,out),"session break became day completion");
    daily.Push(T(21,2,110,6),out);
    Check(daily.Finish(out) && !out.complete && out.beginUs==0 && out.endUs==30 &&
          out.open==100 && out.close==110 && out.volume==10 && out.tickCount==2,"cross-break daily EOF");
    BarBuilder completed("TEST.FUT",0,schedule);
    completed.Push(T(1,1,100,7),out);
    Check(completed.AdvanceWatermark(30,out) && out.complete,"explicit completeness watermark");
    Check(!completed.Finish(out) && out.complete,"EOF changed already published completed bar");
}

}
int main() { return Run([] { SessionsAndBars(); CsvAndCumulative(); SeriesAndOracle();
    QueryBoundaries(); QueryOracle(); BarCsvContract(); BoundedMeans(); StreamingCsv(); StreamingBarsCsv(); MergedCsv(); MergedCsvOracle(); LegacyCsvProfiles(); LegacyCsvRejection(); LegacyCsvStreamingOracle(); LegacyBarProfiles(); LegacyBarRejections(); LegacyBarStreamingOracle(); CsvExceptionMasks(); CsvThrowingSourceFailures(); LegacyExplicitBarPeriods(); LegacyExplicitBarPeriodFailures(); EndOfInputLifecycle(); }); }
