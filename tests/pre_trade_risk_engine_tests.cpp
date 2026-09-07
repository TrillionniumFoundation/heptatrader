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

void BindZeroSnapshot(PreTradeRiskContext& ctx, std::uint64_t generation = 7) {
    ctx.authoritativeSnapshot.identity.present = true;
    ctx.authoritativeSnapshot.identity.complete = true;
    ctx.authoritativeSnapshot.identity.connectionEpoch = 3;
    ctx.authoritativeSnapshot.identity.generation = generation;
    ctx.authoritativeSnapshot.identity.observedAtMs = 4000;
    ctx.authoritativeSnapshot.identity.evaluatedAtMs = 5000;

    ctx.authoritativeSnapshot.exposure.present = true;
    ctx.authoritativeSnapshot.exposure.generation = generation;
    ctx.authoritativeSnapshot.pnl.present = true;
    ctx.authoritativeSnapshot.pnl.generation = generation;
    ctx.authoritativeSnapshot.equity.present = true;
    ctx.authoritativeSnapshot.equity.generation = generation;
}
}

int main() {
    {
        const PreTradeRiskDecision d =
            PreTradeRiskEngine::Evaluate(BaseConfig(), BaseContext());
        Require(d.allow, "legacy-compatible baseline without portfolio limits must pass");
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
                "inclusive derived order-notional boundary must pass");
        ctx.baseCurrencyOrderNotionalPresent = true;
        ctx.baseCurrencyOrderNotional = 100.01;
        d = PreTradeRiskEngine::Evaluate(cfg, ctx);
        Require(!d.allow && d.reasonCode == "RISK_ORDER_NOTIONAL_LIMIT",
                "above order-notional boundary must fail");
        ctx.baseCurrencyOrderNotional = 0.0;
        d = PreTradeRiskEngine::Evaluate(cfg, ctx);
        Require(!d.allow && d.reasonCode == "RISK_ORDER_NOTIONAL_INVALID",
                "present zero notional must not mean missing or safe");
    }
    {
        PreTradeRiskConfig cfg = BaseConfig();
        cfg.maxWorstCaseGrossNotional = 1000.0;
        cfg.maxSnapshotAgeMs = 1000;
        PreTradeRiskContext ctx = BaseContext();
        ctx.baseCurrencyOrderNotionalPresent = true;
        ctx.baseCurrencyOrderNotional = 100.0;
        BindZeroSnapshot(ctx);
        ctx.authoritativeSnapshot.exposure.currentGrossNotional = 400.0;
        ctx.authoritativeSnapshot.exposure.pendingBuyNotional = 250.0;
        ctx.authoritativeSnapshot.exposure.pendingSellNotional = 250.0;
        PreTradeRiskDecision d = PreTradeRiskEngine::Evaluate(cfg, ctx);
        Require(d.allow && d.worstCaseGrossNotional == 1000.0,
                "inclusive worst-case gross boundary must pass");
        Require(d.snapshotConnectionEpoch == 3 && d.snapshotGeneration == 7,
                "accepted decision must retain snapshot identity");
        ctx.authoritativeSnapshot.exposure.pendingSellNotional = 250.01;
        d = PreTradeRiskEngine::Evaluate(cfg, ctx);
        Require(!d.allow && d.reasonCode == "RISK_WORST_CASE_GROSS_LIMIT",
                "pending exposure must count in worst-case gross");
    }
    {
        PreTradeRiskConfig cfg = BaseConfig();
        cfg.maxSnapshotAgeMs = 1000;
        PreTradeRiskContext ctx = BaseContext();
        BindZeroSnapshot(ctx);
        Require(PreTradeRiskEngine::Evaluate(cfg, ctx).allow,
                "inclusive snapshot-age boundary must pass");
        ctx.authoritativeSnapshot.identity.observedAtMs = 3999;
        const PreTradeRiskDecision d = PreTradeRiskEngine::Evaluate(cfg, ctx);
        Require(!d.allow && d.reasonCode == "RISK_SNAPSHOT_STALE",
                "stale snapshot must fail");
        ctx.authoritativeSnapshot.identity.complete = false;
        Require(PreTradeRiskEngine::Evaluate(cfg, ctx).reasonCode ==
                    "RISK_SNAPSHOT_IDENTITY_REQUIRED",
                "incomplete snapshot identity must fail before age");
    }
    {
        PreTradeRiskConfig cfg = BaseConfig();
        cfg.maxDailyLoss = 50.0;
        cfg.maxDrawdown = 75.0;
        cfg.maxSnapshotAgeMs = 1000;
        PreTradeRiskContext ctx = BaseContext();
        BindZeroSnapshot(ctx);
        ctx.authoritativeSnapshot.pnl.realizedPnl = -30.0;
        ctx.authoritativeSnapshot.pnl.unrealizedPnl = -20.0;
        ctx.authoritativeSnapshot.equity.peakEquity = 1000.0;
        ctx.authoritativeSnapshot.equity.currentEquity = 925.0;
        Require(PreTradeRiskEngine::Evaluate(cfg, ctx).allow,
                "inclusive loss and drawdown limits must pass");
        ctx.authoritativeSnapshot.pnl.unrealizedPnl = -20.01;
        Require(PreTradeRiskEngine::Evaluate(cfg, ctx).reasonCode ==
                    "RISK_DAILY_LOSS_LIMIT",
                "daily loss breach must fail");
        ctx.authoritativeSnapshot.pnl.unrealizedPnl = -20.0;
        ctx.authoritativeSnapshot.equity.currentEquity = 924.99;
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
    {
        PreTradeRiskConfig cfg = BaseConfig();
        cfg.maxWorstCaseGrossNotional = 1000.0;
        PreTradeRiskContext ctx = BaseContext();
        ctx.snapshotComplete = true;
        ctx.currentGrossNotional = 0.0;
        ctx.pendingBuyNotional = 0.0;
        ctx.pendingSellNotional = 0.0;
        Require(PreTradeRiskEngine::Evaluate(cfg, ctx).reasonCode ==
                    "RISK_SNAPSHOT_FRESHNESS_POLICY_REQUIRED",
                "portfolio policy without freshness policy must fail");
        cfg.maxSnapshotAgeMs = 1000;
        Require(PreTradeRiskEngine::Evaluate(cfg, ctx).reasonCode ==
                    "RISK_SNAPSHOT_IDENTITY_REQUIRED",
                "legacy default fields must not masquerade as an authoritative snapshot");
    }
    {
        PreTradeRiskConfig cfg = BaseConfig();
        cfg.maxWorstCaseGrossNotional = 1000.0;
        cfg.maxSnapshotAgeMs = 1000;
        PreTradeRiskContext ctx = BaseContext();
        BindZeroSnapshot(ctx);
        ctx.authoritativeSnapshot.exposure.present = false;
        Require(PreTradeRiskEngine::Evaluate(cfg, ctx).reasonCode ==
                    "RISK_SNAPSHOT_EXPOSURE_REQUIRED",
                "missing pending exposure must fail");
        BindZeroSnapshot(ctx);
        ctx.authoritativeSnapshot.exposure.generation = 8;
        Require(PreTradeRiskEngine::Evaluate(cfg, ctx).reasonCode ==
                    "RISK_SNAPSHOT_GENERATION_MISMATCH",
                "mixed exposure generation must fail");
        BindZeroSnapshot(ctx);
        ctx.authoritativeSnapshot.identity.connectionEpoch = 0;
        Require(PreTradeRiskEngine::Evaluate(cfg, ctx).reasonCode ==
                    "RISK_SNAPSHOT_IDENTITY_REQUIRED",
                "missing connection epoch must fail");
        BindZeroSnapshot(ctx);
        ctx.authoritativeSnapshot.identity.generation = 0;
        Require(PreTradeRiskEngine::Evaluate(cfg, ctx).reasonCode ==
                    "RISK_SNAPSHOT_IDENTITY_REQUIRED",
                "missing snapshot generation must fail");
    }
    {
        PreTradeRiskConfig cfg = BaseConfig();
        cfg.maxDailyLoss = 10.0;
        cfg.maxSnapshotAgeMs = 1000;
        PreTradeRiskContext ctx = BaseContext();
        BindZeroSnapshot(ctx);
        ctx.authoritativeSnapshot.pnl.present = false;
        Require(PreTradeRiskEngine::Evaluate(cfg, ctx).reasonCode ==
                    "RISK_SNAPSHOT_PNL_REQUIRED",
                "missing PnL presence must fail");
    }
    {
        PreTradeRiskConfig cfg = BaseConfig();
        cfg.maxDrawdown = 10.0;
        cfg.maxSnapshotAgeMs = 1000;
        PreTradeRiskContext ctx = BaseContext();
        BindZeroSnapshot(ctx);
        ctx.authoritativeSnapshot.equity.present = false;
        Require(PreTradeRiskEngine::Evaluate(cfg, ctx).reasonCode ==
                    "RISK_SNAPSHOT_EQUITY_REQUIRED",
                "missing equity presence must fail");
    }
    {
        PreTradeRiskConfig cfg = BaseConfig();
        cfg.maxWorstCaseGrossNotional = 1000.0;
        cfg.maxDailyLoss = 10.0;
        cfg.maxDrawdown = 10.0;
        cfg.maxSnapshotAgeMs = 1000;
        PreTradeRiskContext ctx = BaseContext();
        BindZeroSnapshot(ctx);
        const PreTradeRiskDecision d = PreTradeRiskEngine::Evaluate(cfg, ctx);
        Require(d.allow,
                "explicit authoritative zero exposure, zero PnL and zero equity must pass");
    }
    std::cout << "pre_trade_risk_engine_tests: PASS" << std::endl;
    return 0;
}
