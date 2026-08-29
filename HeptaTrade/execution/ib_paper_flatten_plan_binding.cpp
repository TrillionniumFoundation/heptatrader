#include "ib_paper_flatten_plan_binding.h"

#include <iomanip>
#include <sstream>

namespace
{
void AppendField(std::string& output, const char* name,
                 const std::string& value)
{
    output.append(name);
    output.push_back('=');
    output.append(std::to_string(value.size()));
    output.push_back(':');
    output.append(value);
    output.push_back('\n');
}

std::string Number(double value)
{
    std::ostringstream output;
    output << std::setprecision(17) << value;
    return output.str();
}

void AppendContractBinding(
    std::string& output,
    const AuthoritativeFlattenPlan& plan)
{
    AppendField(output, "instrument", plan.instrument);
    AppendField(output, "contract.symbol", plan.contract.symbol);
    AppendField(output, "contract.sec_type", plan.contract.secType);
    AppendField(output, "contract.exchange", plan.contract.exchange);
    AppendField(output, "contract.primary_exchange",
                plan.contract.primaryExchange);
    AppendField(output, "contract.currency", plan.contract.currency);
    AppendField(output, "contract.month",
                plan.contract.lastTradeDateOrContractMonth);
    AppendField(output, "contract.right", plan.contract.right);
    AppendField(output, "contract.strike", Number(plan.contract.strike));
    AppendField(output, "contract.multiplier", plan.contract.multiplier);
    AppendField(output, "contract.trading_class",
                plan.contract.tradingClass);
    AppendField(output, "contract.local_symbol",
                plan.contract.localSymbol);
}

void AppendOrderBinding(
    std::string& output,
    const AuthoritativeFlattenPlan& plan)
{
    AppendField(output, "order.side", plan.order.action);
    AppendField(output, "order.type", plan.order.orderType);
    AppendField(output, "order.quantity",
                Number(plan.order.totalQuantity));
    AppendField(output, "order.limit_price", Number(plan.order.lmtPrice));
    AppendField(output, "order.aux_price", Number(plan.order.auxPrice));
    AppendField(output, "order.outside_rth",
                plan.order.outsideRth ? "1" : "0");
    AppendField(output, "order.ref", plan.order.orderRef);
    AppendField(output, "time_in_force", plan.timeInForce);
    AppendField(output, "reference_price", Number(plan.referencePrice));
}

void AppendSnapshotBinding(
    std::string& output,
    const AuthoritativeFlattenPlan& plan)
{
    AppendField(output, "position.quantity",
                Number(plan.expectedPositionQuantity));
    AppendField(output, "position.connection_epoch",
                std::to_string(plan.positionConnectionEpoch));
    AppendField(output, "position.generation",
                std::to_string(plan.positionGeneration));
    AppendField(output, "quote.subscription_id",
                plan.quoteSubscriptionId);
    AppendField(output, "quote.observed_at_ms",
                std::to_string(plan.quoteObservedAtMs));
    AppendField(output, "quote.stale_after_ms",
                std::to_string(plan.quoteStaleAfterMs));
    if (!plan.profileOrderMode.empty())
    {
        AppendField(output, "quote.bid", Number(plan.quoteBid));
        AppendField(output, "quote.ask", Number(plan.quoteAsk));
        AppendField(output, "profile.order_mode",
                    plan.profileOrderMode);
    }
}
}

bool SameIbPaperFlattenContract(
    const InstrumentRef& left,
    const InstrumentRef& right)
{
    return left.symbol == right.symbol &&
        left.secType == right.secType &&
        left.exchange == right.exchange &&
        left.primaryExchange == right.primaryExchange &&
        left.currency == right.currency &&
        left.lastTradeDateOrContractMonth ==
            right.lastTradeDateOrContractMonth &&
        left.right == right.right &&
        left.strike == right.strike &&
        left.multiplier == right.multiplier &&
        left.tradingClass == right.tradingClass &&
        left.localSymbol == right.localSymbol;
}

std::string CanonicalIbPaperFlattenPlanBinding(
    const AuthoritativeFlattenPlan& plan)
{
    std::string output("hepta.flatten-plan.v1\n");
    AppendContractBinding(output, plan);
    AppendOrderBinding(output, plan);
    AppendSnapshotBinding(output, plan);
    return output;
}

bool IbPaperFlattenPreviewPlanMatches(
    const FlattenPositionCommand& command,
    const AuthoritativeFlattenPlan& plan,
    std::string& reason)
{
    if (!command.hasAuthoritativePreviewSnapshot)
        return true;
    if (command.authoritativePreviewPlanBinding.empty() ||
        command.authoritativePreviewPlanBinding !=
            CanonicalIbPaperFlattenPlanBinding(plan))
    {
        reason = "IB_PAPER_FLATTEN_PREVIEW_PLAN_CHANGED";
        return false;
    }
    return true;
}
