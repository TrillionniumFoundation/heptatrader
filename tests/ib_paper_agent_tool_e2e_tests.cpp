#include "../HeptaTrade/execution/ib_paper_execution_runtime_composition.h"
#include "../HeptaTrade/client/native_tool_client.h"
#include "../HeptaTrade/tool_host/tool_gateway_runtime_composition.h"
#include "../HeptaTrade/tool_host/session_supervisor_audit_journal.h"
#include "../HeptaTrade/tool_host/typed_tool_protocol.h"
#include "../HeptaTrade/tool_host/unix_session_supervisor_client.h"
#include "../HeptaTrade/tool_host/unix_tool_client.h"
#include "../HeptaTrade/oms_journal.h"

#include <atomic>
#include <cassert>
#include <chrono>
#include <cstring>
#include <deque>
#include <fstream>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <string>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <thread>
#include <unistd.h>
#include <vector>

namespace
{
class DisarmedKillSwitch final : public IbPaperKillSwitchReader
{
public:
    IbPaperKillSwitchObservation Observe() const override
    {
        IbPaperKillSwitchObservation observation;
        observation.state = IbPaperKillSwitchState::Disarmed;
        return observation;
    }
};

struct FakeBrokerState
{
    std::atomic<int> marketDataRequests{0};
    std::atomic<int> marketDataCancels{0};
    std::atomic<int> placeSends{0};
    std::atomic<int> cancelSends{0};
    std::mutex mutex;
    std::map<long, IBOrderLite> activeOrders;
    long nextOrderId = 100;
    double positionQuantity = 0.0;
    std::atomic<bool> connectorVisible{true};
};

IBEvent Event(IBEventType type, long id = 0)
{
    IBEvent event;
    event.type = type;
    event.id = id;
    event.connectionEpoch = 1;
    event.account = "DU123456";
    return event;
}

class FakeIbWrapper final : public IIBApiWrapper
{
public:
    explicit FakeIbWrapper(const std::shared_ptr<FakeBrokerState>& state)
        : m_state(state)
    {
    }

    bool Connect(const IBConnectParams&) override
    {
        m_connected.store(true);
        Push(Event(IBEventType::NextValidId, m_state->nextOrderId));
        // Startup admission requires the positive cash-farm 2104 callback;
        // this fixture models a healthy Gateway explicitly rather than
        // relying on the runtime's former timer-only warmup.
        IBEvent cashFarmReady = Event(IBEventType::Error);
        cashFarmReady.key = "2104";
        cashFarmReady.value = "cashfarm";
        Push(cashFarmReady);
        return true;
    }
    void SetConnectionEpoch(std::uint64_t epoch) override { m_epoch = epoch; }
    std::uint64_t GetConnectionEpoch() const override { return m_epoch; }
    void Disconnect() override { m_connected.store(false); }
    bool IsConnected() const override {
        return m_connected.load() && m_state->connectorVisible.load();
    }
    const char* GetStatusString() const override { return "OFFLINE_FAKE_IB"; }

    bool ReqAccountSummary() override
    {
        double quantity = 0.0;
        {
            std::lock_guard<std::mutex> lock(m_state->mutex);
            quantity = m_state->positionQuantity;
        }
        IBEvent account = Event(IBEventType::AccountValue);
        account.key = "NetLiquidation:USD";
        account.value = "1000000";
        Push(account);
        IBEvent ready = Event(IBEventType::AccountValue);
        ready.key = "AccountReady:";
        ready.value = "true";
        Push(ready);
        IBEvent cash = Event(IBEventType::AccountValue);
        cash.key = "CashBalance:EUR";
        cash.value = std::to_string(quantity);
        Push(cash);
        Push(Event(IBEventType::AccountSummaryEnd));
        return true;
    }

    bool ReqPositions() override
    {
        Push(Event(IBEventType::PositionEnd));
        return true;
    }

    bool ReqOpenOrders() override
    {
        std::lock_guard<std::mutex> lock(m_state->mutex);
        for (std::map<long, IBOrderLite>::const_iterator it =
                 m_state->activeOrders.begin();
             it != m_state->activeOrders.end(); ++it)
        {
            IBEvent open = Event(IBEventType::OpenOrder, it->first);
            open.order = it->second;
            open.key = "Submitted";
            Push(open);
        }
        Push(Event(IBEventType::OpenOrderEnd));
        return true;
    }
    bool ReqAllOpenOrders() override { return ReqOpenOrders(); }
    bool ReqCompletedOrders() override
    {
        Push(Event(IBEventType::CompletedOrdersEnd));
        return true;
    }
    bool ReqExecutions(int requestId) override
    {
        IBEvent complete = Event(IBEventType::ExecutionDetailsEnd);
        complete.requestId = requestId;
        Push(complete);
        return true;
    }

    bool ReqMktData(int requestId, const IBContractLite& contract) override
    {
        if (contract.symbol != "EUR" || contract.secType != "CASH" ||
            contract.exchange != "IDEALPRO" || contract.currency != "USD")
            return false;
        ++m_state->marketDataRequests;
        IBEvent bid = Event(IBEventType::TickPrice, requestId);
        bid.key = "1";
        bid.number = 1.1000;
        Push(bid);
        IBEvent ask = Event(IBEventType::TickPrice, requestId);
        ask.key = "2";
        ask.number = 1.1002;
        Push(ask);
        return true;
    }

    bool CancelMktData(int requestId) override
    {
        if (requestId <= 0) return false;
        ++m_state->marketDataCancels;
        return true;
    }

    bool PlaceOrder(long orderId, const IBContractLite&,
                    const IBOrderLite& order) override
    {
        {
            std::lock_guard<std::mutex> lock(m_state->mutex);
            if (m_state->activeOrders.find(orderId) !=
                m_state->activeOrders.end())
                return false;
            m_state->activeOrders[orderId] = order;
            if (orderId >= m_state->nextOrderId)
                m_state->nextOrderId = orderId + 1;
        }
        ++m_state->placeSends;
        IBEvent submitted = Event(IBEventType::OrderStatus, orderId);
        submitted.key = "Submitted";
        Push(submitted);
        return true;
    }

    bool CancelOrder(long orderId) override
    {
        {
            std::lock_guard<std::mutex> lock(m_state->mutex);
            if (m_state->activeOrders.erase(orderId) != 1) return false;
        }
        ++m_state->cancelSends;
        IBEvent cancelled = Event(IBEventType::OrderStatus, orderId);
        cancelled.key = "Cancelled";
        Push(cancelled);
        return true;
    }

    bool PollOnce(int) override { return m_connected.load(); }
    bool TryDequeueEvent(IBEvent& event) override
    {
        std::lock_guard<std::mutex> lock(m_eventMutex);
        if (m_events.empty()) return false;
        event = m_events.front();
        m_events.pop_front();
        // The adapter advances the broker connection epoch before Connect().
        // Stamp callbacks at dequeue time so this healthy fake models the
        // active transport generation instead of leaking its constructor
        // epoch into the strict startup farm-readiness gate.
        event.connectionEpoch = m_epoch;
        return true;
    }
    long GetLastValidOrderId() const override
    {
        std::lock_guard<std::mutex> lock(m_state->mutex);
        return m_state->nextOrderId;
    }

    void PublishPosition(double quantity)
    {
        {
            std::lock_guard<std::mutex> lock(m_state->mutex);
            m_state->positionQuantity = quantity;
        }
    }

private:
    void Push(const IBEvent& event)
    {
        std::lock_guard<std::mutex> lock(m_eventMutex);
        m_events.push_back(event);
    }

    std::shared_ptr<FakeBrokerState> m_state;
    std::atomic<bool> m_connected{false};
    std::uint64_t m_epoch = 1;
    mutable std::mutex m_eventMutex;
    std::deque<IBEvent> m_events;
};

std::string TempDirectory(const char* pattern)
{
    std::string value(pattern);
    std::vector<char> buffer(value.begin(), value.end());
    buffer.push_back('\0');
    assert(::mkdtemp(buffer.data()) != nullptr);
    assert(::chmod(buffer.data(), 0700) == 0);
    return std::string(buffer.data());
}

std::string TempSocket(const char* pattern)
{
    std::string value(pattern);
    std::vector<char> buffer(value.begin(), value.end());
    buffer.push_back('\0');
    const int descriptor = ::mkstemp(buffer.data());
    assert(descriptor >= 0);
    ::close(descriptor);
    ::unlink(buffer.data());
    return std::string(buffer.data()) + ".sock";
}

int ActivatedSocket(const std::string& path)
{
    const int descriptor = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    assert(descriptor >= 0);
    sockaddr_un address;
    std::memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    std::strncpy(address.sun_path, path.c_str(), sizeof(address.sun_path) - 1);
    assert(::bind(descriptor,
        reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0);
    assert(::chmod(path.c_str(), 0600) == 0);
    assert(::listen(descriptor, 8) == 0);
    return descriptor;
}

void WritePrivateFile(const std::string& path, const std::string& contents)
{
    std::ofstream output(path.c_str(), std::ios::out | std::ios::trunc);
    assert(output.is_open());
    output << contents;
    output.close();
    assert(::chmod(path.c_str(), 0400) == 0);
}

IbPaperExecutionRuntimeConfig IbConfig(
    int executionFd, int eventFd, const std::string& stateDirectory,
    const std::string& credentialDirectory)
{
    IbPaperExecutionRuntimeConfig config;
    config.mode = IbPaperExecutionRuntimeMode::Paper;
    config.listenFd = executionFd;
    config.eventListenFd = eventFd;
    config.allowedGatewayUids.insert(static_cast<std::uint32_t>(::geteuid()));
    config.gatewayContextBinding.agentId = "codex-agent";
    config.gatewayContextBinding.account = "DU123456";
    config.gatewayContextBinding.venue = "IB";
    config.gatewayContextBinding.executionDomain = "PAPER";
    config.stateDirectory = stateDirectory;
    config.journalPath = stateDirectory + "/oms-journal.jsonl";
    config.controlDirectory = "/run/hepta/ib-paper-control";
    config.fenceCredentialPath =
        credentialDirectory + "/hepta-execution-fence";
    config.fxCashBaselineCredentialPath =
        credentialDirectory + "/hepta-fx-cash-baseline";
    config.authorizationCredentialPath =
        credentialDirectory + "/hepta-ib-paper-authorization";
    config.ioTimeoutMs = 3000;
    config.readinessTimeoutMs = 1000;
    config.profile.enabled = true;
    config.profile.account = "DU123456";
    config.profile.host = "127.0.0.1";
    config.profile.port = 7497;
    config.profile.clientId = 701;
    config.profile.stateDirectory = stateDirectory;
    config.profile.authorizationCredentialPath =
        config.authorizationCredentialPath;
    config.profile.controlDirectory = config.controlDirectory;
    config.profile.maxOrderQuantity = 1000.0;
    config.profile.maxOrderNotional = 250000.0;
    config.profile.maxOrdersPerMinute = 30;
    config.profile.maxActiveOrders = 10;
    config.profile.maxGrossPosition = 5000.0;
    IBContractLite contract;
    contract.symbol = "EUR";
    contract.secType = "CASH";
    contract.exchange = "IDEALPRO";
    contract.currency = "USD";
    config.quoteContracts["EUR.USD"] = contract;
    IbPaperFxCashBaseline baseline;
    baseline.account = config.profile.account;
    baseline.instrument = "EUR.USD";
    baseline.currency = "EUR";
    baseline.baselineCashBalance = 0.0;
    baseline.observedCashBalance = 0.0;
    baseline.campaignExecutionDelta = 0.0;
    baseline.observedAtMs = 1;
    baseline.proof = "sha256:" + std::string(64, '0');
    config.fxCashBaselines["EUR.USD"] = baseline;
    config.primaryQuoteInstrument = "EUR.USD";
    config.quoteMaxAgeMs = 5000;
    return config;
}

IbPaperExecutionRuntimeConfig ExternalIbConfig(
    int executionFd, int eventFd, const std::string& stateDirectory,
    const std::string& credentialDirectory)
{
    IbPaperExecutionRuntimeConfig config = IbConfig(
        executionFd, eventFd, stateDirectory, credentialDirectory);
    config.profile.orderMode = IbPaperOrderMode::ExternalLimitDay;
    config.profile.externalQuoteMaxAgeMs = 5000;
    config.profile.maxOrderQuantity = 1.0;
    config.profile.maxOrderNotional = 5000.0;
    config.profile.maxActiveOrders = 1;
    config.profile.maxGrossPosition = 1.0;
    config.quoteMaxAgeMs = 5000;
    return config;
}

std::string ToolCall(const std::string& socketPath,
                     const std::string& token,
                     const std::string& callId,
                     const TradingToolCall& call,
                     TypedToolResultEnvelope& envelope)
{
    static std::map<std::string, std::unique_ptr<NativeToolClient> > clients;
    const std::string clientKey = socketPath + "\n" + token;
    std::map<std::string, std::unique_ptr<NativeToolClient> >::iterator client =
        clients.find(clientKey);
    if (client == clients.end())
    {
        NativeToolClientConfig config;
        config.socketPath = socketPath;
        config.sessionToken = token;
        config.timeoutMs = 3000;
        client = clients.insert(std::make_pair(
            clientKey, std::unique_ptr<NativeToolClient>(
                new NativeToolClient(config)))).first;
    }
    TradingToolHostRequest request;
    request.toolCallId = callId;
    request.call = call;
    std::string reason;
    NativeToolClientResult result;
    assert(client->second->Call(request, result, reason));
    assert(reason.empty());
    envelope = result.envelope;
    return result.responseJson;
}

std::string PreviewField(const std::string& response,
                         const std::string& name)
{
    const std::string marker = "\"" + name + "\":\"";
    const std::size_t begin = response.find(marker);
    assert(begin != std::string::npos);
    const std::size_t valueBegin = begin + marker.size();
    const std::size_t end = response.find('"', valueBegin);
    assert(end != std::string::npos);
    return response.substr(valueBegin, end - valueBegin);
}

void TestExternalGatewayAuthoritativeHealth()
{
    const std::string stateDirectory =
        TempDirectory("/tmp/hepta-ib-agent-health-state-XXXXXX");
    const std::string credentialDirectory =
        TempDirectory("/tmp/hepta-ib-agent-health-cred-XXXXXX");
    const std::string executionSocket =
        TempSocket("/tmp/hepta-ib-agent-health-execution-XXXXXX");
    const std::string eventSocket =
        TempSocket("/tmp/hepta-ib-agent-health-events-XXXXXX");
    const std::string toolSocket =
        TempSocket("/tmp/hepta-ib-agent-health-tools-XXXXXX");
    const std::string supervisorSocket =
        TempSocket("/tmp/hepta-ib-agent-health-supervisor-XXXXXX");
    const int executionFd = ActivatedSocket(executionSocket);
    const int eventFd = ActivatedSocket(eventSocket);

    WritePrivateFile(
        credentialDirectory + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig ibConfig = ExternalIbConfig(
        executionFd, eventFd, stateDirectory, credentialDirectory);
    std::string authorization;
    std::string reason;
    assert(ibConfig.profile.BuildAuthorizationCredential(
        authorization, reason));
    assert(authorization.compare(0, 16, "PAPER-V4:sha256:") == 0);
    WritePrivateFile(
        credentialDirectory + "/hepta-ib-paper-authorization",
        authorization + "\n");

    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    const std::shared_ptr<DisarmedKillSwitch> killSwitch(
        new DisarmedKillSwitch());
    IbPaperExecutionRuntimeComposition execution(
        ibConfig,
        std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        IbPaperExecutionRuntimeTestHooks(), killSwitch);
    assert(execution.Start(reason));

    ExecutionGatewayRuntimeConfig executionConfig;
    executionConfig.mode = ExecutionGatewayMode::Paper;
    executionConfig.executionSocket = executionSocket;
    executionConfig.eventSocket = eventSocket;
    executionConfig.executionServiceUid =
        static_cast<std::uint32_t>(::geteuid());
    executionConfig.executionServiceUidConfigured = true;
    executionConfig.ioTimeoutMs = 2500;
    executionConfig.mutationToolsEnabled = true;
    executionConfig.externalP1CanaryLimitDay = true;

    AgentOsRuntimeConfig agentConfig;
    agentConfig.toolSocket = toolSocket;
    agentConfig.supervisorSocket = supervisorSocket;
    agentConfig.supervisorUid = static_cast<std::uint32_t>(::geteuid());
    agentConfig.agentUid = static_cast<std::uint32_t>(::geteuid());
    agentConfig.supervisorMaxTtlMs = 3600000;
    agentConfig.supervisorAuditJournalPath =
        stateDirectory + "/tool-decision-audit.hja2";

    std::map<std::string, std::string> values;
    values["HEPTA_TOOL_AGENT_ID"] = "codex-agent";
    values["HEPTA_TOOL_ACCOUNT"] = "DU123456";
    values["HEPTA_TOOL_SESSION_TEMPLATES"] = "watch,paper";
    values["HEPTA_TOOL_MAX_ORDER_QTY"] = "1";
    values["HEPTA_TOOL_MAX_TRADE_CALLS_PER_MIN"] = "10";
    values["HEPTA_TOOL_CONTRACT_BINDINGS"] =
        "EUR.USD|EUR|CASH|IDEALPRO|USD";
    ToolGatewaySessionPolicy policy;
    assert(ToolGatewaySessionPolicy::FromValues(
        values, executionConfig, agentConfig, policy, reason));

    ToolGatewayRuntimeComposition gateway(
        executionConfig, agentConfig, policy);
    assert(gateway.Start(reason));

    SessionSupervisorRequest provision;
    provision.operation = SessionSupervisorOperation::Provision;
    provision.templateId = "paper";
    provision.token = std::string(32, 'H');
    provision.agentId = "codex-agent";
    provision.sessionId = "external-authoritative-health";
    provision.peerUid = static_cast<std::uint32_t>(::geteuid());
    provision.ttlMs = 120000;
    SessionSupervisorResult provisioned;
    assert(UnixSessionSupervisorClient::Call(
        supervisorSocket, provision, provisioned, reason, 3000));
    assert(provisioned.accepted);

    TradingToolCall health;
    health.name = "system.get_health";
    TypedToolResultEnvelope envelope;
    const std::string connected = ToolCall(
        toolSocket, provision.token, "external-health-one",
        health, envelope);
    assert(envelope.status == "ok");
    assert(connected.find("\"authorized_connector_count\":1") !=
        std::string::npos);
    assert(connected.find(
        "\"execution_authoritative_health\":{"
        "\"source\":\"IB\",\"authoritative\":true,"
        "\"paper_order_mode\":\"EXTERNAL_P1_CANARY_LMT_DAY\","
        "\"authorized_connector_count\":1}") != std::string::npos);

    broker->connectorVisible.store(false);
    const std::string disconnected = ToolCall(
        toolSocket, provision.token, "external-health-zero",
        health, envelope);
    assert(envelope.status == "ok");
    assert(disconnected.find("\"authorized_connector_count\":0") !=
        std::string::npos);
    assert(disconnected.find(
        "\"execution_authoritative_health\":{"
        "\"source\":\"IB\",\"authoritative\":true,"
        "\"paper_order_mode\":\"EXTERNAL_P1_CANARY_LMT_DAY\","
        "\"authorized_connector_count\":0}") != std::string::npos);

    execution.Stop();
    const std::string unavailable = ToolCall(
        toolSocket, provision.token, "external-health-unavailable",
        health, envelope);
    assert(envelope.status != "ok");
    assert(unavailable.find("\"authorized_connector_count\"") ==
        std::string::npos);
    assert(unavailable.find("\"execution_authoritative_health\"") ==
        std::string::npos);

    gateway.Stop();
    ::close(executionFd);
    ::close(eventFd);
    ::unlink(executionSocket.c_str());
    ::unlink(eventSocket.c_str());
    ::unlink(toolSocket.c_str());
    ::unlink(supervisorSocket.c_str());
    ::unlink((stateDirectory + "/oms-journal.jsonl").c_str());
    ::unlink((stateDirectory + "/ib-paper-runtime.lock").c_str());
    ::unlink((stateDirectory + "/ib-observability.jsonl").c_str());
    ::unlink((stateDirectory +
              "/ib-fx-cash-restart-attestation").c_str());
    ::unlink(agentConfig.supervisorAuditJournalPath.c_str());
    ::unlink((credentialDirectory +
              "/hepta-execution-fence").c_str());
    ::unlink((credentialDirectory +
              "/hepta-ib-paper-authorization").c_str());
    assert(::rmdir(credentialDirectory.c_str()) == 0);
    assert(::rmdir(stateDirectory.c_str()) == 0);
}
}

int main()
{
    const std::string stateDirectory =
        TempDirectory("/tmp/hepta-ib-agent-state-XXXXXX");
    const std::string credentialDirectory =
        TempDirectory("/tmp/hepta-ib-agent-credentials-XXXXXX");
    const std::string executionSocket =
        TempSocket("/tmp/hepta-ib-agent-execution-XXXXXX");
    const std::string eventSocket =
        TempSocket("/tmp/hepta-ib-agent-events-XXXXXX");
    const std::string toolSocket =
        TempSocket("/tmp/hepta-ib-agent-tools-XXXXXX");
    const std::string supervisorSocket =
        TempSocket("/tmp/hepta-ib-agent-supervisor-XXXXXX");
    const int executionFd = ActivatedSocket(executionSocket);
    const int eventFd = ActivatedSocket(eventSocket);

    WritePrivateFile(
        credentialDirectory + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig ibConfig = IbConfig(
        executionFd, eventFd, stateDirectory, credentialDirectory);
    std::string authorization;
    std::string reason;
    assert(ibConfig.profile.BuildAuthorizationCredential(
        authorization, reason));
    WritePrivateFile(
        credentialDirectory + "/hepta-ib-paper-authorization",
        authorization + "\n");

    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    const std::shared_ptr<DisarmedKillSwitch> killSwitch(
        new DisarmedKillSwitch());
    FakeIbWrapper* fakeIb = new FakeIbWrapper(broker);
    IbPaperExecutionRuntimeComposition execution(
        ibConfig, std::unique_ptr<IIBApiWrapper>(fakeIb),
        IbPaperExecutionRuntimeTestHooks(), killSwitch);
    assert(execution.Start(reason));
    assert(execution.IsRunning());
    assert(broker->marketDataRequests.load() == 1);
    assert(broker->placeSends.load() == 0);

    ExecutionGatewayRuntimeConfig executionConfig;
    executionConfig.mode = ExecutionGatewayMode::Paper;
    executionConfig.executionSocket = executionSocket;
    executionConfig.eventSocket = eventSocket;
    executionConfig.executionServiceUid =
        static_cast<std::uint32_t>(::geteuid());
    executionConfig.executionServiceUidConfigured = true;
    executionConfig.ioTimeoutMs = 2500;
    executionConfig.mutationToolsEnabled = true;

    AgentOsRuntimeConfig agentConfig;
    agentConfig.toolSocket = toolSocket;
    agentConfig.supervisorSocket = supervisorSocket;
    agentConfig.supervisorUid = static_cast<std::uint32_t>(::geteuid());
    agentConfig.agentUid = static_cast<std::uint32_t>(::geteuid());
    agentConfig.supervisorMaxTtlMs = 3600000;
    agentConfig.supervisorAuditJournalPath =
        stateDirectory + "/tool-decision-audit.hja2";

    std::map<std::string, std::string> values;
    values["HEPTA_TOOL_AGENT_ID"] = "codex-agent";
    values["HEPTA_TOOL_ACCOUNT"] = "DU123456";
    values["HEPTA_TOOL_SESSION_TEMPLATES"] = "watch,paper";
    values["HEPTA_TOOL_MAX_ORDER_QTY"] = "1000";
    values["HEPTA_TOOL_MAX_TRADE_CALLS_PER_MIN"] = "100";
    values["HEPTA_TOOL_CONTRACT_BINDINGS"] =
        "EUR.USD|EUR|CASH|IDEALPRO|USD";
    ToolGatewaySessionPolicy policy;
    assert(ToolGatewaySessionPolicy::FromValues(
        values, executionConfig, agentConfig, policy, reason));

    ToolGatewayRuntimeComposition gateway(
        executionConfig, agentConfig, policy);
    assert(gateway.Start(reason));
    assert(gateway.IsRunning());

    SessionSupervisorRequest provision;
    provision.operation = SessionSupervisorOperation::Provision;
    provision.templateId = "paper";
    provision.token = std::string(32, 'I');
    provision.agentId = "codex-agent";
    provision.sessionId = "round34-ib-paper";
    provision.peerUid = static_cast<std::uint32_t>(::geteuid());
    provision.ttlMs = 120000;
    SessionSupervisorResult provisioned;
    assert(UnixSessionSupervisorClient::Call(
        supervisorSocket, provision, provisioned, reason, 3000));
    assert(provisioned.accepted);

    TypedToolResultEnvelope envelope;
    TradingToolCall list;
    list.name = "system.tools.list";
    const std::string listed = ToolCall(
        toolSocket, provision.token, "ib-list-tools", list, envelope);
    assert(envelope.status == "ok");
    assert(listed.find("trade.place_order") != std::string::npos);
    assert(listed.find("trade.cancel_order") != std::string::npos);
    assert(listed.find("execution.get_command_status") != std::string::npos);
    assert(listed.find("risk.preview_flatten") != std::string::npos);
    assert(listed.find("trade.flatten_position") != std::string::npos);

    TradingToolCall health;
    health.name = "system.get_health";
    const std::string healthy = ToolCall(
        toolSocket, provision.token, "ib-system-health", health, envelope);
    assert(envelope.status == "ok");
    assert(healthy.find("\"remote_execution\":true") != std::string::npos);
    assert(healthy.find("\"remote_execution_configured\":true") !=
        std::string::npos);
    assert(healthy.find("\"remote_execution_ready\":true") !=
        std::string::npos);
    assert(healthy.find("\"tool_gateway_epoch\":\"htgw-v1-") !=
        std::string::npos);
    assert(healthy.find("\"execution_service_epoch\":\"\"") ==
        std::string::npos);
    assert(healthy.find("\"read_model\":\"execution_authoritative_v1\"") !=
        std::string::npos);

    TradingToolCall quote;
    quote.name = "market.get_quote";
    quote.instrument = "EUR.USD";
    const std::string quoted = ToolCall(
        toolSocket, provision.token, "ib-authoritative-quote", quote, envelope);
    assert(envelope.status == "ok");
    assert(quoted.find("\"source\":\"IB\"") != std::string::npos);
    assert(quoted.find("\"authoritative\":true") != std::string::npos);
    assert(quoted.find("\"subscription_state\":\"active\"") !=
        std::string::npos);
    assert(quoted.find("\"stale\":false") != std::string::npos);
    assert(quoted.find("\"bid\":1.1") != std::string::npos);

    TradingToolCall unknownQuote = quote;
    unknownQuote.instrument = "GBP.USD";
    const std::string unavailable = ToolCall(
        toolSocket, provision.token, "ib-unavailable-quote",
        unknownQuote, envelope);
    assert(envelope.status != "ok");
    assert(envelope.reasonCode == "AUTHORITATIVE_QUOTE_UNAVAILABLE");
    assert(envelope.detail.empty());
    assert(unavailable.find("\"reason_code\":\"AUTHORITATIVE_QUOTE_UNAVAILABLE\"") !=
        std::string::npos);
    assert(broker->marketDataRequests.load() == 1);

    TradingToolCall place;
    place.name = "trade.place_order";
    place.instrument = "EUR.USD";
    place.ibOrder.action = "BUY";
    place.ibOrder.orderType = "MKT";
    place.ibOrder.totalQuantity = 100.0;
    place.ibOrder.lmtPrice = 0.0;
    place.timeInForce = "DAY";
    place.referencePrice = 1.1001;
    place.expiresAtMs = OmsJournal::NowEpochMs() + 60000;

    const std::string unpreviewed = ToolCall(
        toolSocket, provision.token, "ib-unpreviewed-place",
        place, envelope);
    assert(envelope.status == "rejected");
    assert(unpreviewed.find("PREVIEW_PERMIT_UNKNOWN_OR_CONSUMED") !=
        std::string::npos);
    assert(broker->placeSends.load() == 0);

    TradingToolCall preview = place;
    preview.name = "risk.preview_order";
    const std::string previewed = ToolCall(
        toolSocket, provision.token, "ib-preview", preview, envelope);
    assert(envelope.status == "ok");
    assert(previewed.find("\"source\":\"IB\"") != std::string::npos);
    place.previewPermit = PreviewField(previewed, "preview_permit");
    const std::string mutationCommandId =
        PreviewField(previewed, "command_id");

    const std::string placed = ToolCall(
        toolSocket, provision.token, mutationCommandId, place, envelope);
    assert(envelope.status == "ok");
    assert(placed.find("\"order_id\":100") != std::string::npos);
    assert(broker->placeSends.load() == 1);

    TradingToolCall commandStatus;
    commandStatus.name = "execution.get_command_status";
    commandStatus.targetCommandId = mutationCommandId;
    const std::string queried = ToolCall(
        toolSocket, provision.token, "ib-command-status",
        commandStatus, envelope);
    assert(envelope.status == "ok");
    assert(queried.find("\"authoritative\":true") != std::string::npos);
    assert(queried.find("\"command_status\":\"accepted\"") !=
        std::string::npos);
    assert(queried.find("\"order_id\":100") != std::string::npos);
    assert(queried.find("\"execution_service_epoch\":\"\"") ==
        std::string::npos);
    TradingToolCall missingStatus = commandStatus;
    missingStatus.targetCommandId = "ib-missing-command";
    ToolCall(toolSocket, provision.token, "ib-status-missing",
             missingStatus, envelope);
    assert(envelope.status == "error");
    assert(envelope.reasonCode == "EXECUTION_COMMAND_NOT_FOUND");
    assert(broker->placeSends.load() == 1);

    const std::string replayed = ToolCall(
        toolSocket, provision.token, mutationCommandId, place, envelope);
    assert(envelope.status == "duplicate");
    assert(replayed.find("\"order_id\":100") != std::string::npos);
    assert(broker->placeSends.load() == 1);

    TradingToolCall cancel;
    cancel.name = "trade.cancel_order";
    cancel.orderId = 100;
    bool cancelled = false;
    for (int attempt = 0; attempt < 50 && !cancelled; ++attempt)
    {
        const std::string callId =
            "ib-cancel-" + std::to_string(attempt);
        const std::string response = ToolCall(
            toolSocket, provision.token, callId, cancel, envelope);
        cancelled = envelope.status == "ok";
        if (!cancelled)
        {
            assert(response.find("IB_CANCEL") != std::string::npos ||
                   response.find("ORDER") != std::string::npos);
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
    }
    assert(cancelled);
    assert(broker->cancelSends.load() == 1);
    assert(broker->placeSends.load() == 1);

    fakeIb->PublishPosition(100.0);
    assert(execution.Adapter().ReqRiskRefresh());
    bool flattenReady = false;
    for (int attempt = 0; attempt < 100 && !flattenReady; ++attempt)
    {
        const std::map<std::string, double> positions =
            execution.Adapter().GetAuthoritativePositionQuantities();
        const IBAuthoritativeCorrelationSnapshot correlations =
            execution.Adapter().GetAuthoritativeCorrelationSnapshot();
        const IBAuthoritativeRiskSnapshot risk =
            execution.Adapter().GetAuthoritativeRiskSnapshot();
        flattenReady =
            positions.find("EUR.USD") != positions.end() &&
            positions.find("EUR.USD")->second == 100.0 &&
            risk.coherentRefreshComplete &&
            correlations.complete &&
            correlations.activeOrderIds.empty();
        if (!flattenReady)
            std::this_thread::sleep_for(
                std::chrono::milliseconds(10));
    }
    assert(flattenReady);

    TradingToolCall flattenPreview;
    flattenPreview.name = "risk.preview_flatten";
    flattenPreview.instrument = "EUR.USD";
    const std::string previewedFlatten = ToolCall(
        toolSocket, provision.token, "ib-flatten-preview-100",
        flattenPreview, envelope);
    assert(envelope.status == "ok");
    assert(previewedFlatten.find("\"position_quantity\":100") !=
           std::string::npos);
    assert(previewedFlatten.find("\"side\":\"SELL\"") !=
           std::string::npos);
    TradingToolCall flatten;
    flatten.name = "trade.flatten_position";
    flatten.instrument = "EUR.USD";
    flatten.previewPermit =
        PreviewField(previewedFlatten, "preview_permit");
    const std::string staleFlattenCommandId =
        PreviewField(previewedFlatten, "command_id");

    fakeIb->PublishPosition(80.0);
    assert(execution.Adapter().ReqRiskRefresh());
    bool positionChanged = false;
    for (int attempt = 0; attempt < 100 && !positionChanged; ++attempt)
    {
        const std::map<std::string, double> positions =
            execution.Adapter().GetAuthoritativePositionQuantities();
        const IBAuthoritativeRiskSnapshot risk =
            execution.Adapter().GetAuthoritativeRiskSnapshot();
        positionChanged =
            positions.find("EUR.USD") != positions.end() &&
            positions.find("EUR.USD")->second == 80.0 &&
            risk.coherentRefreshComplete;
        if (!positionChanged)
            std::this_thread::sleep_for(
                std::chrono::milliseconds(10));
    }
    assert(positionChanged);
    const std::string staleFlatten = ToolCall(
        toolSocket, provision.token, staleFlattenCommandId,
        flatten, envelope);
    assert(envelope.status == "rejected");
    assert(staleFlatten.find(
        "IB_PAPER_FLATTEN_PREVIEW_SNAPSHOT_CHANGED") !=
        std::string::npos);
    assert(broker->placeSends.load() == 1);

    const std::string previewedFlatten80 = ToolCall(
        toolSocket, provision.token, "ib-flatten-preview-80",
        flattenPreview, envelope);
    assert(envelope.status == "ok");
    assert(previewedFlatten80.find("\"position_quantity\":80") !=
           std::string::npos);
    flatten.previewPermit =
        PreviewField(previewedFlatten80, "preview_permit");
    const std::string flattenCommandId =
        PreviewField(previewedFlatten80, "command_id");
    const std::string flattened = ToolCall(
        toolSocket, provision.token, flattenCommandId,
        flatten, envelope);
    assert(envelope.status == "ok");
    assert(flattened.find("\"order_id\":101") !=
           std::string::npos);
    assert(broker->placeSends.load() == 2);
    {
        std::lock_guard<std::mutex> lock(broker->mutex);
        assert(broker->activeOrders.at(101).action == "SELL");
        assert(broker->activeOrders.at(101).totalQuantity == 80.0);
    }
    const std::string flattenReplay = ToolCall(
        toolSocket, provision.token, flattenCommandId,
        flatten, envelope);
    assert(envelope.status == "duplicate");
    assert(flattenReplay.find("\"order_id\":101") !=
           std::string::npos);
    assert(broker->placeSends.load() == 2);

    gateway.Stop();
    execution.Stop();
    assert(broker->marketDataCancels.load() == 1);
    assert(broker->placeSends.load() == 2);
    assert(broker->cancelSends.load() == 1);
    std::uint64_t auditRecords = 0;
    assert(SessionSupervisorAuditJournal::Verify(
        agentConfig.supervisorAuditJournalPath, auditRecords, reason));
    assert(auditRecords > 0);

    ::close(executionFd);
    ::close(eventFd);
    ::unlink(executionSocket.c_str());
    ::unlink(eventSocket.c_str());
    ::unlink(toolSocket.c_str());
    ::unlink(supervisorSocket.c_str());
    ::unlink((stateDirectory + "/oms-journal.jsonl").c_str());
    ::unlink((stateDirectory + "/ib-paper-runtime.lock").c_str());
    ::unlink((stateDirectory + "/ib-observability.jsonl").c_str());
    ::unlink((stateDirectory + "/ib-fx-cash-restart-attestation").c_str());
    ::unlink(agentConfig.supervisorAuditJournalPath.c_str());
    ::unlink((credentialDirectory + "/hepta-execution-fence").c_str());
    ::unlink((credentialDirectory + "/hepta-ib-paper-authorization").c_str());
    assert(::rmdir(credentialDirectory.c_str()) == 0);
    assert(::rmdir(stateDirectory.c_str()) == 0);

    TestExternalGatewayAuthoritativeHealth();

    std::cout << "ib_paper_agent_tool_e2e_tests: "
              << "authoritative_quote=verified preview_permit=single_use "
              << "command_id=execution_issued same_command_retry=exactly_once "
              << "broker_place_exactly_once=verified cancel=verified "
              << "authoritative_flatten=verified "
              << "flatten_snapshot_toc_tou=blocked\n";
    return 0;
}
