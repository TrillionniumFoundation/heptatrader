#include "execution/ib_paper_execution_profile.h"

#include <cassert>
#include <iostream>
#include <map>
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
}

int main()
{
    TestQualificationProfileIsDistinctAndBounded();
    TestQualificationProfileRejectsWiderOrAmbiguousAuthority();
    std::cout << "ib paper execution profile tests passed\n";
    return 0;
}
