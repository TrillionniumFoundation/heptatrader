#include "execution/ib_paper_execution_profile.h"
#include "oms_journal.h"

#include <cassert>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <fcntl.h>
#include <iostream>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>
#include <sys/stat.h>
#include <unistd.h>

namespace
{
class MutableKillSwitch final : public IbPaperKillSwitchReader
{
public:
    std::atomic<IbPaperKillSwitchState> state{IbPaperKillSwitchState::Disarmed};

    IbPaperKillSwitchObservation Observe() const override
    {
        IbPaperKillSwitchObservation result;
        result.state = state.load();
        if (result.state == IbPaperKillSwitchState::Engaged)
            result.reasonCode = "IB_PAPER_KILL_SWITCH_ENGAGED";
        else if (result.state == IbPaperKillSwitchState::Uncertain)
            result.reasonCode = "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN";
        return result;
    }
};

class SequencedKillSwitch final : public IbPaperKillSwitchReader
{
public:
    IbPaperKillSwitchObservation Observe() const override
    {
        IbPaperKillSwitchObservation result;
        if (observations.fetch_add(1) == 0)
            result.state = IbPaperKillSwitchState::Disarmed;
        else
        {
            result.state = IbPaperKillSwitchState::Engaged;
            result.reasonCode = "IB_PAPER_KILL_SWITCH_ENGAGED";
        }
        return result;
    }

private:
    mutable std::atomic<int> observations{0};
};

std::string MakeTempDirectory()
{
    char pattern[] = "/tmp/hepta-ib-paper-profile-XXXXXX";
    char* path = ::mkdtemp(pattern);
    assert(path != nullptr);
    return path;
}

void WritePrivateFile(const std::string& path, const std::string& value)
{
    const int fd = ::open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
    assert(fd >= 0);
    assert(::write(fd, value.data(), value.size()) == static_cast<ssize_t>(value.size()));
    assert(::fsync(fd) == 0);
    assert(::fchmod(fd, 0400) == 0);
    assert(::close(fd) == 0);
}

std::string AuthorizationValue(const IbPaperExecutionProfileConfig& config)
{
    std::string value;
    std::string reason;
    assert(config.BuildAuthorizationCredential(value, reason));
    assert(value.compare(0, 16, "PAPER-V3:sha256:") == 0);
    assert(value.size() == 80);
    return value;
}

std::map<std::string, std::string> ValidValues(
    const std::string& stateDirectory, const std::string& credentialsDirectory)
{
    std::map<std::string, std::string> values;
    values["HEPTA_IB_EXECUTION_MODE"] = "PAPER";
    values["HEPTA_IB_PAPER_ACCOUNT"] = "DU123456";
    values["HEPTA_IB_PAPER_HOST"] = "127.0.0.1";
    values["HEPTA_IB_PAPER_PORT"] = "7497";
    values["HEPTA_IB_PAPER_CLIENT_ID"] = "701";
    values["HEPTA_IB_PAPER_MAX_ORDER_QTY"] = "1000";
    values["HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL"] = "250000";
    values["HEPTA_IB_PAPER_MAX_ORDERS_PER_MINUTE"] = "2";
    values["HEPTA_IB_PAPER_MAX_ACTIVE_ORDERS"] = "3";
    values["HEPTA_IB_PAPER_MAX_GROSS_POSITION"] = "5000";
    values["HEPTA_IB_PAPER_CONTROL_DIRECTORY"] =
        "/run/hepta/ib-paper-control";
    values["STATE_DIRECTORY"] = stateDirectory;
    values["CREDENTIALS_DIRECTORY"] = credentialsDirectory;
    return values;
}

std::map<std::string, std::string> ExternalLimitValues(
    const std::string& stateDirectory,
    const std::string& credentialsDirectory)
{
    std::map<std::string, std::string> values =
        ValidValues(stateDirectory, credentialsDirectory);
    values["HEPTA_EXECUTION_EXTERNAL_P1_CANARY_LMT_DAY"] = "1";
    values["HEPTA_EXECUTION_MAX_ORDER_NOTIONAL"] = "5000";
    values["HEPTA_IB_PAPER_MAX_ORDER_QTY"] = "1";
    values["HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL"] = "5000";
    values["HEPTA_IB_PAPER_MAX_ACTIVE_ORDERS"] = "1";
    values["HEPTA_IB_PAPER_MAX_GROSS_POSITION"] = "1";
    values["HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS"] = "5000";
    return values;
}

IbPlaceOrderCommand Place(double quantity = 100.0, double price = 10.0)
{
    IbPlaceOrderCommand command;
    command.context.agentId = "agent";
    command.context.sessionId = "session";
    command.context.toolCallId = "place";
    command.context.account = "DU123456";
    command.context.venue = "IB";
    command.context.executionDomain = "PAPER";
    command.contract.symbol = "EUR";
    command.contract.secType = "CASH";
    command.contract.exchange = "IDEALPRO";
    command.contract.currency = "USD";
    command.instrument = "EUR.USD";
    command.order.action = "BUY";
    command.order.orderType = "MKT";
    command.order.lmtPrice = 0.0;
    command.order.totalQuantity = quantity;
    command.timeInForce = "DAY";
    command.referencePrice = price;
    command.expiresAtMs = 9999999999999LL;
    return command;
}

IbPlaceOrderCommand ExternalLimitPlace(
    const std::string& side = "BUY", double quantity = 1.0)
{
    IbPlaceOrderCommand command = Place(quantity, 10.1);
    command.order.action = side;
    command.order.orderType = "LMT";
    command.order.lmtPrice = side == "SELL" ? 9.9 : 10.1;
    command.referencePrice = command.order.lmtPrice;
    return command;
}

MarketQuoteSnapshot FreshQuote(std::int64_t now)
{
    MarketQuoteSnapshot quote;
    quote.subscriptionId = "IB:1:1:1001";
    quote.instrument = "EUR.USD";
    quote.state = MarketSubscriptionState::Active;
    quote.bid = 9.9;
    quote.ask = 10.1;
    quote.observedAtMs = static_cast<std::uint64_t>(now);
    quote.staleAfterMs = static_cast<std::uint64_t>(now + 5000);
    return quote;
}

bool AuthoritativeContract(const std::string& instrument,
                           InstrumentRef& contract)
{
    if (instrument != "EUR.USD")
        return false;
    contract = Place().contract;
    return true;
}

IbCancelOrderCommand Cancel()
{
    IbCancelOrderCommand command;
    command.context = Place().context;
    command.context.toolCallId = "cancel";
    command.orderId = 42;
    return command;
}

ExecutionControlCommand RecoveryOwnerAudit(
    const AgentExecutionContext& context,
    const std::string& commandId)
{
    ExecutionControlCommand command;
    command.context = context;
    command.context.toolCallId = commandId;
    command.recoveryIngressFence = 1;
    return command;
}

class RecoveryOwnerAuditFixture
{
public:
    RecoveryOwnerAuditFixture()
        : state(MakeTempDirectory()),
          credentials(MakeTempDirectory()),
          journalPath(state + "/oms-owner-audit-journal.jsonl"),
          killSwitch(new MutableKillSwitch())
    {
        std::string reason;
        assert(IbPaperExecutionProfileConfig::FromValues(
            ValidValues(state, credentials), config, reason));
        assert(journal.Init(journalPath));
        coordinatorCallbacks.placeIbOrderCorrelated =
            [&](const IBContractLite&, const IBOrderLite&,
                const std::string& correlation, long* orderId) {
                lastCorrelation = correlation;
                if (throwAfterDispatch)
                    throw std::runtime_error("broker outcome unavailable");
                *orderId = nextOrderId++;
                return true;
            };
        coordinatorCallbacks.validateDecisionLease =
            [](const AgentExecutionContext&, const std::string&,
               std::string*) { return true; };
        coordinatorCallbacks.onIbOrderPlaced =
            [](const IbPlaceOrderCommand&, long, std::string*) {
                return true;
            };
        coordinator.reset(new ExecutionCoordinator(
            journal, coordinatorCallbacks));
        policyCallbacks.riskSnapshot = [&]() { return risk; };
        policyCallbacks.correlationSnapshot = [&]() { return active; };
        policyCallbacks.terminalCorrelationSnapshot =
            [&]() { return terminal; };
        policyCallbacks.recoveryAuditSnapshot = [&]() {
            IBAuthoritativeRecoveryAuditSnapshot snapshot;
            snapshot.active = active;
            snapshot.terminal = terminal;
            snapshot.risk = recoveryRisk;
            snapshot.positionQuantities = recoveryPositions;
            snapshot.postFillRiskReconciliationPending = postFillPending;
            snapshot.exposureGeneration = exposureGeneration;
            snapshot.terminalExposureGeneration =
                terminalExposureGeneration;
            snapshot.riskAbsorbedExposureGeneration =
                riskAbsorbedExposureGeneration;
            snapshot.barrierComplete = barrierComplete;
            snapshot.newConnectionEpochRequired =
                newConnectionEpochRequired;
            snapshot.reasonCode = recoveryAuditReason;
            return snapshot;
        };
        policyCallbacks.nowMs = [&]() { return now; };
        policyCallbacks.authoritativeQuote =
            [&](const std::string&) { return FreshQuote(now); };
        policy.reset(new IbPaperExecutionPolicyAuthority(
            *coordinator, config, policyCallbacks, killSwitch));
        risk.complete = true;
        CompleteSnapshots(41, 7, 11);
    }

    ~RecoveryOwnerAuditFixture()
    {
        policy.reset();
        coordinator.reset();
        assert(::unlink(journalPath.c_str()) == 0);
        assert(::rmdir(credentials.c_str()) == 0);
        assert(::rmdir(state.c_str()) == 0);
    }

    void CompleteSnapshots(std::uint64_t epoch,
                           std::uint64_t activeGeneration,
                           std::uint64_t terminalGeneration)
    {
        active = IBAuthoritativeCorrelationSnapshot();
        active.complete = true;
        active.connectionEpoch = epoch;
        active.generation = activeGeneration;
        terminal = IBAuthoritativeTerminalCorrelationSnapshot();
        terminal.complete = true;
        terminal.connectionEpoch = epoch;
        terminal.generation = terminalGeneration;
        terminal.exposureGeneration = terminalExposureGeneration;
        recoveryRisk = IBAuthoritativeRiskSnapshot();
        recoveryRisk.complete = true;
        recoveryRisk.coherentRefreshComplete = true;
        recoveryRisk.connectionEpoch = epoch;
        recoveryRisk.generation = 13;
        recoveryRisk.accountGeneration = 12;
        recoveryRisk.positionsGeneration = 13;
        recoveryRisk.fxCashGeneration = 12;
        recoveryRisk.accountComplete = true;
        recoveryRisk.positionsComplete = true;
        recoveryRisk.fxCashComplete = true;
        recoveryRisk.riskAbsorbedExposureGeneration =
            riskAbsorbedExposureGeneration;
        recoveryRisk.grossAbsolutePosition = 0.0;
        recoveryPositions.clear();
        barrierComplete = true;
        newConnectionEpochRequired = false;
        postFillPending = false;
        recoveryAuditReason.clear();
    }

    IbPlaceOrderCommand PlaceForOwner(const std::string& commandId)
    {
        IbPlaceOrderCommand command = Place();
        command.context.toolCallId = commandId;
        command.context.decisionLeaseFencingToken = 7;
        command.context.decisionLeaseGeneration = 9;
        return command;
    }

    ExecutionControlResult Audit(const AgentExecutionContext& context,
                                 const std::string& commandId)
    {
        return policy->RecoveryAuditOwner(
            RecoveryOwnerAudit(context, commandId));
    }

    std::string state;
    std::string credentials;
    std::string journalPath;
    IbPaperExecutionProfileConfig config;
    OmsJournal journal;
    std::shared_ptr<MutableKillSwitch> killSwitch;
    ExecutionCoordinatorCallbacks coordinatorCallbacks;
    std::unique_ptr<ExecutionCoordinator> coordinator;
    IbPaperExecutionPolicyCallbacks policyCallbacks;
    std::unique_ptr<IbPaperExecutionPolicyAuthority> policy;
    IbPaperAuthoritativeRiskSnapshot risk;
    IBAuthoritativeCorrelationSnapshot active;
    IBAuthoritativeTerminalCorrelationSnapshot terminal;
    IBAuthoritativeRiskSnapshot recoveryRisk;
    std::map<std::string, double> recoveryPositions;
    std::uint64_t exposureGeneration = 0;
    std::uint64_t terminalExposureGeneration = 0;
    std::uint64_t riskAbsorbedExposureGeneration = 0;
    bool postFillPending = false;
    bool barrierComplete = true;
    bool newConnectionEpochRequired = false;
    std::string recoveryAuditReason;
    std::int64_t now = 100000;
    long nextOrderId = 701;
    bool throwAfterDispatch = false;
    std::string lastCorrelation;
};

FlattenPositionCommand Flatten()
{
    FlattenPositionCommand command;
    command.context = Place().context;
    command.context.toolCallId = "flatten";
    command.context.decisionLeaseFencingToken = 7;
    command.context.decisionLeaseGeneration = 9;
    command.contract = Place().contract;
    command.instrument = Place().instrument;
    command.hasAuthoritativePreviewSnapshot = true;
    command.previewPositionQuantity = 100.0;
    command.previewPositionConnectionEpoch = 11;
    command.previewPositionGeneration = 12;
    return command;
}

void TestDefaultHardOffAndStrictConfiguration()
{
    IbPaperExecutionProfileConfig config;
    std::string reason;
    assert(IbPaperExecutionProfileConfig::FromValues(
        std::map<std::string, std::string>(), config, reason));
    assert(!config.enabled);

    const std::string state = MakeTempDirectory();
    const std::string credentials = MakeTempDirectory();
    std::map<std::string, std::string> values = ValidValues(state, credentials);
    assert(IbPaperExecutionProfileConfig::FromValues(values, config, reason));
    assert(config.enabled);
    assert(config.controlDirectory == "/run/hepta/ib-paper-control");

    values = ValidValues(state, credentials);
    values["HEPTA_IB_PAPER_CONTROL_DIRECTORY"] =
        "/run/hepta/ib-paper-control-codex-a";
    assert(IbPaperExecutionProfileConfig::FromValues(
        values, config, reason));
    values["HEPTA_IB_PAPER_CONTROL_DIRECTORY"] =
        "/run/hepta/ib-paper-control-Codex-A";
    assert(!IbPaperExecutionProfileConfig::FromValues(
        values, config, reason));
    assert(reason == "IB_PAPER_CONTROL_DIRECTORY_INVALID");

    values = ValidValues(state, credentials);
    values["HEPTA_IB_EXECUTION_MODE"] = "LIVE";
    assert(!IbPaperExecutionProfileConfig::FromValues(values, config, reason));
    assert(reason == "IB_PAPER_MODE_UNSUPPORTED");
    values = ValidValues(state, credentials);
    values.erase("HEPTA_IB_PAPER_CONTROL_DIRECTORY");
    assert(!IbPaperExecutionProfileConfig::FromValues(values, config, reason));
    assert(reason == "IB_PAPER_CONTROL_DIRECTORY_INVALID");
    values = ValidValues(state, credentials);
    values["HEPTA_IB_PAPER_CONTROL_DIRECTORY"] = state + "/control";
    assert(!IbPaperExecutionProfileConfig::FromValues(values, config, reason));
    assert(reason == "IB_PAPER_CONTROL_DIRECTORY_INVALID");
    values = ValidValues(state, credentials);
    values["HEPTA_IB_PAPER_ACCOUNT"] = "U123456";
    assert(!IbPaperExecutionProfileConfig::FromValues(values, config, reason));
    assert(reason == "IB_PAPER_ACCOUNT_REQUIRED");
    values = ValidValues(state, credentials);
    values["HEPTA_IB_PAPER_HOST"] = "192.0.2.1";
    assert(!IbPaperExecutionProfileConfig::FromValues(values, config, reason));
    assert(reason == "IB_PAPER_LOOPBACK_HOST_REQUIRED");
    values = ValidValues(state, credentials);
    values["HEPTA_IB_PAPER_PORT"] = "7496";
    assert(!IbPaperExecutionProfileConfig::FromValues(values, config, reason));
    assert(reason == "IB_PAPER_PORT_INVALID");
    values = ValidValues(state, credentials);
    values["HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL"] = "nan";
    assert(!IbPaperExecutionProfileConfig::FromValues(values, config, reason));
    assert(reason == "IB_PAPER_HARD_LIMITS_INVALID");
    values = ValidValues(state, credentials);
    values["HEPTA_IB_PAPER_MAX_ORDER_QTY"] = "25001";
    assert(!IbPaperExecutionProfileConfig::FromValues(values, config, reason));
    assert(reason == "IB_PAPER_HARD_LIMITS_INVALID");
    values = ValidValues(state, credentials);
    values["HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL"] = "250001";
    assert(!IbPaperExecutionProfileConfig::FromValues(values, config, reason));
    assert(reason == "IB_PAPER_HARD_LIMITS_INVALID");
    values = ValidValues(state, credentials);
    values["HEPTA_IB_PAPER_MAX_ORDERS_PER_MINUTE"] = "31";
    assert(!IbPaperExecutionProfileConfig::FromValues(values, config, reason));
    assert(reason == "IB_PAPER_HARD_LIMITS_INVALID");
    values = ValidValues(state, credentials);
    values["HEPTA_IB_PAPER_MAX_ACTIVE_ORDERS"] = "51";
    assert(!IbPaperExecutionProfileConfig::FromValues(values, config, reason));
    assert(reason == "IB_PAPER_HARD_LIMITS_INVALID");
    values = ValidValues(state, credentials);
    values["HEPTA_IB_PAPER_MAX_GROSS_POSITION"] = "100001";
    assert(!IbPaperExecutionProfileConfig::FromValues(values, config, reason));
    assert(reason == "IB_PAPER_HARD_LIMITS_INVALID");

    assert(::rmdir(credentials.c_str()) == 0);
    assert(::rmdir(state.c_str()) == 0);
}

void TestCanonicalIdealproPaperLimits()
{
    const std::string state = MakeTempDirectory();
    const std::string credentials = MakeTempDirectory();
    std::map<std::string, std::string> values =
        ValidValues(state, credentials);
    values["HEPTA_IB_PAPER_MAX_ORDER_QTY"] = "25000";
    values["HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL"] = "35000";
    values["HEPTA_IB_PAPER_MAX_ACTIVE_ORDERS"] = "1";
    values["HEPTA_IB_PAPER_MAX_GROSS_POSITION"] = "25000";

    IbPaperExecutionProfileConfig config;
    std::string reason;
    assert(IbPaperExecutionProfileConfig::FromValues(
        values, config, reason));
    assert(config.maxOrderQuantity == 25000.0);
    assert(config.maxOrderNotional == 35000.0);
    assert(config.maxActiveOrders == 1);
    assert(config.maxGrossPosition == 25000.0);

    const std::shared_ptr<MutableKillSwitch> killSwitch(
        new MutableKillSwitch());
    IbPaperExecutionGuard guard(config, killSwitch);
    IbPaperAuthoritativeRiskSnapshot snapshot;
    snapshot.complete = true;
    assert(guard.AllowPlaceAtAuthoritativePrice(
        Place(25000.0, 1.10), snapshot, 1.10, 100000, reason));

    assert(!guard.AllowPlace(
        Place(25000.01, 1.10), snapshot, 100001, reason));
    assert(reason == "IB_PAPER_MAX_ORDER_QUANTITY_EXCEEDED");
    assert(!guard.AllowPlaceAtAuthoritativePrice(
        Place(25000.0, 1.10), snapshot, 1.41, 100001, reason));
    assert(reason == "IB_PAPER_MAX_ORDER_NOTIONAL_EXCEEDED");
    snapshot.grossAbsolutePosition = 1.0;
    assert(!guard.AllowPlace(
        Place(25000.0, 1.10), snapshot, 100001, reason));
    assert(reason == "IB_PAPER_MAX_GROSS_POSITION_EXCEEDED");

    assert(::rmdir(credentials.c_str()) == 0);
    assert(::rmdir(state.c_str()) == 0);
}

void TestAuthorizationCredentialIsExactAndPrivate()
{
    const std::string state = MakeTempDirectory();
    const std::string credentials = MakeTempDirectory();
    IbPaperExecutionProfileConfig config;
    std::string reason;
    assert(IbPaperExecutionProfileConfig::FromValues(
        ValidValues(state, credentials), config, reason));
    WritePrivateFile(config.authorizationCredentialPath, "PAPER:DU123456\n");
    assert(!config.VerifyAuthorizationCredential(reason));
    assert(reason == "IB_PAPER_AUTHORIZATION_CREDENTIAL_MISMATCH");
    assert(::unlink(config.authorizationCredentialPath.c_str()) == 0);
    const std::string authorization = AuthorizationValue(config);
    WritePrivateFile(config.authorizationCredentialPath, authorization + "\n");
    assert(config.VerifyAuthorizationCredential(reason));

    const std::string hardlink = credentials + "/authorization-hardlink";
    assert(::link(config.authorizationCredentialPath.c_str(), hardlink.c_str()) == 0);
    assert(!config.VerifyAuthorizationCredential(reason));
    assert(reason == "IB_PAPER_AUTHORIZATION_CREDENTIAL_UNSAFE");
    assert(::unlink(hardlink.c_str()) == 0);
    assert(config.VerifyAuthorizationCredential(reason));

    assert(::chmod(config.authorizationCredentialPath.c_str(), 0644) == 0);
    assert(!config.VerifyAuthorizationCredential(reason));
    assert(reason == "IB_PAPER_AUTHORIZATION_CREDENTIAL_UNSAFE");
    assert(::chmod(config.authorizationCredentialPath.c_str(), 0600) == 0);
    assert(!config.VerifyAuthorizationCredential(reason));
    assert(reason == "IB_PAPER_AUTHORIZATION_CREDENTIAL_UNSAFE");
    assert(::chmod(config.authorizationCredentialPath.c_str(), 0400) == 0);
    IbPaperExecutionProfileConfig changed = config;
    changed.maxOrderQuantity = 999.0;
    assert(!changed.VerifyAuthorizationCredential(reason));
    assert(reason == "IB_PAPER_AUTHORIZATION_CREDENTIAL_MISMATCH");
    changed = config;
    changed.controlDirectory = "/run/hepta/ib-paper-control-V2";
    assert(!changed.VerifyAuthorizationCredential(reason));
    assert(reason == "IB_PAPER_CONTROL_DIRECTORY_INVALID");
    assert(::unlink(config.authorizationCredentialPath.c_str()) == 0);
    WritePrivateFile(config.authorizationCredentialPath, "PAPER-V3:sha256:not-a-hash");
    assert(!config.VerifyAuthorizationCredential(reason));
    assert(reason == "IB_PAPER_AUTHORIZATION_CREDENTIAL_MISMATCH");

    assert(::unlink(config.authorizationCredentialPath.c_str()) == 0);
    assert(::rmdir(credentials.c_str()) == 0);
    assert(::rmdir(state.c_str()) == 0);
}

void TestHardLimitsAndIndependentKillSwitch()
{
    const std::string state = MakeTempDirectory();
    const std::string credentials = MakeTempDirectory();
    IbPaperExecutionProfileConfig config;
    std::string reason;
    assert(IbPaperExecutionProfileConfig::FromValues(
        ValidValues(state, credentials), config, reason));
    const std::shared_ptr<MutableKillSwitch> killSwitch(new MutableKillSwitch());
    IbPaperExecutionGuard guard(config, killSwitch);
    IbPaperAuthoritativeRiskSnapshot snapshot;

    assert(!guard.AllowPlace(Place(), snapshot, 100000, reason));
    assert(reason == "IB_PAPER_AUTHORITATIVE_RISK_SNAPSHOT_REQUIRED");
    snapshot.complete = true;
    assert(guard.AllowPlace(Place(), snapshot, 100000, reason));
    guard.ReplaceSendAttemptTimes(std::vector<std::int64_t>(1, 100000), 100000);

    IbPlaceOrderCommand stock = Place();
    stock.contract.secType = "STK";
    stock.contract.symbol = "AAPL";
    assert(guard.AllowPlace(stock, snapshot, 100000, reason));
    IbPlaceOrderCommand future = Place();
    future.contract.secType = "FUT";
    assert(!guard.AllowPlace(future, snapshot, 100000, reason));
    assert(reason == "IB_PAPER_SECURITY_TYPE_NOT_ALLOWED");
    IbPlaceOrderCommand unknown = Place();
    unknown.contract.secType = "";
    assert(!guard.AllowPlace(unknown, snapshot, 100000, reason));
    assert(reason == "IB_PAPER_SECURITY_TYPE_NOT_ALLOWED");

    assert(!guard.AllowPlace(Place(1001.0), snapshot, 100001, reason));
    assert(reason == "IB_PAPER_MAX_ORDER_QUANTITY_EXCEEDED");
    assert(!guard.AllowPlace(Place(1000.0, 251.0), snapshot, 100001, reason));
    assert(reason == "IB_PAPER_MAX_ORDER_NOTIONAL_EXCEEDED");
    snapshot.activeOrderCount = 3;
    assert(!guard.AllowPlace(Place(), snapshot, 100001, reason));
    assert(reason == "IB_PAPER_MAX_ACTIVE_ORDERS_EXCEEDED");
    snapshot.activeOrderCount = 0;
    snapshot.grossAbsolutePosition = 4950.0;
    assert(!guard.AllowPlace(Place(), snapshot, 100001, reason));
    assert(reason == "IB_PAPER_MAX_GROSS_POSITION_EXCEEDED");
    snapshot.grossAbsolutePosition = 0.0;

    IbPlaceOrderCommand limit = Place();
    limit.order.orderType = "LMT";
    limit.order.lmtPrice = 10.0;
    assert(!guard.AllowPlace(limit, snapshot, 100001, reason));
    assert(reason == "IB_PAPER_MARKET_ORDERS_ONLY");
    IbPlaceOrderCommand residualLimit = Place();
    residualLimit.order.lmtPrice = 10.0;
    assert(!guard.AllowPlace(residualLimit, snapshot, 100001, reason));
    assert(reason == "IB_PAPER_MARKET_ORDER_LIMIT_PRICE_FORBIDDEN");

    assert(guard.AllowPlace(Place(), snapshot, 100001, reason));
    std::vector<std::int64_t> attempts;
    attempts.push_back(100000);
    attempts.push_back(100001);
    guard.ReplaceSendAttemptTimes(attempts, 100001);
    assert(!guard.AllowPlace(Place(), snapshot, 100002, reason));
    assert(reason == "IB_PAPER_ORDER_RATE_EXCEEDED");
    AuthoritativeFlattenPlan emergencyPlan;
    emergencyPlan.expectedPositionQuantity = 100.0;
    emergencyPlan.order.action = "SELL";
    emergencyPlan.order.orderType = "MKT";
    emergencyPlan.order.totalQuantity = 100.0;
    emergencyPlan.order.lmtPrice = 0.0;
    emergencyPlan.timeInForce = "DAY";
    emergencyPlan.referencePrice = 10.0;
    assert(guard.AllowFlatten(
        Flatten(), emergencyPlan, snapshot, 100002, reason));
    assert(guard.AllowPlace(Place(), snapshot, 160001, reason));

    killSwitch->state = IbPaperKillSwitchState::Engaged;
    assert(!guard.AllowPlace(Place(), snapshot, 160002, reason));
    assert(reason == "IB_PAPER_KILL_SWITCH_ENGAGED");
    assert(guard.AllowCancel(Cancel(), reason));

    IbPlaceOrderCommand wrongAccount = Place();
    wrongAccount.context.account = "DUOTHER";
    assert(!guard.AllowPlace(wrongAccount, snapshot, 160002, reason));
    assert(reason == "IB_PAPER_EXECUTION_CONTEXT_MISMATCH");

    killSwitch->state = IbPaperKillSwitchState::Disarmed;
    assert(::rmdir(credentials.c_str()) == 0);
    assert(::rmdir(state.c_str()) == 0);
}

void TestPolicyAuthorityUsesOnlyCompleteServiceOwnedSnapshots()
{
    const std::string state = MakeTempDirectory();
    const std::string credentials = MakeTempDirectory();
    IbPaperExecutionProfileConfig config;
    std::string reason;
    assert(IbPaperExecutionProfileConfig::FromValues(
        ValidValues(state, credentials), config, reason));

    const std::string journalPath = state + "/oms-journal.jsonl";
    OmsJournal journal;
    assert(journal.Init(journalPath));
    int venueSends = 0;
    IbPlaceOrderCommand venueCommand;
    ExecutionCoordinatorCallbacks coordinatorCallbacks;
    coordinatorCallbacks.placeIbOrderCommandCorrelated =
        [&](const IbPlaceOrderCommand& command,
            const std::string&, long* orderId) {
            ++venueSends;
            venueCommand = command;
            *orderId = 501;
            return true;
        };
    coordinatorCallbacks.validateDecisionLease =
        [](const AgentExecutionContext&, const std::string&, std::string*) { return true; };
    coordinatorCallbacks.onIbOrderPlaced =
        [](const IbPlaceOrderCommand&, long, std::string*) { return true; };
    ExecutionCoordinator coordinator(journal, coordinatorCallbacks);

    IbPaperAuthoritativeRiskSnapshot risk;
    risk.complete = true;
    IBAuthoritativeCorrelationSnapshot correlations;
    correlations.complete = false;
    correlations.reasonCode = "IB_CORRELATION_REFRESH_PENDING";
    IBAuthoritativeTerminalCorrelationSnapshot terminal;
    terminal.complete = true;
    std::int64_t now = 100000;
    const std::shared_ptr<MutableKillSwitch> killSwitch(new MutableKillSwitch());
    IbPaperExecutionPolicyCallbacks policyCallbacks;
    policyCallbacks.riskSnapshot = [&]() { return risk; };
    policyCallbacks.correlationSnapshot = [&]() { return correlations; };
    policyCallbacks.terminalCorrelationSnapshot = [&]() { return terminal; };
    policyCallbacks.nowMs = [&]() { return now; };
    policyCallbacks.authoritativeQuote = [&](const std::string&) {
        MarketQuoteSnapshot quote = FreshQuote(now);
        quote.subscriptionId = "IB:41:7:1001";
        return quote;
    };
    IbPaperExecutionPolicyAuthority policy(
        coordinator, config, policyCallbacks, killSwitch);

    IbPlaceOrderCommand command = Place();
    command.context.decisionLeaseFencingToken = 7;
    command.context.decisionLeaseGeneration = 9;
    command.authoritativeQuoteBinding.valid = true;
    command.authoritativeQuoteBinding.instrument = "attacker-supplied";
    command.authoritativeQuoteBinding.subscriptionId = "IB:0:0:0";
    command.authoritativeQuoteBinding.bid = 1.0;
    command.authoritativeQuoteBinding.ask = 2.0;
    command.authoritativeQuoteBinding.observedAtMs = 1;
    command.authoritativeQuoteBinding.staleAfterMs = 2;
    const ExecutionCommandResult localPreview =
        policy.PreviewOrder(command);
    assert(localPreview.status == ExecutionCommandStatus::Accepted);
    assert(localPreview.detail ==
        "{\"source\":\"IB\",\"authoritative\":true,"
        "\"subscription_id\":\"IB:41:7:1001\","
        "\"observed_at_ms\":100000,\"stale_after_ms\":105000,"
        "\"stale\":false,\"risk_approved\":true}");
    const ExecutionCommandResult accepted = policy.PlaceIbOrder(command);
    assert(accepted.status == ExecutionCommandStatus::Accepted);
    assert(accepted.orderId == 501);
    assert(venueSends == 1);
    assert(venueCommand.authoritativeQuoteBinding.valid);
    assert(venueCommand.authoritativeQuoteBinding.instrument == "EUR.USD");
    assert(venueCommand.authoritativeQuoteBinding.subscriptionId ==
           "IB:41:7:1001");
    assert(venueCommand.authoritativeQuoteBinding.bid == 9.9);
    assert(venueCommand.authoritativeQuoteBinding.ask == 10.1);
    assert(venueCommand.authoritativeQuoteBinding.observedAtMs ==
           static_cast<std::uint64_t>(now));
    assert(venueCommand.authoritativeQuoteBinding.staleAfterMs ==
           static_cast<std::uint64_t>(now + 5000));
    IbPlaceOrderCommand exactRetry = command;
    exactRetry.authoritativeQuoteBinding = AuthoritativePlaceQuoteBinding();
    const ExecutionCommandResult duplicate = policy.PlaceIbOrder(exactRetry);
    assert(duplicate.status == ExecutionCommandStatus::Duplicate);
    assert(venueSends == 1);

    killSwitch->state = IbPaperKillSwitchState::Engaged;
    const ExecutionCommandResult duplicateWhileKilled = policy.PlaceIbOrder(command);
    assert(duplicateWhileKilled.status == ExecutionCommandStatus::Duplicate);
    IbPlaceOrderCommand conflict = command;
    conflict.order.totalQuantity = 101.0;
    const ExecutionCommandResult conflictWhileKilled = policy.PlaceIbOrder(conflict);
    assert(conflictWhileKilled.status == ExecutionCommandStatus::Rejected);
    assert(conflictWhileKilled.reasonCode == "IDEMPOTENCY_KEY_CONFLICT");
    killSwitch->state = IbPaperKillSwitchState::Disarmed;

    std::size_t affected = 0;
    assert(!policy.ReconcileAuthoritativeState(affected, reason));
    assert(reason == "IB_CORRELATION_REFRESH_PENDING");
    correlations.reasonCode = "IB_ACTIVE_TERMINAL_CORRELATION_CONFLICT";
    assert(!policy.ReconcileAuthoritativeState(affected, reason));
    assert(reason == "IB_ACTIVE_TERMINAL_CORRELATION_CONFLICT");
    correlations.complete = true;
    correlations.reasonCode.clear();
    correlations.connectionEpoch = 11;
    correlations.generation = 7;
    correlations.activeOrderIdsByCorrelation["unrelated"] = 501;
    correlations.activeOrderIds.insert(501);
    terminal.complete = false;
    terminal.reasonCode = "IB_TERMINAL_CORRELATION_REFRESH_PENDING";
    assert(!policy.ReconcileAuthoritativeState(affected, reason));
    assert(reason == "IB_TERMINAL_CORRELATION_REFRESH_PENDING");

    terminal.complete = true;
    terminal.reasonCode.clear();
    terminal.generation = 9;
    terminal.connectionEpoch = 12;
    assert(!policy.ReconcileAuthoritativeState(affected, reason));
    assert(reason == "IB_PAPER_ACTIVE_TERMINAL_EPOCH_MISMATCH");

    terminal.connectionEpoch = correlations.connectionEpoch;
    terminal.executionOrderIds.insert(501);
    terminal.terminalOrderIdsByCorrelation["first-zero-id"] = 0;
    terminal.terminalOrderIdsByCorrelation["second-zero-id"] = 0;
    terminal.terminalStatusesByCorrelation["first-zero-id"] = "Filled";
    terminal.terminalStatusesByCorrelation["second-zero-id"] = "Filled";
    correlations.activeOrderIds.insert(0);
    correlations.activeOrderIdsByCorrelation["active-zero-id"] = 0;
    assert(!policy.ReconcileAuthoritativeState(affected, reason));
    assert(reason == "IB_PAPER_ACTIVE_TERMINAL_ORDER_CONFLICT");
    correlations.activeOrderIds.erase(0);
    correlations.activeOrderIdsByCorrelation.erase("active-zero-id");
    terminal.terminalOrderIdsByCorrelation["unrelated"] = 501;
    terminal.terminalStatusesByCorrelation["unrelated"] = "Cancelled";
    assert(!policy.ReconcileAuthoritativeState(affected, reason));
    assert(reason == "IB_PAPER_TERMINAL_CORRELATION_INVALID");
    terminal.terminalOrderIdsByCorrelation.erase("unrelated");
    terminal.terminalStatusesByCorrelation.erase("unrelated");
    assert(policy.ReconcileAuthoritativeState(affected, reason));

    killSwitch->state = IbPaperKillSwitchState::Engaged;
    IbPlaceOrderCommand killed = Place();
    killed.context.toolCallId = "killed";
    killed.context.decisionLeaseFencingToken = 7;
    killed.context.decisionLeaseGeneration = 9;
    const ExecutionCommandResult blocked = policy.PlaceIbOrder(killed);
    assert(blocked.status == ExecutionCommandStatus::Rejected);
    assert(blocked.reasonCode == "IB_PAPER_KILL_SWITCH_ENGAGED");

    killSwitch->state = IbPaperKillSwitchState::Disarmed;
    assert(::unlink(journalPath.c_str()) == 0);
    assert(::rmdir(credentials.c_str()) == 0);
    assert(::rmdir(state.c_str()) == 0);
}

void TestRecoveryOwnerAuditRequiresCompleteStableBrokerEvidence()
{
    RecoveryOwnerAuditFixture fixture;
    const AgentExecutionContext owner =
        fixture.PlaceForOwner("owner-audit-scope").context;

    fixture.active.complete = false;
    ExecutionControlResult result = fixture.Audit(
        owner, "owner-audit-active-incomplete");
    assert(result.status == ExecutionCommandStatus::Rejected);
    assert(result.reasonCode ==
        "RECOVERY_OWNER_ACTIVE_SNAPSHOT_INCOMPLETE");
    assert(!result.ownerAuditAuthoritative);
    assert(!result.ownerAuditComplete);

    fixture.CompleteSnapshots(41, 7, 11);
    fixture.terminal.complete = false;
    result = fixture.Audit(owner, "owner-audit-terminal-incomplete");
    assert(result.status == ExecutionCommandStatus::Rejected);
    assert(result.reasonCode ==
        "RECOVERY_OWNER_TERMINAL_SNAPSHOT_INCOMPLETE");

    fixture.CompleteSnapshots(41, 0, 11);
    result = fixture.Audit(owner, "owner-audit-generation-missing");
    assert(result.status == ExecutionCommandStatus::Rejected);
    assert(result.reasonCode ==
        "RECOVERY_OWNER_SNAPSHOT_GENERATION_INVALID");

    fixture.CompleteSnapshots(41, 7, 11);
    fixture.terminal.connectionEpoch = 42;
    result = fixture.Audit(owner, "owner-audit-epoch-drift");
    assert(result.status == ExecutionCommandStatus::Rejected);
    assert(result.reasonCode == "RECOVERY_OWNER_SNAPSHOT_EPOCH_DRIFT");
    assert(result.brokerConnectionEpoch == 41);
    assert(result.brokerActiveGeneration == 7);
    assert(result.brokerTerminalGeneration == 11);

    fixture.CompleteSnapshots(41, 7, 11);
    fixture.recoveryRisk.coherentRefreshComplete = false;
    result = fixture.Audit(owner, "owner-audit-risk-not-coherent");
    assert(result.status == ExecutionCommandStatus::Rejected);
    assert(result.reasonCode ==
        "RECOVERY_OWNER_RISK_SNAPSHOT_INCOMPLETE");

    fixture.CompleteSnapshots(41, 7, 11);
    fixture.postFillPending = true;
    result = fixture.Audit(owner, "owner-audit-post-fill-pending");
    assert(result.status == ExecutionCommandStatus::Rejected);
    assert(result.reasonCode ==
        "RECOVERY_OWNER_POST_FILL_RECONCILIATION_PENDING");
    assert(result.brokerPostFillRiskReconciliationPending);

    fixture.CompleteSnapshots(41, 7, 11);
    fixture.exposureGeneration = 5;
    fixture.terminalExposureGeneration = 5;
    fixture.riskAbsorbedExposureGeneration = 4;
    fixture.terminal.exposureGeneration = 5;
    fixture.recoveryRisk.riskAbsorbedExposureGeneration = 4;
    result = fixture.Audit(owner, "owner-audit-fill-not-absorbed");
    assert(result.status == ExecutionCommandStatus::Rejected);
    assert(result.reasonCode ==
        "RECOVERY_OWNER_EXPOSURE_NOT_ABSORBED");
    assert(result.brokerExposureGeneration == 5);
    assert(result.brokerTerminalExposureGeneration == 5);
    assert(result.brokerRiskAbsorbedExposureGeneration == 4);

    fixture.exposureGeneration = 5;
    fixture.terminalExposureGeneration = 5;
    fixture.riskAbsorbedExposureGeneration = 5;
    fixture.CompleteSnapshots(41, 7, 11);
    fixture.recoveryPositions["EUR.USD"] = 0.25;
    fixture.recoveryRisk.grossAbsolutePosition = 0.25;
    result = fixture.Audit(owner, "owner-audit-non-flat-position");
    assert(result.status == ExecutionCommandStatus::Rejected);
    assert(result.reasonCode == "RECOVERY_OWNER_POSITION_NOT_FLAT");
    assert(result.brokerPositionQuantity == "0.25");
    assert(result.brokerGrossAbsolutePosition == "0.25");

    fixture.CompleteSnapshots(41, 7, 11);
    fixture.barrierComplete = false;
    fixture.newConnectionEpochRequired = true;
    fixture.recoveryAuditReason =
        "IB_RECOVERY_AUDIT_NEW_CONNECTION_EPOCH_REQUIRED";
    result = fixture.Audit(owner, "owner-audit-new-epoch-required");
    assert(result.status == ExecutionCommandStatus::Rejected);
    assert(result.reasonCode ==
        "IB_RECOVERY_AUDIT_NEW_CONNECTION_EPOCH_REQUIRED");
    assert(result.brokerRecoveryAuditNewConnectionEpochRequired);
    assert(!result.brokerRecoveryAuditBarrierComplete);

    fixture.exposureGeneration = 0;
    fixture.terminalExposureGeneration = 0;
    fixture.riskAbsorbedExposureGeneration = 0;
    fixture.CompleteSnapshots(41, 7, 11);
    result = fixture.Audit(owner, "owner-audit-zero");
    assert(result.status == ExecutionCommandStatus::Accepted);
    assert(result.reasonCode == "RECOVERY_OWNER_ZERO_CONFIRMED");
    assert(result.ownerAuditAuthoritative);
    assert(result.ownerAuditComplete);
    assert(result.ownerActiveOrderCount == 0);
    assert(result.ownerUncertainCommandCount == 0);
    assert(result.brokerConnectionEpoch == 41);
    assert(result.brokerActiveGeneration == 7);
    assert(result.brokerTerminalGeneration == 11);
    assert(result.brokerRiskGeneration == 13);
    assert(result.brokerAccountGeneration == 12);
    assert(result.brokerPositionGeneration == 13);
    assert(result.brokerFxCashGeneration == 12);
    assert(result.brokerExposureGeneration == 0);
    assert(result.brokerTerminalExposureGeneration == 0);
    assert(result.brokerRiskAbsorbedExposureGeneration == 0);
    assert(result.brokerGlobalActiveOrderCount == 0);
    assert(!result.brokerPostFillRiskReconciliationPending);
    assert(result.brokerRecoveryAuditBarrierComplete);
    assert(!result.brokerRecoveryAuditNewConnectionEpochRequired);
    assert(result.brokerPositionQuantity == "0");
    assert(result.brokerGrossAbsolutePosition == "0");
    assert(result.ownerAccount == "DU123456");
    assert(result.ownerExecutionDomain == "PAPER");
}

void TestRecoveryOwnerAuditScopesActiveOrdersExactly()
{
    RecoveryOwnerAuditFixture fixture;
    const IbPlaceOrderCommand place =
        fixture.PlaceForOwner("owner-audit-active-place");
    const ExecutionCommandResult placed = fixture.policy->PlaceIbOrder(place);
    assert(placed.status == ExecutionCommandStatus::Accepted);
    assert(placed.orderId == 701);
    assert(!fixture.lastCorrelation.empty());
    fixture.active.activeOrderIds.insert(placed.orderId);
    fixture.active.activeOrderIdsByCorrelation[
        fixture.lastCorrelation] = placed.orderId;
    fixture.terminal.executionOrderIds.insert(placed.orderId);

    ExecutionControlResult result = fixture.Audit(
        place.context, "owner-audit-active");
    assert(result.status == ExecutionCommandStatus::Rejected);
    assert(result.reasonCode == "RECOVERY_OWNER_GLOBAL_ACTIVE_ORDERS");
    assert(result.ownerAuditAuthoritative);
    assert(result.ownerAuditComplete);
    assert(result.ownerActiveOrderCount == 1);
    assert(result.ownerUncertainCommandCount == 0);
    assert(result.affectedCount == 1);
    assert(result.brokerGlobalActiveOrderCount == 1);
    assert(result.ownerAccount == place.context.account);
    assert(result.ownerExecutionDomain == place.context.executionDomain);

    AgentExecutionContext wrongAccount = place.context;
    wrongAccount.account = "DU654321";
    result = fixture.Audit(wrongAccount, "owner-audit-wrong-account");
    assert(result.status == ExecutionCommandStatus::Rejected);
    assert(result.reasonCode == "RECOVERY_OWNER_AUDIT_ACCOUNT_MISMATCH");
    assert(result.ownerAccount == "DU654321");

    AgentExecutionContext wrongDomain = place.context;
    wrongDomain.executionDomain = "PAPER:other";
    result = fixture.Audit(wrongDomain, "owner-audit-wrong-domain");
    assert(result.status == ExecutionCommandStatus::Rejected);
    assert(result.reasonCode == "RECOVERY_OWNER_AUDIT_SCOPE_MISMATCH");
    assert(result.ownerExecutionDomain == "PAPER:other");

    fixture.active.activeOrderIds.clear();
    fixture.active.activeOrderIdsByCorrelation.clear();
    result = fixture.Audit(place.context, "owner-audit-flat-after-active");
    assert(result.status == ExecutionCommandStatus::Accepted);
    assert(result.reasonCode == "RECOVERY_OWNER_ZERO_CONFIRMED");
    assert(result.ownerActiveOrderCount == 0);
}

void TestRecoveryOwnerAuditRejectsUnmappedAndUncertainEvidence()
{
    {
        RecoveryOwnerAuditFixture fixture;
        const AgentExecutionContext owner =
            fixture.PlaceForOwner("owner-audit-unmapped-scope").context;
        fixture.active.activeOrderIds.insert(9001);
        fixture.active.activeOrderIdsByCorrelation[
            "unmapped-active-correlation"] = 9001;
        const ExecutionControlResult result = fixture.Audit(
            owner, "owner-audit-unmapped-active");
        assert(result.status == ExecutionCommandStatus::Rejected);
        assert(result.reasonCode ==
            "RECOVERY_OWNER_AUDIT_UNMAPPED_ACTIVE_ORDER");
        assert(!result.ownerAuditAuthoritative);
    }

    {
        RecoveryOwnerAuditFixture fixture;
        fixture.throwAfterDispatch = true;
        const IbPlaceOrderCommand place =
            fixture.PlaceForOwner("owner-audit-uncertain-place");
        const ExecutionCommandResult uncertain =
            fixture.policy->PlaceIbOrder(place);
        assert(uncertain.status == ExecutionCommandStatus::Uncertain);
        assert(uncertain.reasonCode == "IB_PLACE_OUTCOME_UNCERTAIN");
        const ExecutionControlResult result = fixture.Audit(
            place.context, "owner-audit-uncertain");
        assert(result.status == ExecutionCommandStatus::Rejected);
        assert(result.reasonCode ==
            "RECOVERY_OWNER_UNCERTAIN_COMMANDS_REMAIN");
        assert(result.ownerUncertainCommandCount == 1);
        assert(!result.ownerAuditAuthoritative);
        assert(!result.ownerAuditComplete);
    }

    for (const long terminalOrderId : {804L, 0L})
    {
        RecoveryOwnerAuditFixture fixture;
        fixture.throwAfterDispatch = true;
        const IbPlaceOrderCommand place = fixture.PlaceForOwner(
            terminalOrderId == 0 ?
                "general-reconcile-terminal-zero" :
                "general-reconcile-terminal-order");
        const ExecutionCommandResult uncertain =
            fixture.policy->PlaceIbOrder(place);
        assert(uncertain.status == ExecutionCommandStatus::Uncertain);
        assert(!fixture.lastCorrelation.empty());
        fixture.terminal.terminalOrderIdsByCorrelation[
            fixture.lastCorrelation] = terminalOrderId;
        fixture.terminal.terminalStatusesByCorrelation[
            fixture.lastCorrelation] = "Cancelled";
        std::size_t affected = 0;
        std::string reason;
        assert(fixture.policy->ReconcileAuthoritativeState(
            affected, reason));
        assert(affected >= 1);
        assert(!fixture.coordinator->IsMutationBlocked());
        ExecutionCommandResult resolved;
        assert(fixture.coordinator->GetCommandStatus(
            place.context.agentId, place.context.sessionId,
            place.context.toolCallId, resolved));
        assert(resolved.status == ExecutionCommandStatus::Accepted);
        assert(resolved.orderId == terminalOrderId);
        assert(resolved.reasonCode ==
            "AUTHORITATIVE_CORRELATION_CONFIRMED");
    }

    {
        RecoveryOwnerAuditFixture fixture;
        fixture.throwAfterDispatch = true;
        const IbPlaceOrderCommand place =
            fixture.PlaceForOwner("owner-audit-terminal-zero");
        const ExecutionCommandResult uncertain =
            fixture.policy->PlaceIbOrder(place);
        assert(uncertain.status == ExecutionCommandStatus::Uncertain);
        fixture.terminal.terminalOrderIdsByCorrelation[
            fixture.lastCorrelation] = 0;
        fixture.terminal.terminalStatusesByCorrelation[
            fixture.lastCorrelation] = "Cancelled";
        fixture.terminal.terminalOrderIdsByCorrelation[
            "second-terminal-zero"] = 0;
        fixture.terminal.terminalStatusesByCorrelation[
            "second-terminal-zero"] = "Rejected";

        const ExecutionControlResult result = fixture.Audit(
            place.context, "owner-audit-multiple-terminal-zero");
        assert(result.status == ExecutionCommandStatus::Accepted);
        assert(result.reasonCode == "RECOVERY_OWNER_ZERO_CONFIRMED");
        ExecutionCommandResult resolved;
        assert(fixture.coordinator->GetCommandStatus(
            place.context.agentId, place.context.sessionId,
            place.context.toolCallId, resolved));
        assert(resolved.status == ExecutionCommandStatus::Accepted);
        assert(resolved.orderId == 0);
    }

    {
        RecoveryOwnerAuditFixture fixture;
        fixture.throwAfterDispatch = true;
        const IbPlaceOrderCommand place =
            fixture.PlaceForOwner("owner-audit-invalid-terminal-status");
        assert(fixture.policy->PlaceIbOrder(place).status ==
            ExecutionCommandStatus::Uncertain);
        fixture.terminal.terminalOrderIdsByCorrelation[
            fixture.lastCorrelation] = 0;
        fixture.terminal.terminalStatusesByCorrelation[
            fixture.lastCorrelation] = "UnknownTerminal";

        const ExecutionControlResult result = fixture.Audit(
            place.context, "owner-audit-invalid-terminal");
        assert(result.status == ExecutionCommandStatus::Rejected);
        assert(result.reasonCode ==
            "RECOVERY_OWNER_TERMINAL_CORRELATION_INVALID");
        ExecutionCommandResult unresolved;
        assert(fixture.coordinator->GetCommandStatus(
            place.context.agentId, place.context.sessionId,
            place.context.toolCallId, unresolved));
        assert(unresolved.status == ExecutionCommandStatus::Uncertain);
        assert(fixture.coordinator->IsMutationBlocked());
    }

    {
        RecoveryOwnerAuditFixture fixture;
        const AgentExecutionContext owner =
            fixture.PlaceForOwner("owner-audit-zero-conflict-scope").context;
        fixture.active.activeOrderIds.insert(0);
        fixture.active.activeOrderIdsByCorrelation["active-zero"] = 0;
        fixture.terminal.terminalOrderIdsByCorrelation[
            "terminal-zero"] = 0;
        fixture.terminal.terminalStatusesByCorrelation[
            "terminal-zero"] = "Cancelled";
        ExecutionControlResult result = fixture.Audit(
            owner, "owner-audit-active-terminal-zero-conflict");
        assert(result.status == ExecutionCommandStatus::Rejected);
        assert(result.reasonCode ==
            "RECOVERY_OWNER_ACTIVE_TERMINAL_ORDER_CONFLICT");

        fixture.terminal.terminalOrderIdsByCorrelation.clear();
        fixture.terminal.terminalStatusesByCorrelation.clear();
        fixture.terminal.terminalOrderIdsByCorrelation[
            "active-zero"] = 0;
        fixture.terminal.terminalStatusesByCorrelation[
            "active-zero"] = "Cancelled";
        result = fixture.Audit(
            owner, "owner-audit-duplicate-zero-correlation");
        assert(result.status == ExecutionCommandStatus::Rejected);
        assert(result.reasonCode ==
            "RECOVERY_OWNER_TERMINAL_CORRELATION_INVALID");
    }

    {
        RecoveryOwnerAuditFixture fixture;
        const AgentExecutionContext owner =
            fixture.PlaceForOwner("owner-audit-duplicate-terminal-scope").context;
        fixture.terminal.terminalOrderIdsByCorrelation["terminal-a"] = 812;
        fixture.terminal.terminalStatusesByCorrelation["terminal-a"] =
            "Cancelled";
        fixture.terminal.terminalOrderIdsByCorrelation["terminal-b"] = 812;
        fixture.terminal.terminalStatusesByCorrelation["terminal-b"] =
            "Cancelled";
        const ExecutionControlResult result = fixture.Audit(
            owner, "owner-audit-duplicate-terminal-order-id");
        assert(result.status == ExecutionCommandStatus::Rejected);
        assert(result.reasonCode ==
            "RECOVERY_OWNER_TERMINAL_CORRELATION_INVALID");
    }

    {
        RecoveryOwnerAuditFixture fixture;
        const AgentExecutionContext owner =
            fixture.PlaceForOwner("owner-audit-cancel-zero-scope").context;
        fixture.policy.reset();
        fixture.coordinator.reset();

        OmsJournalEvent cancel;
        cancel.eventType = "cancel";
        cancel.tsMs = OmsJournal::NowEpochMs();
        cancel.orderId = 0;
        cancel.reqId = "owner-audit-uncertain-cancel-zero";
        cancel.clientReqId = cancel.reqId;
        cancel.traceId = owner.sessionId;
        cancel.eventId = cancel.reqId + ":cancel:intent_recorded:0";
        cancel.source = "agent.tool:" + owner.agentId;
        cancel.strategy = owner.strategy;
        cancel.account = owner.account;
        cancel.venue = owner.venue;
        cancel.executionDomain = owner.executionDomain;
        cancel.instrument = "EUR.USD";
        cancel.side = "BUY";
        cancel.status = "intent_recorded";
        cancel.requestHash = "sha256:owner-audit-uncertain-cancel-zero";
        assert(fixture.journal.Append(cancel));
        cancel.eventType = "cancel_send_attempt";
        cancel.eventId = cancel.reqId +
            ":cancel_send_attempt:attempt_recorded:0";
        cancel.status = "attempt_recorded";
        assert(fixture.journal.Append(cancel));

        fixture.coordinator.reset(new ExecutionCoordinator(
            fixture.journal, fixture.coordinatorCallbacks));
        std::string recoveryReason;
        assert(!fixture.coordinator->RecoverFromJournal(recoveryReason));
        assert(recoveryReason == "RECOVERY_RECONCILE_REQUIRED");
        fixture.policy.reset(new IbPaperExecutionPolicyAuthority(
            *fixture.coordinator, fixture.config, fixture.policyCallbacks,
            fixture.killSwitch));
        fixture.terminal.terminalOrderIdsByCorrelation[
            "unrelated-terminal-zero"] = 0;
        fixture.terminal.terminalStatusesByCorrelation[
            "unrelated-terminal-zero"] = "Cancelled";

        const ExecutionControlResult result = fixture.Audit(
            owner, "owner-audit-cancel-zero");
        assert(result.status == ExecutionCommandStatus::Rejected);
        assert(result.reasonCode ==
            "RECOVERY_OWNER_UNCERTAIN_COMMANDS_REMAIN");
        ExecutionCommandResult unresolved;
        assert(fixture.coordinator->GetCommandStatus(
            owner.agentId, owner.sessionId,
            "owner-audit-uncertain-cancel-zero", unresolved));
        assert(unresolved.status == ExecutionCommandStatus::Uncertain);
        assert(unresolved.reasonCode == "RECOVERY_RECONCILE_REQUIRED");
    }
}

void TestAuthoritativeFlattenPolicyAndRecovery()
{
    const std::string state = MakeTempDirectory();
    const std::string credentials = MakeTempDirectory();
    IbPaperExecutionProfileConfig config;
    std::string reason;
    assert(IbPaperExecutionProfileConfig::FromValues(
        ValidValues(state, credentials), config, reason));
    const std::string journalPath = state + "/oms-flatten-journal.jsonl";
    const std::shared_ptr<MutableKillSwitch> killSwitch(
        new MutableKillSwitch());
    IbPaperAuthoritativeRiskSnapshot risk;
    risk.complete = true;
    IbPaperAuthoritativePositionSnapshot position;
    position.complete = true;
    position.connectionEpoch = 11;
    position.generation = 12;
    position.quantity = 100.0;
    std::int64_t now = 100000;
    MarketQuoteSnapshot quote = FreshQuote(now);
    int venueSends = 0;
    bool driftPositionAtFinalProof = false;
    AuthoritativeFlattenPlan sentPlan;

    {
        OmsJournal journal;
        assert(journal.Init(journalPath));
        ExecutionCoordinatorCallbacks coordinatorCallbacks;
        coordinatorCallbacks.validateDecisionLease =
            [](const AgentExecutionContext&, const std::string&,
               std::string*) { return true; };
        coordinatorCallbacks.preVenueFlattenCheck =
            [](const FlattenPositionCommand&,
               const AuthoritativeFlattenPlan&, std::string*) {
                return true;
            };
        coordinatorCallbacks.placeIbReduceOnlyOrderCorrelated =
            [&](const AuthoritativeFlattenPlan& plan,
                const std::string&, long* orderId) {
                ++venueSends;
                sentPlan = plan;
                *orderId = 601;
                return true;
            };
        coordinatorCallbacks.proveAndCommitIbFlatNoop =
            [&](const AuthoritativeFlattenPlan& plan,
                const std::function<bool()>& commit,
                bool* attempted,
                std::string* detail) {
                if (driftPositionAtFinalProof)
                {
                    driftPositionAtFinalProof = false;
                    position.quantity = 1e-14;
                }
                if (!risk.complete ||
                    risk.activeOrderCount != 0)
                {
                    if (detail != nullptr)
                        *detail = !risk.complete ?
                            "IB_FLATTEN_POSITION_SNAPSHOT_MISMATCH" :
                            "IB_FLATTEN_ACTIVE_ORDER_SNAPSHOT_UNSAFE";
                    return false;
                }
                if (!position.complete ||
                    position.connectionEpoch !=
                        plan.positionConnectionEpoch ||
                    position.generation !=
                        plan.positionGeneration ||
                    position.quantity != 0.0)
                {
                    if (detail != nullptr)
                        *detail =
                            "IB_FLATTEN_POSITION_CHANGED_BEFORE_NOOP";
                    return false;
                }
                const IbPaperKillSwitchObservation observation =
                    killSwitch->Observe();
                if (observation.state ==
                    IbPaperKillSwitchState::Uncertain)
                {
                    if (detail != nullptr)
                        *detail =
                            "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN";
                    return false;
                }
                *attempted = true;
                return commit();
            };
        coordinatorCallbacks.onIbOrderPlaced =
            [](const IbPlaceOrderCommand&, long, std::string*) {
                return true;
            };
        ExecutionCoordinator coordinator(journal, coordinatorCallbacks);
        IbPaperExecutionPolicyCallbacks policyCallbacks;
        policyCallbacks.riskSnapshot = [&]() { return risk; };
        policyCallbacks.authoritativePosition =
            [&](const std::string&) { return position; };
        policyCallbacks.authoritativeContract = AuthoritativeContract;
        policyCallbacks.authoritativeQuote =
            [&](const std::string&) { return quote; };
        policyCallbacks.nowMs = [&]() { return now; };
        IbPaperExecutionPolicyAuthority policy(
            coordinator, config, policyCallbacks, killSwitch);

        FlattenPositionCommand command = Flatten();
        FlattenPositionCommand previewCommand = command;
        previewCommand.hasAuthoritativePreviewSnapshot = false;
        previewCommand.previewPositionQuantity = 0.0;
        previewCommand.previewPositionConnectionEpoch = 0;
        previewCommand.previewPositionGeneration = 0;
        const ExecutionCommandResult preview =
            policy.PreviewFlattenPosition(previewCommand);
        assert(preview.status == ExecutionCommandStatus::Accepted);
        assert(preview.detail.find("\"side\":\"SELL\"") !=
               std::string::npos);
        assert(preview.detail.find("\"quantity\":100") !=
               std::string::npos);
        assert(preview.detail.find("\"order_type\":\"MKT\"") !=
               std::string::npos);
        assert(preview.detail.find("\"reference_price\":10.1") !=
               std::string::npos);
        assert(preview.detail ==
            "{\"source\":\"IB\",\"authoritative\":true,"
            "\"position_connection_epoch\":11,"
            "\"position_generation\":12,"
            "\"position_quantity\":100,\"side\":\"SELL\","
            "\"quantity\":100,\"order_type\":\"MKT\","
            "\"reference_price\":10.1,"
            "\"quote_subscription_id\":\"IB:1:1:1001\","
            "\"quote_observed_at_ms\":100000,"
            "\"reduce_only\":true,\"risk_approved\":true}");
        assert(!preview.authoritativeFlattenPlanBinding.empty());
        command.previewPositionQuantity =
            preview.authoritativeFlattenPositionQuantity;
        command.previewPositionConnectionEpoch =
            preview.authoritativeFlattenConnectionEpoch;
        command.previewPositionGeneration =
            preview.authoritativeFlattenPositionGeneration;
        command.authoritativePreviewPlanBinding =
            preview.authoritativeFlattenPlanBinding;

        FlattenPositionCommand maliciousPreview = previewCommand;
        maliciousPreview.context.toolCallId =
            "flatten-malicious-contract-preview";
        maliciousPreview.contract.symbol = "GBP";
        const ExecutionCommandResult contractPreviewRejected =
            policy.PreviewFlattenPosition(maliciousPreview);
        assert(contractPreviewRejected.status ==
               ExecutionCommandStatus::Rejected);
        assert(contractPreviewRejected.reasonCode ==
               "IB_PAPER_FLATTEN_CONTRACT_MISMATCH");
        assert(venueSends == 0);

        FlattenPositionCommand maliciousMutation = command;
        maliciousMutation.context.toolCallId =
            "flatten-malicious-contract-mutation";
        maliciousMutation.contract.exchange = "SMART";
        const ExecutionCommandResult contractMutationRejected =
            policy.FlattenPosition(maliciousMutation);
        assert(contractMutationRejected.status ==
               ExecutionCommandStatus::Rejected);
        assert(contractMutationRejected.reasonCode ==
               "IB_PAPER_FLATTEN_CONTRACT_MISMATCH");
        assert(venueSends == 0);

        quote.bid = 9.8;
        quote.ask = 10.2;
        FlattenPositionCommand quoteDrift = command;
        quoteDrift.context.toolCallId = "flatten-quote-drift";
        const ExecutionCommandResult quoteDriftRejected =
            policy.FlattenPosition(quoteDrift);
        assert(quoteDriftRejected.status ==
               ExecutionCommandStatus::Rejected);
        assert(quoteDriftRejected.reasonCode ==
               "IB_PAPER_FLATTEN_PREVIEW_PLAN_CHANGED");
        assert(venueSends == 0);
        quote = FreshQuote(now);

        killSwitch->state = IbPaperKillSwitchState::Engaged;
        const ExecutionCommandResult accepted =
            policy.FlattenPosition(command);
        assert(accepted.status == ExecutionCommandStatus::Accepted);
        assert(accepted.orderId == 601);
        assert(venueSends == 1);
        assert(sentPlan.expectedPositionQuantity == 100.0);
        assert(sentPlan.positionConnectionEpoch == 11);
        assert(sentPlan.positionGeneration == 12);
        assert(sentPlan.order.action == "SELL");
        assert(sentPlan.order.totalQuantity == 100.0);
        assert(sentPlan.order.orderType == "MKT");
        assert(sentPlan.order.lmtPrice == 0.0);
        assert(sentPlan.referencePrice == 10.1);
        assert(sentPlan.quoteSubscriptionId == "IB:1:1:1001");
        assert(sentPlan.quoteObservedAtMs ==
               static_cast<std::uint64_t>(now));
        killSwitch->state = IbPaperKillSwitchState::Disarmed;

        killSwitch->state = IbPaperKillSwitchState::Engaged;
        assert(policy.FlattenPosition(command).status ==
               ExecutionCommandStatus::Duplicate);
        killSwitch->state = IbPaperKillSwitchState::Uncertain;
        FlattenPositionCommand uncertain = command;
        uncertain.context.toolCallId = "flatten-kill-uncertain";
        const ExecutionCommandResult uncertainBlocked =
            policy.FlattenPosition(uncertain);
        assert(uncertainBlocked.status ==
               ExecutionCommandStatus::Rejected);
        assert(uncertainBlocked.reasonCode ==
               "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN");
        assert(venueSends == 1);

        killSwitch->state = IbPaperKillSwitchState::Disarmed;
        risk.activeOrderCount = 1;
        FlattenPositionCommand active = command;
        active.context.toolCallId = "flatten-active-order";
        const ExecutionCommandResult activeBlocked =
            policy.FlattenPosition(active);
        assert(activeBlocked.reasonCode ==
               "IB_PAPER_FLATTEN_ACTIVE_ORDERS_PRESENT");
        risk.activeOrderCount = 0;

        position.complete = false;
        position.reasonCode = "POSITION_REFRESH_PENDING";
        FlattenPositionCommand incomplete = command;
        incomplete.context.toolCallId = "flatten-incomplete";
        assert(policy.FlattenPosition(incomplete).reasonCode ==
               "POSITION_REFRESH_PENDING");
        position.complete = true;
        position.reasonCode.clear();
        position.quantity = 0.0;
        killSwitch->state = IbPaperKillSwitchState::Engaged;
        FlattenPositionCommand flat = command;
        flat.context.toolCallId = "flatten-already-flat";
        flat.hasAuthoritativePreviewSnapshot = false;
        flat.previewPositionQuantity = 0.0;
        flat.previewPositionConnectionEpoch = 0;
        flat.previewPositionGeneration = 0;
        flat.authoritativePreviewPlanBinding.clear();

        killSwitch->state = IbPaperKillSwitchState::Disarmed;
        risk.activeOrderCount = 1;
        FlattenPositionCommand flatWithActiveOrder = flat;
        flatWithActiveOrder.context.toolCallId =
            "flatten-flat-active-order";
        const ExecutionCommandResult flatActiveRejected =
            policy.PreviewFlattenPosition(flatWithActiveOrder);
        assert(flatActiveRejected.status ==
               ExecutionCommandStatus::Rejected);
        assert(flatActiveRejected.reasonCode ==
               "IB_PAPER_FLATTEN_ACTIVE_ORDERS_PRESENT");
        assert(!flatActiveRejected.hasAuthoritativeFlattenSnapshot);
        risk.activeOrderCount = 0;

        risk.complete = false;
        FlattenPositionCommand flatWithIncompleteRisk = flat;
        flatWithIncompleteRisk.context.toolCallId =
            "flatten-flat-incomplete-risk";
        const ExecutionCommandResult flatRiskRejected =
            policy.PreviewFlattenPosition(flatWithIncompleteRisk);
        assert(flatRiskRejected.status ==
               ExecutionCommandStatus::Rejected);
        assert(flatRiskRejected.reasonCode ==
               "IB_PAPER_AUTHORITATIVE_RISK_SNAPSHOT_INCOMPLETE");
        assert(!flatRiskRejected.hasAuthoritativeFlattenSnapshot);
        risk.complete = true;

        killSwitch->state = IbPaperKillSwitchState::Uncertain;
        FlattenPositionCommand flatWithUncertainKill = flat;
        flatWithUncertainKill.context.toolCallId =
            "flatten-flat-uncertain-kill";
        const ExecutionCommandResult flatKillRejected =
            policy.PreviewFlattenPosition(flatWithUncertainKill);
        assert(flatKillRejected.status ==
               ExecutionCommandStatus::Rejected);
        assert(flatKillRejected.reasonCode ==
               "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN");
        assert(!flatKillRejected.hasAuthoritativeFlattenSnapshot);

        killSwitch->state = IbPaperKillSwitchState::Engaged;
        const ExecutionCommandResult flatPreview =
            policy.PreviewFlattenPosition(flat);
        assert(flatPreview.status == ExecutionCommandStatus::Accepted);
        flat.hasAuthoritativePreviewSnapshot = true;
        flat.previewPositionQuantity =
            flatPreview.authoritativeFlattenPositionQuantity;
        flat.previewPositionConnectionEpoch =
            flatPreview.authoritativeFlattenConnectionEpoch;
        flat.previewPositionGeneration =
            flatPreview.authoritativeFlattenPositionGeneration;
        flat.authoritativePreviewPlanBinding =
            flatPreview.authoritativeFlattenPlanBinding;
        driftPositionAtFinalProof = true;
        const ExecutionCommandResult staleNoop =
            policy.FlattenPosition(flat);
        assert(staleNoop.status ==
               ExecutionCommandStatus::Rejected);
        assert(staleNoop.reasonCode ==
               "IB_FLATTEN_POSITION_CHANGED_BEFORE_NOOP");

        position.quantity = 0.0;
        FlattenPositionCommand provedFlat = flat;
        provedFlat.context.toolCallId =
            "flatten-atomically-proved-flat";
        provedFlat.hasAuthoritativePreviewSnapshot = false;
        provedFlat.previewPositionConnectionEpoch = 0;
        provedFlat.previewPositionGeneration = 0;
        provedFlat.authoritativePreviewPlanBinding.clear();
        const ExecutionCommandResult provedFlatPreview =
            policy.PreviewFlattenPosition(provedFlat);
        assert(provedFlatPreview.status ==
               ExecutionCommandStatus::Accepted);
        provedFlat.hasAuthoritativePreviewSnapshot = true;
        provedFlat.previewPositionQuantity =
            provedFlatPreview.authoritativeFlattenPositionQuantity;
        provedFlat.previewPositionConnectionEpoch =
            provedFlatPreview.authoritativeFlattenConnectionEpoch;
        provedFlat.previewPositionGeneration =
            provedFlatPreview.authoritativeFlattenPositionGeneration;
        provedFlat.authoritativePreviewPlanBinding =
            provedFlatPreview.authoritativeFlattenPlanBinding;
        const ExecutionCommandResult noop =
            policy.FlattenPosition(provedFlat);
        assert(noop.status == ExecutionCommandStatus::Accepted);
        assert(noop.reasonCode == "POSITION_ALREADY_FLAT");
        assert(venueSends == 1);
        killSwitch->state = IbPaperKillSwitchState::Disarmed;
    }

    {
        OmsJournal journal;
        assert(journal.Init(journalPath));
        ExecutionCoordinatorCallbacks coordinatorCallbacks;
        coordinatorCallbacks.validateDecisionLease =
            [](const AgentExecutionContext&, const std::string&,
               std::string*) { return true; };
        coordinatorCallbacks.placeIbReduceOnlyOrderCorrelated =
            [&](const AuthoritativeFlattenPlan&, const std::string&,
                long*) {
                ++venueSends;
                return true;
            };
        ExecutionCoordinator coordinator(journal, coordinatorCallbacks);
        assert(coordinator.RecoverFromJournal(reason));
        std::vector<std::int64_t> attempts;
        coordinator.GetPlaceSendAttemptTimes(
            config.account, "PAPER", 0, attempts);
        assert(attempts.size() == 1);
        IbPaperExecutionPolicyCallbacks policyCallbacks;
        policyCallbacks.riskSnapshot = [&]() { return risk; };
        policyCallbacks.authoritativePosition =
            [&](const std::string&) { return position; };
        policyCallbacks.authoritativeContract = AuthoritativeContract;
        policyCallbacks.authoritativeQuote =
            [&](const std::string&) { return FreshQuote(now); };
        policyCallbacks.nowMs = [&]() { return now; };
        IbPaperExecutionPolicyAuthority policy(
            coordinator, config, policyCallbacks, killSwitch);
        const ExecutionCommandResult duplicate =
            policy.FlattenPosition(Flatten());
        assert(duplicate.status == ExecutionCommandStatus::Duplicate);
        assert(duplicate.orderId == 601);
        assert(venueSends == 1);
    }

    int intents = 0;
    int receipts = 0;
    int noops = 0;
    {
        OmsJournal journal;
        assert(journal.Init(journalPath));
        assert(journal.Replay([&](const OmsJournalEvent& event) {
            if (event.eventType == "flatten_intent") ++intents;
            if (event.eventType == "flatten_sent") ++receipts;
            if (event.eventType == "flatten_noop") ++noops;
        }));
    }
    assert(intents == 3);
    assert(receipts == 1);
    assert(noops == 1);
    assert(::unlink(journalPath.c_str()) == 0);
    assert(::rmdir(credentials.c_str()) == 0);
    assert(::rmdir(state.c_str()) == 0);
}

void TestExternalLimitDayEntryAndAtomicFlatten()
{
    const std::string state = MakeTempDirectory();
    const std::string credentials = MakeTempDirectory();
    std::map<std::string, std::string> values =
        ExternalLimitValues(state, credentials);
    IbPaperExecutionProfileConfig config;
    std::string reason;
    assert(IbPaperExecutionProfileConfig::FromValues(
        values, config, reason));
    assert(config.enabled);
    assert(config.UsesExternalLimitDay());
    assert(config.orderMode == IbPaperOrderMode::ExternalLimitDay);
    assert(std::string(config.AllowedOrderTypes()) == "LMT");
    assert(config.externalQuoteMaxAgeMs == 5000);
    std::string externalCredential;
    assert(config.BuildAuthorizationCredential(
        externalCredential, reason));
    assert(externalCredential.compare(
        0, 16, "PAPER-V4:sha256:") == 0);
    assert(externalCredential.size() == 80);
    IbPaperExecutionProfileConfig changedExternal = config;
    changedExternal.externalQuoteMaxAgeMs = 4999;
    std::string changedExternalCredential;
    assert(changedExternal.BuildAuthorizationCredential(
        changedExternalCredential, reason));
    assert(changedExternalCredential != externalCredential);

    IbPaperExecutionProfileConfig localConfig;
    assert(IbPaperExecutionProfileConfig::FromValues(
        ValidValues(state, credentials), localConfig, reason));
    assert(!localConfig.UsesExternalLimitDay());
    assert(std::string(localConfig.AllowedOrderTypes()) == "MKT");
    assert(AuthorizationValue(localConfig).compare(
        0, 16, "PAPER-V3:sha256:") == 0);

    std::map<std::string, std::string> invalid = values;
    invalid["HEPTA_EXECUTION_EXTERNAL_P1_CANARY_LMT_DAY"] = "";
    assert(!IbPaperExecutionProfileConfig::FromValues(
        invalid, config, reason));
    assert(reason ==
        "IB_PAPER_EXTERNAL_ORDER_MODE_CONFIGURATION_INVALID");
    invalid = values;
    invalid["HEPTA_EXECUTION_EXTERNAL_P1_CANARY_LMT_DAY"] =
        "EXTERNAL_LMT_DAY";
    assert(!IbPaperExecutionProfileConfig::FromValues(
        invalid, config, reason));
    invalid = values;
    invalid.erase("HEPTA_EXECUTION_MAX_ORDER_NOTIONAL");
    assert(!IbPaperExecutionProfileConfig::FromValues(
        invalid, config, reason));
    invalid = values;
    invalid["HEPTA_IB_PAPER_MAX_ORDER_QTY"] = "1.01";
    assert(!IbPaperExecutionProfileConfig::FromValues(
        invalid, config, reason));
    assert(reason == "IB_PAPER_EXTERNAL_ORDER_MODE_LIMITS_INVALID");
    invalid = values;
    invalid["HEPTA_IB_PAPER_MAX_ACTIVE_ORDERS"] = "2";
    assert(!IbPaperExecutionProfileConfig::FromValues(
        invalid, config, reason));
    invalid = values;
    invalid["HEPTA_IB_PAPER_MAX_GROSS_POSITION"] = "1.01";
    assert(!IbPaperExecutionProfileConfig::FromValues(
        invalid, config, reason));
    invalid = values;
    invalid["HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL"] = "5000.01";
    assert(!IbPaperExecutionProfileConfig::FromValues(
        invalid, config, reason));
    invalid = values;
    invalid["HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS"] = "5001";
    assert(!IbPaperExecutionProfileConfig::FromValues(
        invalid, config, reason));
    invalid = values;
    invalid.erase("HEPTA_EXECUTION_EXTERNAL_P1_CANARY_LMT_DAY");
    assert(!IbPaperExecutionProfileConfig::FromValues(
        invalid, config, reason));
    assert(reason ==
        "IB_PAPER_EXTERNAL_ORDER_MODE_CONFIGURATION_INVALID");

    assert(IbPaperExecutionProfileConfig::FromValues(
        values, config, reason));
    const std::shared_ptr<MutableKillSwitch> killSwitch(
        new MutableKillSwitch());
    IbPaperExecutionGuard guard(config, killSwitch);
    IbPaperAuthoritativeRiskSnapshot risk;
    risk.complete = true;
    assert(guard.AllowPlaceAtAuthoritativePrice(
        ExternalLimitPlace(), risk, 10.1, 100000, reason));
    assert(guard.AllowPlaceAtAuthoritativePrice(
        ExternalLimitPlace("SELL"), risk, 9.9, 100000, reason));
    IbPlaceOrderCommand market = ExternalLimitPlace();
    market.order.orderType = "MKT";
    market.order.lmtPrice = 0.0;
    assert(!guard.AllowPlaceAtAuthoritativePrice(
        market, risk, 10.1, 100000, reason));
    assert(reason == "IB_PAPER_EXTERNAL_LIMIT_ORDERS_ONLY");
    IbPlaceOrderCommand wrongLimit = ExternalLimitPlace();
    wrongLimit.order.lmtPrice = 10.09;
    assert(!guard.AllowPlaceAtAuthoritativePrice(
        wrongLimit, risk, 10.1, 100000, reason));
    assert(reason == "IB_PAPER_EXTERNAL_LIMIT_PRICE_MISMATCH");
    wrongLimit = ExternalLimitPlace();
    wrongLimit.referencePrice = 10.09;
    assert(!guard.AllowPlaceAtAuthoritativePrice(
        wrongLimit, risk, 10.1, 100000, reason));
    wrongLimit = ExternalLimitPlace();
    wrongLimit.timeInForce = "GTC";
    assert(!guard.AllowPlaceAtAuthoritativePrice(
        wrongLimit, risk, 10.1, 100000, reason));
    assert(reason == "IB_PAPER_ORDER_INTENT_INVALID");
    IbPlaceOrderCommand hiddenField = ExternalLimitPlace();
    hiddenField.order.totalQuantity = -1.0;
    assert(!guard.AllowPlaceAtAuthoritativePrice(
        hiddenField, risk, 10.1, 100000, reason));
    assert(reason == "IB_PAPER_EXTERNAL_ORDER_FIELDS_INVALID");
    hiddenField = ExternalLimitPlace();
    hiddenField.order.auxPrice = 10.0;
    assert(!guard.AllowPlaceAtAuthoritativePrice(
        hiddenField, risk, 10.1, 100000, reason));
    assert(reason == "IB_PAPER_EXTERNAL_ORDER_FIELDS_INVALID");
    hiddenField = ExternalLimitPlace();
    hiddenField.order.outsideRth = true;
    assert(!guard.AllowPlaceAtAuthoritativePrice(
        hiddenField, risk, 10.1, 100000, reason));
    hiddenField = ExternalLimitPlace();
    hiddenField.order.orderRef = "caller-owned";
    assert(!guard.AllowPlaceAtAuthoritativePrice(
        hiddenField, risk, 10.1, 100000, reason));
    assert(!guard.AllowPlaceAtAuthoritativePrice(
        ExternalLimitPlace("BUY", 1.01), risk, 10.1, 100000, reason));
    assert(reason == "IB_PAPER_MAX_ORDER_QUANTITY_EXCEEDED");
    risk.activeOrderCount = 1;
    assert(!guard.AllowPlaceAtAuthoritativePrice(
        ExternalLimitPlace(), risk, 10.1, 100000, reason));
    assert(reason == "IB_PAPER_MAX_ACTIVE_ORDERS_EXCEEDED");
    risk.activeOrderCount = 0;

    AuthoritativeFlattenPlan directPlan;
    directPlan.expectedPositionQuantity = 0.75;
    directPlan.order.action = "SELL";
    directPlan.order.orderType = "LMT";
    directPlan.order.totalQuantity = 0.75;
    directPlan.order.lmtPrice = 9.9;
    directPlan.timeInForce = "DAY";
    directPlan.referencePrice = 9.9;
    directPlan.quoteBid = 9.9;
    directPlan.quoteAsk = 10.1;
    directPlan.quoteSubscriptionId = "IB:11:12:13";
    directPlan.quoteObservedAtMs = 99900;
    directPlan.quoteStaleAfterMs = 100100;
    directPlan.profileOrderMode = "EXTERNAL_P1_CANARY_LMT_DAY";
    FlattenPositionCommand directFlatten = Flatten();
    assert(guard.AllowFlatten(
        directFlatten, directPlan, risk, 100000, reason));
    AuthoritativeFlattenPlan changedPlan = directPlan;
    changedPlan.order.totalQuantity = 0.5;
    assert(!guard.AllowFlatten(
        directFlatten, changedPlan, risk, 100000, reason));
    assert(reason == "IB_PAPER_FLATTEN_NOT_EXACT_REDUCE_ONLY");
    changedPlan = directPlan;
    changedPlan.order.action = "BUY";
    assert(!guard.AllowFlatten(
        directFlatten, changedPlan, risk, 100000, reason));
    changedPlan = directPlan;
    changedPlan.timeInForce = "GTC";
    assert(!guard.AllowFlatten(
        directFlatten, changedPlan, risk, 100000, reason));
    assert(reason == "IB_PAPER_FLATTEN_ORDER_INVALID");
    changedPlan = directPlan;
    changedPlan.order.lmtPrice = 9.8;
    assert(!guard.AllowFlatten(
        directFlatten, changedPlan, risk, 100000, reason));
    changedPlan = directPlan;
    changedPlan.order.auxPrice = 1.0;
    assert(!guard.AllowFlatten(
        directFlatten, changedPlan, risk, 100000, reason));
    assert(reason == "IB_PAPER_FLATTEN_ORDER_INVALID");
    changedPlan = directPlan;
    changedPlan.order.outsideRth = true;
    assert(!guard.AllowFlatten(
        directFlatten, changedPlan, risk, 100000, reason));
    changedPlan = directPlan;
    changedPlan.order.orderRef = "caller-owned";
    assert(!guard.AllowFlatten(
        directFlatten, changedPlan, risk, 100000, reason));
    changedPlan = directPlan;
    changedPlan.quoteSubscriptionId.clear();
    assert(!guard.AllowFlatten(
        directFlatten, changedPlan, risk, 100000, reason));
    changedPlan = directPlan;
    changedPlan.quoteStaleAfterMs = 99999;
    assert(!guard.AllowFlatten(
        directFlatten, changedPlan, risk, 100000, reason));
    changedPlan = directPlan;
    changedPlan.expectedPositionQuantity = 1.01;
    changedPlan.order.totalQuantity = 1.01;
    assert(!guard.AllowFlatten(
        directFlatten, changedPlan, risk, 100000, reason));
    assert(reason ==
        "IB_PAPER_EXTERNAL_FLATTEN_POSITION_LIMIT_EXCEEDED");
    changedPlan = directPlan;
    changedPlan.expectedPositionQuantity = 1.0;
    changedPlan.order.totalQuantity = 1.0;
    changedPlan.order.lmtPrice = 5001.0;
    changedPlan.referencePrice = 5001.0;
    changedPlan.quoteBid = 5001.0;
    changedPlan.quoteAsk = 5001.0;
    assert(!guard.AllowFlatten(
        directFlatten, changedPlan, risk, 100000, reason));
    assert(reason == "IB_PAPER_MAX_ORDER_NOTIONAL_EXCEEDED");

    AuthoritativeFlattenPlan directNoop = directPlan;
    directNoop.expectedPositionQuantity = 0.0;
    directNoop.order.action.clear();
    directNoop.order.totalQuantity = 0.0;
    directNoop.order.lmtPrice = 0.0;
    directNoop.referencePrice = 0.0;
    assert(guard.AllowFlatten(
        directFlatten, directNoop, risk, 100000, reason));
    directNoop.quoteSubscriptionId.clear();
    assert(!guard.AllowFlatten(
        directFlatten, directNoop, risk, 100000, reason));
    assert(reason == "IB_PAPER_FLATTEN_ORDER_INVALID");

    const std::string journalPath =
        state + "/oms-external-limit-journal.jsonl";
    OmsJournal journal;
    assert(journal.Init(journalPath));
    int entrySends = 0;
    int flattenSends = 0;
    AuthoritativeFlattenPlan sentPlan;
    ExecutionCoordinatorCallbacks coordinatorCallbacks;
    coordinatorCallbacks.placeIbOrderCommandCorrelated =
        [&](const IbPlaceOrderCommand&, const std::string&,
            long* orderId) {
            ++entrySends;
            *orderId = 701;
            return true;
        };
    coordinatorCallbacks.placeIbReduceOnlyOrderCorrelated =
        [&](const AuthoritativeFlattenPlan& plan,
            const std::string&, long* orderId) {
            ++flattenSends;
            sentPlan = plan;
            *orderId = 702;
            return true;
        };
    coordinatorCallbacks.proveAndCommitIbFlatNoop =
        [](const AuthoritativeFlattenPlan&,
           const std::function<bool()>& commit,
           bool* attempted, std::string*) {
            *attempted = true;
            return commit();
        };
    coordinatorCallbacks.validateDecisionLease =
        [](const AgentExecutionContext&, const std::string&,
           std::string*) { return true; };
    coordinatorCallbacks.onIbOrderPlaced =
        [](const IbPlaceOrderCommand&, long, std::string*) {
            return true;
        };
    ExecutionCoordinator coordinator(journal, coordinatorCallbacks);
    std::int64_t now = 100000;
    MarketQuoteSnapshot quote = FreshQuote(now);
    IbPaperAuthoritativePositionSnapshot position;
    position.complete = true;
    position.connectionEpoch = 11;
    position.generation = 12;
    position.quantity = 0.75;
    IbPaperExecutionPolicyCallbacks policyCallbacks;
    policyCallbacks.riskSnapshot = [&]() { return risk; };
    policyCallbacks.nowMs = [&]() { return now; };
    policyCallbacks.authoritativeQuote =
        [&](const std::string&) { return quote; };
    policyCallbacks.authoritativePosition =
        [&](const std::string&) { return position; };
    policyCallbacks.authoritativeContract = AuthoritativeContract;
    IbPaperExecutionPolicyAuthority policy(
        coordinator, config, policyCallbacks, killSwitch);

    IbPlaceOrderCommand entry = ExternalLimitPlace();
    entry.context.toolCallId = "external-entry";
    entry.context.decisionLeaseFencingToken = 7;
    entry.context.decisionLeaseGeneration = 9;
    const ExecutionCommandResult entryPreview =
        policy.PreviewOrder(entry);
    assert(entryPreview.status == ExecutionCommandStatus::Accepted);
    assert(entryPreview.detail.find("\"order_type\":\"LMT\"") !=
        std::string::npos);
    assert(entryPreview.detail.find("\"tif\":\"DAY\"") !=
        std::string::npos);
    assert(entryPreview.detail.find("\"limit_price\":10.1") !=
        std::string::npos);
    assert(entryPreview.detail.find("\"reference_price\":10.1") !=
        std::string::npos);
    assert(entryPreview.detail.find("\"quote_bid\":9.9") !=
        std::string::npos);
    assert(entryPreview.detail.find("\"quote_ask\":10.1") !=
        std::string::npos);
    assert(policy.PlaceIbOrder(entry).status ==
        ExecutionCommandStatus::Accepted);
    assert(entrySends == 1);

    IbPlaceOrderCommand externalMarket = market;
    externalMarket.context.toolCallId = "external-market";
    assert(policy.PreviewOrder(externalMarket).reasonCode ==
        "IB_PAPER_EXTERNAL_LIMIT_ORDERS_ONLY");
    IbPlaceOrderCommand drift = ExternalLimitPlace();
    drift.context.toolCallId = "external-drift";
    quote.ask = 10.2;
    assert(policy.PlaceIbOrder(drift).reasonCode ==
        "IB_PAPER_EXTERNAL_LIMIT_PRICE_MISMATCH");
    quote = FreshQuote(now);
    quote.staleAfterMs = static_cast<std::uint64_t>(now - 1);
    drift.context.toolCallId = "external-stale";
    assert(policy.PreviewOrder(drift).reasonCode ==
        "AUTHORITATIVE_QUOTE_STALE");
    quote = FreshQuote(now);

    FlattenPositionCommand flatten = Flatten();
    flatten.context.toolCallId = "external-flatten";
    flatten.hasAuthoritativePreviewSnapshot = false;
    flatten.previewPositionQuantity = 0.0;
    flatten.previewPositionConnectionEpoch = 0;
    flatten.previewPositionGeneration = 0;
    const ExecutionCommandResult flattenPreview =
        policy.PreviewFlattenPosition(flatten);
    assert(flattenPreview.status == ExecutionCommandStatus::Accepted);
    assert(flattenPreview.detail.find("\"order_type\":\"LMT\"") !=
        std::string::npos);
    assert(flattenPreview.detail.find("\"tif\":\"DAY\"") !=
        std::string::npos);
    assert(flattenPreview.detail.find("\"limit_price\":9.9") !=
        std::string::npos);
    assert(flattenPreview.detail.find("\"reference_price\":9.9") !=
        std::string::npos);
    assert(flattenPreview.detail.find("\"quote_bid\":9.9") !=
        std::string::npos);
    assert(flattenPreview.detail.find("\"quote_ask\":10.1") !=
        std::string::npos);
    assert(flattenPreview.detail.find("\"reduce_only\":true") !=
        std::string::npos);
    assert(flattenPreview.detail.find("\"atomic\":true") !=
        std::string::npos);
    assert(flattenPreview.detail.find("\"risk_approved\":true") !=
        std::string::npos);
    assert(flattenPreview.authoritativeFlattenPlanBinding.find(
        "profile.order_mode") != std::string::npos);
    flatten.hasAuthoritativePreviewSnapshot = true;
    flatten.previewPositionQuantity =
        flattenPreview.authoritativeFlattenPositionQuantity;
    flatten.previewPositionConnectionEpoch =
        flattenPreview.authoritativeFlattenConnectionEpoch;
    flatten.previewPositionGeneration =
        flattenPreview.authoritativeFlattenPositionGeneration;
    flatten.authoritativePreviewPlanBinding =
        flattenPreview.authoritativeFlattenPlanBinding;
    const ExecutionCommandResult flattened =
        policy.FlattenPosition(flatten);
    assert(flattened.status == ExecutionCommandStatus::Accepted);
    assert(flattened.orderId == 702);
    assert(flattenSends == 1);
    assert(sentPlan.profileOrderMode ==
        "EXTERNAL_P1_CANARY_LMT_DAY");
    assert(sentPlan.order.action == "SELL");
    assert(sentPlan.order.orderType == "LMT");
    assert(sentPlan.order.totalQuantity == 0.75);
    assert(sentPlan.order.lmtPrice == 9.9);
    assert(sentPlan.referencePrice == 9.9);
    assert(sentPlan.quoteBid == 9.9);
    assert(sentPlan.quoteAsk == 10.1);
    assert(sentPlan.timeInForce == "DAY");
    assert(policy.FlattenPosition(flatten).status ==
        ExecutionCommandStatus::Duplicate);
    assert(flattenSends == 1);

    FlattenPositionCommand tampered = flatten;
    tampered.context.toolCallId = "external-flatten-tampered";
    tampered.authoritativePreviewPlanBinding.push_back('x');
    assert(policy.FlattenPosition(tampered).reasonCode ==
        "IB_PAPER_FLATTEN_PREVIEW_PLAN_CHANGED");
    assert(flattenSends == 1);
    position.quantity = 1.01;
    FlattenPositionCommand overPosition = Flatten();
    overPosition.context.toolCallId = "external-flatten-over-position";
    overPosition.hasAuthoritativePreviewSnapshot = false;
    assert(policy.PreviewFlattenPosition(overPosition).reasonCode ==
        "IB_PAPER_EXTERNAL_FLATTEN_POSITION_LIMIT_EXCEEDED");
    position.quantity = 0.75;
    risk.activeOrderCount = 1;
    FlattenPositionCommand active = Flatten();
    active.context.toolCallId = "external-flatten-active";
    active.hasAuthoritativePreviewSnapshot = false;
    assert(policy.PreviewFlattenPosition(active).reasonCode ==
        "IB_PAPER_FLATTEN_ACTIVE_ORDERS_PRESENT");
    risk.activeOrderCount = 0;
    position.quantity = 0.0;
    FlattenPositionCommand noop = Flatten();
    noop.context.toolCallId = "external-flatten-noop";
    noop.hasAuthoritativePreviewSnapshot = false;
    const ExecutionCommandResult noopPreview =
        policy.PreviewFlattenPosition(noop);
    assert(noopPreview.status == ExecutionCommandStatus::Accepted);
    assert(noopPreview.detail.find("\"order_type\":\"LMT\"") !=
        std::string::npos);
    assert(noopPreview.detail.find("\"atomic\":true") !=
        std::string::npos);

    assert(::unlink(journalPath.c_str()) == 0);
    assert(::rmdir(credentials.c_str()) == 0);
    assert(::rmdir(state.c_str()) == 0);
}

void TestDurableSendAttemptRateBudgetSurvivesRestart()
{
    const std::string state = MakeTempDirectory();
    const std::string credentials = MakeTempDirectory();
    std::map<std::string, std::string> values = ValidValues(state, credentials);
    values["HEPTA_IB_PAPER_MAX_ORDERS_PER_MINUTE"] = "1";
    IbPaperExecutionProfileConfig config;
    std::string reason;
    assert(IbPaperExecutionProfileConfig::FromValues(values, config, reason));
    const std::string journalPath = state + "/oms-rate-journal.jsonl";
    const std::int64_t now = static_cast<std::int64_t>(OmsJournal::NowEpochMs());
    int venueAttempts = 0;
    const std::shared_ptr<MutableKillSwitch> killSwitch(new MutableKillSwitch());

    {
        OmsJournal journal;
        assert(journal.Init(journalPath));
        ExecutionCoordinatorCallbacks coordinatorCallbacks;
        coordinatorCallbacks.placeIbOrderCorrelated =
            [&](const IBContractLite&, const IBOrderLite&, const std::string&, long*) {
                ++venueAttempts;
                return false;
            };
        coordinatorCallbacks.lastIbRejectReason =
            []() { return std::string("offline fake rejected after dispatch attempt"); };
        coordinatorCallbacks.validateDecisionLease =
            [](const AgentExecutionContext&, const std::string&, std::string*) {
                return true;
            };
        ExecutionCoordinator coordinator(journal, coordinatorCallbacks);
        IbPaperAuthoritativeRiskSnapshot risk;
        risk.complete = true;
        IbPaperExecutionPolicyCallbacks policyCallbacks;
        policyCallbacks.riskSnapshot = [&]() { return risk; };
        policyCallbacks.nowMs = [&]() { return now; };
        policyCallbacks.authoritativeQuote = [&](const std::string&) {
            return FreshQuote(now);
        };
        IbPaperExecutionPolicyAuthority policy(
            coordinator, config, policyCallbacks, killSwitch);
        IbPlaceOrderCommand command = Place();
        command.context.decisionLeaseFencingToken = 7;
        command.context.decisionLeaseGeneration = 9;
        const ExecutionCommandResult rejected = policy.PlaceIbOrder(command);
        assert(rejected.status == ExecutionCommandStatus::Rejected);
        assert(rejected.reasonCode == "IB_PLACE_REJECT");
        assert(venueAttempts == 1);

        IbPlaceOrderCommand second = command;
        second.context.toolCallId = "place-second";
        const ExecutionCommandResult rateBlocked = policy.PlaceIbOrder(second);
        assert(rateBlocked.status == ExecutionCommandStatus::Rejected);
        assert(rateBlocked.reasonCode == "IB_PAPER_ORDER_RATE_EXCEEDED");
        assert(venueAttempts == 1);
    }

    {
        OmsJournal journal;
        assert(journal.Init(journalPath));
        ExecutionCoordinatorCallbacks coordinatorCallbacks;
        coordinatorCallbacks.placeIbOrderCorrelated =
            [&](const IBContractLite&, const IBOrderLite&, const std::string&, long*) {
                ++venueAttempts;
                return true;
            };
        coordinatorCallbacks.validateDecisionLease =
            [](const AgentExecutionContext&, const std::string&, std::string*) {
                return true;
            };
        ExecutionCoordinator coordinator(journal, coordinatorCallbacks);
        assert(coordinator.RecoverFromJournal(reason));
        std::vector<std::int64_t> attempts;
        coordinator.GetPlaceSendAttemptTimes(
            config.account, "PAPER", now - 60000, attempts);
        assert(attempts.size() == 1);

        IbPaperAuthoritativeRiskSnapshot risk;
        risk.complete = true;
        IbPaperExecutionPolicyCallbacks policyCallbacks;
        policyCallbacks.riskSnapshot = [&]() { return risk; };
        policyCallbacks.nowMs = [&]() { return now + 1; };
        policyCallbacks.authoritativeQuote = [&](const std::string&) {
            return FreshQuote(now + 1);
        };
        IbPaperExecutionPolicyAuthority policy(
            coordinator, config, policyCallbacks, killSwitch);
        IbPlaceOrderCommand exact = Place();
        exact.context.decisionLeaseFencingToken = 7;
        exact.context.decisionLeaseGeneration = 9;
        const ExecutionCommandResult duplicate = policy.PlaceIbOrder(exact);
        assert(duplicate.status == ExecutionCommandStatus::Duplicate);
        IbPlaceOrderCommand fresh = exact;
        fresh.context.toolCallId = "place-after-restart";
        const ExecutionCommandResult rateBlocked = policy.PlaceIbOrder(fresh);
        assert(rateBlocked.status == ExecutionCommandStatus::Rejected);
        assert(rateBlocked.reasonCode == "IB_PAPER_ORDER_RATE_EXCEEDED");
        assert(venueAttempts == 1);
    }

    assert(::unlink(journalPath.c_str()) == 0);
    assert(::rmdir(credentials.c_str()) == 0);
    assert(::rmdir(state.c_str()) == 0);
}

void TestKillSwitchRecheckedImmediatelyBeforeVenueIo()
{
    const std::string state = MakeTempDirectory();
    const std::string credentials = MakeTempDirectory();
    IbPaperExecutionProfileConfig config;
    std::string reason;
    assert(IbPaperExecutionProfileConfig::FromValues(
        ValidValues(state, credentials), config, reason));
    const std::string journalPath = state + "/oms-pre-venue-journal.jsonl";
    OmsJournal journal;
    assert(journal.Init(journalPath));
    int venueSends = 0;
    const std::shared_ptr<SequencedKillSwitch> killSwitch(
        new SequencedKillSwitch());
    ExecutionCoordinatorCallbacks coordinatorCallbacks;
    coordinatorCallbacks.preVenuePlaceCheck =
        [killSwitch](const IbPlaceOrderCommand&, std::string* detail) {
            std::string reason;
            const bool blocked = killSwitch->BlocksRiskIncrease(reason);
            if (blocked && detail) *detail = reason;
            return !blocked;
        };
    coordinatorCallbacks.placeIbOrderCorrelated =
        [&](const IBContractLite&, const IBOrderLite&, const std::string&, long*) {
            ++venueSends;
            return true;
        };
    coordinatorCallbacks.validateDecisionLease =
        [](const AgentExecutionContext&, const std::string&, std::string*) {
            return true;
        };
    ExecutionCoordinator coordinator(journal, coordinatorCallbacks);
    IbPaperAuthoritativeRiskSnapshot risk;
    risk.complete = true;
    IbPaperExecutionPolicyCallbacks policyCallbacks;
    policyCallbacks.riskSnapshot = [&]() { return risk; };
    policyCallbacks.nowMs = []() { return static_cast<std::int64_t>(100000); };
    policyCallbacks.authoritativeQuote = [](const std::string&) {
        return FreshQuote(100000);
    };
    IbPaperExecutionPolicyAuthority policy(
        coordinator, config, policyCallbacks, killSwitch);

    IbPlaceOrderCommand command = Place();
    command.context.toolCallId = "pre-venue-kill-switch";
    command.context.decisionLeaseFencingToken = 7;
    command.context.decisionLeaseGeneration = 9;
    const ExecutionCommandResult blocked = policy.PlaceIbOrder(command);
    assert(blocked.status == ExecutionCommandStatus::Rejected);
    assert(blocked.reasonCode == "IB_PAPER_KILL_SWITCH_ENGAGED");
    assert(venueSends == 0);
    const ExecutionCommandResult duplicate = policy.PlaceIbOrder(command);
    assert(duplicate.status == ExecutionCommandStatus::Duplicate);
    assert(venueSends == 0);

    assert(::unlink(journalPath.c_str()) == 0);
    assert(::rmdir(credentials.c_str()) == 0);
    assert(::rmdir(state.c_str()) == 0);
}
}

int main()
{
    TestDefaultHardOffAndStrictConfiguration();
    TestCanonicalIdealproPaperLimits();
    TestAuthorizationCredentialIsExactAndPrivate();
    TestHardLimitsAndIndependentKillSwitch();
    TestPolicyAuthorityUsesOnlyCompleteServiceOwnedSnapshots();
    TestRecoveryOwnerAuditRequiresCompleteStableBrokerEvidence();
    TestRecoveryOwnerAuditScopesActiveOrdersExactly();
    TestRecoveryOwnerAuditRejectsUnmappedAndUncertainEvidence();
    TestAuthoritativeFlattenPolicyAndRecovery();
    TestExternalLimitDayEntryAndAtomicFlatten();
    TestDurableSendAttemptRateBudgetSurvivesRestart();
    TestKillSwitchRecheckedImmediatelyBeforeVenueIo();
    std::cout << "ib_paper_reconcile_fault_matrix_evidence:"
              << " active_snapshot_incomplete_rejected=verified"
              << " active_terminal_correlation_conflict_rejected=verified"
              << " terminal_snapshot_incomplete_rejected=verified"
              << " active_terminal_epoch_mismatch_rejected=verified"
              << " complete_epoch_aligned_reconcile=verified"
              << " recovery_owner_exact_scope=verified"
              << " recovery_owner_unmapped_and_uncertain_rejected=verified"
              << std::endl;
    return 0;
}
