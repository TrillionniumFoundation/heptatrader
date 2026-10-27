#include "execution/ib_paper_execution_profile.h"
#include "execution/ib_paper_execution_runtime_config.h"
#include "execution/ib_paper_account_broker_evidence.h"
#include "../HeptaTrade/execution/ib_paper_fx_cash_restart_catchup.h"

#include <atomic>
#include <cassert>
#include <chrono>
#include <condition_variable>
#include <future>
#include <mutex>
#include <thread>
#include <unistd.h>
#include <iostream>
#include <map>
#include <memory>
#include <limits>
#include <string>
#include <cstdlib>

namespace
{
std::string FixtureTemporaryPath(const std::string& leaf) {
    const char* env=std::getenv("TMPDIR");
    return std::string(env && *env ? env : "/tmp")+"/"+leaf+"-XXXXXX";
}

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

OmsJournalEvent CatchUpEvent(
    const std::string& type, std::int64_t ts, long orderId,
    const std::string& source)
{
    OmsJournalEvent event;
    event.eventType = type;
    event.tsMs = ts;
    event.orderId = orderId;
    event.source = source;
    event.traceId = "session";
    event.strategy = "agent-native";
    event.account = "DU123";
    event.executionDomain = "PAPER";
    event.instrument = "EUR.USD";
    event.side = "BUY";
    event.qty = 25000.0;
    event.price = 1.1;
    return event;
}

void TestFxCashRestartCatchUpBindsReusedOrderIdToPostCheckpointSend()
{
    IbPaperFxCashRestartCatchUpContext context;
    context.toolSource = "agent.tool:paper";
    context.brokerSource = "agent:paper";
    context.account = "DU123";
    context.executionDomain = "PAPER";
    context.maxGrossPosition = 25000.0;
    std::map<std::string, IbPaperFxCashBaseline> baselines;
    IbPaperFxCashBaseline baseline;
    baseline.account = context.account;
    baseline.instrument = "EUR.USD";
    baseline.currency = "EUR";
    baseline.baselineCashBalance = -100000.0;
    baseline.observedCashBalance = -125000.0;
    baseline.campaignExecutionDelta = -25000.0;
    baseline.observedAtMs = 1000;
    baseline.proof = "sha256:" + std::string(64, 'a');
    baselines[baseline.instrument] = baseline;

    std::vector<OmsJournalEvent> events;
    // Same numeric IB order id from an older generation: the checkpoint
    // watermark must make these irrelevant to the new command.
    OmsJournalEvent oldAccepted =
        CatchUpEvent("broker_order_accepted", 900, 101, context.brokerSource);
    oldAccepted.brokerServiceEpoch = "old-service";
    oldAccepted.brokerConnectionEpoch = 1;
    events.push_back(oldAccepted);
    OmsJournalEvent oldExecution =
        CatchUpEvent("broker_execution", 950, 101, context.brokerSource);
    oldExecution.side = "BOT";
    oldExecution.brokerServiceEpoch = "old-service";
    oldExecution.brokerConnectionEpoch = 1;
    oldExecution.brokerExecutionId = "old-exec";
    events.push_back(oldExecution);

    OmsJournalEvent accepted =
        CatchUpEvent("broker_order_accepted", 1999, 101, context.brokerSource);
    accepted.brokerServiceEpoch = "new-service";
    accepted.brokerConnectionEpoch = 3;
    events.push_back(accepted);
    OmsJournalEvent sent =
        CatchUpEvent("flatten_sent", 2000, 101, context.toolSource);
    sent.venueCorrelationId = "hepta-v1-sha256:new";
    events.push_back(sent);
    OmsJournalEvent partial =
        CatchUpEvent("broker_execution", 2050, 101, context.brokerSource);
    partial.side = "BOT";
    partial.qty = 10000.0;
    partial.brokerServiceEpoch = "new-service";
    partial.brokerConnectionEpoch = 3;
    partial.brokerExecutionId = "new-exec-partial";
    events.push_back(partial);
    OmsJournalEvent terminal = partial;
    terminal.tsMs = 2100;
    terminal.qty = 25000.0; // IB execDetails carries cumulative quantity.
    terminal.brokerExecutionId = "new-exec-terminal";
    events.push_back(terminal);
    // A completed-order refresh from a later process may replay the old
    // execution with the reused order id. Different service identity must
    // make it non-authoritative for this send.
    OmsJournalEvent replayedOld = terminal;
    replayedOld.tsMs = 2200;
    replayedOld.brokerServiceEpoch = "later-service";
    replayedOld.brokerConnectionEpoch = 1;
    replayedOld.brokerExecutionId = "old-exec";
    events.push_back(replayedOld);

    std::string reason;
    assert(ApplyIbPaperFxCashRestartCatchUp(
        events, context, baselines, reason));
    assert(reason.empty());
    assert(baselines["EUR.USD"].campaignExecutionDelta == 0.0);
    assert(baselines["EUR.USD"].observedCashBalance == -100000.0);
    assert(baselines["EUR.USD"].observedAtMs == 2100);
}

void TestFxCashRestartCatchUpFailsClosedOnAmbiguousOrIncompleteEvidence()
{
    IbPaperFxCashRestartCatchUpContext context;
    context.toolSource = "agent.tool:paper";
    context.brokerSource = "agent:paper";
    context.account = "DU123";
    context.executionDomain = "PAPER";
    context.maxGrossPosition = 25000.0;
    auto makeBaseline = [&]() {
        std::map<std::string, IbPaperFxCashBaseline> result;
        IbPaperFxCashBaseline baseline;
        baseline.account = context.account;
        baseline.instrument = "EUR.USD";
        baseline.currency = "EUR";
        baseline.baselineCashBalance = -100000.0;
        baseline.observedCashBalance = -125000.0;
        baseline.campaignExecutionDelta = -25000.0;
        baseline.observedAtMs = 1000;
        baseline.proof = "sha256:" + std::string(64, 'a');
        result[baseline.instrument] = baseline;
        return result;
    };

    OmsJournalEvent sent =
        CatchUpEvent("flatten_sent", 7000, 101, context.toolSource);
    sent.venueCorrelationId = "corr-new";
    OmsJournalEvent oldAccepted =
        CatchUpEvent("broker_order_accepted", 1500, 101, context.brokerSource);
    oldAccepted.brokerServiceEpoch = "old-service";
    oldAccepted.brokerConnectionEpoch = 1;
    OmsJournalEvent accepted =
        CatchUpEvent("broker_order_accepted", 6999, 101, context.brokerSource);
    accepted.brokerServiceEpoch = "new-service";
    accepted.brokerConnectionEpoch = 2;
    OmsJournalEvent terminal =
        CatchUpEvent("broker_execution", 7100, 101, context.brokerSource);
    terminal.side = "BOT";
    terminal.brokerServiceEpoch = "new-service";
    terminal.brokerConnectionEpoch = 2;
    terminal.brokerExecutionId = "exec-new";
    terminal.venueCorrelationId = "corr-new";

    std::string reason;
    auto baselines = makeBaseline();
    assert(!ApplyIbPaperFxCashRestartCatchUp(
        {oldAccepted, accepted, sent, terminal}, context, baselines, reason));
    assert(reason == "IB_FX_CASH_RESTART_CATCHUP_ACCEPTANCE_INVALID");

    baselines = makeBaseline();
    OmsJournalEvent wrongCorrelation = terminal;
    wrongCorrelation.venueCorrelationId = "corr-other";
    assert(!ApplyIbPaperFxCashRestartCatchUp(
        {accepted, sent, wrongCorrelation}, context, baselines, reason));
    assert(reason == "IB_FX_CASH_RESTART_CATCHUP_EXECUTION_INVALID");

    baselines = makeBaseline();
    OmsJournalEvent partial = terminal;
    partial.qty = 10000.0;
    assert(!ApplyIbPaperFxCashRestartCatchUp(
        {accepted, sent, partial}, context, baselines, reason));
    assert(reason == "IB_FX_CASH_RESTART_CATCHUP_EXECUTION_INVALID");
}

void TestFxCashRestartCatchUpZeroFillApiCancellation()
{
    IbPaperFxCashRestartCatchUpContext context;
    context.toolSource="agent.tool:paper";context.brokerSource="agent:paper";
    context.account="DU123";context.executionDomain="PAPER";context.maxGrossPosition=25000;
    context.brokerClientId=701;
    std::map<std::string,IbPaperFxCashBaseline> baselines;
    IbPaperFxCashBaseline baseline;
    baseline.account=context.account;baseline.instrument="EUR.USD";baseline.currency="EUR";
    baseline.baselineCashBalance=100000;baseline.observedCashBalance=100000;
    baseline.campaignExecutionDelta=0;baseline.observedAtMs=1000;
    baseline.proof="sha256:"+std::string(64,'a');baselines[baseline.instrument]=baseline;
    auto accepted=CatchUpEvent("broker_order_accepted",2000,103,context.brokerSource);
    accepted.reqId="original-cancelled-request";accepted.side="SELL";
    accepted.brokerServiceEpoch="service-A";accepted.brokerConnectionEpoch=3;accepted.brokerClientId=701;
    auto sent=CatchUpEvent("place_sent",2000,103,context.toolSource);
    sent.reqId=accepted.reqId;sent.side="SELL";
    sent.venueCorrelationId="hepta-v1-sha256:"+std::string(64,'c');
    auto cancelled=accepted;cancelled.eventType="broker_order_status";cancelled.tsMs=3000;
    cancelled.brokerCallbackType="orderStatus";cancelled.status="ApiCancelled";
    cancelled.qty=0;cancelled.price=0;cancelled.brokerRemainingQuantity=25000;
    cancelled.venueCorrelationId=sent.venueCorrelationId;
    std::string reason;
    assert(ApplyIbPaperFxCashRestartCatchUp({accepted,sent,cancelled},context,baselines,reason));
    assert(reason.empty());
    assert(baselines.at("EUR.USD").campaignExecutionDelta==0);
    assert(baselines.at("EUR.USD").observedCashBalance==100000);
    // A cancellation is not a new cash observation or an economic fill.
    assert(baselines.at("EUR.USD").observedAtMs==1000);
    const auto original=baselines;
    // Duplicate receipt and whole-journal replay are idempotent.
    for (const std::string status : {"Cancelled","ApiCancelled"})
    {
        auto duplicate=cancelled;duplicate.status=status;duplicate.brokerRemainingQuantity=0;
        assert(ApplyIbPaperFxCashRestartCatchUp({accepted,sent,duplicate,duplicate},context,baselines,reason));
        assert(baselines.at("EUR.USD").observedCashBalance==100000);
        assert(baselines.at("EUR.USD").observedAtMs==1000);
    }
    auto reject=[&](const std::vector<OmsJournalEvent>& input) {
        auto state=original;
        assert(!ApplyIbPaperFxCashRestartCatchUp(input,context,state,reason));
        assert(!reason.empty());
        assert(state.at("EUR.USD").observedCashBalance==100000);
        assert(state.at("EUR.USD").campaignExecutionDelta==0);
        assert(state.at("EUR.USD").observedAtMs==1000);
    };
    reject({accepted,sent}); // unknown is not cancellation
    for (int variant=0;variant<24;++variant)
    {
        auto bad=cancelled;
        switch (variant) {
        case 0: bad.reqId="other-command";break;
        case 1: bad.reqId.clear();break;
        case 2: bad.venueCorrelationId="other-correlation";break;
        case 3: bad.venueCorrelationId.clear();break;
        case 4: bad.orderId=104;break;
        case 5: bad.traceId="other-session";break;
        case 6: bad.account="DU999";break;
        case 7: bad.executionDomain="LIVE";break;
        case 8: bad.instrument="GBP.USD";break;
        case 9: bad.side="BUY";break;
        case 10: bad.brokerServiceEpoch="other-service";break;
        case 11: bad.brokerConnectionEpoch=4;break;
        case 12: bad.brokerClientId=702;break;
        case 13: bad.brokerClientId=-1;break;
        case 14: bad.brokerCallbackType.clear();break;
        case 15: bad.tsMs=1999;break;
        case 16: bad.qty=1;break;
        case 17: bad.price=1.1;break;
        case 18: bad.brokerRemainingQuantity=10000;break;
        case 19: bad.brokerExecutionId="unexpected-execution";break;
        case 20: bad.status="PendingCancel";break;
        case 21: bad.status="Inactive";break;
        case 22: bad.eventType="broker_error";bad.brokerErrorCode=10328;break;
        case 23: bad.qty=std::numeric_limits<double>::quiet_NaN();break;
        }
        reject({accepted,sent,bad});
    }
    // Partial fill + cancellation must not be turned into a zero movement.
    auto partial=cancelled;partial.eventType="broker_execution";
    partial.brokerExecutionId="partial-exec";partial.side="SLD";
    partial.qty=10000;partial.price=1.1328;partial.tsMs=2500;
    reject({accepted,sent,partial,cancelled});
    auto full=partial;full.qty=25000;full.brokerExecutionId="full-exec";
    reject({accepted,sent,full,cancelled});
    partial.brokerServiceEpoch="reconnected-service";partial.brokerConnectionEpoch=9;
    reject({accepted,sent,partial,cancelled});
    auto partialStatus=cancelled;partialStatus.status="Submitted";
    partialStatus.qty=10000;partialStatus.price=1.1328;partialStatus.tsMs=2500;
    reject({accepted,sent,partialStatus,cancelled});
    // Same number from BEFORE the checkpoint is not this cancelled lifetime.
    auto historical=partial;historical.tsMs=900;historical.reqId="old-command";
    historical.venueCorrelationId="old-correlation";
    assert(ApplyIbPaperFxCashRestartCatchUp({historical,accepted,sent,cancelled},context,baselines,reason));
    assert(baselines.at("EUR.USD").observedCashBalance==100000);
    // Independent filled and non-filled commands compose without phantom cash.
    auto fillAccepted=accepted;fillAccepted.orderId=104;fillAccepted.tsMs=4000;fillAccepted.reqId="filled-request";
    auto fillSent=sent;fillSent.orderId=104;fillSent.tsMs=4000;fillSent.reqId="filled-request";
    fillSent.venueCorrelationId="filled-correlation";
    auto fill=full;fill.orderId=104;fill.tsMs=4100;fill.reqId=fillSent.reqId;fill.venueCorrelationId=fillSent.venueCorrelationId;
    assert(ApplyIbPaperFxCashRestartCatchUp({accepted,sent,cancelled,fillAccepted,fillSent,fill},context,baselines,reason));
    assert(baselines.at("EUR.USD").campaignExecutionDelta==-25000);
    assert(baselines.at("EUR.USD").observedCashBalance==75000);
    assert(baselines.at("EUR.USD").observedAtMs==4100);
}

void TestRuntimeColdConnectBudgetDefaultsFailClosed()
{
    IbPaperExecutionRuntimeConfig config;
    std::string reason;
    assert(config.initialConnectTimeoutMs == 10000);
    config.initialConnectTimeoutMs = 9999;
    assert(!config.Validate(reason));
    assert(reason == "IB_PAPER_RUNTIME_DISABLED_CONFIGURATION_PRESENT");

    std::map<std::string, std::string> residual;
    residual["HEPTA_IB_EXECUTION_MODE"] = "DISABLED";
    residual["HEPTA_IB_EXECUTION_INITIAL_CONNECT_TIMEOUT_MS"] = "2500";
    assert(!IbPaperExecutionRuntimeConfig::FromValues(
        residual, static_cast<int>(::getpid()), config, reason));
    assert(reason == "IB_PAPER_RUNTIME_DISABLED_CONFIGURATION_PRESENT");
}

void TestProductionIdentityUsesSelectedCustody()
{
    IbPaperExecutionRuntimeConfig config;
    config.mode = IbPaperExecutionRuntimeMode::Paper;
    std::string reason;
    const std::uint32_t uid = static_cast<std::uint32_t>(::geteuid());
    config.allowedGatewayUids.insert(uid);
    assert(!config.ValidateProductionIdentity(uid, reason));
    config.profile.userScope = true;
    assert(config.ValidateProductionIdentity(uid, reason) == (uid != 0));
    config.allowedGatewayUids.clear();
    config.allowedGatewayUids.insert(uid + 1);
    assert(!config.ValidateProductionIdentity(uid, reason));
    config.profile.userScope = false;
    assert(config.ValidateProductionIdentity(uid, reason) == (uid != 0));
    config.profile.userScope = true;
    assert(!config.ValidateProductionIdentity(uid + 1, reason));
    assert(!config.ValidateProductionIdentity(0, reason));
}

void TestUserPaperCustodyIsExplicitAndBounded()
{
    auto values = QualificationValues();
    values.erase("HEPTA_EXECUTION_EXTERNAL_QUALIFICATION_LMT_DAY");
    values.erase("HEPTA_EXECUTION_QUALIFICATION_MAX_ORDER_NOTIONAL");
    values["HEPTA_IB_PAPER_USER_SCOPE"] = "1";
    values["HEPTA_IB_PAPER_MAX_ORDER_QTY"] = "100000";
    values["HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL"] = "100000";
    values["HEPTA_IB_PAPER_MAX_GROSS_POSITION"] = "100000";
    values["HEPTA_IB_PAPER_MAX_ORDERS_PER_MINUTE"] = "2";
    values["HEPTA_IB_PAPER_CONTROL_DIRECTORY"] = IbPaperExecutionProfileConfig::UserControlDirectoryPath();
    IbPaperExecutionProfileConfig profile;
    std::string reason, credential;
    const bool admitted = IbPaperExecutionProfileConfig::FromValues(values, profile, reason);
    if (::geteuid() == 0 || ::getegid() == 0)
    { assert(!admitted); return; }
    assert(admitted && profile.userScope);
    assert(profile.BuildAuthorizationCredential(credential, reason));
    assert(credential.find("PAPER-USER-V1:sha256:") == 0);
    const std::string original = credential;
    auto changed = values;
    changed["HEPTA_IB_PAPER_QUOTE_CONTRACTS"] = "GBP.USD|GBP|CASH|IDEALPRO|USD";
    changed["HEPTA_IB_PAPER_PRIMARY_QUOTE_INSTRUMENT"] = "GBP.USD";
    assert(IbPaperExecutionProfileConfig::FromValues(changed, profile, reason));
    assert(profile.BuildAuthorizationCredential(credential, reason) && credential != original);
    const std::pair<const char*, const char*> bad[] = {
        {"HEPTA_IB_PAPER_USER_SCOPE", "yes"}, {"HEPTA_IB_PAPER_USER_SCOPE", "0"},
        {"HEPTA_IB_EXECUTION_MODE", "LIVE"}, {"HEPTA_IB_EXECUTION_MODE", "DISABLED"},
        {"HEPTA_IB_PAPER_ACCOUNT", "U12345"}, {"HEPTA_IB_PAPER_PORT", "4001"},
        {"HEPTA_IB_PAPER_HOST", "192.0.2.1"},
        {"HEPTA_IB_PAPER_CONTROL_DIRECTORY", "/run/user/1/heptatrader/control"},
        {"HEPTA_IB_PAPER_MAX_ACTIVE_ORDERS", "2"},
        {"HEPTA_IB_PAPER_MAX_ORDERS_PER_MINUTE", "3"},
        {"HEPTA_IB_PAPER_MAX_ORDER_QTY", "100001"},
        {"HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL", "100001"},
        {"HEPTA_IB_PAPER_MAX_GROSS_POSITION", "100001"},
        {"HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS", "5001"},
        {"HEPTA_IB_PAPER_PRIMARY_QUOTE_INSTRUMENT", "GBP.USD"}};
    for (const auto& item : bad)
    {
        changed = values; changed[item.first] = item.second;
        assert(!IbPaperExecutionProfileConfig::FromValues(changed, profile, reason));
    }
    changed = QualificationValues(); changed["HEPTA_IB_PAPER_USER_SCOPE"] = "1";
    assert(!IbPaperExecutionProfileConfig::FromValues(changed, profile, reason));
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
    IbPlaceOrderCommand unsupportedOffset =
        QualificationOrder(config, "BUY", 1.0);
    unsupportedOffset.order.positionEffect = "OPEN";
    assert(!admission.AllowPlaceAtAuthoritativePrice(
        unsupportedOffset, entryRisk, 1.11, 999, reason));
    assert(reason == "IB_PAPER_ORDER_INTENT_INVALID");

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

void TestAuditedOwnerReleaseIsDurableAndDoesNotInstallRecoveryIngress()
{
    IbPaperExecutionProfileConfig config; std::string reason;
    assert(IbPaperExecutionProfileConfig::FromValues(QualificationValues(), config, reason));
    std::string path = FixtureTemporaryPath("hepta-owner-release");
    const int fd = ::mkstemp(&path[0]); assert(fd >= 0); ::close(fd);
    OmsJournal journal; assert(journal.Init(path.c_str()));
    ExecutionCoordinatorCallbacks callbacks;
    ExecutionCoordinator coordinator(journal, callbacks);
    IBAuthoritativeRecoveryAuditSnapshot snapshot;
    snapshot.active.complete = true;
    snapshot.active.connectionEpoch = 1; snapshot.active.generation = 1;
    snapshot.terminal.complete = true;
    snapshot.terminal.connectionEpoch = 1; snapshot.terminal.generation = 1;
    snapshot.risk.complete = true; snapshot.risk.coherentRefreshComplete = true;
    snapshot.risk.accountComplete = true; snapshot.risk.positionsComplete = true;
    snapshot.risk.fxCashComplete = true; snapshot.risk.connectionEpoch = 1;
    snapshot.risk.generation = 1; snapshot.risk.accountGeneration = 1;
    snapshot.risk.positionsGeneration = 1; snapshot.risk.fxCashGeneration = 1;
    snapshot.barrierComplete = true;
    IbPaperExecutionPolicyCallbacks policyCallbacks;
    policyCallbacks.nowMs = [] { return OmsJournal::NowEpochMs(); };
    policyCallbacks.recoveryAuditSnapshot = [&] { return snapshot; };
    std::shared_ptr<IbPaperKillSwitchReader> guard(new FixedKillSwitchReader(IbPaperKillSwitchState::Engaged));
    IbPaperExecutionPolicyAuthority policy(coordinator, config, policyCallbacks, guard);
    ExecutionControlCommand command;
    command.context = QualificationOrder(config, "BUY", 1.0).context;
    command.context.agentId = "resume-owner"; command.context.sessionId = "resume-session";
    command.context.toolCallId = "owner-audit";
    const auto audit = policy.AuditSessionOwner(command);
    assert(audit.status == ExecutionCommandStatus::Accepted);
    assert(audit.ownerAuditAuthoritative && audit.ownerAuditComplete);
    assert(!coordinator.IsSessionOwnerRecoveryOnly("resume-owner", "resume-session"));
    coordinator.FenceSessionOwner("resume-owner", "resume-session");
    snapshot.risk.complete = false;
    assert(policy.ReleaseSessionOwnerFence(command).status == ExecutionCommandStatus::Rejected);
    assert(coordinator.IsSessionOwnerFenced("resume-owner", "resume-session"));
    snapshot.risk.complete = true;
    snapshot.positionQuantities["EUR.USD"] = 1.0;
    snapshot.risk.grossAbsolutePosition = 1.0;
    assert(policy.ReleaseSessionOwnerFence(command).status == ExecutionCommandStatus::Rejected);
    assert(coordinator.IsSessionOwnerFenced("resume-owner", "resume-session"));
    snapshot.positionQuantities.clear(); snapshot.risk.grossAbsolutePosition = 0.0;
    snapshot.active.activeOrderIds.insert(123);
    assert(policy.ReleaseSessionOwnerFence(command).status == ExecutionCommandStatus::Rejected);
    snapshot.active.activeOrderIds.clear();
    const auto released = policy.ReleaseSessionOwnerFence(command);
    assert(released.status == ExecutionCommandStatus::Accepted);
    assert(released.reasonCode == "SESSION_OWNER_FENCE_RELEASED");
    assert(!coordinator.IsSessionOwnerFenced("resume-owner", "resume-session"));
    assert(coordinator.RecoverFromJournal(reason));
    assert(!coordinator.IsSessionOwnerFenced("resume-owner", "resume-session"));
    // Lost reply: repeating the audited control action is harmless, not a new order.
    const auto replay = policy.ReleaseSessionOwnerFence(command);
    assert(replay.status == ExecutionCommandStatus::Accepted);
    assert(replay.reasonCode == "SESSION_OWNER_NOT_FENCED");
    coordinator.FenceSessionOwner("resume-owner", "resume-session");
    assert(coordinator.EnterRecoveryOnlyOwner("resume-owner", "resume-session", 3, reason));
    assert(policy.ReleaseSessionOwnerFence(command).status == ExecutionCommandStatus::Rejected);
    assert(coordinator.IsSessionOwnerFenced("resume-owner", "resume-session"));
    assert(coordinator.IsSessionOwnerRecoveryOnly("resume-owner", "resume-session"));
    ::unlink(path.c_str());
}

void TestActiveUserRecoveryMarkerResumesButTerminalOwnerNeverDoes()
{
    if (::geteuid() == 0 || ::getegid() == 0) return;
    auto values = QualificationValues();
    values.erase("HEPTA_EXECUTION_EXTERNAL_QUALIFICATION_LMT_DAY");
    values.erase("HEPTA_EXECUTION_QUALIFICATION_MAX_ORDER_NOTIONAL");
    values["HEPTA_IB_PAPER_USER_SCOPE"] = "1";
    values["HEPTA_IB_PAPER_MAX_ORDER_QTY"] = "25000";
    values["HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL"] = "35000";
    values["HEPTA_IB_PAPER_MAX_GROSS_POSITION"] = "25000";
    values["HEPTA_IB_PAPER_MAX_ORDERS_PER_MINUTE"] = "2";
    values["HEPTA_IB_PAPER_CONTROL_DIRECTORY"] = IbPaperExecutionProfileConfig::UserControlDirectoryPath();
    IbPaperExecutionProfileConfig config; std::string reason;
    assert(IbPaperExecutionProfileConfig::FromValues(values, config, reason));
    std::string path = FixtureTemporaryPath("hepta-user-recovery-resume");
    const int fd = ::mkstemp(&path[0]); assert(fd >= 0); ::close(fd);
    OmsJournal journal; assert(journal.Init(path.c_str()));
    ExecutionCoordinatorCallbacks callbacks; ExecutionCoordinator coordinator(journal, callbacks);
    IBAuthoritativeRecoveryAuditSnapshot snapshot;
    snapshot.active.complete = true; snapshot.active.connectionEpoch = 1; snapshot.active.generation = 1;
    snapshot.terminal.complete = true; snapshot.terminal.connectionEpoch = 1; snapshot.terminal.generation = 1;
    snapshot.risk.complete = true; snapshot.risk.coherentRefreshComplete = true;
    snapshot.risk.accountComplete = true; snapshot.risk.positionsComplete = true; snapshot.risk.fxCashComplete = true;
    snapshot.risk.connectionEpoch = 1; snapshot.risk.generation = 1; snapshot.risk.accountGeneration = 1;
    snapshot.risk.positionsGeneration = 1; snapshot.risk.fxCashGeneration = 1; snapshot.barrierComplete = true;
    IbPaperExecutionPolicyCallbacks policyCallbacks;
    policyCallbacks.nowMs = [] { return OmsJournal::NowEpochMs(); };
    policyCallbacks.recoveryAuditSnapshot = [&] { return snapshot; };
    std::shared_ptr<IbPaperKillSwitchReader> guard(new FixedKillSwitchReader(IbPaperKillSwitchState::Engaged));
    IbPaperExecutionPolicyAuthority policy(coordinator, config, policyCallbacks, guard);
    ExecutionControlCommand command; command.context = QualificationOrder(config, "BUY", 1.0).context;
    command.context.agentId="active-user"; command.context.sessionId="active-session";
    command.context.toolCallId="audited-resume";
    assert(coordinator.EnterRecoveryOnlyOwner("active-user","active-session",1,reason));
    coordinator.FenceSessionOwner("active-user","active-session");
    assert(!coordinator.AuditAndReleaseSessionOwnerFence("active-user","active-session",true,reason));
    assert(!coordinator.AuditAndReleaseSessionOwnerFence("active-user","active-session",true,reason,2));
    snapshot.risk.grossAbsolutePosition=1.0; snapshot.positionQuantities["EUR.USD"]=1.0;
    assert(policy.ReleaseSessionOwnerFence(command).status==ExecutionCommandStatus::Rejected);
    assert(coordinator.IsSessionOwnerFenced("active-user","active-session"));
    assert(coordinator.IsSessionOwnerRecoveryOnly("active-user","active-session"));
    snapshot.risk.grossAbsolutePosition=0.0; snapshot.positionQuantities.clear();
    const auto released=policy.ReleaseSessionOwnerFence(command);
    assert(released.status==ExecutionCommandStatus::Accepted && released.reasonCode=="SESSION_OWNER_FENCE_RELEASED");
    assert(!coordinator.IsSessionOwnerFenced("active-user","active-session"));
    assert(!coordinator.IsSessionOwnerRecoveryOnly("active-user","active-session"));
    assert(coordinator.RecoverFromJournal(reason));
    assert(!coordinator.IsSessionOwnerFenced("active-user","active-session"));
    assert(!coordinator.IsSessionOwnerRecoveryOnly("active-user","active-session"));
    assert(!coordinator.EnterRecoveryOnlyOwner("active-user","active-session",1,reason));
    assert(reason=="SESSION_RECOVERY_INGRESS_FENCE_STALE");
    assert(coordinator.EnterRecoveryOnlyOwner("active-user","active-session",2,reason));
    // A fresh legitimate recovery generation can be resumed; a stale one cannot.
    assert(policy.ReleaseSessionOwnerFence(command).status==ExecutionCommandStatus::Accepted);
    assert(coordinator.RecoverFromJournal(reason));
    assert(!coordinator.EnterRecoveryOnlyOwner("active-user","active-session",2,reason));
    PaperTerminalFenceBinding binding; binding.owner=command.context;
    binding.finalizationId="terminal-user"; binding.preliminaryReceiptSha256="sha256:"+std::string(64,'a');
    binding.recoveryIngressFence=3; binding.serviceEpoch="terminal-test";
    binding.serviceFencingGeneration=1; binding.serviceProcessId=1; binding.serviceProcessStartTicks=1;
    binding.brokerConnectionEpoch=1; binding.brokerSocketIdentitySha256="sha256:"+std::string(64,'b');
    PaperTerminalMutationUniverse universe;
    assert(coordinator.EnterPaperTerminalFenceAndProject(binding,universe,reason));
    assert(coordinator.EnterRecoveryOnlyOwner("active-user","active-session",3,reason));
    coordinator.FenceSessionOwner("active-user","active-session");
    assert(!coordinator.AuditAndReleaseSessionOwnerFence("active-user","active-session",true,reason,3));
    assert(policy.ReleaseSessionOwnerFence(command).status==ExecutionCommandStatus::Rejected);
    assert(coordinator.IsMutationBlocked(nullptr));
    assert(coordinator.IsSessionOwnerRecoveryOnly("active-user","active-session"));
    ::unlink(path.c_str());
}

// Real policy/coordinator path with latch-controlled venue callbacks. Progress
// must occur before releasing the slow provider, not merely below a benchmark
// percentile. No sockets, Broker API or trading service are used.
struct PolicyExitBarrier
{
    std::mutex mutex;
    std::condition_variable changed;
    bool entered = false;
    bool released = false;
    void Block()
    {
        std::unique_lock<std::mutex> lock(mutex);
        entered = true;
        changed.notify_all();
        assert(changed.wait_for(lock, std::chrono::seconds(15), [&] { return released; }));
    }
    void WaitEntered()
    {
        std::unique_lock<std::mutex> lock(mutex);
        assert(changed.wait_for(lock, std::chrono::seconds(2), [&] { return entered; }));
    }
    void Release()
    {
        std::lock_guard<std::mutex> lock(mutex);
        released = true;
        changed.notify_all();
    }
};

void TestPolicyGateClosesExitAdmissionWhenOriginalCohortDrains()
{
    IbPolicyOperationGate gate;
    const auto original = gate.lock_shared(false);
    PolicyExitBarrier exclusiveBarrier;
    auto recovery = std::async(std::launch::async, [&] {
        std::lock_guard<IbPolicyOperationGate> lock(gate);
        exclusiveBarrier.Block();
    });
    // Observe the captured cohort through the admission token instead of
    // assuming the recovery thread has reached lock() after a short sleep.
    std::uint64_t joined = original;
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);
    do
    {
        joined = gate.lock_shared(true);
        if (joined > original) break;
        gate.unlock_shared(joined);
        std::this_thread::yield();
        assert(std::chrono::steady_clock::now() < deadline);
    } while (true);
    gate.unlock_shared(original);
    std::promise<void> attempting;
    auto attempted = attempting.get_future();
    auto lateExit = std::async(std::launch::async, [&] {
        attempting.set_value();
        IbPolicySharedOperation operation(gate, true);
    });
    attempted.wait();
    assert(lateExit.wait_for(std::chrono::milliseconds(30)) == std::future_status::timeout);
    gate.unlock_shared(joined);
    exclusiveBarrier.WaitEntered();
    assert(lateExit.wait_for(std::chrono::milliseconds(0)) == std::future_status::timeout);
    exclusiveBarrier.Release();
    recovery.get();
    assert(lateExit.wait_for(std::chrono::seconds(1)) == std::future_status::ready);
    lateExit.get();
}

struct ConcurrentPolicyFixture
{
    IbPaperExecutionProfileConfig config;
    std::string path;
    OmsJournal journal;
    PolicyExitBarrier placeBarrier, flattenBarrier, riskBarrier;
    std::atomic<int> places{0}, cancels{0}, flattens{0}, recoveryReads{0}, terminalCalls{0};
    std::atomic<std::size_t> activeOrders{0};
    std::atomic<bool> pauseNextRiskSnapshot{false};
    bool uncertainPlace = false, uncertainCancel = false, uncertainFlatten = false;
    const std::uint64_t quoteAt = OmsJournal::NowEpochMs();
    std::unique_ptr<ExecutionCoordinator> coordinator;
    std::unique_ptr<IbPaperExecutionPolicyAuthority> policy;

    ConcurrentPolicyFixture()
    {
        auto values = QualificationValues();
        values.erase("HEPTA_EXECUTION_EXTERNAL_QUALIFICATION_LMT_DAY");
        values.erase("HEPTA_EXECUTION_QUALIFICATION_MAX_ORDER_NOTIONAL");
        values.erase("HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS");
        values.erase("HEPTA_IB_PAPER_QUOTE_CONTRACTS");
        values.erase("HEPTA_IB_PAPER_PRIMARY_QUOTE_INSTRUMENT");
        values["HEPTA_IB_PAPER_MAX_ORDER_QTY"] = "2";
        values["HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL"] = "100";
        values["HEPTA_IB_PAPER_MAX_GROSS_POSITION"] = "4";
        values["HEPTA_IB_PAPER_MAX_ACTIVE_ORDERS"] = "4";
        values["HEPTA_IB_PAPER_MAX_ORDERS_PER_MINUTE"] = "2";
        std::string reason;
        assert(IbPaperExecutionProfileConfig::FromValues(values, config, reason));
        path = FixtureTemporaryPath("hepta-policy-concurrent-exits");
        const int fd = ::mkstemp(&path[0]); assert(fd >= 0); ::close(fd);
        assert(journal.Init(path.c_str()));
        ExecutionCoordinatorCallbacks callbacks;
        callbacks.validateDecisionLease = [](const AgentExecutionContext&, const std::string&, std::string*) { return true; };
        callbacks.placement = VenuePlacement::Immediate([this](const PlaceOrderCommand& command, const std::string&) {
            ++places;
            if (command.context.toolCallId == "slow-place")
            {
                placeBarrier.Block();
                if (uncertainPlace) return VenuePlaceResult::Uncertain("fixture lost place acknowledgement");
                return VenuePlaceResult::Submitted(42);
            }
            return VenuePlaceResult::Submitted(41);
        });
        callbacks.canCancelIbOrder = [](long, std::string*) { return true; };
        callbacks.cancelOrder = [this](long id) {
            assert(id == 41); ++cancels;
            return uncertainCancel ? VenueCancelResult::Uncertain("fixture lost cancel acknowledgement") :
                VenueCancelResult::Submitted();
        };
        callbacks.flattenOrder = [this](const AuthoritativeFlattenPlan& plan, const std::string&) {
            assert(plan.instrument == "GBP.USD" && plan.expectedPositionQuantity == 1.0 &&
                   plan.order.action == "SELL" && plan.order.totalQuantity == 1.0);
            ++flattens;
            flattenBarrier.Block();
            return uncertainFlatten ? VenueFlattenResult::Uncertain("fixture lost flatten acknowledgement", 43) :
                VenueFlattenResult::Submitted(43);
        };
        coordinator.reset(new ExecutionCoordinator(journal, callbacks));
        IbPaperExecutionPolicyCallbacks policyCallbacks;
        policyCallbacks.nowMs = [] { return OmsJournal::NowEpochMs(); };
        policyCallbacks.riskSnapshot = [this] {
            if (pauseNextRiskSnapshot.exchange(false)) riskBarrier.Block();
            IbPaperAuthoritativeRiskSnapshot risk; risk.complete = true;
            risk.grossAbsolutePosition = 1.0; risk.activeOrderCount = activeOrders.load(); return risk;
        };
        policyCallbacks.authoritativeContract = [](const std::string& instrument, InstrumentRef& contract) {
            if (instrument != "EUR.USD" && instrument != "GBP.USD") return false;
            contract.symbol = instrument == "EUR.USD" ? "EUR" : "GBP";
            contract.secType = "CASH"; contract.exchange = "IDEALPRO"; contract.currency = "USD"; return true;
        };
        policyCallbacks.authoritativePosition = [](const std::string&) {
            IbPaperAuthoritativePositionSnapshot position; position.complete = true;
            position.connectionEpoch = 1; position.generation = 1; position.quantity = 1.0; return position;
        };
        policyCallbacks.authoritativeQuote = [this](const std::string& instrument) {
            MarketQuoteSnapshot quote; quote.subscriptionId = "concurrent-" + instrument; quote.instrument = instrument;
            quote.state = MarketSubscriptionState::Active; quote.bid = 1.10; quote.ask = 1.11;
            quote.observedAtMs = quoteAt; quote.staleAfterMs = quoteAt + 60000; return quote;
        };
        policyCallbacks.correlationSnapshot = [this] {
            ++recoveryReads;
            IBAuthoritativeCorrelationSnapshot result; result.complete = false;
            result.reasonCode = "FIXTURE_RECONCILIATION_NOT_PROVEN"; return result;
        };
        policyCallbacks.beginTerminalRecoveryAudit = [this](const ExecutionControlCommand&,
            IBAuthoritativeRecoveryAuditSnapshot&, ExecutionTerminalResult&, std::string& reason) {
            ++terminalCalls; reason = "FIXTURE_TERMINAL_WITNESS_NOT_PROVEN"; return false;
        };
        policyCallbacks.commitTerminalRecoveryAudit = [](const ExecutionControlCommand&,
            const ExecutionOwnerAuditResult&, ExecutionTerminalResult&, std::string&) { assert(false); return false; };
        std::shared_ptr<IbPaperKillSwitchReader> killSwitch(new FixedKillSwitchReader(IbPaperKillSwitchState::Disarmed));
        policy.reset(new IbPaperExecutionPolicyAuthority(*coordinator, config, policyCallbacks, killSwitch));
    }
    ~ConcurrentPolicyFixture() { policy.reset(); coordinator.reset(); ::unlink(path.c_str()); }
    PlaceOrderCommand Place(const std::string& id) const
    {
        PlaceOrderCommand command;
        command.context.agentId = "entry-owner"; command.context.sessionId = "session";
        command.context.account = config.account; command.context.venue = "IB"; command.context.executionDomain = "PAPER";
        command.context.toolCallId = id; command.context.decisionLeaseFencingToken = 1; command.context.decisionLeaseGeneration = 1;
        command.instrument = "EUR.USD"; command.contract.symbol = "EUR"; command.contract.secType = "CASH";
        command.contract.exchange = "IDEALPRO"; command.contract.currency = "USD";
        command.order.action = "BUY"; command.order.orderType = "MKT"; command.order.totalQuantity = 1.0;
        command.referencePrice = 1.11; command.timeInForce = "DAY"; command.expiresAtMs = quoteAt + 60000;
        return command;
    }
    FlattenPositionCommand Flatten(const std::string& instrument, const std::string& id)
    {
        FlattenPositionCommand command; command.context = Place(id).context;
        command.context.agentId = "exit-owner"; command.instrument = instrument;
        command.contract.symbol = instrument == "EUR.USD" ? "EUR" : "GBP";
        command.contract.secType = "CASH"; command.contract.exchange = "IDEALPRO"; command.contract.currency = "USD";
        const auto preview = policy->PreviewFlattenPosition(command);
        assert(preview.status == ExecutionCommandStatus::Accepted);
        command.hasAuthoritativePreviewSnapshot = true;
        command.previewPositionQuantity = preview.authoritativeFlattenPositionQuantity;
        command.previewPositionConnectionEpoch = preview.authoritativeFlattenConnectionEpoch;
        command.previewPositionGeneration = preview.authoritativeFlattenPositionGeneration;
        command.authoritativePreviewPlanBinding = preview.authoritativeFlattenPlanBinding;
        return command;
    }
};

void TestSlowPlaceAllowsConfirmedCancelAndPreservesRecovery()
{
    for (const bool uncertain : {false, true})
    {
        ConcurrentPolicyFixture fixture; fixture.uncertainPlace = uncertain; fixture.uncertainCancel = uncertain;
        assert(fixture.policy->PlaceOrder(fixture.Place("confirmed-old")).status == ExecutionCommandStatus::Accepted);
        fixture.activeOrders.store(1); // Independently observed older active order.
        const auto slow = fixture.Place("slow-place");
        auto place = std::async(std::launch::async, [&] { return fixture.policy->PlaceOrder(slow); });
        fixture.placeBarrier.WaitEntered();
        std::string replayReason;
        assert(!fixture.coordinator->RecoverFromJournal(replayReason));
        assert(replayReason == "OMS_RECOVERY_DISPATCH_IN_FLIGHT");
        assert(fixture.coordinator->RuntimeObservation().riskMutationDispatchInFlight);
        auto recovery = std::async(std::launch::async, [&] {
            std::size_t count = 0; std::string reason;
            return fixture.policy->ReconcileAuthoritativeState(count, reason);
        });
        assert(recovery.wait_for(std::chrono::milliseconds(30)) == std::future_status::timeout);
        assert(fixture.recoveryReads.load() == 0);
        CancelOrderCommand cancel; cancel.context = slow.context; cancel.context.toolCallId = "cancel-confirmed"; cancel.orderId = 41;
        const auto started = std::chrono::steady_clock::now();
        auto pending = std::async(std::launch::async, [&] { return fixture.policy->CancelOrder(cancel); });
        assert(pending.wait_for(std::chrono::seconds(1)) == std::future_status::ready);
        const auto result = pending.get();
        const auto cancelNs = std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now()-started).count();
        assert(cancelNs < 1000000000LL && fixture.cancels.load() == 1);
        assert(result.status == (uncertain ? ExecutionCommandStatus::Uncertain : ExecutionCommandStatus::Accepted));
        assert(fixture.policy->CancelOrder(cancel).status ==
            (uncertain ? ExecutionCommandStatus::Uncertain : ExecutionCommandStatus::Duplicate));
        assert(fixture.cancels.load() == 1);
        assert(place.wait_for(std::chrono::milliseconds(0)) == std::future_status::timeout);
        ExecutionControlCommand control; control.context = slow.context; control.targetCommandId = "slow-place";
        assert(fixture.policy->QueryCommandStatus(control).targetStatus == ExecutionCommandStatus::Uncertain);
        assert(fixture.policy->ReleaseSessionOwnerFence(control).reasonCode == "IB_PAPER_CONTROL_BUSY");
        assert(fixture.coordinator->FenceSessionOwner(slow.context.agentId, slow.context.sessionId) >= 1);
        auto fenced = cancel; fenced.context.toolCallId = "new-after-fence";
        assert(fixture.policy->CancelOrder(fenced).reasonCode == "SESSION_OWNER_FENCED");
        fixture.placeBarrier.Release();
        assert(place.get().status == (uncertain ? ExecutionCommandStatus::Uncertain : ExecutionCommandStatus::Accepted));
        assert(!recovery.get() && fixture.recoveryReads.load() == 1);
        assert(fixture.policy->PlaceOrder(slow).status ==
            (uncertain ? ExecutionCommandStatus::Uncertain : ExecutionCommandStatus::Duplicate));
        assert(fixture.places.load() == 2 && fixture.cancels.load() == 1);
        if (uncertain) assert(fixture.coordinator->IsMutationBlocked(nullptr));
        std::cout << "{\"schema\":\"heptatrader.policy-exit-progress.v1\",\"operation\":\"cancel\",\"cancel_total_ns\":"
                  << cancelNs << ",\"progress_before_place_release\":true,\"uncertain_case\":" << (uncertain?"true":"false") << "}\n";
    }
}

void TestSlowPlaceAllowsIndependentFlattenAndRetainsScopes()
{
    for (const bool uncertain : {false, true})
    {
        ConcurrentPolicyFixture fixture; fixture.uncertainFlatten = uncertain;
        const auto same = fixture.Flatten("EUR.USD", "same-scope-close");
        auto crossDomain = same;
        crossDomain.context.toolCallId = "same-scope-cross-domain";
        crossDomain.context.executionDomain = "PAPER:exit";
        const auto independent = fixture.Flatten("GBP.USD", "independent-close");
        const auto slow = fixture.Place("slow-place");
        auto place = std::async(std::launch::async, [&] { return fixture.policy->PlaceOrder(slow); });
        fixture.placeBarrier.WaitEntered();
        const auto began = std::chrono::steady_clock::now();
        for (const auto& candidate : {same, crossDomain})
        {
            auto conflict = std::async(std::launch::async, [&] { return fixture.policy->FlattenPosition(candidate); });
            assert(conflict.wait_for(std::chrono::seconds(1)) == std::future_status::ready);
            assert(conflict.get().reasonCode == "MUTATION_BLOCKED" && fixture.flattens.load() == 0);
        }
        auto reduction = std::async(std::launch::async, [&] { return fixture.policy->FlattenPosition(independent); });
        fixture.flattenBarrier.WaitEntered();
        const auto progressNs = std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now()-began).count();
        assert(progressNs < 2000000000LL && fixture.flattens.load() == 1);
        assert(place.wait_for(std::chrono::milliseconds(0)) == std::future_status::timeout);
        assert(fixture.coordinator->ObserveAccountLocalDrain(slow.context).venueDispatchesInFlight == 2);
        assert(fixture.policy->FlattenPosition(independent).status == ExecutionCommandStatus::Uncertain);
        assert(fixture.flattens.load() == 1);
        fixture.placeBarrier.Release();
        assert(place.get().status == ExecutionCommandStatus::Accepted);
        // Completing one scope must not clear the other scope's in-flight bit.
        assert(fixture.coordinator->RuntimeObservation().riskMutationDispatchInFlight);
        assert(fixture.coordinator->ObserveAccountLocalDrain(slow.context).venueDispatchesInFlight == 1);
        auto newEntry = fixture.Place("new-while-reducing");
        const auto blocked = fixture.policy->PlaceOrder(newEntry);
        assert(blocked.status == ExecutionCommandStatus::Rejected);
        assert(blocked.reasonCode == "IB_PAPER_ORDER_RATE_EXCEEDED");
        assert(fixture.places.load() == 1); // Both original send attempts consume the entry budget.
        assert(fixture.coordinator->FenceSessionOwner(independent.context.agentId, independent.context.sessionId) >= 1);
        ExecutionControlCommand terminal; terminal.context = independent.context;
        terminal.recoveryIngressFence = 19; terminal.targetCommandId = "terminal-test";
        terminal.terminalPreliminaryReceiptSha256 = "sha256:" + std::string(64, 'a');
        auto finalize = std::async(std::launch::async, [&] { return fixture.policy->TerminalizeRecoveryOwner(terminal); });
        assert(finalize.wait_for(std::chrono::milliseconds(30)) == std::future_status::timeout);
        assert(fixture.terminalCalls.load() == 0);
        fixture.flattenBarrier.Release();
        assert(reduction.get().status == (uncertain ? ExecutionCommandStatus::Uncertain : ExecutionCommandStatus::Accepted));
        const auto finalized = finalize.get();
        assert(finalized.status == ExecutionCommandStatus::Rejected);
        assert(finalized.reasonCode == "FIXTURE_TERMINAL_WITNESS_NOT_PROVEN");
        assert(fixture.terminalCalls.load() == 1); // A fence alone cannot manufacture a terminal witness.
        assert(!fixture.coordinator->RuntimeObservation().riskMutationDispatchInFlight);
        assert(fixture.policy->FlattenPosition(independent).status ==
            (uncertain ? ExecutionCommandStatus::Uncertain : ExecutionCommandStatus::Duplicate));
        assert(fixture.flattens.load() == 1);
        if (uncertain) assert(fixture.coordinator->IsMutationBlocked(nullptr));
        std::cout << "{\"schema\":\"heptatrader.policy-exit-progress.v1\",\"operation\":\"flatten\",\"progress_ns\":"
                  << progressNs << ",\"progress_before_place_release\":true,\"same_scope_blocked\":true,\"uncertain_case\":"
                  << (uncertain?"true":"false") << "}\n";
    }
}

void TestCompletedFlattenCannotSpendStaleEntryBudget()
{
    ConcurrentPolicyFixture fixture;
    assert(fixture.policy->PlaceOrder(fixture.Place("budget-first")).status == ExecutionCommandStatus::Accepted);
    std::string reason;
    assert(fixture.coordinator->RecordOrderTerminalDurably(41, &reason));
    const auto independent = fixture.Flatten("GBP.USD", "budget-reduction");
    fixture.pauseNextRiskSnapshot.store(true);
    const auto entry = fixture.Place("budget-race");
    auto placement = std::async(std::launch::async, [&] { return fixture.policy->PlaceOrder(entry); });
    fixture.riskBarrier.WaitEntered(); // Entry has read one send; cap is two.
    auto reduction = std::async(std::launch::async, [&] { return fixture.policy->FlattenPosition(independent); });
    fixture.flattenBarrier.WaitEntered();
    fixture.flattenBarrier.Release();
    assert(reduction.get().status == ExecutionCommandStatus::Accepted);
    assert(!fixture.coordinator->RuntimeObservation().riskMutationDispatchInFlight);
    // The reduction finished completely between the entry's first budget read
    // and coordinator admission. Neither an in-flight bit nor entryMutex can
    // detect this; the final locked budget check must refuse without a send.
    fixture.riskBarrier.Release();
    const auto result = placement.get();
    assert(result.status == ExecutionCommandStatus::Rejected);
    assert(result.reasonCode == "IB_PAPER_ORDER_RATE_EXCEEDED");
    assert(fixture.places.load() == 1 && fixture.flattens.load() == 1);
    std::vector<std::int64_t> attempts;
    fixture.coordinator->GetPlaceSendAttemptTimes(entry.context.account,
        entry.context.executionDomain, fixture.quoteAt - 60000, attempts);
    assert(attempts.size() == fixture.config.maxOrdersPerMinute);
}

void TestPolicyControlsDoNotWaitBehindVenueDispatch()
{
    IbPaperExecutionProfileConfig config; std::string reason;
    assert(IbPaperExecutionProfileConfig::FromValues(QualificationValues(), config, reason));
    std::string path = FixtureTemporaryPath("hepta-policy-control");
    const int fd = ::mkstemp(&path[0]); assert(fd >= 0); ::close(fd);
    OmsJournal journal; assert(journal.Init(path.c_str()));
    std::mutex mutex; std::condition_variable changed; bool entered = false, released = false;
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.placement = VenuePlacement::Immediate([](const PlaceOrderCommand&, const std::string&) {
        return VenuePlaceResult::Submitted(42);
    });
    callbacks.validateDecisionLease = [](const AgentExecutionContext&, const std::string&, std::string*) { return true; };
    callbacks.cancelOrder = [&](long) {
        std::unique_lock<std::mutex> lock(mutex); entered = true; changed.notify_all();
        assert(changed.wait_for(lock, std::chrono::seconds(5), [&] { return released; }));
        return VenueCancelResult::Submitted();
    };
    ExecutionCoordinator coordinator(journal, callbacks);
    PlaceOrderCommand place = QualificationOrder(config, "BUY", 1.0);
    place.context.agentId = "policy-control"; place.context.sessionId = "session";
    place.context.toolCallId = "place"; place.context.decisionLeaseFencingToken = 1;
    place.context.decisionLeaseGeneration = 1; place.contract.symbol = "EUR";
    place.instrument = "EUR.USD"; place.expiresAtMs = OmsJournal::NowEpochMs() + 60000;
    assert(coordinator.PlaceOrder(place).status == ExecutionCommandStatus::Accepted);
    IbPaperExecutionPolicyCallbacks policyCallbacks;
    policyCallbacks.nowMs = [] { return OmsJournal::NowEpochMs(); };
    std::shared_ptr<IbPaperKillSwitchReader> killSwitch(new FixedKillSwitchReader(IbPaperKillSwitchState::Engaged));
    IbPaperExecutionPolicyAuthority policy(coordinator, config, policyCallbacks, killSwitch);
    CancelOrderCommand cancel; cancel.context = place.context; cancel.context.toolCallId = "cancel"; cancel.orderId = 42;
    auto pending = std::async(std::launch::async, [&] { return policy.CancelOrder(cancel); });
    {
        std::unique_lock<std::mutex> lock(mutex);
        assert(changed.wait_for(lock, std::chrono::seconds(2), [&] { return entered; }));
    }
    ExecutionControlCommand control; control.context = place.context;
    control.context.toolCallId = "control"; control.targetCommandId = "place";
    auto status = std::async(std::launch::async, [&] { return policy.QueryCommandStatus(control); });
    assert(status.wait_for(std::chrono::seconds(1)) == std::future_status::ready);
    assert(status.get().status == ExecutionCommandStatus::Accepted);
    auto fence = std::async(std::launch::async, [&] { return policy.FenceSessionOwner(control); });
    assert(fence.wait_for(std::chrono::seconds(1)) == std::future_status::ready);
    const auto installed = fence.get();
    assert(installed.status == ExecutionCommandStatus::Accepted);
    assert(installed.affectedCount >= 1);
    assert(installed.reasonCode == "SESSION_OWNER_FENCE_INSTALLED_OUTSTANDING_RISK");
    const auto release = policy.ReleaseSessionOwnerFence(control);
    assert(release.status == ExecutionCommandStatus::Rejected);
    assert(release.reasonCode == "IB_PAPER_CONTROL_BUSY");
    assert(coordinator.IsSessionOwnerFenced(place.context.agentId, place.context.sessionId));
    { std::lock_guard<std::mutex> lock(mutex); released = true; changed.notify_all(); }
    assert(pending.get().status == ExecutionCommandStatus::Accepted);
    ::unlink(path.c_str());
}

}

IbPaperExecutionProfileConfig MixedCryptoProfileForTest()
{
    auto values=QualificationValues();
    values.erase("HEPTA_EXECUTION_EXTERNAL_QUALIFICATION_LMT_DAY");
    values.erase("HEPTA_EXECUTION_QUALIFICATION_MAX_ORDER_NOTIONAL");
    values["HEPTA_IB_PAPER_USER_SCOPE"]="1";
    values["HEPTA_IB_PAPER_MAX_ORDER_QTY"]="25000";
    values["HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL"]="35000";
    values["HEPTA_IB_PAPER_MAX_GROSS_POSITION"]="25000";
    values["HEPTA_IB_PAPER_MAX_ORDERS_PER_MINUTE"]="2";
    values["HEPTA_IB_PAPER_CONTROL_DIRECTORY"]=IbPaperExecutionProfileConfig::UserControlDirectoryPath();
    values["HEPTA_IB_PAPER_QUOTE_CONTRACTS"]="BTC.USD|BTC|CRYPTO|PAXOS|USD;EUR.USD|EUR|CASH|IDEALPRO|USD";
    values["HEPTA_IB_PAPER_PRIMARY_QUOTE_INSTRUMENT"]="BTC.USD";
    IbPaperExecutionProfileConfig config;std::string reason;
    assert(IbPaperExecutionProfileConfig::FromValues(values,config,reason));
    assert(config.UsesUserCrypto());return config;
}

void TestMixedCryptoRuntimeParsesRealEnvironmentFields()
{
    if (::geteuid()==0 || ::getegid()==0) return;
    std::string stateDir = FixtureTemporaryPath("hepta-crypto-runtime-state");
    std::string credentialDir = FixtureTemporaryPath("hepta-crypto-runtime-credentials");
    assert(::mkdtemp(&stateDir[0]) && ::mkdtemp(&credentialDir[0]));
    auto values=QualificationValues();
    values["STATE_DIRECTORY"]=stateDir;
    values["CREDENTIALS_DIRECTORY"]=credentialDir;
    values.erase("HEPTA_EXECUTION_EXTERNAL_QUALIFICATION_LMT_DAY");
    values.erase("HEPTA_EXECUTION_QUALIFICATION_MAX_ORDER_NOTIONAL");
    values["HEPTA_IB_PAPER_USER_SCOPE"]="1";
    values["HEPTA_IB_PAPER_MAX_ORDER_QTY"]="25000";
    values["HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL"]="35000";
    values["HEPTA_IB_PAPER_MAX_GROSS_POSITION"]="25000";
    values["HEPTA_IB_PAPER_MAX_ORDERS_PER_MINUTE"]="2";
    values["HEPTA_IB_PAPER_CONTROL_DIRECTORY"]=IbPaperExecutionProfileConfig::UserControlDirectoryPath();
    values["HEPTA_IB_PAPER_QUOTE_CONTRACTS"]="BTC.USD|BTC|CRYPTO|PAXOS|USD;EUR.USD|EUR|CASH|IDEALPRO|USD";
    values["HEPTA_IB_PAPER_PRIMARY_QUOTE_INSTRUMENT"]="BTC.USD";
    values["HEPTA_IB_EXECUTION_GATEWAY_UID"]=std::to_string(::geteuid());
    values["HEPTA_IB_EXECUTION_GATEWAY_AGENT_ID"]="pocket4-paper";
    values["HEPTA_IB_EXECUTION_DOMAIN_ID"]="PAPER";
    values["LISTEN_PID"]="1234"; values["LISTEN_FDS"]="2";
    values["LISTEN_FDNAMES"]="execution:events";
    IbPaperExecutionRuntimeConfig runtime;std::string reason;
    const bool ok=IbPaperExecutionRuntimeConfig::FromValues(values,1234,runtime,reason);
    if (!ok) std::cerr << reason << '\n';
    assert(ok && runtime.profile.UsesUserCrypto() && runtime.quoteContracts.size()==2);
    assert(runtime.quoteContracts.at("BTC.USD").secType=="CRYPTO");
    assert(runtime.quoteContracts.at("EUR.USD").secType=="CASH");
    for (const auto& contracts : {
            "BTC.USD|BTC|CRYPTO|ZEROHASH|USD;EUR.USD|EUR|CASH|IDEALPRO|USD",
            "ETH.USD|ETH|CRYPTO|PAXOS|USD;EUR.USD|EUR|CASH|IDEALPRO|USD",
            "BTC.USD|BTC|CRYPTO|PAXOS|USD"}) {
        auto bad=values;bad["HEPTA_IB_PAPER_QUOTE_CONTRACTS"]=contracts;
        assert(!IbPaperExecutionRuntimeConfig::FromValues(bad,1234,runtime,reason));
    }
    auto live=values;live["HEPTA_IB_EXECUTION_MODE"]="LIVE";
    assert(!IbPaperExecutionRuntimeConfig::FromValues(live,1234,runtime,reason));
    values["HEPTA_IB_PAPER_QUOTE_CONTRACTS"]="EUR.USD|EUR|CASH|IDEALPRO|USD";
    values["HEPTA_IB_PAPER_PRIMARY_QUOTE_INSTRUMENT"]="EUR.USD";
    assert(IbPaperExecutionRuntimeConfig::FromValues(values,1234,runtime,reason));
    assert(!runtime.profile.UsesUserCrypto() && runtime.quoteContracts.size()==1);
    assert(::rmdir(stateDir.c_str())==0 && ::rmdir(credentialDir.c_str())==0);
}

void TestCryptoGuardHasIndependentUnitsAndNoSpotShorts()
{
    if (::geteuid()==0 || ::getegid()==0)return;
    const auto config=MixedCryptoProfileForTest();
    IbPlaceOrderCommand command;
    command.context.account=config.account;command.context.venue="IB";command.context.executionDomain="PAPER";
    command.instrument="BTC.USD";command.contract.symbol="BTC";command.contract.secType="CRYPTO";
    command.contract.exchange="PAXOS";command.contract.currency="USD";
    command.timeInForce="IOC";command.order.action="BUY";command.order.orderType="MKT";
    command.order.totalQuantity=0.0;command.order.cashQuantity=25000.0;command.order.lmtPrice=0.0;command.referencePrice=85000.25;
    IbPaperAuthoritativeRiskSnapshot risk;risk.complete=true;risk.instrumentPositionsComplete=true;
    risk.instrumentPositions["EUR.USD"]=-25000;risk.instrumentPositions["BTC.USD"]=0;
    risk.grossAbsolutePosition=25000;std::string reason;
    auto allowed=[&](const IbPlaceOrderCommand& order,const IbPaperAuthoritativeRiskSnapshot& snapshot) {
        std::shared_ptr<IbPaperKillSwitchReader> guard(new FixedKillSwitchReader(IbPaperKillSwitchState::Disarmed));
        IbPaperExecutionGuard admission(config,guard);
        return admission.AllowPlaceAtAuthoritativePrice(order,snapshot,order.referencePrice,1000,reason);
    };
    assert(allowed(command,risk)); // An FX 25k short does not consume BTC unit capacity.
    // The resident policy stays exactly $25k. Qualification gets one separate
    // smaller envelope and cannot turn the production order into arbitrary sizing.
    assert(IbPaperExecutionProfileConfig::CryptoCashQuantityValid(25000.0,85000.25));
    assert(IbPaperExecutionProfileConfig::CryptoCashQuantityValid(42.50,85000.25));
    assert(IbPaperExecutionProfileConfig::CryptoCashQuantityValid(150.0,400000.0));
    assert(!IbPaperExecutionProfileConfig::CryptoCashQuantityValid(42.51,85000.25));
    assert(!IbPaperExecutionProfileConfig::CryptoCashQuantityValid(150.01,400000.0));
    assert(!IbPaperExecutionProfileConfig::CryptoCashQuantityValid(151.0,400000.0));
    assert(!IbPaperExecutionProfileConfig::CryptoCashQuantityValid(24999.99,85000.25));
    assert(!IbPaperExecutionProfileConfig::CryptoCashQuantityValid(42.505,85000.25));
    auto canary=command;canary.order.cashQuantity=42.50;assert(allowed(canary,risk));
    for (int kind=0;kind!=12;++kind) {
        auto bad=command;auto badRisk=risk;
        if(kind==0)bad.order.totalQuantity=25000;
        if(kind==1)bad.order.cashQuantity=24999.99;
        if(kind==2)bad.order.cashQuantity=84.995;
        if(kind==3)bad.order.action="SELL";
        if(kind==4)bad.timeInForce="DAY";
        if(kind==5)bad.contract.exchange="ZEROHASH";
        if(kind==6)bad.contract.symbol="ETH";
        if(kind==7)bad.order.lmtPrice=85000.10;
        if(kind==8)bad.order.orderType="LMT";
        if(kind==9){bad.order.cashQuantity=25000.01;bad.referencePrice=150000.25;}
        if(kind==10)badRisk.instrumentPositionsComplete=false;
        if(kind==11)badRisk.instrumentPositions["BTC.USD"]=0.0001;
        assert(!allowed(bad,badRisk));
    }
    for (const double cash : {0.0, -1.0, 24999.99, 25000.01, std::numeric_limits<double>::infinity(),
                              std::numeric_limits<double>::quiet_NaN()}) {
        auto bad=command;bad.order.cashQuantity=cash;assert(!allowed(bad,risk));
    }
    auto arbitraryReference=command;arbitraryReference.referencePrice=85000.1;
    assert(allowed(arbitraryReference,risk)); // MKT reference need not be a limit-price tick.
    auto foreign=risk;foreign.instrumentPositions["ETH.USD"]=1;
    assert(!allowed(command,foreign));
    auto excessiveFx=risk;excessiveFx.instrumentPositions["EUR.USD"]=-25001;
    assert(!allowed(command,excessiveFx));
    auto invalid=risk;invalid.instrumentPositions["BTC.USD"]=-0.00000001;
    assert(!allowed(command,invalid));
    std::string mixedCredential;assert(config.BuildAuthorizationCredential(mixedCredential,reason));
    auto single=config;single.userQuoteContracts="EUR.USD|EUR|CASH|IDEALPRO|USD";single.userPrimaryQuoteInstrument="EUR.USD";
    std::string oldCredential;assert(single.BuildAuthorizationCredential(oldCredential,reason));
    assert(mixedCredential!=oldCredential && !single.UsesUserCrypto());
}

void TestLocalFxFullReductionAboveStaleNotionalCap()
{
    if (::geteuid()==0 || ::getegid()==0) return;
    // User PAPER mixed-quote profile: max order notional 35k, max FX qty 25k.
    // An originally compliant EUR short may mark above 35k without any new
    // intent. Only the exactly broker-sized opposing full close is exempt.
    const auto config=MixedCryptoProfileForTest();
    std::shared_ptr<IbPaperKillSwitchReader> engaged(
        new FixedKillSwitchReader(IbPaperKillSwitchState::Engaged));
    IbPaperExecutionGuard guard(config,engaged);
    FlattenPositionCommand cmd;
    cmd.context.account=config.account;cmd.context.venue="IB";
    cmd.context.executionDomain="PAPER";cmd.instrument="EUR.USD";
    AuthoritativeFlattenPlan plan;
    plan.instrument="EUR.USD";plan.contract.symbol="EUR";
    plan.contract.secType="CASH";plan.contract.exchange="IDEALPRO";
    plan.contract.currency="USD";
    plan.timeInForce="DAY";plan.expectedPositionQuantity=-25000;
    plan.positionConnectionEpoch=3;plan.positionGeneration=11;
    plan.order.action="BUY";plan.order.orderType="MKT";
    plan.order.totalQuantity=25000;plan.order.lmtPrice=0;
    plan.referencePrice=1.52; // $38k, exceeds original $35k entry ceiling
    plan.quoteBid=1.5199;plan.quoteAsk=1.52;
    plan.quoteSubscriptionId="broker-cash-current";
    plan.quoteObservedAtMs=1900;plan.quoteStaleAfterMs=6900;
    IbPaperAuthoritativeRiskSnapshot risk;
    risk.complete=true;risk.activeOrderCount=0;
    std::string reason;
    assert(guard.AllowFlatten(cmd,plan,risk,2000,reason));
    assert(reason.empty());
    auto partial=plan;partial.order.totalQuantity=24000; // $36,480
    assert(!guard.AllowFlatten(cmd,partial,risk,2000,reason));
    assert(reason=="IB_PAPER_MAX_ORDER_NOTIONAL_EXCEEDED");
    auto within=plan;within.order.totalQuantity=23000; // $34,960
    assert(guard.AllowFlatten(cmd,within,risk,2000,reason));
    auto overshoot=plan;overshoot.order.totalQuantity=25000.01;
    assert(!guard.AllowFlatten(cmd,overshoot,risk,2000,reason));
    assert(reason=="IB_PAPER_FLATTEN_NOT_EXACT_REDUCE_ONLY");
    auto wrongSide=plan;wrongSide.order.action="SELL";
    assert(!guard.AllowFlatten(cmd,wrongSide,risk,2000,reason));
    auto wrongType=plan;wrongType.order.orderType="LMT";
    assert(!guard.AllowFlatten(cmd,wrongType,risk,2000,reason));
    auto invalidPrice=plan;invalidPrice.referencePrice=0;
    assert(!guard.AllowFlatten(cmd,invalidPrice,risk,2000,reason));
    auto active=risk;active.activeOrderCount=1;
    assert(!guard.AllowFlatten(cmd,plan,active,2000,reason));
    assert(reason=="IB_PAPER_FLATTEN_ACTIVE_ORDERS_PRESENT");
}

void TestCryptoFlattenIsExactOwnedLongAndMarketIOC()
{
    if (::geteuid()==0 || ::getegid()==0)return;
    const auto config=MixedCryptoProfileForTest();
    std::shared_ptr<IbPaperKillSwitchReader> engaged(new FixedKillSwitchReader(IbPaperKillSwitchState::Engaged));
    IbPaperExecutionGuard guard(config,engaged);
    FlattenPositionCommand command;command.context.account=config.account;command.context.venue="IB";
    command.context.executionDomain="PAPER";command.instrument="BTC.USD";
    AuthoritativeFlattenPlan plan;
    plan.instrument="BTC.USD";plan.contract.symbol="BTC";plan.contract.secType="CRYPTO";
    plan.contract.exchange="PAXOS";plan.contract.currency="USD";plan.profileOrderMode="USER_CRYPTO_MKT_IOC";
    plan.timeInForce="IOC";plan.expectedPositionQuantity=0.0004;
    plan.positionConnectionEpoch=1;plan.positionGeneration=1;
    plan.order.action="SELL";plan.order.orderType="MKT";plan.order.totalQuantity=0.0004;
    plan.order.lmtPrice=0.0;
    plan.referencePrice=85000;plan.quoteBid=85000;plan.quoteAsk=85000.25;
    plan.quoteSubscriptionId="btc-quote";plan.quoteObservedAtMs=1000;plan.quoteStaleAfterMs=5000;
    IbPaperAuthoritativeRiskSnapshot risk;risk.complete=true;risk.activeOrderCount=0;
    std::string reason;assert(guard.AllowFlatten(command,plan,risk,2000,reason));
    for(int kind=0;kind!=5;++kind) {
        auto bad=plan;
        if(kind==0)bad.order.totalQuantity=0.0005;
        if(kind==1)bad.expectedPositionQuantity=-0.0004;
        if(kind==2)bad.order.action="BUY";
        if(kind==3)bad.timeInForce="DAY";
        if(kind==4)bad.contract.exchange="ZEROHASH";
        assert(!guard.AllowFlatten(command,bad,risk,2000,reason));
    }
    auto tooDeep=plan;tooDeep.order.lmtPrice=84957.25;tooDeep.referencePrice=84957.25;
    assert(!guard.AllowFlatten(command,tooDeep,risk,2000,reason));
    auto aboveBid=plan;aboveBid.order.lmtPrice=85000.25;aboveBid.referencePrice=85000.25;
    assert(!guard.AllowFlatten(command,aboveBid,risk,2000,reason));
    auto injected=plan;injected.order.cashQuantity=20;
    assert(!guard.AllowFlatten(command,injected,risk,2000,reason));
    auto recovered=plan;recovered.expectedPositionQuantity=0.00101;recovered.order.totalQuantity=0.00101;
    assert(guard.AllowFlatten(command,recovered,risk,2000,reason));
    recovered.order.totalQuantity+=0.00000001;
    assert(!guard.AllowFlatten(command,recovered,risk,2000,reason));
}

void TestActiveLeaseAuditPreservesHeldFxButCannotReleaseFence()
{
    if (::geteuid()==0 || ::getegid()==0)return;
    const auto config=MixedCryptoProfileForTest();std::string reason;
    std::string path = FixtureTemporaryPath("hepta-active-crypto-lease");
    int fd=::mkstemp(&path[0]);assert(fd>=0);::close(fd);
    OmsJournal journal;assert(journal.Init(path.c_str()));ExecutionCoordinatorCallbacks callbacks;
    ExecutionCoordinator coordinator(journal,callbacks);
    IBAuthoritativeRecoveryAuditSnapshot s;
    s.active.complete=true;s.active.connectionEpoch=1;s.active.generation=1;
    s.terminal.complete=true;s.terminal.connectionEpoch=1;s.terminal.generation=1;
    s.risk.complete=true;s.risk.coherentRefreshComplete=true;s.risk.accountComplete=true;
    s.risk.positionsComplete=true;s.risk.fxCashComplete=true;s.risk.connectionEpoch=1;
    s.risk.generation=1;s.risk.accountGeneration=1;s.risk.positionsGeneration=1;s.risk.fxCashGeneration=1;
    s.positionQuantities["EUR.USD"]=-25000;s.risk.grossAbsolutePosition=25000;
    s.newConnectionEpochRequired=true;s.barrierComplete=false;
    IbPaperExecutionPolicyCallbacks policyCallbacks;
    policyCallbacks.nowMs=[] {return OmsJournal::NowEpochMs();};
    policyCallbacks.leaseAuditSnapshot=[&] {return s;};
    policyCallbacks.recoveryAuditSnapshot=[&] {return s;};
    std::shared_ptr<IbPaperKillSwitchReader> guard(new FixedKillSwitchReader(IbPaperKillSwitchState::Engaged));
    IbPaperExecutionPolicyAuthority policy(coordinator,config,policyCallbacks,guard);
    ExecutionControlCommand command;command.context.account=config.account;command.context.executionDomain="PAPER";
    command.context.venue="IB";command.context.agentId="fx-owner";command.context.sessionId="fx-session";
    command.context.toolCallId="lease-continue";
    const auto continued=policy.AuditSessionLease(command);
    assert(continued.status==ExecutionCommandStatus::Accepted);
    assert(continued.reasonCode=="SESSION_LEASE_CONTINUATION_CONFIRMED");
    assert(policy.AuditSessionOwner(command).status==ExecutionCommandStatus::Rejected);
    s.risk.complete=false;assert(policy.AuditSessionLease(command).status==ExecutionCommandStatus::Rejected);
    s.risk.complete=true;s.active.activeOrderIds.insert(73);
    assert(policy.AuditSessionLease(command).status==ExecutionCommandStatus::Rejected);
    s.active.activeOrderIds.clear();coordinator.FenceSessionOwner("fx-owner","fx-session");
    assert(policy.AuditSessionLease(command).status==ExecutionCommandStatus::Rejected);
    assert(coordinator.IsSessionOwnerFenced("fx-owner","fx-session"));
    ::unlink(path.c_str());
}

void TestConfiguredUserOwnerResumeScopesAreIndependent()
{
    if (::geteuid()==0 || ::getegid()==0)return;
    auto config=MixedCryptoProfileForTest();std::string reason,oldCredential,newCredential;
    assert(config.BuildAuthorizationCredential(oldCredential,reason));
    config.userCryptoAgentId="user-agent";config.userCryptoSessionId="crypto-owner";
    assert(config.BuildAuthorizationCredential(newCredential,reason));
    assert(oldCredential!=newCredential);
    auto changed=config;changed.userCryptoSessionId="another-owner";
    std::string changedCredential;assert(changed.BuildAuthorizationCredential(changedCredential,reason));
    assert(changedCredential!=newCredential);
    for(int invalid=0;invalid!=5;++invalid) {
        auto bad=config;
        if(invalid==0)bad.userCryptoAgentId.clear();
        if(invalid==1)bad.userCryptoSessionId.clear();
        if(invalid==2)bad.userCryptoSessionId="../foreign";
        if(invalid==3)bad.userCryptoAgentId=std::string(65,'x');
        if(invalid==4){bad.userQuoteContracts="EUR.USD|EUR|CASH|IDEALPRO|USD";bad.userPrimaryQuoteInstrument="EUR.USD";}
        assert(!bad.Validate(reason));
    }
    std::string path = FixtureTemporaryPath("hepta-scoped-crypto-resume");
    int fd=::mkstemp(&path[0]);assert(fd>=0);::close(fd);
    OmsJournal journal;assert(journal.Init(path.c_str()));ExecutionCoordinatorCallbacks callbacks;
    ExecutionCoordinator coordinator(journal,callbacks);
    IBAuthoritativeRecoveryAuditSnapshot snapshot;
    snapshot.active.complete=true;snapshot.active.connectionEpoch=3;snapshot.active.generation=4;
    snapshot.terminal.complete=true;snapshot.terminal.connectionEpoch=3;snapshot.terminal.generation=5;
    snapshot.risk.complete=true;snapshot.risk.coherentRefreshComplete=true;
    snapshot.risk.accountComplete=true;snapshot.risk.positionsComplete=true;snapshot.risk.fxCashComplete=true;
    snapshot.risk.connectionEpoch=3;snapshot.risk.generation=6;snapshot.risk.accountGeneration=7;
    snapshot.risk.positionsGeneration=8;snapshot.risk.fxCashGeneration=9;snapshot.barrierComplete=true;
    snapshot.risk.grossAbsolutePosition=25000;snapshot.positionQuantities["EUR.USD"]=-25000;
    IbPaperAuthoritativePositionSnapshot btc;btc.complete=true;btc.connectionEpoch=3;btc.generation=8;
    auto fx=btc;fx.quantity=-25000;
    IbPaperExecutionPolicyCallbacks pc;pc.nowMs=[] {return OmsJournal::NowEpochMs();};
    pc.recoveryAuditSnapshot=[&]{return snapshot;};pc.leaseAuditSnapshot=pc.recoveryAuditSnapshot;
    pc.authoritativePosition=[&](const std::string& instrument){return instrument=="BTC.USD"?btc:fx;};
    std::shared_ptr<IbPaperKillSwitchReader> kill(new FixedKillSwitchReader(IbPaperKillSwitchState::Engaged));
    IbPaperExecutionPolicyAuthority policy(coordinator,config,pc,kill);
    ExecutionControlCommand cmd;cmd.context.account=config.account;cmd.context.executionDomain="PAPER";
    cmd.context.venue="IB";cmd.context.agentId="user-agent";cmd.context.sessionId="crypto-owner";
    cmd.context.toolCallId="scoped-resume";
    assert(coordinator.EnterRecoveryOnlyOwner("user-agent","crypto-owner",1,reason));
    assert(coordinator.EnterRecoveryOnlyOwner("user-agent","fx-owner",1,reason));
    auto audit=policy.AuditSessionOwner(cmd);
    assert(audit.status==ExecutionCommandStatus::Accepted && audit.ownerAuditAuthoritative && audit.ownerAuditComplete);
    assert(audit.reasonCode=="SESSION_CRYPTO_OWNER_ZERO_CONFIRMED");
    assert(audit.brokerPositionQuantity=="-25000" && audit.brokerGrossAbsolutePosition=="25000");
    // Scoped resume must never be used as a global recovery/finalization certificate.
    auto global=cmd;global.recoveryIngressFence=1;
    assert(policy.RecoveryAuditOwner(global).reasonCode=="RECOVERY_OWNER_POSITION_NOT_FLAT");
    auto other=cmd;other.context.sessionId="fx-owner";
    assert(policy.AuditSessionOwner(other).reasonCode=="FX_OWNER_POSITION_NOT_FLAT");
    other=cmd;other.context.agentId="another-agent";
    assert(policy.AuditSessionOwner(other).status==ExecutionCommandStatus::Rejected);
    other=cmd;other.context.account="DU999999";
    assert(policy.AuditSessionOwner(other).status==ExecutionCommandStatus::Rejected);
    for(int invalid=0;invalid!=8;++invalid) {
        auto previous=snapshot;auto priorBtc=btc;auto priorFx=fx;
        if(invalid==0)btc.complete=false;
        if(invalid==1)btc.connectionEpoch++;
        if(invalid==2)btc.generation++;
        if(invalid==3)fx.generation++;
        if(invalid==4)fx.quantity=-25001;
        if(invalid==5){snapshot.positionQuantities["OTHER"]=1;snapshot.risk.grossAbsolutePosition=25001;}
        if(invalid==6)snapshot.terminal.complete=false;
        if(invalid==7)snapshot.postFillRiskReconciliationPending=true;
        assert(policy.AuditSessionOwner(cmd).status==ExecutionCommandStatus::Rejected);
        assert(policy.ReleaseSessionOwnerFence(cmd).status==ExecutionCommandStatus::Rejected);
        assert(coordinator.IsSessionOwnerRecoveryOnly("user-agent","crypto-owner"));
        snapshot=previous;btc=priorBtc;fx=priorFx;
    }
    btc.quantity=0.0001;snapshot.positionQuantities["BTC.USD"]=btc.quantity;
    snapshot.risk.grossAbsolutePosition=25000.0001;
    assert(policy.AuditSessionOwner(cmd).reasonCode=="CRYPTO_OWNER_POSITION_NOT_FLAT");
    assert(policy.ReleaseSessionOwnerFence(cmd).status==ExecutionCommandStatus::Rejected);
    btc.quantity=0;snapshot.positionQuantities.erase("BTC.USD");snapshot.risk.grossAbsolutePosition=25000;
    snapshot.active.activeOrderIds.insert(91);
    assert(policy.ReleaseSessionOwnerFence(cmd).status==ExecutionCommandStatus::Rejected);
    snapshot.active.activeOrderIds.clear();
    auto released=policy.ReleaseSessionOwnerFence(cmd);
    assert(released.status==ExecutionCommandStatus::Accepted && released.reasonCode=="SESSION_OWNER_FENCE_RELEASED");
    assert(!coordinator.IsSessionOwnerRecoveryOnly("user-agent","crypto-owner"));
    assert(coordinator.IsSessionOwnerRecoveryOnly("user-agent","fx-owner"));
    assert(snapshot.positionQuantities.at("EUR.USD")==-25000);

    // Symmetric FX-owner resume: EUR is flat while the isolated BTC owner may
    // retain exposure.  The global recovery path must still require all-flat.
    snapshot.positionQuantities.clear();
    snapshot.positionQuantities["BTC.USD"]=0.0001;
    snapshot.risk.grossAbsolutePosition=0.0001;
    btc.quantity=0.0001;fx.quantity=0.0;
    auto fxOwner=cmd;fxOwner.context.sessionId="fx-owner";fxOwner.context.toolCallId="scoped-fx-resume";
    const auto fxAudit=policy.AuditSessionOwner(fxOwner);
    assert(fxAudit.status==ExecutionCommandStatus::Accepted && fxAudit.ownerAuditAuthoritative && fxAudit.ownerAuditComplete);
    assert(fxAudit.reasonCode=="SESSION_FX_OWNER_ZERO_CONFIRMED");
    assert(fxAudit.brokerPositionQuantity=="0.0001" && fxAudit.brokerGrossAbsolutePosition=="0.0001");
    auto fxGlobal=fxOwner;fxGlobal.recoveryIngressFence=1;
    assert(policy.RecoveryAuditOwner(fxGlobal).reasonCode=="RECOVERY_OWNER_POSITION_NOT_FLAT");
    const auto fxReleased=policy.ReleaseSessionOwnerFence(fxOwner);
    assert(fxReleased.status==ExecutionCommandStatus::Accepted && fxReleased.reasonCode=="SESSION_OWNER_FENCE_RELEASED");
    assert(!coordinator.IsSessionOwnerRecoveryOnly("user-agent","fx-owner"));

    int mutations=0;
    journal.Replay([&](const OmsJournalEvent& e){if(e.eventType=="place_send_attempt"||e.eventType=="flatten_send_attempt"||e.eventType=="cancel_send_attempt")++mutations;});
    assert(mutations==0);::unlink(path.c_str());
}



void TestAccountNewEntryFenceRequiresConfiguredNativeCustodian()
{
    if (::geteuid()==0 || ::getegid()==0) return;
    const std::string path=FixtureTemporaryPath("account-native-fence");
    OmsJournal journal;assert(journal.Init(path));
    ExecutionCoordinatorCallbacks callbacks;
    ExecutionCoordinator coordinator(journal,callbacks);
    auto config=MixedCryptoProfileForTest();
    config.userCryptoAgentId="user-agent";
    config.userCryptoSessionId="crypto-owner";
    std::string reason;
    assert(config.Validate(reason));
    IbPaperExecutionPolicyCallbacks policyCallbacks;
    int postFenceReads = 0;
    policyCallbacks.beginAccountPostFenceBrokerRead =
        [&](const AgentExecutionContext& owner, std::string& detail) {
            assert(coordinator.IsAccountNewEntryFenced(owner.account));
            assert(coordinator.ObserveAccountLocalDrain(owner).localHotProjectionQuiescent);
            // An independent original-journal replay at the exact callback
            // boundary must find the fsynced account lock already present.
            ExecutionCoordinator replay(journal,callbacks);
            std::string replayReason;
            assert(replay.RecoverFromJournal(replayReason));
            assert(replay.IsAccountNewEntryFenced(owner.account));
            ++postFenceReads;
            detail = postFenceReads == 1 ?
                "FIXTURE_ORIGINAL_BROKER_READ_REQUESTED" :
                "IB_ACCOUNT_POST_FENCE_READ_ALREADY_ATTEMPTED";
            return postFenceReads == 1;
        };
    std::shared_ptr<IbPaperKillSwitchReader> guard(
        new FixedKillSwitchReader(IbPaperKillSwitchState::Engaged));
    IbPaperExecutionPolicyAuthority policy(
        coordinator,config,policyCallbacks,guard);
    ExecutionControlCommand command;
    command.context.agentId="user-agent";
    command.context.sessionId="crypto-owner";
    command.context.account=config.account;
    command.context.venue="IB";
    command.context.executionDomain="PAPER";
    command.context.toolCallId="account-no-new-entry";

    for (int scenario=0;scenario<5;++scenario) {
        auto wrong=command;
        if(scenario==0)wrong.context.agentId="foreign-agent";
        if(scenario==1)wrong.context.sessionId="fx-owner";
        if(scenario==2)wrong.context.account="DU_OTHER";
        if(scenario==3)wrong.context.executionDomain="IB-PAPER:EUR.USD";
        if(scenario==4)wrong.recoveryIngressFence=1;
        const auto denied=policy.FenceAccountNewEntries(wrong);
        assert(denied.status==ExecutionCommandStatus::Rejected);
        assert(denied.reasonCode=="ACCOUNT_NEW_ENTRY_FENCE_OWNER_UNAUTHORIZED" ||
               denied.reasonCode=="IB_PAPER_CONTROL_CONTEXT_INVALID");
        assert(!coordinator.IsAccountNewEntryFenced(config.account));
    }
    assert(postFenceReads==0); // unauthorized sessions never invoke Broker reads
    const auto accepted=policy.FenceAccountNewEntries(command);
    assert(accepted.status==ExecutionCommandStatus::Accepted);
    assert(postFenceReads==1);
    assert(accepted.detail.find(
        "ACCOUNT_POST_FENCE_BROKER_READ_REQUESTED_NOT_STOP_PERMIT:")==0);
    assert(accepted.affectedCount==0);
    assert(accepted.reasonCode=="ACCOUNT_NEW_ENTRY_FENCE_INSTALLED_NOT_STOP_PERMIT");
    assert(coordinator.IsAccountNewEntryFenced(config.account));
    command.context.toolCallId="account-no-new-entry-idempotent";
    const auto repeated=policy.FenceAccountNewEntries(command);
    assert(repeated.status==ExecutionCommandStatus::Accepted);
    assert(postFenceReads==2); // existing one-shot adapter refuses duplicate
    assert(repeated.detail.find("ACCOUNT_POST_FENCE_BROKER_READ_NOT_REQUESTED:")==0);
    assert(repeated.affectedCount==0);
    ExecutionCoordinator restarted(journal,callbacks);
    assert(restarted.RecoverFromJournal(reason));
    assert(restarted.IsAccountNewEntryFenced(config.account));
    ::unlink(path.c_str());
}


IBAuthoritativeRecoveryAuditSnapshot CompleteSameAdapterBrokerFixture()
{
    IBAuthoritativeRecoveryAuditSnapshot sample;
    sample.transportConnected = true;
    sample.eventStreamAuthoritative = true;
    sample.active.complete = true;
    sample.active.connectionEpoch = 7;
    sample.active.generation = 12;
    sample.terminal.complete = true;
    sample.terminal.connectionEpoch = 7;
    sample.terminal.generation = 11;
    sample.terminal.exposureGeneration = 2;
    sample.risk.complete = true;
    sample.risk.coherentRefreshComplete = true;
    sample.risk.accountComplete = true;
    sample.risk.positionsComplete = true;
    sample.risk.fxCashComplete = true;
    sample.risk.connectionEpoch = 7;
    sample.risk.generation = 15;
    sample.risk.riskAbsorbedExposureGeneration = 2;
    sample.risk.grossAbsolutePosition = 0.0;
    sample.exposureGeneration = 2;
    sample.terminalExposureGeneration = 2;
    sample.riskAbsorbedExposureGeneration = 2;
    sample.barrierComplete = true;
    sample.positionQuantities["EUR.USD"] = 0.0;
    sample.positionQuantities["BTC.USD"] = 0.0;
    return sample;
}

void TestSingleBrokerLockEvidenceCannotFabricateGlobalAccountZero()
{
    const auto baseline = CompleteSameAdapterBrokerFixture();
    const auto clean = InspectIbPaperAccountBrokerEvidence(baseline, 701);
    assert(clean.snapshotCoherent && clean.brokerZeroObserved);
    assert(clean.globalActiveOrders == 0 && clean.foreignActiveOrders == 0);
    assert(std::string(clean.reasonCode) ==
        "IB_ACCOUNT_BROKER_ZERO_OBSERVED_NOT_STOP_PERMIT");
    const auto demand = [&](const std::function<void(
        IBAuthoritativeRecoveryAuditSnapshot&)>& corrupt, const char* reason) {
        auto snapshot = baseline;
        corrupt(snapshot);
        const auto observed = InspectIbPaperAccountBrokerEvidence(snapshot, 701);
        assert(!observed.brokerZeroObserved);
        assert(std::string(observed.reasonCode) == reason);
    };
    demand([](IBAuthoritativeRecoveryAuditSnapshot& x){x.transportConnected = false;},
           "IB_ACCOUNT_BROKER_NOT_CONNECTED");
    demand([](IBAuthoritativeRecoveryAuditSnapshot& x){x.eventStreamAuthoritative = false;},
           "IB_ACCOUNT_BROKER_EVENT_STREAM_UNTRUSTED");
    demand([](IBAuthoritativeRecoveryAuditSnapshot& x){x.active.complete = false;},
           "IB_ACCOUNT_BROKER_SNAPSHOT_INCOMPLETE");
    demand([](IBAuthoritativeRecoveryAuditSnapshot& x){x.terminal.complete = false;},
           "IB_ACCOUNT_BROKER_SNAPSHOT_INCOMPLETE");
    demand([](IBAuthoritativeRecoveryAuditSnapshot& x){x.risk.positionsComplete = false;},
           "IB_ACCOUNT_BROKER_SNAPSHOT_INCOMPLETE");
    demand([](IBAuthoritativeRecoveryAuditSnapshot& x){x.risk.fxCashComplete = false;},
           "IB_ACCOUNT_BROKER_SNAPSHOT_INCOMPLETE");
    demand([](IBAuthoritativeRecoveryAuditSnapshot& x){x.risk.coherentRefreshComplete = false;},
           "IB_ACCOUNT_BROKER_SNAPSHOT_INCOMPLETE");
    demand([](IBAuthoritativeRecoveryAuditSnapshot& x){x.active.generation = 0;},
           "IB_ACCOUNT_BROKER_SNAPSHOT_INCOMPLETE");
    demand([](IBAuthoritativeRecoveryAuditSnapshot& x){x.terminal.connectionEpoch = 8;},
           "IB_ACCOUNT_BROKER_CONNECTION_EPOCH_MISMATCH");
    demand([](IBAuthoritativeRecoveryAuditSnapshot& x){x.risk.connectionEpoch = 6;},
           "IB_ACCOUNT_BROKER_CONNECTION_EPOCH_MISMATCH");
    demand([](IBAuthoritativeRecoveryAuditSnapshot& x){x.active.activeOrderIds.insert(146);},
           "IB_ACCOUNT_BROKER_CLIENT_IDENTITY_INCOMPLETE");
    demand([](IBAuthoritativeRecoveryAuditSnapshot& x){x.active.activeOrderIds.insert(146);
                       x.active.activeOrderClientIdsByOrderId[146] = -1;},
           "IB_ACCOUNT_BROKER_CLIENT_IDENTITY_INCOMPLETE");
    // A manual TWS order may be unbound with API orderId=0. Its existence
    // must not become a false empty account just because there is no local
    // numeric order identity (permId is a separate Broker-wide identity).
    demand([](IBAuthoritativeRecoveryAuditSnapshot& x){x.active.activeOrderIds.insert(0);
                       x.active.activeOrderClientIdsByOrderId[0] = 0;},
           "IB_ACCOUNT_BROKER_CLIENT_IDENTITY_INCOMPLETE");
    demand([](IBAuthoritativeRecoveryAuditSnapshot& x){x.active.activeOrderIds.insert(146);
                       x.active.activeOrderClientIdsByOrderId[146] = 999;
                       x.active.activeOrderIdsByCorrelation["foreign-H1"] = 146;},
           "IB_ACCOUNT_BROKER_CORRELATION_SCOPE_INVALID");
    demand([](IBAuthoritativeRecoveryAuditSnapshot& x){x.barrierComplete = false;},
           "IB_ACCOUNT_BROKER_RECOVERY_BARRIER_INCOMPLETE");
    demand([](IBAuthoritativeRecoveryAuditSnapshot& x){x.newConnectionEpochRequired = true;},
           "IB_ACCOUNT_BROKER_RECOVERY_BARRIER_INCOMPLETE");
    demand([](IBAuthoritativeRecoveryAuditSnapshot& x){x.postFillRiskReconciliationPending = true;},
           "IB_ACCOUNT_BROKER_POSTFILL_RECONCILIATION_PENDING");
    demand([](IBAuthoritativeRecoveryAuditSnapshot& x){x.riskAbsorbedExposureGeneration = 1;},
           "IB_ACCOUNT_BROKER_EXPOSURE_GENERATION_MISMATCH");
    demand([](IBAuthoritativeRecoveryAuditSnapshot& x){x.terminalExposureGeneration = 1;},
           "IB_ACCOUNT_BROKER_EXPOSURE_GENERATION_MISMATCH");
    demand([](IBAuthoritativeRecoveryAuditSnapshot& x){x.positionQuantities["EUR.USD"] =
                           std::numeric_limits<double>::quiet_NaN();},
           "IB_ACCOUNT_BROKER_POSITION_INVALID");
    demand([](IBAuthoritativeRecoveryAuditSnapshot& x){x.risk.grossAbsolutePosition = -0.01;},
           "IB_ACCOUNT_BROKER_POSITION_INVALID");
    demand([](IBAuthoritativeRecoveryAuditSnapshot& x){x.positionQuantities["BTC.USD"] = 0.001;},
           "IB_ACCOUNT_BROKER_POSITION_REMAINS");
    demand([](IBAuthoritativeRecoveryAuditSnapshot& x){x.positionQuantities["BTC.USD"] = 1e-12;},
           "IB_ACCOUNT_BROKER_POSITION_REMAINS");
    demand([](IBAuthoritativeRecoveryAuditSnapshot& x){x.risk.grossAbsolutePosition = 0.001;},
           "IB_ACCOUNT_BROKER_POSITION_REMAINS");
    demand([](IBAuthoritativeRecoveryAuditSnapshot& x){x.active.activeOrderIds.insert(146);
                       x.active.activeOrderClientIdsByOrderId[146] = 999;},
           "IB_ACCOUNT_BROKER_ACTIVE_ORDERS_REMAIN");
    auto foreign = baseline;
    foreign.active.activeOrderIds.insert(281);
    foreign.active.activeOrderClientIdsByOrderId[281] = 999;
    const auto foreignObs = InspectIbPaperAccountBrokerEvidence(foreign, 701);
    assert(foreignObs.snapshotCoherent && !foreignObs.brokerZeroObserved);
    assert(foreignObs.foreignActiveOrders == 1);
    auto original = baseline;
    original.active.activeOrderIds.insert(290);
    original.active.activeOrderClientIdsByOrderId[290] = 701;
    original.active.activeOrderIdsByCorrelation["own-correlation"] = 290;
    const auto originalObs = InspectIbPaperAccountBrokerEvidence(original, 701);
    assert(originalObs.snapshotCoherent && !originalObs.brokerZeroObserved);
    assert(originalObs.foreignActiveOrders == 0);
}

void TestObservationProfileCannotBorrowTradingAuthority()
{
    if (::geteuid()==0 || ::getegid()==0) return;
    auto normal=MixedCryptoProfileForTest();
    std::string reason, tradeCredential, observeCredential;
    assert(normal.BuildAuthorizationCredential(tradeCredential,reason));
    auto observation=normal;observation.observationOnly=true;
    assert(observation.Validate(reason));
    assert(observation.BuildAuthorizationCredential(observeCredential,reason));
    assert(observeCredential.find("PAPER-OBSERVATION-V1:sha256:")==0);
    assert(observeCredential!=tradeCredential);
    auto other=observation;other.stateDirectory+="-another-cycle";
    std::string otherCredential;assert(other.BuildAuthorizationCredential(otherCredential,reason));
    assert(otherCredential!=observeCredential);
    other=observation;other.userCryptoAgentId="trade-owner";other.userCryptoSessionId="trade-session";
    assert(!other.Validate(reason));
    std::shared_ptr<IbPaperKillSwitchReader> disarmed(new FixedKillSwitchReader(IbPaperKillSwitchState::Disarmed));
    IbPaperExecutionGuard guard(observation,disarmed);
    IbPaperAuthoritativeRiskSnapshot risk;risk.complete=true;
    IbPlaceOrderCommand place;IbCancelOrderCommand cancel;
    FlattenPositionCommand flatten;AuthoritativeFlattenPlan plan;
    assert(!guard.AllowPlace(place,risk,1000,reason)&&reason=="IB_OBSERVATION_ONLY");
    assert(!guard.AllowPlaceAtAuthoritativePrice(place,risk,100,1000,reason)&&reason=="IB_OBSERVATION_ONLY");
    assert(!guard.AllowCancel(cancel,reason)&&reason=="IB_OBSERVATION_ONLY");
    assert(!guard.AllowFlatten(flatten,plan,risk,1000,reason)&&reason=="IB_OBSERVATION_ONLY");
    auto values=QualificationValues();values["HEPTA_IB_PAPER_OBSERVATION_ONLY"]="true";
    IbPaperExecutionProfileConfig invalid;
    assert(!IbPaperExecutionProfileConfig::FromValues(values,invalid,reason));
    values["HEPTA_IB_PAPER_OBSERVATION_ONLY"]="1";
    assert(!IbPaperExecutionProfileConfig::FromValues(values,invalid,reason)); // no external-canary combination
    values["HEPTA_IB_EXECUTION_MODE"]="DISABLED";
    assert(!IbPaperExecutionProfileConfig::FromValues(values,invalid,reason));
}

int main()
{
    TestSingleBrokerLockEvidenceCannotFabricateGlobalAccountZero();
    TestObservationProfileCannotBorrowTradingAuthority();
    TestAccountNewEntryFenceRequiresConfiguredNativeCustodian();
    TestFxCashRestartCatchUpZeroFillApiCancellation();
    TestFxCashRestartCatchUpBindsReusedOrderIdToPostCheckpointSend();
    TestFxCashRestartCatchUpFailsClosedOnAmbiguousOrIncompleteEvidence();
    TestRuntimeColdConnectBudgetDefaultsFailClosed();
    TestProductionIdentityUsesSelectedCustody();
    TestUserPaperCustodyIsExplicitAndBounded();
    TestMixedCryptoRuntimeParsesRealEnvironmentFields();
    TestCryptoGuardHasIndependentUnitsAndNoSpotShorts();
    TestCryptoFlattenIsExactOwnedLongAndMarketIOC();
    TestLocalFxFullReductionAboveStaleNotionalCap();
    TestActiveLeaseAuditPreservesHeldFxButCannotReleaseFence();
    TestConfiguredUserOwnerResumeScopesAreIndependent();
    TestActiveUserRecoveryMarkerResumesButTerminalOwnerNeverDoes();
    TestAuditedOwnerReleaseIsDurableAndDoesNotInstallRecoveryIngress();
    TestPolicyGateClosesExitAdmissionWhenOriginalCohortDrains();
    TestSlowPlaceAllowsConfirmedCancelAndPreservesRecovery();
    TestSlowPlaceAllowsIndependentFlattenAndRetainsScopes();
    TestCompletedFlattenCannotSpendStaleEntryBudget();
    TestPolicyControlsDoNotWaitBehindVenueDispatch();
    TestQualificationProfileIsDistinctAndBounded();
    TestQualificationProfileRejectsWiderOrAmbiguousAuthority();
    TestQualificationEnvelopeAlwaysHasAnAtomicFlattenPath();
    std::cout << "ib paper execution profile tests passed\n";
    return 0;
}
