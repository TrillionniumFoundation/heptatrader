#include "hepta/research/analytics.h"
#include "hepta/research/market_data.h"
#include "hepta/research/replay.h"
#include "hepta/research/strategy.h"
#include <cmath>
#include <cstdio>
#include <memory>
#include <sstream>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <locale>
#include <map>
#include <set>
#include <algorithm>
#include <stdexcept>
#include <string>
#include <vector>
#include <utility>

using namespace hepta::research;
namespace {
long long Integer(const std::string& s) {
    if (s.empty()) throw std::invalid_argument("empty integer");
    for (char c : s) if (c < '0' || c > '9') throw std::invalid_argument("unsigned decimal integer required");
    std::size_t n = 0; const auto v = std::stoll(s, &n);
    if (n != s.size()) throw std::invalid_argument("invalid integer");
    return v;
}
void Check(bool condition, const char* reason) {
    if (!condition) throw std::invalid_argument(reason);
}
std::uint64_t UnsignedInteger(const std::string& text) {
    Check(!text.empty(), "empty unsigned integer");
    for (char c : text) Check(c >= '0' && c <= '9', "unsigned decimal integer required");
    std::size_t used = 0;
    const auto value = std::stoull(text, &used);
    Check(used == text.size() && value <= std::numeric_limits<std::uint64_t>::max(),
          "unsigned integer out of range");
    return static_cast<std::uint64_t>(value);
}
int UtcOffset(const std::string& text) {
    const bool negative = !text.empty() && text[0] == '-';
    const auto magnitude = UnsignedInteger(negative ? text.substr(1) : text);
    Check(magnitude <= 840, "UTC offset must be between -840 and 840 minutes");
    return (negative ? -1 : 1) * static_cast<int>(magnitude);
}
// This small sidecar is an explicit caller-supplied binding, not an exchange
// calendar, receipt-time oracle, or authenticated provenance certificate.
// Source data decoding remains in the existing Data SDK.
class RowEvidence {
public:
    RowEvidence(std::istream& input, const char* header) : input_(input) {
        std::string line;
        Check(Line(line) && line == header, "research evidence header mismatch");
    }
    std::vector<std::string> Next(std::size_t row, const std::string& instrument,
                                   const std::string& day, std::size_t columns) {
        std::string line;
        Check(Line(line), "research evidence ended before source data");
        std::vector<std::string> fields;
        std::size_t begin = 0;
        for (;;) {
            const auto end = line.find(',', begin);
            const auto length = (end == std::string::npos ? line.size() : end) - begin;
            Check(length > 0 && length <= 128 && fields.size() < columns,
                  "research evidence field count or length invalid");
            auto field = line.substr(begin, length);
            for (unsigned char c : field)
                Check(c >= 32 && c <= 126 && c != '"', "research evidence field invalid");
            fields.push_back(std::move(field));
            if (end == std::string::npos) break;
            begin = end + 1;
        }
        Check(fields.size() == columns && UnsignedInteger(fields[0]) == row &&
              fields[1] == instrument && fields[2] == day,
              "research evidence row, instrument, or day mismatch");
        return fields;
    }
    void Finish() {
        std::string line;
        Check(!Line(line), "research evidence has an unmatched trailing row");
    }
private:
    bool Line(std::string& line) {
        line.clear();
        for (;;) {
            const auto c = input_.get();
            if (c == std::char_traits<char>::eof()) {
                if (input_.bad() || !input_.eof())
                    throw std::runtime_error("research evidence read failed");
                if (line.empty()) return false;
                break;
            }
            if (c == '\n') break;
            Check(line.size() < 4096, "research evidence line too long");
            line.push_back(static_cast<char>(c));
        }
        if (!line.empty() && line.back() == '\r') line.pop_back();
        return true;
    }
    std::istream& input_;
};
// Validate the WHOLE input/evidence pair before releasing stdout. The temporary
// file uses bounded RAM and is closed on every path. It is neither durable
// strategy state nor an execution outbox. Disk space scales with output size.
class ValidatedOutput {
public:
    ValidatedOutput() : file_(std::tmpfile(), &std::fclose) {
        if (!file_) throw std::runtime_error("cannot create research output spool");
    }
    void Append(const std::string& text) {
        if (std::fwrite(text.data(), 1, text.size(), file_.get()) != text.size())
            throw std::runtime_error("research output spool write failed");
    }
    void Publish(std::ostream& output) {
        if (std::fflush(file_.get()) != 0 || std::fseek(file_.get(), 0, SEEK_SET) != 0)
            throw std::runtime_error("research output spool rewind failed");
        char buffer[8192];
        for (;;) {
            const auto count = std::fread(buffer, 1, sizeof(buffer), file_.get());
            if (count) output.write(buffer, static_cast<std::streamsize>(count));
            if (!output) throw std::runtime_error("research output failed");
            if (count < sizeof(buffer)) {
                if (std::ferror(file_.get())) throw std::runtime_error("research output spool read failed");
                break;
            }
        }
        output.flush();
        if (!output) throw std::runtime_error("research output failed");
    }
private:
    std::unique_ptr<std::FILE, int(*)(std::FILE*)> file_;
};
#include "model_cli.h"

void LegacyUsage(std::ostream& out) {
    out << "Offline legacy input modes (one explicitly bound instrument):\n"
        << "  hepta-research-replay --import-legacy-ticks LAYOUT RAW.csv CLOCKS.csv SESSIONS.csv INSTRUMENT baseline|day-start headerless|header [MAX_ROWS]\n"
        << "  hepta-research-replay --forecast-legacy-bars LAYOUT RAW.csv EVIDENCE.csv SESSIONS.csv INSTRUMENT FAST SLOW [MAX_ROWS] [--period-us POSITIVE_MICROSECONDS]\n"
        << "Tick layouts: Hepta32 Immsg34 Immsg35 Zs58. Bar layouts: Futures11 Futures13 Stock7 (headerless).\n"
        << "Tick evidence header: row,instrument,trading_day,action_day,utc_offset_minutes\n"
        << "Bar evidence header: row,instrument,source_civil_day,utc_offset_minutes,tick_count,observed_at_us,complete\n"
        << "MAX_ROWS: 1..10000000; default 1000000. No clock/completion evidence is inferred. No broker access.\n";
}
int LegacyInput(int argc, char** argv, bool ticks) {
    // The optional duration belongs ONLY to completed-bar input. Its position
    // is unambiguous and leaves every original positional invocation intact.
    int positionalCount = argc;
    std::int64_t periodUs = 0;
    if (!ticks && (argc == 11 || argc == 12)) {
        Check(std::string(argv[argc - 2]) == "--period-us", "explicit --period-us option required");
        periodUs = Integer(argv[argc - 1]);
        Check(periodUs > 0, "research bar period must be positive");
        positionalCount -= 2;
    }
    if (positionalCount != 9 && positionalCount != 10) { LegacyUsage(std::cerr); return 2; }
    const auto quota = positionalCount == 10 ? Integer(argv[9]) : 1000000LL;
    Check(quota > 0 && quota <= 10000000, "research row quota must be 1..10000000");
    const std::string layoutName = argv[2];
    const std::map<std::string, LegacyTickCsvLayout> tickLayouts = {
        {"Hepta32", LegacyTickCsvLayout::Hepta32}, {"Immsg34", LegacyTickCsvLayout::Immsg34},
        {"Immsg35", LegacyTickCsvLayout::Immsg35}, {"Zs58", LegacyTickCsvLayout::Zs58}};
    const std::map<std::string, LegacyBarCsvLayout> barLayouts = {
        {"Futures11", LegacyBarCsvLayout::Futures11}, {"Futures13", LegacyBarCsvLayout::Futures13},
        {"Stock7", LegacyBarCsvLayout::Stock7}};
    Check(ticks ? tickLayouts.count(layoutName) != 0 : barLayouts.count(layoutName) != 0,
          "unsupported explicit research input layout");
    const std::string firstVolume = argv[7], header = argv[8];
    long long fast = 0, slow = 0;
    if (ticks) {
        Check(firstVolume == "baseline" || firstVolume == "day-start", "explicit first-volume policy required");
        Check(header == "headerless" || header == "header", "explicit source header policy required");
    } else {
        fast = Integer(argv[7]); slow = Integer(argv[8]);
        Check(fast > 0 && slow > fast && slow <= 1000000, "invalid forecast window bounds");
    }
    std::ifstream raw(argv[3]), evidenceFile(argv[4]), sessionFile(argv[5]);
    if (!raw || !evidenceFile || !sessionFile) throw std::runtime_error("cannot open research input/evidence");
    const std::string instrument = argv[6];
    const auto schedule = ReadSessionsCsv(sessionFile);
    ValidatedOutput output;
    if (ticks) {
        RowEvidence evidence(evidenceFile, "row,instrument,trading_day,action_day,utc_offset_minutes");
        std::map<std::string, SessionSchedule> bindings;
        bindings.emplace(instrument, schedule);
        LegacyTickCsvReader input(raw, tickLayouts.at(layoutName), std::move(bindings),
            [&evidence](std::size_t row, const std::string& symbol, const std::string& day,
                        const std::string&) {
                const auto cells = evidence.Next(row, symbol, day, 5);
                LegacyTickClock clock;
                clock.actionDay = cells[3]; clock.utcOffsetMinutes = UtcOffset(cells[4]);
                return clock;
            }, firstVolume == "day-start", header == "header", static_cast<std::size_t>(quota));
        LegacyTickRecord record;
        while (input.Next(record)) {
            // Reuse the existing portable codec with a bounded ONE-row vector;
            // subsequent records omit only that codec's repeated header line.
            std::ostringstream encoded;
            WriteTicksCsv(encoded, {record.tick});
            const auto text = encoded.str();
            const auto newline = text.find('\n');
            Check(newline != std::string::npos, "portable tick codec produced no header");
            output.Append(input.RowsRead() == 1 ? text : text.substr(newline + 1));
        }
        Check(input.RowsRead() > 0, "empty legacy tick dataset");
        evidence.Finish();
    } else {
        RowEvidence evidence(evidenceFile,
            "row,instrument,source_civil_day,utc_offset_minutes,tick_count,observed_at_us,complete");
        LegacyBarEvidenceResolver resolve =
            [&evidence](std::size_t row, const std::string& symbol, const std::string& day) {
                const auto cells = evidence.Next(row, symbol, day, 7);
                Check(cells[6] == "0" || cells[6] == "1", "bar completion must be explicit 0 or 1");
                LegacyBarEvidence value;
                value.utcOffsetMinutes = UtcOffset(cells[3]);
                value.tickCount = UnsignedInteger(cells[4]);
                value.observedAtUs = Integer(cells[5]); value.complete = cells[6] == "1";
                return value;
            };
        // Keep profile defaults in the Data SDK's original overload rather
        // than maintaining another table of durations in the executable.
        std::unique_ptr<LegacyBarCsvReader> input(periodUs > 0
            ? new LegacyBarCsvReader(raw, barLayouts.at(layoutName), instrument, schedule,
                                     resolve, periodUs, "", static_cast<std::size_t>(quota))
            : new LegacyBarCsvReader(raw, barLayouts.at(layoutName), instrument, schedule,
                                     resolve, "", static_cast<std::size_t>(quota)));
        MovingAverageForecast strategy(static_cast<std::size_t>(fast), static_cast<std::size_t>(slow));
        output.Append("instrument,trading_day,bar_begin_us,bar_end_us,observed_at_us,direction\n");
        LegacyBarRecord record;
        while (input->Next(record)) {
            Forecast forecast;
            if (!strategy.ObserveCompletedBar(record.bar, record.observedAtUs, forecast)) continue;
            std::ostringstream encoded; encoded.imbue(std::locale::classic());
            encoded << forecast.instrument << ',' << record.bar.tradingDay << ','
                    << record.bar.beginUs << ',' << record.bar.endUs << ','
                    << forecast.observedAtUs << ',' << forecast.direction << '\n';
            output.Append(encoded.str());
        }
        Check(input->RowsRead() > 0, "empty legacy bar dataset");
        evidence.Finish();
    }
    output.Publish(std::cout);
    return 0;
}
}
int main(int argc, char** argv) {
    try {
        if (argc > 1 && std::string(argv[1]) == "--model-stream")
            return ModelInput(argc);
        if (argc > 1 && std::string(argv[1]) == "--import-legacy-ticks")
            return LegacyInput(argc, argv, true);
        if (argc > 1 && std::string(argv[1]) == "--forecast-legacy-bars")
            return LegacyInput(argc, argv, false);
        // Deliberately small offline example. No gateway address or credentials
        // are accepted; neither native nor Execution libraries are linked.
        int positional = 1;
        while (positional < argc && std::string(argv[positional]).find("--") != 0) ++positional;
        double initialEquity = 100000, multiplier = 1, feePerUnit = .01;
        std::map<std::string, double*> options{{"--initial-equity", &initialEquity},
            {"--multiplier", &multiplier}, {"--fee-per-unit", &feePerUnit}};
        std::map<std::string, bool> seen;
        for (int i = positional; i < argc; i += 2) {
            const std::string name = argv[i];
            Check(i + 1 < argc && options.count(name) != 0 && !seen[name],
                  "unknown, repeated or valueless replay option");
            seen[name] = true;
            const std::string text = argv[i + 1];
            Check(!text.empty() && text.size() <= 128, "invalid replay numeric option");
            std::istringstream input(text); input.imbue(std::locale::classic());
            double value = 0; input >> std::noskipws >> value;
            Check(!input.fail() && input.peek() == std::char_traits<char>::eof() &&
                  std::isfinite(value), "invalid replay numeric option");
            *options.at(name) = value;
        }
        Check(initialEquity > 0 && multiplier > 0 && feePerUnit >= 0,
              "replay requires positive capital/multiplier and nonnegative fee");
        if (positional != 8 && positional != 9) {
            std::cerr << "Usage: hepta-research-replay TICKS.csv SESSIONS.csv INSTRUMENT PERIOD_US FAST SLOW UNITS [average|fifo] [--initial-equity N] [--multiplier N] [--fee-per-unit N]\n";
            LegacyUsage(std::cerr);
            return 2;
        }
        const std::string basisName = positional == 9 ? argv[8] : "average";
        if (basisName != "average" && basisName != "fifo")
            throw std::invalid_argument("cost basis must be average or fifo");
        const auto basis = basisName == "fifo" ? CostBasis::Fifo : CostBasis::WeightedAverage;
        const auto period = Integer(argv[4]), fast = Integer(argv[5]), slow = Integer(argv[6]), units = Integer(argv[7]);
        if (fast <= 0 || slow <= fast || slow > 1000000 || units <= 0 || units > 1000000000)
            throw std::invalid_argument("invalid strategy or unit bounds");
        std::ifstream tickFile(argv[1]), sessionFile(argv[2]);
        if (!tickFile || !sessionFile) throw std::runtime_error("cannot open research input");
        TickCsvReader input(tickFile);
        Tick tick;
        if (!input.Next(tick)) throw std::invalid_argument("empty tick dataset");
        const auto schedule = ReadSessionsCsv(sessionFile);
        BarBuilder builder(argv[3], period, schedule);
        MovingAverageForecast strategy(static_cast<std::size_t>(fast), static_cast<std::size_t>(slow));
        ReplayMatcher matcher(argv[3], schedule, feePerUnit);
        ResearchLedger ledger(argv[3], initialEquity, multiplier, 100000, basis);
        std::string pending;
        std::uint64_t orders = 0, fills = 0, forecasts = 0;
        std::cout.imbue(std::locale::classic());
        std::cout << std::setprecision(17) << "timestamp_us,order_id,quantity,price,fee\n";
        // Consume one checked row at a time. Historical input size does not
        // become a retained vector; match/strategy/ledger bounds remain intact.
        do {
            // Match previously submitted orders first. A newly closed-bar
            // forecast cannot consume this tick, including same-timestamp data.
            for (const auto& event : matcher.OnTick(tick)) {
                if (event.kind == ReplayEventKind::Fill) {
                    ledger.Apply(event.fill); ++fills;
                    std::cout << event.fill.timestampUs << ',' << event.fill.orderId << ',' <<
                              event.fill.side * event.fill.quantity << ',' << event.fill.price << ',' << event.fill.fee << '\n';
                }
            }
            Bar completed;
            if (builder.Push(tick, completed) != TickOutcome::ClosedPrevious) continue;
            Forecast forecast;
            // The closing tick may arrive well after completed.endUs. The
            // existing matcher owns the monotonic clock; do not backdate the
            // newly observed signal to the historical candle boundary.
            if (!strategy.ObserveCompletedBar(completed, tick.timestampUs, forecast)) continue;
            ++forecasts;
            if (!pending.empty()) matcher.Cancel(pending);
            const auto position = ledger.Mark(tick.price).quantity;
            const auto delta = forecast.direction * units - position;
            if (delta == 0) continue;
            ReplayOrder order;
            order.orderId = "research-" + std::to_string(++orders);
            order.instrument = tick.instrument; order.tradingDay = schedule.At(tick.timestampUs).tradingDay;
            order.submittedAtUs = forecast.observedAtUs;
            order.expiresAtUs = schedule.Day(order.tradingDay).closeUs;
            order.side = delta > 0 ? 1 : -1; order.quantity = delta > 0 ? delta : -delta;
            order.limitPrice = tick.price;
            matcher.Submit(order); pending = order.orderId;
        } while (input.Next(tick));
        // EOF is a real boundary, not a fictional next-session tick. Positions
        // remain marked, while ALL resting orders receive terminal treatment.
        // Next leaves tick unchanged at clean EOF. Any late parse/I/O failure
        // exits without this success summary; partial output is not a result.
        Bar incompleteTail;
        builder.Finish(incompleteTail); // EOF is not a completeness watermark.
        const auto finalEvents = matcher.Finish(tick.timestampUs);
        if (!matcher.Finished() || matcher.ActiveOrders() != 0)
            throw std::logic_error("replay finalization left active orders");
        const auto account = ledger.Mark(tick.price);
        std::cout << "{\"model\":\"offline-last-trade-liquidity-v1\",\"forecasts\":" << forecasts
                  << ",\"orders\":" << orders << ",\"fills\":" << fills
                  << ",\"position\":" << account.quantity << ",\"fees\":" << account.fees
                  << ",\"cost_basis\":\"" << basisName << "\""
                  << ",\"realized_gross\":" << account.realizedGross
                  << ",\"unrealized\":" << account.unrealized
                  << ",\"equity\":" << account.equity
                  << ",\"finalized\":true,\"active_orders\":" << matcher.ActiveOrders()
                  << ",\"eof_terminal_events\":" << finalEvents.size()
                  << ",\"initial_equity\":" << initialEquity
                  << ",\"multiplier\":" << multiplier << ",\"fee_per_unit\":" << feePerUnit
                  << ",\"broker_authorized\":false}\n";
        std::cout.flush();
        if (!std::cout) throw std::runtime_error("research output failed");
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "RESEARCH_REPLAY_FAILED: " << e.what() << '\n';
        return 1;
    }
}
