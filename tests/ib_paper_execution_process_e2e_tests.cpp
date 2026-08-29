#include "../HeptaTrade/execution/ib_paper_execution_runtime_composition.h"
#include "../HeptaTrade/execution/unix_execution_service.h"

#include <cassert>
#include <cerrno>
#include <chrono>
#include <cstdlib>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <map>
#include <memory>
#include <poll.h>
#include <sstream>
#include <string>
#include <signal.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>

namespace
{
const char* const kAccount = "DU123456";
const int kChildStartupTimeoutMs = 10000;

class DisarmedKillSwitch final : public IbPaperKillSwitchReader
{
public:
    IbPaperKillSwitchObservation Observe() const override
    {
        IbPaperKillSwitchObservation result;
        result.state = IbPaperKillSwitchState::Disarmed;
        return result;
    }
};

void HoldChildForSoakSampling()
{
    const char* value = std::getenv("HEPTA_E2E_SOAK_HOLD_MS");
    if (value == nullptr || *value == '\0') return;
    char* end = nullptr;
    const long milliseconds = std::strtol(value, &end, 10);
    if (end == value || *end != '\0' || milliseconds <= 0 || milliseconds > 1000)
        return;
    ::usleep(static_cast<useconds_t>(milliseconds) * 1000);
}

bool WriteExact(int fd, const char* data, std::size_t size)
{
    std::size_t offset = 0;
    while (offset < size)
    {
        const ssize_t count = ::write(fd, data + offset, size - offset);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return false;
        offset += static_cast<std::size_t>(count);
    }
    return true;
}

bool AppendDurableLine(const std::string& path, const std::string& line)
{
    const int fd = ::open(path.c_str(), O_WRONLY | O_APPEND | O_CREAT |
        O_CLOEXEC | O_NOFOLLOW, 0600);
    if (fd < 0) return false;
    const bool wrote = WriteExact(fd, line.data(), line.size());
    const bool synced = wrote && ::fsync(fd) == 0;
    const bool closed = ::close(fd) == 0;
    return synced && closed;
}

std::map<long, std::string> LoadBrokerOrders(const std::string& path)
{
    std::map<long, std::string> orders;
    std::ifstream input(path.c_str());
    std::string line;
    while (std::getline(input, line))
    {
        std::istringstream record(line);
        char operation = 0;
        long orderId = -1;
        std::string orderRef;
        if (!(record >> operation >> orderId)) continue;
        if (operation == 'P' && record >> orderRef)
            orders[orderId] = orderRef;
        else if (operation == 'C' || operation == 'F')
            orders.erase(orderId);
    }
    return orders;
}

struct BrokerTerminal
{
    std::string orderRef;
    std::string status;
};

std::map<long, BrokerTerminal> LoadBrokerTerminals(const std::string& path)
{
    std::map<long, BrokerTerminal> terminals;
    std::ifstream input(path.c_str());
    std::string line;
    while (std::getline(input, line))
    {
        std::istringstream record(line);
        char operation = 0;
        long orderId = -1;
        BrokerTerminal terminal;
        if (!(record >> operation >> orderId)) continue;
        if ((operation == 'C' || operation == 'F') &&
            record >> terminal.orderRef >> terminal.status)
            terminals[orderId] = terminal;
    }
    return terminals;
}

int CountBrokerSends(const std::string& path)
{
    std::ifstream input(path.c_str());
    int sends = 0;
    std::string line;
    while (std::getline(input, line))
        if (line.compare(0, 2, "P ") == 0) ++sends;
    return sends;
}

int CountBrokerCancelSends(const std::string& path)
{
    std::ifstream input(path.c_str());
    int sends = 0;
    std::string line;
    while (std::getline(input, line))
        if (line.compare(0, 2, "C ") == 0) ++sends;
    return sends;
}

bool AppendFilledTerminal(const std::string& path, long orderId)
{
    const std::map<long, std::string> active = LoadBrokerOrders(path);
    const std::map<long, std::string>::const_iterator found = active.find(orderId);
    if (found == active.end()) return false;
    std::ostringstream record;
    record << "F " << orderId << ' ' << found->second << " Filled\n";
    return AppendDurableLine(path, record.str());
}

class FileBackedFakeIbWrapper final : public IIBApiWrapper
{
public:
    explicit FileBackedFakeIbWrapper(const std::string& brokerLedgerPath)
        : m_brokerLedgerPath(brokerLedgerPath), m_connected(false), m_epoch(1),
          m_nextOrderId(100)
    {
        const std::map<long, std::string> orders = LoadBrokerOrders(m_brokerLedgerPath);
        if (!orders.empty()) m_nextOrderId = orders.rbegin()->first + 1;
    }

    bool Connect(const IBConnectParams&) override
    {
        m_connected = true;
        m_events.push_back(Event(IBEventType::NextValidId, m_nextOrderId));
        // Model the broker's positive cash market-data-farm readiness signal;
        // the runtime must not infer it from a local API connect/timer.
        IBEvent cashFarmReady = Event(IBEventType::Error);
        cashFarmReady.key = "2104";
        cashFarmReady.value = "cashfarm";
        m_events.push_back(cashFarmReady);
        return true;
    }
    void SetConnectionEpoch(std::uint64_t epoch) override { m_epoch = epoch; }
    std::uint64_t GetConnectionEpoch() const override { return m_epoch; }
    void Disconnect() override { m_connected = false; }
    bool IsConnected() const override { return m_connected; }
    const char* GetStatusString() const override { return "FILE_BACKED_FAKE_IB"; }

    bool ReqAccountSummary() override
    {
        IBEvent account = Event(IBEventType::AccountValue);
        account.key = "NetLiquidation:USD";
        account.value = "1000000";
        m_events.push_back(account);
        IBEvent ready = Event(IBEventType::AccountValue);
        ready.key = "AccountReady:";
        ready.value = "true";
        m_events.push_back(ready);
        IBEvent cash = Event(IBEventType::AccountValue);
        cash.key = "CashBalance:EUR";
        cash.value = "0";
        m_events.push_back(cash);
        m_events.push_back(Event(IBEventType::AccountSummaryEnd));
        return true;
    }

    bool ReqPositions() override
    {
        m_events.push_back(Event(IBEventType::PositionEnd));
        return true;
    }

    bool ReqOpenOrders() override
    {
        const std::map<long, std::string> orders = LoadBrokerOrders(m_brokerLedgerPath);
        for (std::map<long, std::string>::const_iterator it = orders.begin();
             it != orders.end(); ++it)
        {
            IBEvent open = Event(IBEventType::OpenOrder, it->first);
            open.order.orderRef = it->second;
            m_events.push_back(open);
        }
        m_events.push_back(Event(IBEventType::OpenOrderEnd));
        return true;
    }
    bool ReqAllOpenOrders() override { return ReqOpenOrders(); }
    bool ReqCompletedOrders() override
    {
        const std::map<long, BrokerTerminal> terminals =
            LoadBrokerTerminals(m_brokerLedgerPath);
        for (std::map<long, BrokerTerminal>::const_iterator it =
                 terminals.begin(); it != terminals.end(); ++it)
        {
            IBEvent completed = Event(IBEventType::CompletedOrder, it->first);
            completed.order.orderRef = it->second.orderRef;
            completed.key = it->second.status;
            m_events.push_back(completed);
        }
        m_events.push_back(Event(IBEventType::CompletedOrdersEnd));
        return true;
    }
    bool ReqExecutions(int requestId) override
    {
        const std::map<long, BrokerTerminal> terminals =
            LoadBrokerTerminals(m_brokerLedgerPath);
        for (std::map<long, BrokerTerminal>::const_iterator it =
                 terminals.begin(); it != terminals.end(); ++it)
        {
            if (it->second.status != "Filled") continue;
            IBEvent execution = Event(IBEventType::ExecutionDetails, it->first);
            execution.requestId = requestId;
            // Model a real economic execution callback. A zero-valued
            // execDetails marker is not fill evidence and must not resolve a
            // crash-uncertain cancel as AUTHORITATIVE_CANCEL_TARGET_FILLED.
            execution.key = "fixture-execution-" +
                std::to_string(it->first);
            execution.value = "BOT";
            execution.number = 1.1001;
            execution.number2 = 100.0;
            execution.number3 = 0.0;
            execution.contract.symbol = "EUR";
            execution.contract.secType = "CASH";
            execution.contract.exchange = "IDEALPRO";
            execution.contract.currency = "USD";
            m_events.push_back(execution);
        }
        IBEvent complete = Event(IBEventType::ExecutionDetailsEnd);
        complete.requestId = requestId;
        m_events.push_back(complete);
        return true;
    }

    bool ReqMktData(int requestId, const IBContractLite&) override
    {
        IBEvent bid = Event(IBEventType::TickPrice, requestId);
        bid.key = "1";
        bid.number = 1.1000;
        m_events.push_back(bid);
        IBEvent ask = Event(IBEventType::TickPrice, requestId);
        ask.key = "2";
        ask.number = 1.1002;
        m_events.push_back(ask);
        return true;
    }
    bool CancelMktData(int) override { return true; }

    bool PlaceOrder(long orderId, const IBContractLite&, const IBOrderLite& order) override
    {
        if (order.orderRef.empty()) return false;
        std::ostringstream record;
        record << "P " << orderId << ' ' << order.orderRef << '\n';
        if (!AppendDurableLine(m_brokerLedgerPath, record.str())) return false;
        if (orderId >= m_nextOrderId) m_nextOrderId = orderId + 1;
        IBEvent submitted = Event(IBEventType::OrderStatus, orderId);
        submitted.key = "Submitted";
        m_events.push_back(submitted);
        return true;
    }

    bool CancelOrder(long orderId) override
    {
        const std::map<long, std::string> active =
            LoadBrokerOrders(m_brokerLedgerPath);
        const std::map<long, std::string>::const_iterator found =
            active.find(orderId);
        if (found == active.end()) return false;
        std::ostringstream record;
        record << "C " << orderId << ' ' << found->second << " Cancelled\n";
        if (!AppendDurableLine(m_brokerLedgerPath, record.str())) return false;
        IBEvent cancelled = Event(IBEventType::OrderStatus, orderId);
        cancelled.key = "Cancelled";
        m_events.push_back(cancelled);
        return true;
    }

    bool PollOnce(int) override { return m_connected; }
    bool TryDequeueEvent(IBEvent& event) override
    {
        if (m_events.empty()) return false;
        event = m_events.front();
        m_events.pop_front();
        return true;
    }
    long GetLastValidOrderId() const override { return m_nextOrderId; }

private:
    IBEvent Event(IBEventType type, long id = 0) const
    {
        IBEvent event;
        event.type = type;
        event.id = id;
        event.connectionEpoch = m_epoch;
        event.account = kAccount;
        return event;
    }

    std::string m_brokerLedgerPath;
    bool m_connected;
    std::uint64_t m_epoch;
    long m_nextOrderId;
    std::deque<IBEvent> m_events;
};

std::string TempDirectory(const char* pattern)
{
    char buffer[128];
    std::strncpy(buffer, pattern, sizeof(buffer));
    buffer[sizeof(buffer) - 1] = '\0';
    char* created = ::mkdtemp(buffer);
    assert(created != nullptr);
    assert(::chmod(created, 0700) == 0);
    return created;
}

void WriteCredentialFile(const std::string& path, const std::string& contents)
{
    const int fd = ::open(path.c_str(), O_WRONLY | O_CREAT | O_TRUNC |
        O_CLOEXEC | O_NOFOLLOW, 0400);
    assert(fd >= 0);
    assert(::fchmod(fd, 0400) == 0);
    assert(WriteExact(fd, contents.data(), contents.size()));
    assert(::fsync(fd) == 0);
    assert(::close(fd) == 0);
}

int ActivatedSocket(const std::string& path)
{
    ::unlink(path.c_str());
    const int fd = ::socket(AF_UNIX, SOCK_STREAM, 0);
    assert(fd >= 0);
    struct sockaddr_un address;
    std::memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    assert(path.size() < sizeof(address.sun_path));
    std::memcpy(address.sun_path, path.c_str(), path.size() + 1);
    assert(::bind(fd, reinterpret_cast<struct sockaddr*>(&address),
        sizeof(address)) == 0);
    assert(::chmod(path.c_str(), 0600) == 0);
    assert(::listen(fd, 16) == 0);
    return fd;
}

IbPaperExecutionRuntimeConfig Config(int executionFd, int eventFd,
    const std::string& stateDirectory, const std::string& credentialDirectory)
{
    IbPaperExecutionRuntimeConfig config;
    config.mode = IbPaperExecutionRuntimeMode::Paper;
    config.listenFd = executionFd;
    config.eventListenFd = eventFd;
    config.allowedGatewayUids.insert(static_cast<std::uint32_t>(::geteuid()));
    config.gatewayContextBinding.agentId = "agent-process-e2e";
    config.gatewayContextBinding.account = kAccount;
    config.gatewayContextBinding.venue = "IB";
    config.gatewayContextBinding.executionDomain = "PAPER";
    config.stateDirectory = stateDirectory;
    config.journalPath = stateDirectory + "/oms-journal.jsonl";
    config.controlDirectory = "/run/hepta/ib-paper-control";
    config.fenceCredentialPath = credentialDirectory + "/hepta-execution-fence";
    config.fxCashBaselineCredentialPath =
        credentialDirectory + "/hepta-fx-cash-baseline";
    config.authorizationCredentialPath =
        credentialDirectory + "/hepta-ib-paper-authorization";
    // The file-backed fake has no external upstream, but crash-replay cases
    // deliberately force durable journal/ledger and authoritative snapshot
    // work before readiness.  Give that fixture a bounded, scheduler-tolerant
    // budget so release soak results do not depend on a 1s host-load race.
    config.ioTimeoutMs = 3000;
    config.readinessTimeoutMs = 5000;
    config.reconnectTimeoutMs = 5000;
    config.profile.enabled = true;
    config.profile.account = kAccount;
    config.profile.host = "127.0.0.1";
    config.profile.port = 7497;
    config.profile.clientId = 701;
    config.profile.stateDirectory = stateDirectory;
    config.profile.authorizationCredentialPath = config.authorizationCredentialPath;
    config.profile.controlDirectory = config.controlDirectory;
    config.profile.maxOrderQuantity = 1000.0;
    config.profile.maxOrderNotional = 250000.0;
    config.profile.maxOrdersPerMinute = 10;
    config.profile.maxActiveOrders = 10;
    config.profile.maxGrossPosition = 5000.0;
    IBContractLite eurUsd;
    eurUsd.symbol = "EUR";
    eurUsd.secType = "CASH";
    eurUsd.exchange = "IDEALPRO";
    eurUsd.currency = "USD";
    config.quoteContracts["EUR.USD"] = eurUsd;
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

IbPlaceOrderCommand Place(const std::string& commandId)
{
    IbPlaceOrderCommand command;
    command.context.agentId = "agent-process-e2e";
    command.context.sessionId = "session-process-e2e";
    command.context.toolCallId = commandId;
    command.context.strategy = "offline-file-backed-fake-ib";
    command.context.account = kAccount;
    command.context.venue = "IB";
    command.context.executionDomain = "PAPER";
    command.context.decisionLeaseFencingToken = 77;
    command.context.decisionLeaseGeneration = 9;
    command.contract.symbol = "EUR";
    command.contract.secType = "CASH";
    command.contract.exchange = "IDEALPRO";
    command.contract.currency = "USD";
    command.instrument = "EUR.USD";
    command.order.action = "BUY";
    command.order.orderType = "MKT";
    command.order.totalQuantity = 100.0;
    command.order.lmtPrice = 0.0;
    command.timeInForce = "DAY";
    command.referencePrice = 1.1;
    command.expiresAtMs = OmsJournal::NowEpochMs() + 600000;
    return command;
}

IbCancelOrderCommand Cancel(const std::string& commandId, long orderId)
{
    IbCancelOrderCommand command;
    command.context.agentId = "agent-process-e2e";
    command.context.sessionId = "session-process-e2e";
    command.context.toolCallId = commandId;
    command.context.strategy = "offline-file-backed-fake-ib";
    command.context.account = kAccount;
    command.context.venue = "IB";
    command.context.executionDomain = "PAPER";
    command.context.decisionLeaseFencingToken = 77;
    command.context.decisionLeaseGeneration = 9;
    command.orderId = orderId;
    command.instrument = "EUR.USD";
    command.side = "BUY";
    return command;
}

int CountJournalEvents(const std::string& path, const std::string& eventType)
{
    OmsJournal journal;
    assert(journal.Init(path));
    int count = 0;
    assert(journal.Replay([&](const OmsJournalEvent& event) {
        if (event.eventType == eventType) ++count;
    }) >= 0);
    return count;
}

int CountJournalEventsWithStatus(const std::string& path,
                                 const std::string& eventType,
                                 const std::string& status)
{
    OmsJournal journal;
    assert(journal.Init(path));
    int count = 0;
    assert(journal.Replay([&](const OmsJournalEvent& event) {
        if (event.eventType == eventType && event.status == status) ++count;
    }) >= 0);
    return count;
}

bool ReadLineWithTimeout(int fd, std::string& line, int timeoutMs)
{
    const std::chrono::steady_clock::time_point deadline =
        std::chrono::steady_clock::now() + std::chrono::milliseconds(timeoutMs);
    line.clear();
    while (std::chrono::steady_clock::now() < deadline)
    {
        const int remaining = static_cast<int>(std::chrono::duration_cast<
            std::chrono::milliseconds>(deadline - std::chrono::steady_clock::now()).count());
        struct pollfd descriptor;
        descriptor.fd = fd;
        descriptor.events = POLLIN;
        descriptor.revents = 0;
        const int ready = ::poll(&descriptor, 1, remaining > 0 ? remaining : 1);
        if (ready < 0 && errno == EINTR) continue;
        if (ready <= 0) return false;
        char value = 0;
        const ssize_t count = ::read(fd, &value, 1);
        if (count <= 0) return false;
        if (value == '\n') return true;
        line.push_back(value);
    }
    return false;
}

void NotifyStartup(int fd, bool started, const std::string& reason)
{
    const std::string line = started ? "R\n" : "F:" + reason + "\n";
    WriteExact(fd, line.data(), line.size());
    ::close(fd);
}

int RunChild(int executionFd, int eventFd, int readyFd, int stageFd,
    const std::string& stateDirectory, const std::string& credentialDirectory,
    const std::string& brokerLedgerPath, const std::string& faultStage)
{
    IbPaperExecutionRuntimeTestHooks hooks;
    if (!faultStage.empty())
    {
        hooks.onStage = [faultStage, stageFd](const char* stage) {
            if (faultStage != stage) return;
            const char barrier = 'B';
            if (!WriteExact(stageFd, &barrier, 1)) ::_exit(70);
            for (;;) ::pause();
        };
    }
    IbPaperExecutionRuntimeComposition runtime(
        Config(executionFd, eventFd, stateDirectory, credentialDirectory),
        std::unique_ptr<IIBApiWrapper>(
            new FileBackedFakeIbWrapper(brokerLedgerPath)), hooks,
        std::shared_ptr<IbPaperKillSwitchReader>(new DisarmedKillSwitch()));
    std::string reason;
    const bool started = runtime.Start(reason);
    if (started) HoldChildForSoakSampling();
    NotifyStartup(readyFd, started, reason);
    if (!started) { ::close(stageFd); return 60; }
    if (faultStage.empty()) ::close(stageFd);
    for (;;) ::pause();
}

struct SpawnResult
{
    pid_t pid = -1;
    int stageFd = -1;
    bool started = false;
    std::string reason;
};

bool WaitForServiceReady(const std::string& socketPath)
{
    const std::chrono::steady_clock::time_point deadline =
        std::chrono::steady_clock::now() + std::chrono::seconds(5);
    UnixExecutionServiceClient client(socketPath, 250);
    while (std::chrono::steady_clock::now() < deadline)
    {
        ExecutionServiceIdentity identity;
        std::string reason;
        if (client.GetServiceIdentity(identity, reason) &&
            !identity.serviceEpoch.empty() && identity.serviceFencingGeneration > 0)
            return true;
        ::usleep(25000);
    }
    return false;
}

std::string PreviewField(const ExecutionCommandResult& preview,
                         const std::string& name)
{
    const std::string marker = "\"" + name + "\":\"";
    const std::size_t begin = preview.detail.find(marker);
    assert(begin != std::string::npos);
    const std::size_t value = begin + marker.size();
    const std::size_t end = preview.detail.find('"', value);
    assert(end != std::string::npos);
    return preview.detail.substr(value, end - value);
}

bool PreviewFixtureOrder(UnixExecutionServiceClient& client,
                         IbPlaceOrderCommand& command,
                         ExecutionCommandResult& preview)
{
    const std::string previewRequestId = command.context.toolCallId;
    command.previewPermit.clear();
    preview = client.PreviewOrder(command);
    if (preview.status != ExecutionCommandStatus::Accepted) return false;
    command.previewPermit = PreviewField(preview, "preview_permit");
    command.context.toolCallId = PreviewField(preview, "command_id");
    assert(!command.previewPermit.empty());
    assert(command.context.toolCallId != previewRequestId);
    return true;
}

ExecutionCommandResult RetryFixturePlace(
    UnixExecutionServiceClient& client,
    const IbPlaceOrderCommand& command)
{
    ExecutionCommandResult result;
    for (int attempt = 0; attempt < 3; ++attempt)
    {
        result = client.PlaceIbOrder(command);
        if (result.status == ExecutionCommandStatus::Accepted ||
            result.status == ExecutionCommandStatus::Duplicate)
            return result;
        if (result.status != ExecutionCommandStatus::Uncertain ||
            result.reasonCode != "EXECUTION_SERVICE_UNAVAILABLE")
            return result;
        ::usleep(25000);
    }
    return result;
}

ExecutionCommandResult PlaceFixtureOrder(
    UnixExecutionServiceClient& client,
    IbPlaceOrderCommand& command)
{
    ExecutionCommandResult preview;
    if (!PreviewFixtureOrder(client, command, preview)) return preview;
    return RetryFixturePlace(client, command);
}

ExecutionCommandResult RetryFixtureCancel(
    UnixExecutionServiceClient& client,
    const IbCancelOrderCommand& command)
{
    ExecutionCommandResult result;
    for (int attempt = 0; attempt < 3; ++attempt)
    {
        result = client.CancelIbOrder(command);
        if (result.status == ExecutionCommandStatus::Accepted ||
            result.status == ExecutionCommandStatus::Duplicate)
            return result;
        if (result.status != ExecutionCommandStatus::Uncertain ||
            result.reasonCode != "EXECUTION_SERVICE_UNAVAILABLE")
            return result;
        ::usleep(25000);
    }
    return result;
}

SpawnResult SpawnChild(const char* self, const std::string& socketPath,
    const std::string& eventPath, const std::string& stateDirectory,
    const std::string& credentialDirectory, const std::string& brokerLedgerPath,
    const std::string& faultStage)
{
    const int executionFd = ActivatedSocket(socketPath);
    const int eventFd = ActivatedSocket(eventPath);
    int readyPipe[2];
    int stagePipe[2];
    assert(::pipe(readyPipe) == 0);
    assert(::pipe(stagePipe) == 0);
    const pid_t pid = ::fork();
    assert(pid >= 0);
    if (pid == 0)
    {
        ::close(readyPipe[0]);
        ::close(stagePipe[0]);
        const std::string executionFdValue = std::to_string(executionFd);
        const std::string eventFdValue = std::to_string(eventFd);
        const std::string readyFdValue = std::to_string(readyPipe[1]);
        const std::string stageFdValue = std::to_string(stagePipe[1]);
        ::execl(self, self, "--child", executionFdValue.c_str(),
            eventFdValue.c_str(), readyFdValue.c_str(), stageFdValue.c_str(),
            stateDirectory.c_str(), credentialDirectory.c_str(),
            brokerLedgerPath.c_str(), faultStage.c_str(),
            static_cast<char*>(nullptr));
        ::_exit(127);
    }
    ::close(executionFd);
    ::close(eventFd);
    ::close(readyPipe[1]);
    ::close(stagePipe[1]);
    SpawnResult result;
    result.pid = pid;
    result.stageFd = stagePipe[0];
    std::string startup;
    // A restart with durable active-order evidence performs authoritative
    // replay and fsyncs its FX-cash attestation before publishing readiness.
    // Keep this handshake outside that legitimate I/O-bound window; the soak
    // runner still enforces the independent per-binary hard timeout.
    if (!ReadLineWithTimeout(
            readyPipe[0], startup, kChildStartupTimeoutMs))
    {
        int status = 0;
        const pid_t waited = ::waitpid(pid, &status, WNOHANG);
        std::cerr << "child startup channel failed; waitpid=" << waited
                  << " status=" << status << std::endl;
        if (waited == 0)
        {
            ::kill(pid, SIGKILL);
            ::waitpid(pid, &status, 0);
        }
        assert(false);
    }
    ::close(readyPipe[0]);
    result.started = startup == "R";
    if (!result.started && startup.compare(0, 2, "F:") == 0)
        result.reason = startup.substr(2);
    if (!result.started)
    {
        int status = 0;
        assert(::waitpid(pid, &status, 0) == pid);
        assert(WIFEXITED(status));
        assert(WEXITSTATUS(status) == 60);
        ::close(result.stageFd);
        result.stageFd = -1;
    }
    else if (!WaitForServiceReady(socketPath))
    {
        std::cerr << "child readiness identity handshake failed" << std::endl;
        ::kill(pid, SIGKILL);
        int status = 0;
        ::waitpid(pid, &status, 0);
        assert(false);
    }
    return result;
}

void KillChild(SpawnResult& child)
{
    assert(child.started);
    assert(::kill(child.pid, SIGKILL) == 0);
    int status = 0;
    assert(::waitpid(child.pid, &status, 0) == child.pid);
    assert(WIFSIGNALED(status));
    assert(WTERMSIG(status) == SIGKILL);
    if (child.stageFd >= 0) ::close(child.stageFd);
    child.stageFd = -1;
    child.started = false;
}

void WaitForBarrierAndKill(SpawnResult& child)
{
    struct pollfd descriptor;
    descriptor.fd = child.stageFd;
    descriptor.events = POLLIN;
    descriptor.revents = 0;
    assert(::poll(&descriptor, 1, 5000) == 1);
    char barrier = 0;
    assert(::read(child.stageFd, &barrier, 1) == 1);
    assert(barrier == 'B');
    KillChild(child);
}

struct Fixture
{
    std::string stateDirectory;
    std::string credentialDirectory;
    std::string brokerLedgerPath;
    std::string socketPath;
    std::string eventPath;
};

Fixture MakeFixture(const std::string& label)
{
    Fixture fixture;
    fixture.stateDirectory = TempDirectory("/tmp/hepta-ib-process-state-XXXXXX");
    fixture.credentialDirectory = TempDirectory("/tmp/hepta-ib-process-cred-XXXXXX");
    fixture.brokerLedgerPath = fixture.stateDirectory + "/fake-broker.ledger";
    fixture.socketPath = "/tmp/hepta-ib-process-" + std::to_string(::getpid()) +
        "-" + label + ".sock";
    fixture.eventPath = "/tmp/hepta-ib-process-events-" +
        std::to_string(::getpid()) + "-" + label + ".sock";
    WriteCredentialFile(fixture.credentialDirectory + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    std::string authorization;
    std::string authorizationReason;
    const IbPaperExecutionRuntimeConfig authorizationConfig = Config(
        -1, -1, fixture.stateDirectory, fixture.credentialDirectory);
    assert(authorizationConfig.profile.BuildAuthorizationCredential(
        authorization, authorizationReason));
    WriteCredentialFile(fixture.credentialDirectory +
        "/hepta-ib-paper-authorization", authorization + "\n");
    return fixture;
}

void CleanupFixture(const Fixture& fixture)
{
    ::unlink(fixture.socketPath.c_str());
    ::unlink(fixture.eventPath.c_str());
    ::unlink((fixture.stateDirectory + "/oms-journal.jsonl").c_str());
    ::unlink((fixture.stateDirectory + "/ib-paper-runtime.lock").c_str());
    ::unlink((fixture.stateDirectory + "/ib-observability.jsonl").c_str());
    ::unlink((fixture.stateDirectory +
        "/ib-fx-cash-restart-attestation").c_str());
    ::unlink(fixture.brokerLedgerPath.c_str());
    ::unlink((fixture.credentialDirectory + "/hepta-execution-fence").c_str());
    ::unlink((fixture.credentialDirectory +
        "/hepta-ib-paper-authorization").c_str());
    assert(::rmdir(fixture.credentialDirectory.c_str()) == 0);
    assert(::rmdir(fixture.stateDirectory.c_str()) == 0);
}

void TestStateLockRestartReplay(const char* self)
{
    const Fixture fixture = MakeFixture("lock-replay");
    SpawnResult primary = SpawnChild(self, fixture.socketPath, fixture.eventPath,
        fixture.stateDirectory, fixture.credentialDirectory,
        fixture.brokerLedgerPath, std::string());
    if (!primary.started)
        std::cerr << "primary startup failed: " << primary.reason << std::endl;
    assert(primary.started);

    const std::string competingSocket = fixture.socketPath + ".competing";
    const std::string competingEvent = fixture.eventPath + ".competing";
    SpawnResult competing = SpawnChild(self, competingSocket, competingEvent,
        fixture.stateDirectory, fixture.credentialDirectory,
        fixture.brokerLedgerPath, std::string());
    assert(!competing.started);
    assert(competing.reason == "IB_PAPER_STATE_LOCK_UNAVAILABLE");
    ::unlink(competingSocket.c_str());
    ::unlink(competingEvent.c_str());

    UnixExecutionServiceClient client(fixture.socketPath, 3000);
    IbPlaceOrderCommand command = Place("ib-process-lock-replay");
    const ExecutionCommandResult accepted = PlaceFixtureOrder(client, command);
    if (accepted.status != ExecutionCommandStatus::Accepted &&
        accepted.status != ExecutionCommandStatus::Duplicate)
        std::cerr << "lock replay place failed reason=" << accepted.reasonCode
                  << " detail=" << accepted.detail << std::endl;
    assert(accepted.status == ExecutionCommandStatus::Accepted ||
           accepted.status == ExecutionCommandStatus::Duplicate);
    assert(accepted.commandId == command.context.toolCallId);
    assert(CountBrokerSends(fixture.brokerLedgerPath) == 1);
    KillChild(primary);

    SpawnResult restarted = SpawnChild(self, fixture.socketPath, fixture.eventPath,
        fixture.stateDirectory, fixture.credentialDirectory,
        fixture.brokerLedgerPath, std::string());
    assert(restarted.started);
    const ExecutionCommandResult oldEpoch = client.PlaceIbOrder(command);
    assert(oldEpoch.status == ExecutionCommandStatus::Rejected);
    assert(oldEpoch.reasonCode == "EXECUTION_SERVICE_EPOCH_MISMATCH");
    const ExecutionCommandResult duplicate = client.PlaceIbOrder(command);
    assert(duplicate.status == ExecutionCommandStatus::Duplicate);
    assert(duplicate.commandId == command.context.toolCallId);
    assert(duplicate.orderId == accepted.orderId);
    assert(CountBrokerSends(fixture.brokerLedgerPath) == 1);
    KillChild(restarted);
    CleanupFixture(fixture);
}

void TestFourSigkillWindows(const char* self)
{
    struct CrashCase
    {
        const char* stage;
        int expectedIntents;
        int expectedReceipts;
        int expectedSends;
        bool restartStarts;
        bool retryAccepted;
    };
    const CrashCase cases[] = {
        {"before_dispatch", 0, 0, 0, true, true},
        {"before_venue_send", 1, 0, 0, false, false},
        {"after_venue_send", 1, 0, 1, true, false},
        {"after_receipt", 1, 1, 1, true, false}
    };

    for (std::size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i)
    {
        const Fixture fixture = MakeFixture(cases[i].stage);
        SpawnResult faulted = SpawnChild(self, fixture.socketPath, fixture.eventPath,
            fixture.stateDirectory, fixture.credentialDirectory,
            fixture.brokerLedgerPath, cases[i].stage);
        assert(faulted.started);
        UnixExecutionServiceClient client(fixture.socketPath, 3000);
        IbPlaceOrderCommand command =
            Place(std::string("ib-process-") + cases[i].stage);
        ExecutionCommandResult preview;
        assert(PreviewFixtureOrder(client, command, preview));
        ExecutionCommandResult transportResult;
        std::thread request([&]() {
            transportResult = client.PlaceIbOrder(command);
        });
        WaitForBarrierAndKill(faulted);
        request.join();
        assert(transportResult.status == ExecutionCommandStatus::Uncertain);
        assert(transportResult.commandId == command.context.toolCallId);
        assert(CountJournalEvents(fixture.stateDirectory +
            "/oms-journal.jsonl", "order_intent") == cases[i].expectedIntents);
        assert(CountJournalEvents(fixture.stateDirectory +
            "/oms-journal.jsonl", "place_sent") == cases[i].expectedReceipts);
        assert(CountBrokerSends(fixture.brokerLedgerPath) == cases[i].expectedSends);

        SpawnResult restarted = SpawnChild(self, fixture.socketPath, fixture.eventPath,
            fixture.stateDirectory, fixture.credentialDirectory,
            fixture.brokerLedgerPath, std::string());
        assert(restarted.started == cases[i].restartStarts);
        if (!restarted.started)
        {
            assert(restarted.reason == "RECOVERY_RECONCILE_REQUIRED");
        }
        else
        {
            const ExecutionCommandResult oldEpoch = client.PlaceIbOrder(command);
            assert(oldEpoch.status == ExecutionCommandStatus::Rejected);
            assert(oldEpoch.reasonCode == "EXECUTION_SERVICE_EPOCH_MISMATCH");
            if (cases[i].retryAccepted)
            {
                // No durable intent exists in the before-dispatch crash
                // window, so a new Execution-issued command id is required.
                // Reusing the consumed pre-crash permit is fail-closed.
                const std::string crashedCommandId =
                    command.context.toolCallId;
                ExecutionCommandResult replacementPreview;
                assert(PreviewFixtureOrder(
                    client, command, replacementPreview));
                assert(command.context.toolCallId != crashedCommandId);
                const ExecutionCommandResult retry =
                    RetryFixturePlace(client, command);
                if (retry.status != ExecutionCommandStatus::Accepted &&
                    retry.status != ExecutionCommandStatus::Duplicate)
                    std::cerr << "before-dispatch replacement did not reach a "
                              << "durable response: status="
                              << static_cast<int>(retry.status)
                              << " reason=" << retry.reasonCode
                              << " detail=" << retry.detail << std::endl;
                // A response timeout may occur after the mutation and receipt
                // are durable. Retrying the exact Execution-issued command id
                // must then return Duplicate without a second venue send.
                assert(retry.status == ExecutionCommandStatus::Accepted ||
                       retry.status == ExecutionCommandStatus::Duplicate);
                assert(retry.commandId == command.context.toolCallId);
            }
            else
            {
                const ExecutionCommandResult retry =
                    client.PlaceIbOrder(command);
                assert(retry.status == ExecutionCommandStatus::Duplicate);
                assert(retry.commandId == command.context.toolCallId);
            }
            const int expectedSends = cases[i].expectedSends +
                (cases[i].retryAccepted ? 1 : 0);
            assert(CountBrokerSends(fixture.brokerLedgerPath) == expectedSends);
            KillChild(restarted);
        }
        CleanupFixture(fixture);
    }
}

void TestCancelSigkillAndTerminalResolutionMatrix(const char* self)
{
    struct CrashCase
    {
        const char* name;
        const char* stage;
        int expectedIntents;
        int expectedAttempts;
        int expectedReceipts;
        int expectedCancelSends;
        bool appendFilled;
        bool restartStarts;
        ExecutionCommandStatus resolvedStatus;
        const char* resolutionCode;
    };
    const CrashCase cases[] = {
        {"before-dispatch", "before_cancel_dispatch", 0, 0, 0, 0,
         false, true, ExecutionCommandStatus::Accepted, ""},
        {"before-send-open", "before_cancel_venue_send", 1, 1, 0, 0,
         false, false, ExecutionCommandStatus::Uncertain, ""},
        {"before-send-filled", "before_cancel_venue_send", 1, 1, 0, 0,
         true, true, ExecutionCommandStatus::Rejected,
         "AUTHORITATIVE_CANCEL_TARGET_FILLED"},
        {"after-send-cancelled", "after_cancel_venue_send", 1, 1, 0, 1,
         false, true, ExecutionCommandStatus::Accepted,
         "AUTHORITATIVE_CANCEL_TERMINAL_CONFIRMED"},
        {"after-receipt", "after_cancel_receipt", 1, 1, 1, 1,
         false, true, ExecutionCommandStatus::Accepted, ""}
    };

    for (std::size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i)
    {
        const Fixture fixture = MakeFixture(
            (std::string("cancel-") + cases[i].name).c_str());
        SpawnResult primary = SpawnChild(self, fixture.socketPath,
            fixture.eventPath, fixture.stateDirectory,
            fixture.credentialDirectory, fixture.brokerLedgerPath,
            std::string());
        assert(primary.started);
        UnixExecutionServiceClient client(fixture.socketPath, 3000);
        IbPlaceOrderCommand place = Place(
            std::string("ib-cancel-place-") + cases[i].name);
        const ExecutionCommandResult placed = PlaceFixtureOrder(client, place);
        if (placed.status != ExecutionCommandStatus::Accepted &&
            placed.status != ExecutionCommandStatus::Duplicate)
            std::cerr << "cancel fixture place failed case=" << cases[i].name
                      << " reason=" << placed.reasonCode
                      << " detail=" << placed.detail << std::endl;
        assert(placed.status == ExecutionCommandStatus::Accepted ||
               placed.status == ExecutionCommandStatus::Duplicate);
        assert(placed.orderId >= 0);
        KillChild(primary);

        SpawnResult faulted = SpawnChild(self, fixture.socketPath,
            fixture.eventPath, fixture.stateDirectory,
            fixture.credentialDirectory, fixture.brokerLedgerPath,
            cases[i].stage);
        assert(faulted.started);
        const IbCancelOrderCommand cancel = Cancel(
            std::string("ib-cancel-") + cases[i].name, placed.orderId);
        const ExecutionCommandResult faultEpoch = client.CancelIbOrder(cancel);
        assert(faultEpoch.status == ExecutionCommandStatus::Rejected);
        assert(faultEpoch.reasonCode == "EXECUTION_SERVICE_EPOCH_MISMATCH");
        ExecutionCommandResult transportResult;
        std::thread request([&]() {
            transportResult = client.CancelIbOrder(cancel);
        });
        WaitForBarrierAndKill(faulted);
        request.join();
        assert(transportResult.status == ExecutionCommandStatus::Uncertain);
        assert(transportResult.commandId == cancel.context.toolCallId);

        const std::string journalPath = fixture.stateDirectory +
            "/oms-journal.jsonl";
        assert(CountJournalEventsWithStatus(journalPath, "cancel",
            "intent_recorded") == cases[i].expectedIntents);
        assert(CountJournalEvents(journalPath, "cancel_send_attempt") ==
            cases[i].expectedAttempts);
        assert(CountJournalEventsWithStatus(journalPath, "cancel",
            "cancel_sent") == cases[i].expectedReceipts);
        assert(CountBrokerCancelSends(fixture.brokerLedgerPath) ==
            cases[i].expectedCancelSends);
        if (cases[i].appendFilled)
            assert(AppendFilledTerminal(
                fixture.brokerLedgerPath, placed.orderId));

        SpawnResult restarted = SpawnChild(self, fixture.socketPath,
            fixture.eventPath, fixture.stateDirectory,
            fixture.credentialDirectory, fixture.brokerLedgerPath,
            std::string());
        if (restarted.started != cases[i].restartStarts)
            std::cerr << "cancel restart mismatch case=" << cases[i].name
                      << " expected=" << cases[i].restartStarts
                      << " actual=" << restarted.started
                      << " reason=" << restarted.reason << std::endl;
        assert(restarted.started == cases[i].restartStarts);
        if (!restarted.started)
        {
            assert(restarted.reason == "RECOVERY_RECONCILE_REQUIRED");
        }
        else
        {
            const ExecutionCommandResult oldEpoch = client.CancelIbOrder(cancel);
            assert(oldEpoch.status == ExecutionCommandStatus::Rejected);
            assert(oldEpoch.reasonCode == "EXECUTION_SERVICE_EPOCH_MISMATCH");
            const ExecutionCommandResult retry =
                RetryFixtureCancel(client, cancel);
            if (cases[i].expectedIntents == 0)
                assert(retry.status == ExecutionCommandStatus::Accepted ||
                       retry.status == ExecutionCommandStatus::Duplicate);
            else
                assert(retry.status == ExecutionCommandStatus::Duplicate);

            ExecutionControlCommand query;
            query.context = cancel.context;
            query.context.toolCallId = cancel.context.toolCallId + "-status";
            query.targetCommandId = cancel.context.toolCallId;
            const ExecutionControlResult status =
                client.QueryCommandStatus(query);
            assert(status.status == ExecutionCommandStatus::Accepted);
            assert(status.targetStatus == cases[i].resolvedStatus);
            if (cases[i].resolutionCode[0] != '\0')
                assert(status.reasonCode == cases[i].resolutionCode);

            const int expectedAfterRetry = cases[i].expectedCancelSends +
                (cases[i].expectedIntents == 0 ? 1 : 0);
            assert(CountBrokerCancelSends(fixture.brokerLedgerPath) ==
                expectedAfterRetry);
            KillChild(restarted);
        }
        assert(CountBrokerSends(fixture.brokerLedgerPath) == 1);
        CleanupFixture(fixture);
    }
}
}

int main(int argc, char** argv)
{
    if (argc == 10 && std::string(argv[1]) == "--child")
        return RunChild(std::atoi(argv[2]), std::atoi(argv[3]),
            std::atoi(argv[4]), std::atoi(argv[5]), argv[6], argv[7],
            argv[8], argv[9]);
    TestStateLockRestartReplay(argv[0]);
    TestFourSigkillWindows(argv[0]);
    TestCancelSigkillAndTerminalResolutionMatrix(argv[0]);
    std::cout << "ib_paper_execution_process_e2e_evidence: composition_child_exec=verified"
              << " startup_handshake=verified state_lock=verified restart_replay=verified"
              << " idempotent_fixture_setup=verified"
              << " sigkill_windows=4 broker_send_ledger=verified"
              << " cancel_sigkill_scenarios=5"
              << " cancel_terminal_resolution=verified"
              << " cancel_no_resend=verified oms_durability=verified"
              << std::endl;
    std::cout << "ib_paper_execution_process_e2e_tests: PASS" << std::endl;
    return 0;
}
