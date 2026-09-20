#include "hepta/research/replay.h"
#include "hepta/research/strategy.h"
#include "test_support.h"
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
    Throws([] { MovingAverageForecast wrong(2, 2); });
    Throws([&] { strategy.OnCompletedBar(B(40, 12), f); });
}
}
int main() { return Run([] { Matching(); Strategy(); }); }
