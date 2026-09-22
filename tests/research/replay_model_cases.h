#pragma once
#include "hepta/research/replay.h"
#include "hepta/research/strategy.h"
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

inline void IntegerSignal() {
    Throws([] { IntegerGridMovingAverage x(0, 2); });
    Throws([] { IntegerGridMovingAverage x(2, 2); });
    Throws([] { IntegerGridMovingAverage x(1, 100001); });
    std::size_t checked = 0;
    // Independent bounded direct-sum/cross-product oracle. With these windows
    // and inputs every product fits int64, including near the public tick bound.
    for (std::size_t slow = 2; slow <= 17; ++slow) for (std::size_t fast = 1; fast < slow; ++fast)
    for (const auto base : {-(1LL << 40) + 8, 0LL, (1LL << 40) - 8}) {
        IntegerGridMovingAverage signal(fast, slow);
        std::vector<std::int64_t> closes;
        for (int i = 0; i < 80; ++i) {
            const auto value = base + ((i * 37 + i * i) % 17) - 8;
            closes.push_back(value);
            const auto before = signal;
            Throws([&] { signal.ObserveClose((1LL << 40) + 1); });
            auto copy = before;
            const int actual = signal.ObserveClose(value);
            Check(copy.ObserveClose(value) == actual, "failed integer signal does not advance state");
            int expected = 0;
            if (closes.size() >= slow) {
                std::int64_t shortSum = 0, longSum = 0;
                for (std::size_t j = 0; j < slow; ++j) {
                    longSum += closes[closes.size()-1-j];
                    if (j < fast) shortSum += closes[closes.size()-1-j];
                }
                const auto left = shortSum * static_cast<std::int64_t>(slow);
                const auto right = longSum * static_cast<std::int64_t>(fast);
                expected = left > right ? 1 : (left < right ? -1 : 0);
            }
            Check(actual == expected, "exact signed-grid MA independent oracle"); ++checked;
        }
    }
    IntegerGridMovingAverage large(99999, 100000);
    for (int i = 0; i < 100000; ++i)
        Check(large.ObserveClose(1LL << 40) == 0, "maximum window equal means");
    Check(large.ObserveClose((1LL << 40) - 1) == -1, "one tick difference at maximum window");
    std::cout << "integer MA independent oracle cases=" << checked << '\n';
}
inline void PartialValuation() {
    const auto a = Spec(), b = Spec("B");
    ResearchPortfolio p(1000, "USD", {a.account, b.account});
    auto empty = p.Valuation(0, 0);
    Check(empty.complete && empty.snapshot.positions.size() == 2, "flat portfolio needs no mark");
    Near(empty.cash, 1000); Near(empty.snapshot.equity, 1000);
    Check(empty.unobservedMarks == std::vector<std::string>({"A","B"}), "absent flat quote differs from observed zero");
    ResearchFill f; f.fillId="v1"; f.orderId="o"; f.instrument="A"; f.timestampUs=1;
    f.side=1; f.quantity=2; f.price=-5; f.fee=1;
    p.Apply(f); auto missing=p.Valuation(1, 0);
    Check(!missing.complete && missing.missingMarks == std::vector<std::string>{"A"} &&
          missing.staleMarks.empty(), "missing held mark is not zero-valued equity");
    Near(missing.cash, 1009); Near(missing.snapshot.positions.at("A").quantity, 2);
    Near(missing.snapshot.fees, 1); Throws([&] { p.Snapshot(1, 100); });
    p.Observe(Open(2, 1, -4)); const auto fresh=p.Valuation(2, 0);
    Check(fresh.complete, "signed quote produces complete valuation");
    Near(fresh.snapshot.equity, 1001); Near(fresh.grossNotional, 8); Near(fresh.cash, 1009);
    const auto stale=p.Valuation(3, 0);
    Check(!stale.complete && stale.staleMarks == std::vector<std::string>{"A"} &&
          stale.missingMarks.empty(), "stale held quote explicitly identified");
    Near(stale.cash, fresh.cash); Throws([&] { p.Snapshot(3, 0); });
    p.Observe(Open(3, 2, 0)); Near(p.Valuation(3, 0).snapshot.equity, 1009);
    f.fillId="v2"; f.timestampUs=4; f.side=-1; f.quantity=2; f.price=1; f.fee=.5; p.Apply(f);
    const auto flat=p.Valuation(4, 0);
    Check(flat.complete && flat.missingMarks.empty() && flat.staleMarks.empty(), "flat stale mark is immaterial");
    Near(flat.cash, 1010.5); Near(flat.snapshot.equity, 1010.5); Near(flat.grossNotional, 0);
    Throws([&] { p.Valuation(3, 0); }); Throws([&] { p.Valuation(4, -1); });
    // A strict snapshot must not acquire the partial API's new cash/gross
    // overflow preconditions for otherwise-valid large opposite notionals.
    auto c=Spec("C"), d=Spec("D"); c.account.multiplier=d.account.multiplier=1e308;
    ResearchPortfolio big(1000,"USD",{c.account,d.account});
    f.fee=0;f.price=1;f.quantity=1;f.timestampUs=0;f.fillId="large1";f.instrument="C";f.side=1;big.Apply(f);
    f.fillId="large2";f.instrument="D";f.side=-1;big.Apply(f);
    big.Observe(Open(0,1,1,"C"));big.Observe(Open(0,1,1,"D"));
    Near(big.Snapshot(0,0).equity,1000);
    Throws([&] { big.Valuation(0,0); });
}
inline void ClosePhaseConsumer() {
    auto a=Spec(), b=Spec("B"); a.feePerUnit=.5; b.feePerUnit=.25;
    NextBarPolicy policy; policy.timing=NextOpenTiming::AfterClosePhase;
    policy.slippageTicks=1; policy.instrumentSlippageTicks["B"]=2;
    NextBarReplay replay(1000,"USD",{a,b},policy);
    replay.ObserveMark(Open(10,1,10));
    replay.SetTarget(Target("phase-a",10,2));
    auto fills=replay.ObserveOpen(Open(10,2,11));
    Check(fills.size()==1 && fills[0].price==12 && fills[0].quantity==2,
          "explicit completed-close phase can precede equal-time next open");
    Check(replay.PendingTargets().empty(),"actual open consumes target once");
    Near(replay.Valuation(10,0).snapshot.equity,997);
    replay.ObserveMark(Open(11,3,12));
    replay.SetTarget(Target("phase-next",11,-1,12));
    Check(!replay.ObserveMark(Open(11,3,12)) && replay.PendingTargets().at("A")==-1,
          "same quote retry neither fills nor consumes target");
    Throws([&] { replay.ObserveMark(Open(11,3,13)); });
    Throws([&] { replay.ObserveMark(Open(9,4,13)); });
    Check(replay.Valuation(11,0).snapshot.timestampUs==11 && replay.PendingTargets().at("A")==-1,"rejected mark atomicity");
    replay.ObserveMark(Open(11,1,20,"B"));
    replay.SetTarget(Target("phase-b",11,-1,20,"B"));
    auto bf=replay.ObserveOpen(Open(11,2,20,"B"));
    Check(bf.size()==1 && bf[0].price==18,"instrument slippage override");
    auto reverse=replay.ObserveOpen(Open(12,4,13));
    Check(reverse.size()==2 && reverse[0].quantity==2 && reverse[1].quantity==1,
          "phase mode retains canonical close-first reversal");
    auto value=replay.Valuation(12,0);
    Check(!value.complete && value.staleMarks == std::vector<std::string>{"B"},"phase model retains stale gaps");
    replay.ObserveMark(Open(12,3,19,"B"));Check(replay.Valuation(12,0).complete,"new observed mark restores valuation");
    Throws([&] { replay.Valuation(11,0); });
    NextBarPolicy invalid=policy;invalid.timing=static_cast<NextOpenTiming>(3);
    Throws([&] { NextBarReplay x(1000,"USD",{a,b},invalid); });
    invalid=policy;invalid.instrumentSlippageTicks["unknown"]=1;
    Throws([&] { NextBarReplay x(1000,"USD",{a,b},invalid); });
    invalid=policy;invalid.instrumentSlippageTicks["A"]=-1;
    Throws([&] { NextBarReplay x(1000,"USD",{a,b},invalid); });
    policy.instrumentSlippageTicks.clear();
    NextBarReplay capped(1000,"USD",{a},policy,1);
    const auto mark=Open(1,1,10);capped.ObserveMark(mark);
    Check(!capped.ObserveMark(mark),"duplicate mark does not consume capacity");
    Throws([&] { capped.ObserveMark(Open(2,2,11)); });
    Check(capped.Valuation(1,0).snapshot.timestampUs==1,"quota rejection retains previous clock");
    Near(capped.Snapshot(1,0).equity,1000);
}

// A quote receipt has one semantic kind. A valuation-only observation cannot
// be reused as opening evidence, even when every Tick field is identical.
inline void QuoteKindIdentity() {
    std::size_t cases = 0;
    for (auto timing : {NextOpenTiming::StrictlyLater, NextOpenTiming::AfterClosePhase})
    for (const auto price : {-10.0, 0.0, 10.0})
    for (const auto desired : {-2LL, 0LL, 2LL}) {
        auto spec = Spec(); spec.feePerUnit = .5;
        NextBarPolicy policy; policy.timing = timing;
        // Three accepted events: target, mark, and one fresh open. Rejected
        // cross-kind calls and exact retries must not consume that budget.
        NextBarReplay replay(1000, "USD", {spec}, policy, 3);
        replay.SetTarget(Target("kind-target", 10, desired, price));
        const auto when = timing == NextOpenTiming::StrictlyLater ? 11 : 10;
        const auto mark = Open(when, 1, price);
        Check(replay.ObserveMark(mark), "accept valuation-only quote");
        const auto before = replay.Valuation(when, 0);
        for (int retry = 0; retry < 3; ++retry) {
            Throws([&] { replay.ObserveOpen(mark); });
            Check(!replay.ObserveMark(mark), "same-kind mark retry stays inert");
            const auto after = replay.Valuation(when, 0);
            Check(after.complete && replay.PendingTargets().at("A") == desired,
                  "kind conflict preserves pending target and mark validity");
            Near(after.snapshot.positions.at("A").quantity, 0);
            Near(after.snapshot.fees, before.snapshot.fees);
            Near(after.cash, before.cash); Near(after.snapshot.equity, before.snapshot.equity);
        }
        auto changed = mark; changed.price += 1;
        Throws([&] { replay.ObserveOpen(changed); });
        Throws([&] { replay.ObserveMark(changed); });
        const auto opening = Open(when + 1, 2, price);
        const auto fills = replay.ObserveOpen(opening);
        Check(fills.size() == static_cast<std::size_t>(desired != 0),
              "fresh open consumes target exactly once after rejected reclassification");
        Check(replay.PendingTargets().empty(), "fresh open consumes even a zero target");
        Near(replay.Snapshot(when + 1, 0).positions.at("A").quantity, desired);
        Near(replay.Snapshot(when + 1, 0).fees, desired == 0 ? 0 : 1);
        Check(replay.ObserveOpen(opening).empty(), "same-kind open retry stays inert");
        Throws([&] { replay.ObserveMark(opening); });
        Throws([&] { replay.ObserveMark(Open(when + 2, 3, price)); });
        Near(replay.Snapshot(when + 1, 0).positions.at("A").quantity, desired);
        ++cases;
    }
    // A historical last-open retry remains inert after a newer mark and target.
    // Instrument-local sequence identity must not reject another instrument.
    NextBarPolicy policy; policy.timing = NextOpenTiming::AfterClosePhase;
    NextBarReplay replay(1000, "USD", {Spec(), Spec("B")}, policy);
    const auto opening = Open(10, 1, 10);
    Check(replay.ObserveOpen(opening).empty(), "initial opening without target");
    replay.ObserveMark(Open(11, 2, 11));
    replay.SetTarget(Target("later-target", 11, 2, 11));
    Check(replay.ObserveOpen(opening).empty() && replay.PendingTargets().at("A") == 2,
          "historical last-open retry does not consume a newer target");
    Throws([&] { replay.ObserveMark(opening); });
    Check(replay.ObserveOpen(Open(11, 2, 20, "B")).empty(),
          "receipt sequence is instrument-local");
    Throws([&] { replay.ObserveMark(Open(11, 2, 20, "B")); });
    Near(replay.Valuation(11, 0).snapshot.positions.at("A").quantity, 0);
    Check(replay.PendingTargets().at("A") == 2, "other instrument cannot consume target");
    Check(replay.ObserveOpen(Open(12, 3, 12)).size() == 1, "subsequent real open is eligible");
    std::cout << "quote-kind identity cases=" << cases << '\n';
}

inline void RunAll() { SignedAccounting(); Slippage(); OrderFlow(); FlowConservation(); NextBar();
    IntegerSignal(); PartialValuation(); ClosePhaseConsumer(); QuoteKindIdentity(); }
} // namespace replay_model_cases
