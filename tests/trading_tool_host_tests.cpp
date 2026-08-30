#include "../HeptaTrade/tool_host/trading_tool_host.h"
#include "../HeptaTrade/execution/execution_coordinator.h"
#include "../HeptaTrade/tool_host/session_supervisor_lease_store.h"
#include "../HeptaTrade/tool_host/trading_tool_session_control_plane.h"
#include "../HeptaTrade/intent/bounded_json.h"

#include <cassert>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <fcntl.h>
#include <iostream>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <unordered_map>
#include <unistd.h>

// Keep cache inspection test-only.  The production host deliberately does
// not expose its local replay optimization as API; the friend is used here
// to verify bounded retention/expiry without waiting a wall-clock day.
class TradingToolHostTestAccess
{
public:
    static std::size_t ReplaySize(const TradingToolHost& host)
    {
        std::lock_guard<std::mutex> lock(host.m_mutex);
        return host.m_mutationReplays.size();
    }

    static void ExpireAllReplays(TradingToolHost& host)
    {
        std::lock_guard<std::mutex> lock(host.m_mutex);
        const std::chrono::steady_clock::time_point expired =
            std::chrono::steady_clock::now() - std::chrono::milliseconds(1);
        for (std::unordered_map<std::string,
                 TradingToolHost::MutationReplayRecord>::iterator it =
                 host.m_mutationReplays.begin();
             it != host.m_mutationReplays.end(); ++it)
            it->second.steadyExpiresAt = expired;
    }

    static bool AllReplayPermitsCleared(const TradingToolHost& host)
    {
        std::lock_guard<std::mutex> lock(host.m_mutex);
        for (std::unordered_map<std::string,
                 TradingToolHost::MutationReplayRecord>::const_iterator it =
                 host.m_mutationReplays.begin();
             it != host.m_mutationReplays.end(); ++it)
        {
            if (!it->second.call.previewPermit.empty()) return false;
        }
        return true;
    }
};

namespace {

class RecoveryControlAuthority : public ExecutionControlAuthority
{
public:
	std::function<ExecutionControlResult(const ExecutionControlCommand&)>
		query;

	ExecutionControlResult QueryCommandStatus(
		const ExecutionControlCommand& command) override
	{
		assert(query);
		return query(command);
	}
	ExecutionControlResult FenceSessionOwner(
		const ExecutionControlCommand& command) override
	{
		return Rejected(command);
	}
	ExecutionControlResult ReleaseSessionOwnerFence(
		const ExecutionControlCommand& command) override
	{
		return Rejected(command);
	}
	ExecutionControlResult ReconcileAuthoritativeState(
		const ExecutionControlCommand& command) override
	{
		return Rejected(command);
	}

private:
	static ExecutionControlResult Rejected(
		const ExecutionControlCommand& command)
	{
		ExecutionControlResult result;
		result.commandId = command.context.toolCallId;
		result.reasonCode = "TEST_CONTROL_OPERATION_UNAVAILABLE";
		return result;
	}
};

std::string TempJournalPath()
{
    char path[] = "/tmp/hepta-tool-host-XXXXXX";
    const int fd = mkstemp(path);
    assert(fd >= 0);
    close(fd);
    return std::string(path);
}

std::string TempAbsentPath()
{
    const std::string path = TempJournalPath();
    assert(::unlink(path.c_str()) == 0);
    return path;
}

std::string TempLeaseKeyPath()
{
    const std::string path = TempJournalPath();
    const int fd = ::open(path.c_str(), O_WRONLY | O_TRUNC | O_CLOEXEC);
    assert(fd >= 0);
    const std::string key(32, 'R');
    assert(::write(fd, key.data(), key.size()) ==
        static_cast<ssize_t>(key.size()));
    assert(::fsync(fd) == 0);
    assert(::close(fd) == 0);
    return path;
}

TradingToolCall PlaceCall()
{
    TradingToolCall call;
    call.name = "trade.place_order";
    call.instrument = "EUR.USD";
    call.ibContract.symbol = "EUR";
    call.ibContract.currency = "USD";
    call.ibContract.secType = "CASH";
    call.ibContract.exchange = "IDEALPRO";
    call.ibOrder.action = "BUY";
    call.ibOrder.orderType = "LMT";
    call.timeInForce = "DAY";
    call.ibOrder.totalQuantity = 1000.0;
    call.ibOrder.lmtPrice = 1.1;
    call.previewPermit = "sha256:" + std::string(64, 'a');
    call.expiresAtMs = OmsJournal::NowEpochMs() + 60000;
    return call;
}

class TargetReplayAuthority : public ExecutionAuthority
{
public:
    ExecutionCommandResult PlaceOrder(const PlaceOrderCommand& command) override
    {
        ++placeCalls;
        lastPlace = command;
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Accepted;
        result.commandId = command.context.toolCallId;
        result.orderId = 1901;
        return result;
    }

    ExecutionCommandResult CancelOrder(const CancelOrderCommand& command) override
    {
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Accepted;
        result.commandId = command.context.toolCallId;
        result.orderId = command.orderId;
        return result;
    }

    int placeCalls = 0;
    PlaceOrderCommand lastPlace;
};

std::string TargetJsonStringField(const std::string& json,
                                  const std::string& key)
{
    BoundedJsonValue root;
    std::string reason;
    assert(ParseBoundedJson(json, root, reason));
    const BoundedJsonValue* field = root.Find(key);
    std::string value;
    assert(field != nullptr && field->String(value));
    return value;
}

TradingToolCall HostTargetCall(const std::string& name,
                               double target,
                               std::int64_t expiresAtMs)
{
    TradingToolCall call;
    call.name = name;
    call.instrument = "EUR.USD";
    call.ibOrder.totalQuantity = target;
    call.referencePrice = 5.0;
    call.expiresAtMs = expiresAtMs;
    return call;
}

void BindSchemaHash(const TradingToolRegistry& registry,
                    TradingToolHostRequest& request)
{
    TradingToolDescriptor descriptor;
    assert(registry.GetDescriptor(request.call.name, descriptor));
    request.expectedSchemaHash =
        TradingToolRegistry::DescriptorSchemaHash(descriptor);
}

void TestServerBoundIdentityAndCapabilities()
{
    const std::string path = TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));
    int placeCalls = 0;
    DecisionLeaseManager leases;
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.placeIbOrder = [&](const IBContractLite&, const IBOrderLite&, long* orderId) {
        ++placeCalls;
        *orderId = 8801;
        return true;
    };
    callbacks.cancelIbOrder = [](long) { return true; };
    callbacks.validateDecisionLease = [&](const AgentExecutionContext& context,
                                          const std::string& instrument,
                                          std::string* reason) {
        DecisionLeaseKey key;
        key.executionDomain = context.executionDomain;
        key.account = context.account;
        key.instrument = instrument;
        DecisionLeaseOwner owner;
        owner.agentId = context.agentId;
        owner.sessionId = context.sessionId;
        DecisionLeaseCredential credential;
        credential.fencingToken = context.decisionLeaseFencingToken;
        credential.generation = context.decisionLeaseGeneration;
        const DecisionLeaseResult result = leases.Validate(key, owner, credential);
        if (reason != nullptr) *reason = DecisionLeaseManager::StatusName(result.status);
        return result.status == DecisionLeaseStatus::Valid;
    };
    ExecutionCoordinator execution(journal, callbacks);
    TradingToolRegistry registry(execution);
    TradingToolHost host(registry, leases);

    TradingToolHostSessionBinding watch;
    watch.token = "watch-session-token-00000001";
    watch.peerUid = 1001;
    watch.session.executionContext.agentId = "watch-agent";
    watch.session.executionContext.sessionId = "watch-session";
    watch.session.executionContext.account = "DU123";
    watch.session.environment = "WATCH";
    watch.executionDomain = "IB-PAPER";
    watch.session.capabilities.insert("market.read");
    watch.expiresAtMs = OmsJournal::NowEpochMs() + 60000;
    std::string reason;
    assert(host.RegisterSession(watch, reason));

    TradingToolHostSessionBinding invalidWatch = watch;
    invalidWatch.token = "watch-session-token-00000002";
    invalidWatch.session.capabilities.insert("operator.trade.place");
    assert(!host.RegisterSession(invalidWatch, reason));
    assert(reason == "WATCH_SESSION_CANNOT_TRADE");

    TradingToolHostRequest request;
    request.sessionToken = watch.token;
    request.toolCallId = "watch-place-1";
    request.call = PlaceCall();
    BindSchemaHash(registry, request);
    assert(host.Invoke(1001, request).status == TradingToolCallStatus::PermissionDenied);
    assert(placeCalls == 0);
    assert(host.Invoke(9999, request).reasonCode == "PEER_UID_MISMATCH");
    TradingToolHostRequest watchBeforeSchema = request;
    watchBeforeSchema.toolCallId = "watch-policy-before-schema";
    watchBeforeSchema.expectedSchemaHash =
        "sha256:" + std::string(64, '0');
    assert(host.Invoke(1001, watchBeforeSchema).reasonCode ==
        "WATCH_SESSION_CANNOT_TRADE");

    TradingToolHostSessionBinding paper = watch;
    paper.token = "paper-session-token-0000001";
    paper.session.executionContext.agentId = "paper-agent";
    paper.session.executionContext.sessionId = "paper-session";
    paper.session.environment = "PAPER";
    paper.session.capabilities.insert("operator.trade.place");
    paper.allowedInstruments.insert("EUR.USD");
    paper.instrumentContracts["EUR.USD"] = PlaceCall().ibContract;
    paper.maxOrderQuantity = 1000.0;
    paper.maxTradeCallsPerMinute = 2;
    paper.executionDomain = "IB-PAPER";
    assert(host.RegisterSession(paper, reason));

    std::string actualSchemaHash;
    assert(!host.ValidateSchemaHash(
        "trade.place_order", std::string(), actualSchemaHash));
    assert(actualSchemaHash.find("sha256:") == 0);
    assert(host.ValidateSchemaHash(
        "system.tools.list", std::string(), actualSchemaHash));
    assert(!host.ValidateSchemaHash(
        "system.tools.list", "sha256:" + std::string(64, '0'),
        actualSchemaHash));
    assert(!host.ValidateSchemaHash(
        "vendor.unknown", std::string(), actualSchemaHash));
    assert(actualSchemaHash.empty());

    TradingToolHostRequest schemaRequired;
    schemaRequired.sessionToken = paper.token;
    schemaRequired.toolCallId = "schema-required";
    schemaRequired.call = PlaceCall();
    const TradingToolResult schemaRequiredResult =
        host.Invoke(1001, schemaRequired);
    assert(schemaRequiredResult.reasonCode == "SCHEMA_HASH_REQUIRED");
    assert(schemaRequiredResult.detail.find("sha256:") == 0);

    TradingToolHostRequest schemaMismatch;
    schemaMismatch.sessionToken = paper.token;
    schemaMismatch.toolCallId = "schema-mismatch";
    schemaMismatch.call = PlaceCall();
    schemaMismatch.expectedSchemaHash = "sha256:" + std::string(64, '0');
    const TradingToolResult schemaMismatchResult = host.Invoke(1001, schemaMismatch);
    assert(schemaMismatchResult.reasonCode == "SCHEMA_HASH_MISMATCH");
    assert(schemaMismatchResult.detail.find("sha256:") == 0);

    TradingToolHostSessionBinding derivative = paper;
    derivative.token = "derivative-session-token-001";
    derivative.session.executionContext.agentId = "derivative-agent";
    derivative.session.executionContext.sessionId = "derivative-session";
    derivative.allowedInstruments.clear();
    derivative.instrumentContracts.clear();
    const std::string optionIdentity = "OPT:SPY260721P00500000:USD:SMART";
    IBContractLite optionContract;
    optionContract.symbol = "SPY";
    optionContract.secType = "OPT";
    optionContract.exchange = "SMART";
    optionContract.currency = "USD";
    optionContract.localSymbol = "SPY  260721P00500000";
    derivative.allowedInstruments.insert(optionIdentity);
    derivative.instrumentContracts[optionIdentity] = optionContract;
    assert(host.RegisterSession(derivative, reason));
    TradingToolSessionContractCatalogSnapshot catalog = host.GetContractCatalogSnapshot();
    assert(catalog.sessionCount == 3);
    assert(catalog.contracts.at("EUR.USD").sessionReferences == 1);
    assert(catalog.contracts.at(optionIdentity).sessionReferences == 1);

    TradingToolHostSessionBinding mismatchedDerivative = derivative;
    mismatchedDerivative.token = "derivative-session-token-002";
    mismatchedDerivative.allowedInstruments.clear();
    mismatchedDerivative.instrumentContracts.clear();
    mismatchedDerivative.allowedInstruments.insert("SPY");
    mismatchedDerivative.instrumentContracts["SPY"] = optionContract;
    assert(!host.RegisterSession(mismatchedDerivative, reason));
    assert(reason == "SERVER_CONTRACT_IDENTITY_MISMATCH");

    TradingToolHostRequest derivativeRequest;
    derivativeRequest.sessionToken = derivative.token;
    derivativeRequest.toolCallId = "derivative-place-mismatch";
    derivativeRequest.call = PlaceCall();
    derivativeRequest.call.instrument = optionIdentity;
    derivativeRequest.call.ibContract.localSymbol = "WRONG";
    BindSchemaHash(registry, derivativeRequest);
    assert(host.Invoke(1001, derivativeRequest).reasonCode == "CONTRACT_IDENTITY_MISMATCH");

    request.sessionToken = paper.token;
    request.toolCallId = "paper-place-missing-tif";
    request.call = PlaceCall();
    request.call.timeInForce.clear();
    assert(host.Invoke(1001, request).reasonCode == "INVALID_TIME_IN_FORCE");
    assert(placeCalls == 0);

    request.toolCallId = "paper-place-1";
    request.call = PlaceCall();
    const TradingToolResult accepted = host.Invoke(1001, request);
    assert(accepted.status == TradingToolCallStatus::Ok);
    assert(accepted.orderId == 8801);
    assert(placeCalls == 1);

    TradingToolHostSessionBinding cancelOnly;
    cancelOnly.token = "cancel-session-token-000001";
    cancelOnly.peerUid = 1001;
    cancelOnly.session.executionContext.agentId = "cancel-agent";
    cancelOnly.session.executionContext.sessionId = "cancel-session";
    cancelOnly.session.executionContext.account = "DU123";
    cancelOnly.session.environment = "PAPER";
    cancelOnly.session.capabilities.insert("system.read");
    cancelOnly.session.capabilities.insert("trade.cancel");
    cancelOnly.maxTradeCallsPerMinute = 2;
    cancelOnly.executionDomain = "IB-PAPER";
    cancelOnly.expiresAtMs = OmsJournal::NowEpochMs() + 60000;
    assert(host.RegisterSession(cancelOnly, reason));

    // Queue cancellation is owned by UnixToolServer.  A direct host caller
    // must receive the typed control-plane rejection instead of a misleading
    // registry ``handler unavailable`` result.
    TradingToolHostRequest directCancel;
    directCancel.sessionToken = cancelOnly.token;
    directCancel.toolCallId = "direct-cancel-001";
    directCancel.cancelToolCallId = "queued-cancel-001";
    directCancel.call.name = "system.cancel_request";
    BindSchemaHash(registry, directCancel);
    const TradingToolResult directCancelResult =
        host.Invoke(cancelOnly.peerUid, directCancel);
    assert(directCancelResult.status == TradingToolCallStatus::InvalidTool);
    assert(directCancelResult.reasonCode == "CONTROL_TOOL_REQUIRED");

    // The direct Host API must enforce the same idempotency-key grammar as
    // the typed protocol; otherwise an embedded caller could inject control
    // bytes or an all-punctuation key into replay/journal namespaces.
    TradingToolHostRequest invalidDirectId = directCancel;
    invalidDirectId.toolCallId = "--------";
    assert(host.Invoke(cancelOnly.peerUid, invalidDirectId).reasonCode ==
           "INVALID_COMMAND_ID");
    invalidDirectId.toolCallId = std::string(129, 'a');
    assert(host.Invoke(cancelOnly.peerUid, invalidDirectId).reasonCode ==
           "INVALID_COMMAND_ID");

    TradingToolHostRequest cancelRequest;
    cancelRequest.sessionToken = cancelOnly.token;
    cancelRequest.toolCallId = "cancel-unknown";
    cancelRequest.call.name = "trade.cancel_order";
    cancelRequest.call.orderId = 9999;
    BindSchemaHash(registry, cancelRequest);
    const TradingToolResult unknownCancel = host.Invoke(1001, cancelRequest);
    assert(unknownCancel.reasonCode == "ORDER_OWNER_UNKNOWN");
    cancelRequest.toolCallId = "cancel-spoof";
    cancelRequest.call.instrument = "EUR.USD";
    assert(host.Invoke(1001, cancelRequest).reasonCode == "UNEXPECTED_TOOL_FIELD");

    request.toolCallId = "paper-place-too-large";
    request.call.ibOrder.totalQuantity = 1001.0;
    assert(host.Invoke(1001, request).reasonCode == "AGENT_ORDER_QUANTITY_LIMIT");
    assert(placeCalls == 1);

    request.toolCallId = "paper-place-wrong-instrument";
    request.call = PlaceCall();
    request.call.instrument = "GBP.USD";
    assert(host.Invoke(1001, request).reasonCode == "INSTRUMENT_NOT_ALLOWED");
    assert(placeCalls == 1);

    request.toolCallId = "paper-place-2";
    request.call = PlaceCall();

    TradingToolHostSessionBinding contender = paper;
    contender.token = "paper-session-token-0000002";
    contender.session.executionContext.agentId = "paper-agent-2";
    contender.session.executionContext.sessionId = "paper-session-2";
    assert(host.RegisterSession(contender, reason));
    catalog = host.GetContractCatalogSnapshot();
    assert(catalog.contracts.at("EUR.USD").sessionReferences == 2);
    request.sessionToken = contender.token;
    request.toolCallId = "contender-place-1";
    assert(host.Invoke(1001, request).reasonCode == "DECISION_LEASE_BUSY");
    assert(placeCalls == 1);

    host.RevokeSession(paper.token);
    catalog = host.GetContractCatalogSnapshot();
    assert(catalog.contracts.at("EUR.USD").sessionReferences == 1);
    request.toolCallId = "contender-place-2";
    assert(host.Invoke(1001, request).reasonCode == "DECISION_LEASE_BUSY");
    assert(placeCalls == 1);
    host.RevokeSession(contender.token);
    request.sessionToken = paper.token;
    assert(host.Invoke(1001, request).reasonCode == "SESSION_NOT_FOUND");
    std::remove(path.c_str());
}

void TestReadVisibilityScopeIsServerBound()
{
    const std::string path = TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));
    ExecutionCoordinatorCallbacks executionCallbacks;
    ExecutionCoordinator execution(journal, executionCallbacks);
    bool observedScope = false;
    TradingToolReadCallbacks reads;
    reads.portfolioListPositions = [&](const TradingToolSession& session, const TradingToolCall&,
                                      std::string& payload, std::string&) {
        observedScope = session.visibleInstruments.size() == 1 &&
            session.visibleInstruments.find("OPT:SPY260721P00500000:USD:SMART") !=
                session.visibleInstruments.end() &&
            session.boundInstrumentContracts.find("OPT:SPY260721P00500000:USD:SMART") !=
                session.boundInstrumentContracts.end();
        payload = "{\"positions\":[]}";
        return true;
    };
    TradingToolRegistry registry(execution, reads);
    TradingToolHost host(registry);

    TradingToolHostSessionBinding binding;
    binding.token = "visibility-session-token-001";
    binding.peerUid = 1001;
    binding.session.executionContext.agentId = "visibility-agent";
    binding.session.executionContext.sessionId = "visibility-session";
    binding.session.executionContext.account = "DU123";
    binding.session.environment = "WATCH";
    binding.session.capabilities.insert("portfolio.read");
    binding.allowedInstruments.insert("OPT:SPY260721P00500000:USD:SMART");
    IBContractLite visibleContract;
    visibleContract.symbol = "SPY";
    visibleContract.secType = "OPT";
    visibleContract.exchange = "SMART";
    visibleContract.currency = "USD";
    visibleContract.localSymbol = "SPY  260721P00500000";
    binding.instrumentContracts["OPT:SPY260721P00500000:USD:SMART"] = visibleContract;
    binding.executionDomain = "IB-PAPER";
    binding.expiresAtMs = OmsJournal::NowEpochMs() + 60000;
    std::string reason;
    assert(host.RegisterSession(binding, reason));

    TradingToolHostRequest request;
    request.sessionToken = binding.token;
    request.toolCallId = "visibility-read-1";
    request.call.name = "portfolio.list_positions";
    BindSchemaHash(registry, request);
    const TradingToolResult result = host.Invoke(1001, request);
    assert(result.status == TradingToolCallStatus::Ok);
    assert(observedScope);
    std::remove(path.c_str());
}

void TestOsOwnedSessionControlPlane()
{
    const std::string path = TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));
    ExecutionCoordinatorCallbacks callbacks;
    ExecutionCoordinator execution(journal, callbacks);
    TradingToolRegistry registry(execution);
    TradingToolHost host(registry);
    TradingToolSessionControlPlane controlPlane(host,
        [](const std::string& issuer, const TradingToolHostSessionBinding&, std::string& reason) {
            if (issuer != "hepta.os.test")
            {
                reason = "ISSUER_DENIED";
                return false;
            }
            return true;
        });
    TradingToolHostSessionBinding binding;
    binding.token = "control-plane-token-000001";
    binding.peerUid = static_cast<std::uint32_t>(getuid());
    binding.session.executionContext.agentId = "control-agent";
    binding.session.executionContext.sessionId = "control-session";
    binding.session.executionContext.account = "DU123";
    binding.session.environment = "WATCH";
    binding.executionDomain = "IB-PAPER";
    binding.expiresAtMs = OmsJournal::NowEpochMs() + 60000;
    std::string reason;
    assert(!controlPlane.Provision("agent.claimed.issuer", binding, reason));
    assert(reason == "ISSUER_DENIED");
    assert(host.SessionCount() == 0);
    assert(controlPlane.Provision("hepta.os.test", binding, reason));
    assert(host.SessionCount() == 1);
    std::uint64_t generation = 0;
    assert(controlPlane.Renew("hepta.os.test", binding.token, 1,
        OmsJournal::NowEpochMs() + 120000, generation, reason));
    assert(generation == 2);
    assert(!controlPlane.Renew("hepta.os.test", binding.token, 1,
        OmsJournal::NowEpochMs() + 120000, generation, reason));
    assert(reason == "SESSION_LEASE_GENERATION_MISMATCH");
    const std::string rotatedToken = "control-plane-token-rotated-01";
    assert(controlPlane.Rotate("hepta.os.test", binding.token, rotatedToken, 2,
        OmsJournal::NowEpochMs() + 120000, generation, reason));
    assert(generation == 3);
    binding.token = rotatedToken;
    assert(!controlPlane.Revoke("agent.claimed.issuer", binding.token, 3, reason));
    assert(host.SessionCount() == 1);
    assert(!controlPlane.Revoke("hepta.os.test", binding.token, 2, reason));
    assert(reason == "SESSION_LEASE_GENERATION_MISMATCH");
    assert(controlPlane.Revoke("hepta.os.test", binding.token, 3, reason));
    assert(host.SessionCount() == 0);

    binding.token = "control-plane-token-000002";
    binding.session.executionContext.sessionId = "expired-session";
    binding.expiresAtMs = OmsJournal::NowEpochMs() + 2;
    assert(controlPlane.Provision("hepta.os.test", binding, reason));
    usleep(4000);
    assert(controlPlane.ReapExpired(static_cast<std::uint64_t>(OmsJournal::NowEpochMs())) == 1);
    assert(host.ListSessions().size() == host.SessionCount());
    assert(host.SessionCount() == 0);
    std::remove(path.c_str());
}

void TestMultiInstrumentMutationOwnerHandoff()
{
	const std::string path = TempJournalPath();
	OmsJournal journal;
	assert(journal.Init(path));
	DecisionLeaseManager::TimePoint leaseNow = DecisionLeaseManager::Clock::now();
	DecisionLeaseManager leases([&]() { return leaseNow; }, std::chrono::hours(24));
	long nextOrderId = 9900;
	std::vector<std::uint64_t> fencingTokens;
	ExecutionCoordinatorCallbacks callbacks;
	callbacks.placeIbOrder = [&](const IBContractLite&, const IBOrderLite&, long* orderId) {
		*orderId = ++nextOrderId;
		return true;
	};
	callbacks.validateDecisionLease = [&](const AgentExecutionContext& context,
		const std::string& instrument, std::string* reason) {
		DecisionLeaseKey key;
		key.executionDomain = context.executionDomain;
		key.account = context.account;
		key.instrument = instrument;
		DecisionLeaseOwner owner;
		owner.agentId = context.agentId;
		owner.sessionId = context.sessionId;
		DecisionLeaseCredential credential;
		credential.fencingToken = context.decisionLeaseFencingToken;
		credential.generation = context.decisionLeaseGeneration;
		const DecisionLeaseResult result = leases.Validate(key, owner, credential);
		if (reason != nullptr) *reason = DecisionLeaseManager::StatusName(result.status);
		return result.status == DecisionLeaseStatus::Valid;
	};
	callbacks.onIbOrderPlaced = [&](const IbPlaceOrderCommand& command, long,
		std::string*) {
		fencingTokens.push_back(command.context.decisionLeaseFencingToken);
		return true;
	};
	ExecutionCoordinator execution(journal, callbacks);
	TradingToolRegistry registry(execution);
	TradingToolHost host(registry, leases);
	auto bindingFor = [](const std::string& token, const std::string& agent,
		const std::string& session, const std::string& instrument,
		const IBContractLite& contract) {
		TradingToolHostSessionBinding binding;
		binding.token = token;
		binding.peerUid = 1001;
		binding.session.executionContext.agentId = agent;
		binding.session.executionContext.sessionId = session;
		binding.session.executionContext.account = "DU123";
		binding.session.environment = "PAPER";
		binding.session.capabilities.insert("operator.trade.place");
		binding.allowedInstruments.insert(instrument);
		binding.instrumentContracts[instrument] = contract;
		binding.maxOrderQuantity = 1000.0;
		binding.maxTradeCallsPerMinute = 20;
		binding.executionDomain = "IB-PAPER";
		binding.decisionLeaseTtlMs = 5000;
		binding.expiresAtMs = OmsJournal::NowEpochMs() + 60000;
		return binding;
	};
	IBContractLite eurContract = PlaceCall().ibContract;
	IBContractLite gbpContract = eurContract;
	gbpContract.symbol = "GBP";
	TradingToolHostSessionBinding eurA = bindingFor(
		"handoff-eur-session-token-0001", "eur-agent-a", "eur-session-a",
		"EUR.USD", eurContract);
	TradingToolHostSessionBinding eurB = bindingFor(
		"handoff-eur-session-token-0002", "eur-agent-b", "eur-session-b",
		"EUR.USD", eurContract);
	TradingToolHostSessionBinding gbp = bindingFor(
		"handoff-gbp-session-token-0001", "gbp-agent", "gbp-session",
		"GBP.USD", gbpContract);
	std::string reason;
	assert(host.RegisterSession(eurA, reason));
	assert(host.RegisterSession(eurB, reason));
	assert(host.RegisterSession(gbp, reason));
	auto place = [&registry](const TradingToolHostSessionBinding& binding,
		const std::string& callId, const std::string& instrument,
		const IBContractLite& contract) {
		TradingToolHostRequest request;
		request.sessionToken = binding.token;
		request.toolCallId = callId;
		request.call = PlaceCall();
		request.call.instrument = instrument;
		request.call.ibContract = contract;
		BindSchemaHash(registry, request);
		return request;
	};
	assert(host.Invoke(1001, place(eurA, "eur-a-place", "EUR.USD", eurContract)).status ==
		TradingToolCallStatus::Ok);
	assert(host.Invoke(1001, place(eurB, "eur-b-busy", "EUR.USD", eurContract)).reasonCode ==
		"DECISION_LEASE_BUSY");
	assert(host.Invoke(1001, place(gbp, "gbp-place", "GBP.USD", gbpContract)).status ==
		TradingToolCallStatus::Ok);
	host.RevokeSession(eurA.token);
	assert(host.Invoke(1001, place(eurB, "eur-b-fenced", "EUR.USD", eurContract)).reasonCode ==
		"DECISION_LEASE_BUSY");
	leaseNow += std::chrono::milliseconds(5001);
	assert(host.Invoke(1001, place(eurB, "eur-b-handoff", "EUR.USD", eurContract)).status ==
		TradingToolCallStatus::Ok);
	assert(fencingTokens.size() == 3);
	assert(fencingTokens[0] != fencingTokens[1]);
	assert(fencingTokens[2] > fencingTokens[0]);
	std::remove(path.c_str());
}

void TestFailClosedTwoPhaseOwnerFence()
{
	const std::string path = TempJournalPath();
	OmsJournal journal;
	assert(journal.Init(path));
	ExecutionCoordinatorCallbacks callbacks;
	ExecutionCoordinator execution(journal, callbacks);
	TradingToolRegistry registry(execution);
	TradingToolHost host(registry);

	TradingToolHostSessionBinding binding;
	binding.token = "pending-fence-session-token-0001";
	binding.peerUid = 1001;
	binding.session.executionContext.agentId = "pending-agent";
	binding.session.executionContext.sessionId = "pending-session";
	binding.session.executionContext.account = "DU123";
	binding.session.environment = "PAPER";
	binding.session.capabilities.insert("operator.trade.place");
	binding.allowedInstruments.insert("EUR.USD");
	binding.instrumentContracts["EUR.USD"] = PlaceCall().ibContract;
	binding.maxOrderQuantity = 1000.0;
	binding.maxTradeCallsPerMinute = 10;
	binding.executionDomain = "IB-PAPER";
	binding.expiresAtMs = OmsJournal::NowEpochMs() + 60000;
	std::string reason;
	assert(host.RegisterSession(binding, reason));

	bool remoteReady = false;
	std::size_t attempts = 0;
	host.SetSessionRevokedObserver(
		[&](const TradingToolHostSessionBinding& observed,
			const std::string& revokeReason,
			std::string& failureReason) {
			++attempts;
			assert(observed.session.executionContext.agentId == "pending-agent");
			assert(observed.session.executionContext.sessionId == "pending-session");
			assert(!observed.enabled);
			assert(revokeReason == "session_revoked");
			if (!remoteReady)
			{
				failureReason = "REMOTE_FENCE_UNAVAILABLE";
				return false;
			}
			failureReason.clear();
			return true;
		});

	assert(!host.RevokeCurrentSessionIfOwner(
		binding.token, "different-agent", "pending-session",
		"session_revoked", reason));
	assert(reason == "SESSION_OWNER_IDENTITY_MISMATCH");
	TradingToolHostSessionBinding stillEnabled;
	assert(host.GetSession(binding.token, stillEnabled));
	assert(stillEnabled.enabled && attempts == 0);

	assert(!host.RevokeSession(binding.token, 1, reason));
	assert(reason == "REMOTE_FENCE_UNAVAILABLE");
	assert(attempts == 1 && host.SessionCount() == 1);
	TradingToolHostSessionBinding pending;
	assert(host.GetSession(binding.token, pending));
	assert(!pending.enabled);

	TradingToolHostRequest mutation;
	mutation.sessionToken = binding.token;
	mutation.toolCallId = "pending-fence-mutation";
	mutation.call = PlaceCall();
	BindSchemaHash(registry, mutation);
	assert(host.Invoke(binding.peerUid, mutation).reasonCode == "SESSION_DISABLED");

	TradingToolHostSessionBinding bypass = binding;
	bypass.token = "pending-fence-session-token-0002";
	assert(!host.RegisterSession(bypass, reason));
	assert(reason == "SESSION_OWNER_FENCE_PENDING");
	std::uint64_t generation = 0;
	assert(!host.UpdateSessionLease(binding.token,
		"pending-fence-session-token-0003", 1,
		OmsJournal::NowEpochMs() + 60000, generation, reason));
	assert(reason == "SESSION_OWNER_FENCE_PENDING");

	remoteReady = true;
	assert(host.RevokeSession(binding.token, 1, reason));
	assert(reason.empty());
	assert(attempts == 2 && host.SessionCount() == 0);
	assert(host.RegisterSession(bypass, reason));
	host.RevokeSession(bypass.token);
	std::remove(path.c_str());
}

void TestInFlightMutationCannotCrossFailedOwnerFence()
{
	class CountingAuthority : public ExecutionAuthority
	{
	public:
		explicit CountingAuthority(std::atomic<int>& placeCalls)
			: m_placeCalls(placeCalls) {}

		ExecutionCommandResult PlaceOrder(
			const PlaceOrderCommand& command) override
		{
			++m_placeCalls;
			ExecutionCommandResult result;
			result.status = ExecutionCommandStatus::Accepted;
			result.commandId = command.context.toolCallId;
			result.orderId = 1;
			return result;
		}

		ExecutionCommandResult CancelOrder(
			const CancelOrderCommand& command) override
		{
			ExecutionCommandResult result;
			result.status = ExecutionCommandStatus::Accepted;
			result.commandId = command.context.toolCallId;
			return result;
		}

	private:
		std::atomic<int>& m_placeCalls;
	};

	std::atomic<int> placeCalls(0);
	std::atomic<bool> readinessEntered(false);
	std::atomic<bool> releaseReadiness(false);
	CountingAuthority authority(placeCalls);
	TradingToolRegistry registry(authority);
	DecisionLeaseManager leases;
	TradingToolHost host(
		registry, leases,
		[&](const TradingToolSession&, const TradingToolCall&,
			std::string&) {
			readinessEntered.store(true);
			while (!releaseReadiness.load()) usleep(1000);
			return true;
		});

	TradingToolHostSessionBinding binding;
	binding.token = "inflight-fence-session-token-0001";
	binding.peerUid = 1001;
	binding.session.executionContext.agentId = "inflight-agent";
	binding.session.executionContext.sessionId = "inflight-session";
	binding.session.executionContext.account = "DU123";
	binding.session.environment = "PAPER";
	binding.session.capabilities.insert("operator.trade.place");
	binding.allowedInstruments.insert("EUR.USD");
	binding.instrumentContracts["EUR.USD"] = PlaceCall().ibContract;
	binding.maxOrderQuantity = 1000.0;
	binding.maxTradeCallsPerMinute = 10;
	binding.executionDomain = "IB-PAPER";
	binding.expiresAtMs = OmsJournal::NowEpochMs() + 60000;
	std::string reason;
	assert(host.RegisterSession(binding, reason));
	host.SetSessionRevokedObserver(
		[](const TradingToolHostSessionBinding& observed,
			const std::string&, std::string& failureReason) {
			assert(!observed.enabled);
			failureReason = "REMOTE_FENCE_UNAVAILABLE";
			return false;
		});

	TradingToolHostRequest mutation;
	mutation.sessionToken = binding.token;
	mutation.toolCallId = "inflight-place";
	mutation.call = PlaceCall();
	BindSchemaHash(registry, mutation);
	TradingToolResult mutationResult;
	std::thread mutationThread([&]() {
		mutationResult = host.Invoke(binding.peerUid, mutation);
	});
	for (int i = 0; i < 2000 && !readinessEntered.load(); ++i)
		usleep(1000);
	assert(readinessEntered.load());

	assert(!host.RevokeSession(binding.token, 1, reason));
	assert(reason == "REMOTE_FENCE_UNAVAILABLE");
	releaseReadiness.store(true);
	mutationThread.join();

	assert(mutationResult.status == TradingToolCallStatus::PermissionDenied);
	assert(mutationResult.reasonCode == "SESSION_OWNER_FENCE_PENDING");
	assert(placeCalls.load() == 0);
	TradingToolHostSessionBinding pending;
	assert(host.GetSession(binding.token, pending));
	assert(!pending.enabled);
}

void TestInFlightMutationLinearizesBeforeLeaseRotation()
{
	class BlockingAuthority : public ExecutionAuthority
	{
	public:
		BlockingAuthority(std::atomic<bool>& entered,
			std::atomic<bool>& release)
			: m_entered(entered), m_release(release) {}

		ExecutionCommandResult PlaceOrder(
			const PlaceOrderCommand& command) override
		{
			m_entered.store(true);
			while (!m_release.load()) usleep(1000);
			ExecutionCommandResult result;
			result.status = ExecutionCommandStatus::Accepted;
			result.commandId = command.context.toolCallId;
			result.orderId = 2;
			return result;
		}

		ExecutionCommandResult CancelOrder(
			const CancelOrderCommand& command) override
		{
			ExecutionCommandResult result;
			result.status = ExecutionCommandStatus::Accepted;
			result.commandId = command.context.toolCallId;
			return result;
		}

	private:
		std::atomic<bool>& m_entered;
		std::atomic<bool>& m_release;
	};

	std::atomic<bool> authorityEntered(false);
	std::atomic<bool> releaseAuthority(false);
	std::atomic<bool> rotateStarted(false);
	std::atomic<bool> rotateFinished(false);
	BlockingAuthority authority(authorityEntered, releaseAuthority);
	TradingToolRegistry registry(authority);
	DecisionLeaseManager leases;
	TradingToolHost host(registry, leases);

	TradingToolHostSessionBinding binding;
	binding.token = "inflight-rotate-session-token-0001";
	binding.peerUid = 1001;
	binding.session.executionContext.agentId = "inflight-rotate-agent";
	binding.session.executionContext.sessionId = "inflight-rotate-session";
	binding.session.executionContext.account = "DU123";
	binding.session.environment = "PAPER";
	binding.session.capabilities.insert("operator.trade.place");
	binding.allowedInstruments.insert("EUR.USD");
	binding.instrumentContracts["EUR.USD"] = PlaceCall().ibContract;
	binding.maxOrderQuantity = 1000.0;
	binding.maxTradeCallsPerMinute = 10;
	binding.executionDomain = "IB-PAPER";
	binding.expiresAtMs = OmsJournal::NowEpochMs() + 60000;
	std::string reason;
	assert(host.RegisterSession(binding, reason));

	TradingToolHostRequest mutation;
	mutation.sessionToken = binding.token;
	mutation.toolCallId = "inflight-rotate-place";
	mutation.call = PlaceCall();
	BindSchemaHash(registry, mutation);
	TradingToolResult mutationResult;
	std::thread mutationThread([&]() {
		mutationResult = host.Invoke(binding.peerUid, mutation);
	});
	for (int i = 0; i < 2000 && !authorityEntered.load(); ++i)
		usleep(1000);
	assert(authorityEntered.load());

	const std::string replacementToken =
		"inflight-rotate-session-token-0002";
	bool rotated = false;
	std::uint64_t newGeneration = 0;
	std::string rotateReason;
	std::thread rotateThread([&]() {
		rotateStarted.store(true);
		rotated = host.UpdateSessionLease(
			binding.token, replacementToken, 1,
			OmsJournal::NowEpochMs() + 120000,
			newGeneration, rotateReason);
		rotateFinished.store(true);
	});
	for (int i = 0; i < 2000 && !rotateStarted.load(); ++i)
		usleep(1000);
	assert(rotateStarted.load());
	usleep(50000);
	assert(!rotateFinished.load());

	releaseAuthority.store(true);
	mutationThread.join();
	rotateThread.join();
	assert(mutationResult.status == TradingToolCallStatus::Ok);
	assert(rotated && rotateReason.empty() && newGeneration == 2);
	TradingToolHostSessionBinding replacement;
	assert(!host.GetSession(binding.token, replacement));
	assert(host.GetSession(replacementToken, replacement));
	assert(replacement.enabled && replacement.leaseGeneration == 2);
	host.RevokeSession(replacementToken);
}

void TestInFlightPreviewLinearizesBeforeOwnerFence()
{
	const std::string path = TempJournalPath();
	OmsJournal journal;
	assert(journal.Init(path));
	ExecutionCoordinatorCallbacks callbacks;
	ExecutionCoordinator execution(journal, callbacks);
	std::atomic<bool> previewEntered(false);
	std::atomic<bool> releasePreview(false);
	std::atomic<bool> previewExited(false);
	TradingToolReadCallbacks reads;
	reads.riskPreviewOrder = [&](const TradingToolSession&,
		const TradingToolCall&, std::string& payload, std::string&) {
		previewEntered.store(true);
		while (!releasePreview.load()) usleep(1000);
		payload =
			"{\"approved\":true,\"preview_permit\":\"sha256:"
			"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\","
			"\"command_id\":\"hexec-command-"
			"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\"}";
		previewExited.store(true);
		return true;
	};
	TradingToolRegistry registry(execution, reads);
	DecisionLeaseManager leases;
	TradingToolHost host(registry, leases);

	TradingToolHostSessionBinding binding;
	binding.token = "inflight-preview-session-token-0001";
	binding.peerUid = 1001;
	binding.session.executionContext.agentId = "inflight-preview-agent";
	binding.session.executionContext.sessionId = "inflight-preview-session";
	binding.session.executionContext.account = "DU123";
	binding.session.environment = "PAPER";
	binding.session.capabilities.insert("operator.risk.preview");
	binding.allowedInstruments.insert("EUR.USD");
	binding.instrumentContracts["EUR.USD"] = PlaceCall().ibContract;
	binding.maxOrderQuantity = 1000.0;
	binding.executionDomain = "IB-PAPER";
	binding.expiresAtMs = OmsJournal::NowEpochMs() + 60000;
	std::string reason;
	assert(host.RegisterSession(binding, reason));

	std::atomic<int> fenceCalls(0);
	host.SetSessionRevokedObserver(
		[&](const TradingToolHostSessionBinding& observed,
			const std::string&, std::string& failureReason) {
			assert(previewExited.load());
			assert(observed.session.executionContext.agentId ==
				"inflight-preview-agent");
			++fenceCalls;
			failureReason.clear();
			return true;
		});

	TradingToolHostRequest preview;
	preview.sessionToken = binding.token;
	preview.toolCallId = "inflight-preview-read";
	preview.call = PlaceCall();
	preview.call.name = "risk.preview_order";
	// The raw-place fixture carries a mutation permit; risk preview is a
	// separate read operation and must never receive that one-time credential.
	preview.call.previewPermit.clear();
	BindSchemaHash(registry, preview);
	TradingToolResult previewResult;
	std::thread previewThread([&]() {
		previewResult = host.Invoke(binding.peerUid, preview);
	});
	for (int i = 0; i < 2000 && !previewEntered.load(); ++i)
		usleep(1000);
	assert(previewEntered.load());

	std::atomic<bool> revokeStarted(false);
	std::atomic<bool> revokeFinished(false);
	bool revoked = false;
	std::string revokeReason;
	std::thread revokeThread([&]() {
		revokeStarted.store(true);
		revoked = host.RevokeSession(
			binding.token, 1, "preview_race", revokeReason);
		revokeFinished.store(true);
	});
	for (int i = 0; i < 2000 && !revokeStarted.load(); ++i)
		usleep(1000);
	assert(revokeStarted.load());
	usleep(50000);
	assert(!revokeFinished.load());
	assert(fenceCalls.load() == 0);

	releasePreview.store(true);
	previewThread.join();
	revokeThread.join();
	assert(previewResult.status == TradingToolCallStatus::Ok);
	assert(revoked && revokeReason.empty());
	assert(fenceCalls.load() == 1);
	assert(host.SessionCount() == 0);
	std::remove(path.c_str());
}

void TestRecoveryQueryLinearizesAfterIngressAndClosesEntry()
{
	const std::string path = TempJournalPath();
	const std::string leasePath = TempAbsentPath();
	const std::string keyPath = TempLeaseKeyPath();
	OmsJournal journal;
	assert(journal.Init(path));
	SessionSupervisorLeaseStore store;
	std::string reason;
	assert(store.Init(leasePath, keyPath, reason));
	ExecutionCoordinatorCallbacks callbacks;
	ExecutionCoordinator execution(journal, callbacks);
	std::atomic<bool> previewEntered(false);
	std::atomic<bool> releasePreview(false);
	std::atomic<bool> previewExited(false);
	TradingToolReadCallbacks reads;
	reads.riskPreviewOrder = [&](const TradingToolSession&,
		const TradingToolCall&, std::string& payload, std::string&) {
		previewEntered.store(true);
		while (!releasePreview.load()) usleep(1000);
		payload = "{\"approved\":true}";
		previewExited.store(true);
		return true;
	};
	TradingToolRegistry registry(execution, reads);
	DecisionLeaseManager leases;
	TradingToolHost host(registry, leases);
	TradingToolHostSessionBinding binding;
	binding.token = "root-recovery-query-session-token-0001";
	binding.peerUid = 1001;
	binding.session.executionContext.agentId = "recovery-query-agent";
	binding.session.executionContext.sessionId = "recovery-query-session";
	binding.session.executionContext.account = "DU123";
	binding.session.environment = "PAPER";
	binding.session.capabilities.insert("operator.risk.preview");
	// Recovery checks cover both the legacy raw preview path and the ordinary
	// target-position intent path.  The latter must not be able to re-enter by
	// choosing a target that happens to increase exposure.
	binding.session.capabilities.insert("risk.read");
	binding.session.capabilities.insert("intent.apply");
	binding.allowedInstruments.insert("EUR.USD");
	binding.instrumentContracts["EUR.USD"] = PlaceCall().ibContract;
	binding.maxOrderQuantity = 1000.0;
	binding.maxTradeCallsPerMinute = 10;
	binding.executionDomain = "IB-PAPER";
	binding.expiresAtMs = OmsJournal::NowEpochMs() + 60000;
	assert(host.RegisterSession(binding, reason));
	SessionSupervisorLeaseRecord leaseRecord;
	leaseRecord.templateId = "paper";
	leaseRecord.issuer = "hepta.os.root-custodian";
	leaseRecord.token = binding.token;
	leaseRecord.agentId = binding.session.executionContext.agentId;
	leaseRecord.sessionId = binding.session.executionContext.sessionId;
	leaseRecord.ownerAccount = binding.session.executionContext.account;
	leaseRecord.ownerExecutionDomain = binding.executionDomain;
	leaseRecord.peerUid = binding.peerUid;
	leaseRecord.expiresAtMs = binding.expiresAtMs;
	leaseRecord.leaseGeneration = binding.leaseGeneration;
	assert(store.Put(leaseRecord, reason));

	std::atomic<int> queryCalls(0);
	RecoveryControlAuthority recoveryAuthority;
	recoveryAuthority.query =
		[&](const ExecutionControlCommand& command) {
			assert(previewExited.load());
			assert(command.recoveryIngressFence == 1);
			assert(command.targetCommandId ==
				"hexec-command-ingress-race");
			++queryCalls;
			ExecutionControlResult result;
			result.targetCommandId = command.targetCommandId;
			result.status = ExecutionCommandStatus::Rejected;
			result.reasonCode = "EXECUTION_COMMAND_NOT_FOUND";
			result.serviceEpoch = "hexec-v6-race";
			result.serviceFencingGeneration = 3;
			return result;
		};
	host.SetRecoveryControlAuthority(&recoveryAuthority);
	ExecutionControlResult failedStatus;
	std::string failedReason;
	SessionSupervisorLeaseStore failedStore;
	SessionSupervisorLeaseRecord failedRecord = leaseRecord;
	assert(!host.EnterRecoveryOnlyAndQuery(
		binding.token, 1, "hexec-command-ingress-race",
		failedStore, failedRecord, failedStatus, failedReason));
	assert(failedReason == "LEASE_STORE_TOKEN_NOT_FOUND");
	assert(queryCalls.load() == 0);
	TradingToolHostSessionBinding unchanged;
	assert(host.GetSession(binding.token, unchanged));
	assert(!unchanged.recoveryOnly);
	SessionSupervisorLeaseRecord unchangedLease;
	assert(store.Get(binding.token, unchangedLease));
	assert(!unchangedLease.recoveryOnly);

	TradingToolHostRequest preview;
	preview.sessionToken = binding.token;
	preview.toolCallId = "recovery-query-preview-inflight";
	preview.call = PlaceCall();
	preview.call.name = "risk.preview_order";
	preview.call.previewPermit.clear();
	BindSchemaHash(registry, preview);
	TradingToolResult previewResult;
	std::thread previewThread([&]() {
		previewResult = host.Invoke(binding.peerUid, preview);
	});
	for (int i = 0; i < 2000 && !previewEntered.load(); ++i)
		usleep(1000);
	assert(previewEntered.load());

	std::atomic<bool> queryFinished(false);
	std::atomic<bool> durableCommitReached(false);
	std::atomic<bool> releaseDurableCommit(false);
	struct DurableCommitContext
	{
		std::atomic<bool>* reached;
		std::atomic<bool>* release;
	};
	DurableCommitContext durableContext = {
		&durableCommitReached, &releaseDurableCommit};
	ExecutionControlResult commandStatus;
	bool queried = false;
	std::string queryReason;
	std::thread queryThread([&]() {
		queried = host.EnterRecoveryOnlyAndQuery(
			binding.token, 1, "hexec-command-ingress-race",
			store, leaseRecord, commandStatus, queryReason,
			[](void* rawContext) {
				DurableCommitContext& context =
					*static_cast<DurableCommitContext*>(rawContext);
				context.reached->store(true);
				while (!context.release->load()) usleep(1000);
			},
			&durableContext);
		queryFinished.store(true);
	});
	usleep(50000);
	assert(!queryFinished.load() && queryCalls.load() == 0);
	releasePreview.store(true);
	previewThread.join();
	for (int i = 0; i < 2000 && !durableCommitReached.load(); ++i)
		usleep(1000);
	assert(durableCommitReached.load());
	SessionSupervisorLeaseRecord durable;
	assert(store.Get(binding.token, durable));
	assert(durable.recoveryOnly);
	assert(durable.recoveryCommandId == "hexec-command-ingress-race");

	// The lease is already durable, but the committer deliberately has not
	// returned to install the in-memory flag.  A new entry preview must wait on
	// the same dispatch lock and cannot cross this exact window.
	TradingToolHostRequest postCommitPreview = preview;
	postCommitPreview.toolCallId = "recovery-query-preview-after-store-commit";
	std::atomic<bool> postCommitFinished(false);
	TradingToolResult postCommitResult;
	std::thread postCommitThread([&]() {
		postCommitResult = host.Invoke(binding.peerUid, postCommitPreview);
		postCommitFinished.store(true);
	});
	usleep(50000);
	assert(!postCommitFinished.load());
	releaseDurableCommit.store(true);
	queryThread.join();
	postCommitThread.join();
	assert(previewResult.status == TradingToolCallStatus::Ok);
	assert(queried && queryReason.empty() && queryCalls.load() == 1);
	assert(commandStatus.reasonCode == "EXECUTION_COMMAND_NOT_FOUND");
	assert(postCommitFinished.load());
	assert(postCommitResult.status == TradingToolCallStatus::PermissionDenied);
	assert(postCommitResult.reasonCode == "SESSION_RECOVERY_ONLY");

	preview.toolCallId = "recovery-query-preview-after-fence";
	const TradingToolResult blocked = host.Invoke(binding.peerUid, preview);
	assert(blocked.status == TradingToolCallStatus::PermissionDenied);
	assert(blocked.reasonCode == "SESSION_RECOVERY_ONLY");

	TradingToolHostRequest targetPreview;
	targetPreview.sessionToken = binding.token;
	targetPreview.toolCallId = "recovery-target-preview-blocked";
	targetPreview.call.name = "intent.preview_target_position";
	targetPreview.call.instrument = "EUR.USD";
	targetPreview.call.ibOrder.totalQuantity = 100.0;
	targetPreview.call.referencePrice = 5.0;
	targetPreview.call.expiresAtMs = OmsJournal::NowEpochMs() + 30000;
	BindSchemaHash(registry, targetPreview);
	const TradingToolResult targetPreviewBlocked = host.Invoke(
		binding.peerUid, targetPreview);
	assert(targetPreviewBlocked.status == TradingToolCallStatus::PermissionDenied);
	assert(targetPreviewBlocked.reasonCode == "SESSION_RECOVERY_ONLY");

	TradingToolHostRequest targetApply = targetPreview;
	targetApply.toolCallId = "recovery-target-apply-blocked";
	targetApply.call.name = "intent.apply_target_position";
	targetApply.call.previewPermit = "sha256:" + std::string(64, 'a');
	BindSchemaHash(registry, targetApply);
	const TradingToolResult targetApplyBlocked = host.Invoke(
		binding.peerUid, targetApply);
	assert(targetApplyBlocked.status == TradingToolCallStatus::PermissionDenied);
	assert(targetApplyBlocked.reasonCode == "SESSION_RECOVERY_ONLY");
	std::remove(path.c_str());
	std::remove(leasePath.c_str());
	std::remove(keyPath.c_str());
}

void TestInFlightWatchReadLinearizesBeforeOwnerFence()
{
	const std::string path = TempJournalPath();
	OmsJournal journal;
	assert(journal.Init(path));
	ExecutionCoordinatorCallbacks callbacks;
	ExecutionCoordinator execution(journal, callbacks);
	std::atomic<bool> readEntered(false);
	std::atomic<bool> releaseRead(false);
	std::atomic<bool> readExited(false);
	TradingToolReadCallbacks reads;
	reads.systemGetHealth = [&](const TradingToolSession&,
		const TradingToolCall&, std::string& payload, std::string&) {
		readEntered.store(true);
		while (!releaseRead.load()) usleep(1000);
		payload = "{\"ready\":true}";
		readExited.store(true);
		return true;
	};
	TradingToolRegistry registry(execution, reads);
	TradingToolHost host(registry);

	TradingToolHostSessionBinding binding;
	binding.token = "inflight-watch-read-session-token-0001";
	binding.peerUid = 1001;
	binding.session.executionContext.agentId = "inflight-watch-read-agent";
	binding.session.executionContext.sessionId =
		"inflight-watch-read-session";
	binding.session.executionContext.account = "DU123";
	binding.session.environment = "WATCH";
	binding.session.capabilities.insert("system.read");
	binding.executionDomain = "IB-PAPER";
	binding.expiresAtMs = OmsJournal::NowEpochMs() + 60000;
	std::string reason;
	assert(host.RegisterSession(binding, reason));

	std::atomic<int> fenceCalls(0);
	host.SetSessionRevokedObserver(
		[&](const TradingToolHostSessionBinding& observed,
			const std::string&, std::string& failureReason) {
			assert(readExited.load());
			assert(observed.session.executionContext.agentId ==
				"inflight-watch-read-agent");
			++fenceCalls;
			failureReason.clear();
			return true;
		});

	TradingToolHostRequest request;
	request.sessionToken = binding.token;
	request.toolCallId = "inflight-watch-health-read";
	request.call.name = "system.get_health";
	BindSchemaHash(registry, request);
	TradingToolResult readResult;
	std::thread readThread([&]() {
		readResult = host.Invoke(binding.peerUid, request);
	});
	for (int i = 0; i < 2000 && !readEntered.load(); ++i)
		usleep(1000);
	assert(readEntered.load());

	std::atomic<bool> revokeStarted(false);
	std::atomic<bool> revokeFinished(false);
	bool revoked = false;
	std::string revokeReason;
	std::thread revokeThread([&]() {
		revokeStarted.store(true);
		revoked = host.RevokeSession(
			binding.token, 1, "watch_read_race", revokeReason);
		revokeFinished.store(true);
	});
	for (int i = 0; i < 2000 && !revokeStarted.load(); ++i)
		usleep(1000);
	assert(revokeStarted.load());
	usleep(50000);
	assert(!revokeFinished.load());
	assert(fenceCalls.load() == 0);

	releaseRead.store(true);
	readThread.join();
	revokeThread.join();
	assert(readResult.status == TradingToolCallStatus::Ok);
	assert(revoked && revokeReason.empty());
	assert(fenceCalls.load() == 1);
	assert(host.SessionCount() == 0);
	std::remove(path.c_str());
}

void TestEntryCancelAndFlattenBudgetsAreIndependent()
{
	const std::string path = TempJournalPath();
	OmsJournal journal;
	assert(journal.Init(path));
	DecisionLeaseManager leases;
	ExecutionCoordinatorCallbacks callbacks;
	callbacks.placeIbOrder = [](const IBContractLite&, const IBOrderLite&,
		long* orderId) {
		*orderId = 9901;
		return true;
	};
	callbacks.cancelIbOrder = [](long) { return true; };
	callbacks.validateDecisionLease = [&](const AgentExecutionContext& context,
		const std::string& instrument, std::string* reason) {
		DecisionLeaseKey key;
		key.executionDomain = context.executionDomain;
		key.account = context.account;
		key.instrument = instrument;
		DecisionLeaseOwner owner;
		owner.agentId = context.agentId;
		owner.sessionId = context.sessionId;
		DecisionLeaseCredential credential;
		credential.fencingToken = context.decisionLeaseFencingToken;
		credential.generation = context.decisionLeaseGeneration;
		const DecisionLeaseResult result = leases.Validate(
			key, owner, credential);
		if (reason != nullptr)
			*reason = DecisionLeaseManager::StatusName(result.status);
		return result.status == DecisionLeaseStatus::Valid;
	};
	ExecutionCoordinator execution(journal, callbacks);
	TradingToolReadCallbacks reads;
	reads.riskPreviewFlatten = [](
		const TradingToolSession&, const TradingToolCall&,
		std::string& payload, std::string&) {
		payload = "{\"approved\":true}";
		return true;
	};
	TradingToolTradeCallbacks trades;
	trades.flattenPosition = [](
		const TradingToolSession& session, const TradingToolCall&) {
		ExecutionCommandResult result;
		result.status = ExecutionCommandStatus::Accepted;
		result.commandId = session.executionContext.toolCallId;
		return result;
	};
	TradingToolRegistry registry(execution, reads, trades);
	TradingToolHost host(registry, leases);

	TradingToolHostSessionBinding binding;
	binding.token = "independent-rate-budget-session-0001";
	binding.peerUid = 1001;
	binding.session.executionContext.agentId = "rate-budget-agent";
	binding.session.executionContext.sessionId = "rate-budget-session";
	binding.session.executionContext.account = "DU123";
	binding.session.environment = "PAPER";
	binding.session.capabilities.insert("operator.trade.place");
	binding.session.capabilities.insert("trade.cancel");
	binding.session.capabilities.insert("trade.flatten");
	binding.allowedInstruments.insert("EUR.USD");
	binding.instrumentContracts["EUR.USD"] = PlaceCall().ibContract;
	binding.maxOrderQuantity = 1000.0;
	binding.maxTradeCallsPerMinute = 1;
	binding.executionDomain = "IB-PAPER";
	binding.expiresAtMs = OmsJournal::NowEpochMs() + 60000;
	std::string reason;
	assert(host.RegisterSession(binding, reason));

	TradingToolHostRequest place;
	place.sessionToken = binding.token;
	place.toolCallId = "independent-entry-first";
	place.call = PlaceCall();
	BindSchemaHash(registry, place);
	assert(host.Invoke(binding.peerUid, place).status ==
		TradingToolCallStatus::Ok);
	const TradingToolResult placeReplay =
		host.Invoke(binding.peerUid, place);
	assert(placeReplay.status == TradingToolCallStatus::Duplicate);
	assert(placeReplay.orderId == 9901);
	place.toolCallId = "independent-entry-second";
	assert(host.Invoke(binding.peerUid, place).reasonCode ==
		"AGENT_TRADE_RATE_LIMIT");

	TradingToolHostRequest cancel;
	cancel.sessionToken = binding.token;
	cancel.call.name = "trade.cancel_order";
	cancel.call.orderId = 123456;
	BindSchemaHash(registry, cancel);
	for (int attempt = 0; attempt < 4; ++attempt)
	{
		cancel.toolCallId = "independent-cancel-" +
			std::to_string(attempt + 1);
		assert(host.Invoke(binding.peerUid, cancel).reasonCode !=
			"AGENT_RISK_REDUCTION_RATE_LIMIT");
	}
	cancel.toolCallId = "independent-cancel-5";
	assert(host.Invoke(binding.peerUid, cancel).reasonCode ==
		"AGENT_RISK_REDUCTION_RATE_LIMIT");

	TradingToolHostRequest flatten;
	flatten.sessionToken = binding.token;
	flatten.toolCallId = "independent-flatten-after-cancels";
	flatten.call.name = "trade.flatten_position";
	flatten.call.instrument = "EUR.USD";
	flatten.call.previewPermit =
		"sha256:" + std::string(64, 'a');
	BindSchemaHash(registry, flatten);
	assert(host.Invoke(binding.peerUid, flatten).status ==
		TradingToolCallStatus::Ok);
	flatten.toolCallId = "independent-flatten-second";
	assert(host.Invoke(binding.peerUid, flatten).status ==
		TradingToolCallStatus::Ok);
	flatten.toolCallId = "independent-flatten-after-cancels";
	const TradingToolResult replay = host.Invoke(binding.peerUid, flatten);
	assert(replay.status == TradingToolCallStatus::Duplicate);
	assert(replay.reasonCode == "DUPLICATE_TOOL_CALL");
	TradingToolHostRequest conflict;
	conflict.sessionToken = binding.token;
	conflict.toolCallId = flatten.toolCallId;
	conflict.call.name = "trade.cancel_order";
	conflict.call.orderId = 123456;
	BindSchemaHash(registry, conflict);
	assert(host.Invoke(binding.peerUid, conflict).reasonCode ==
		"IDEMPOTENCY_KEY_CONFLICT");
	flatten.toolCallId = "independent-flatten-third";
	assert(host.Invoke(binding.peerUid, flatten).reasonCode ==
		"AGENT_RISK_REDUCTION_RATE_LIMIT");
	std::remove(path.c_str());
}

void TestAcceptedTargetReplayBypassesBusyLease()
{
    TargetReplayAuthority authority;
    TradingToolReadCallbacks reads;
    reads.systemGetHealth = [](const TradingToolSession&,
                               const TradingToolCall&,
                               std::string& payload,
                               std::string&) {
        payload = "{\"event_watermark\":5,\"execution_service_fencing_generation\":9,"
                  "\"gateway_ready\":true,"
                  "\"remote_execution_ready\":true,\"execution_service_epoch\":\"epoch-a\"}";
        return true;
    };
    std::int64_t quoteObservedAtMs = 0;
    reads.marketGetQuote = [&quoteObservedAtMs](const TradingToolSession&,
                                                const TradingToolCall& call,
                                                std::string& payload,
                                                std::string&) {
        if (quoteObservedAtMs == 0)
            quoteObservedAtMs = OmsJournal::NowEpochMs();
        payload = "{\"ask\":1.1002,\"authoritative\":true,\"stale\":false,"
                  "\"observed_at_ms\":" + std::to_string(quoteObservedAtMs) +
                  ",\"instrument\":\"" + call.instrument +
                  "\",\"bid\":1.1000}";
        return true;
    };
    reads.accountGetSummary = [](const TradingToolSession&,
                                 const TradingToolCall&,
                                 std::string& payload,
                                 std::string&) {
        payload = "{\"authoritative\":true}";
        return true;
    };
    reads.portfolioListPositions = [](const TradingToolSession&,
                                      const TradingToolCall&,
                                      std::string& payload,
                                      std::string&) {
        payload = "{\"authoritative\":true,\"positions\":[{"
                  "\"instrument\":\"EUR.USD\",\"quantity\":10}]}";
        return true;
    };
    reads.ordersList = [](const TradingToolSession&,
                          const TradingToolCall&,
                          std::string& payload,
                          std::string&) {
        payload = "{\"authoritative\":true,\"orders\":[]}";
        return true;
    };
    reads.riskGetLimits = [](const TradingToolSession&,
                             const TradingToolCall&,
                             std::string& payload,
                             std::string&) {
        payload = "{\"authoritative\":true,\"max_order_quantity\":25000}";
        return true;
    };
    reads.riskPreviewOrder = [](const TradingToolSession&,
                                const TradingToolCall& call,
                                std::string& payload,
                                std::string&) {
        payload = "{\"authoritative\":true,\"preview_permit\":\"sha256:"
                  "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
                  "\",\"mutation_command_id\":\"host-target-mutation-001\","
                  "\"expires_at_ms\":" + std::to_string(call.expiresAtMs) + "}";
        return true;
    };

    TradingToolRegistry registry(authority, reads);
    DecisionLeaseManager leases;
    bool readiness = true;
    bool throwReadiness = false;
    int readinessCalls = 0;
    TradingToolHost host(
        registry, leases,
        [&](const TradingToolSession&, const TradingToolCall&,
            std::string& reason) {
            ++readinessCalls;
            if (throwReadiness)
                throw std::runtime_error("readiness callback failure");
            if (!readiness)
            {
                reason = "remote execution became unavailable";
                return false;
            }
            reason.clear();
            return true;
        });
    TradingToolHostSessionBinding binding;
    binding.token = "host-target-replay-session-token-001";
    binding.peerUid = 1001;
    binding.session.executionContext.agentId = "host-target-replay-agent";
    binding.session.executionContext.sessionId = "host-target-replay-session";
    binding.session.executionContext.account = "DU123";
    binding.session.executionContext.venue = "IB";
    binding.session.environment = "PAPER";
    binding.session.capabilities.insert("system.read");
    binding.session.capabilities.insert("risk.read");
    binding.session.capabilities.insert("intent.apply");
    binding.session.capabilities.insert("operator.trade.place");
    binding.allowedInstruments.insert("EUR.USD");
    binding.instrumentContracts["EUR.USD"] = PlaceCall().ibContract;
    binding.maxOrderQuantity = 1000.0;
    binding.maxTradeCallsPerMinute = 4;
    binding.executionDomain = "IB-PAPER";
    binding.decisionLeaseTtlMs = 5000;
    binding.expiresAtMs = OmsJournal::NowEpochMs() + 60000;
    std::string reason;
    assert(host.RegisterSession(binding, reason));

    const std::int64_t expires = OmsJournal::NowEpochMs() + 30000;
    TradingToolHostRequest preview;
    preview.sessionToken = binding.token;
    preview.toolCallId = "host-target-preview-001";
    preview.call = HostTargetCall(
        "intent.preview_target_position", 100.0, expires);
    BindSchemaHash(registry, preview);
    const TradingToolResult previewEnvelope = host.Invoke(
        binding.peerUid, preview);
    assert(previewEnvelope.status == TradingToolCallStatus::Ok);
    const std::string permit = TargetJsonStringField(
        previewEnvelope.payloadJson, "preview_permit");
    const std::string mutationId = TargetJsonStringField(
        previewEnvelope.payloadJson, "mutation_command_id");

    TradingToolHostRequest apply;
    apply.sessionToken = binding.token;
    apply.toolCallId = mutationId;
    apply.call = HostTargetCall(
        "intent.apply_target_position", 100.0, expires);
    apply.call.previewPermit = permit;
    BindSchemaHash(registry, apply);
    assert(host.Invoke(binding.peerUid, apply).status ==
           TradingToolCallStatus::Ok);
    assert(authority.placeCalls == 1);
    assert(readinessCalls == 1);

    // Fence the original lease and grant it to another owner.  A replay must
    // still resolve from the host ledger before readiness and
    // EnsureDecisionLease instead of surfacing TRADING_STATE_NOT_READY,
    // DECISION_LEASE_BUSY or dispatching a second order.
    readiness = false;
    DecisionLeaseOwner owner;
    owner.agentId = binding.session.executionContext.agentId;
    owner.sessionId = binding.session.executionContext.sessionId;
    leases.FenceOwner(owner);
    DecisionLeaseKey key;
    key.executionDomain = binding.executionDomain;
    key.account = binding.session.executionContext.account;
    key.instrument = "EUR.USD";
    DecisionLeaseOwner blocker;
    blocker.agentId = "host-target-replay-blocker";
    blocker.sessionId = "host-target-replay-blocker-session";
    assert(leases.Acquire(key, blocker, std::chrono::milliseconds(5000)).Succeeded());

    const TradingToolResult replay = host.Invoke(binding.peerUid, apply);
    assert(replay.status == TradingToolCallStatus::Duplicate);
    assert(replay.reasonCode == "DUPLICATE_TOOL_CALL");
    assert(authority.placeCalls == 1);
    assert(readinessCalls == 1);

    // A new mutation whose pre-dispatch readiness callback throws must fail
    // closed without reaching the authority. The callback exception itself is
    // never exposed over the tool wire.
    throwReadiness = true;
    readiness = true;
    TradingToolHostRequest readinessFailure;
    readinessFailure.sessionToken = binding.token;
    readinessFailure.toolCallId = "host-readiness-exception-001";
    readinessFailure.call = PlaceCall();
    BindSchemaHash(registry, readinessFailure);
    const TradingToolResult readinessResult = host.Invoke(
        binding.peerUid, readinessFailure);
    assert(readinessResult.status == TradingToolCallStatus::Rejected);
    assert(readinessResult.reasonCode == "TRADING_STATE_NOT_READY");
    assert(readinessResult.detail == "mutation readiness check failed");
    assert(authority.placeCalls == 1);
}

void TestMutationReplayCacheBoundedAndExpires()
{
    TargetReplayAuthority authority;
    TradingToolRegistry registry(authority);
    DecisionLeaseManager leases;
    TradingToolHost host(registry, leases);

    TradingToolHostSessionBinding binding;
    binding.token = "host-mutation-cache-session-token-001";
    binding.peerUid = 1001;
    binding.session.executionContext.agentId = "host-mutation-cache-agent";
    binding.session.executionContext.sessionId = "host-mutation-cache-session";
    binding.session.executionContext.account = "DU123";
    binding.session.executionContext.venue = "IB";
    binding.session.environment = "PAPER";
    binding.session.capabilities.insert("operator.trade.place");
    binding.allowedInstruments.insert("EUR.USD");
    binding.instrumentContracts["EUR.USD"] = PlaceCall().ibContract;
    binding.maxOrderQuantity = 1000.0;
    // The test intentionally drives more than the host's 2048-entry local
    // replay bound.  Keep the session/rate budget above that workload.
    binding.maxTradeCallsPerMinute = 3000;
    binding.executionDomain = "IB-PAPER";
    binding.decisionLeaseTtlMs = 5000;
    binding.expiresAtMs = OmsJournal::NowEpochMs() + 10 * 60 * 1000;
    std::string reason;
    assert(host.RegisterSession(binding, reason));

    const std::size_t totalCalls = 2050;
    TradingToolHostRequest lastRequest;
    for (std::size_t i = 0; i < totalCalls; ++i)
    {
        TradingToolHostRequest request;
        request.sessionToken = binding.token;
        request.toolCallId = "host-mutation-cache-" + std::to_string(i);
        request.call = PlaceCall();
        BindSchemaHash(registry, request);
        const TradingToolResult result = host.Invoke(binding.peerUid, request);
        assert(result.status == TradingToolCallStatus::Ok);
        lastRequest = request;
    }
    assert(authority.placeCalls == static_cast<int>(totalCalls));
    assert(TradingToolHostTestAccess::ReplaySize(host) == 2048);
    // A one-time preview credential must not be retained in the host-local
    // replay witness after the authority has accepted the mutation.
    assert(TradingToolHostTestAccess::AllReplayPermitsCleared(host));

    // A still-live exact retry is deterministic and does not dispatch again.
    const TradingToolResult duplicate = host.Invoke(
        binding.peerUid, lastRequest);
    assert(duplicate.status == TradingToolCallStatus::Duplicate);
    assert(duplicate.reasonCode == "DUPLICATE_TOOL_CALL");
    assert(authority.placeCalls == static_cast<int>(totalCalls));

    // Expire the local optimization without waiting 24 hours.  The same
    // command then falls through to the durable authority and is reinserted,
    // proving expiry pruning rather than an unbounded stale replay map.
    TradingToolHostTestAccess::ExpireAllReplays(host);
    const TradingToolResult afterExpiry = host.Invoke(
        binding.peerUid, lastRequest);
    assert(afterExpiry.status == TradingToolCallStatus::Ok);
    assert(authority.placeCalls == static_cast<int>(totalCalls + 1));
    assert(TradingToolHostTestAccess::ReplaySize(host) == 1);
}

} // namespace

int main()
{
    TestServerBoundIdentityAndCapabilities();
    TestReadVisibilityScopeIsServerBound();
	TestOsOwnedSessionControlPlane();
	TestMultiInstrumentMutationOwnerHandoff();
	TestFailClosedTwoPhaseOwnerFence();
	TestInFlightMutationCannotCrossFailedOwnerFence();
	TestInFlightMutationLinearizesBeforeLeaseRotation();
	TestInFlightPreviewLinearizesBeforeOwnerFence();
	TestRecoveryQueryLinearizesAfterIngressAndClosesEntry();
	TestInFlightWatchReadLinearizesBeforeOwnerFence();
	TestEntryCancelAndFlattenBudgetsAreIndependent();
	TestAcceptedTargetReplayBypassesBusyLease();
	TestMutationReplayCacheBoundedAndExpires();
    std::cout << "trading_tool_host_tests: PASS" << std::endl;
    return 0;
}
