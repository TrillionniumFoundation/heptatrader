#include "risk/pre_trade_risk_engine.h"

#include <cmath>
#include <algorithm>
#include <cstdlib>
#include <iostream>
#include <functional>
#include <vector>
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
    ctx.authorizedSubject.portfolioId = "sim-portfolio";
    ctx.authorizedSubject.account = ctx.account;
    ctx.authorizedSubject.venue = ctx.venue;
    ctx.authorizedSubject.baseCurrency = "USD";
    ctx.authorizedSubject.instruments.insert(ctx.symbol);
    ctx.instrumentContract.specificationId = "sim-eurusd-unit-contract";
    ctx.instrumentContract.specificationVersion = 1;
    ctx.instrumentContract.instrument = ctx.symbol;
    ctx.instrumentContract.kind = PreTradeRiskInstrumentKind::CashFx;
    ctx.instrumentContract.quantityUnit = PreTradeRiskQuantityUnit::BaseCurrencyUnits;
    ctx.instrumentContract.priceUnit = PreTradeRiskPriceUnit::QuoteCurrencyPerUnit;
    ctx.instrumentContract.multiplier = 1.0;
    ctx.instrumentContract.quoteCurrency = "USD";
    ctx.authorizedQuoteSourceId = "sim-quote-feed";
    ctx.authorizedFxSourceId = "sim-fx-feed";
    return ctx;
}

void BindZeroSnapshot(PreTradeRiskContext& ctx, std::uint64_t generation = 7) {
    ctx.evaluatedAtMs = 5000;
    ctx.authoritativeSnapshot.identity.subject = ctx.authorizedSubject;
    ctx.authoritativeSnapshot.identity.present = true;
    ctx.authoritativeSnapshot.identity.complete = true;
    ctx.authoritativeSnapshot.identity.connectionEpoch = 3;
    ctx.authoritativeSnapshot.identity.generation = generation;
    ctx.authoritativeSnapshot.identity.observedAtMs = 4000;
    ctx.authoritativeSnapshot.identity.evaluatedAtMs = 5000;

    ctx.authoritativeSnapshot.exposure.subject = ctx.authorizedSubject;
    ctx.authoritativeSnapshot.exposure.connectionEpoch = 3;
    ctx.authoritativeSnapshot.exposure.present = true;
    ctx.authoritativeSnapshot.exposure.generation = generation;
    ctx.authoritativeSnapshot.pnl.subject = ctx.authorizedSubject;
    ctx.authoritativeSnapshot.pnl.connectionEpoch = 3;
    ctx.authoritativeSnapshot.pnl.present = true;
    ctx.authoritativeSnapshot.pnl.generation = generation;
    ctx.authoritativeSnapshot.equity.subject = ctx.authorizedSubject;
    ctx.authoritativeSnapshot.equity.connectionEpoch = 3;
    ctx.authoritativeSnapshot.equity.present = true;
    ctx.authoritativeSnapshot.equity.generation = generation;

    PreTradeRiskOrderNotionalEvidence& evidence = ctx.orderNotionalEvidence;
    evidence.present = true;
    evidence.subject = ctx.authorizedSubject;
    evidence.connectionEpoch = 3;
    evidence.generation = generation;
    evidence.contract = ctx.instrumentContract;
    evidence.quantity = ctx.totalQuantity;
    evidence.quote.sourceId = ctx.authorizedQuoteSourceId;
    evidence.quote.instrument = ctx.symbol;
    evidence.quote.currency = ctx.instrumentContract.quoteCurrency;
    evidence.quote.connectionEpoch = 3;
    evidence.quote.generation = generation;
    evidence.quote.observedAtMs = 4000;
    evidence.quote.price = ctx.referencePrice;
    evidence.fx.sourceId = ctx.authorizedFxSourceId;
    evidence.fx.fromCurrency = ctx.instrumentContract.quoteCurrency;
    evidence.fx.toCurrency = ctx.authorizedSubject.baseCurrency;
    evidence.fx.connectionEpoch = 3;
    evidence.fx.generation = generation;
    evidence.fx.observedAtMs = 4000;
    evidence.fx.rate = 1.0;
    evidence.baseCurrencyNotional = ctx.totalQuantity * ctx.instrumentContract.multiplier *
        std::max(ctx.referencePrice, ctx.limitPrice);
}

void TestSubjectIsolation() {
    PreTradeRiskConfig cfg = BaseConfig();
    cfg.maxWorstCaseGrossNotional = 1000.0;
    cfg.maxDailyLoss = 100.0;
    cfg.maxDrawdown = 100.0;
    cfg.maxSnapshotAgeMs = 1000;
    const std::vector<std::function<void(PreTradeRiskSubject&)>> crossSubject = {
        [](PreTradeRiskSubject& s) { s.account = "other-account"; },
        [](PreTradeRiskSubject& s) { s.venue = "other-venue"; },
        [](PreTradeRiskSubject& s) { s.baseCurrency = "EUR"; },
        [](PreTradeRiskSubject& s) { s.portfolioId = "other-portfolio"; },
        [](PreTradeRiskSubject& s) { s.instruments.insert("other-contract"); },
        [](PreTradeRiskSubject& s) { s.instruments.clear(); },
        [](PreTradeRiskSubject& s) { s.portfolioId.clear(); },
        [](PreTradeRiskSubject& s) { s.instruments.insert(""); }
    };
    // Same numbers, timestamps and generations cannot make a different
    // subject authoritative. Exercise the top identity and each required section.
    for (const auto& mutate : crossSubject) {
        for (int section = 0; section < 4; ++section) {
            PreTradeRiskContext ctx = BaseContext();
            BindZeroSnapshot(ctx);
            PreTradeRiskSubject* subjects[] = {
                &ctx.authoritativeSnapshot.identity.subject,
                &ctx.authoritativeSnapshot.exposure.subject,
                &ctx.authoritativeSnapshot.pnl.subject,
                &ctx.authoritativeSnapshot.equity.subject
            };
            mutate(*subjects[section]);
            Require(PreTradeRiskEngine::Evaluate(cfg, ctx).reasonCode ==
                        "RISK_SNAPSHOT_SUBJECT_MISMATCH",
                    "cross-subject or mixed-section snapshot must fail");
        }
    }
    for (int field = 0; field < 3; ++field) {
        PreTradeRiskContext ctx = BaseContext();
        BindZeroSnapshot(ctx);
        if (field == 0) ctx.account = "different-order-account";
        if (field == 1) ctx.venue = "different-order-venue";
        if (field == 2) ctx.symbol = "EUR.GBP";
        Require(PreTradeRiskEngine::Evaluate(cfg, ctx).reasonCode ==
                    "RISK_SNAPSHOT_SUBJECT_MISMATCH",
                "order context must bind authorized subject and instrument set");
    }
    for (int section = 0; section < 3; ++section) {
        PreTradeRiskContext ctx = BaseContext();
        BindZeroSnapshot(ctx);
        std::uint64_t* epochs[] = {
            &ctx.authoritativeSnapshot.exposure.connectionEpoch,
            &ctx.authoritativeSnapshot.pnl.connectionEpoch,
            &ctx.authoritativeSnapshot.equity.connectionEpoch
        };
        ++*epochs[section];
        Require(PreTradeRiskEngine::Evaluate(cfg, ctx).reasonCode ==
                    "RISK_SNAPSHOT_GENERATION_MISMATCH",
                "same generation from a different section epoch must fail");
    }
}

void TestNotionalEvidence() {
    PreTradeRiskConfig cfg = BaseConfig();
    cfg.maxOrderNotional = 100.0;
    PreTradeRiskContext ctx = BaseContext();
    BindZeroSnapshot(ctx);
    Require(PreTradeRiskEngine::Evaluate(cfg, ctx).reasonCode ==
                "RISK_SNAPSHOT_FRESHNESS_POLICY_REQUIRED",
            "order-only notional policy requires snapshot freshness");
    cfg.maxSnapshotAgeMs = 1000;
    struct HostileCase {
        const char* name;
        std::function<void(PreTradeRiskContext&)> mutate;
        const char* reason;
    };
    const std::vector<HostileCase> cases = {
        {"generic quantity-price fallback removed", [](PreTradeRiskContext& c) {
            c.orderNotionalEvidence.present = false;
        }, "RISK_ORDER_NOTIONAL_UNAVAILABLE"},
        {"missing evaluation clock", [](PreTradeRiskContext& c) {
            c.evaluatedAtMs = 0;
        }, "RISK_SNAPSHOT_EVALUATION_TIME_MISMATCH"},
        {"old snapshot cannot supply its own evaluation clock", [](PreTradeRiskContext& c) {
            c.evaluatedAtMs = 10000;
        }, "RISK_SNAPSHOT_EVALUATION_TIME_MISMATCH"},
        {"legacy scalar cannot bypass binding", [](PreTradeRiskContext& c) {
            c.orderNotionalEvidence.present = false;


        }, "RISK_ORDER_NOTIONAL_UNAVAILABLE"},
        {"converted account mismatch", [](PreTradeRiskContext& c) {
            c.orderNotionalEvidence.subject.account = "other";
        }, "RISK_ORDER_NOTIONAL_SUBJECT_MISMATCH"},
        {"converted epoch mismatch", [](PreTradeRiskContext& c) {
            ++c.orderNotionalEvidence.connectionEpoch;
        }, "RISK_ORDER_NOTIONAL_GENERATION_MISMATCH"},
        {"converted generation mismatch", [](PreTradeRiskContext& c) {
            ++c.orderNotionalEvidence.generation;
        }, "RISK_ORDER_NOTIONAL_GENERATION_MISMATCH"},
        {"omitted multiplier", [](PreTradeRiskContext& c) {
            c.orderNotionalEvidence.contract.multiplier = 0.0;
        }, "RISK_ORDER_NOTIONAL_UNIT_CONTRACT_INVALID"},
        {"omitted trusted specification", [](PreTradeRiskContext& c) {
            c.instrumentContract.specificationId.clear();
        }, "RISK_ORDER_NOTIONAL_UNIT_CONTRACT_INVALID"},
        {"different specification version", [](PreTradeRiskContext& c) {
            ++c.orderNotionalEvidence.contract.specificationVersion;
        }, "RISK_ORDER_NOTIONAL_UNIT_CONTRACT_INVALID"},
        {"quantity unit mismatch", [](PreTradeRiskContext& c) {
            c.orderNotionalEvidence.contract.quantityUnit = PreTradeRiskQuantityUnit::Contracts;
        }, "RISK_ORDER_NOTIONAL_UNIT_CONTRACT_INVALID"},
        {"unsupported units even when both agree", [](PreTradeRiskContext& c) {
            c.instrumentContract.quantityUnit = PreTradeRiskQuantityUnit::Contracts;
            c.orderNotionalEvidence.contract = c.instrumentContract;
        }, "RISK_ORDER_NOTIONAL_UNIT_CONTRACT_INVALID"},
        {"omitted price unit", [](PreTradeRiskContext& c) {
            c.orderNotionalEvidence.contract.priceUnit = PreTradeRiskPriceUnit::Unspecified;
        }, "RISK_ORDER_NOTIONAL_UNIT_CONTRACT_INVALID"},
        {"wrong contract identity", [](PreTradeRiskContext& c) {
            c.orderNotionalEvidence.contract.instrument = "EUR.GBP";
        }, "RISK_ORDER_NOTIONAL_UNIT_CONTRACT_INVALID"},
        {"reused conversion for different quantity", [](PreTradeRiskContext& c) {
            c.orderNotionalEvidence.quantity = 0.5;
        }, "RISK_ORDER_NOTIONAL_UNIT_CONTRACT_INVALID"},
        {"mismatched quote currency", [](PreTradeRiskContext& c) {
            c.orderNotionalEvidence.quote.currency = "EUR";
        }, "RISK_ORDER_NOTIONAL_QUOTE_INVALID"},
        {"mismatched quote instrument", [](PreTradeRiskContext& c) {
            c.orderNotionalEvidence.quote.instrument = "EUR.GBP";
        }, "RISK_ORDER_NOTIONAL_QUOTE_INVALID"},
        {"mismatched quote source", [](PreTradeRiskContext& c) {
            c.orderNotionalEvidence.quote.sourceId = "untrusted";
        }, "RISK_ORDER_NOTIONAL_QUOTE_INVALID"},
        {"old quote generation", [](PreTradeRiskContext& c) {
            --c.orderNotionalEvidence.quote.generation;
        }, "RISK_ORDER_NOTIONAL_QUOTE_INVALID"},
        {"quote from previous connection epoch", [](PreTradeRiskContext& c) {
            --c.orderNotionalEvidence.quote.connectionEpoch;
        }, "RISK_ORDER_NOTIONAL_QUOTE_INVALID"},
        {"changed reference price", [](PreTradeRiskContext& c) {
            c.orderNotionalEvidence.quote.price = 99.0;
        }, "RISK_ORDER_NOTIONAL_QUOTE_INVALID"},
        {"stale quote", [](PreTradeRiskContext& c) {
            c.orderNotionalEvidence.quote.observedAtMs = 3999;
        }, "RISK_ORDER_NOTIONAL_QUOTE_STALE"},
        {"future quote", [](PreTradeRiskContext& c) {
            c.orderNotionalEvidence.quote.observedAtMs = 5001;
        }, "RISK_ORDER_NOTIONAL_QUOTE_STALE"},
        {"omitted conversion", [](PreTradeRiskContext& c) {
            c.orderNotionalEvidence.fx = PreTradeRiskFxEvidence{};
        }, "RISK_ORDER_NOTIONAL_FX_INVALID"},
        {"wrong FX source currency", [](PreTradeRiskContext& c) {
            c.orderNotionalEvidence.fx.fromCurrency = "EUR";
        }, "RISK_ORDER_NOTIONAL_FX_INVALID"},
        {"wrong FX base currency", [](PreTradeRiskContext& c) {
            c.orderNotionalEvidence.fx.toCurrency = "EUR";
        }, "RISK_ORDER_NOTIONAL_FX_INVALID"},
        {"mismatched FX source", [](PreTradeRiskContext& c) {
            c.orderNotionalEvidence.fx.sourceId = "untrusted";
        }, "RISK_ORDER_NOTIONAL_FX_INVALID"},
        {"old FX generation", [](PreTradeRiskContext& c) {
            --c.orderNotionalEvidence.fx.generation;
        }, "RISK_ORDER_NOTIONAL_FX_INVALID"},
        {"FX from previous connection epoch", [](PreTradeRiskContext& c) {
            --c.orderNotionalEvidence.fx.connectionEpoch;
        }, "RISK_ORDER_NOTIONAL_FX_INVALID"},
        {"nonidentity same-currency conversion", [](PreTradeRiskContext& c) {
            c.orderNotionalEvidence.fx.rate = 0.01;
        }, "RISK_ORDER_NOTIONAL_FX_INVALID"},
        {"nonfinite FX", [](PreTradeRiskContext& c) {
            c.orderNotionalEvidence.fx.rate = std::numeric_limits<double>::infinity();
        }, "RISK_ORDER_NOTIONAL_FX_INVALID"},
        {"stale FX", [](PreTradeRiskContext& c) {
            c.orderNotionalEvidence.fx.observedAtMs = 3999;
        }, "RISK_ORDER_NOTIONAL_FX_STALE"},
        {"future FX", [](PreTradeRiskContext& c) {
            c.orderNotionalEvidence.fx.observedAtMs = 5001;
        }, "RISK_ORDER_NOTIONAL_FX_STALE"},
        {"understated converted amount", [](PreTradeRiskContext& c) {
            c.orderNotionalEvidence.baseCurrencyNotional = 0.01;
        }, "RISK_ORDER_NOTIONAL_CONVERSION_MISMATCH"},
        {"NaN converted amount", [](PreTradeRiskContext& c) {
            c.orderNotionalEvidence.baseCurrencyNotional = std::numeric_limits<double>::quiet_NaN();
        }, "RISK_ORDER_NOTIONAL_INVALID"},
        {"limit price must be included in possible spend", [](PreTradeRiskContext& c) {
            c.limitPrice = 100.5;
        }, "RISK_ORDER_NOTIONAL_CONVERSION_MISMATCH"}
    };
    for (const HostileCase& test : cases) {
        ctx = BaseContext();
        BindZeroSnapshot(ctx);
        test.mutate(ctx);
        const PreTradeRiskDecision result = PreTradeRiskEngine::Evaluate(cfg, ctx);
        if (result.allow || result.reasonCode != test.reason) {
            std::cerr << "unexpected reason for " << test.name << ": " << result.reasonCode << '\n';
            Require(false, test.name);
        }
    }

    for (PreTradeRiskInstrumentKind kind : {PreTradeRiskInstrumentKind::Future,
                                           PreTradeRiskInstrumentKind::Option}) {
        ctx = BaseContext();
        ctx.symbol = kind == PreTradeRiskInstrumentKind::Future ? "ES-202612-CME" : "SPY-20261218-C-600";
        ctx.authorizedSubject.instruments = {ctx.symbol};
        ctx.instrumentContract.instrument = ctx.symbol;
        ctx.instrumentContract.specificationId = ctx.symbol + "-spec";
        ctx.instrumentContract.kind = kind;
        ctx.instrumentContract.quantityUnit = PreTradeRiskQuantityUnit::Contracts;
        ctx.instrumentContract.multiplier = kind == PreTradeRiskInstrumentKind::Future ? 50.0 : 100.0;
        ctx.limitPrice = ctx.referencePrice = kind == PreTradeRiskInstrumentKind::Future ? 5000.0 : 5.0;
        ctx.totalQuantity = 2.0;
        BindZeroSnapshot(ctx);
        cfg.maxOrderNotional = ctx.orderNotionalEvidence.baseCurrencyNotional;
        Require(PreTradeRiskEngine::Evaluate(cfg, ctx).allow,
                "explicit futures multiplier or option premium/multiplier passes at inclusive limit");
        cfg.maxOrderNotional -= 1.0;
        Require(PreTradeRiskEngine::Evaluate(cfg, ctx).reasonCode == "RISK_ORDER_NOTIONAL_LIMIT",
                "futures and option multiplier must count against order limit");
        ctx.orderNotionalEvidence.baseCurrencyNotional = ctx.totalQuantity * ctx.referencePrice;
        Require(PreTradeRiskEngine::Evaluate(cfg, ctx).reasonCode == "RISK_ORDER_NOTIONAL_CONVERSION_MISMATCH",
                "dropping futures or option multiplier must fail");
    }

    ctx = BaseContext();
    ctx.authorizedSubject.baseCurrency = "EUR";
    BindZeroSnapshot(ctx);
    ctx.orderNotionalEvidence.fx.rate = 0.9;
    ctx.orderNotionalEvidence.baseCurrencyNotional = 90.0;
    cfg.maxOrderNotional = 90.0;
    Require(PreTradeRiskEngine::Evaluate(cfg, ctx).allow,
            "fresh explicit USD-to-EUR conversion is accepted at inclusive limit");
    ctx.orderNotionalEvidence.baseCurrencyNotional = 100.0;
    Require(PreTradeRiskEngine::Evaluate(cfg, ctx).reasonCode == "RISK_ORDER_NOTIONAL_CONVERSION_MISMATCH",
            "quote-currency amount cannot masquerade as account-base amount");

    ctx = BaseContext();
    ctx.symbol = "AAPL-NASDAQ-USD";
    ctx.authorizedSubject.instruments = {ctx.symbol};
    ctx.instrumentContract.instrument = ctx.symbol;
    ctx.instrumentContract.specificationId = "aapl-shares-v1";
    ctx.instrumentContract.kind = PreTradeRiskInstrumentKind::Stock;
    ctx.instrumentContract.quantityUnit = PreTradeRiskQuantityUnit::Shares;
    ctx.orderType = "MKT";
    ctx.limitPrice = 0.0;
    BindZeroSnapshot(ctx);
    cfg.maxOrderNotional = 100.0;
    Require(PreTradeRiskEngine::Evaluate(cfg, ctx).allow,
            "explicit stock-share and market-reference convention must pass");
    ctx.instrumentContract.multiplier = 100.0;
    ctx.orderNotionalEvidence.contract = ctx.instrumentContract;
    Require(PreTradeRiskEngine::Evaluate(cfg, ctx).reasonCode == "RISK_ORDER_NOTIONAL_UNIT_CONTRACT_INVALID",
            "unsupported stock lot multiplier cannot replace share unit convention");
}

void TestNumericAggregation() {
    PreTradeRiskConfig cfg = BaseConfig();
    cfg.maxDailyLoss = 10.0;
    cfg.maxSnapshotAgeMs = 1000;
    for (double sign : {-1.0, 1.0}) {
        PreTradeRiskContext ctx = BaseContext();
        BindZeroSnapshot(ctx);
        ctx.authoritativeSnapshot.pnl.realizedPnl = sign * std::numeric_limits<double>::max();
        ctx.authoritativeSnapshot.pnl.unrealizedPnl = sign * std::numeric_limits<double>::max();
        Require(PreTradeRiskEngine::Evaluate(cfg, ctx).reasonCode == "RISK_DAILY_LOSS_INVALID",
                "PnL aggregation overflow must not become an observed zero loss");
    }
}
}

int main() {
    TestSubjectIsolation();
    TestNotionalEvidence();
    TestNumericAggregation();
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
        cfg.maxSnapshotAgeMs = 1000;
        PreTradeRiskContext ctx = BaseContext();
        BindZeroSnapshot(ctx);
        PreTradeRiskDecision d = PreTradeRiskEngine::Evaluate(cfg, ctx);
        Require(d.allow && d.orderNotional == 100.0,
                "inclusive explicitly converted order-notional boundary must pass");
        ctx.limitPrice = 100.01;
        ctx.orderNotionalEvidence.baseCurrencyNotional = 100.01;
        d = PreTradeRiskEngine::Evaluate(cfg, ctx);
        Require(!d.allow && d.reasonCode == "RISK_ORDER_NOTIONAL_LIMIT",
                "above order-notional boundary must fail");
        ctx.orderNotionalEvidence.baseCurrencyNotional = 0.0;
        d = PreTradeRiskEngine::Evaluate(cfg, ctx);
        Require(!d.allow && d.reasonCode == "RISK_ORDER_NOTIONAL_INVALID",
                "present zero notional must not mean missing or safe");
    }
    {
        PreTradeRiskConfig cfg = BaseConfig();
        cfg.maxWorstCaseGrossNotional = 1000.0;
        cfg.maxSnapshotAgeMs = 1000;
        PreTradeRiskContext ctx = BaseContext();


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
    {
        // Mixed native units are valued independently in account currency. A
        // short option contributes gross premium notional, never negative risk.
        PreTradeRiskContext seed = BaseContext();
        seed.authorizedSubject.instruments = {"EUR.USD", "STOCK.EUR", "FUT.202612", "OPT.202612.C.100"};
        std::vector<PreTradeRiskPortfolioAsset> assets;
        for (const auto& name : seed.authorizedSubject.instruments) {
            PreTradeRiskPortfolioAsset asset;
            asset.unitMark = seed;
            auto& c = asset.unitMark;
            c.symbol = name; c.orderType = "MKT"; c.totalQuantity = 1;
            c.instrumentContract.instrument = name;
            c.instrumentContract.specificationId = name+"-v1";
            c.referencePrice = 100; asset.signedQuantity = 10;
            if (name == "STOCK.EUR") {
                c.instrumentContract.kind = PreTradeRiskInstrumentKind::Stock;
                c.instrumentContract.quantityUnit = PreTradeRiskQuantityUnit::Shares;
                c.instrumentContract.quoteCurrency = "EUR";
                c.referencePrice = 50; asset.signedQuantity = 3;
            } else if (name == "FUT.202612") {
                c.instrumentContract.kind = PreTradeRiskInstrumentKind::Future;
                c.instrumentContract.quantityUnit = PreTradeRiskQuantityUnit::Contracts;
                c.instrumentContract.multiplier = 50;
                c.referencePrice = 200; asset.signedQuantity = 2;
            } else if (name == "OPT.202612.C.100") {
                c.instrumentContract.kind = PreTradeRiskInstrumentKind::Option;
                c.instrumentContract.quantityUnit = PreTradeRiskQuantityUnit::Contracts;
                c.instrumentContract.multiplier = 100;
                c.referencePrice = 3; asset.signedQuantity = -4;
            }
            c.limitPrice = c.referencePrice;
            BindZeroSnapshot(c);
            c.orderNotionalEvidence.fx.rate = name == "STOCK.EUR" ? 1.1 : 1.0;
            c.orderNotionalEvidence.baseCurrencyNotional = c.referencePrice*c.instrumentContract.multiplier*c.orderNotionalEvidence.fx.rate;
            assets.push_back(asset);
        }
        const auto identity = assets[0].unitMark.authoritativeSnapshot.identity;
        std::vector<PreTradeRiskPendingOrder> pending;
        for (const auto& asset : assets) {
            if (asset.unitMark.symbol == "FUT.202612" || asset.unitMark.symbol == "STOCK.EUR") {
                PreTradeRiskPendingOrder order;
                order.orderId = asset.unitMark.symbol+"-pending";
                order.valuation = asset.unitMark;
                auto& c = order.valuation;
                c.orderType = "LMT";
                c.action = c.symbol == "FUT.202612" ? "BUY" : "SELL";
                c.totalQuantity = c.symbol == "FUT.202612" ? 1 : 2;
                c.limitPrice = c.symbol == "FUT.202612" ? 220 : 50;
                c.orderNotionalEvidence.quantity = c.totalQuantity;
                c.orderNotionalEvidence.baseCurrencyNotional = c.totalQuantity*c.limitPrice*c.instrumentContract.multiplier*c.orderNotionalEvidence.fx.rate;
                pending.push_back(order);
            }
        }
        const auto assemble = [&]() {return PreTradeRiskEngine::AssemblePortfolioExposure(identity,assets,pending,true,true,5000,1000);};
        auto result = assemble();
        Require(result.complete, "mixed-asset assembly must validate bound marks");
        Require(std::fabs(result.exposure.currentGrossNotional-22365) < 1e-8, "gross must use multiplier and FX conversion");
        Require(result.exposure.pendingBuyNotional == 11000 && std::fabs(result.exposure.pendingSellNotional-110) < 1e-8,
                "pending limit orders must retain conservative converted exposure");
        auto saved = assets;
        assets.pop_back(); result=assemble();
        Require(!result.complete && !result.exposure.present, "missing even a zero asset invalidates entire portfolio");
        assets=saved; assets[0].unitMark.orderNotionalEvidence.fx.generation++;
        Require(!assemble().complete, "mixed generation must not aggregate");
        assets=saved; assets[0].unitMark.authorizedSubject.account="OTHER";
        Require(!assemble().complete, "cross-account marks must not aggregate");
        assets=saved; pending.push_back(pending.front());
        Require(!assemble().complete, "duplicate order must not be counted or accepted");
        pending.pop_back();assets[0].signedQuantity=std::numeric_limits<double>::max();
        Require(!assemble().complete, "overflow must not publish partial exposure");
        assets=saved;
        Require(!PreTradeRiskEngine::AssemblePortfolioExposure(identity,assets,pending,true,false,5000,1000).complete,
                "incomplete order barrier must not mean no pending orders");
    }
    std::cout << "pre_trade_risk_engine_tests: PASS" << std::endl;
    return 0;
}
