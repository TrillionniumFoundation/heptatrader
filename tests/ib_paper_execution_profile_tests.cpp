#include "execution/ib_paper_execution_profile.h"
#include "execution/ib_paper_execution_runtime_config.h"

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
    values["HEPTA_IB_PAPER_MAX_ORDER_QTY"] = "25000";
    values["HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL"] = "35000";
    values["HEPTA_IB_PAPER_MAX_GROSS_POSITION"] = "25000";
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
        {"HEPTA_IB_PAPER_MAX_ORDER_QTY", "25001"},
        {"HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL", "35001"},
        {"HEPTA_IB_PAPER_MAX_GROSS_POSITION", "25001"},
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
    char path[] = "/tmp/hepta-owner-release-XXXXXX";
    const int fd = ::mkstemp(path); assert(fd >= 0); ::close(fd);
    OmsJournal journal; assert(journal.Init(path));
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
    ::unlink(path);
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
    char path[] = "/tmp/hepta-user-recovery-resume-XXXXXX";
    const int fd = ::mkstemp(path); assert(fd >= 0); ::close(fd);
    OmsJournal journal; assert(journal.Init(path));
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
    ::unlink(path);
}

void TestPolicyControlsDoNotWaitBehindVenueDispatch()
{
    IbPaperExecutionProfileConfig config; std::string reason;
    assert(IbPaperExecutionProfileConfig::FromValues(QualificationValues(), config, reason));
    char path[] = "/tmp/hepta-policy-control-XXXXXX";
    const int fd = ::mkstemp(path); assert(fd >= 0); ::close(fd);
    OmsJournal journal; assert(journal.Init(path));
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
    assert(fence.get().status == ExecutionCommandStatus::Accepted);
    const auto release = policy.ReleaseSessionOwnerFence(control);
    assert(release.status == ExecutionCommandStatus::Rejected);
    assert(release.reasonCode == "IB_PAPER_CONTROL_BUSY");
    assert(coordinator.IsSessionOwnerFenced(place.context.agentId, place.context.sessionId));
    { std::lock_guard<std::mutex> lock(mutex); released = true; changed.notify_all(); }
    assert(pending.get().status == ExecutionCommandStatus::Accepted);
    ::unlink(path);
}

}

int main()
{
    TestRuntimeColdConnectBudgetDefaultsFailClosed();
    TestProductionIdentityUsesSelectedCustody();
    TestUserPaperCustodyIsExplicitAndBounded();
    TestActiveUserRecoveryMarkerResumesButTerminalOwnerNeverDoes();
    TestAuditedOwnerReleaseIsDurableAndDoesNotInstallRecoveryIngress();
    TestPolicyControlsDoNotWaitBehindVenueDispatch();
    TestQualificationProfileIsDistinctAndBounded();
    TestQualificationProfileRejectsWiderOrAmbiguousAuthority();
    TestQualificationEnvelopeAlwaysHasAnAtomicFlattenPath();
    std::cout << "ib paper execution profile tests passed\n";
    return 0;
}
