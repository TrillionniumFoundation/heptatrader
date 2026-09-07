#include "risk/pre_trade_risk_engine.h"

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

PreTradeRiskConfig BaseConfig() {
    PreTradeRiskConfig cfg;
    cfg.enableOrderSubmission = true;
    cfg.maxOrderQuantity = 10.0;
    cfg.maxDailyOrders = 100;
    cfg.maxPriceDeviationBps = 100.0;
    return cfg;
}

PreTradeRiskContext BaseContext() {
    PreTradeRiskContext ctx;
    ctx.venue = "SIM";
    ctx.account = "SIM";
    ctx.symbol = "EUR.USD";
    ctx.action = "BUY";
    ctx.orderType = "LMT";
    ctx.totalQuantity = 1.0;
    ctx.limitPrice = 100.0;
    ctx.referencePrice = 100.0;
    ctx.accountWhitelisted = true;
    ctx.paperAccount = true;
    ctx.positionKnown = true;
    return ctx;
}
}

int main() {
    {
        const PreTradeRiskDecision d =
            PreTradeRiskEngine::Evaluate(BaseConfig(), BaseContext());
        Require(d.allow, "legacy-compatible baseline must pass");
        Require(d.reasonCode == "RISK_OK", "baseline reason");
    }
    {
        PreTradeRiskContext ctx = BaseContext();
        ctx.totalQuantity = std::numeric_limits<double>::quiet_NaN();
        const PreTradeRiskDecision d =
            PreTradeRiskEngine::Evaluate(BaseConfig(), ctx);
        Require(!d.allow && d.reasonCode == "RISK_QTY_OUT_OF_RANGE",
                "NaN quantity must fail closed");
    }
    {
        PreTradeRiskConfig cfg = BaseConfig();
        cfg.maxOrderNotional = 100.0;
        PreTradeRiskContext ctx = BaseContext();
        PreTradeRiskDecision d = PreTradeRiskEngine::Evaluate(cfg, ctx);
        Require(d.allow && d.orderNotional == 100.0,
                "inclusive order-notional boundary must pass");
        ctx.baseCurrencyOrderNotional = 100.01;
        d = PreTradeRiskEngine::Evaluate(cfg, ctx);
        Require(!d.allow && d.reasonCode == "RISK_ORDER_NOTIONAL_LIMIT",
                "above order-notional boundary must fail");
    }
    {
        PreTradeRiskConfig cfg = BaseConfig();
        cfg.maxWorstCaseGrossNotional = 1000.0;
        PreTradeRiskContext ctx = BaseContext();
        ctx.baseCurrencyOrderNotional = 100.0;
        ctx.currentGrossNotional = 400.0;
        ctx.pendingBuyNotional = 250.0;
        ctx.pendingSellNotional = 250.0;
        PreTradeRiskDecision d = PreTradeRiskEngine::Evaluate(cfg, ctx);
        Require(d.allow && d.worstCaseGrossNotional == 1000.0,
                "inclusive worst-case gross boundary must pass");
        ctx.pendingSellNotional = 250.01;
        d = PreTradeRiskEngine::Evaluate(cfg, ctx);
        Require(!d.allow && d.reasonCode == "RISK_WORST_CASE_GROSS_LIMIT",
                "pending exposure must count in worst-case gross");
    }
    {
        PreTradeRiskConfig cfg = BaseConfig();
        cfg.maxSnapshotAgeMs = 1000;
        PreTradeRiskContext ctx = BaseContext();
        ctx.nowMs = 5000;
        ctx.snapshotObservedAtMs = 4000;
        Require(PreTradeRiskEngine::Evaluate(cfg, ctx).allow,
                "inclusive snapshot-age boundary must pass");
        ctx.snapshotObservedAtMs = 3999;
        const PreTradeRiskDecision d = PreTradeRiskEngine::Evaluate(cfg, ctx);
        Require(!d.allow && d.reasonCode == "RISK_SNAPSHOT_STALE",
                "stale snapshot must fail");
        ctx.snapshotComplete = false;
        Require(PreTradeRiskEngine::Evaluate(cfg, ctx).reasonCode ==
                    "RISK_SNAPSHOT_INCOMPLETE",
                "incomplete snapshot must fail before age");
    }
    {
        PreTradeRiskConfig cfg = BaseConfig();
        cfg.maxDailyLoss = 50.0;
        cfg.maxDrawdown = 75.0;
        PreTradeRiskContext ctx = BaseContext();
        ctx.realizedPnl = -30.0;
        ctx.unrealizedPnl = -20.0;
        ctx.peakEquity = 1000.0;
        ctx.currentEquity = 925.0;
        Require(PreTradeRiskEngine::Evaluate(cfg, ctx).allow,
                "inclusive loss and drawdown limits must pass");
        ctx.unrealizedPnl = -20.01;
        Require(PreTradeRiskEngine::Evaluate(cfg, ctx).reasonCode ==
                    "RISK_DAILY_LOSS_LIMIT",
                "daily loss breach must fail");
        ctx.unrealizedPnl = -20.0;
        ctx.currentEquity = 924.99;
        Require(PreTradeRiskEngine::Evaluate(cfg, ctx).reasonCode ==
                    "RISK_DRAWDOWN_LIMIT",
                "drawdown breach must fail");
    }
    {
        PreTradeRiskConfig cfg = BaseConfig();
        cfg.flattenOnly = true;
        cfg.maxWorstCaseGrossNotional = 1.0;
        cfg.maxDailyLoss = 1.0;
        PreTradeRiskContext ctx = BaseContext();
        ctx.action = "SELL";
        ctx.netPosition = 5.0;
        ctx.totalQuantity = 2.0;
        ctx.currentGrossNotional = 10000.0;
        ctx.realizedPnl = -10000.0;
        Require(PreTradeRiskEngine::Evaluate(cfg, ctx).allow,
                "partial long flatten must remain available during breaches");
        ctx.totalQuantity = 5.0;
        Require(PreTradeRiskEngine::Evaluate(cfg, ctx).allow,
                "exact long flatten to zero must pass");
        ctx.totalQuantity = 6.0;
        Require(PreTradeRiskEngine::Evaluate(cfg, ctx).reasonCode ==
                    "RISK_FLATTEN_ONLY_BLOCK",
                "small long-to-short crossing must be blocked");
        ctx.totalQuantity = 10.0;
        Require(PreTradeRiskEngine::Evaluate(cfg, ctx).reasonCode ==
                    "RISK_FLATTEN_ONLY_BLOCK",
                "large long-to-short crossing must be blocked");
        ctx.action = "BUY";
        ctx.totalQuantity = 1.0;
        Require(PreTradeRiskEngine::Evaluate(cfg, ctx).reasonCode ==
                    "RISK_FLATTEN_ONLY_BLOCK",
                "same-direction long increase must be blocked");

        ctx.netPosition = -5.0;
        ctx.action = "BUY";
        ctx.totalQuantity = 2.0;
        Require(PreTradeRiskEngine::Evaluate(cfg, ctx).allow,
                "partial short flatten must pass");
        ctx.totalQuantity = 5.0;
        Require(PreTradeRiskEngine::Evaluate(cfg, ctx).allow,
                "exact short flatten to zero must pass");
        ctx.totalQuantity = 6.0;
        Require(PreTradeRiskEngine::Evaluate(cfg, ctx).reasonCode ==
                    "RISK_FLATTEN_ONLY_BLOCK",
                "small short-to-long crossing must be blocked");
        ctx.action = "SELL";
        ctx.totalQuantity = 1.0;
        Require(PreTradeRiskEngine::Evaluate(cfg, ctx).reasonCode ==
                    "RISK_FLATTEN_ONLY_BLOCK",
                "same-direction short increase must be blocked");

        ctx.netPosition = 0.0;
        ctx.action = "BUY";
        Require(PreTradeRiskEngine::Evaluate(cfg, ctx).reasonCode ==
                    "RISK_FLATTEN_ONLY_BLOCK",
                "zero position cannot be flattened into new exposure");
    }
    {
        PreTradeRiskConfig cfg = BaseConfig();
        cfg.maxPriceDeviationBps = 10.0;
        PreTradeRiskContext ctx = BaseContext();
        ctx.limitPrice = 101.0;
        const PreTradeRiskDecision d = PreTradeRiskEngine::Evaluate(cfg, ctx);
        Require(!d.allow && d.reasonCode == "RISK_PRICE_DEVIATION_TOO_LARGE",
                "price deviation must fail");
    }
    std::cout << "pre_trade_risk_engine_tests: PASS" << std::endl;
    return 0;
}
