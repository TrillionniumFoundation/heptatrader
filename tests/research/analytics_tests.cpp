#include "hepta/research/analytics.h"
#include "test_support.h"
#include <limits>
using namespace hepta::research;
namespace {
EquityPoint P(long time, double equity, double flow = 0) { EquityPoint p; p.timestampUs = time; p.equity = equity; p.externalFlow = flow; return p; }
ResearchFill F(const std::string& id, long time, int side, long quantity, double price, double fee = 0) {
    ResearchFill f; f.fillId = id; f.orderId = "order-" + id; f.instrument = "TEST.FUT"; f.timestampUs = time; f.side = side; f.quantity = quantity; f.price = price; f.fee = fee; return f;
}
void Metrics() {
    auto empty = EvaluateEquity({}, 252); Check(empty.returnCount == 0 && !empty.sharpe.defined, "empty metrics");
    auto constant = EvaluateEquity({P(0, 100), P(1, 100), P(2, 100)}, 252);
    Check(constant.annualizedVolatility.defined && !constant.sharpe.defined && !constant.sortino.defined && !constant.calmar.defined, "undefined denominators");
    Near(constant.totalReturn, 0); Near(constant.annualizedReturn.value, 0);
    auto flow = EvaluateEquity({P(0, 100), P(1, 200, 100), P(2, 100, -100)}, 252);
    Near(flow.totalReturn, 0); Near(flow.maxDrawdown, 0);
    auto result = EvaluateEquity({P(0, 100), P(1, 110), P(2, 99), P(3, 108.9)}, 3);
    Near(result.totalReturn, .089); Near(result.maxDrawdown, .1); Near(result.annualizedReturn.value, .089);
    Near(result.annualizedVolatility.value, .2); Near(result.sharpe.value, .5);
    Check(result.sortino.defined && result.calmar.defined, "defined ratios");
    Throws([] { EvaluateEquity({P(0, 0)}, 252); });
    Throws([] { EvaluateEquity({P(0, 100), P(0, 101)}, 252); });
    Throws([] { EvaluateEquity({P(0, 100), P(1, 100, 101)}, 252); });
    Throws([] { EvaluateEquity({P(0, 100)}, 0); });
    Throws([] { EvaluateEquity({P(0, 100)}, 252, -1); });
    Throws([] { EvaluateEquity({P(0, 100, 5)}, 252); });
}
void Ledger() {
    ResearchLedger ledger("TEST.FUT", 10000, 10);
    auto buy = F("f1", 1, 1, 2, 100, 2); Check(ledger.Apply(buy), "buy"); Check(!ledger.Apply(buy), "fill duplicate");
    auto changed = buy; changed.price = 101; Throws([&] { ledger.Apply(changed); });
    Near(ledger.Mark(110).equity, 10198);
    ledger.Apply(F("f2", 2, -1, 1, 110, 1)); auto a = ledger.Mark(110);
    Check(a.quantity == 1, "partial close quantity"); Near(a.realizedGross, 100); Near(a.unrealized, 100); Near(a.equity, 10197);
    ledger.Apply(F("f3", 3, -1, 3, 90, 3)); a = ledger.Mark(80);
    Check(a.quantity == -2, "flip quantity"); Near(a.averageEntry, 90); Near(a.realizedGross, 0); Near(a.unrealized, 200); Near(a.equity, 10194);
    Throws([&] { ledger.Apply(F("late", 1, 1, 1, 100)); });
    Near(ledger.Mark(80).equity, 10194);
    ledger.Apply(F("f4", 4, 1, 2, 80, 2)); a = ledger.Mark(80); Check(a.quantity == 0, "flat"); Near(a.averageEntry, 0); Near(a.equity, 10192);
    Throws([&] { ledger.Mark(std::numeric_limits<double>::quiet_NaN()); });
    ResearchLedger cap("TEST.FUT", 100, 1, 1); cap.Apply(F("one", 0, 1, 1, 1)); Throws([&] { cap.Apply(F("two", 1, 1, 1, 1)); });
    // Independent cash-flow identity checks 500 partial closes and reversals.
    ResearchLedger random("TEST.FUT", 100000, 5); long position = 0; double cash = 100000;
    for (int i = 1; i <= 500; ++i) {
        int side = i % 3 == 0 ? -1 : 1; long q = 1 + (i * 7) % 5; double p = 90 + (i * 13) % 21;
        auto f = F(std::to_string(i), i, side, q, p, .25 * q); random.Apply(f);
        cash -= side * q * p * 5 + f.fee; position += side * q;
        const auto value = random.Mark(100); Check(value.quantity == position, "oracle position"); Near(value.equity, cash + position * 100 * 5, 1e-7);
    }
}
}
namespace {
void BoundedCostAndAtomicRejection() {
    const double max = std::numeric_limits<double>::max();
    for (int side : {-1, 1}) {
        ResearchLedger ledger("TEST.FUT", 1000, 1);
        // Previously the 2049th identical fill produced an average slightly
        // above DBL_MAX. The true average and mark-to-market P&L are unchanged.
        for (int i = 1; i <= 4096; ++i) {
            const auto fill = F("constant-" + std::to_string(i), i, side, 1, max);
            Check(ledger.Apply(fill) && !ledger.Apply(fill), "constant fill identity");
            const auto account = ledger.Mark(max);
            Check(account.quantity == side * i && account.averageEntry == max &&
                  account.unrealized == 0 && account.realizedGross == 0 && account.equity == 1000,
                  "constant-price cost and equity invariants");
        }
        ledger.Apply(F("same-price-close", 4097, -side, 4096, max));
        const auto account = ledger.Mark(max);
        Check(account.quantity == 0 && account.averageEntry == 0 && account.equity == 1000,
              "same-price round trip does not invent P&L");
    }
    // The same convex-mean contract holds with heavily unequal fill quantities.
    ResearchLedger large("TEST.FUT", 1000, 1);
    auto first = F("weighted-first", 0, 1, 1, max); first.quantity = 999999999999LL;
    large.Apply(first); large.Apply(F("weighted-last", 1, 1, 1, max));
    Check(large.Mark(max).quantity == 1000000000000LL && large.Mark(max).equity == 1000,
          "bounded unequal-weight mean");
    Throws([&] { large.Apply(F("too-large", 2, 1, 1, max)); });
    Check(large.Mark(max).quantity == 1000000000000LL, "position-cap rejection unchanged");

    // Do not fix harmless rounding by suppressing genuine overflow. A rejected
    // fill must leave quantity, P&L, clock and fill identity available for retry.
    ResearchLedger overflow("TEST.FUT", 1000, 1);
    overflow.Apply(F("open", 0, 1, 2, 1));
    auto close = F("close", 1, -1, 2, max);
    Throws([&] { overflow.Apply(close); });
    auto account = overflow.Mark(1);
    Check(account.quantity == 2 && account.averageEntry == 1 && account.equity == 1000,
          "true P&L overflow rolls back account");
    close.price = 2;
    Check(overflow.Apply(close) && !overflow.Apply(close), "rejected fill ID was not consumed");
    Check(overflow.Mark(2).equity == 1002, "retry after true overflow");
}
}
namespace {
void FifoAttributionAndBounds() {
    ResearchLedger fifo("TEST.FUT", 1000, 10, 100, CostBasis::Fifo);
    ResearchLedger average("TEST.FUT", 1000, 10);
    Check(fifo.Basis() == CostBasis::Fifo && average.Basis() == CostBasis::WeightedAverage,
          "explicit cost convention; unchanged default");
    for (auto* ledger : {&fifo, &average}) {
        ledger->Apply(F("a", 1, 1, 1, 100));
        ledger->Apply(F("b", 2, 1, 1, 120));
        ledger->Apply(F("c", 3, -1, 1, 130));
    }
    Near(fifo.Mark(140).realizedGross, 300); Near(fifo.Mark(140).averageEntry, 120);
    Near(average.Mark(140).realizedGross, 200); Near(average.Mark(140).averageEntry, 110);
    Near(fifo.Mark(140).equity, average.Mark(140).equity);
    Throws([] { ResearchLedger invalid("TEST.FUT", 1000, 1, 100, static_cast<CostBasis>(99)); });

    const double max = std::numeric_limits<double>::max();
    for (int side : {-1, 1}) {
        ResearchLedger compressed("TEST.FUT", 1000, 1, 10000, CostBasis::Fifo);
        for (int i = 1; i <= 4096; ++i) compressed.Apply(F("max-" + std::to_string(i), i, side, 1, max));
        const auto full = compressed.Mark(max);
        Check(full.quantity == side * 4096 && full.equity == 1000 && full.averageEntry == max,
              "FIFO equal maximum prices preserve cost");
        compressed.Apply(F("exit", 4097, -side, 4096, max));
        Check(compressed.Mark(max).equity == 1000 && compressed.Quantity() == 0, "FIFO extreme round trip");
        ResearchLedger huge("TEST.FUT", 1000, 1, 10, CostBasis::Fifo);
        auto lot = F("huge", 0, side, 1, max); lot.quantity = 1000000000000LL;
        huge.Apply(lot); Check(huge.Quantity() == side * lot.quantity, "compressed trillion-contract lot");
        Throws([&] { huge.Apply(F("over", 1, side, 1, max)); });
        huge.Apply(F("small-exit", 1, -side, 1, max));
        Check(huge.Mark(max).equity == 1000, "compressed partial close remains exact");
    }
    ResearchLedger overflow("TEST.FUT", 1000, 1, 4, CostBasis::Fifo);
    overflow.Apply(F("open", 0, 1, 1, 1)); overflow.Apply(F("open-2", 0, 1, 1, 2));
    auto close = F("overflow", 10, -1, 2, max);
    Throws([&] { overflow.Apply(close); });
    Near(overflow.Mark(2).equity, 1001); Near(overflow.Mark(2).averageEntry, 1.5);
    // The earlier retry proves both clock and receipt were rolled back.
    close.timestampUs = 1; close.price = 3;
    Check(overflow.Apply(close) && !overflow.Apply(close), "FIFO failed update consumes no identity/time");
    Near(overflow.Mark(3).realizedGross, 3);
    ResearchLedger feeOverflow("TEST.FUT", 1000, 1, 4, CostBasis::Fifo);
    auto first = F("fee-a", 0, 1, 1, 1, max); feeOverflow.Apply(first);
    auto second = F("fee-b", 10, 1, 1, 2, max);
    Throws([&] { feeOverflow.Apply(second); });
    Check(feeOverflow.Quantity() == 1, "fee overflow leaves lots intact");
    second.timestampUs = 1; second.fee = 0; Check(feeOverflow.Apply(second), "fee-overflow retry");
}
void ExhaustiveFifoOracle() {
    // An independent UNIT-lot oracle deliberately does not use production's
    // quantity-compressed algorithm. Enumerate all 12^4 four-fill histories:
    // two sides x two quantities x three prices. Check every prefix.
    std::size_t states = 0;
    const int histories = 12 * 12 * 12 * 12;
    for (int history = 0; history < histories; ++history) {
        const double multiplier = 1 + history % 5;
        ResearchLedger fifo("TEST.FUT", 100000, multiplier, 8, CostBasis::Fifo);
        ResearchLedger average("TEST.FUT", 100000, multiplier, 8);
        std::deque<double> unitCosts;
        long quantity = 0;
        double realized = 0, fees = 0, cash = 100000;
        int digits = history;
        for (int step = 1; step <= 4; ++step) {
            const int action = digits % 12; digits /= 12;
            const int side = action < 6 ? 1 : -1;
            const int count = 1 + (action / 3) % 2;
            const double price = 90 + 10 * (action % 3);
            const auto fill = F(std::to_string(step), step, side, count, price, .25 * count);
            for (int unit = 0; unit < count; ++unit) {
                if (quantity == 0 || (quantity > 0) == (side > 0)) unitCosts.push_back(price);
                else {
                    realized += (price - unitCosts.front()) * (quantity > 0 ? 1 : -1) * multiplier;
                    unitCosts.pop_front();
                }
                quantity += side;
            }
            cash -= side * count * price * multiplier + fill.fee; fees += fill.fee;
            Check(fifo.Apply(fill) && !fifo.Apply(fill), "FIFO exact retry"); average.Apply(fill);
            const double mark = 95 + 5 * (history % 4);
            double unrealized = 0, sumCosts = 0;
            for (double cost : unitCosts) {
                unrealized += (mark - cost) * (quantity > 0 ? 1 : -1) * multiplier;
                sumCosts += cost;
            }
            const auto a = fifo.Mark(mark);
            Check(a.quantity == quantity && fifo.Quantity() == quantity, "unit-lot oracle position");
            Near(a.realizedGross, realized); Near(a.unrealized, unrealized); Near(a.fees, fees);
            Near(a.averageEntry, unitCosts.empty() ? 0 : sumCosts / unitCosts.size());
            Near(a.equity, cash + quantity * mark * multiplier);
            Near(a.equity, average.Mark(mark).equity);
            ++states;
        }
    }
    Check(states == 82944, "nonempty exhaustive FIFO coverage");
    std::cout << "fifo_histories=" << histories << " fifo_prefix_states=" << states << '\n';
}
ResearchInstrument Spec(const std::string& name, double multiplier, CostBasis basis = CostBasis::Fifo,
                        const std::string& currency = "USD") {
    ResearchInstrument spec; spec.instrument = name; spec.multiplier = multiplier;
    spec.currency = currency; spec.costBasis = basis; return spec;
}
Tick Q(const std::string& name, long time, std::uint64_t sequence, double price) {
    Tick tick; tick.instrument = name; tick.timestampUs = time; tick.sequence = sequence; tick.price = price;
    return tick;
}
ResearchCashFlow Flow(const std::string& id, long time, double amount) {
    ResearchCashFlow flow; flow.flowId = id; flow.timestampUs = time; flow.amount = amount; return flow;
}
void PortfolioValuationAndIdentity() {
    ResearchPortfolio portfolio(10000, "USD", {Spec("TEST.FUT", 10), Spec("OTHER.FUT", 5)});
    Near(portfolio.Snapshot(0, 0).equity, 10000);
    auto a = F("a", 1, 1, 2, 100, 2);
    auto b = F("b", 2, -1, 3, 50, 3); b.instrument = "OTHER.FUT";
    portfolio.Apply(a); portfolio.Apply(b);
    Throws([&] { portfolio.Snapshot(2, 100); }); // Fill prices are not substitute marks.
    const auto qa = Q("TEST.FUT", 3, 1, 110), qb = Q("OTHER.FUT", 3, 1, 40);
    portfolio.Observe(qa); Throws([&] { portfolio.Snapshot(3, 0); });
    portfolio.Observe(qb); auto value = portfolio.Snapshot(3, 0);
    Near(value.equity, 10345); Near(value.unrealized, 350); Near(value.fees, 5);
    Check(value.currency == "USD" && value.positions.size() == 2, "fixed currency/universe");
    Throws([&] { portfolio.Snapshot(4, 0); });
    const auto deposit = Flow("deposit", 4, 1000);
    Check(portfolio.ApplyCashFlow(deposit) && !portfolio.ApplyCashFlow(deposit), "idempotent deposit");
    Near(portfolio.Snapshot(4, 1).equity, 11345);
    Check(!portfolio.Apply(a) && !portfolio.Apply(b) && !portfolio.Observe(qa), "old exact receipts do not rewind");
    auto conflict = a; conflict.instrument = "OTHER.FUT";
    Throws([&] { portfolio.Apply(conflict); }); // IDs are global, not per instrument.
    auto badFlow = deposit; badFlow.amount = 1001; Throws([&] { portfolio.ApplyCashFlow(badFlow); });
    portfolio.Apply(F("close-a", 5, -1, 1, 120, 1));
    Check(!portfolio.Observe(qa), "old tick retry remains a retry after fill");
    Throws([&] { portfolio.Snapshot(5, 100); }); // Retry cannot revive invalidated mark.
    portfolio.Observe(Q("TEST.FUT", 5, 2, 115));
    value = portfolio.Snapshot(5, 2); Near(value.equity, 11494);
    Near(value.realizedGross, 200); Near(value.unrealized, 300);
    portfolio.ApplyCashFlow(Flow("withdrawal", 6, -500));
    Throws([&] { portfolio.Observe(Q("OTHER.FUT", 4, 2, 45)); });
    portfolio.Observe(Q("OTHER.FUT", 6, 2, 45));
    Near(portfolio.Snapshot(6, 1).equity, 10919);
    portfolio.Apply(F("flat-a", 7, -1, 1, 115, 1));
    auto flatB = F("flat-b", 8, 1, 3, 45, 3); flatB.instrument = "OTHER.FUT";
    portfolio.Apply(flatB); value = portfolio.Snapshot(8, 0);
    Near(value.equity, 10915); Near(value.realizedGross, 425); Near(value.fees, 10);
    Near(value.externalFlows, 500); Near(value.unrealized, 0);
    Check(value.positions.at("TEST.FUT").markPrice == 0 && value.positions.at("OTHER.FUT").markPrice == 0,
          "flat positions require no fictional quote");
    // Exhausted capital is reportable research output, not an automatic top-up.
    portfolio.ApplyCashFlow(Flow("all-capital", 9, -12000)); Near(portfolio.Snapshot(9, 0).equity, -1085);
    Throws([&] { portfolio.Snapshot(8, 0); }); Throws([&] { portfolio.Snapshot(9, -1); });
}
void PortfolioFailureAtomicity() {
    const auto spec = Spec("TEST.FUT", 1);
    Throws([&] { ResearchPortfolio p(100, "USD", {}); });
    Throws([&] { ResearchPortfolio p(0, "USD", {spec}); });
    Throws([&] { ResearchPortfolio p(100, "usd", {spec}); });
    Throws([&] { ResearchPortfolio p(100, "USD", {spec, spec}); });
    Throws([&] { ResearchPortfolio p(100, "USD", {spec, Spec("CN.FUT", 1, CostBasis::Fifo, "CNY")}); });
    Throws([&] { ResearchPortfolio p(100, "USD", {Spec("TEST.FUT", 0)}); });
    Throws([&] { ResearchPortfolio p(100, "USD", {spec}, 0); });
    ResearchPortfolio p(1000, "USD", {spec}, 4);
    const auto before = Q("TEST.FUT", 0, 1, 1); p.Observe(before);
    auto fill = F("open", 0, 1, 2, 1); p.Apply(fill);
    Check(!p.Observe(before), "same-time pre-fill retry not a fresh observation");
    Throws([&] { p.Snapshot(0, 0); });
    p.Observe(Q("TEST.FUT", 0, 2, 1)); Near(p.Snapshot(0, 0).equity, 1000);
    auto overflow = F("close", 20, -1, 2, std::numeric_limits<double>::max());
    Throws([&] { p.Apply(overflow); });
    Near(p.Snapshot(0, 0).equity, 1000); // Clock, mark and receipt all unchanged.
    overflow.timestampUs = 1; overflow.price = 2;
    Check(p.Apply(overflow) && !p.Apply(overflow), "portfolio rejected fill retry");
    Near(p.Snapshot(1, 0).equity, 1002);
    auto invalid = F("bad", 30, 1, 0, 2); Throws([&] { p.Apply(invalid); });
    invalid.timestampUs = 2; invalid.quantity = 1; p.Apply(invalid);
    p.Observe(Q("TEST.FUT", 2, 3, 2));
    auto wrongTick = Q("TEST.FUT", 30, 3, 3); Throws([&] { p.Observe(wrongTick); });
    auto unknown = Q("UNKNOWN", 30, 1, 1); Throws([&] { p.Observe(unknown); });
    Near(p.Snapshot(2, 0).equity, 1002);
    auto invalidFlow = Flow("cash", 40, std::numeric_limits<double>::infinity());
    Throws([&] { p.ApplyCashFlow(invalidFlow); });
    invalidFlow.timestampUs = 3; invalidFlow.amount = 100; p.ApplyCashFlow(invalidFlow);
    Check(!p.Apply(fill) && !p.ApplyCashFlow(invalidFlow), "exact receipts survive capacity");
    Throws([&] { p.Apply(F("capacity", 4, -1, 1, 2)); });
    Throws([&] { p.ApplyCashFlow(Flow("too-many", 4, 1)); });
    p.Observe(Q("TEST.FUT", 4, 4, 2)); Near(p.Snapshot(4, 0).equity, 1102);

    ResearchPortfolio cash(1, "USD", {spec});
    cash.ApplyCashFlow(Flow("max", 0, std::numeric_limits<double>::max()));
    Throws([&] { cash.ApplyCashFlow(Flow("overflow", 100, std::numeric_limits<double>::max())); });
    Check(cash.ApplyCashFlow(Flow("overflow", 1, -1)), "cash overflow rolls back clock/ID");
}
void PortfolioCashConservationOracle() {
    ResearchPortfolio p(100000, "USD", {Spec("TEST.FUT", 3), Spec("OTHER.FUT", 7, CostBasis::WeightedAverage)});
    double cash = 100000;
    long qa = 0, qb = 0;
    for (int i = 1; i <= 1200; ++i) {
        const bool second = i % 2 == 0;
        const std::string name = second ? "OTHER.FUT" : "TEST.FUT";
        const int side = i % 5 < 2 ? -1 : 1, count = 1 + (i * 7) % 3;
        const double price = 90 + i % 21, multiplier = second ? 7 : 3;
        auto fill = F("f-" + std::to_string(i), i, side, count, price, .25 * count); fill.instrument = name;
        p.Apply(fill); cash -= side * count * price * multiplier + fill.fee;
        (second ? qb : qa) += side * count;
        if (i % 13 == 0) { const double flow = i % 26 == 0 ? -200 : 100; p.ApplyCashFlow(Flow(std::to_string(i), i, flow)); cash += flow; }
        p.Observe(Q("TEST.FUT", i, i, 101)); p.Observe(Q("OTHER.FUT", i, i, 99));
        const auto value = p.Snapshot(i, 0);
        Near(value.equity, cash + qa * 101 * 3 + qb * 99 * 7, 1e-7);
        Check(value.positions.at("TEST.FUT").quantity == qa && value.positions.at("OTHER.FUT").quantity == qb,
              "portfolio independent cash/position oracle");
    }
    std::cout << "portfolio_cash_oracle_states=1200\n";
}
}
int main() { return Run([] {
    Metrics(); Ledger(); BoundedCostAndAtomicRejection();
    FifoAttributionAndBounds(); ExhaustiveFifoOracle();
    PortfolioValuationAndIdentity(); PortfolioFailureAtomicity(); PortfolioCashConservationOracle();
}); }
