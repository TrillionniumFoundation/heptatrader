#include "../HeptaTrade/risk/pre_trade_risk_engine.h"

#include <cmath>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <string>

namespace {

void Require(bool condition, const char* expression, int line)
{
    if (condition) return;
    std::cerr << "requirement failed at line " << line << ": "
              << expression << "\n";
    std::abort();
}

#define REQUIRE(expression) \
    Require(static_cast<bool>(expression), #expression, __LINE__)

PreTradeRiskConfig BaseConfig()
{
    PreTradeRiskConfig config;
    config.enableOrderSubmission = true;
    config.maxOrderQuantity = 100.0;
    config.maxDailyOrders = 10;
    config.maxPriceDeviationBps = 50.0;
    return config;
}

PreTradeRiskContext BaseContext()
{
    PreTradeRiskContext context;
    context.venue = "IB";
    context.account = "DU123456";
    context.symbol = "EURUSD";
    context.action = "BUY";
    context.orderType = "LMT";
    context.totalQuantity = 1.0;
    context.limitPrice = 1.1000;
    context.referencePrice = 1.1000;
    context.accountWhitelisted = true;
    context.paperAccount = true;
    return context;
}

void RequireReject(const PreTradeRiskConfig& config,
                   const PreTradeRiskContext& context,
                   const char* reason)
{
    const PreTradeRiskDecision decision =
        PreTradeRiskEngine::Evaluate(config, context);
    REQUIRE(!decision.allow);
    REQUIRE(decision.reasonCode == reason);
}

void TestValidOrderIsAllowed()
{
    const PreTradeRiskDecision decision =
        PreTradeRiskEngine::Evaluate(BaseConfig(), BaseContext());
    REQUIRE(decision.allow);
    REQUIRE(decision.reasonCode == "RISK_OK");
}

void TestNonFiniteInputsFailClosed()
{
    const double nan = std::numeric_limits<double>::quiet_NaN();
    const double infinity = std::numeric_limits<double>::infinity();

    PreTradeRiskConfig config = BaseConfig();
    PreTradeRiskContext context = BaseContext();
    context.totalQuantity = nan;
    RequireReject(config, context, "RISK_QTY_OUT_OF_RANGE");

    context = BaseContext();
    context.totalQuantity = infinity;
    RequireReject(config, context, "RISK_QTY_OUT_OF_RANGE");

    context = BaseContext();
    context.limitPrice = nan;
    RequireReject(config, context, "RISK_PRICE_NOT_FINITE");

    context = BaseContext();
    context.referencePrice = infinity;
    RequireReject(config, context, "RISK_PRICE_NOT_FINITE");

    config = BaseConfig();
    config.maxOrderQuantity = nan;
    RequireReject(config, BaseContext(), "RISK_CONFIG_INVALID");

    config = BaseConfig();
    config.maxPriceDeviationBps = infinity;
    RequireReject(config, BaseContext(), "RISK_CONFIG_INVALID");
}

void TestActionAndOrderTypeAreExplicit()
{
    PreTradeRiskContext context = BaseContext();
    context.action = "HOLD";
    RequireReject(BaseConfig(), context, "RISK_ACTION_INVALID");

    context = BaseContext();
    context.orderType = "STOP";
    RequireReject(BaseConfig(), context, "RISK_ORDER_TYPE_INVALID");
}

void TestLimitOrdersRequireUsablePrices()
{
    PreTradeRiskContext context = BaseContext();
    context.limitPrice = 0.0;
    RequireReject(BaseConfig(), context, "RISK_LIMIT_PRICE_INVALID");

    context = BaseContext();
    context.referencePrice = 0.0;
    RequireReject(BaseConfig(), context, "RISK_REFERENCE_PRICE_INVALID");

    context = BaseContext();
    context.limitPrice = 1.20;
    context.referencePrice = 1.10;
    RequireReject(BaseConfig(), context,
                  "RISK_PRICE_DEVIATION_TOO_LARGE");
}

void TestContextAndLiveGatesFailClosed()
{
    PreTradeRiskContext context = BaseContext();
    context.todayOrderCount = -1;
    RequireReject(BaseConfig(), context, "RISK_CONTEXT_INVALID");

    context = BaseContext();
    context.todayOrderCount = 10;
    RequireReject(BaseConfig(), context, "RISK_DAILY_ORDER_LIMIT");

    context = BaseContext();
    context.accountWhitelisted = false;
    RequireReject(BaseConfig(), context, "RISK_ACCOUNT_NOT_WHITELISTED");

    context = BaseContext();
    context.paperAccount = false;
    RequireReject(BaseConfig(), context, "RISK_LIVE_NOT_AUTHORIZED");

    PreTradeRiskConfig config = BaseConfig();
    config.allowLiveTrading = true;
    context = BaseContext();
    context.paperAccount = false;
    RequireReject(config, context, "RISK_LIVE_KILL_SWITCH_ON");
}

void TestFlattenOnlyNeverCrossesZero()
{
    PreTradeRiskConfig config = BaseConfig();
    config.flattenOnly = true;

    PreTradeRiskContext context = BaseContext();
    context.positionKnown = true;
    context.netPosition = 10.0;
    context.action = "SELL";
    context.totalQuantity = 4.0;
    REQUIRE(PreTradeRiskEngine::Evaluate(config, context).allow);

    context.totalQuantity = 10.0;
    REQUIRE(PreTradeRiskEngine::Evaluate(config, context).allow);

    context.totalQuantity = 10.0001;
    RequireReject(config, context, "RISK_FLATTEN_ONLY_BLOCK");

    context = BaseContext();
    context.positionKnown = true;
    context.netPosition = -10.0;
    context.action = "BUY";
    context.totalQuantity = 10.0;
    REQUIRE(PreTradeRiskEngine::Evaluate(config, context).allow);

    context.totalQuantity = 11.0;
    RequireReject(config, context, "RISK_FLATTEN_ONLY_BLOCK");

    context = BaseContext();
    context.positionKnown = true;
    context.netPosition = 0.0;
    context.action = "SELL";
    RequireReject(config, context, "RISK_FLATTEN_ONLY_BLOCK");

    context = BaseContext();
    context.positionKnown = false;
    RequireReject(config, context,
                  "RISK_FLATTEN_ONLY_POSITION_UNKNOWN");

    context = BaseContext();
    context.positionKnown = true;
    context.netPosition = std::numeric_limits<double>::quiet_NaN();
    RequireReject(config, context, "RISK_POSITION_NOT_FINITE");
}

void TestFlattenOnlyProperty()
{
    PreTradeRiskConfig config = BaseConfig();
    config.flattenOnly = true;
    config.maxOrderQuantity = 1000.0;

    for (int positionTicks = -80; positionTicks <= 80; ++positionTicks)
    {
        if (positionTicks == 0) continue;
        const double position = positionTicks / 4.0;
        for (int quantityTicks = 1; quantityTicks <= 100; ++quantityTicks)
        {
            const double quantity = quantityTicks / 4.0;
            for (int side = 0; side < 2; ++side)
            {
                PreTradeRiskContext context = BaseContext();
                context.positionKnown = true;
                context.netPosition = position;
                context.action = side == 0 ? "BUY" : "SELL";
                context.totalQuantity = quantity;
                const PreTradeRiskDecision decision =
                    PreTradeRiskEngine::Evaluate(config, context);
                if (!decision.allow) continue;

                const double signedQuantity =
                    context.action == "BUY" ? quantity : -quantity;
                const double after = position + signedQuantity;
                REQUIRE(std::abs(after) < std::abs(position) || after == 0.0);
                REQUIRE(after == 0.0 ||
                        (after > 0.0) == (position > 0.0));
            }
        }
    }
}

} // namespace

int main()
{
    TestValidOrderIsAllowed();
    TestNonFiniteInputsFailClosed();
    TestActionAndOrderTypeAreExplicit();
    TestLimitOrdersRequireUsablePrices();
    TestContextAndLiveGatesFailClosed();
    TestFlattenOnlyNeverCrossesZero();
    TestFlattenOnlyProperty();
    return 0;
}
