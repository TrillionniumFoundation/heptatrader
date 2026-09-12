#include "pre_trade_risk_engine.h"

#include <cmath>
#include <initializer_list>
#include <algorithm>
#include <limits>

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

bool ValidSubject(const PreTradeRiskSubject& subject) {
    return !subject.portfolioId.empty() && !subject.account.empty() &&
        !subject.venue.empty() && !subject.baseCurrency.empty() &&
        !subject.instruments.empty() && subject.instruments.count("") == 0;
}

bool SameSubject(const PreTradeRiskSubject& left,
                 const PreTradeRiskSubject& right) {
    return ValidSubject(left) && ValidSubject(right) &&
        left.portfolioId == right.portfolioId && left.account == right.account &&
        left.venue == right.venue && left.baseCurrency == right.baseCurrency &&
        left.instruments == right.instruments;
}

bool ValidInstrumentContract(const PreTradeRiskInstrumentContract& contract) {
    if (contract.specificationId.empty() || contract.specificationVersion == 0 ||
        contract.instrument.empty() || contract.quoteCurrency.empty() ||
        !std::isfinite(contract.multiplier) || contract.multiplier <= 0.0 ||
        contract.priceUnit != PreTradeRiskPriceUnit::QuoteCurrencyPerUnit)
        return false;
    switch (contract.kind) {
    case PreTradeRiskInstrumentKind::CashFx:
        return contract.quantityUnit == PreTradeRiskQuantityUnit::BaseCurrencyUnits &&
            contract.multiplier == 1.0;
    case PreTradeRiskInstrumentKind::Stock:
        return contract.quantityUnit == PreTradeRiskQuantityUnit::Shares &&
            contract.multiplier == 1.0;
    case PreTradeRiskInstrumentKind::Future:
    case PreTradeRiskInstrumentKind::Option:
        return contract.quantityUnit == PreTradeRiskQuantityUnit::Contracts;
    default:
        return false;
    }
}

bool SameInstrumentContract(const PreTradeRiskInstrumentContract& left,
                            const PreTradeRiskInstrumentContract& right) {
    return ValidInstrumentContract(left) && ValidInstrumentContract(right) &&
        left.specificationId == right.specificationId &&
        left.specificationVersion == right.specificationVersion &&
        left.instrument == right.instrument && left.kind == right.kind &&
        left.quantityUnit == right.quantityUnit && left.priceUnit == right.priceUnit &&
        left.multiplier == right.multiplier && left.quoteCurrency == right.quoteCurrency;
}

bool FreshEvidence(std::int64_t observedAtMs,
                   const PreTradeRiskSnapshotIdentity& identity,
                   std::int64_t maxAgeMs) {
    return observedAtMs > 0 && observedAtMs <= identity.evaluatedAtMs &&
        identity.evaluatedAtMs - observedAtMs <= maxAgeMs;
}

PreTradeRiskDecision ValidateOrderNotional(const PreTradeRiskConfig& cfg,
                                          const PreTradeRiskContext& ctx) {
    const PreTradeRiskOrderNotionalEvidence& evidence = ctx.orderNotionalEvidence;
    const PreTradeRiskSnapshotIdentity& identity = ctx.authoritativeSnapshot.identity;
    if (!evidence.present)
        return Reject("RISK_ORDER_NOTIONAL_UNAVAILABLE", "bound converted notional evidence is required");
    if (!SameSubject(evidence.subject, identity.subject))
        return Reject("RISK_ORDER_NOTIONAL_SUBJECT_MISMATCH", "converted notional belongs to another risk subject");
    if (evidence.connectionEpoch != identity.connectionEpoch ||
        !SameGeneration(evidence.generation, identity))
        return Reject("RISK_ORDER_NOTIONAL_GENERATION_MISMATCH", "converted notional belongs to another snapshot epoch/generation");
    if (!SameInstrumentContract(ctx.instrumentContract, evidence.contract) ||
        evidence.contract.instrument != ctx.symbol ||
        evidence.quantity != ctx.totalQuantity)
        return Reject("RISK_ORDER_NOTIONAL_UNIT_CONTRACT_INVALID", "converted notional must match the execution-owned instrument specification and quantity");

    const PreTradeRiskPriceEvidence& quote = evidence.quote;
    if (ctx.authorizedQuoteSourceId.empty() || quote.sourceId != ctx.authorizedQuoteSourceId ||
        quote.instrument != ctx.symbol || quote.currency != evidence.contract.quoteCurrency ||
        quote.connectionEpoch != identity.connectionEpoch ||
        !SameGeneration(quote.generation, identity) ||
        !std::isfinite(quote.price) || quote.price <= 0.0 ||
        quote.price != ctx.referencePrice)
        return Reject("RISK_ORDER_NOTIONAL_QUOTE_INVALID", "quote source, instrument, currency, generation and reference price must match");
    if (!FreshEvidence(quote.observedAtMs, identity, cfg.maxSnapshotAgeMs))
        return Reject("RISK_ORDER_NOTIONAL_QUOTE_STALE", "converted notional quote is stale or future dated");

    const PreTradeRiskFxEvidence& fx = evidence.fx;
    if (ctx.authorizedFxSourceId.empty() || fx.sourceId != ctx.authorizedFxSourceId ||
        fx.fromCurrency != quote.currency || fx.toCurrency != identity.subject.baseCurrency ||
        fx.connectionEpoch != identity.connectionEpoch ||
        !SameGeneration(fx.generation, identity) ||
        !std::isfinite(fx.rate) || fx.rate <= 0.0 ||
        (fx.fromCurrency == fx.toCurrency && fx.rate != 1.0))
        return Reject("RISK_ORDER_NOTIONAL_FX_INVALID", "explicit FX conversion source, currencies, rate and generation must match");
    if (!FreshEvidence(fx.observedAtMs, identity, cfg.maxSnapshotAgeMs))
        return Reject("RISK_ORDER_NOTIONAL_FX_STALE", "converted notional FX evidence is stale or future dated");

    if (ctx.orderType != "LMT" && ctx.orderType != "MKT")
        return Reject("RISK_ORDER_NOTIONAL_UNIT_CONTRACT_INVALID", "unsupported order price convention");
    // A limit above the observed reference must not understate possible spend.
    const double price = ctx.orderType == "LMT" ?
        std::max(ctx.limitPrice, quote.price) : quote.price;
    const double calculated = evidence.quantity * evidence.contract.multiplier * price * fx.rate;
    if (!std::isfinite(evidence.baseCurrencyNotional) || evidence.baseCurrencyNotional <= 0.0 ||
        !std::isfinite(calculated) || calculated <= 0.0)
        return Reject("RISK_ORDER_NOTIONAL_INVALID", "converted notional and validated unit arithmetic must be finite and positive");
    const double tolerance = std::numeric_limits<double>::epsilon() *
        std::max(calculated, evidence.baseCurrencyNotional) * 8.0;
    if (std::abs(calculated - evidence.baseCurrencyNotional) > tolerance)
        return Reject("RISK_ORDER_NOTIONAL_CONVERSION_MISMATCH", "converted amount does not match quantity, multiplier, quote and FX evidence");
    // Use the conservative value even within floating-point rounding tolerance.
    return Allow(std::max(calculated, evidence.baseCurrencyNotional));
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
    const bool needsNotional = !reducingExposure &&
        (LimitEnabled(cfg.maxOrderNotional) || needsExposure);
    const bool needsSnapshot = needsPortfolioSnapshot || needsNotional || cfg.maxSnapshotAgeMs > 0;
    const PreTradeRiskSnapshotIdentity& identity =
        ctx.authoritativeSnapshot.identity;

    if ((needsPortfolioSnapshot || needsNotional) && cfg.maxSnapshotAgeMs == 0) {
        return Reject("RISK_SNAPSHOT_FRESHNESS_POLICY_REQUIRED",
                      "portfolio and notional limits require a positive maxSnapshotAgeMs");
    }
    if (needsSnapshot) {
        if (!ValidSnapshotIdentity(identity)) {
            return Reject("RISK_SNAPSHOT_IDENTITY_REQUIRED",
                          "authoritative snapshot identity, completeness, epoch, generation and timestamps are required");
        }
        if (ctx.evaluatedAtMs <= 0 || identity.evaluatedAtMs != ctx.evaluatedAtMs) {
            return Reject("RISK_SNAPSHOT_EVALUATION_TIME_MISMATCH",
                          "snapshot evaluation must match the execution-owned clock for this decision");
        }
        if (!SameSubject(ctx.authorizedSubject, identity.subject) ||
            ctx.authorizedSubject.account != ctx.account ||
            ctx.authorizedSubject.venue != ctx.venue ||
            ctx.authorizedSubject.instruments.count(ctx.symbol) != 1) {
            return Reject("RISK_SNAPSHOT_SUBJECT_MISMATCH",
                          "snapshot must match the authorized portfolio, account, venue, base currency and full instrument set");
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
        if (!SameSubject(exposure.subject, identity.subject)) {
            return Reject("RISK_SNAPSHOT_SUBJECT_MISMATCH", "exposure belongs to another risk subject");
        }
        if (exposure.connectionEpoch != identity.connectionEpoch ||
            !SameGeneration(exposure.generation, identity)) {
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
        if (!SameSubject(pnl.subject, identity.subject)) {
            return Reject("RISK_SNAPSHOT_SUBJECT_MISMATCH", "PnL belongs to another risk subject");
        }
        if (pnl.connectionEpoch != identity.connectionEpoch ||
            !SameGeneration(pnl.generation, identity)) {
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
        if (!SameSubject(equity.subject, identity.subject)) {
            return Reject("RISK_SNAPSHOT_SUBJECT_MISMATCH", "equity belongs to another risk subject");
        }
        if (equity.connectionEpoch != identity.connectionEpoch ||
            !SameGeneration(equity.generation, identity)) {
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
    if (needsNotional) {
        PreTradeRiskDecision notional = ValidateOrderNotional(cfg, ctx);
        if (!notional.allow) {
            notional.snapshotConnectionEpoch = identity.connectionEpoch;
            notional.snapshotGeneration = identity.generation;
            return notional;
        }
        orderNotional = notional.orderNotional;
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
            if (!std::isfinite(totalPnl) || !std::isfinite(dailyLoss)) {
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


PreTradeRiskPortfolioAssembly PreTradeRiskEngine::AssemblePortfolioExposure(
    const PreTradeRiskSnapshotIdentity& identity,
    const std::vector<PreTradeRiskPortfolioAsset>& assets,
    const std::vector<PreTradeRiskPendingOrder>& pending,
    bool positionsComplete, bool ordersComplete,
    std::int64_t evaluatedAtMs, std::int64_t maxAgeMs) {
    PreTradeRiskPortfolioAssembly output;
    const auto fail = [&output](const char* reason) {
        output.complete = false;
        output.reasonCode = reason;
        // Do not publish a partial sum as an authoritative zero/underestimate.
        output.exposure = PreTradeRiskExposureSnapshot();
        return output;
    };
    if (!positionsComplete || !ordersComplete || !ValidSnapshotIdentity(identity) ||
        !ValidSubject(identity.subject) || maxAgeMs <= 0 || evaluatedAtMs != identity.evaluatedAtMs ||
        !FreshEvidence(identity.observedAtMs, identity, maxAgeMs))
        return fail("RISK_PORTFOLIO_BARRIER_INCOMPLETE");
    PreTradeRiskConfig valuationPolicy;
    valuationPolicy.enableOrderSubmission = true;
    valuationPolicy.maxOrderQuantity = std::numeric_limits<double>::max();
    valuationPolicy.maxOrderNotional = std::numeric_limits<double>::max();
    valuationPolicy.maxSnapshotAgeMs = maxAgeMs;
    valuationPolicy.maxDailyOrders = 1;
    // Valuation does not admit a mutation. The actual final order is evaluated
    // separately under its configured quantity, price, loss and rate policy.
    valuationPolicy.maxPriceDeviationBps = std::numeric_limits<double>::max();
    const auto value = [&](const PreTradeRiskContext& input) {
        const PreTradeRiskSnapshotIdentity& other = input.authoritativeSnapshot.identity;
        if (!SameSubject(input.authorizedSubject, identity.subject) ||
            !SameSubject(other.subject, identity.subject) ||
            other.connectionEpoch != identity.connectionEpoch || other.generation != identity.generation ||
            other.observedAtMs != identity.observedAtMs || input.evaluatedAtMs != evaluatedAtMs ||
            other.evaluatedAtMs != evaluatedAtMs || !input.paperAccount)
            return Reject("RISK_PORTFOLIO_IDENTITY_MISMATCH", "valuation subject/epoch/clock mismatch");
        PreTradeRiskContext context = input;
        context.todayOrderCount = 0; // a pure mark, not an order admission
        context.accountWhitelisted = true; // subject equality above is the valuation boundary
        return Evaluate(valuationPolicy, context);
    };
    long double gross = 0.0L, buy = 0.0L, sell = 0.0L;
    std::set<std::string> instruments, orders;
    for (const auto& asset : assets) {
        if (!std::isfinite(asset.signedQuantity) || asset.unitMark.totalQuantity != 1.0 ||
            asset.unitMark.orderType != "MKT" || !instruments.insert(asset.unitMark.symbol).second)
            return fail("RISK_PORTFOLIO_ASSET_INVALID");
        const PreTradeRiskDecision marked = value(asset.unitMark);
        if (!marked.allow) return fail(marked.reasonCode.c_str());
        gross += std::fabs(static_cast<long double>(asset.signedQuantity)) * marked.orderNotional;
    }
    if (instruments != identity.subject.instruments)
        return fail("RISK_PORTFOLIO_INSTRUMENT_COVERAGE");
    for (const auto& order : pending) {
        if (order.orderId.empty() || !orders.insert(order.orderId).second)
            return fail("RISK_PORTFOLIO_ORDER_IDENTITY");
        const PreTradeRiskDecision marked = value(order.valuation);
        if (!marked.allow) return fail(marked.reasonCode.c_str());
        if (order.valuation.action == "BUY") buy += marked.orderNotional;
        else sell += marked.orderNotional; // Evaluate already rejects an unknown side
    }
    const auto roundUp = [](long double amount) {
        double rounded = static_cast<double>(amount);
        if (static_cast<long double>(rounded) < amount)
            rounded = std::nextafter(rounded, std::numeric_limits<double>::infinity());
        return rounded;
    };
    if (!std::isfinite(gross) || !std::isfinite(buy) || !std::isfinite(sell) ||
        !std::isfinite(roundUp(gross)) || !std::isfinite(roundUp(buy)) || !std::isfinite(roundUp(sell)))
        return fail("RISK_PORTFOLIO_NOTIONAL_OVERFLOW");
    output.exposure.subject = identity.subject;
    output.exposure.present = true;
    output.exposure.connectionEpoch = identity.connectionEpoch;
    output.exposure.generation = identity.generation;
    output.exposure.currentGrossNotional = roundUp(gross);
    output.exposure.pendingBuyNotional = roundUp(buy);
    output.exposure.pendingSellNotional = roundUp(sell);
    output.complete = true;
    output.reasonCode = "RISK_OK";
    return output;
}
