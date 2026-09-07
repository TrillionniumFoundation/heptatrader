#include "pre_trade_risk_engine.h"

#include <cmath>
#include <initializer_list>

namespace {
PreTradeRiskDecision Allow(double orderNotional = 0.0,
                           double worstCaseGrossNotional = 0.0,
                           std::uint64_t snapshotConnectionEpoch = 0,
                           std::uint64_t snapshotGeneration = 0) {
    PreTradeRiskDecision d;
    d.allow = true;
    d.reasonCode = "RISK_OK";
    d.orderNotional = orderNotional;
    d.worstCaseGrossNotional = worstCaseGrossNotional;
    d.snapshotConnectionEpoch = snapshotConnectionEpoch;
    d.snapshotGeneration = snapshotGeneration;
    return d;
}

PreTradeRiskDecision Reject(const char* code, const std::string& detail,
                            double orderNotional = 0.0,
                            double worstCaseGrossNotional = 0.0,
                            std::uint64_t snapshotConnectionEpoch = 0,
                            std::uint64_t snapshotGeneration = 0) {
    PreTradeRiskDecision d;
    d.allow = false;
    d.reasonCode = code ? code : "RISK_REJECTED";
    d.detail = detail;
    d.orderNotional = orderNotional;
    d.worstCaseGrossNotional = worstCaseGrossNotional;
    d.snapshotConnectionEpoch = snapshotConnectionEpoch;
    d.snapshotGeneration = snapshotGeneration;
    return d;
}

bool FiniteNonNegative(double value) {
    return std::isfinite(value) && value >= 0.0;
}

bool LimitEnabled(double value) {
    return std::isfinite(value) && value > 0.0;
}

bool ExceedsInclusiveLimit(double value, double limit) {
    return value > limit;
}

bool ValidSnapshotIdentity(const PreTradeRiskSnapshotIdentity& identity) {
    return identity.present && identity.complete &&
        identity.connectionEpoch > 0 && identity.generation > 0 &&
        identity.observedAtMs > 0 && identity.evaluatedAtMs > 0 &&
        identity.evaluatedAtMs >= identity.observedAtMs;
}

bool SameGeneration(std::uint64_t sectionGeneration,
                    const PreTradeRiskSnapshotIdentity& identity) {
    return sectionGeneration > 0 && sectionGeneration == identity.generation;
}
}

PreTradeRiskDecision PreTradeRiskEngine::Evaluate(
    const PreTradeRiskConfig& cfg, const PreTradeRiskContext& ctx) {
    if (cfg.globalKillSwitch) {
        return Reject("RISK_GLOBAL_KILL_SWITCH_ON", "global kill switch enabled");
    }

    if (!cfg.enableOrderSubmission) {
        return Reject("RISK_ORDER_SUBMISSION_DISABLED", "order submission gate is closed");
    }

    if (!std::isfinite(ctx.totalQuantity) || ctx.totalQuantity <= 0.0 ||
        !std::isfinite(cfg.maxOrderQuantity) || cfg.maxOrderQuantity <= 0.0 ||
        ctx.totalQuantity > cfg.maxOrderQuantity) {
        return Reject("RISK_QTY_OUT_OF_RANGE", "qty invalid or exceeds maxOrderQuantity");
    }

    if (ctx.todayOrderCount < 0 || cfg.maxDailyOrders <= 0 ||
        ctx.todayOrderCount >= cfg.maxDailyOrders) {
        return Reject("RISK_DAILY_ORDER_LIMIT", "daily order limit reached or invalid");
    }

    if (!ctx.accountWhitelisted) {
        return Reject("RISK_ACCOUNT_NOT_WHITELISTED", "account is not in whitelist");
    }

    if (!ctx.paperAccount) {
        if (!cfg.allowLiveTrading) {
            return Reject("RISK_LIVE_NOT_AUTHORIZED", "live trading is not explicitly authorized");
        }
        if (cfg.liveKillSwitch) {
            return Reject("RISK_LIVE_KILL_SWITCH_ON", "live kill switch is ON");
        }
    }

    bool reducingExposure = false;
    if (cfg.flattenOnly) {
        if (!ctx.positionKnown || !std::isfinite(ctx.netPosition)) {
            return Reject("RISK_FLATTEN_ONLY_POSITION_UNKNOWN", "flatten-only mode requires known finite position");
        }
        reducingExposure = IsFlatteningOrder(ctx);
        if (!reducingExposure) {
            return Reject("RISK_FLATTEN_ONLY_BLOCK", "order is not reducing current exposure");
        }
    }

    if (!std::isfinite(ctx.limitPrice) || !std::isfinite(ctx.referencePrice) ||
        ctx.limitPrice < 0.0 || ctx.referencePrice < 0.0) {
        return Reject("RISK_PRICE_INVALID", "price inputs must be finite and non-negative");
    }

    if (cfg.maxPriceDeviationBps < 0.0 || !std::isfinite(cfg.maxPriceDeviationBps)) {
        return Reject("RISK_PRICE_POLICY_INVALID", "maxPriceDeviationBps must be finite and non-negative");
    }
    if (cfg.maxPriceDeviationBps > 0.0 && ctx.orderType == "LMT") {
        if (ctx.limitPrice <= 0.0 || ctx.referencePrice <= 0.0) {
            return Reject("RISK_REFERENCE_PRICE_REQUIRED", "LMT deviation check requires positive prices");
        }
        const double devBps = std::abs(ctx.limitPrice - ctx.referencePrice) /
            ctx.referencePrice * 10000.0;
        if (!std::isfinite(devBps) || devBps > cfg.maxPriceDeviationBps) {
            return Reject("RISK_PRICE_DEVIATION_TOO_LARGE", "limit price deviation exceeds maxPriceDeviationBps");
        }
    }

    for (double limit : {
            cfg.maxOrderNotional,
            cfg.maxWorstCaseGrossNotional,
            cfg.maxDailyLoss,
            cfg.maxDrawdown}) {
        if (!std::isfinite(limit) || limit < 0.0) {
            return Reject("RISK_LIMIT_POLICY_INVALID", "optional risk limits must be finite and non-negative");
        }
    }
    if (cfg.maxSnapshotAgeMs < 0) {
        return Reject("RISK_SNAPSHOT_POLICY_INVALID", "maxSnapshotAgeMs must be non-negative");
    }

    const bool needsExposure =
        !reducingExposure && LimitEnabled(cfg.maxWorstCaseGrossNotional);
    const bool needsPnl = !reducingExposure && LimitEnabled(cfg.maxDailyLoss);
    const bool needsEquity = !reducingExposure && LimitEnabled(cfg.maxDrawdown);
    const bool needsPortfolioSnapshot = needsExposure || needsPnl || needsEquity;
    const bool needsSnapshot = needsPortfolioSnapshot || cfg.maxSnapshotAgeMs > 0;
    const PreTradeRiskSnapshotIdentity& identity =
        ctx.authoritativeSnapshot.identity;

    if (needsPortfolioSnapshot && cfg.maxSnapshotAgeMs == 0) {
        return Reject("RISK_SNAPSHOT_FRESHNESS_POLICY_REQUIRED",
                      "portfolio limits require a positive maxSnapshotAgeMs");
    }
    if (needsSnapshot) {
        if (!ValidSnapshotIdentity(identity)) {
            return Reject("RISK_SNAPSHOT_IDENTITY_REQUIRED",
                          "authoritative snapshot identity, completeness, epoch, generation and timestamps are required");
        }
        if (identity.evaluatedAtMs - identity.observedAtMs >
            cfg.maxSnapshotAgeMs) {
            return Reject("RISK_SNAPSHOT_STALE",
                          "authoritative risk snapshot is stale",
                          0.0, 0.0, identity.connectionEpoch,
                          identity.generation);
        }
    }

    if (needsExposure) {
        const PreTradeRiskExposureSnapshot& exposure =
            ctx.authoritativeSnapshot.exposure;
        if (!exposure.present) {
            return Reject("RISK_SNAPSHOT_EXPOSURE_REQUIRED",
                          "gross and pending exposure snapshot is required",
                          0.0, 0.0, identity.connectionEpoch,
                          identity.generation);
        }
        if (!SameGeneration(exposure.generation, identity)) {
            return Reject("RISK_SNAPSHOT_GENERATION_MISMATCH",
                          "exposure does not belong to the authoritative snapshot generation",
                          0.0, 0.0, identity.connectionEpoch,
                          identity.generation);
        }
        if (!FiniteNonNegative(exposure.currentGrossNotional) ||
            !FiniteNonNegative(exposure.pendingBuyNotional) ||
            !FiniteNonNegative(exposure.pendingSellNotional)) {
            return Reject("RISK_NUMERIC_CONTEXT_INVALID",
                          "gross or pending exposure context is invalid",
                          0.0, 0.0, identity.connectionEpoch,
                          identity.generation);
        }
    }

    if (needsPnl) {
        const PreTradeRiskPnlSnapshot& pnl = ctx.authoritativeSnapshot.pnl;
        if (!pnl.present) {
            return Reject("RISK_SNAPSHOT_PNL_REQUIRED",
                          "realized and unrealized PnL snapshot is required",
                          0.0, 0.0, identity.connectionEpoch,
                          identity.generation);
        }
        if (!SameGeneration(pnl.generation, identity)) {
            return Reject("RISK_SNAPSHOT_GENERATION_MISMATCH",
                          "PnL does not belong to the authoritative snapshot generation",
                          0.0, 0.0, identity.connectionEpoch,
                          identity.generation);
        }
        if (!std::isfinite(pnl.realizedPnl) ||
            !std::isfinite(pnl.unrealizedPnl)) {
            return Reject("RISK_NUMERIC_CONTEXT_INVALID",
                          "PnL context is invalid",
                          0.0, 0.0, identity.connectionEpoch,
                          identity.generation);
        }
    }

    if (needsEquity) {
        const PreTradeRiskEquitySnapshot& equity =
            ctx.authoritativeSnapshot.equity;
        if (!equity.present) {
            return Reject("RISK_SNAPSHOT_EQUITY_REQUIRED",
                          "peak and current equity snapshot is required",
                          0.0, 0.0, identity.connectionEpoch,
                          identity.generation);
        }
        if (!SameGeneration(equity.generation, identity)) {
            return Reject("RISK_SNAPSHOT_GENERATION_MISMATCH",
                          "equity does not belong to the authoritative snapshot generation",
                          0.0, 0.0, identity.connectionEpoch,
                          identity.generation);
        }
        if (!FiniteNonNegative(equity.peakEquity) ||
            !FiniteNonNegative(equity.currentEquity)) {
            return Reject("RISK_NUMERIC_CONTEXT_INVALID",
                          "equity context is invalid",
                          0.0, 0.0, identity.connectionEpoch,
                          identity.generation);
        }
    }

    double orderNotional = 0.0;
    if (ctx.baseCurrencyOrderNotionalPresent) {
        if (!std::isfinite(ctx.baseCurrencyOrderNotional) ||
            ctx.baseCurrencyOrderNotional <= 0.0) {
            return Reject("RISK_ORDER_NOTIONAL_INVALID",
                          "present base-currency order notional must be finite and positive",
                          0.0, 0.0, identity.connectionEpoch,
                          identity.generation);
        }
        orderNotional = ctx.baseCurrencyOrderNotional;
    } else if (LimitEnabled(cfg.maxOrderNotional) || needsExposure) {
        const double price = ctx.referencePrice > 0.0 ?
            ctx.referencePrice : ctx.limitPrice;
        if (!std::isfinite(price) || price <= 0.0) {
            return Reject("RISK_ORDER_NOTIONAL_UNAVAILABLE", "base-currency order notional requires a positive authoritative price");
        }
        orderNotional = ctx.totalQuantity * price;
        if (!std::isfinite(orderNotional) || orderNotional <= 0.0) {
            return Reject("RISK_ORDER_NOTIONAL_UNAVAILABLE", "calculated order notional is invalid");
        }
    }

    double worstCaseGrossNotional = orderNotional;
    if (needsExposure) {
        const PreTradeRiskExposureSnapshot& exposure =
            ctx.authoritativeSnapshot.exposure;
        worstCaseGrossNotional = exposure.currentGrossNotional +
            exposure.pendingBuyNotional + exposure.pendingSellNotional +
            orderNotional;
        if (!std::isfinite(worstCaseGrossNotional)) {
            return Reject("RISK_WORST_CASE_GROSS_INVALID", "worst-case gross notional overflowed",
                          orderNotional, 0.0, identity.connectionEpoch,
                          identity.generation);
        }
    }

    // A verified flatten-only order is an exit path. Portfolio loss/gross
    // breaches may force that mode and therefore must not block exposure
    // reduction. Per-order quantity and price validity still apply above.
    if (!reducingExposure) {
        if (LimitEnabled(cfg.maxOrderNotional) &&
            ExceedsInclusiveLimit(orderNotional, cfg.maxOrderNotional)) {
            return Reject("RISK_ORDER_NOTIONAL_LIMIT", "order notional exceeds maxOrderNotional",
                          orderNotional, worstCaseGrossNotional,
                          identity.connectionEpoch, identity.generation);
        }
        if (needsExposure &&
            ExceedsInclusiveLimit(worstCaseGrossNotional,
                                  cfg.maxWorstCaseGrossNotional)) {
            return Reject("RISK_WORST_CASE_GROSS_LIMIT", "current plus pending plus candidate exposure exceeds maxWorstCaseGrossNotional",
                          orderNotional, worstCaseGrossNotional,
                          identity.connectionEpoch, identity.generation);
        }

        if (needsPnl) {
            const PreTradeRiskPnlSnapshot& pnl = ctx.authoritativeSnapshot.pnl;
            const double totalPnl = pnl.realizedPnl + pnl.unrealizedPnl;
            const double dailyLoss = totalPnl < 0.0 ? -totalPnl : 0.0;
            if (!std::isfinite(dailyLoss)) {
                return Reject("RISK_DAILY_LOSS_INVALID", "daily loss calculation is invalid",
                              orderNotional, worstCaseGrossNotional,
                              identity.connectionEpoch, identity.generation);
            }
            if (ExceedsInclusiveLimit(dailyLoss, cfg.maxDailyLoss)) {
                return Reject("RISK_DAILY_LOSS_LIMIT", "daily loss exceeds maxDailyLoss",
                              orderNotional, worstCaseGrossNotional,
                              identity.connectionEpoch, identity.generation);
            }
        }

        if (needsEquity) {
            const PreTradeRiskEquitySnapshot& equity =
                ctx.authoritativeSnapshot.equity;
            const double drawdown = equity.peakEquity > equity.currentEquity ?
                equity.peakEquity - equity.currentEquity : 0.0;
            if (!std::isfinite(drawdown)) {
                return Reject("RISK_DRAWDOWN_INVALID", "drawdown calculation is invalid",
                              orderNotional, worstCaseGrossNotional,
                              identity.connectionEpoch, identity.generation);
            }
            if (ExceedsInclusiveLimit(drawdown, cfg.maxDrawdown)) {
                return Reject("RISK_DRAWDOWN_LIMIT", "drawdown exceeds maxDrawdown",
                              orderNotional, worstCaseGrossNotional,
                              identity.connectionEpoch, identity.generation);
            }
        }
    }

    return Allow(orderNotional, worstCaseGrossNotional,
                 needsSnapshot ? identity.connectionEpoch : 0,
                 needsSnapshot ? identity.generation : 0);
}

bool PreTradeRiskEngine::IsFlatteningOrder(const PreTradeRiskContext& ctx) {
    if (!std::isfinite(ctx.totalQuantity) || !std::isfinite(ctx.netPosition) ||
        ctx.totalQuantity <= 0.0) {
        return false;
    }

    if (ctx.netPosition > 0.0) {
        if (ctx.action != "SELL") return false;
        const double afterPosition = ctx.netPosition - ctx.totalQuantity;
        return std::isfinite(afterPosition) && afterPosition >= 0.0 &&
            afterPosition < ctx.netPosition;
    }

    if (ctx.netPosition < 0.0) {
        if (ctx.action != "BUY") return false;
        const double afterPosition = ctx.netPosition + ctx.totalQuantity;
        return std::isfinite(afterPosition) && afterPosition <= 0.0 &&
            afterPosition > ctx.netPosition;
    }

    return false;
}
