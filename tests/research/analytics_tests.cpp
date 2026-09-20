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
int main() { return Run([] { Metrics(); Ledger(); BoundedCostAndAtomicRejection(); }); }
