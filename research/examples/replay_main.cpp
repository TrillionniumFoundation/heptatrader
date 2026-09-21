#include "hepta/research/analytics.h"
#include "hepta/research/market_data.h"
#include "hepta/research/replay.h"
#include "hepta/research/strategy.h"
#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <locale>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>

using namespace hepta::research;
namespace {
long long Integer(const std::string& s) {
    if (s.empty()) throw std::invalid_argument("empty integer");
    for (char c : s) if (c < '0' || c > '9') throw std::invalid_argument("unsigned decimal integer required");
    std::size_t n = 0; const auto v = std::stoll(s, &n);
    if (n != s.size()) throw std::invalid_argument("invalid integer");
    return v;
}
}
int main(int argc, char** argv) {
    try {
        // Deliberately small offline example. No gateway address or credentials
        // are accepted; neither native nor Execution libraries are linked.
        if (argc != 8 && argc != 9) {
            std::cerr << "Usage: hepta-research-replay TICKS.csv SESSIONS.csv INSTRUMENT PERIOD_US FAST SLOW UNITS [average|fifo]\n";
            return 2;
        }
        const std::string basisName = argc == 9 ? argv[8] : "average";
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
        ReplayMatcher matcher(argv[3], schedule, .01);
        ResearchLedger ledger(argv[3], 100000, 1, 100000, basis);
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
                    std::cout << event.fill.timestampUs << ',' << event.fill.orderId << ','
                              << event.fill.side * event.fill.quantity << ',' << event.fill.price << ',' << event.fill.fee << '\n';
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
                  << ",\"broker_authorized\":false}\n";
        if (!std::cout) throw std::runtime_error("research output failed");
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "RESEARCH_REPLAY_FAILED: " << e.what() << '\n';
        return 1;
    }
}
