#include "../HeptaTrade/events/execution_event_hub.h"
#include "../HeptaTrade/execution/execution_coordinator.h"
#include "../HeptaTrade/simulator/deterministic_execution_venue.h"
#include "../HeptaTrade/state/authoritative_trading_snapshot_store.h"
#include "../HeptaTrade/tool_host/typed_tool_protocol.h"
#include "../HeptaTrade/tool_host/session_supervisor_protocol.h"
#include "../HeptaTrade/tool_host/session_supervisor_lease_store.h"
#include "../HeptaTrade/tool_host/trading_tool_session_control_plane.h"
#include "../HeptaTrade/tool_host/unix_session_supervisor_server.h"
#include "../HeptaTrade/tool_host/unix_tool_server.h"

#include <cassert>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <string>
#include <map>
#include <sys/socket.h>
#include <sys/un.h>
#include <thread>
#include <unistd.h>
#include <vector>

namespace {

constexpr int kLocalSocketTimeoutMs = 5000;

std::string TempPath(const char* pattern)
{
    std::string value(pattern);
    std::vector<char> buffer(value.begin(), value.end());
    buffer.push_back('\0');
    const int fd = mkstemp(buffer.data());
    assert(fd >= 0);
    close(fd);
    unlink(buffer.data());
    return std::string(buffer.data());
}

std::string TempKeyPath()
{
	const std::string path = TempPath("/tmp/hepta-sim-supervisor-key-XXXXXX");
	const int fd = open(path.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
	assert(fd >= 0);
	const std::string key(32, 'S');
	assert(write(fd, key.data(), key.size()) == static_cast<ssize_t>(key.size()));
	assert(fsync(fd) == 0);
	close(fd);
	return path;
}

SessionSupervisorResult InvokeSupervisor(const std::string& socketPath,
	const SessionSupervisorRequest& request)
{
	const int client = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
	assert(client >= 0);
	sockaddr_un address;
	std::memset(&address, 0, sizeof(address));
	address.sun_family = AF_UNIX;
	std::strncpy(address.sun_path, socketPath.c_str(), sizeof(address.sun_path) - 1);
	assert(connect(client, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0);
	std::string reason;
	std::string body;
	assert(SessionSupervisorProtocol::EncodeRequest(request, body, reason));
	assert(TypedToolProtocol::WriteFrame(
		client, body, kLocalSocketTimeoutMs, reason));
	std::string response;
	assert(TypedToolProtocol::ReadFrame(
		client, 4096, kLocalSocketTimeoutMs, response, reason));
	SessionSupervisorResult result;
	assert(SessionSupervisorProtocol::DecodeResult(response, result, reason));
	close(client);
	return result;
}

std::string InvokeSocket(const std::string& socketPath,
                         const TradingToolRegistry& registry,
                         TradingToolHostRequest request)
{
    TradingToolDescriptor descriptor;
    assert(registry.GetDescriptor(request.call.name, descriptor));
    request.expectedSchemaHash =
        TradingToolRegistry::DescriptorSchemaHash(descriptor);
    const int client = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    assert(client >= 0);
    sockaddr_un address;
    std::memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    std::strncpy(address.sun_path, socketPath.c_str(), sizeof(address.sun_path) - 1);
    assert(connect(client, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0);
    std::string reason;
    std::string body;
    assert(TypedToolProtocol::EncodeRequest(request, body, reason));
    assert(TypedToolProtocol::WriteFrame(
        client, body, kLocalSocketTimeoutMs, reason));
    std::string response;
    assert(TypedToolProtocol::ReadFrame(
        client, 65536, kLocalSocketTimeoutMs, response, reason));
    close(client);
    return response;
}

long long JsonInteger(const std::string& json, const std::string& key)
{
    const std::string marker = std::string("\"") + key + "\":";
    const std::size_t start = json.rfind(marker);
    assert(start != std::string::npos);
    const char* begin = json.c_str() + start + marker.size();
    char* end = nullptr;
    const long long value = std::strtoll(begin, &end, 10);
    assert(end != begin);
    return value;
}

TradingToolHostRequest PlaceRequest(const std::string& token, const std::string& callId, double price)
{
    TradingToolHostRequest request;
    request.sessionToken = token;
    request.toolCallId = callId;
    request.call.name = "trade.place_order";
    request.call.instrument = "EUR.USD";
    request.call.ibContract.symbol = "EUR";
    request.call.ibContract.currency = "USD";
    request.call.ibContract.secType = "CASH";
    request.call.ibContract.exchange = "SIM";
    request.call.ibOrder.action = "BUY";
    request.call.ibOrder.orderType = "LMT";
    request.call.timeInForce = "DAY";
    request.call.ibOrder.totalQuantity = 100.0;
    request.call.ibOrder.lmtPrice = price;
    request.call.referencePrice = 1.1001;
    request.call.expiresAtMs = OmsJournal::NowEpochMs() + 60000;
    return request;
}

TradingToolHostRequest WaitRequest(const std::string& token, const std::string& callId, std::uint64_t cursor)
{
    TradingToolHostRequest request;
    request.sessionToken = token;
    request.toolCallId = callId;
    request.call.name = "events.wait";
    request.call.afterEventSequence = cursor;
    request.call.waitTimeoutMs = 100;
    return request;
}

void TestAgentToolSocketToSimulatorLifecycle()
{
    const std::string journalPath = TempPath("/tmp/hepta-sim-e2e-journal-XXXXXX");
    const std::string socketPath = TempPath("/tmp/hepta-sim-e2e-socket-XXXXXX");
	const std::string supervisorSocketPath = TempPath("/tmp/hepta-sim-supervisor-socket-XXXXXX");
	const std::string supervisorStorePath = TempPath("/tmp/hepta-sim-supervisor-store-XXXXXX");
	const std::string supervisorKeyPath = TempKeyPath();
    OmsJournal journal;
    assert(journal.Init(journalPath));
    DeterministicExecutionVenue venue;
    venue.SetQuote("EUR.USD", 1.1000, 1.1002);
    ExecutionEventHub events(32);
    DecisionLeaseManager leases;
    AuthoritativeTradingSnapshotStore snapshots;
    const std::uint64_t bootstrapMs = static_cast<std::uint64_t>(OmsJournal::NowEpochMs());
    assert(snapshots.SetExecutionState(true, true, bootstrapMs, "SIM.connection").accepted);
    AuthoritativeQuote initialQuote;
    initialQuote.instrument = "EUR.USD";
    initialQuote.bid = 1.1000;
    initialQuote.ask = 1.1002;
    initialQuote.last = 1.1001;
    assert(snapshots.UpsertQuote(initialQuote, bootstrapMs, "SIM.quote").accepted);
    AuthoritativeAccount account;
    account.account = "SIM-PAPER";
    account.currency = "USD";
    account.hasNetLiquidation = true;
    account.netLiquidation = 100000.0;
    std::vector<AuthoritativeAccount> accounts(1, account);
    assert(snapshots.ReplaceAccounts(accounts, bootstrapMs, "SIM.account_end").accepted);
    assert(snapshots.ReplacePositions(std::vector<AuthoritativePosition>(), bootstrapMs,
                                      "SIM.position_end").accepted);
    assert(snapshots.ReplaceActiveOrders(std::vector<AuthoritativeActiveOrder>(), bootstrapMs,
                                         "SIM.open_order_end").accepted);
    std::map<long, IBOrderLite> submittedOrders;

    ExecutionCoordinatorCallbacks callbacks;
    callbacks.placeIbOrder = [&](const IBContractLite& contract, const IBOrderLite& order, long* orderId) {
        const bool placed = venue.PlaceOrder(contract, order, orderId);
        if (placed && orderId != nullptr) submittedOrders[*orderId] = order;
        return placed;
    };
    callbacks.canCancelIbOrder = [&](long orderId, std::string* reason) { return venue.CanCancelOrder(orderId, reason); };
    callbacks.cancelIbOrder = [&](long orderId) { return venue.CancelOrder(orderId); };
    callbacks.lastIbRejectReason = [&]() { return venue.LastRejectReason(); };
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
    venue.SetEventSink([&](const SimulatedOrderEvent& simEvent) {
        ExecutionOrderOwner owner;
        assert(execution.GetOrderOwner(simEvent.orderId, owner));
        ExecutionEvent event;
        event.executionDomain = "SIM-PAPER";
        event.agentId = owner.agentId;
        event.sessionId = owner.sessionId;
        event.type = simEvent.status == "Filled" ? "order.fill" : "order.status";
        event.venue = "SIM";
        event.orderId = simEvent.orderId;
        event.instrument = simEvent.instrument;
        event.side = simEvent.side;
        event.status = simEvent.status;
        event.filledQuantity = simEvent.filledQuantity;
        event.remainingQuantity = simEvent.remainingQuantity;
        event.averageFillPrice = simEvent.averageFillPrice;
        events.Publish(event);
        const std::uint64_t nowMs = static_cast<std::uint64_t>(OmsJournal::NowEpochMs());
        const std::map<long, IBOrderLite>::const_iterator request = submittedOrders.find(simEvent.orderId);
        assert(request != submittedOrders.end());
        if (simEvent.status == "Submitted")
        {
            AuthoritativeActiveOrder active;
            active.venue = "SIM";
            active.orderId = simEvent.orderId;
            active.account = "SIM-PAPER";
            active.instrument = simEvent.instrument;
            active.side = simEvent.side == "BUY" ? AuthoritativeOrderSide::Buy : AuthoritativeOrderSide::Sell;
            active.type = request->second.orderType == "MKT" ? AuthoritativeOrderType::Market : AuthoritativeOrderType::Limit;
            active.status = AuthoritativeActiveOrderStatus::Submitted;
            active.totalQuantity = request->second.totalQuantity;
            active.remainingQuantity = simEvent.remainingQuantity;
            active.limitPrice = request->second.orderType == "LMT" ? request->second.lmtPrice : 0.0;
            assert(snapshots.UpsertActiveOrder(active, nowMs, "SIM.order_status").accepted);
        }
        if (simEvent.status == "Filled" || simEvent.status == "Cancelled")
        {
            assert(snapshots.EraseActiveOrder("SIM", simEvent.orderId, nowMs,
                                              "SIM.order_terminal").accepted);
            std::vector<AuthoritativePosition> positions;
            const double position = venue.Position(simEvent.instrument);
            if (position != 0.0)
            {
                AuthoritativePosition value;
                value.account = "SIM-PAPER";
                value.instrument = simEvent.instrument;
                value.quantity = position;
                value.averageCost = simEvent.averageFillPrice;
                positions.push_back(value);
            }
            assert(snapshots.ReplacePositions(positions, nowMs, "SIM.position_end").accepted);
            execution.RecordOrderTerminal(simEvent.orderId);
        }
    });

    std::atomic<bool> expiryBlockerEntered(false);
    std::atomic<bool> releaseExpiryBlocker(false);
    TradingToolReadCallbacks reads;
    reads.marketGetQuote = [&](const TradingToolSession& session, const TradingToolCall& call,
                               std::string& payload, std::string& reason) {
		if (session.executionContext.toolCallId == "block-expiry-inflight")
		{
			expiryBlockerEntered.store(true, std::memory_order_release);
			while (!releaseExpiryBlocker.load(std::memory_order_acquire))
				usleep(1000);
		}
		else if (session.executionContext.toolCallId.find("block-") == 0)
			usleep(250000);
        const std::uint64_t nowMs = static_cast<std::uint64_t>(OmsJournal::NowEpochMs());
        const AuthoritativeQuoteRecord quote = snapshots.GetQuote(call.instrument, nowMs, 5000);
        if (quote.state.availability != AuthoritativeSnapshotAvailability::Fresh)
        { reason = "QUOTE_NOT_READY"; return false; }
        payload = std::string("{\"instrument\":\"") + call.instrument + "\",\"bid\":" +
                  std::to_string(quote.value.bid) + ",\"ask\":" + std::to_string(quote.value.ask) +
                  ",\"snapshot_version\":" + std::to_string(quote.state.updatedAtVersion) + "}";
        return true;
    };
    reads.eventsWait = [&](const TradingToolSession& session, const TradingToolCall& call,
                            std::string& payload, std::string& reason) {
        ExecutionEvent event;
        if (!events.WaitNext(session.executionContext.executionDomain,
                             session.executionContext.agentId, session.executionContext.sessionId,
                             call.afterEventSequence,
                             call.waitTimeoutMs, event)) { reason = "EVENT_WAIT_TIMEOUT"; return false; }
        payload = ExecutionEventHub::ToJson(event);
        return true;
    };
    TradingToolTradeCallbacks trades;
    TradingToolRegistry registry(execution, reads, trades);
    TradingToolDescriptor unavailableFlatten;
    assert(!registry.GetDescriptor(
        "trade.flatten_position", unavailableFlatten));
    TradingToolMutationReadiness readiness = [&](const TradingToolSession& session,
                                                 const TradingToolCall& call,
                                                 std::string& reason) {
        if (call.name == "trade.cancel_order") return true;
        const AuthoritativeTradingSnapshot snapshot = snapshots.GetSnapshot(
            static_cast<std::uint64_t>(OmsJournal::NowEpochMs()));
        if (!snapshot.executionState.connected || !snapshot.executionState.authoritative)
        { reason = "EXECUTION_STATE_NOT_AUTHORITATIVE"; return false; }
        if (!snapshot.positionsState.complete ||
            snapshot.positionsState.availability != AuthoritativeSnapshotAvailability::Fresh)
        { reason = "POSITIONS_NOT_READY"; return false; }
        if (!snapshot.activeOrdersState.complete ||
            snapshot.activeOrdersState.availability != AuthoritativeSnapshotAvailability::Fresh)
        { reason = "ACTIVE_ORDERS_NOT_READY"; return false; }
        for (std::map<AuthoritativeOrderKey, AuthoritativeActiveOrderRecord>::const_iterator it =
                 snapshot.activeOrders.begin(); it != snapshot.activeOrders.end(); ++it)
        {
            if (it->second.value.account == session.executionContext.account &&
                it->second.value.instrument == call.instrument)
            { reason = "INSTRUMENT_HAS_ACTIVE_ORDER"; return false; }
        }
        return true;
    };
    TradingToolHost host(registry, leases, readiness);
    TradingToolHostSessionBinding binding;
    binding.token = "simulator-session-token-0001";
    binding.peerUid = static_cast<std::uint32_t>(getuid());
    binding.session.executionContext.agentId = "sim-agent";
    binding.session.executionContext.sessionId = "sim-session";
    binding.session.executionContext.account = "SIM-PAPER";
    binding.session.executionContext.venue = "SIM";
    binding.session.executionContext.strategy = "e2e";
    binding.session.environment = "PAPER";
    binding.session.capabilities.insert("market.read");
    binding.session.capabilities.insert("events.read");
    binding.session.capabilities.insert("system.read");
    binding.session.capabilities.insert("trade.place");
    binding.session.capabilities.insert("trade.cancel");
    binding.session.capabilities.insert("trade.flatten");
    binding.allowedInstruments.insert("EUR.USD");
    IBContractLite boundContract;
    boundContract.symbol = "EUR";
    boundContract.currency = "USD";
    boundContract.secType = "CASH";
    boundContract.exchange = "SIM";
    binding.instrumentContracts["EUR.USD"] = boundContract;
    binding.maxOrderQuantity = 1000.0;
    binding.maxTradeCallsPerMinute = 20;
    binding.executionDomain = "SIM-PAPER";
    binding.expiresAtMs = static_cast<std::uint64_t>(OmsJournal::NowEpochMs()) + 60000;
    std::string reason;
    assert(host.RegisterSession(binding, reason));
    UnixToolServer server(host);
    server.AllowMissingDecisionAuditForTests();
    assert(server.Start(socketPath, reason));

    TradingToolHostRequest quote;
    quote.sessionToken = binding.token;
    quote.toolCallId = "quote-001";
    quote.call.name = "market.get_quote";
    quote.call.instrument = "EUR.USD";
    assert(InvokeSocket(socketPath, registry, quote).find("\"status\":\"ok\"") != std::string::npos);

    const std::string placed = InvokeSocket(
        socketPath, registry, PlaceRequest(binding.token, "place-fill", 1.1003));
    assert(placed.find("\"status\":\"ok\"") != std::string::npos);
    const long fillOrderId = static_cast<long>(JsonInteger(placed, "order_id"));
    venue.Process();
    std::string event = InvokeSocket(
        socketPath, registry, WaitRequest(binding.token, "wait-submitted", 0));
    assert(event.find("Submitted") != std::string::npos);
    std::uint64_t cursor = static_cast<std::uint64_t>(JsonInteger(event, "sequence"));
    event = InvokeSocket(
        socketPath, registry, WaitRequest(binding.token, "wait-filled", cursor));
    assert(event.find("Filled") != std::string::npos);
    assert(JsonInteger(event, "order_id") == fillOrderId);
    cursor = static_cast<std::uint64_t>(JsonInteger(event, "sequence"));
    assert(venue.Position("EUR.USD") == 100.0);

    // The Simulator fixture deliberately has no authoritative flatten
    // preview/permit authority, so flatten remains unpublished. Offset the
    // test position through the ordinary, policy-checked place path instead.
    TradingToolHostRequest offset =
        PlaceRequest(binding.token, "offset-fill", 1.0999);
    offset.call.ibOrder.action = "SELL";
    assert(InvokeSocket(socketPath, registry, offset).find(
        "\"status\":\"ok\"") != std::string::npos);
    venue.Process();
    event = InvokeSocket(
        socketPath, registry, WaitRequest(binding.token, "wait-flatten-submitted", cursor));
    assert(event.find("Submitted") != std::string::npos);
    cursor = static_cast<std::uint64_t>(JsonInteger(event, "sequence"));
    event = InvokeSocket(
        socketPath, registry, WaitRequest(binding.token, "wait-flatten-filled", cursor));
    assert(event.find("Filled") != std::string::npos);
    cursor = static_cast<std::uint64_t>(JsonInteger(event, "sequence"));
    assert(venue.Position("EUR.USD") == 0.0);

    const std::string resting = InvokeSocket(
        socketPath, registry, PlaceRequest(binding.token, "place-rest", 1.0990));
    const long restingOrderId = static_cast<long>(JsonInteger(resting, "order_id"));
    venue.Process();
    event = InvokeSocket(
        socketPath, registry, WaitRequest(binding.token, "wait-rest-submitted", cursor));
    assert(event.find("Submitted") != std::string::npos);
    cursor = static_cast<std::uint64_t>(JsonInteger(event, "sequence"));

    TradingToolHostRequest cancel;
    cancel.sessionToken = binding.token;
    cancel.toolCallId = "cancel-rest";
    cancel.call.name = "trade.cancel_order";
    cancel.call.orderId = restingOrderId;
    assert(InvokeSocket(socketPath, registry, cancel).find("\"status\":\"ok\"") != std::string::npos);
    venue.Process();
    event = InvokeSocket(
        socketPath, registry, WaitRequest(binding.token, "wait-cancelled", cursor));
    assert(event.find("Cancelled") != std::string::npos);
    assert(JsonInteger(event, "order_id") == restingOrderId);

	const int agentCount = 8;
	std::vector<TradingToolHostSessionBinding> soakBindings;
	std::vector<SessionSupervisorRequest> soakSessions;
	TradingToolSessionControlPlane controlPlane(host,
		[](const std::string& issuer, const TradingToolHostSessionBinding&, std::string&) {
			return issuer == "hepta.sim.os";
		});
	SessionSupervisorLeaseStore supervisorStore;
	assert(supervisorStore.Init(supervisorStorePath, supervisorKeyPath, reason));
	UnixSessionSupervisorServer supervisor(controlPlane);
	supervisor.SetLeaseStore(&supervisorStore);
	std::map<std::uint32_t, std::string> issuers;
	issuers[static_cast<std::uint32_t>(getuid())] = "hepta.sim.os";
	assert(supervisor.Start(supervisorSocketPath, issuers,
		[&](const SessionSupervisorRequest& request,
			TradingToolHostSessionBinding& resolved, std::string&) {
			resolved = binding;
			resolved.token = request.token;
			resolved.peerUid = request.peerUid;
			resolved.session.executionContext.agentId = request.agentId;
			resolved.session.executionContext.sessionId = request.sessionId;
			resolved.session.environment = "WATCH";
			resolved.session.capabilities.clear();
			resolved.session.capabilities.insert("market.read");
			resolved.session.capabilities.insert("system.read");
			resolved.expiresAtMs = static_cast<std::uint64_t>(OmsJournal::NowEpochMs()) +
				request.ttlMs;
			resolved.leaseGeneration = 1;
			return request.templateId == "watch";
		}, reason, 4096, 1000));
	for (int i = 0; i < agentCount; ++i)
	{
		TradingToolHostSessionBinding soak = binding;
		soak.token = "simulator-soak-session-token-" + std::to_string(i);
		soak.session.executionContext.agentId = "sim-soak-agent-" + std::to_string(i);
		soak.session.executionContext.sessionId = "sim-soak-session-" + std::to_string(i);
		soak.session.capabilities.clear();
		soak.session.capabilities.insert("market.read");
		SessionSupervisorRequest provision;
		provision.operation = SessionSupervisorOperation::Provision;
		provision.templateId = "watch";
		provision.token = soak.token;
		provision.agentId = soak.session.executionContext.agentId;
		provision.sessionId = soak.session.executionContext.sessionId;
		provision.peerUid = soak.peerUid;
		provision.ttlMs = 60000;
		const SessionSupervisorResult provisioned =
			InvokeSupervisor(supervisorSocketPath, provision);
		assert(provisioned.accepted && provisioned.leaseGeneration == 1);
		soakBindings.push_back(soak);
		soakSessions.push_back(provision);
	}
	assert(host.SessionCount() == static_cast<std::size_t>(agentCount + 1));
	const std::uint64_t soakQuoteMs =
		static_cast<std::uint64_t>(OmsJournal::NowEpochMs());
	assert(snapshots.UpsertQuote(
		initialQuote, soakQuoteMs, "SIM.quote.soak_refresh").accepted);
	std::atomic<int> successfulQuotes(0);
	std::vector<std::thread> agents;
	for (int i = 0; i < agentCount; ++i)
	{
		const std::string soakToken = soakBindings[i].token;
		agents.push_back(std::thread([&, i, soakToken]() {
			for (int iteration = 0; iteration < 32; ++iteration)
			{
				TradingToolHostRequest soakQuote;
				soakQuote.sessionToken = soakToken;
				soakQuote.toolCallId = "soak-quote-" + std::to_string(i) + "-" +
					std::to_string(iteration);
				soakQuote.call.name = "market.get_quote";
				soakQuote.call.instrument = "EUR.USD";
				if (InvokeSocket(socketPath, registry, soakQuote).find("\"status\":\"ok\"") !=
					std::string::npos)
					++successfulQuotes;
			}
		}));
	}
	const std::chrono::steady_clock::time_point firstQuoteDeadline =
		std::chrono::steady_clock::now() + std::chrono::seconds(10);
	while (successfulQuotes.load() == 0 &&
		std::chrono::steady_clock::now() < firstQuoteDeadline)
		std::this_thread::sleep_for(std::chrono::milliseconds(1));
	assert(successfulQuotes.load() > 0);
	for (int i = 0; i < agentCount; ++i)
	{
		SessionSupervisorRequest lifecycle = soakSessions[i];
		lifecycle.expectedGeneration = 1;
		lifecycle.ttlMs = 60000;
		if (i < agentCount / 2)
			lifecycle.operation = SessionSupervisorOperation::Renew;
		else
		{
			lifecycle.operation = SessionSupervisorOperation::Rotate;
			lifecycle.replacementToken = lifecycle.token + "-rotated";
		}
		const SessionSupervisorResult changed =
			InvokeSupervisor(supervisorSocketPath, lifecycle);
		assert(changed.accepted && changed.leaseGeneration == 2);
		if (i >= agentCount / 2)
		{
			soakSessions[i].token = lifecycle.replacementToken;
			soakBindings[i].token = lifecycle.replacementToken;
		}
		soakSessions[i].expectedGeneration = 2;
	}
	for (std::size_t i = 0; i < agents.size(); ++i) agents[i].join();
	assert(successfulQuotes.load() > 0);
	for (int i = 0; i < agentCount; ++i)
	{
		TradingToolHostRequest postRotateQuote;
		postRotateQuote.sessionToken = soakBindings[i].token;
		postRotateQuote.toolCallId = "post-rotate-quote-" + std::to_string(i);
		postRotateQuote.call.name = "market.get_quote";
		postRotateQuote.call.instrument = "EUR.USD";
		assert(InvokeSocket(socketPath, registry, postRotateQuote).find("\"status\":\"ok\"") !=
			std::string::npos);
	}
	for (int cycle = 0; cycle < 4; ++cycle)
	{
		for (int i = 0; i < agentCount; ++i)
		{
			SessionSupervisorRequest rotate = soakSessions[i];
			rotate.operation = SessionSupervisorOperation::Rotate;
			rotate.ttlMs = 60000;
			rotate.replacementToken = rotate.token + "-r" + std::to_string(cycle);
			const SessionSupervisorResult rotated =
				InvokeSupervisor(supervisorSocketPath, rotate);
			assert(rotated.accepted);
			soakSessions[i].token = rotate.replacementToken;
			soakSessions[i].expectedGeneration = rotated.leaseGeneration;
			soakBindings[i].token = rotate.replacementToken;
		}
	}
	for (int i = 0; i < agentCount; ++i)
	{
		SessionSupervisorRequest revoke = soakSessions[i];
		revoke.operation = SessionSupervisorOperation::Revoke;
		const SessionSupervisorResult revoked =
			InvokeSupervisor(supervisorSocketPath, revoke);
		assert(revoked.accepted);
	}
	assert(host.SessionCount() == 1);
	TradingToolHostRequest fencedQuote;
	fencedQuote.sessionToken = soakBindings[0].token;
	fencedQuote.toolCallId = "fenced-owner-quote";
	fencedQuote.call.name = "market.get_quote";
	fencedQuote.call.instrument = "EUR.USD";
	const std::string fencedResponse = InvokeSocket(socketPath, registry, fencedQuote);
	assert(fencedResponse.find("\"status\":\"ok\"") == std::string::npos);
	assert(fencedResponse.find("SESSION_NOT_FOUND") != std::string::npos ||
		fencedResponse.find("SESSION_OWNER_FENCED") != std::string::npos);

    server.Stop();
	assert(server.Start(socketPath, reason, 65536, 3000, 1, 32, 1, 8, 2, 5000));
	// The preceding lifecycle/rotation stress is intentionally substantial and
	// can exceed the quote's five-second freshness window on a loaded CI host.
	// Refresh the fixture at the boundary of the expiry/queue semantics being
	// tested so this assertion cannot turn into an unrelated QUOTE_NOT_READY.
	const std::uint64_t restartQuoteMs =
		static_cast<std::uint64_t>(OmsJournal::NowEpochMs());
	assert(snapshots.UpsertQuote(initialQuote, restartQuoteMs,
		"SIM.quote.expiry_fixture_refresh").accepted);
	SessionSupervisorRequest restartSession;
	restartSession.operation = SessionSupervisorOperation::Provision;
	restartSession.templateId = "watch";
	restartSession.token = "simulator-restart-session-token-0001";
	restartSession.agentId = "sim-restart-agent";
	restartSession.sessionId = "sim-restart-session";
	restartSession.peerUid = static_cast<std::uint32_t>(getuid());
	restartSession.ttlMs = 60000;
	assert(InvokeSupervisor(supervisorSocketPath, restartSession).accepted);
	SessionSupervisorRequest expirySession = restartSession;
	expirySession.token = "simulator-expiry-session-token-0001";
	expirySession.agentId = "sim-expiry-agent";
	expirySession.sessionId = "sim-expiry-session";
	expirySession.ttlMs = 1000;
	assert(InvokeSupervisor(supervisorSocketPath, expirySession).accepted);
	TradingToolHostRequest expiryBlocker;
	expiryBlocker.sessionToken = expirySession.token;
	expiryBlocker.toolCallId = "block-expiry-inflight";
	expiryBlocker.call.name = "market.get_quote";
	expiryBlocker.call.instrument = "EUR.USD";
	std::string expiryBlockerResponse;
	std::thread expiryBlockerThread([&]() {
		expiryBlockerResponse = InvokeSocket(socketPath, registry, expiryBlocker);
	});
	while (!expiryBlockerEntered.load(std::memory_order_acquire)) usleep(1000);
	TradingToolHostRequest expiryQueued = expiryBlocker;
	expiryQueued.toolCallId = "expiry-queued";
	std::string expiryQueuedResponse;
	std::size_t expiredSessions = 0;
	std::atomic<bool> reapStarted(false);
	std::atomic<bool> reapFinished(false);
	bool reapAccepted = false;
	std::thread reapThread([&]() {
		reapStarted.store(true, std::memory_order_release);
		reapAccepted = supervisor.ReapExpired(
			static_cast<std::uint64_t>(OmsJournal::NowEpochMs()) + 2000,
			expiredSessions, reason);
		reapFinished.store(true, std::memory_order_release);
	});
	while (!reapStarted.load(std::memory_order_acquire)) usleep(1000);
	usleep(50000);
	assert(!reapFinished.load(std::memory_order_acquire));
	releaseExpiryBlocker.store(true, std::memory_order_release);
	expiryBlockerThread.join();
	reapThread.join();
	assert(reapAccepted && expiredSessions == 1);
	expiryQueuedResponse = InvokeSocket(socketPath, registry, expiryQueued);
	assert(expiryBlockerResponse.find("\"status\":\"ok\"") != std::string::npos);
	assert(expiryQueuedResponse.find("SESSION_NOT_FOUND") != std::string::npos);
	std::string blockerResponse;
	TradingToolHostRequest blockerQuote;
	blockerQuote.sessionToken = restartSession.token;
	blockerQuote.toolCallId = "block-integrated-restart";
	blockerQuote.call.name = "market.get_quote";
	blockerQuote.call.instrument = "EUR.USD";
	std::thread blocker([&]() {
		blockerResponse = InvokeSocket(socketPath, registry, blockerQuote);
	});
	while (server.GetHealth().activeRequests == 0) usleep(1000);
	TradingToolHostRequest queuedQuote = blockerQuote;
	queuedQuote.toolCallId = "integrated-cancel-target";
	std::string queuedResponse;
	std::thread queued([&]() {
		queuedResponse = InvokeSocket(socketPath, registry, queuedQuote);
	});
	while (server.GetHealth().pendingConnections == 0) usleep(1000);
	TradingToolHostRequest cancelQueued;
	cancelQueued.sessionToken = restartSession.token;
	cancelQueued.toolCallId = "integrated-cancel-command";
	cancelQueued.call.name = "system.cancel_request";
	cancelQueued.cancelToolCallId = queuedQuote.toolCallId;
	const std::string cancelResponse =
		InvokeSocket(socketPath, registry, cancelQueued);
	queued.join();
	assert(server.Drain(1000));
	blocker.join();
	assert(blockerResponse.find("\"status\":\"ok\"") != std::string::npos);
	assert(cancelResponse.find("REQUEST_CANCELLED") != std::string::npos);
	assert(queuedResponse.find("REQUEST_CANCELLED") != std::string::npos);
	assert(server.GetHealth().cancelledRequests >= 1);
	server.Stop();
	assert(server.Start(socketPath, reason));
	TradingToolHostRequest restartQuote;
	restartQuote.sessionToken = restartSession.token;
	restartQuote.toolCallId = "restart-quote";
	restartQuote.call.name = "market.get_quote";
	restartQuote.call.instrument = "EUR.USD";
	assert(InvokeSocket(socketPath, registry, restartQuote).find("\"status\":\"ok\"") !=
		std::string::npos);
	restartSession.operation = SessionSupervisorOperation::Revoke;
	restartSession.expectedGeneration = 1;
	assert(InvokeSupervisor(supervisorSocketPath, restartSession).accepted);
	server.Stop();
	supervisor.Stop();
	int replayedEvents = 0;
	assert(journal.Replay([&](const OmsJournalEvent&) { ++replayedEvents; }) == replayedEvents);
	assert(replayedEvents > 0);
	ExecutionCoordinator recovered(journal, callbacks);
	assert(recovered.RecoverFromJournal(reason));
	assert(!recovered.IsMutationBlocked(&reason));
	ExecutionOrderOwner recoveredOwner;
	assert(recovered.GetOrderOwner(fillOrderId, recoveredOwner));
	assert(recovered.GetOrderOwner(restingOrderId, recoveredOwner));
	std::size_t reconciledOwners = 0;
	assert(recovered.ReconcileOrderOwners(std::set<long>(), true, reconciledOwners, reason));
	assert(reconciledOwners >= 2);
	ExecutionCoordinator reconciledReplay(journal, callbacks);
	assert(reconciledReplay.RecoverFromJournal(reason));
	assert(!reconciledReplay.GetOrderOwner(fillOrderId, recoveredOwner));
	assert(!reconciledReplay.GetOrderOwner(restingOrderId, recoveredOwner));
    std::remove(journalPath.c_str());
	std::remove(supervisorStorePath.c_str());
	std::remove(supervisorKeyPath.c_str());
}

} // namespace

int main()
{
    TestAgentToolSocketToSimulatorLifecycle();
    return 0;
}
