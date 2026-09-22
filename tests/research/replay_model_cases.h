#pragma once
#include "hepta/research/replay.h"
#include "test_support.h"
#include <limits>
#include <map>
#include <vector>
namespace replay_model_cases {
using namespace hepta::research;
inline FlowInstrument Spec(const std::string& name = "A", bool signedPrices = true) {
    FlowInstrument s; s.account.instrument = name; s.account.currency = "USD";
    s.account.multiplier = 1; s.account.costBasis = CostBasis::Fifo; s.tickSize = 1;
    s.account.priceDomain = signedPrices ? ResearchPriceDomain::SignedFinite : ResearchPriceDomain::Positive;
    return s;
}
inline FlowEvent Add(std::uint64_t seq, const std::string& id, int side,
        std::int64_t qty, std::int64_t price, FlowActor actor = FlowActor::External,
        FlowTimeInForce tif = FlowTimeInForce::Gtc, const std::string& instrument = "A") {
    FlowEvent e; e.sequence = seq; e.timestampUs = seq; e.orderId = id; e.instrument = instrument;
    e.side = side; e.quantity = qty; e.priceTicks = price; e.actor = actor; e.timeInForce = tif;
    return e;
}
inline FlowEvent Mark(std::uint64_t seq, std::int64_t price, const std::string& instrument = "A") {
    FlowEvent e; e.kind = FlowEventKind::Mark; e.sequence = seq; e.timestampUs = seq;
    e.instrument = instrument; e.priceTicks = price; return e;
}
inline Tick Open(std::int64_t time, std::uint64_t seq, double price, const std::string& name = "A") {
    Tick t; t.instrument = name; t.timestampUs = time; t.sequence = seq; t.price = price; return t;
}
inline NextBarTarget Target(const std::string& id, std::int64_t end, std::int64_t qty,
                           double price = 10, const std::string& name = "A") {
    NextBarTarget t; t.targetId = id; t.observedAtUs = end; t.targetQuantity = qty;
    auto& b = t.sourceBar; b.instrument = name; b.tradingDay = "20260922";
    b.beginUs = end - 1; b.endUs = end; b.open = b.high = b.low = b.close = price;
    b.complete = true; b.tickCount = 1; return t;
}
inline void SignedAccounting() {
    ResearchFill f; f.fillId = "a"; f.orderId = "o"; f.instrument = "A";
    f.timestampUs = 1; f.side = 1; f.quantity = 2; f.price = -5; f.fee = 1;
    ResearchLedger positive("A", 1000, 2);
    Throws([&] { positive.Apply(f); }); Throws([&] { positive.Mark(0); });
    Throws([] { ResearchLedger x("A", 1000, 1, 10, CostBasis::Fifo, static_cast<ResearchPriceDomain>(9)); });
    for (auto basis : {CostBasis::Fifo, CostBasis::WeightedAverage}) {
        ResearchLedger ledger("A", 1000, 2, 100, basis, ResearchPriceDomain::SignedFinite);
        auto fill = f; Check(ledger.Apply(fill) && !ledger.Apply(fill), "signed fill identity");
        Near(ledger.Mark(0).equity, 1019);
        fill.fillId = "b"; fill.timestampUs = 2; fill.side = -1; fill.quantity = 1; fill.price = 0; fill.fee = .5;
        ledger.Apply(fill); Near(ledger.Mark(2).equity, 1022.5);
        ResearchSettlement settlement; settlement.settlementId = "s"; settlement.instrument = "A";
        settlement.timestampUs = 3; settlement.price = 1; ledger.Settle(settlement);
        Near(ledger.Mark(2).equity, 1022.5); Near(ledger.Mark(2).realizedGross, 22);
        Check(!ledger.Settle(settlement), "settlement replay retains signed state");
        settlement.settlementId = "negative"; settlement.timestampUs = 4; settlement.price = -1;
        ledger.Settle(settlement); Near(ledger.Mark(2).equity, 1022.5);
        fill.fillId = "nan"; fill.timestampUs = 5; fill.price = std::numeric_limits<double>::quiet_NaN();
        Throws([&] { ledger.Apply(fill); }); Near(ledger.Mark(2).equity, 1022.5);
    }
    ResearchLedger zero("A", 1000, 1, 10, CostBasis::Fifo, ResearchPriceDomain::SignedFinite);
    f.price = 0; f.quantity = 1; f.fee = 0; zero.Apply(f);
    ResearchSettlement z; z.settlementId = "z"; z.instrument = "A"; z.timestampUs = 2; z.price = 1;
    zero.Settle(z); Near(zero.Mark(2).equity, 1002);
    auto a = Spec(), b = Spec("B"); ResearchPortfolio portfolio(1000, "USD", {a.account, b.account});
    f.price = -5; f.timestampUs = 1; portfolio.Apply(f);
    Throws([&] { portfolio.Snapshot(1, 100); });
    portfolio.Observe(Open(2, 1, 0)); Near(portfolio.Snapshot(2, 0).equity, 1005);
    Throws([&] { portfolio.Snapshot(3, 0); });
}
inline void Slippage() {
    SessionWindow w; w.openUs = 0; w.closeUs = 100; w.tradingDay = "20260922";
    std::size_t count = 0;
    for (int side : {-1, 1}) for (int slip = 0; slip < 4; ++slip)
    for (int price = -3; price <= 3; ++price) for (int limit = -3; limit <= 3; ++limit) {
        ReplayExecutionPolicy p; p.tickSize = .5; p.slippageTicks = slip;
        p.priceDomain = ResearchPriceDomain::SignedFinite;
        ReplayMatcher m("A", SessionSchedule({w}), .25, 100, p);
        ReplayOrder o; o.orderId = "o"; o.instrument = "A"; o.tradingDay = w.tradingDay;
        o.side = side; o.quantity = 2; o.limitPrice = limit * .5; o.submittedAtUs = 1; o.expiresAtUs = 50;
        m.Submit(o); auto tick = Open(2, 1, price * .5); tick.volume = 1;
        auto fills = m.OnTick(tick); const int execution = price + side * slip;
        const bool expected = side == 1 ? execution <= limit : execution >= limit;
        Check(fills.size() == static_cast<std::size_t>(expected), "slippage crossing oracle");
        if (expected) { Near(fills[0].fill.price, execution * .5); Near(fills[0].fill.fee, .25); }
        Check(m.OnTick(tick).empty(), "slippage exact retry does not consume volume"); ++count;
    }
    ReplayExecutionPolicy invalid; invalid.slippageTicks = 1;
    Throws([&] { ReplayMatcher m("A", SessionSchedule({w}), 0, 10, invalid); });
    ResearchPriceGrid grid(.5, ResearchPriceDomain::SignedFinite);
    Throws([&] { grid.Index(.125); }); Throws([&] { grid.Price(1099511627777LL); });
    for (int i = -50; i <= 50; ++i) Check(grid.Index(grid.Price(i)) == i, "signed grid round trip");
    std::cout << "slippage independent grid cases=" << count << '\n';
}
inline void OrderFlow() {
    OrderFlowReplay book(1000, "USD", {Spec()});
    auto ext = Add(1, "external", -1, 3, 10); book.Consume(ext);
    book.Consume(Add(2, "research", -1, 2, 10, FlowActor::Research));
    Check(book.Consume(Add(3, "take-external", 1, 2, 10)).empty(), "external external has no account fill");
    Check(book.Order("external").remaining == 1 && book.Order("research").remaining == 2, "external queue ahead");
    auto fills = book.Consume(Add(4, "take-research", 1, 2, 10));
    Check(fills.size() == 1 && fills[0].orderId == "research" && fills[0].quantity == 1 && fills[0].side == -1,
          "maker research attribution after queue ahead");
    Throws([&] { book.Snapshot(4, 100); }); book.Consume(Mark(5, 8));
    Near(book.Snapshot(5, 0).equity, 1002); Throws([&] { book.Snapshot(6, 0); });
    Check(book.Consume(ext).empty() && book.ClockUs() == 5, "old flow retry does not rewind");
    auto conflict = ext; conflict.quantity = 5; Throws([&] { book.Consume(conflict); });
    auto self = book.Consume(Add(6, "self", 1, 2, 11, FlowActor::Research));
    Check(self.empty() && book.Order("self").cancelled == 2 && book.Order("research").remaining == 1,
          "cancel aggressor self trade prevention");
    FlowEvent cancel; cancel.kind = FlowEventKind::Cancel; cancel.sequence = 7; cancel.timestampUs = 7;
    cancel.instrument = "A"; cancel.orderId = "research"; cancel.actor = FlowActor::Research; cancel.quantity = 2;
    Throws([&] { book.Consume(cancel); }); cancel.quantity = 0; book.Consume(cancel);
    Check(book.ActiveOrders() == 0 && book.Order("research").cancelled == 1, "cancel atomicity");
    book.Consume(Add(8, "day", 1, 2, -2, FlowActor::Research, FlowTimeInForce::Day));
    book.Consume(Add(9, "gtc", 1, 2, -3));
    FlowEvent end; end.kind = FlowEventKind::SessionEnd; end.instrument = "A"; end.sequence = 10; end.timestampUs = 10;
    book.Consume(end); Check(book.Order("day").remaining == 0 && book.Order("gtc").remaining == 2, "DAY not GTC expiry");
    auto rebase = Mark(11, 7); rebase.kind = FlowEventKind::BasisRebase;
    book.Consume(rebase); const auto snap = book.Snapshot(11, 0);
    Near(snap.equity, 1003); Near(snap.externalFlows, 0); Near(snap.realizedGross, 3);
    auto signedSpec = Spec(); signedSpec.feePerUnit = .5; signedSpec.feeRate = .1; signedSpec.account.multiplier = 2;
    OrderFlowReplay negative(1000, "USD", {signedSpec});
    negative.Consume(Add(1, "negative-maker", -1, 2, -5));
    auto bought = negative.Consume(Add(2, "negative-buy", 1, 2, -4, FlowActor::Research));
    Check(bought.size() == 1, "negative flow fill"); Near(bought[0].fee, 3);
    negative.Consume(Mark(3, 0)); Near(negative.Snapshot(3, 0).equity, 1017);
    // FOK insufficient liquidity is decided before a nonexistent fee overflow.
    auto huge = Spec(); huge.feePerUnit = std::numeric_limits<double>::max();
    OrderFlowReplay fok(1000, "USD", {huge}); fok.Consume(Add(1, "maker", -1, 2, 10));
    auto fail = Add(2, "too-large", 1, 3, 10, FlowActor::Research, FlowTimeInForce::Fok);
    Check(fok.Consume(fail).empty() && fok.Order("maker").remaining == 2 && fok.Order("too-large").cancelled == 3,
          "FOK preflight does not apply partial fees");
    auto overflow = Add(3, "overflow", 1, 2, 10, FlowActor::Research);
    Throws([&] { fok.Consume(overflow); });
    Check(fok.ClockUs() == 2 && fok.Order("maker").remaining == 2, "whole event rollback");
    auto fixed = overflow; fixed.actor = FlowActor::External;
    Check(fok.Consume(fixed).empty() && fok.Order("maker").remaining == 0, "rejected event did not reserve identity");
    // Multiple price levels precede time; partial cancellation preserves time.
    OrderFlowReplay prices(1000, "USD", {Spec()});
    prices.Consume(Add(1, "expensive", -1, 1, 12));
    prices.Consume(Add(2, "cheap", -1, 3, 10));
    prices.Consume(Add(3, "later", -1, 1, 10));
    cancel.actor = FlowActor::External; cancel.sequence = 4; cancel.timestampUs = 4; cancel.orderId = "cheap"; cancel.quantity = 1;
    prices.Consume(cancel);
    auto take = Add(5, "walk", 1, 4, 0, FlowActor::Research, FlowTimeInForce::Ioc); take.hasLimit = false;
    const auto walk = prices.Consume(take);
    Check(walk.size() == 3 && walk[0].quantity == 2 && walk[1].quantity == 1, "price then original arrival");
    Near(walk[0].price, 10); Near(walk[2].price, 12);
    auto invalid = Mark(6, 5); invalid.orderId = "irrelevant";
    Throws([&] { prices.Consume(invalid); }); Check(prices.ClockUs() == 5, "irrelevant event fields reject");
}
inline void FlowConservation() {
    std::size_t cases = 0;
    for (int side : {-1, 1}) for (int makerActor = 0; makerActor < 2; ++makerActor)
    for (int takerActor = 0; takerActor < 2; ++takerActor) for (int tif = 0; tif < 4; ++tif)
    for (int q = 1; q <= 5; ++q) for (int available = 1; available <= 5; ++available) {
        OrderFlowReplay book(1000, "USD", {Spec()});
        auto maker = Add(1, "m", -side, available, -2, static_cast<FlowActor>(makerActor));
        auto taker = Add(2, "t", side, q, -2, static_cast<FlowActor>(takerActor), static_cast<FlowTimeInForce>(tif));
        book.Consume(maker); auto fills = book.Consume(taker);
        const bool self = makerActor == 1 && takerActor == 1;
        const int expected = self || (tif == 3 && available < q) ? 0 : std::min(q, available);
        const auto m = book.Order("m"), t = book.Order("t");
        Check(m.filled == expected && t.filled == expected, "independent flow quantity oracle");
        Check(m.remaining + m.filled + m.cancelled == available &&
              t.remaining + t.filled + t.cancelled == q, "flow conservation");
        const bool research = expected && (makerActor || takerActor);
        Check(fills.size() == static_cast<std::size_t>(research), "research attribution oracle");
        if (research) Check(fills[0].quantity == expected, "one research fill per match");
        ++cases;
    }
    std::cout << "order flow independent conservation cases=" << cases << '\n';
}
inline void NextBar() {
    auto a = Spec(), b = Spec("B"); a.feePerUnit = b.feePerUnit = .5;
    NextBarReplay replay(1000, "USD", {a, b});
    auto first = Target("long-A", 1, 2); Check(replay.SetTarget(first), "new target");
    Check(replay.ObserveOpen(Open(1, 1, 10)).empty(), "no same-time next-bar execution");
    auto fills = replay.ObserveOpen(Open(2, 2, 11));
    Check(fills.size() == 1 && fills[0].quantity == 2, "next distinct open fill");
    Near(replay.Snapshot(2, 0).equity, 999);
    auto second = Target("short-B", 3, -1, 20, "B"); replay.SetTarget(second);
    auto pending = Target("reverse-A", 3, -2); replay.SetTarget(pending);
    Check(!replay.SetTarget(first), "old target replay does not replace new pending target");
    auto reverse = replay.ObserveOpen(Open(4, 3, 12));
    Check(reverse.size() == 2 && reverse[0].side == -1 && reverse[0].quantity == 2 && reverse[1].quantity == 2,
          "reversal explicitly closes then opens");
    replay.ObserveOpen(Open(4, 1, 20, "B"));
    Near(replay.Snapshot(4, 0).equity, 998.5);
    Check(replay.ObserveOpen(Open(4, 3, 12)).empty(), "duplicate open is inert");
    Throws([&] { replay.ObserveOpen(Open(4, 3, 13)); });
    Throws([&] { replay.ObserveOpen(Open(4, 4, 12)); });
    Throws([&] { replay.Snapshot(5, 0); });
    auto partial = Target("partial", 5, 2); partial.sourceBar.complete = false;
    Throws([&] { replay.SetTarget(partial); });
    auto future = Target("future", 7, 2); future.observedAtUs = 6;
    Throws([&] { replay.SetTarget(future); });
    auto delayed = Target("delayed", 5, 0); delayed.observedAtUs = 10; replay.SetTarget(delayed);
    Throws([&] { replay.ObserveOpen(Open(9, 4, 5)); });
    Check(replay.ObserveOpen(Open(10, 4, 5)).empty(), "delayed bar cannot backdate signal");
    Check(replay.ObserveOpen(Open(11, 5, 4)).size() == 1, "first later open consumes delayed target");
    // Signed zero/negative model inputs use the same native accounting core.
    NextBarReplay signedReplay(1000, "USD", {Spec()}, 1);
    signedReplay.SetTarget(Target("negative", 1, 2, -5));
    auto negative = signedReplay.ObserveOpen(Open(2, 1, -4));
    Check(negative.size() == 1 && negative[0].price == -3, "signed adverse next-open slippage");
    Near(signedReplay.Snapshot(2, 0).equity, 998);
    auto longZero = Target("zero", 3, 2, 0); signedReplay.SetTarget(longZero);
    Check(signedReplay.ObserveOpen(Open(4, 2, 0)).empty(), "same target quantity no fabricated order");
    Near(signedReplay.Snapshot(4, 0).equity, 1006);
    // No marks/fees/target consumption survive a failed fill.
    auto huge = Spec(); huge.feePerUnit = std::numeric_limits<double>::max();
    NextBarReplay rollback(1000, "USD", {huge}); rollback.SetTarget(Target("big", 1, 2));
    Throws([&] { rollback.ObserveOpen(Open(2, 1, 10)); });
    rollback.SetTarget(Target("replace", 1, 1));
    Check(rollback.ObserveOpen(Open(2, 1, 10)).size() == 1, "next-open rollback preserves source sequence");
    NextBarReplay capped(1000, "USD", {Spec()}, 0, 2); auto target = Target("cap", 1, 1);
    capped.SetTarget(target); capped.ObserveOpen(Open(2, 1, 10));
    Throws([&] { capped.ObserveOpen(Open(3, 2, 11)); });
    Check(!capped.SetTarget(target), "historical retry does not consume quota");
}
inline void RunAll() { SignedAccounting(); Slippage(); OrderFlow(); FlowConservation(); NextBar(); }
} // namespace replay_model_cases
