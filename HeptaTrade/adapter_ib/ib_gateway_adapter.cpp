#include "ib_gateway_adapter.h"
#include "ib_venue_correlation.h"
#include <ctime>
#include <cmath>
#include <sstream>
#include <iomanip>
#include <cstdlib>
#include <exception>
#include <algorithm>
#include <cctype>
#include <cstdio>
#include <fstream>
#include <mutex>
#include <chrono>
#include <vector>
#include <atomic>
#include <climits>
#include <cerrno>
#include <utility>
#include <limits>
#include <locale>
namespace {
std::unique_ptr<IIBApiWrapper> CreateDefaultIBApiWrapper() {
    return std::unique_ptr<IIBApiWrapper>(CreateIBApiWrapper());
}

std::mutex g_obsLogMutex;
std::string EscapeJson(const std::string& s) {
    std::string out;
    out.reserve(s.size() + 8);
    for (char ch : s) {
        switch (ch) {
        case '\\': out += "\\\\"; break;
        case '"': out += "\\\""; break;
        case '\n': out += "\\n"; break;
        case '\r': out += "\\r"; break;
        case '\t': out += "\\t"; break;
        default:
            if (static_cast<unsigned char>(ch) < 0x20) {
                out += ' ';
            } else {
                out += ch;
            }
            break;
        }
    }
    return out;
}

std::string JoinSortedCodes(const std::unordered_set<int>& codes) {
    std::vector<int> sorted(codes.begin(), codes.end());
    std::sort(sorted.begin(), sorted.end());
    std::ostringstream oss;
    oss.imbue(std::locale::classic());
    for (std::size_t i = 0; i < sorted.size(); ++i) {
        if (i > 0) oss << ",";
        oss << sorted[i];
    }
    return oss.str();
}

int FuseWeightForError(int ibErrorCode) {
    if (ibErrorCode <= 0) return 0;
    // Informational / connectivity chatter (mostly already ignored by fuseIgnoreErrorCodes)
    if (ibErrorCode >= 2100 && ibErrorCode < 2200) return 0;
    // Order validation / routing / broker-side warnings: mild weight
    if ((ibErrorCode >= 100 && ibErrorCode < 1000) || (ibErrorCode >= 10000 && ibErrorCode < 11000)) return 1;
    // Everything else defaults to medium severity
    return 2;
}

std::string NormalizeIbOptionRight(std::string right) {
    std::transform(right.begin(), right.end(), right.begin(), [](unsigned char ch) {
        return ch >= static_cast<unsigned char>('a') &&
                ch <= static_cast<unsigned char>('z') ?
            static_cast<char>(ch - static_cast<unsigned char>('a') +
                              static_cast<unsigned char>('A')) :
            static_cast<char>(ch);
    });
    if (right == "CALL") return "C";
    if (right == "PUT") return "P";
    return right;
}

std::string ContractDuplicateKey(const IBContractLite& c) {
    std::ostringstream oss;
    oss.imbue(std::locale::classic());
    oss << c.symbol << "|" << c.secType << "|" << c.exchange << "|" << c.primaryExchange << "|"
        << c.currency << "|" << c.lastTradeDateOrContractMonth << "|" << NormalizeIbOptionRight(c.right) << "|"
        << std::fixed << std::setprecision(8) << c.strike << "|"
        << c.multiplier << "|" << c.tradingClass << "|" << c.localSymbol;
    return oss.str();
}

std::string ObsLogPath() {
    const char* p = std::getenv("HEPTA_IB_OBS_LOG");
    if (p != nullptr && *p != '\0') {
        return p;
    }
    return "runtime-logs/ib_observability.jsonl";
}

std::string UtcNowIso8601Ms() {
    using namespace std::chrono;
    auto now = system_clock::now();
    auto ms = duration_cast<milliseconds>(now.time_since_epoch()) % 1000;
    std::time_t tt = system_clock::to_time_t(now);
    std::tm tmUtc{};
#ifdef _WIN32
    gmtime_s(&tmUtc, &tt);
#else
    gmtime_r(&tt, &tmUtc);
#endif
    char date[32] = { 0 };
    if (std::strftime(date, sizeof(date), "%Y-%m-%dT%H:%M:%S", &tmUtc) == 0) {
        return std::string();
    }
    std::ostringstream out;
    out.imbue(std::locale::classic());
    out << date << '.' << std::setfill('0') << std::setw(3)
        << static_cast<int>(ms.count()) << 'Z';
    return out.str();
}

bool ParseCanonicalUnsignedEnv(const char* raw, unsigned long long& parsed) {
    if (raw == nullptr || *raw == '\0') return false;
    const std::string value(raw);
    if (value.size() > 1 && value[0] == '0') return false;
    for (const char c : value)
        if (c < '0' || c > '9') return false;
    char* end = nullptr;
    errno = 0;
    const unsigned long long number = std::strtoull(value.c_str(), &end, 10);
    if (errno != 0 || end == value.c_str() || *end != '\0') return false;
    parsed = number;
    return true;
}

int GetEnvIntClamped(const char* name, int defValue, int minValue) {
    const char* raw = std::getenv(name);
    if (raw == nullptr || *raw == '\0') return defValue;
    unsigned long long parsed = 0;
    if (!ParseCanonicalUnsignedEnv(raw, parsed)) return defValue;
    if (parsed < static_cast<unsigned long long>(minValue))
        parsed = static_cast<unsigned long long>(minValue);
    if (parsed > static_cast<unsigned long long>(INT_MAX))
        parsed = static_cast<unsigned long long>(INT_MAX);
    return static_cast<int>(parsed);
}

bool ShouldEmitObsLatencySample(const char* path, const char* stage) {
    const int sampleEvery = GetEnvIntClamped("HEPTA_IB_ADV_OBS_LAT_SAMPLE_EVERY", 1, 1);
    const int minIntervalMs = GetEnvIntClamped("HEPTA_IB_ADV_OBS_LAT_MIN_INTERVAL_MS", 0, 0);
    if (sampleEvery <= 1 && minIntervalMs <= 0) return true;

    static std::atomic<unsigned long long> seq{0};
    if (sampleEvery > 1) {
        const unsigned long long n = seq.fetch_add(1, std::memory_order_relaxed) + 1;
        if ((n % static_cast<unsigned long long>(sampleEvery)) != 0ULL) {
            return false;
        }
    }

    if (minIntervalMs > 0) {
        static std::mutex gateMutex;
        static std::unordered_map<std::string, long long> lastEmitMsByKey;
        const std::string key = std::string(path ? path : "") + "|" + std::string(stage ? stage : "");
        const long long nowMs = static_cast<long long>(std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now().time_since_epoch()).count());
        std::lock_guard<std::mutex> lk(gateMutex);
        auto it = lastEmitMsByKey.find(key);
        if (it != lastEmitMsByKey.end() && (nowMs - it->second) < static_cast<long long>(minIntervalMs)) {
            return false;
        }
        lastEmitMsByKey[key] = nowMs;
    }

    return true;
}
void AppendObsLogLine(const std::string& line, const std::string& explicitPath) {
    std::lock_guard<std::mutex> lk(g_obsLogMutex);
    const std::string path = explicitPath.empty() ? ObsLogPath() : explicitPath;
    std::ofstream ofs(path.c_str(), std::ios::out | std::ios::app);
    if (!ofs.is_open()) return;
    ofs << line << "\n";
}
} // namespace

HeptaIBGatewayAdapter::HeptaIBGatewayAdapter()
    : HeptaIBGatewayAdapter(
          CreateDefaultIBApiWrapper(), CreateDefaultIBApiWrapper) {
}

HeptaIBGatewayAdapter::HeptaIBGatewayAdapter(std::unique_ptr<IIBApiWrapper> api)
    : HeptaIBGatewayAdapter(std::move(api), CreateDefaultIBApiWrapper) {
}

HeptaIBGatewayAdapter::HeptaIBGatewayAdapter(
    std::unique_ptr<IIBApiWrapper> api,
    const std::function<std::unique_ptr<IIBApiWrapper>()>& reconnectApiFactory)
    : m_connected(false), m_api(std::move(api)),
      m_reconnectApiFactory(reconnectApiFactory),
      m_eventIngressFence(new std::recursive_mutex()) {
    BindEventIngressFence();
    if (m_api) m_connectionEpoch = m_api->GetConnectionEpoch();
}

HeptaIBGatewayAdapter::~HeptaIBGatewayAdapter() {
    Disconnect();
}

bool HeptaIBGatewayAdapter::Init(const HeptaIBConfig& cfg) {
    std::map<std::string, std::string> fxInstrumentByBaseCurrency;
    for (std::map<std::string, InstrumentRef>::const_iterator it =
             cfg.authoritativeCashFxContracts.begin();
         it != cfg.authoritativeCashFxContracts.end(); ++it) {
        const InstrumentRef& contract = it->second;
        if (it->first.empty() || cfg.account.empty() ||
            contract.secType != "CASH" || contract.symbol.empty() ||
            contract.currency.empty() ||
            contract.symbol == contract.currency ||
            !std::isfinite(cfg.authoritativeCashFxBaselines.count(it->first) ?
                cfg.authoritativeCashFxBaselines.find(it->first)->second :
                std::numeric_limits<double>::quiet_NaN()) ||
            !std::isfinite(
                cfg.authoritativeCashFxStartupObservedBalances.count(
                    it->first) ?
                cfg.authoritativeCashFxStartupObservedBalances.find(
                    it->first)->second :
                std::numeric_limits<double>::quiet_NaN()) ||
            !fxInstrumentByBaseCurrency.insert(
                std::make_pair(contract.symbol, it->first)).second) {
            return false;
        }
    }
    if (cfg.authoritativeCashFxBaselines.size() !=
            cfg.authoritativeCashFxContracts.size() ||
        cfg.authoritativeCashFxStartupObservedBalances.size() !=
            cfg.authoritativeCashFxContracts.size()) return false;
    m_cfg = cfg;
    m_fxInstrumentByBaseCurrency = fxInstrumentByBaseCurrency;
    m_circuitBreakerTripped = false;
    m_todayOrderCount = 0;
    m_consecutiveErrorCount = 0;
    m_dayOfYear = -1;
    m_localOrderSeed = 1;
    m_lastReferencePrice = 0.0;
    m_lastReferencePriceTs = 0;
    m_lastOrderSig.clear();
    m_lastOrderTs = 0;
    m_orderSubmitTs.clear();
    m_orderAttemptTimes.clear();
    m_cancelSubmitTs.clear();
    m_pendingCancelOrderIds.clear();
    m_symbolNetPosition.clear();
    // Init is a fresh local configuration generation even when the underlying
    // broker connection epoch has not changed. Never carry broker
    // acknowledgement or terminal evidence into that generation.
    m_orderLifecycle.InvalidateConnectionEpoch();
    m_orderLifecycle.ActivateConnectionEpoch(m_connectionEpoch);
    m_eventStreamAuthoritative = true;
    m_lastEventOverflowGeneration = 0;
    m_correlationGeneration = 0;
    m_correlationRefreshPending = false;
    m_correlationRefreshConflict = false;
    m_correlationSnapshot = IBAuthoritativeCorrelationSnapshot();
    m_pendingCorrelationOrderIds.clear();
    m_pendingActiveOrderIds.clear();
    m_postFillReconciliationOrderIds.clear();
    m_postFillExposureGenerationByOrderId.clear();
    m_observedEconomicFillQuantityByOrderId.clear();
    m_orderRiskBaselines.clear();
    m_terminalCorrelationGeneration = 0;
    m_terminalCorrelationRequestIssuedForEpoch = false;
    m_terminalExecutionRequestId = 0;
    m_completedOrdersRefreshPending = false;
    m_executionsRefreshPending = false;
    m_terminalCorrelationRefreshConflict = false;
    m_terminalCorrelationSnapshot =
        IBAuthoritativeTerminalCorrelationSnapshot();
    m_pendingTerminalOrderIds.clear();
    m_pendingTerminalStatuses.clear();
    m_pendingTerminalCorrelationsByOrderId.clear();
    m_pendingExecutionOrderIds.clear();
    m_exposureGeneration = 0;
    m_riskGeneration = 0;
    m_coherentRiskRefreshDispatching = false;
    m_coherentRiskRefreshPending = false;
    m_coherentRiskRefreshForRecoveryAudit = false;
    m_coherentRiskRefreshConnectionEpoch = 0;
    m_coherentRiskRefreshAccountGeneration = 0;
    m_coherentRiskRefreshPositionGeneration = 0;
    m_coherentRiskRefreshExposureGeneration = 0;
    m_coherentRiskRefreshActiveGeneration = 0;
    m_coherentRiskRefreshTerminalGeneration = 0;
    m_coherentRiskRefreshMutationGeneration = 0;
    m_accountRefreshPending = false;
    m_positionsRefreshPending = false;
    m_accountRefreshObserved = false;
    m_accountReadyObserved = false;
    m_accountReady = false;
    m_fxCashRefreshConflict = false;
    m_fxCashInitialAttestationPending =
        !m_fxInstrumentByBaseCurrency.empty();
    m_positionsRefreshConflict = false;
    m_riskSnapshot = IBAuthoritativeRiskSnapshot();
    m_pendingPositionQuantities.clear(); m_pendingPositionContracts.clear();
    m_authoritativePositionQuantities.clear(); m_authoritativePositionContracts.clear();
    m_pendingFxCashBalances.clear();
    m_authoritativeFxCashBalances.clear();
    m_authoritativeFxPositionQuantities.clear();
    m_authoritativeFxCashExposures.clear();
    m_brokerMutationGeneration = 0;
    m_recoveryAuditMinimumConnectionEpoch = 0;
    m_recoveryAuditBarrierComplete = false;
    m_recoveryAuditBarrierConnectionEpoch = 0;
    m_recoveryAuditBarrierAttemptedConnectionEpoch = 0;
    m_recoveryAuditBarrierMutationGeneration = 0;
    m_recoveryAuditBarrierReason.clear();
    m_riskSnapshot.fxCashComplete = m_fxInstrumentByBaseCurrency.empty();

    // Keep adapter transport gates in HeptaIBRiskConfig, but route the order
    // decision itself through the venue-independent policy.  All common
    // budgets are explicit in the adapter config so the richer PAPER profile
    // can bind the exact same values before this transport is called.
    m_cachedRiskLimits = DeterministicRiskLimits{};
    m_cachedRiskLimits.orderSubmissionEnabled =
        m_cfg.risk.enableOrderSubmission;
    m_cachedRiskLimits.globalKillSwitch = m_cfg.risk.globalKillSwitch;
    m_cachedRiskLimits.flattenOnly = m_cfg.risk.flattenOnly;
    m_cachedRiskLimits.maxOrderQuantity = m_cfg.risk.maxOrderQuantity;
    m_cachedRiskLimits.maxOrdersPerMinute = m_cfg.risk.maxOrdersPerMinute;
    m_cachedRiskLimits.maxPriceDeviationBps =
        m_cfg.risk.maxPriceDeviationBps;
    m_cachedRiskLimits.maxOrderNotional = m_cfg.risk.maxOrderNotional;
    m_cachedRiskLimits.maxActiveOrders = m_cfg.risk.maxActiveOrders;
    m_cachedRiskLimits.maxGrossPosition = m_cfg.risk.maxGrossPosition;
    m_cachedRiskLimits.requireFreshQuote = m_cfg.risk.requireFreshQuote;
    m_cachedRiskLimits.requireCompleteSnapshot =
        m_cfg.risk.requireCompleteSnapshot;

    m_riskCtxScratch = DeterministicRiskContext{};
    EmitObsEvent("init", "\"host\":\"" + EscapeJson(m_cfg.host) + "\",\"port\":" + std::to_string(m_cfg.port) + ",\"clientId\":" + std::to_string(m_cfg.clientId));
    return true;
}

bool HeptaIBGatewayAdapter::PrepareReconnectCashAttestation(
    const std::map<std::string, double>& observedBalances,
    std::string& reason) {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    if (observedBalances.size() !=
            m_cfg.authoritativeCashFxContracts.size() ||
        m_cfg.authoritativeCashFxBaselines.size() !=
            m_cfg.authoritativeCashFxContracts.size()) {
        reason = "IB_FX_CASH_RECONNECT_CHECKPOINT_INVALID";
        return false;
    }
    for (std::map<std::string, InstrumentRef>::const_iterator contract =
             m_cfg.authoritativeCashFxContracts.begin();
         contract != m_cfg.authoritativeCashFxContracts.end(); ++contract) {
        const std::map<std::string, double>::const_iterator observed =
            observedBalances.find(contract->first);
        const std::map<std::string, double>::const_iterator baseline =
            m_cfg.authoritativeCashFxBaselines.find(contract->first);
        if (observed == observedBalances.end() ||
            baseline == m_cfg.authoritativeCashFxBaselines.end() ||
            !std::isfinite(observed->second) ||
            !std::isfinite(baseline->second)) {
            reason = "IB_FX_CASH_RECONNECT_CHECKPOINT_INVALID";
            return false;
        }
    }
    m_cfg.authoritativeCashFxStartupObservedBalances = observedBalances;
    m_fxCashInitialAttestationPending = !observedBalances.empty();
    reason.clear();
    return true;
}

bool HeptaIBGatewayAdapter::Connect() {
    auto t0 = std::chrono::steady_clock::now();
    EmitObsEvent("connect.start", "\"host\":\"" + EscapeJson(m_cfg.host) + "\",\"port\":" + std::to_string(m_cfg.port) + ",\"clientId\":" + std::to_string(m_cfg.clientId));

    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    if (m_terminalTransportHalted) {
        m_connected = false;
        // A deferred cancel belongs to the transport generation in which it
        // was requested.  Terminal transport fencing makes that generation
        // unusable; never replay its local intent on a later resurrection.
        m_pendingCancelOrderIds.clear();
        m_lastRejectReason = "IB_TERMINAL_TRANSPORT_HALTED";
        return false;
    }
    if (m_connectionEpoch == std::numeric_limits<std::uint64_t>::max()) {
        m_connected = false;
        m_pendingCancelOrderIds.clear();
        m_orderLifecycle.InvalidateConnectionEpoch();
        InvalidateCorrelationSnapshot("IB_CORRELATION_CONNECTION_EPOCH_EXHAUSTED");
        InvalidateTerminalCorrelationSnapshot(
            "IB_TERMINAL_CORRELATION_CONNECTION_EPOCH_EXHAUSTED");
        InvalidateRiskSnapshot("IB_RISK_CONNECTION_EPOCH_EXHAUSTED");
        EmitObsEvent("connect.epoch_exhausted");
        return false;
    }
    ++m_connectionEpoch;
    // Reference prices are connection-epoch scoped.  Never carry a quote
    // observed on an older broker session into a new send decision.
    m_lastReferencePrice = 0.0;
    m_lastReferencePriceTs = 0;
    // The rolling send-attempt budget is account/process scoped, not broker
    // epoch scoped.  Retain recent attempts across reconnects so an outage
    // cannot be used to reset the common rate limiter; Init() clears it only
    // for a genuinely new adapter configuration generation.
    // A pending cancel is deliberately process-local and epoch-bound.  Once
    // the transport is replaced, the old order id may be reused by IB; only
    // recovery after a fresh authoritative snapshot may create a new intent.
    m_pendingCancelOrderIds.clear();
    m_exposureGeneration = 0;
    m_observedEconomicFillQuantityByOrderId.clear();
    m_postFillExposureGenerationByOrderId.clear();
    m_postFillReconciliationOrderIds.clear();
    m_coherentRiskRefreshPending = false;
    m_coherentRiskRefreshForRecoveryAudit = false;
    m_recoveryAuditBarrierComplete = false;
    m_recoveryAuditBarrierConnectionEpoch = 0;
    m_recoveryAuditBarrierAttemptedConnectionEpoch = 0;
    m_recoveryAuditBarrierMutationGeneration = 0;
    m_recoveryAuditBarrierReason =
        "IB_RECOVERY_AUDIT_BARRIER_NOT_COMPLETE";
    m_orderLifecycle.ActivateConnectionEpoch(m_connectionEpoch);
    m_terminalCorrelationRequestIssuedForEpoch = false;
    m_terminalExecutionRequestId = 0;
    InvalidateCorrelationSnapshot("IB_CORRELATION_CONNECTION_CHANGED");
    InvalidateTerminalCorrelationSnapshot(
        "IB_TERMINAL_CORRELATION_CONNECTION_CHANGED");
    InvalidateRiskSnapshot("IB_RISK_CONNECTION_CHANGED");
    if (m_apiConnectAttempted) {
        if (m_api) m_api->Disconnect();
        // An explicitly injected wrapper without an explicitly injected
        // rebuild factory cannot be safely resurrected. Production always has
        // the default factory; offline tests must opt in to reconnect behavior.
        m_api = m_reconnectApiFactory ? m_reconnectApiFactory() :
            std::unique_ptr<IIBApiWrapper>();
        BindEventIngressFence();
    }
    m_apiConnectAttempted = true;
    if (!m_api) {
        m_connected = false;
        m_pendingCancelOrderIds.clear();
        m_orderLifecycle.InvalidateConnectionEpoch();
        InvalidateCorrelationSnapshot("IB_CORRELATION_API_UNAVAILABLE");
        InvalidateTerminalCorrelationSnapshot(
            "IB_TERMINAL_CORRELATION_API_UNAVAILABLE");
        InvalidateRiskSnapshot("IB_RISK_API_UNAVAILABLE");
        EmitLatency("connect", "api_connect", std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - t0).count(), false, "\"reason\":\"api_null_after_rebuild\"");
        return false;
    }

    IBConnectParams p;
    p.host = m_cfg.host;
    p.port = m_cfg.port;
    p.clientId = m_cfg.clientId;
    p.account = m_cfg.account;
    p.readOnly = m_cfg.readOnly;

    m_api->SetConnectionEpoch(m_connectionEpoch);
    m_connected = m_api->Connect(p);
    if (!m_connected) {
        m_pendingCancelOrderIds.clear();
        m_orderLifecycle.InvalidateConnectionEpoch();
    }
    EmitLatency("connect", "api_connect", std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - t0).count(), m_connected,
        "\"status\":\"" + EscapeJson(GetStatusString()) + "\""
        + ",\"wrapperRebuilt\":true");
    return m_connected;
}

void HeptaIBGatewayAdapter::Disconnect() {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    if (m_api) m_api->Disconnect();
    m_connected = false;
    m_lastReferencePrice = 0.0;
    m_lastReferencePriceTs = 0;
    m_pendingCancelOrderIds.clear();
    m_orderLifecycle.InvalidateConnectionEpoch();
    InvalidateCorrelationSnapshot("IB_CORRELATION_DISCONNECTED");
    InvalidateTerminalCorrelationSnapshot(
        "IB_TERMINAL_CORRELATION_DISCONNECTED");
    InvalidateRiskSnapshot("IB_RISK_DISCONNECTED");
    m_postFillReconciliationOrderIds.clear();
    m_postFillExposureGenerationByOrderId.clear();
    m_orderRiskBaselines.clear();
    InvalidateRecoveryAuditBarrier("IB_RECOVERY_AUDIT_DISCONNECTED");
}

bool HeptaIBGatewayAdapter::EncodeVenueOrderRef(
    const std::string& correlationId, std::string& orderRef,
    std::string& reason) const {
    return IbVenueCorrelationCodec::EncodeOrderRef(
        correlationId, orderRef, reason);
}

bool HeptaIBGatewayAdapter::DecodeVenueOrderRef(
    const std::string& orderRef, std::string& correlationId,
    std::string& reason) const {
    return IbVenueCorrelationCodec::DecodeOrderRef(
        orderRef, correlationId, reason);
}

long HeptaIBGatewayAdapter::GetLastValidOrderId() const {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    if (!m_api) return -1;
    return m_api->GetLastValidOrderId();
}

IBAuthoritativeRecoveryAuditSnapshot
HeptaIBGatewayAdapter::BuildRecoveryAuditSnapshotLocked() const {
    IBAuthoritativeRecoveryAuditSnapshot snapshot;
    snapshot.active = m_correlationSnapshot;
    snapshot.terminal = m_terminalCorrelationSnapshot;
    snapshot.risk = m_riskSnapshot;
    snapshot.positionQuantities = m_authoritativePositionQuantities;
    for (std::map<std::string, double>::const_iterator it =
             m_authoritativeFxPositionQuantities.begin();
         it != m_authoritativeFxPositionQuantities.end(); ++it)
        snapshot.positionQuantities[it->first] = it->second;
    snapshot.exposureGeneration = m_exposureGeneration;
    snapshot.terminalExposureGeneration =
        m_terminalCorrelationSnapshot.exposureGeneration;
    snapshot.riskAbsorbedExposureGeneration =
        m_riskSnapshot.riskAbsorbedExposureGeneration;
    snapshot.postFillRiskReconciliationPending =
        !m_postFillReconciliationOrderIds.empty() ||
        !m_riskSnapshot.coherentRefreshComplete ||
        m_riskSnapshot.riskAbsorbedExposureGeneration !=
            m_exposureGeneration;
    snapshot.barrierComplete = m_recoveryAuditBarrierComplete &&
        m_recoveryAuditBarrierConnectionEpoch == m_connectionEpoch &&
        m_recoveryAuditBarrierMutationGeneration ==
            m_brokerMutationGeneration;
    snapshot.reasonCode = snapshot.barrierComplete ? std::string() :
        (m_recoveryAuditBarrierReason.empty() ?
            "IB_RECOVERY_AUDIT_BARRIER_NOT_COMPLETE" :
            m_recoveryAuditBarrierReason);
    return snapshot;
}

IBAuthoritativeRecoveryAuditSnapshot
HeptaIBGatewayAdapter::GetAuthoritativeRecoveryAuditSnapshot() const {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    return BuildRecoveryAuditSnapshotLocked();
}

IBAuthoritativeRecoveryAuditSnapshot
HeptaIBGatewayAdapter::BeginRecoveryAuditBarrier() {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    IBAuthoritativeRecoveryAuditSnapshot snapshot =
        BuildRecoveryAuditSnapshotLocked();
    const bool cleanPrecondition = m_connectionEpoch != 0 &&
        snapshot.active.complete && snapshot.active.generation != 0 &&
        snapshot.active.connectionEpoch == m_connectionEpoch &&
        snapshot.active.activeOrderIds.empty() &&
        // A historical execution discovered after an older flat risk view is
        // exactly why recovery requests a new epoch. Only a live fill awaiting
        // runtime reconciliation blocks disconnect/reconnect here.
        m_postFillReconciliationOrderIds.empty();
    if (m_recoveryAuditMinimumConnectionEpoch == 0) {
        if (!cleanPrecondition) {
            snapshot.barrierComplete = false;
            snapshot.reasonCode =
                "IB_RECOVERY_AUDIT_PRECONDITIONS_NOT_FLAT";
            return snapshot;
        }
        if (m_connectionEpoch ==
            std::numeric_limits<std::uint64_t>::max()) {
            snapshot.barrierComplete = false;
            snapshot.reasonCode =
                "IB_RECOVERY_AUDIT_CONNECTION_EPOCH_EXHAUSTED";
            return snapshot;
        }
        m_recoveryAuditMinimumConnectionEpoch = m_connectionEpoch + 1;
        snapshot.barrierComplete = false;
        snapshot.newConnectionEpochRequired = true;
        snapshot.reasonCode =
            "IB_RECOVERY_AUDIT_NEW_CONNECTION_EPOCH_REQUIRED";
        return snapshot;
    }
    if (m_connectionEpoch < m_recoveryAuditMinimumConnectionEpoch) {
        snapshot.barrierComplete = false;
        snapshot.newConnectionEpochRequired = true;
        snapshot.reasonCode =
            "IB_RECOVERY_AUDIT_NEW_CONNECTION_EPOCH_REQUIRED";
        return snapshot;
    }
    if (snapshot.barrierComplete) return snapshot;
    if (m_recoveryAuditBarrierAttemptedConnectionEpoch ==
            m_connectionEpoch && cleanPrecondition &&
        m_connectionEpoch !=
            std::numeric_limits<std::uint64_t>::max()) {
        m_recoveryAuditMinimumConnectionEpoch = m_connectionEpoch + 1;
        snapshot.newConnectionEpochRequired = true;
        snapshot.reasonCode =
            "IB_RECOVERY_AUDIT_NEW_CONNECTION_EPOCH_REQUIRED";
    }
    return snapshot;
}

bool HeptaIBGatewayAdapter::HaltTransportForTerminalAuditLocked(
    std::vector<IBEvent>& drainedEvents,
    IBAuthoritativeRecoveryAuditSnapshot& frozenSnapshot,
    std::string& reason)
{
    drainedEvents.clear();
    if (m_terminalTransportHalted)
    {
        m_pendingCancelOrderIds.clear();
        frozenSnapshot = BuildRecoveryAuditSnapshotLocked();
        if (!m_terminalTransportDrainVerified)
        {
            reason = "POST_CUTOFF_SIGNED_WITNESS_REQUIRED";
            return false;
        }
        reason.clear();
        return true;
    }
    if (!m_api || !m_api->IsConnected())
    {
        m_pendingCancelOrderIds.clear();
        reason = "IB_TERMINAL_TRANSPORT_NOT_CONNECTED";
        return false;
    }
    // Connect and every broker-send callback must fail before the wrapper is
    // touched. The runtime already closed lifecycle and joined its poll owner.
    m_terminalTransportHalted = true;
    m_terminalTransportDrainVerified = false;
    m_terminalCallbacksInFlight = 0;
    try
    {
        IBTerminalTransportDrainWitness witness;
        if (!m_api->HaltAndDrainTerminalTransport(
                drainedEvents, witness, reason))
        {
            // Unsupported witnesses still require an actual transport cut.
            // Disconnect is not promoted to evidence and no queued callback is
            // allowed to set the verified-drain bit.
            try { m_api->Disconnect(); }
            catch (...) {}
            m_connected = false;
            m_terminalCallbacksInFlight = witness.callbacksInFlight;
            frozenSnapshot = BuildRecoveryAuditSnapshotLocked();
            if (reason.empty()) reason = "IB_TERMINAL_RAW_DRAIN_FAILED";
            m_pendingCancelOrderIds.clear();
            return false;
        }
        m_connected = false;
        m_pendingCancelOrderIds.clear();
        m_terminalCallbacksInFlight = witness.callbacksInFlight;
        m_terminalTransportDrainVerified =
            witness.ingressHalted && witness.readerStopped &&
            witness.rawMessageQueueDrained &&
            witness.callbackEventQueueDrained &&
            !witness.eventQueueOverflowed &&
            witness.callbacksInFlight == 0;
        if (!m_terminalTransportDrainVerified)
        {
            reason = "IB_TERMINAL_TRANSPORT_WITNESS_INVALID";
            m_pendingCancelOrderIds.clear();
            frozenSnapshot = BuildRecoveryAuditSnapshotLocked();
            return false;
        }
    }
    catch (...)
    {
        m_pendingCancelOrderIds.clear();
        frozenSnapshot = BuildRecoveryAuditSnapshotLocked();
        reason = "IB_TERMINAL_TRANSPORT_HALT_EXCEPTION";
        return false;
    }
    frozenSnapshot = BuildRecoveryAuditSnapshotLocked();
    reason.clear();
    return true;
}

bool HeptaIBGatewayAdapter::ReqAccountSummary() {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    if (!m_coherentRiskRefreshDispatching) {
        m_coherentRiskRefreshPending = false;
        m_coherentRiskRefreshForRecoveryAudit = false;
        m_riskSnapshot.complete = false;
        m_riskSnapshot.coherentRefreshComplete = false;
    }
    if (m_accountRefreshPending) return false;
    if (!m_api || !m_api->IsConnected()) {
        InvalidateRiskSnapshot("IB_ACCOUNT_SUMMARY_REFRESH_DISCONNECTED");
        return false;
    }
    if (m_riskGeneration == std::numeric_limits<std::uint64_t>::max()) {
        InvalidateRiskSnapshot("IB_RISK_GENERATION_EXHAUSTED");
        return false;
    }
    ++m_riskGeneration;
    m_accountRefreshPending = true;
    m_accountRefreshObserved = false;
    m_accountReadyObserved = false;
    m_accountReady = false;
    m_fxCashRefreshConflict = false;
    m_pendingFxCashBalances.clear();
    m_riskSnapshot.connectionEpoch = m_connectionEpoch;
    m_riskSnapshot.generation = m_riskGeneration;
    m_riskSnapshot.accountGeneration = m_riskGeneration;
    m_riskSnapshot.accountComplete = false;
    if (!m_fxInstrumentByBaseCurrency.empty()) {
        m_riskSnapshot.fxCashComplete = false;
        m_riskSnapshot.fxCashGeneration = m_riskGeneration;
        // CASH and securities are one combined risk view. Account refresh
        // alone must never relabel retained reqPositions data with a newer
        // generation; require a matching new positions end boundary too.
        m_riskSnapshot.positionsComplete = false;
        m_authoritativeFxCashBalances.clear();
        m_authoritativeFxPositionQuantities.clear();
        m_authoritativeFxCashExposures.clear();
        RefreshGrossAbsolutePosition();
    }
    m_riskSnapshot.reasonCode = "IB_ACCOUNT_SUMMARY_REFRESH_PENDING";
    if (!m_api->ReqAccountSummary()) {
        m_accountRefreshPending = false;
        m_riskSnapshot.reasonCode = "IB_ACCOUNT_SUMMARY_REFRESH_REJECTED";
        return false;
    }
    return true;
}

bool HeptaIBGatewayAdapter::ReqPositions() {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    if (!m_coherentRiskRefreshDispatching) {
        m_coherentRiskRefreshPending = false;
        m_coherentRiskRefreshForRecoveryAudit = false;
        m_riskSnapshot.complete = false;
        m_riskSnapshot.coherentRefreshComplete = false;
    }
    if (m_positionsRefreshPending) return false;
    if (!m_api || !m_api->IsConnected()) {
        InvalidateRiskSnapshot("IB_POSITIONS_REFRESH_DISCONNECTED");
        return false;
    }
    if (m_riskGeneration == std::numeric_limits<std::uint64_t>::max()) {
        InvalidateRiskSnapshot("IB_RISK_GENERATION_EXHAUSTED");
        return false;
    }
    ++m_riskGeneration;
    m_positionsRefreshPending = true;
    m_positionsRefreshConflict = false;
    m_pendingPositionQuantities.clear(); m_pendingPositionContracts.clear();
    m_riskSnapshot.connectionEpoch = m_connectionEpoch;
    m_riskSnapshot.generation = m_riskGeneration;
    m_riskSnapshot.positionsGeneration = m_riskGeneration;
    m_riskSnapshot.positionsComplete = false;
    m_riskSnapshot.grossAbsolutePosition = 0.0;
    m_riskSnapshot.reasonCode = "IB_POSITIONS_REFRESH_PENDING";
    if (!m_api->ReqPositions()) {
        m_positionsRefreshPending = false;
        m_riskSnapshot.reasonCode = "IB_POSITIONS_REFRESH_REJECTED";
        return false;
    }
    return true;
}

bool HeptaIBGatewayAdapter::ReqRiskRefresh() {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    return BeginCoherentRiskRefresh(false);
}

bool HeptaIBGatewayAdapter::ReqRecoveryAuditRiskRefresh() {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    if (!m_correlationSnapshot.complete ||
        !m_terminalCorrelationSnapshot.complete ||
        m_correlationSnapshot.connectionEpoch != m_connectionEpoch ||
        m_terminalCorrelationSnapshot.connectionEpoch != m_connectionEpoch ||
        m_correlationSnapshot.generation == 0 ||
        m_terminalCorrelationSnapshot.generation == 0) {
        m_lastRejectReason =
            "IB_RECOVERY_AUDIT_BROKER_BARRIER_INCOMPLETE";
        return false;
    }
    m_recoveryAuditBarrierAttemptedConnectionEpoch = m_connectionEpoch;
    return BeginCoherentRiskRefresh(true);
}

bool HeptaIBGatewayAdapter::BeginCoherentRiskRefresh(
    bool recoveryAuditBarrier) {
    if (m_accountRefreshPending || m_positionsRefreshPending)
        return false;
    if (m_riskGeneration >
        std::numeric_limits<std::uint64_t>::max() - 2) {
        InvalidateRiskSnapshot("IB_RISK_GENERATION_EXHAUSTED");
        return false;
    }
    m_coherentRiskRefreshPending = true;
    m_coherentRiskRefreshForRecoveryAudit = recoveryAuditBarrier;
    m_coherentRiskRefreshConnectionEpoch = m_connectionEpoch;
    m_coherentRiskRefreshExposureGeneration = m_exposureGeneration;
    m_coherentRiskRefreshActiveGeneration = recoveryAuditBarrier ?
        m_correlationSnapshot.generation : 0;
    m_coherentRiskRefreshTerminalGeneration = recoveryAuditBarrier ?
        m_terminalCorrelationSnapshot.generation : 0;
    m_coherentRiskRefreshMutationGeneration = m_brokerMutationGeneration;
    // Publish both expected generations before invoking either wrapper method.
    // This remains correct even for a test wrapper that synchronously makes an
    // End callback observable during dispatch rather than queueing it normally.
    m_coherentRiskRefreshAccountGeneration = m_riskGeneration + 1;
    m_coherentRiskRefreshPositionGeneration = m_riskGeneration + 2;
    m_riskSnapshot.complete = false;
    m_riskSnapshot.coherentRefreshComplete = false;
    m_coherentRiskRefreshDispatching = true;
    const bool accountDispatched = ReqAccountSummary();
    const bool positionsDispatched = accountDispatched && ReqPositions();
    m_coherentRiskRefreshDispatching = false;
    if (!accountDispatched) {
        m_coherentRiskRefreshPending = false;
        m_coherentRiskRefreshForRecoveryAudit = false;
        return false;
    }
    if (!positionsDispatched) {
        InvalidateRiskSnapshot("IB_RISK_REFRESH_PARTIAL_DISPATCH");
        return false;
    }
    return true;
}

bool HeptaIBGatewayAdapter::HasPendingPostFillRiskReconciliation() const {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    return !m_postFillReconciliationOrderIds.empty() ||
        !m_riskSnapshot.coherentRefreshComplete ||
        m_riskSnapshot.riskAbsorbedExposureGeneration !=
            m_exposureGeneration;
}

bool HeptaIBGatewayAdapter::HasPendingLivePostFillRiskReconciliation() const {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    return !m_postFillReconciliationOrderIds.empty();
}

bool HeptaIBGatewayAdapter::AcknowledgePostFillRiskReconciled(long orderId) {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    const std::map<long, std::uint64_t>::const_iterator exposure =
        m_postFillExposureGenerationByOrderId.find(orderId);
    if (orderId < 0 || exposure ==
            m_postFillExposureGenerationByOrderId.end() ||
        !m_riskSnapshot.complete ||
        !m_riskSnapshot.coherentRefreshComplete ||
        m_riskSnapshot.riskAbsorbedExposureGeneration < exposure->second ||
        m_postFillReconciliationOrderIds.erase(orderId) == 0)
        return false;
    m_postFillExposureGenerationByOrderId.erase(orderId);
    m_orderRiskBaselines.erase(orderId);
    if (m_correlationSnapshot.complete) {
        m_correlationSnapshot.activeOrderIds.erase(orderId);
        for (std::map<std::string, long>::iterator it =
                 m_correlationSnapshot.activeOrderIdsByCorrelation.begin();
             it != m_correlationSnapshot.activeOrderIdsByCorrelation.end();) {
            if (it->second == orderId)
                it = m_correlationSnapshot.activeOrderIdsByCorrelation.erase(it);
            else
                ++it;
        }
    }
    return true;
}

bool HeptaIBGatewayAdapter::GetOrderRiskBaseline(
    long orderId, IBOrderRiskBaseline& out) const {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    const std::map<long, IBOrderRiskBaseline>::const_iterator found =
        m_orderRiskBaselines.find(orderId);
    if (found == m_orderRiskBaselines.end()) return false;
    out = found->second;
    return true;
}

bool HeptaIBGatewayAdapter::ReqOpenOrders() {
    return BeginOpenOrderRefresh(false);
}

bool HeptaIBGatewayAdapter::ReqAuthoritativeOpenOrders() {
    return BeginOpenOrderRefresh(true);
}

bool HeptaIBGatewayAdapter::BeginOpenOrderRefresh(bool accountWide) {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    if (!m_postFillReconciliationOrderIds.empty()) {
        m_lastRejectReason = "IB_POST_FILL_RISK_REFRESH_PENDING";
        return false;
    }
    if (m_correlationRefreshPending) {
        return false;
    }
    if (!m_api || !m_api->IsConnected()) {
        InvalidateCorrelationSnapshot("IB_CORRELATION_REFRESH_DISCONNECTED");
        return false;
    }
    if (m_correlationGeneration == std::numeric_limits<std::uint64_t>::max()) {
        InvalidateCorrelationSnapshot("IB_CORRELATION_GENERATION_EXHAUSTED");
        return false;
    }
    ++m_correlationGeneration;
    m_correlationRefreshPending = true;
    m_correlationRefreshConflict = false;
    m_pendingCorrelationOrderIds.clear();
    m_pendingActiveOrderIds.clear();
    m_correlationSnapshot.connectionEpoch = m_connectionEpoch;
    m_correlationSnapshot.generation = m_correlationGeneration;
    m_correlationSnapshot.complete = false;
    m_correlationSnapshot.reasonCode = "IB_CORRELATION_REFRESH_PENDING";
    m_correlationSnapshot.activeOrderIdsByCorrelation.clear();
    m_correlationSnapshot.activeOrderIds.clear();
    const bool requested = accountWide ?
        m_api->ReqAllOpenOrders() : m_api->ReqOpenOrders();
    if (!requested) {
        InvalidateCorrelationSnapshot("IB_CORRELATION_REFRESH_REJECTED");
        return false;
    }
    return true;
}

bool HeptaIBGatewayAdapter::ReqTerminalCorrelations() {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    // completedOrdersEnd has no request id. A second request in one connection
    // epoch could be falsely completed by a delayed End from the first request,
    // so retry requires a reconnect/new epoch even after failure or timeout.
    if (m_terminalCorrelationRequestIssuedForEpoch) {
        m_lastRejectReason =
            "IB_TERMINAL_CORRELATION_RECONNECT_REQUIRED";
        return false;
    }
    if (m_completedOrdersRefreshPending || m_executionsRefreshPending)
        return false;
    if (!m_api || !m_api->IsConnected()) {
        InvalidateTerminalCorrelationSnapshot(
            "IB_TERMINAL_CORRELATION_REFRESH_DISCONNECTED");
        return false;
    }
    if (m_terminalCorrelationGeneration >=
        static_cast<std::uint64_t>(std::numeric_limits<int>::max())) {
        InvalidateTerminalCorrelationSnapshot(
            "IB_TERMINAL_CORRELATION_GENERATION_EXHAUSTED");
        return false;
    }
    ++m_terminalCorrelationGeneration;
    m_terminalCorrelationRequestIssuedForEpoch = true;
    m_terminalExecutionRequestId =
        static_cast<int>(m_terminalCorrelationGeneration);
    m_completedOrdersRefreshPending = true;
    m_executionsRefreshPending = true;
    m_terminalCorrelationRefreshConflict = false;
    m_pendingTerminalOrderIds.clear();
    m_pendingTerminalStatuses.clear();
    m_pendingTerminalCorrelationsByOrderId.clear();
    m_pendingExecutionOrderIds.clear();
    m_terminalCorrelationSnapshot.connectionEpoch = m_connectionEpoch;
    m_terminalCorrelationSnapshot.generation =
        m_terminalCorrelationGeneration;
    m_terminalCorrelationSnapshot.complete = false;
    m_terminalCorrelationSnapshot.exposureGeneration = 0;
    m_terminalCorrelationSnapshot.reasonCode =
        "IB_TERMINAL_CORRELATION_REFRESH_PENDING";
    m_terminalCorrelationSnapshot.terminalOrderIdsByCorrelation.clear();
    m_terminalCorrelationSnapshot.terminalStatusesByCorrelation.clear();
    m_terminalCorrelationSnapshot.executionOrderIds.clear();

    // completedOrders(apiOnly=false) is account-wide across API clients. The
    // adapter still enforces exact-account and H1 namespace filtering.
    if (!m_api->ReqCompletedOrders() ||
        !m_api->ReqExecutions(m_terminalExecutionRequestId)) {
        InvalidateTerminalCorrelationSnapshot(
            "IB_TERMINAL_CORRELATION_REFRESH_REJECTED");
        return false;
    }
    return true;
}

bool HeptaIBGatewayAdapter::ReqMktData(int reqId, const IBContractLite& c) {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    if (!m_api || m_terminalTransportHalted || !m_api->IsConnected())
        return false;
    return m_api->ReqMktData(reqId, c);
}

std::recursive_mutex& HeptaIBGatewayAdapter::EventIngressFence() {
    return *m_eventIngressFence;
}

void HeptaIBGatewayAdapter::BeginEventIngressAdmission() {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    if (m_api) m_api->BeginEventIngressAdmission();
}

void HeptaIBGatewayAdapter::EndEventIngressAdmission() {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    if (m_api) m_api->EndEventIngressAdmission();
}

void HeptaIBGatewayAdapter::FlushEventIngressAdmission() {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    if (m_api) m_api->FlushEventIngressAdmission();
}

void HeptaIBGatewayAdapter::CompleteEventIngressAdmission() {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    if (m_api) m_api->CompleteEventIngressAdmission();
}

bool HeptaIBGatewayAdapter::EventIngressAdmissionFailed() const {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    return m_api && m_api->EventIngressAdmissionFailed();
}

void HeptaIBGatewayAdapter::BindEventIngressFence() {
    if (m_api) m_api->SetEventIngressFence(m_eventIngressFence);
}

bool HeptaIBGatewayAdapter::CancelMktData(int reqId) {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    if (!m_api || m_terminalTransportHalted || !m_api->IsConnected())
        return false;
    return m_api->CancelMktData(reqId);
}

void HeptaIBGatewayAdapter::UpdateReferencePrice(double price) {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    if (m_connected && std::isfinite(price) && price > 0.0) {
        m_lastReferencePrice = price;
        m_lastReferencePriceTs = std::time(nullptr);
    }
}

void HeptaIBGatewayAdapter::SetRuntimeFlattenOnly(bool enabled, const std::string& reason) {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    m_cfg.risk.flattenOnly = enabled;
    m_cachedRiskLimits.flattenOnly = enabled;
    std::string fields = "\"enabled\":" + std::string(enabled ? "true" : "false");
    if (!reason.empty()) {
        fields += ",\"reason\":\"" + EscapeJson(reason) + "\"";
    }
    EmitObsEvent("risk.flatten_only_runtime", fields);
}

bool HeptaIBGatewayAdapter::CanCancelOrder(long orderId, std::string* suppressReason) const {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    return m_orderLifecycle.CanCancel(orderId, suppressReason);
}

bool HeptaIBGatewayAdapter::CancelOrder(long orderId) {
    auto t0 = std::chrono::steady_clock::now();
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    m_lastRejectReason.clear();
    if (!m_api || !m_connected) {
        EmitLatency("cancel", "api_cancel", std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - t0).count(), false,
            "\"orderId\":" + std::to_string(orderId) + ",\"reason\":\"not_connected\"");
        return false;
    }
    std::string suppress;
    if (!CanCancelOrder(orderId, &suppress)) {
        if (suppress == "NO_BROKER_ACK") {
            // The local place was accepted but IB has not delivered
            // Submitted/OpenOrder yet.  Queue one cancel intent and dispatch
            // it from the first broker acknowledgement callback; sending
            // CancelOrder now races the asynchronous submit and yields a
            // misleading broker rejection.
            m_pendingCancelOrderIds.insert(orderId);
            m_lastRejectReason = "IB_CANCEL_DEFERRED_UNTIL_BROKER_ACK";
            EmitLatency("cancel", "deferred_until_broker_ack",
                std::chrono::duration_cast<std::chrono::milliseconds>(
                    std::chrono::steady_clock::now() - t0).count(), true,
                "\"orderId\":" + std::to_string(orderId));
            return true;
        }
        EmitLatency("cancel", "guard_block", std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - t0).count(), false,
            "\"orderId\":" + std::to_string(orderId) + ",\"reason\":\"" + EscapeJson(suppress) + "\"");
        return false;
    }
    if (!BeginBrokerMutation("IB_RECOVERY_AUDIT_CANCEL_MUTATION"))
        return false;
    bool ok = m_api->CancelOrder(orderId);
    if (ok) {
        m_cancelSubmitTs[orderId] = std::chrono::steady_clock::now();
    }
    EmitLatency("cancel", "api_cancel", std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - t0).count(), ok,
        "\"orderId\":" + std::to_string(orderId));
    return ok;
}

void HeptaIBGatewayAdapter::DispatchPendingCancelIfAcknowledged(
    long orderId, const std::string& status, bool economicTerminal,
    bool authoritativeOpenOrderAck) {
    if (m_pendingCancelOrderIds.find(orderId) ==
            m_pendingCancelOrderIds.end() || !m_api || !m_connected)
        return;
    // A Filled callback without positive quantity/price is only a broker
    // lifecycle acknowledgement, not economic terminal evidence.  The
    // projector and lifecycle tracker intentionally keep such an order
    // cancellable; do not erase a queued cancel merely because its text says
    // "Filled".
    const bool terminal = status == "Cancelled" || status == "ApiCancelled" ||
        status == "Inactive" || status == "Rejected" ||
        (status == "Filled" && economicTerminal);
    if (terminal) {
        m_pendingCancelOrderIds.erase(orderId);
        return;
    }
    if (status.empty() && !authoritativeOpenOrderAck)
        return;
    if (!status.empty() && status != "ApiPending" && status != "PendingSubmit" &&
        status != "PreSubmitted" && status != "Submitted" &&
        status != "PartiallyFilled" && status != "PendingCancel" &&
        status != "Filled")
        return;
    // PendingCancel is already an acknowledged cancellation request.  Avoid
    // issuing a second API cancel while retaining the broker's state as the
    // source of truth for final resolution.
    if (status == "PendingCancel") {
        m_pendingCancelOrderIds.erase(orderId);
        return;
    }
    if (!BeginBrokerMutation("IB_RECOVERY_AUDIT_CANCEL_MUTATION")) {
        // Keep the intent queued when the mutation fence is closed.  A later
        // authoritative callback may retry it; dropping it here could leave
        // an acknowledged active order without its requested cancel.
        m_lastRejectReason = "IB_DEFERRED_CANCEL_MUTATION_BLOCKED";
        return;
    }
    const bool sent = m_api->CancelOrder(orderId);
    // The API call has now been attempted.  Whether it was accepted or
    // rejected is durable coordinator/reconciliation state; do not replay the
    // same request on a later status callback.
    m_pendingCancelOrderIds.erase(orderId);
    if (sent) {
        m_cancelSubmitTs[orderId] = std::chrono::steady_clock::now();
        m_lastRejectReason.clear();
    } else {
        m_lastRejectReason = "IB_DEFERRED_CANCEL_API_REJECTED";
    }
    EmitLatency("cancel", "deferred_api_cancel", 0, sent,
        "\"orderId\":" + std::to_string(orderId) +
        ",\"ackStatus\":\"" + EscapeJson(status) + "\"");
}

bool HeptaIBGatewayAdapter::BeginBrokerMutation(
    const std::string& reason) {
    if (m_terminalTransportHalted) {
        m_lastRejectReason = "IB_TERMINAL_TRANSPORT_HALTED";
        return false;
    }
    if (m_brokerMutationGeneration ==
        std::numeric_limits<std::uint64_t>::max()) {
        m_lastRejectReason = "IB_BROKER_MUTATION_GENERATION_EXHAUSTED";
        InvalidateRecoveryAuditBarrier(m_lastRejectReason);
        return false;
    }
    ++m_brokerMutationGeneration;
    InvalidateRecoveryAuditBarrier(reason);
    return true;
}

bool HeptaIBGatewayAdapter::IsOrderGateOpen() const {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    return m_cfg.risk.enableOrderSubmission && !m_cfg.risk.globalKillSwitch &&
        !m_circuitBreakerTripped && m_eventStreamAuthoritative;
}

bool HeptaIBGatewayAdapter::IsCircuitBreakerTripped() const {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    return m_circuitBreakerTripped;
}

bool HeptaIBGatewayAdapter::IsEventStreamAuthoritative() const {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    return m_eventStreamAuthoritative;
}

std::uint64_t HeptaIBGatewayAdapter::GetLastEventOverflowGeneration() const {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    return m_lastEventOverflowGeneration;
}

bool HeptaIBGatewayAdapter::MarkAuthoritativeResyncComplete(std::uint64_t overflowGeneration) {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    if (m_eventStreamAuthoritative || overflowGeneration == 0 ||
        overflowGeneration != m_lastEventOverflowGeneration) {
        return false;
    }
    m_eventStreamAuthoritative = true;
    m_lastRejectReason.clear();
    EmitObsEvent(
        "event_queue.authoritative_resync_complete",
        "\"overflow_generation\":" + std::to_string(overflowGeneration));
    return true;
}

int HeptaIBGatewayAdapter::GetTodayOrderCount() const {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    return m_todayOrderCount;
}

bool HeptaIBGatewayAdapter::RunPreflightChecks(std::string& reason) const {
    std::string code;
    std::string detail;
    const bool ok = RunPreflightChecksDetailed(code, detail);
    if (ok) {
        reason = "RISK_OK";
    } else if (!detail.empty()) {
        reason = code + ": " + detail;
    } else {
        reason = code;
    }
    return ok;
}

bool HeptaIBGatewayAdapter::RunPreflightChecksDetailed(std::string& reasonCode, std::string& detail) const {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    reasonCode = "RISK_OK";
    detail.clear();

    if (!m_api) {
        reasonCode = "RISK_IB_API_NULL";
        detail = "IB API wrapper is null";
        return false;
    }
    if (!m_eventStreamAuthoritative) {
        reasonCode = "RISK_IB_EVENT_STREAM_NOT_AUTHORITATIVE";
        detail = "event loss observed; complete account, position, and open-order resynchronization is required";
        return false;
    }
    if (m_cfg.risk.requireTwsConnected && !m_connected) {
        reasonCode = "RISK_NOT_CONNECTED";
        detail = "IB gateway is not connected";
        return false;
    }
    if (m_cfg.risk.requireNextValidId && GetLastValidOrderId() <= 0) {
        reasonCode = "RISK_NEXT_VALID_ID_NOT_READY";
        detail = "nextValidId not ready";
        return false;
    }
    if (m_cfg.risk.requireAccountConfigured && m_cfg.account.empty()) {
        reasonCode = "RISK_ACCOUNT_NOT_CONFIGURED";
        detail = "account is empty";
        return false;
    }
    if (!m_cfg.account.empty() && !IsAccountWhitelisted(m_cfg.account)) {
        reasonCode = "RISK_ACCOUNT_NOT_WHITELISTED";
        detail = "account is not in whitelist";
        return false;
    }
    // LIVE is not an active product capability.  Keep the low-level adapter
    // fail-closed even when a caller supplies the legacy opt-in flags: the
    // PAPER runtime is the only supported mutation composition, and no
    // direct adapter caller may turn a non-DU account into a venue send.
    if (m_cfg.risk.enableOrderSubmission && !m_cfg.account.empty() &&
        !IsPaperAccount(m_cfg.account)) {
        reasonCode = "RISK_LIVE_UNSUPPORTED";
        detail = "LIVE trading is unsupported by the active adapter";
        return false;
    }
    if (m_cfg.risk.maxOrderQuantity <= 0) {
        reasonCode = "RISK_CONFIG_INVALID_MAX_ORDER_QTY";
        detail = "maxOrderQuantity must be > 0";
        return false;
    }
    if (m_cfg.risk.maxDailyOrders <= 0) {
        reasonCode = "RISK_CONFIG_INVALID_MAX_DAILY_ORDERS";
        detail = "maxDailyOrders must be > 0";
        return false;
    }
    if (m_cfg.risk.maxPriceDeviationBps < 0.0) {
        reasonCode = "RISK_CONFIG_INVALID_MAX_PRICE_DEV_BPS";
        detail = "maxPriceDeviationBps must be >= 0";
        return false;
    }
    if (m_cfg.risk.duplicateOrderWindowSec < 0) {
        reasonCode = "RISK_CONFIG_INVALID_DUP_WINDOW_SEC";
        detail = "duplicateOrderWindowSec must be >= 0";
        return false;
    }
    if (m_cfg.risk.duplicatePriceTolerance < 0.0) {
        reasonCode = "RISK_CONFIG_INVALID_DUP_TOL";
        detail = "duplicatePriceTolerance must be >= 0";
        return false;
    }
    std::string commonRiskReason;
    if (!DeterministicRiskPolicy::ValidateLimits(
            m_cachedRiskLimits, commonRiskReason)) {
        reasonCode = commonRiskReason.empty() ?
            "RISK_LIMITS_INVALID" : commonRiskReason;
        detail = "deterministic risk limits are invalid";
        return false;
    }
    return true;
}

void HeptaIBGatewayAdapter::NotifyErrorEvent(int ibErrorCode) {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    if (ibErrorCode != 0 &&
        m_cfg.risk.fuseIgnoreErrorCodes.find(ibErrorCode) != m_cfg.risk.fuseIgnoreErrorCodes.end()) {
        EmitObsEvent("risk.error_ignored_for_fuse",
            "\"error_code\":" + std::to_string(ibErrorCode) +
            ",\"ignored_codes\":\"" + JoinSortedCodes(m_cfg.risk.fuseIgnoreErrorCodes) + "\"");
        return;
    }

    ++m_consecutiveErrorCount;
    if (ibErrorCode != 0) m_errorCodeCounts[ibErrorCode] += 1;
    const int fuseWeight = FuseWeightForError(ibErrorCode);
    if (fuseWeight > 0) m_errorFuseScore += fuseWeight;

    if (m_cfg.risk.enableErrorCodeBlacklist && ibErrorCode != 0 &&
        m_cfg.risk.errorCodeBlacklist.find(ibErrorCode) != m_cfg.risk.errorCodeBlacklist.end()) {
        m_circuitBreakerTripped = true;
        m_circuitBreakerTripTs = std::time(nullptr);
        EmitObsEvent("risk.circuit_breaker", "\"reason\":\"RISK_IB_ERROR_BLACKLIST\",\"error_code\":" + std::to_string(ibErrorCode));
        return;
    }

    if (m_cfg.risk.enableAutoCircuitBreaker && m_errorFuseScore >= m_cfg.risk.fuseOnErrorCount) {
        m_circuitBreakerTripped = true;
        m_circuitBreakerTripTs = std::time(nullptr);
        EmitObsEvent("risk.circuit_breaker", "\"reason\":\"RISK_IB_ERROR_FUSE\",\"error_count\":" + std::to_string(m_consecutiveErrorCount) + ",\"fuse_score\":" + std::to_string(m_errorFuseScore));
    }
}

long HeptaIBGatewayAdapter::NextOrderId() {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    long ibOrderId = GetLastValidOrderId();
    if (ibOrderId > 0) {
        if (m_localOrderSeed < ibOrderId) m_localOrderSeed = ibOrderId;
    }
    return m_localOrderSeed++;
}

bool HeptaIBGatewayAdapter::IsSameTradingDay() const {
    std::time_t t = std::time(nullptr);
    std::tm nowTm{};
#ifdef _WIN32
    localtime_s(&nowTm, &t);
#else
    localtime_r(&t, &nowTm);
#endif
    return m_dayOfYear == nowTm.tm_yday;
}

bool HeptaIBGatewayAdapter::IsDuplicateOrder(const IBContractLite& c, const IBOrderLite& o, std::time_t nowTs) const {
    if (m_cfg.risk.duplicateOrderWindowSec <= 0 || m_lastOrderSig.empty() || m_lastOrderTs <= 0) {
        return false;
    }

    std::ostringstream oss;
    oss.imbue(std::locale::classic());
    oss << ContractDuplicateKey(c) << "|"
        << o.action << "|" << o.orderType << "|" << std::fixed << std::setprecision(8)
        << o.totalQuantity;
    std::string sigNoPrice = oss.str();

    double lastPrice = 0.0;
    std::string lastNoPrice;
    std::size_t pos = m_lastOrderSig.rfind('|');
    if (pos != std::string::npos) {
        lastNoPrice = m_lastOrderSig.substr(0, pos);
        std::istringstream priceInput(m_lastOrderSig.substr(pos + 1));
        priceInput.imbue(std::locale::classic());
        priceInput >> std::noskipws >> lastPrice;
        if (!priceInput || !priceInput.eof() || !std::isfinite(lastPrice))
            return false;
    }

    if (lastNoPrice != sigNoPrice) return false;
    if (nowTs - m_lastOrderTs > m_cfg.risk.duplicateOrderWindowSec) return false;

    double tol = m_cfg.risk.duplicatePriceTolerance;
    return std::abs(lastPrice - o.lmtPrice) <= tol;
}

void HeptaIBGatewayAdapter::RememberLastOrder(const IBContractLite& c, const IBOrderLite& o, std::time_t nowTs) {
    std::ostringstream oss;
    oss.imbue(std::locale::classic());
    oss << ContractDuplicateKey(c) << "|"
        << o.action << "|" << o.orderType << "|" << std::fixed << std::setprecision(8)
        << o.totalQuantity << "|" << o.lmtPrice;
    m_lastOrderSig = oss.str();
    m_lastOrderTs = nowTs;
}

void HeptaIBGatewayAdapter::EmitObsEvent(const char* eventName, const std::string& fieldsJson) const {
    std::string line = "{\"ts\":\"" + UtcNowIso8601Ms() + "\",\"event\":\"" + EscapeJson(eventName ? eventName : "") + "\"";
    if (!fieldsJson.empty()) line += "," + fieldsJson;
    line += "}";
    AppendObsLogLine(line, m_cfg.observabilityLogPath);
}

void HeptaIBGatewayAdapter::EmitLatency(const char* path, const char* stage, long latencyMs, bool ok, const std::string& fieldsJson) const {
    if (!ShouldEmitObsLatencySample(path, stage)) {
        return;
    }
    std::string fields = "\"path\":\"" + EscapeJson(path ? path : "") + "\",\"stage\":\"" + EscapeJson(stage ? stage : "") +
        "\",\"latency_ms\":" + std::to_string(latencyMs) + ",\"ok\":" + std::string(ok ? "true" : "false");
    if (!fieldsJson.empty()) fields += "," + fieldsJson;
    EmitObsEvent("latency", fields);
}

bool HeptaIBGatewayAdapter::IsPaperAccount(const std::string& account) const {
    if (account.empty()) return false;
    std::string up = account;
    std::transform(up.begin(), up.end(), up.begin(), [](unsigned char c) {
        return c >= static_cast<unsigned char>('a') &&
                c <= static_cast<unsigned char>('z') ?
            static_cast<char>(c - static_cast<unsigned char>('a') +
                              static_cast<unsigned char>('A')) :
            static_cast<char>(c);
    });
    return up.rfind("DU", 0) == 0;
}

bool HeptaIBGatewayAdapter::IsAccountWhitelisted(const std::string& account) const {
    if (account.empty()) return false;
    if (m_cfg.risk.accountWhitelist.empty()) return false;

    std::string acc = account;
    std::transform(acc.begin(), acc.end(), acc.begin(), [](unsigned char c) {
        return c >= static_cast<unsigned char>('a') &&
                c <= static_cast<unsigned char>('z') ?
            static_cast<char>(c - static_cast<unsigned char>('a') +
                              static_cast<unsigned char>('A')) :
            static_cast<char>(c);
    });

    for (const auto& rawRule : m_cfg.risk.accountWhitelist) {
        if (rawRule.empty()) continue;

        std::string rule = rawRule;
        std::transform(rule.begin(), rule.end(), rule.begin(), [](unsigned char c) {
            return c >= static_cast<unsigned char>('a') &&
                    c <= static_cast<unsigned char>('z') ?
                static_cast<char>(c - static_cast<unsigned char>('a') +
                                  static_cast<unsigned char>('A')) :
                static_cast<char>(c);
        });

        if (!rule.empty() && rule.back() == '*') {
            rule.pop_back();
            if (acc.rfind(rule, 0) == 0) return true;
        } else if (acc == rule) {
            return true;
        }
    }
    return false;
}

std::string HeptaIBGatewayAdapter::GetLastRejectReason() const {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    return m_lastRejectReason;
}


const char* HeptaIBGatewayAdapter::GetStatusString() const {
    thread_local std::string tlsStatus;
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    tlsStatus = (m_api ? m_api->GetStatusString() : (m_connected ? "IB_CONNECTED" : "IB_DISCONNECTED"));
    return tlsStatus.c_str();
}

std::string HeptaIBGatewayAdapter::GetPositionSummary() const {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    if (m_symbolNetPosition.empty()) return "flat";
    std::ostringstream oss;
    // This summary is surfaced through status/read contracts and must remain
    // deterministic even when an embedding process installs a comma-decimal
    // locale.  It also feeds operator diagnostics, so locale drift here can
    // make an otherwise valid numeric position unreadable.
    oss.imbue(std::locale::classic());
    bool first = true;
    for (const auto& kv : m_symbolNetPosition) {
        if (!first) oss << ";";
        first = false;
        oss << kv.first << ":" << kv.second;
    }
    return oss.str();
}

bool HeptaIBGatewayAdapter::ProveAndCommitFlatNoop(
    const std::string& instrument,
    std::uint64_t expectedConnectionEpoch,
    std::uint64_t expectedPositionGeneration,
    const std::function<bool()>& durableCommit,
    bool* commitAttempted,
    std::string* reason) {
    std::lock_guard<std::recursive_mutex> lock(m_apiMutex);
    if (commitAttempted != nullptr) *commitAttempted = false;
    const auto reject = [&](const char* code) {
        m_lastRejectReason = code;
        if (reason != nullptr) *reason = code;
        return false;
    };
    if (instrument.empty() || !m_riskSnapshot.accountComplete ||
        !m_riskSnapshot.positionsComplete ||
        !m_riskSnapshot.fxCashComplete || !m_eventStreamAuthoritative ||
        expectedConnectionEpoch == 0 || expectedPositionGeneration == 0 ||
        m_riskSnapshot.connectionEpoch != expectedConnectionEpoch ||
        m_riskSnapshot.positionsGeneration != expectedPositionGeneration)
        return reject("IB_FLATTEN_POSITION_SNAPSHOT_MISMATCH");
    double positionQuantity = 0.0;
    const std::map<std::string, InstrumentRef>::const_iterator fx =
        m_cfg.authoritativeCashFxContracts.find(instrument);
    if (fx != m_cfg.authoritativeCashFxContracts.end()) {
        std::string positionReason;
        if (!ResolveAuthoritativePositionQuantity(
                instrument, fx->second, positionQuantity, positionReason) ||
            positionQuantity != 0.0)
            return reject("IB_FLATTEN_POSITION_CHANGED_BEFORE_NOOP");
    } else {
        const auto position =
            m_authoritativePositionQuantities.find(instrument);
        if (position != m_authoritativePositionQuantities.end() &&
            (!std::isfinite(position->second) || position->second != 0.0))
            return reject("IB_FLATTEN_POSITION_CHANGED_BEFORE_NOOP");
    }
    if (!m_correlationSnapshot.complete ||
        m_correlationSnapshot.connectionEpoch != expectedConnectionEpoch ||
        !m_correlationSnapshot.activeOrderIds.empty())
        return reject("IB_FLATTEN_ACTIVE_ORDER_SNAPSHOT_UNSAFE");
    if (!m_prePlaceOrderSendCheck)
        return reject("IB_PAPER_KILL_SWITCH_READER_REQUIRED");
    if (!durableCommit || commitAttempted == nullptr)
        return reject("IB_FLATTEN_NOOP_COMMIT_CALLBACK_INVALID");
    IBFinalOrderSendContext context;
    context.exactReduceOnly = true;
    context.proveFlatOnly = true;
    context.instrument = instrument;
    IBContractLite contract;
    IBOrderLite order;
    std::string finalReason;
    bool allowed = false;
    try {
        allowed = m_prePlaceOrderSendCheck(
            &context, contract, order, &finalReason);
    } catch (const std::exception& error) {
        finalReason = error.what();
    } catch (...) {
        finalReason = "final prove-flat check threw";
    }
    if (!allowed) {
        if (finalReason.empty())
            finalReason = "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN";
        m_lastRejectReason = finalReason;
        if (reason != nullptr) *reason = finalReason;
        return false;
    }
    *commitAttempted = true;
    bool committed = false;
    try {
        committed = durableCommit();
    } catch (...) {
        committed = false;
    }
    if (!committed)
        return reject("OMS_FLATTEN_NOOP_WRITE_FAILED");
    m_lastRejectReason.clear();
    if (reason != nullptr) reason->clear();
    return true;
}
