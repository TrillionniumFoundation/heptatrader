#include "risk/portfolio_risk_snapshot_builder.h"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <string>

namespace {
void Require(bool condition, const char* message) {
    if (!condition) {
        std::cerr << "FAIL: " << message << std::endl;
        std::exit(1);
    }
}

PreTradeRiskInstrumentContract Stock(
    const std::string& instrument, const std::string& currency) {
    PreTradeRiskInstrumentContract contract;
    contract.specificationId = instrument + "-spec";
    contract.specificationVersion = 1;
    contract.instrument = instrument;
    contract.kind = PreTradeRiskInstrumentKind::Stock;
    contract.quantityUnit = PreTradeRiskQuantityUnit::Shares;
    contract.priceUnit = PreTradeRiskPriceUnit::QuoteCurrencyPerUnit;
    contract.multiplier = 1.0;
    contract.quoteCurrency = currency;
    return contract;
}

PreTradeRiskPriceEvidence Price(const std::string& source,
                                const std::string& instrument,
                                const std::string& currency,
                                double price,
                                std::int64_t observed = 9500) {
    PreTradeRiskPriceEvidence evidence;
    evidence.sourceId = source;
    evidence.instrument = instrument;
    evidence.currency = currency;
    evidence.connectionEpoch = 9;
    evidence.generation = 4;
    evidence.observedAtMs = observed;
    evidence.price = price;
    return evidence;
}

PreTradeRiskFxEvidence Fx(const std::string& source,
                          const std::string& from,
                          double rate,
                          std::int64_t observed = 9500) {
    PreTradeRiskFxEvidence evidence;
    evidence.sourceId = source;
    evidence.fromCurrency = from;
    evidence.toCurrency = "USD";
    evidence.connectionEpoch = 9;
    evidence.generation = 4;
    evidence.observedAtMs = observed;
    evidence.rate = rate;
    return evidence;
}

PortfolioRiskSnapshotBuildRequest BaseRequest() {
    PortfolioRiskSnapshotBuildRequest request;
    request.subject.portfolioId = "P1";
    request.subject.account = "A1";
    request.subject.venue = "TEST";
    request.subject.baseCurrency = "USD";
    request.subject.instruments = {"AAPL-US", "BMW-DE"};
    request.connectionEpoch = 9;
    request.generation = 4;
    request.evaluatedAtMs = 10000;
    request.maxEvidenceAgeMs = 1000;
    request.positionsComplete = true;
    request.pendingOrdersComplete = true;

    PortfolioRiskValuationInput aapl;
    aapl.contract = Stock("AAPL-US", "USD");
    aapl.authorizedQuoteSourceId = "Q-AAPL";
    aapl.authorizedFxSourceId = "FX-USD";
    aapl.netQuantity = 10.0;
    aapl.mark = Price("Q-AAPL", "AAPL-US", "USD", 200.0);
    aapl.fx = Fx("FX-USD", "USD", 1.0);
    request.positions.push_back(aapl);

    PortfolioRiskValuationInput bmw;
    bmw.contract = Stock("BMW-DE", "EUR");
    bmw.authorizedQuoteSourceId = "Q-BMW";
    bmw.authorizedFxSourceId = "FX-EURUSD";
    bmw.netQuantity = -20.0;
    bmw.mark = Price("Q-BMW", "BMW-DE", "EUR", 100.0);
    bmw.fx = Fx("FX-EURUSD", "EUR", 1.1);
    request.positions.push_back(bmw);

    PortfolioRiskPendingOrderInput buy;
    buy.orderId = "ORDER-BUY-1";
    buy.contract = bmw.contract;
    buy.authorizedQuoteSourceId = bmw.authorizedQuoteSourceId;
    buy.authorizedFxSourceId = bmw.authorizedFxSourceId;
    buy.side = "BUY";
    buy.quantity = 5.0;
    buy.limitPrice = 105.0;
    buy.quote = bmw.mark;
    buy.fx = bmw.fx;
    request.pendingOrders.push_back(buy);

    PortfolioRiskPendingOrderInput sell;
    sell.orderId = "ORDER-SELL-1";
    sell.contract = aapl.contract;
    sell.authorizedQuoteSourceId = aapl.authorizedQuoteSourceId;
    sell.authorizedFxSourceId = aapl.authorizedFxSourceId;
    sell.side = "SELL";
    sell.quantity = 2.0;
    sell.limitPrice = 210.0;
    sell.quote = aapl.mark;
    sell.fx = aapl.fx;
    request.pendingOrders.push_back(sell);

    request.account.complete = true;
    request.account.baseCurrency = "USD";
    request.account.connectionEpoch = 9;
    request.account.generation = 4;
    request.account.observedAtMs = 9600;
    request.account.realizedPnl = -50.0;
    request.account.unrealizedPnl = 20.0;
    request.account.peakEquity = 10000.0;
    request.account.currentEquity = 9900.0;
    return request;
}

bool Near(double left, double right) {
    return std::fabs(left - right) <=
        1e-9 * std::max(1.0, std::fabs(right));
}
}

int main() {
    {
        const PortfolioRiskSnapshotBuildResult result =
            PortfolioRiskSnapshotBuilder::Build(BaseRequest());
        Require(result.ok && result.reasonCode == "PORTFOLIO_RISK_SNAPSHOT_OK",
                "valid multi-asset snapshot must build");
        Require(result.snapshot.identity.complete &&
                    result.snapshot.identity.connectionEpoch == 9 &&
                    result.snapshot.identity.generation == 4 &&
                    result.snapshot.identity.observedAtMs == 9500,
                "snapshot identity must bind the oldest contributing evidence");
        Require(Near(result.snapshot.exposure.currentGrossNotional, 4200.0),
                "current gross must convert both instruments to USD");
        Require(Near(result.snapshot.exposure.pendingBuyNotional, 577.5),
                "pending buy must use conservative limit and FX conversion");
        Require(Near(result.snapshot.exposure.pendingSellNotional, 420.0),
                "pending sell must be base-currency notional");
        Require(result.snapshot.pnl.present &&
                    result.snapshot.pnl.realizedPnl == -50.0 &&
                    result.snapshot.equity.present &&
                    result.snapshot.equity.currentEquity == 9900.0,
                "account PnL/equity sections must be explicit");
    }
    {
        PortfolioRiskSnapshotBuildRequest request = BaseRequest();
        request.positionsComplete = false;
        Require(PortfolioRiskSnapshotBuilder::Build(request).reasonCode ==
                    "PORTFOLIO_RISK_POSITION_SET_INCOMPLETE",
                "position known-empty/completeness must be explicit");
    }
    {
        PortfolioRiskSnapshotBuildRequest request = BaseRequest();
        request.pendingOrdersComplete = false;
        Require(PortfolioRiskSnapshotBuilder::Build(request).reasonCode ==
                    "PORTFOLIO_RISK_PENDING_SET_INCOMPLETE",
                "pending known-empty/completeness must be explicit");
    }
    {
        PortfolioRiskSnapshotBuildRequest request = BaseRequest();
        request.positions.pop_back();
        Require(PortfolioRiskSnapshotBuilder::Build(request).reasonCode ==
                    "PORTFOLIO_RISK_POSITION_SET_INCOMPLETE",
                "missing authorized instrument valuation must fail");
    }
    {
        PortfolioRiskSnapshotBuildRequest request = BaseRequest();
        request.positions[1] = request.positions[0];
        Require(PortfolioRiskSnapshotBuilder::Build(request).reasonCode ==
                    "PORTFOLIO_RISK_POSITION_DUPLICATE",
                "duplicate position valuation must fail");
    }
    {
        PortfolioRiskSnapshotBuildRequest request = BaseRequest();
        request.positions[0].contract.multiplier = 100.0;
        Require(PortfolioRiskSnapshotBuilder::Build(request).reasonCode ==
                    "PORTFOLIO_RISK_CONTRACT_INVALID",
                "stock multiplier ambiguity must fail");
    }
    {
        PortfolioRiskSnapshotBuildRequest request = BaseRequest();
        request.positions[1].mark.observedAtMs = 8999;
        Require(PortfolioRiskSnapshotBuilder::Build(request).reasonCode ==
                    "PORTFOLIO_RISK_VALUATION_EVIDENCE_INVALID",
                "stale mark must fail");
    }
    {
        PortfolioRiskSnapshotBuildRequest request = BaseRequest();
        request.positions[1].fx.toCurrency = "EUR";
        Require(PortfolioRiskSnapshotBuilder::Build(request).reasonCode ==
                    "PORTFOLIO_RISK_VALUATION_EVIDENCE_INVALID",
                "FX evidence must convert to account base currency");
    }
    {
        PortfolioRiskSnapshotBuildRequest request = BaseRequest();
        request.pendingOrders[1].orderId = request.pendingOrders[0].orderId;
        Require(PortfolioRiskSnapshotBuilder::Build(request).reasonCode ==
                    "PORTFOLIO_RISK_PENDING_ORDER_IDENTITY_INVALID",
                "duplicate pending order identity must fail");
    }
    {
        PortfolioRiskSnapshotBuildRequest request = BaseRequest();
        request.pendingOrders[0].contract.specificationVersion = 2;
        Require(PortfolioRiskSnapshotBuilder::Build(request).reasonCode ==
                    "PORTFOLIO_RISK_PENDING_CONTRACT_INVALID",
                "pending order contract drift must fail");
    }
    {
        PortfolioRiskSnapshotBuildRequest request = BaseRequest();
        request.pendingOrders[0].authorizedQuoteSourceId = "OTHER";
        Require(PortfolioRiskSnapshotBuilder::Build(request).reasonCode ==
                    "PORTFOLIO_RISK_PENDING_CONTRACT_INVALID",
                "pending order source drift must fail");
    }
    {
        PortfolioRiskSnapshotBuildRequest request = BaseRequest();
        request.account.complete = false;
        Require(PortfolioRiskSnapshotBuilder::Build(request).reasonCode ==
                    "PORTFOLIO_RISK_ACCOUNT_EVIDENCE_INVALID",
                "account snapshot completeness must be explicit");
    }
    {
        PortfolioRiskSnapshotBuildRequest request = BaseRequest();
        request.account.baseCurrency = "EUR";
        Require(PortfolioRiskSnapshotBuilder::Build(request).reasonCode ==
                    "PORTFOLIO_RISK_ACCOUNT_EVIDENCE_INVALID",
                "account PnL/equity currency must match portfolio base currency");
    }
    {
        PortfolioRiskSnapshotBuildRequest request = BaseRequest();
        request.account.generation = 3;
        Require(PortfolioRiskSnapshotBuilder::Build(request).reasonCode ==
                    "PORTFOLIO_RISK_ACCOUNT_EVIDENCE_INVALID",
                "mixed-generation account evidence must fail");
    }
    {
        PortfolioRiskSnapshotBuildRequest request = BaseRequest();
        request.account.currentEquity = 10001.0;
        Require(PortfolioRiskSnapshotBuilder::Build(request).reasonCode ==
                    "PORTFOLIO_RISK_ACCOUNT_EVIDENCE_INVALID",
                "current equity above declared peak must fail");
    }
    {
        PortfolioRiskSnapshotBuildRequest request = BaseRequest();
        request.positions[0].netQuantity = std::numeric_limits<double>::max();
        request.positions[0].mark.price = std::numeric_limits<double>::max();
        Require(PortfolioRiskSnapshotBuilder::Build(request).reasonCode ==
                    "PORTFOLIO_RISK_NOTIONAL_OVERFLOW",
                "valuation overflow must fail closed");
    }
    {
        PortfolioRiskSnapshotBuildRequest request = BaseRequest();
        request.positions[0].fx.rate = 1.01;
        Require(PortfolioRiskSnapshotBuilder::Build(request).reasonCode ==
                    "PORTFOLIO_RISK_VALUATION_EVIDENCE_INVALID",
                "same-currency FX must be exact identity conversion");
    }
    {
        PortfolioRiskSnapshotBuildRequest request = BaseRequest();
        request.pendingOrders[0].quote.observedAtMs = 10001;
        Require(PortfolioRiskSnapshotBuilder::Build(request).reasonCode ==
                    "PORTFOLIO_RISK_PENDING_EVIDENCE_INVALID",
                "future-dated pending quote must fail");
    }
    std::cout << "portfolio_risk_snapshot_builder_tests: PASS" << std::endl;
    return 0;
}
