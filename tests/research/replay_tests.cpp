#include "hepta/research/replay.h"
#include "hepta/research/strategy.h"
#include "test_support.h"
#include <algorithm>
#include <limits>
using namespace hepta::research;
namespace {
SessionSchedule Schedule() { SessionWindow a, b; a.openUs = 0; a.closeUs = 1000; a.tradingDay = "20260921"; b.openUs = 1000; b.closeUs = 2000; b.tradingDay = "20260922"; return SessionSchedule({a, b}); }
Tick T(long t, unsigned seq, double price, long vol) { Tick x; x.instrument = "TEST.FUT"; x.timestampUs = t; x.sequence = seq; x.price = price; x.volume = vol; return x; }
ReplayOrder O(const std::string& id, long qty, ReplayTimeInForce tif = ReplayTimeInForce::Day) { ReplayOrder o; o.orderId = id; o.instrument = "TEST.FUT"; o.tradingDay = "20260921"; o.submittedAtUs = 10; o.expiresAtUs = 1500; o.side = 1; o.quantity = qty; o.limitPrice = 100; o.timeInForce = tif; return o; }
Bar B(long time, double close) { Bar b; b.instrument = "TEST.FUT"; b.tradingDay = "20260921"; b.beginUs = time; b.endUs = time + 10; b.open = b.high = b.low = b.close = close; b.volume = b.tickCount = 1; b.complete = true; return b; }
void Matching() {
    ReplayMatcher m("TEST.FUT", Schedule(), .5); auto a = O("a", 3), b = O("b", 3);
    Check(m.Submit(a) && !m.Submit(a), "order idempotency");
    auto conflict = a; conflict.quantity = 2; Throws([&] { m.Submit(conflict); }); m.Submit(b);
    Check(m.OnTick(T(10, 1, 100, 100)).empty(), "no same-timestamp fill");
    Check(m.OnTick(T(11, 2, 101, 100)).empty(), "no limit violation");
    auto fills = m.OnTick(T(12, 3, 99, 4)); Check(fills.size() == 2, "shared liquidity fills");
    Check(fills[0].fill.quantity == 3 && fills[1].fill.quantity == 1 && fills[1].remaining == 2, "FIFO participation");
    Near(fills[0].fill.fee, 1.5); Check(m.OnTick(T(12, 3, 99, 4)).empty(), "no duplicate volume consumption");
    Throws([&] { m.OnTick(T(12, 3, 99, 5)); }); Check(m.ActiveOrders() == 1, "invalid tick atomicity");
    Check(m.Cancel("b").size() == 1 && m.Cancel("b").empty(), "idempotent cancel");
    Throws([&] { m.Cancel("unknown"); });
    ReplayMatcher tif("TEST.FUT", Schedule()); tif.Submit(O("fok", 3, ReplayTimeInForce::FillOrKill)); tif.Submit(O("ioc", 3, ReplayTimeInForce::ImmediateOrCancel));
    auto ev = tif.OnTick(T(20, 1, 100, 2)); Check(ev.size() == 3 && ev[0].kind == ReplayEventKind::Cancelled && ev[1].fill.quantity == 2 && ev[2].remaining == 1, "FOK/IOC");
    Check(tif.ActiveOrders() == 0, "IOC leftovers removed");
    ReplayMatcher day("TEST.FUT", Schedule()); day.Submit(O("day", 1)); auto expired = day.OnTick(T(1000, 1, 90, 100));
    Check(expired.size() == 1 && expired[0].kind == ReplayEventKind::Expired, "day-order rollover");
    ReplayMatcher limit("TEST.FUT", Schedule(), 0, 1); limit.Submit(O("one", 1)); Throws([&] { limit.Submit(O("two", 1)); });
    ReplayMatcher many("TEST.FUT", Schedule());
    for (int i = 0; i < 100; ++i) many.Submit(O(std::to_string(i), 2));
    std::int64_t total = 0; for (const auto& e : many.OnTick(T(20, 1, 100, 37))) total += e.fill.quantity;
    Check(total == 37, "aggregate liquidity conservation");
}
void Strategy() {
    MovingAverageForecast strategy(1, 2); Forecast f;
    Check(!strategy.OnCompletedBar(B(0, 10), f), "warmup");
    Check(strategy.OnCompletedBar(B(10, 12), f) && f.direction == 1 && f.observedAtUs == 20, "bull forecast");
    Check(!strategy.OnCompletedBar(B(20, 13), f), "unchanged forecast");
    Check(strategy.OnCompletedBar(B(30, 11), f) && f.direction == -1, "bear forecast");
    auto partial = B(40, 12); partial.complete = false; Throws([&] { strategy.OnCompletedBar(partial, f); });
    Check(strategy.OnCompletedBar(B(40, 12), f) && f.direction == 1, "partial did not mutate strategy");
    // Strategy warmup must not overflow on a valid constant seven-bar window
    // or emit a false direction because means used different denominators.
    MovingAverageForecast constant(3, 7);
    for (int i = 0; i < 300; ++i)
        Check(!constant.OnCompletedBar(B(i * 10, std::numeric_limits<double>::max()), f),
              "bounded constant means remain a no-signal strategy");
    Throws([] { MovingAverageForecast wrong(2, 2); });
    Throws([&] { strategy.OnCompletedBar(B(40, 12), f); });
}
void NoTickExpiryAndFinish() {
    SessionWindow a, b; a.openUs = 0; a.closeUs = 100; a.tradingDay = "20260921";
    b.openUs = 200; b.closeUs = 300; b.tradingDay = a.tradingDay;
    ReplayMatcher m("TEST.FUT", SessionSchedule({a, b}));
    auto normal = O("day", 3), ttl = O("ttl", 2); ttl.expiresAtUs = 150;
    m.Submit(normal); m.Submit(ttl);
    Check(m.AdvanceWatermark(100).empty() && m.ActiveOrders() == 2, "break is not end of trading day");
    auto events = m.AdvanceWatermark(150);
    Check(events.size() == 1 && events[0].orderId == "ttl" && events[0].kind == ReplayEventKind::Expired,
          "TTL expires during a break without a tick");
    Check(m.AdvanceWatermark(150).empty(), "repeated watermark is idempotent");
    Throws([&] { m.AdvanceWatermark(149); });
    Throws([&] { m.OnTick(T(90, 1, 99, 100)); });
    Check(m.ClockUs() == 150 && m.ActiveOrders() == 1, "rejection does not consume liquidity or clock");
    auto partial = m.OnTick(T(200, 1, 99, 1));
    Check(partial.size() == 1 && partial[0].remaining == 2, "resumes next session same trading day");
    events = m.AdvanceWatermark(300);
    Check(events.size() == 1 && events[0].remaining == 2 && events[0].kind == ReplayEventKind::Expired,
          "final session expires remainder without a next-day tick");
    Check(m.ActiveOrders() == 0 && !m.Submit(normal), "retry does not resurrect expired order");
    Check(m.Finish(300).empty() && m.Finished() && m.Finish(300).empty(), "empty finish is idempotent");
    Throws([&] { m.Finish(301); }); Throws([&] { m.AdvanceWatermark(300); });
    Throws([&] { m.OnTick(T(200, 1, 99, 1)); });
    ReplayMatcher truncated("TEST.FUT", Schedule());
    auto shortTtl = O("short", 1); shortTtl.expiresAtUs = 20;
    truncated.Submit(shortTtl); truncated.Submit(O("long", 5));
    events = truncated.Finish(20);
    Check(events.size() == 2 && events[0].kind == ReplayEventKind::Expired &&
          events[1].kind == ReplayEventKind::Cancelled && events[1].remaining == 5,
          "EOF distinguishes expired and cancelled, never fabricates a fill");
    Check(truncated.ActiveOrders() == 0 && truncated.Cancel("long").empty(), "terminal cancel idempotent");
    Throws([&] { truncated.Submit(O("new", 1)); });
    Check(!truncated.Submit(shortTtl), "terminal exact submit retry is still idempotent");
}
void ClockAndRollback() {
    ReplayMatcher m("TEST.FUT", Schedule());
    auto future = O("future", 1); future.submittedAtUs = 100;
    m.Submit(future);
    Throws([&] { m.Submit(O("retroactive", 1)); });
    Throws([&] { m.OnTick(T(99, 1, 99, 1)); });
    Check(m.OnTick(T(100, 1, 99, 10)).empty(), "same timestamp remains ineligible after submission");
    Check(m.OnTick(T(101, 2, 99, 1)).size() == 1, "later event can fill");
    m.AdvanceWatermark(200);
    Check(m.OnTick(T(101, 2, 99, 1)).empty() && m.ClockUs() == 200, "exact tick retry never rewinds clock");
    auto bad = O("bad", 1); bad.submittedAtUs = 250; bad.expiresAtUs = 300; bad.tradingDay = "20260922";
    Throws([&] { m.Submit(bad); }); Check(m.ClockUs() == 200, "invalid new submission cannot advance clock");
    ReplayMatcher overflow("TEST.FUT", Schedule(), std::numeric_limits<double>::max());
    overflow.Submit(O("large-fee", 2));
    Throws([&] { overflow.OnTick(T(20, 1, 99, 2)); });
    Check(overflow.ClockUs() == 10 && overflow.ActiveOrders() == 1, "fee overflow rolls back the entire tick");
    auto one = overflow.OnTick(T(20, 1, 99, 1));
    Check(one.size() == 1 && one[0].fill.fillId == "large-fee:1" && one[0].remaining == 1,
          "rejected tick did not consume sequence, quantity or fill ID");
    Check(overflow.Finish(20).at(0).remaining == 1, "partial fill remainder is preserved at finish");
}
void ReplayConservation() {
    // Exhaustive small books: both directions, all TIFs, quantity and liquidity.
    std::size_t scenarios = 0;
    for (int side : {-1, 1}) for (int kind = 0; kind < 3; ++kind)
    for (long q = 1; q <= 8; ++q) for (long volume = 0; volume <= 10; ++volume) {
        ReplayMatcher m("TEST.FUT", Schedule());
        auto order = O("model", q, static_cast<ReplayTimeInForce>(kind)); order.side = side;
        m.Submit(order);
        const auto events = m.OnTick(T(20, 1, 100, volume));
        long filled = 0, terminated = 0;
        for (const auto& e : events) {
            if (e.kind == ReplayEventKind::Fill) filled += static_cast<long>(e.fill.quantity);
            else terminated += static_cast<long>(e.remaining);
        }
        for (const auto& e : m.Finish(20)) terminated += static_cast<long>(e.remaining);
        const auto expected = kind == 2 && volume < q ? 0 : std::min(q, volume);
        Check(filled == expected && filled <= volume && filled + terminated == q && m.ActiveOrders() == 0,
              "filled plus terminal remainder must equal submitted quantity");
        ++scenarios;
    }
    std::cout << "replay conservation scenarios=" << scenarios << '\n';
}
}
int main() { return Run([] { Matching(); Strategy(); NoTickExpiryAndFinish(); ClockAndRollback(); ReplayConservation(); }); }
