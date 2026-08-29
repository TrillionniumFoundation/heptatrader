#include "ib_paper_execution_runtime_internal.h"

#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <fcntl.h>
#include <iostream>
#include <iomanip>
#include <limits>
#include <openssl/evp.h>
#include <cctype>
#include <algorithm>
#include <set>
#include <sstream>
#include <vector>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>

namespace ib_paper_execution_runtime_internal
{
const std::size_t kMaxPendingAdapterEvents = 20000;
const char* const kFxCashRestartCheckpointFile =
    "ib-fx-cash-restart-attestation";
const char* const kPaperTerminalLatchFile =
    "ib-paper-terminal-halt.v1";

std::string EscapeJson(const std::string& value)
{
    std::string escaped;
    for (std::string::const_iterator it = value.begin(); it != value.end(); ++it)
    {
        const unsigned char byte = static_cast<unsigned char>(*it);
        if (byte == '"' || byte == '\\') escaped.push_back('\\');
        escaped.push_back(byte < 0x20 ? '?' : static_cast<char>(byte));
    }
    return escaped;
}

using namespace ib_paper_execution_runtime_internal;

bool ParsePositiveUnsigned(const std::string& value, std::uint64_t& parsed)
{
    if (value.empty()) return false;
    std::uint64_t number = 0;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const unsigned char character = static_cast<unsigned char>(value[i]);
        if (character < static_cast<unsigned char>('0') ||
            character > static_cast<unsigned char>('9'))
            return false;
        const std::uint64_t digit = static_cast<std::uint64_t>(character - '0');
        if (number > (std::numeric_limits<std::uint64_t>::max() - digit) / 10)
            return false;
        number = number * 10 + digit;
    }
    if (number == 0) return false;
    parsed = number;
    return true;
}

bool HasPositiveEconomicFillEvidence(const IBEvent& event)
{
    // event.value=="execDetails" only records callback provenance. Economic
    // settlement always requires a positive finite cumulative quantity and
    // price, including for the wrapper's synthetic orderStatus callback.
    return std::isfinite(event.number2) && event.number2 > 0.0 &&
        std::isfinite(event.number) && event.number > 0.0;
}

bool IsHistoricalSyntheticExecutionStatus(const IBEvent& event)
{
    return event.type == IBEventType::OrderStatus &&
        event.value == "execDetails" && event.requestId >= 0;
}

bool IsEconomicallyTerminalOrderStatus(const IBEvent& event)
{
    if (event.key != "Filled")
        return event.key == "Cancelled" || event.key == "ApiCancelled" ||
               event.key == "Inactive" || event.key == "Rejected";
    return HasPositiveEconomicFillEvidence(event);
}

bool IsPersistedBrokerCallback(IBEventType type)
{
    return type == IBEventType::OrderStatus || type == IBEventType::Error ||
        type == IBEventType::ExecutionDetails ||
        type == IBEventType::ExecutionDetailsEnd ||
        type == IBEventType::CompletedOrder ||
        type == IBEventType::CompletedOrdersEnd;
}

bool ParseBrokerErrorCode(const std::string& value, int& code)
{
    if (value.empty()) return false;
    char* end = nullptr;
    errno = 0;
    const long parsed = std::strtol(value.c_str(), &end, 10);
    if (errno == ERANGE || end == value.c_str() || end == nullptr ||
        *end != '\0' || parsed < std::numeric_limits<int>::min() ||
        parsed > std::numeric_limits<int>::max()) return false;
    code = static_cast<int>(parsed);
    return true;
}

std::string StatusReasonCode(const std::string& status)
{
    std::string normalized;
    normalized.reserve(status.size());
    for (std::string::const_iterator it = status.begin();
         it != status.end(); ++it)
    {
        const unsigned char value = static_cast<unsigned char>(*it);
        normalized.push_back(std::isalnum(value) ?
            static_cast<char>(std::toupper(value)) : '_');
    }
    return normalized.empty() ? std::string("IB_ORDER_STATUS_UNKNOWN") :
        std::string("IB_ORDER_") + normalized;
}

std::string NormalizeExecutionSide(const std::string& side)
{
    if (side == "BOT" || side == "BUY") return "BUY";
    if (side == "SLD" || side == "SELL") return "SELL";
    return std::string();
}

std::uint64_t NowEpochMs()
{
    return static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count());
}

std::string Sha256Text(const std::string& value)
{
    unsigned char digest[EVP_MAX_MD_SIZE];
    unsigned int length = 0;
    EVP_MD_CTX* context = EVP_MD_CTX_new();
    if (context == nullptr) return std::string();
    const bool ok = EVP_DigestInit_ex(context, EVP_sha256(), nullptr) == 1 &&
        EVP_DigestUpdate(context, value.data(), value.size()) == 1 &&
        EVP_DigestFinal_ex(context, digest, &length) == 1;
    EVP_MD_CTX_free(context);
    if (!ok) return std::string();
    std::ostringstream output;
    output << "sha256:" << std::hex << std::setfill('0');
    for (unsigned int i = 0; i < length; ++i)
        output << std::setw(2) << static_cast<unsigned int>(digest[i]);
    return output.str();
}

bool ReadSmallPrivateFile(const std::string& path, std::string& contents,
                          std::string& reason)
{
    const int fd = ::open(path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) { reason = "IB_PAPER_FENCE_CREDENTIAL_OPEN_FAILED"; return false; }
    struct stat metadata;
    if (::fstat(fd, &metadata) != 0)
    {
        ::close(fd);
        reason = "IB_PAPER_FENCE_CREDENTIAL_UNSAFE";
        return false;
    }
    const mode_t credentialMode = metadata.st_mode & 07777;
    const bool privateSourceMode = credentialMode == 0400;
    const bool systemdCredentialMode =
        credentialMode == 0440 && metadata.st_uid == 0 && metadata.st_gid == 0;
    if (!S_ISREG(metadata.st_mode) ||
        metadata.st_size <= 0 || metadata.st_size > 256 ||
        (!privateSourceMode && !systemdCredentialMode) ||
        metadata.st_nlink != 1 ||
        (metadata.st_uid != 0 && metadata.st_uid != ::geteuid()))
    {
        ::close(fd);
        reason = "IB_PAPER_FENCE_CREDENTIAL_UNSAFE";
        return false;
    }
    contents.assign(static_cast<std::size_t>(metadata.st_size), '\0');
    std::size_t offset = 0;
    while (offset < contents.size())
    {
        const ssize_t count = ::read(fd, &contents[offset], contents.size() - offset);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) { ::close(fd); reason = "IB_PAPER_FENCE_CREDENTIAL_READ_FAILED"; return false; }
        offset += static_cast<std::size_t>(count);
    }
    const int closeResult = ::close(fd);
    if (closeResult != 0) { reason = "IB_PAPER_FENCE_CREDENTIAL_READ_FAILED"; return false; }
    return true;
}

bool ValidateOrCreatePrivateFile(const std::string& path, std::string& reason)
{
    struct stat existing;
    const bool exists = ::lstat(path.c_str(), &existing) == 0;
    if (exists && (!S_ISREG(existing.st_mode) || existing.st_uid != ::geteuid() ||
                   (existing.st_mode & 0077) != 0))
    { reason = "IB_PAPER_JOURNAL_UNSAFE"; return false; }
    if (!exists && errno != ENOENT)
    { reason = "IB_PAPER_JOURNAL_INSPECTION_FAILED"; return false; }
    const int fd = ::open(path.c_str(), O_WRONLY | O_APPEND | O_CLOEXEC | O_NOFOLLOW |
        (exists ? 0 : (O_CREAT | O_EXCL)), 0600);
    if (fd < 0) { reason = "IB_PAPER_JOURNAL_OPEN_FAILED"; return false; }
    const bool safe = ::fchmod(fd, 0600) == 0;
    const int closeResult = ::close(fd);
    if (!safe || closeResult != 0) { reason = "IB_PAPER_JOURNAL_UNSAFE"; return false; }
    return true;
}
}

IbPaperExecutionRuntimeComposition::IbPaperExecutionRuntimeComposition(
    const IbPaperExecutionRuntimeConfig& config,
    std::unique_ptr<IIBApiWrapper> injectedApi,
    const IbPaperExecutionRuntimeTestHooks& testHooks,
    const std::shared_ptr<IbPaperKillSwitchReader>& injectedKillSwitch)
    : m_config(config), m_ownedListenFd(config.listenFd),
      m_ownedEventListenFd(config.eventListenFd), m_stateLockFd(-1),
      m_fencingToken(0), m_fencingGeneration(0), m_startAttempted(false),
      m_started(false), m_injectedApi(injectedApi.get() != nullptr),
      m_testHooks(testHooks), m_initialApi(std::move(injectedApi)),
      m_killSwitch(injectedKillSwitch), m_polling(false),
      m_fatalRuntimeError(false), m_startupBrokerPhase(false),
      m_startupWaitingForUpstream(false),
      m_startupUpstreamUnavailable(false),
      m_startupUpstreamRestored(false),
      m_startupMarketDataFarmWaiting(false),
      m_startupMarketDataFarmRestored(false),
      m_startupMarketDataFarmEpoch(0),
      m_reconnectPending(false),
      m_reconnectTransportConnected(false),
      m_reconnectUpstreamUnavailable(false),
      m_reconnectUpstreamRestored(false),
      m_recoveryAuditReconnectRequested(false),
      m_postFillRiskRefreshPending(false)
{
    m_config.listenFd = -1;
    m_config.eventListenFd = -1;
}

IbPaperExecutionRuntimeComposition::~IbPaperExecutionRuntimeComposition() { Stop(); }

void IbPaperExecutionRuntimeComposition::SetStartupCancellationProbe(
    const std::function<bool()>& cancellationProbe)
{
    // This setter is intentionally a pre-Start configuration boundary.  A
    // steady-state runtime has a different shutdown path (the owner loop
    // already consumes signals), and replacing the callback concurrently
    // with startup would race the bounded readiness state machines.
    if (m_startAttempted) return;
    m_startupCancellationProbe = cancellationProbe;
}

bool IbPaperExecutionRuntimeComposition::StartupCancellationRequested(
    std::string& reason) const
{
    if (!m_startupCancellationProbe) return false;
    bool requested = false;
    try
    {
        requested = m_startupCancellationProbe();
    }
    catch (...)
    {
        reason = "IB_PAPER_STARTUP_CANCEL_PROBE_FAILED";
        return true;
    }
    if (!requested) return false;
    reason = "IB_PAPER_STARTUP_CANCELLED";
    return true;
}

bool IbPaperExecutionRuntimeComposition::AbortStartupIfCancelled(
    std::string& reason)
{
    if (!StartupCancellationRequested(reason)) return false;
    m_startupBrokerPhase.store(false);
    m_startupWaitingForUpstream.store(false);
    m_startupUpstreamUnavailable.store(false);
    m_startupUpstreamRestored.store(false);
    m_startupMarketDataFarmWaiting.store(false);
    m_startupMarketDataFarmRestored.store(false);
    m_startupMarketDataFarmEpoch.store(0);
    if (m_adapter)
    {
        std::string boundaryReason;
        if (!DisconnectAndDrainBoundaryEvents(boundaryReason) &&
            !boundaryReason.empty())
            reason = boundaryReason;
    }
    return true;
}

void IbPaperExecutionRuntimeComposition::NotifyTestStage(const char* stage) const
{
    if (m_testHooks.onStage) m_testHooks.onStage(stage);
}

void IbPaperExecutionRuntimeComposition::CloseUnconsumedListenFds()
{
    if (m_ownedListenFd >= 0) { ::close(m_ownedListenFd); m_ownedListenFd = -1; }
    if (m_ownedEventListenFd >= 0) { ::close(m_ownedEventListenFd); m_ownedEventListenFd = -1; }
}

bool IbPaperExecutionRuntimeComposition::AllowsRiskIncrease(
    std::string& reason) const
{
    if (HasFatalRuntimeError(&reason))
    {
        if (reason.empty()) reason = "IB_PAPER_RUNTIME_FATAL";
        return false;
    }
    if (!m_lifecycleGate || !m_lifecycleGate->ready.load())
    {
        reason = "IB_PAPER_RUNTIME_NOT_READY";
        return false;
    }
    if (PostFillRiskRefreshPending())
    {
        reason = "IB_POST_FILL_RISK_REFRESH_PENDING";
        return false;
    }
    if (!m_adapter)
    {
        reason = "IB_PAPER_AUTHORITATIVE_RISK_UNREADY";
        return false;
    }
    const IBAuthoritativeRiskSnapshot risk =
        m_adapter->GetAuthoritativeRiskSnapshot();
    if (!risk.accountComplete || !risk.positionsComplete ||
        !risk.fxCashComplete || risk.accountGeneration == 0 ||
        risk.positionsGeneration == 0 || risk.fxCashGeneration == 0)
    {
        reason = risk.reasonCode.empty() ?
            "IB_PAPER_AUTHORITATIVE_RISK_UNREADY" : risk.reasonCode;
        return false;
    }
    const IBAuthoritativeCorrelationSnapshot correlations =
        m_adapter->GetAuthoritativeCorrelationSnapshot();
    if (!correlations.complete || correlations.generation == 0 ||
        correlations.connectionEpoch != risk.connectionEpoch)
    {
        reason = correlations.reasonCode.empty() ?
            "IB_PAPER_AUTHORITATIVE_ORDERS_UNREADY" :
            correlations.reasonCode;
        return false;
    }
    if (correlations.activeOrderIds.size() >=
            m_config.profile.maxActiveOrders)
    {
        reason = "IB_PAPER_MAX_ACTIVE_ORDERS_EXCEEDED";
        return false;
    }
    if (!m_killSwitch || m_killSwitch->BlocksRiskIncrease(reason))
    {
        if (reason.empty()) reason = "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN";
        return false;
    }
    reason.clear();
    return true;
}

bool IbPaperExecutionRuntimeComposition::ValidateStartupContract(
    std::string& reason)
{
    if (m_startAttempted) { reason = "IB_PAPER_RUNTIME_START_ALREADY_ATTEMPTED"; return false; }
    m_startAttempted = true;
    IbPaperExecutionRuntimeConfig validation = m_config;
    validation.listenFd = m_ownedListenFd;
    validation.eventListenFd = m_ownedEventListenFd;
    if (!validation.Validate(reason) || !validation.Enabled())
    { if (reason.empty()) reason = "IB_PAPER_RUNTIME_DISABLED"; CloseUnconsumedListenFds(); return false; }
#if defined(HEPTA_ENABLE_IBAPI)
    // The IB-disabled binary is a non-installable process-test stub. Every real
    // broker-capable build enforces numeric UID separation at runtime, in
    // addition to the unit and provisioned-host name/UID contract.
    if (!validation.ValidateProductionIdentity(
            static_cast<std::uint32_t>(::geteuid()), reason))
    {
        CloseUnconsumedListenFds();
        return false;
    }
#endif
    static const char* forbiddenEnvironment[] = {
        "HEPTA_IB_ADV_OBS_LAT_MIN_INTERVAL_MS", "HEPTA_IB_ADV_OBS_LAT_SAMPLE_EVERY",
        "HEPTA_IB_EVENT_QUEUE_MAX", "HEPTA_IB_MARKET_DATA_TYPE",
        "HEPTA_IB_OBS_LOG", "HEPTA_IB_POLLONCE_TIMEOUT_MS",
        "HEPTA_IB_TRACE", "HEPTA_IB_TRACE_FILE"
    };
    for (std::size_t i = 0; i < sizeof(forbiddenEnvironment) / sizeof(forbiddenEnvironment[0]); ++i)
        if (::getenv(forbiddenEnvironment[i]) != nullptr)
        { reason = "IB_PAPER_RESIDUAL_ADAPTER_ENV_FORBIDDEN"; CloseUnconsumedListenFds(); return false; }
    if (!m_killSwitch)
    {
        if (m_testHooks.openKillSwitch)
        {
            if (!m_testHooks.openKillSwitch(
                    m_config.controlDirectory, m_killSwitch, reason) ||
                !m_killSwitch)
            {
                if (reason.empty())
                    reason = "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN";
                CloseUnconsumedListenFds();
                return false;
            }
        }
        else
        {
            std::shared_ptr<IbPaperKillSwitch> productionKillSwitch;
            if (!IbPaperKillSwitch::OpenAndPinProduction(
                    m_config.controlDirectory, productionKillSwitch, reason))
            {
                CloseUnconsumedListenFds();
                return false;
            }
            m_killSwitch = productionKillSwitch;
        }
    }
    NotifyTestStage("startup_contract_validated");
    return true;
}

bool IbPaperExecutionRuntimeComposition::PrepareExecutionFoundation(
    std::string& reason)
{
    if (!m_config.profile.VerifyAuthorizationCredential(reason) ||
        !PreparePrivateState(reason) || !LoadPaperTerminalLatch(reason) ||
        (!m_terminalLatchPresent && !LoadFxCashBaselines(reason)) ||
        (!m_terminalLatchPresent && !LoadFxCashRestartCheckpoint(reason)) ||
        !LoadFenceCredential(reason))
    { CloseUnconsumedListenFds(); return false; }
    if (m_terminalLatchPresent &&
        m_terminalResult.terminalizationServiceFencingGeneration !=
            m_fencingGeneration)
    {
        reason = "IB_PAPER_TERMINAL_LATCH_SERVICE_FENCE_MISMATCH";
        CloseUnconsumedListenFds();
        return false;
    }
    if (!GenerateExecutionServiceIdentity(
            m_fencingGeneration, m_serviceIdentity, reason))
    { CloseUnconsumedListenFds(); return false; }
    m_lifecycleGate.reset(new ExecutionServiceLifecycleGate());
    m_eventHub.reset(new ExecutionEventHub(1024, m_serviceIdentity.serviceEpoch));
    m_decisionLeases.reset(new ExecutionDecisionLeaseAuthority());
    ::setenv("HEPTA_OMS_ASYNC_FLUSH", "0", 1);
    ::setenv("HEPTA_OMS_SYNC_CRITICAL", "1", 1);
    ::setenv("HEPTA_OMS_BATCH_SIZE", "1", 1);
    ::setenv("HEPTA_OMS_FLUSH_INTERVAL_MS", "0", 1);
    if (!m_journal.Init(m_config.journalPath))
    { reason = "IB_PAPER_OMS_INIT_FAILED"; CloseUnconsumedListenFds(); return false; }
    if (!RebuildRecentBrokerOrders(reason))
    { CloseUnconsumedListenFds(); return false; }
    NotifyTestStage("execution_foundation_ready");
    return true;
}

bool IbPaperExecutionRuntimeComposition::StartIpcAndPublishReady(
    std::string& reason)
{
    if (AbortStartupIfCancelled(reason)) return false;
    m_eventServer.reset(new UnixExecutionEventFeedServer(
        *m_eventHub, m_serviceIdentity, m_lifecycleGate));
    const int eventFd = m_ownedEventListenFd; m_ownedEventListenFd = -1;
    if (!m_eventServer->StartFromFd(eventFd, m_config.allowedGatewayUids,
            m_config.gatewayContextBinding, reason, 8192,
            m_config.ioTimeoutMs, 4, 32))
    { m_eventServer.reset(); CloseUnconsumedListenFds(); return false; }
    m_hookAuthority.reset(new IbPaperExecutionHookAuthority(
        *m_policyAuthority, m_testHooks.onStage,
        [this](std::string* fatalReason) {
            return HasFatalRuntimeError(fatalReason);
        }));
    ExecutionAuthority* servedAuthority = m_hookAuthority.get();
    m_server.reset(new UnixExecutionServiceServer(
        *servedAuthority, m_policyAuthority.get(), m_decisionLeases));
    const int executionFd = m_ownedListenFd; m_ownedListenFd = -1;
    if (!m_server->StartFromFd(executionFd, m_config.allowedGatewayUids,
            m_config.gatewayContextBinding, m_serviceIdentity,
            m_lifecycleGate, reason,
            m_config.maxRequestBytes, m_config.ioTimeoutMs))
    { m_server.reset(); m_eventServer->Stop(); m_eventServer.reset(); return false; }
    if (m_terminalLatchPresent)
        return PublishTerminalControlReady(reason);
    {
        // MarkFatalRuntimeError() takes this same lock.  The poll thread may
        // start immediately, but a fatal observation can only close the gate
        // after this one and only Ready publication; Start can never overwrite
        // a fatal transition with a later ready=true store.
        std::lock_guard<std::mutex> lifecycleLock(m_lifecycleStateMutex);
        m_polling.store(true);
        try
        {
            m_pollThread = std::thread(
                &IbPaperExecutionRuntimeComposition::AdapterLoop, this);
        }
        catch (...)
        {
            m_polling.store(false);
            m_lifecycleGate->ready.store(false);
            reason = "IB_PAPER_POLL_THREAD_START_FAILED";
            m_server->Stop();
            m_server.reset();
            m_eventServer->Stop();
            m_eventServer.reset();
            return false;
        }
        m_lifecycleGate->terminalControlOnly.store(false);
        m_lifecycleGate->ready.store(true);
        m_started = true;
    }
    reason.clear();
    return true;
}

bool IbPaperExecutionRuntimeComposition::Start(std::string& reason)
{
    if (StartupCancellationRequested(reason)) return false;
    if (!ValidateStartupContract(reason) ||
        !PrepareExecutionFoundation(reason))
        return false;
    // Recover command ownership before any completedOrder/execDetails refresh
    // can arrive. Otherwise restart reconciliation would durably retain a
    // callback but lose its owner-scoped events.wait/recent_orders routing.
    BuildCoordinator();
    if (m_terminalLatchPresent)
    {
        if (!BuildTerminalControlAuthority(reason))
        {
            CloseUnconsumedListenFds();
            return false;
        }
        return StartIpcAndPublishReady(reason);
    }
    if (AbortStartupIfCancelled(reason))
    { CloseUnconsumedListenFds(); return false; }
    if (!StartAdapterAndBuildSnapshots(reason))
    { CloseUnconsumedListenFds(); return false; }
    // A successful first broker snapshot seals the initial restart point (or
    // refreshes a previously sealed point) before policy/IPC can publish any
    // mutation authority.
    if (!PersistFxCashRestartCheckpoint(reason))
    { CloseUnconsumedListenFds(); return false; }
    if (!BuildPolicyAuthority(reason))
        return false;
    return StartIpcAndPublishReady(reason);
}

void IbPaperExecutionRuntimeComposition::AdapterLoop()
{
    while (m_polling.load())
    {
        if (m_reconnectPending.load())
        {
            std::string reconnectReason;
            if (!DriveBrokerReconnect(reconnectReason))
            {
                MarkFatalRuntimeError(reconnectReason.empty() ?
                    "IB_PAPER_BROKER_RECONNECT_FAILED" : reconnectReason);
                break;
            }
            if (m_reconnectPending.load())
            {
                std::this_thread::sleep_for(std::chrono::milliseconds(10));
                continue;
            }
        }
        if (m_recoveryAuditReconnectRequested.load())
        {
            if (!DriveRecoveryAuditReconnect()) break;
            continue;
        }
        // EReader already owns the blocking broker socket read. Do not wait on
        // its signal while holding the adapter API mutex: a busy market-data
        // stream can otherwise let this loop reacquire the recursive mutex
        // indefinitely and starve authoritative read/preview RPCs.
        const bool drained = DrainAdapterEvents(0);
        if (m_reconnectPending.load()) continue;
        DrivePostFillRiskRefresh();
        if (m_fatalRuntimeError.load()) break;
        if (!drained)
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
}
void IbPaperExecutionRuntimeComposition::Stop()
{
    if (m_lifecycleGate)
    {
        m_lifecycleGate->ready.store(false);
        m_lifecycleGate->terminalControlOnly.store(false);
    }
    m_startupBrokerPhase.store(false);
    m_startupWaitingForUpstream.store(false);
    m_startupUpstreamUnavailable.store(false);
    m_startupUpstreamRestored.store(false);
    m_startupMarketDataFarmWaiting.store(false);
    m_startupMarketDataFarmRestored.store(false);
    m_startupMarketDataFarmEpoch.store(0);
    m_reconnectPending.store(false);
    m_reconnectTransportConnected.store(false);
    m_reconnectUpstreamUnavailable.store(false); m_reconnectUpstreamRestored.store(false);
    m_reconnectRefreshDispatched.store(false); m_reconnectRiskRefreshDispatched.store(false);
    m_recoveryAuditReconnectRequested.store(false);
    m_reconnectConnectionEpoch.store(0); m_polling.store(false);
    m_started = false;
    if (m_server) m_server->Stop();
    if (m_pollThread.joinable()) m_pollThread.join();
    StopQuoteSubscriptions();
    std::string boundaryReason;
    DisconnectAndDrainBoundaryEvents(boundaryReason);
    if (!boundaryReason.empty()) MarkFatalRuntimeError(boundaryReason);
    {
        std::lock_guard<std::recursive_mutex> lock(m_authoritativeQuoteSendMutex);
        m_pendingAuthoritativeQuoteEvents = 0;
    }
    {
        std::lock_guard<std::mutex> lock(m_adapterEventDrainMutex);
        m_pendingAdapterEvents.clear();
    }
    if (m_eventServer) m_eventServer->Stop();
    CloseUnconsumedListenFds();
    if (m_stateLockFd >= 0)
    { ::flock(m_stateLockFd, LOCK_UN); ::close(m_stateLockFd); m_stateLockFd = -1; }
}
bool IbPaperExecutionRuntimeComposition::IsRunning() const
{
    return !m_fatalRuntimeError.load() && m_started && m_server &&
        m_server->IsRunning() && m_eventServer && m_eventServer->IsRunning();
}
bool IbPaperExecutionRuntimeComposition::IsMutationBlocked(std::string* reason) const
{
    std::string fatalReason;
    if (HasFatalRuntimeError(&fatalReason))
    {
        if (reason)
            *reason = fatalReason.empty() ? "IB_PAPER_RUNTIME_FATAL" : fatalReason;
        return true;
    }
    if (m_terminalLatchPresent ||
        (m_lifecycleGate && m_lifecycleGate->terminalControlOnly.load()))
    {
        if (reason) *reason = "IB_PAPER_TERMINAL_HALTED";
        return true;
    }
    if (m_reconnectPending.load() ||
        !m_lifecycleGate || !m_lifecycleGate->ready.load())
    {
        if (reason) *reason = "IB_PAPER_BROKER_RECONNECT_PENDING";
        return true;
    }
    if (PostFillRiskRefreshPending())
    {
        if (reason) *reason = "IB_POST_FILL_RISK_REFRESH_PENDING";
        return true;
    }
    if (!m_coordinator)
    {
        if (reason) *reason = "IB_PAPER_RUNTIME_NOT_STARTED";
        return true;
    }
    std::string killSwitchReason;
    if (!m_killSwitch || m_killSwitch->BlocksRiskIncrease(killSwitchReason))
    {
        if (reason)
        {
            *reason = killSwitchReason.empty() ?
                "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN" : killSwitchReason;
        }
        return true;
    }
    return m_coordinator->IsMutationBlocked(reason);
}

const std::string& IbPaperExecutionRuntimeComposition::RecoveryReason() const
{ return m_recoveryReason; }
HeptaIBGatewayAdapter& IbPaperExecutionRuntimeComposition::Adapter() { return *m_adapter; }
ExecutionCoordinator& IbPaperExecutionRuntimeComposition::Coordinator() { return *m_coordinator; }
ExecutionEventHub& IbPaperExecutionRuntimeComposition::EventHub() { return *m_eventHub; }
