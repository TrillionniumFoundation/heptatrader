#include "execution/ib_paper_execution_profile.h"

#include <cassert>
#include <iostream>
#include <map>
#include <memory>
#include <string>

namespace
{
std::map<std::string, std::string> QualificationValues()
{
    std::map<std::string, std::string> values;
    values["HEPTA_IB_EXECUTION_MODE"] = "PAPER";
    values["HEPTA_IB_PAPER_ACCOUNT"] = "DU123456";
    values["HEPTA_IB_PAPER_HOST"] = "127.0.0.1";
    values["HEPTA_IB_PAPER_PORT"] = "4002";
    values["HEPTA_IB_PAPER_CLIENT_ID"] = "9127";
    values["HEPTA_IB_PAPER_MAX_ORDER_QTY"] = "1000000";
    values["HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL"] = "1500000";
    values["HEPTA_IB_PAPER_MAX_ORDERS_PER_MINUTE"] = "6";
    values["HEPTA_IB_PAPER_MAX_ACTIVE_ORDERS"] = "1";
    values["HEPTA_IB_PAPER_MAX_GROSS_POSITION"] = "1000000";
    values["HEPTA_EXECUTION_EXTERNAL_QUALIFICATION_LMT_DAY"] = "1";
    values["HEPTA_EXECUTION_QUALIFICATION_MAX_ORDER_NOTIONAL"] = "1500000";
    values["HEPTA_IB_PAPER_QUOTE_CONTRACTS"] =
        "EUR.USD|EUR|CASH|IDEALPRO|USD";
    values["HEPTA_IB_PAPER_PRIMARY_QUOTE_INSTRUMENT"] = "EUR.USD";
    values["HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS"] = "5000";
    values["HEPTA_IB_PAPER_CONTROL_DIRECTORY"] =
        "/run/hepta/ib-paper-control-qualification";
    values["STATE_DIRECTORY"] = "/var/lib/hepta-qualification/state";
    values["CREDENTIALS_DIRECTORY"] =
        "/run/credentials/hepta-qualification";
    return values;
}

void TestQualificationProfileIsDistinctAndBounded()
{
    const std::map<std::string, std::string> values = QualificationValues();
    IbPaperExecutionProfileConfig config;
    std::string reason;
    assert(IbPaperExecutionProfileConfig::FromValues(values, config, reason));
    assert(config.enabled);
    assert(config.UsesExternalLimitDay());
    assert(config.UsesExternalQualificationLimitDay());
    assert(config.orderMode ==
        IbPaperOrderMode::ExternalQualificationLimitDay);
    assert(std::string(config.AllowedOrderTypes()) == "LMT");
    assert(std::string(IbPaperExecutionProfileConfig::OrderModeName(
        config.orderMode)) == "EXTERNAL_QUALIFICATION_LMT_DAY");
    std::string credential;
    assert(config.BuildAuthorizationCredential(credential, reason));
    assert(credential.size() == 80);
    assert(credential.compare(0, 16, "PAPER-V5:sha256:") == 0);

    std::map<std::string, std::string> different = QualificationValues();
    different["HEPTA_IB_PAPER_QUOTE_CONTRACTS"] =
        "GBP.USD|GBP|CASH|IDEALPRO|USD";
    different["HEPTA_IB_PAPER_PRIMARY_QUOTE_INSTRUMENT"] = "GBP.USD";
    IbPaperExecutionProfileConfig differentConfig;
    assert(IbPaperExecutionProfileConfig::FromValues(
        different, differentConfig, reason));
    std::string differentCredential;
    assert(differentConfig.BuildAuthorizationCredential(
        differentCredential, reason));
    assert(differentCredential != credential);
}

void TestQualificationProfileRejectsWiderOrAmbiguousAuthority()
{
    std::string reason;
    IbPaperExecutionProfileConfig config;

    std::map<std::string, std::string> values = QualificationValues();
    values["HEPTA_IB_PAPER_MAX_ORDER_QTY"] = "1000001";
    assert(!IbPaperExecutionProfileConfig::FromValues(values, config, reason));
    assert(reason == "IB_PAPER_QUALIFICATION_ORDER_MODE_LIMITS_INVALID");

    values = QualificationValues();
    values["HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL"] = "1500001";
    assert(!IbPaperExecutionProfileConfig::FromValues(values, config, reason));
    assert(reason == "IB_PAPER_QUALIFICATION_ORDER_MODE_LIMITS_INVALID");

    values = QualificationValues();
    values["HEPTA_IB_PAPER_MAX_ORDERS_PER_MINUTE"] = "7";
    assert(!IbPaperExecutionProfileConfig::FromValues(values, config, reason));
    assert(reason == "IB_PAPER_QUALIFICATION_ORDER_MODE_LIMITS_INVALID");

    values = QualificationValues();
    values["HEPTA_IB_PAPER_MAX_ACTIVE_ORDERS"] = "2";
    assert(!IbPaperExecutionProfileConfig::FromValues(values, config, reason));
    assert(reason == "IB_PAPER_QUALIFICATION_ORDER_MODE_LIMITS_INVALID");

    values = QualificationValues();
    values["HEPTA_IB_PAPER_MAX_GROSS_POSITION"] = "999999";
    assert(!IbPaperExecutionProfileConfig::FromValues(values, config, reason));
    assert(reason == "IB_PAPER_QUALIFICATION_ORDER_MODE_LIMITS_INVALID");

    // The predecessor defect allowed sequential fills to accumulate beyond
    // one permitted atomic flatten order. Reject that envelope before activation.
    values = QualificationValues();
    values["HEPTA_IB_PAPER_MAX_ORDER_QTY"] = "250000";
    assert(!IbPaperExecutionProfileConfig::FromValues(values, config, reason));
    assert(reason == "IB_PAPER_QUALIFICATION_ORDER_MODE_LIMITS_INVALID");

    values = QualificationValues();
    values["HEPTA_EXECUTION_EXTERNAL_P1_CANARY_LMT_DAY"] = "1";
    values["HEPTA_EXECUTION_MAX_ORDER_NOTIONAL"] = "5000";
    assert(!IbPaperExecutionProfileConfig::FromValues(values, config, reason));
    assert(reason == "IB_PAPER_EXTERNAL_ORDER_MODE_CONFIGURATION_INVALID");

    values = QualificationValues();
    values["HEPTA_EXECUTION_QUALIFICATION_MAX_ORDER_NOTIONAL"] = "1500001";
    assert(!IbPaperExecutionProfileConfig::FromValues(values, config, reason));
    assert(reason == "IB_PAPER_EXTERNAL_ORDER_MODE_CONFIGURATION_INVALID");

    values = QualificationValues();
    values["HEPTA_IB_PAPER_QUOTE_CONTRACTS"] +=
        ";GBP.USD|GBP|CASH|IDEALPRO|USD";
    assert(!IbPaperExecutionProfileConfig::FromValues(values, config, reason));
    assert(reason == "IB_PAPER_QUALIFICATION_ORDER_MODE_LIMITS_INVALID");

    values = QualificationValues();
    values["HEPTA_IB_PAPER_PRIMARY_QUOTE_INSTRUMENT"] = "GBP.USD";
    assert(!IbPaperExecutionProfileConfig::FromValues(values, config, reason));
    assert(reason == "IB_PAPER_QUALIFICATION_ORDER_MODE_LIMITS_INVALID");
}

class FixedKillSwitchReader final : public IbPaperKillSwitchReader
{
public:
    explicit FixedKillSwitchReader(IbPaperKillSwitchState state)
        : m_state(state) {}

    IbPaperKillSwitchObservation Observe() const override
    {
        IbPaperKillSwitchObservation observation;
        observation.state = m_state;
        if (m_state == IbPaperKillSwitchState::Engaged)
            observation.reasonCode = "IB_PAPER_KILL_SWITCH_ENGAGED";
        else if (m_state == IbPaperKillSwitchState::Uncertain)
            observation.reasonCode = "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN";
        return observation;
    }

private:
    IbPaperKillSwitchState m_state;
};

IbPlaceOrderCommand QualificationOrder(
    const IbPaperExecutionProfileConfig& config,
    const std::string& side,
    double quantity)
{
    IbPlaceOrderCommand command;
    command.context.account = config.account;
    command.context.venue = "IB";
    command.context.executionDomain = "PAPER";
    command.contract.secType = "CASH";
    command.timeInForce = "DAY";
    command.order.action = side;
    command.order.orderType = "LMT";
    command.order.totalQuantity = quantity;
    command.order.lmtPrice = side == "SELL" ? 1.10 : 1.11;
    command.referencePrice = command.order.lmtPrice;
    return command;
}

AuthoritativeFlattenPlan ExactQualificationPlan(double position)
{
    AuthoritativeFlattenPlan plan;
    plan.profileOrderMode = "EXTERNAL_QUALIFICATION_LMT_DAY";
    plan.timeInForce = "DAY";
    plan.expectedPositionQuantity = position;
    plan.order.action = position > 0.0 ? "SELL" : "BUY";
    plan.order.orderType = "LMT";
    plan.order.totalQuantity = position > 0.0 ? position : -position;
    plan.quoteSubscriptionId = "qualification-quote";
    plan.quoteObservedAtMs = 1000;
    plan.quoteStaleAfterMs = 5000;
    plan.quoteBid = 1.10;
    plan.quoteAsk = 1.11;
    plan.order.lmtPrice = position > 0.0 ? plan.quoteBid : plan.quoteAsk;
    plan.referencePrice = plan.order.lmtPrice;
    return plan;
}

void TestQualificationEnvelopeAlwaysHasAnAtomicFlattenPath()
{
    std::map<std::string, std::string> values = QualificationValues();
    values["HEPTA_IB_PAPER_MAX_ORDER_QTY"] = "250000";
    values["HEPTA_IB_PAPER_MAX_GROSS_POSITION"] = "250000";
    IbPaperExecutionProfileConfig config;
    std::string reason;
    assert(IbPaperExecutionProfileConfig::FromValues(values, config, reason));
    assert(config.maxOrderQuantity == config.maxGrossPosition);
    assert(config.maxActiveOrders == 1);
    assert(config.qualificationQuoteContracts ==
        "EUR.USD|EUR|CASH|IDEALPRO|USD");

    // Two sequential partial fills can reach, but never exceed, the exact
    // one-order flatten envelope.
    const std::shared_ptr<IbPaperKillSwitchReader> disarmed(
        new FixedKillSwitchReader(IbPaperKillSwitchState::Disarmed));
    IbPaperExecutionGuard admission(config, disarmed);
    IbPaperAuthoritativeRiskSnapshot entryRisk;
    entryRisk.complete = true;
    entryRisk.activeOrderCount = 0;
    entryRisk.grossAbsolutePosition = 0.0;
    const IbPlaceOrderCommand first =
        QualificationOrder(config, "BUY", 125000.0);
    assert(admission.AllowPlaceAtAuthoritativePrice(
        first, entryRisk, 1.11, 1000, reason));
    entryRisk.grossAbsolutePosition = 125000.0;
    const IbPlaceOrderCommand second =
        QualificationOrder(config, "BUY", 125000.0);
    assert(admission.AllowPlaceAtAuthoritativePrice(
        second, entryRisk, 1.11, 1001, reason));
    entryRisk.grossAbsolutePosition = 250000.0;
    const IbPlaceOrderCommand overflow =
        QualificationOrder(config, "BUY", 1.0);
    assert(!admission.AllowPlaceAtAuthoritativePrice(
        overflow, entryRisk, 1.11, 1002, reason));
    assert(reason == "IB_PAPER_MAX_GROSS_POSITION_EXCEEDED");

    // Once the operator kill switch is engaged, both signs and a partial-fill
    // exposure remain exactly reducible without crossing zero.
    const std::shared_ptr<IbPaperKillSwitchReader> engaged(
        new FixedKillSwitchReader(IbPaperKillSwitchState::Engaged));
    IbPaperExecutionGuard flatten(config, engaged);
    FlattenPositionCommand command;
    command.context.account = config.account;
    command.context.venue = "IB";
    command.context.executionDomain = "PAPER";
    command.context.allowCancelAny = false;
    IbPaperAuthoritativeRiskSnapshot flattenRisk;
    flattenRisk.complete = true;
    flattenRisk.activeOrderCount = 0;

    AuthoritativeFlattenPlan longPlan = ExactQualificationPlan(250000.0);
    assert(flatten.AllowFlatten(command, longPlan, flattenRisk, 2000, reason));
    assert(reason.empty());

    AuthoritativeFlattenPlan partialShortPlan =
        ExactQualificationPlan(-125000.0);
    assert(flatten.AllowFlatten(
        command, partialShortPlan, flattenRisk, 2000, reason));
    assert(reason.empty());

    AuthoritativeFlattenPlan unreachable =
        ExactQualificationPlan(250001.0);
    assert(!flatten.AllowFlatten(
        command, unreachable, flattenRisk, 2000, reason));
    assert(reason == "IB_PAPER_EXTERNAL_FLATTEN_POSITION_LIMIT_EXCEEDED");
}
}

int main()
{
    TestQualificationProfileIsDistinctAndBounded();
    TestQualificationProfileRejectsWiderOrAmbiguousAuthority();
    TestQualificationEnvelopeAlwaysHasAnAtomicFlattenPath();
    std::cout << "ib paper execution profile tests passed\n";
    return 0;
}
