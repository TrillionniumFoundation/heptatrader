#include "ib_paper_execution_runtime_internal.h"

#include <cerrno>
#include <cmath>
#include <cstdlib>
#include <fcntl.h>
#include <iomanip>
#include <locale>
#include <map>
#include <set>
#include <sstream>
#include <vector>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>

using namespace ib_paper_execution_runtime_internal;

namespace
{
bool TerminalText(const std::string& value, std::size_t maximum)
{
    if (value.empty() || value.size() > maximum) return false;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const unsigned char byte = static_cast<unsigned char>(value[i]);
        if (byte < 0x21 || byte > 0x7e || byte == '=') return false;
    }
    return true;
}

bool TerminalSha256(const std::string& value)
{
    if (value.size() != 71 || value.compare(0, 7, "sha256:") != 0)
        return false;
    for (std::size_t i = 7; i < value.size(); ++i)
        if (!((value[i] >= '0' && value[i] <= '9') ||
              (value[i] >= 'a' && value[i] <= 'f')))
            return false;
    return true;
}

}

bool IbPaperExecutionRuntimeComposition::LoadPaperTerminalLatch(
    std::string& reason)
{
    m_terminalLatchPresent = false;
    m_terminalLatchPreparing = false;
    m_terminalLatchHalted = false;
    m_terminalFinalizationId.clear();
    m_terminalPreliminaryReceiptSha256.clear();
    m_terminalOwnerAgentId.clear();
    m_terminalOwnerSessionId.clear();
    m_terminalOwnerAccount.clear();
    m_terminalOwnerExecutionDomain.clear();
    m_terminalRecoveryIngressFence = 0;
    m_terminalFenceBinding = PaperTerminalFenceBinding();
    m_terminalMutationManifest = PaperTerminalMutationManifest();
    m_terminalResult = ExecutionControlResult();

    const int directoryFd = ::open(m_config.stateDirectory.c_str(),
        O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    struct stat directoryMetadata;
    if (directoryFd < 0 || ::fstat(directoryFd, &directoryMetadata) != 0 ||
        !S_ISDIR(directoryMetadata.st_mode) ||
        directoryMetadata.st_uid != ::geteuid() ||
        (directoryMetadata.st_mode & 0777) != 0700)
    {
        if (directoryFd >= 0) ::close(directoryFd);
        reason = "IB_PAPER_TERMINAL_LATCH_UNSAFE";
        return false;
    }
    const int fd = ::openat(directoryFd, kPaperTerminalLatchFile,
        O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0)
    {
        const int savedErrno = errno;
        ::close(directoryFd);
        if (savedErrno == ENOENT)
        {
            reason.clear();
            return true;
        }
        reason = "IB_PAPER_TERMINAL_LATCH_UNSAFE";
        return false;
    }
    struct stat metadata;
    if (::fstat(fd, &metadata) != 0 || !S_ISREG(metadata.st_mode) ||
        metadata.st_uid != ::geteuid() ||
        (metadata.st_mode & 07777) != 0600 || metadata.st_nlink != 1 ||
        metadata.st_size <= 0 || metadata.st_size > 16384)
    {
        ::close(fd);
        ::close(directoryFd);
        reason = "IB_PAPER_TERMINAL_LATCH_UNSAFE";
        return false;
    }
    std::string contents(static_cast<std::size_t>(metadata.st_size), '\0');
    std::size_t offset = 0;
    while (offset < contents.size())
    {
        const ssize_t count =
            ::read(fd, &contents[offset], contents.size() - offset);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0)
        {
            ::close(fd);
            ::close(directoryFd);
            reason = "IB_PAPER_TERMINAL_LATCH_READ_FAILED";
            return false;
        }
        offset += static_cast<std::size_t>(count);
    }
    if (::close(fd) != 0 || ::close(directoryFd) != 0)
    {
        reason = "IB_PAPER_TERMINAL_LATCH_READ_FAILED";
        return false;
    }

    PaperTerminalLatchDecoded decoded;
    if (!DecodePaperTerminalLatchContents(
            m_config.stateDirectory, contents, decoded, reason))
        return false;
    const bool preparing = decoded.preparing;
    const bool halted = decoded.halted;
    const std::string& finalization = decoded.binding.finalizationId;
    const std::string& preliminary =
        decoded.binding.preliminaryReceiptSha256;
    const std::string& agent = decoded.binding.owner.agentId;
    const std::string& session = decoded.binding.owner.sessionId;
    const std::string& account = decoded.binding.owner.account;
    const std::string& domain = decoded.binding.owner.executionDomain;
    const std::uint64_t recoveryIngressFence =
        decoded.binding.recoveryIngressFence;
    const PaperTerminalFenceBinding& binding = decoded.binding;
    const PaperTerminalMutationManifest& manifest = decoded.manifest;
    const ExecutionControlResult& terminal = decoded.terminal;

    m_terminalLatchPresent = true;
    m_terminalLatchPreparing = preparing;
    m_terminalLatchHalted = halted;
    m_terminalFinalizationId = finalization;
    m_terminalPreliminaryReceiptSha256 = preliminary;
    m_terminalOwnerAgentId = agent;
    m_terminalOwnerSessionId = session;
    m_terminalOwnerAccount = account;
    m_terminalOwnerExecutionDomain = domain;
    m_terminalRecoveryIngressFence = recoveryIngressFence;
    m_terminalFenceBinding = binding;
    m_terminalMutationManifest = manifest;
    m_terminalResult = terminal;
    reason.clear();
    return true;
}

bool IbPaperExecutionRuntimeComposition::PersistPaperTerminalizingLatch(
    const ExecutionControlCommand& command, std::string& reason)
{
    if (!TerminalText(command.targetCommandId, 128) ||
        !TerminalSha256(command.terminalPreliminaryReceiptSha256) ||
        !TerminalText(command.context.agentId, 128) ||
        !TerminalText(command.context.sessionId, 128) ||
        !TerminalText(command.context.account, 128) ||
        !TerminalText(command.context.executionDomain, 128) ||
        command.recoveryIngressFence == 0)
    {
        reason = "IB_PAPER_TERMINAL_LATCH_BINDING_INVALID";
        return false;
    }
    if (!m_terminalLatchPresent)
    {
        IBBrokerConnectionIdentity socketIdentity;
        std::uint64_t processStartTicks = 0;
        if (!m_adapter ||
            !m_adapter->GetBrokerConnectionIdentity(socketIdentity, reason) ||
            !ReadSelfStartTicks(processStartTicks) ||
            !TerminalText(m_serviceIdentity.serviceEpoch, 128) ||
            m_serviceIdentity.serviceFencingGeneration == 0)
        {
            if (reason.empty())
                reason = "IB_PAPER_TERMINAL_PREPARING_IDENTITY_UNAVAILABLE";
            return false;
        }
        PaperTerminalFenceBinding binding;
        binding.owner = command.context;
        binding.finalizationId = command.targetCommandId;
        binding.preliminaryReceiptSha256 =
            command.terminalPreliminaryReceiptSha256;
        binding.recoveryIngressFence = command.recoveryIngressFence;
        binding.serviceEpoch = m_serviceIdentity.serviceEpoch;
        binding.serviceFencingGeneration =
            m_serviceIdentity.serviceFencingGeneration;
        binding.serviceProcessId =
            static_cast<std::uint64_t>(::getpid());
        binding.serviceProcessStartTicks = processStartTicks;
        binding.brokerConnectionEpoch = socketIdentity.connectionEpoch;
        binding.brokerSocketIdentitySha256 =
            Sha256Text(socketIdentity.canonical);
        if (!ValidPaperTerminalFenceBinding(binding, reason)) return false;
        const std::string seed = TerminalLatchPrefix(
            binding, "PREPARING", nullptr);
        if (!WriteTerminalLatchAtomic(
                m_config.stateDirectory, seed, nullptr, reason) ||
            !LoadPaperTerminalLatch(reason) ||
            !m_terminalLatchPresent || !m_terminalLatchPreparing)
        {
            if (reason.empty())
                reason = "IB_PAPER_TERMINAL_PREPARING_LATCH_FAILED";
            return false;
        }
        NotifyTestStage("paper_terminal_preparing_latch_committed");
    }

    const bool exact =
        m_terminalFinalizationId == command.targetCommandId &&
        m_terminalPreliminaryReceiptSha256 ==
            command.terminalPreliminaryReceiptSha256 &&
        m_terminalOwnerAgentId == command.context.agentId &&
        m_terminalOwnerSessionId == command.context.sessionId &&
        m_terminalOwnerAccount == command.context.account &&
        m_terminalOwnerExecutionDomain == command.context.executionDomain &&
        m_terminalRecoveryIngressFence == command.recoveryIngressFence;
    if (!exact || m_terminalLatchHalted)
    {
        reason = exact ? "IB_PAPER_TERMINAL_LATCH_ALREADY_HALTED" :
            "IB_PAPER_TERMINAL_LATCH_BINDING_MISMATCH";
        return false;
    }
    {
        std::lock_guard<std::mutex> lifecycleLock(m_lifecycleStateMutex);
        if (m_lifecycleGate)
        {
            m_lifecycleGate->ready.store(false);
            m_lifecycleGate->terminalControlOnly.store(true);
        }
    }
    PaperTerminalMutationUniverse universe;
    if (!m_coordinator ||
        !m_coordinator->EnterPaperTerminalFenceAndProject(
            m_terminalFenceBinding, universe, reason))
        return false;
    NotifyTestStage("paper_terminal_global_fence_committed");
    PaperTerminalMutationManifest desired;
    PaperTerminalMutationManifest committed;
    if (!BuildPaperTerminalMutationManifest(
            m_terminalFenceBinding, universe, desired, reason) ||
        !CommitPaperTerminalMutationManifest(
            m_config.stateDirectory, desired, committed, reason))
        return false;
    NotifyTestStage("paper_terminal_manifest_committed");
    if (!m_terminalLatchPreparing)
    {
        if (m_terminalMutationManifest.contents != committed.contents)
        {
            reason = "IB_PAPER_TERMINAL_MANIFEST_REPLAY_MISMATCH";
            return false;
        }
        reason.clear();
        return true;
    }
    const std::string expectedSeed = TerminalLatchPrefix(
        m_terminalFenceBinding, "PREPARING", nullptr);
    const std::string terminalizing = TerminalLatchPrefix(
        m_terminalFenceBinding, "TERMINALIZING", &committed);
    if (!WriteTerminalLatchAtomic(m_config.stateDirectory, terminalizing,
            &expectedSeed, reason) || !LoadPaperTerminalLatch(reason) ||
        m_terminalLatchPreparing || m_terminalLatchHalted ||
        m_terminalMutationManifest.contents != committed.contents)
    {
        if (reason.empty())
            reason = "IB_PAPER_TERMINALIZING_LATCH_FAILED";
        return false;
    }
    NotifyTestStage("paper_terminal_terminalizing_latch_committed");
    reason.clear();
    return true;
}

bool IbPaperExecutionRuntimeComposition::PersistPaperTerminalHaltedLatch(
    const ExecutionControlCommand& command,
    const ExecutionControlResult& audit,
    ExecutionControlResult& terminal,
    std::string& reason)
{
    const bool exactBinding = m_terminalLatchPresent &&
        !m_terminalLatchHalted &&
        m_terminalFinalizationId == command.targetCommandId &&
        m_terminalPreliminaryReceiptSha256 ==
            command.terminalPreliminaryReceiptSha256 &&
        m_terminalOwnerAgentId == command.context.agentId &&
        m_terminalOwnerSessionId == command.context.sessionId &&
        m_terminalOwnerAccount == command.context.account &&
        m_terminalOwnerExecutionDomain == command.context.executionDomain;
    const bool exactFence =
        m_terminalRecoveryIngressFence == command.recoveryIngressFence;
    if (!exactBinding || !exactFence ||
        audit.status != ExecutionCommandStatus::Accepted ||
        !audit.ownerAuditAuthoritative || !audit.ownerAuditComplete ||
        audit.brokerConnectionEpoch == 0 ||
        audit.brokerConnectionEpoch !=
            m_terminalFenceBinding.brokerConnectionEpoch ||
        audit.brokerActiveGeneration == 0 ||
        audit.brokerTerminalGeneration == 0 ||
        audit.brokerRiskGeneration == 0 ||
        audit.brokerAccountGeneration == 0 ||
        audit.brokerPositionGeneration == 0 ||
        audit.brokerFxCashGeneration == 0 ||
        audit.brokerGlobalActiveOrderCount != 0 ||
        audit.ownerActiveOrderCount != 0 ||
        audit.ownerUncertainCommandCount != 0 ||
        audit.brokerPostFillRiskReconciliationPending ||
        !audit.brokerRecoveryAuditBarrierComplete ||
        audit.brokerRecoveryAuditNewConnectionEpochRequired ||
        audit.brokerPositionQuantity != "0" ||
        audit.brokerGrossAbsolutePosition != "0" ||
        audit.brokerTerminalExposureGeneration >
            audit.brokerRiskAbsorbedExposureGeneration ||
        audit.brokerRiskAbsorbedExposureGeneration !=
            audit.brokerExposureGeneration)
    {
        reason = (exactBinding && exactFence) ?
            "IB_PAPER_TERMINAL_AUDIT_INVALID" :
            "IB_PAPER_TERMINAL_LATCH_BINDING_MISMATCH";
        return false;
    }
    std::ostringstream output;
    output.imbue(std::locale::classic());
    const std::string expectedIntent = TerminalLatchPrefix(
        m_terminalFenceBinding, "TERMINALIZING",
        &m_terminalMutationManifest);
    output << TerminalLatchPrefix(
        m_terminalFenceBinding, "TERMINAL_HALTED",
        &m_terminalMutationManifest);
    AppendTerminalAudit(output, audit);
    const std::string contents = output.str();
    if (contents.size() > 16384 ||
        !WriteTerminalLatchAtomic(m_config.stateDirectory, contents,
            &expectedIntent, reason))
        return false;
    terminal = audit;
    terminal.status = ExecutionCommandStatus::Accepted;
    terminal.targetCommandId = command.targetCommandId;
    terminal.mutationBlocked = true;
    terminal.terminalizationServiceEpoch =
        m_terminalResult.terminalizationServiceEpoch;
    terminal.terminalizationServiceFencingGeneration =
        m_terminalResult.terminalizationServiceFencingGeneration;
    terminal.terminalizationGeneration = 1;
    terminal.terminalLatchSha256 = Sha256Text(contents);
    terminal.terminalServiceProcessId =
        m_terminalFenceBinding.serviceProcessId;
    terminal.terminalServiceProcessStartTicks =
        m_terminalFenceBinding.serviceProcessStartTicks;
    terminal.terminalBrokerSocketIdentitySha256 =
        m_terminalFenceBinding.brokerSocketIdentitySha256;
    terminal.terminalMutationManifestFile =
        PaperTerminalMutationManifestFileName();
    terminal.terminalMutationManifestFileSha256 =
        m_terminalMutationManifest.fileSha256;
    terminal.terminalMutationManifestBodySha256 =
        m_terminalMutationManifest.bodySha256;
    terminal.terminalKnownMutationCommandSetSha256 =
        m_terminalMutationManifest.universe.commandSetSha256;
    terminal.terminalKnownMutationCommandCount =
        m_terminalMutationManifest.universe.commands.size();
    terminal.terminalKnownCorrelationSetSha256 =
        m_terminalMutationManifest.universe.correlationSetSha256;
    terminal.terminalKnownCorrelationCount =
        m_terminalMutationManifest.universe.correlations.size();
    terminal.terminalMutationGateClosed = true;
    terminal.terminalBrokerTransportConnected = false;
    terminal.terminalBrokerEventIngressHalted = true;
    terminal.terminalBrokerCallbackQueueDrained = true;
    terminal.terminalBrokerCallbacksInFlight = 0;
    terminal.terminalBrokerReconnectPermitted = false;
    terminal.terminalLatchDurable = true;
    terminal.terminalRuntimeLatchLoaded = true;
    terminal.terminalRuntimeVerified = true;
    terminal.terminalReplay = false;
    terminal.reasonCode = "PAPER_EXECUTION_TERMINAL_HALTED";
    m_terminalLatchHalted = true;
    m_terminalResult = terminal;
    reason.clear();
    return true;
}

bool IbPaperExecutionRuntimeComposition::AdvanceReconnectMarketDataGate(
    bool requiresCashMarketDataFarm, bool upstreamReady,
    std::chrono::steady_clock::time_point observedNow,
    std::chrono::steady_clock::time_point marketDataWarmupReadyAt,
    std::chrono::milliseconds cashFarmStabilityWindow,
    std::chrono::steady_clock::time_point& cashFarmStableSince,
    std::string& reason, bool& marketDataFarmReady)
{
    (void)upstreamReady;
    (void)reason;
    const std::uint64_t reconnectEpoch =
        m_reconnectConnectionEpoch.load(std::memory_order_acquire);
    const std::uint64_t adapterEpoch = m_adapter ?
        m_adapter->GetConnectionEpoch() : 0;
    const std::uint64_t farmEpoch =
        m_startupMarketDataFarmEpoch.load(std::memory_order_acquire);
    const bool cashFarmReadyWitness =
        m_startupMarketDataFarmRestored.load() &&
        !m_startupMarketDataFarmWaiting.load() &&
        reconnectEpoch != 0 && adapterEpoch == reconnectEpoch &&
        farmEpoch == reconnectEpoch;
    if (requiresCashMarketDataFarm)
    {
        // A reconnect may issue formal subscriptions only after a positive
        // CASH-farm 2104 from this connection epoch and the quiet lease below.
        // Generic transport 2104 is only diagnostic and never authorizes a
        // request; a 2119 resets the lease through the farm state flags.
        if (!cashFarmReadyWitness)
            cashFarmStableSince = std::chrono::steady_clock::time_point();
        else if (cashFarmStableSince ==
                 std::chrono::steady_clock::time_point())
            cashFarmStableSince = observedNow;
    }
    const bool cashFarmStable = requiresCashMarketDataFarm &&
        cashFarmReadyWitness &&
        cashFarmStableSince != std::chrono::steady_clock::time_point() &&
        observedNow - cashFarmStableSince >= cashFarmStabilityWindow;
    marketDataFarmReady = requiresCashMarketDataFarm ? cashFarmStable :
        (!m_startupMarketDataFarmWaiting.load() &&
         observedNow >= marketDataWarmupReadyAt);
    return true;
}

bool IbPaperExecutionRuntimeComposition::DispatchReconnectRefreshIfReady(
    bool upstreamReady, bool marketDataFarmReady,
    std::chrono::steady_clock::time_point observedNow,
    std::chrono::steady_clock::time_point& snapshotDeadline,
    std::string& reason, bool& retryScheduled)
{
    retryScheduled = false;
    if (m_reconnectRefreshDispatched.load() || !upstreamReady ||
        !marketDataFarmReady)
        return true;
    const bool quotesStarted = StartQuoteSubscriptions(
        reason, m_reconnectConnectionEpoch.load());
    const bool refreshAccepted = quotesStarted &&
        m_adapter->ReqAuthoritativeOpenOrders() &&
        m_adapter->ReqTerminalCorrelations();
    if (!refreshAccepted)
    {
        // StartQuoteSubscriptions drains callbacks immediately; a fatal
        // 10197/overflow is terminal evidence, not a transient retry.
        if (m_fatalRuntimeError.load())
        {
            std::string fatalReason;
            HasFatalRuntimeError(&fatalReason);
            return FailBrokerReconnect(reason, fatalReason.empty() ?
                "IB_PAPER_BROKER_RECONNECT_CALLBACK_FATAL" : fatalReason);
        }
        std::string cleanupReason;
        if (!StopQuoteSubscriptions(&cleanupReason))
        {
            DisconnectAndDrainBoundaryEvents(cleanupReason);
            m_reconnectTransportConnected.store(false);
            reason = cleanupReason;
            return false;
        }
        m_reconnectTransportConnected.store(false);
        if (!DisconnectAndDrainBoundaryEvents(reason))
            return FailBrokerReconnect(reason, reason, false);
        const int backoffMs = 100 +
            100 * std::min(m_reconnectAttempt, 19);
        m_reconnectNextAttemptAt = std::min(
            m_reconnectDeadline,
            std::chrono::steady_clock::now() +
                std::chrono::milliseconds(backoffMs));
        reason.clear();
        retryScheduled = true;
        return true;
    }
    m_reconnectRefreshDispatched.store(true);
    snapshotDeadline = std::min(
        m_reconnectDeadline,
        observedNow + std::chrono::milliseconds(m_config.readinessTimeoutMs));
    NotifyTestStage("broker_reconnect_refresh_dispatched");
    return true;
}
