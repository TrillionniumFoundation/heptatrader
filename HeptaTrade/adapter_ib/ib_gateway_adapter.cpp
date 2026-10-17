#include "ib_gateway_adapter.h"
#include "../execution/ib_bracket_h1_identity.h"
#include "ib_h1_native_stop_echo_research.h"
#include "ib_h1_native_stop_remaining_research.h"
#include "ib_venue_correlation.h"
#include "../execution/ib_h1_native_id_restart_floor.h"
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
    std::transform(right.begin(), right.end(), right.begin(), [](unsigned char ch) { return static_cast<char>(std::toupper(ch)); });
    if (right == "CALL") return "C";
    if (right == "PUT") return "P";
    return right;
}

std::string ContractDuplicateKey(const IBContractLite& c) {
    std::ostringstream oss;
    oss.exceptions(std::ios::badbit | std::ios::failbit);
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
    out << date << '.' << std::setfill('0') << std::setw(3)
        << static_cast<int>(ms.count()) << 'Z';
    return out.str();
}

int GetEnvIntClamped(const char* name, int defValue, int minValue) {
    const char* raw = std::getenv(name);
    if (raw == nullptr || *raw == '\0') return defValue;
    char* endPtr = nullptr;
    long v = std::strtol(raw, &endPtr, 10);
    if (endPtr == raw) return defValue;
    if (v < static_cast<long>(minValue)) v = static_cast<long>(minValue);
    if (v > static_cast<long>(INT_MAX)) v = static_cast<long>(INT_MAX);
    return static_cast<int>(v);
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
    for (const auto& item : cfg.authoritativeCryptoContracts)
    {
        const auto& c = item.second;
        if (item.first != "BTC.USD" || c.symbol != "BTC" || c.secType != "CRYPTO" ||
            c.exchange != "PAXOS" || c.currency != "USD" ||
            !c.primaryExchange.empty() || !c.lastTradeDateOrContractMonth.empty() ||
            !c.right.empty() || c.strike != 0 || !c.multiplier.empty() ||
            !c.localSymbol.empty() || !c.tradingClass.empty() ||
            cfg.authoritativeCashFxContracts.count(item.first)) return false;
    }
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
    m_pendingActiveOrderClientIds.clear();
    m_pendingActivePermIdOwners.clear();
    m_pendingUnboundManualPermIds.clear();
    m_pendingProtectiveCandidatesByPermId.clear();
    m_pendingProtectiveMetadataIncomplete = false;
    m_pendingMissingPermanentId = false;
    m_postFillReconciliationOrderIds.clear();
    m_postFillExposureGenerationByOrderId.clear();
    m_observedEconomicFillQuantityByIdentity.clear();
    m_orderRiskBaselines.clear();
    m_liveTerminalBindings.clear();
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
    m_pendingExecutionCorrelations.clear();
    m_pendingExecutionIdsByCorrelation.clear();
    m_pendingExecutionCumulativeQtyByCorrelation.clear();
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
    m_rejectedFxCashObservationBalances.clear();
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

    m_cachedRiskCfg.enableOrderSubmission = m_cfg.risk.enableOrderSubmission;
    m_cachedRiskCfg.globalKillSwitch = m_cfg.risk.globalKillSwitch;
    m_cachedRiskCfg.flattenOnly = m_cfg.risk.flattenOnly;
    m_cachedRiskCfg.maxOrderQuantity = m_cfg.risk.maxOrderQuantity;
    m_cachedRiskCfg.maxDailyOrders = m_cfg.risk.maxDailyOrders;
    m_cachedRiskCfg.maxPriceDeviationBps = m_cfg.risk.maxPriceDeviationBps;
    m_cachedRiskCfg.allowLiveTrading = m_cfg.risk.allowLiveTrading;
    m_cachedRiskCfg.liveKillSwitch = m_cfg.risk.liveKillSwitch;

    m_cachedAccountWhitelisted = IsAccountWhitelisted(m_cfg.account);
    m_cachedPaperAccount = IsPaperAccount(m_cfg.account);

    m_riskCtxScratch = PreTradeRiskContext{};
    m_riskCtxScratch.venue = "IB";
    m_riskCtxScratch.account = m_cfg.account;
    m_riskCtxScratch.adapterTag = "ib_gateway_adapter";
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

bool HeptaIBGatewayAdapter::Connect(int handshakeTimeoutMs) {
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
    // A pending cancel is deliberately process-local and epoch-bound.  Once
    // the transport is replaced, the old order id may be reused by IB; only
    // recovery after a fresh authoritative snapshot may create a new intent.
    m_pendingCancelOrderIds.clear();
    m_exposureGeneration = 0;
    m_observedEconomicFillQuantityByIdentity.clear();
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
    p.handshakeTimeoutMs = std::max(100, std::min(handshakeTimeoutMs, 20000));

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
    snapshot.transportConnected = m_connected && m_api && m_api->IsConnected();
    snapshot.eventStreamAuthoritative = m_eventStreamAuthoritative;
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
            m_brokerMutationGeneration &&
        // A prior BeginRecoveryAuditBarrier may require a *new* connection
        // epoch. A same-epoch refresh cannot promote this stale boundary.
        (m_recoveryAuditMinimumConnectionEpoch == 0 ||
         m_connectionEpoch >= m_recoveryAuditMinimumConnectionEpoch);
    snapshot.reasonCode = snapshot.barrierComplete ? std::string() :
        (m_recoveryAuditBarrierReason.empty() ?
            "IB_RECOVERY_AUDIT_BARRIER_NOT_COMPLETE" :
            m_recoveryAuditBarrierReason);
    snapshot.callbackQueueLag = m_callbackQueueLag;
    snapshot.callbackConflictCount = m_callbackConflictCount;
    snapshot.callbackConflictMetricsSaturated =
        m_callbackConflictMetricsSaturated;
    return snapshot;
}

IBAuthoritativeRecoveryAuditSnapshot
HeptaIBGatewayAdapter::GetAuthoritativeRecoveryAuditSnapshot() const {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    return BuildRecoveryAuditSnapshotLocked();
}

bool HeptaIBGatewayAdapter::InspectResearchH1OcaAccountScope(
    const hepta_bracket_h1::FrozenBracket& original,
    const hepta_bracket_h1::BoundRefs& refs,
    IBH1OcaAdapterScopeObservation& observation,
    std::string& reason) const
{
    observation=IBH1OcaAdapterScopeObservation{};
    std::lock_guard<std::recursive_mutex> lock(m_apiMutex);
    if(!m_api || !m_api->IsConnected() || !m_connected ||
       m_terminalTransportHalted ||
       original.executionDomain!="PAPER" ||
       original.account.empty() || original.account!=m_cfg.account ||
       !IsPaperAccount(m_cfg.account) ||
       original.clientId<=0 || original.clientId!=m_cfg.clientId ||
       original.contractId!=12087792 ||
       original.childOrderId<=0 ||
       m_connectionEpoch==0 ||
       m_connectionEpoch<original.connectionEpoch ||
       m_cfg.authoritativeCashFxContracts.count("EUR.USD")!=1) {
        reason="H1_OCA_ADAPTER_EXACT_ACCOUNT_OWNER_OR_CONTRACT_INVALID";
        return false;
    }
    const InstrumentRef& fx=m_cfg.authoritativeCashFxContracts.at("EUR.USD");
    if(fx.symbol!="EUR" || fx.secType!="CASH" ||
       fx.currency!="USD" || fx.exchange!="IDEALPRO") {
        reason="H1_OCA_ADAPTER_FX_CASH_CONTRACT_CHANGED";
        return false;
    }
    if(!hepta_bracket_h1::VerifyFrozenRefs(original,refs,reason))
        return false;
    const IBAuthoritativeRecoveryAuditSnapshot snap=
        BuildRecoveryAuditSnapshotLocked();
    if(!m_eventStreamAuthoritative ||
       !m_h1NativeStopRefreshAccountWide ||
       !snap.active.complete || !snap.risk.complete ||
       !snap.risk.coherentRefreshComplete ||
       !snap.risk.accountComplete || !snap.risk.positionsComplete ||
       !snap.risk.fxCashComplete ||
       snap.active.generation==0 || snap.risk.generation==0 ||
       snap.risk.accountGeneration==0 || snap.risk.positionsGeneration==0 ||
       snap.risk.fxCashGeneration==0 ||
       snap.active.connectionEpoch!=m_connectionEpoch ||
       snap.risk.connectionEpoch!=m_connectionEpoch ||
       snap.postFillRiskReconciliationPending ||
       snap.exposureGeneration!=snap.riskAbsorbedExposureGeneration ||
       snap.newConnectionEpochRequired ||
       snap.callbackConflictMetricsSaturated || snap.callbackConflictCount!=0) {
        reason="H1_OCA_ADAPTER_ACTIVE_RISK_BARRIER_INCOMPLETE";
        return false;
    }
    const auto id=snap.active.activeOrderIdsByCorrelation.find(
        refs.childCorrelation);
    if(snap.active.activeOrderIds.size()!=1 ||
       snap.active.activeOrderIdsByCorrelation.size()!=1 ||
       id==snap.active.activeOrderIdsByCorrelation.end() ||
       id->second!=original.childOrderId ||
       snap.active.activeOrderIds.count(original.childOrderId)!=1) {
        reason="H1_OCA_ADAPTER_ORIGINAL_ACTIVE_STOP_NOT_UNIQUE";
        return false;
    }
    const auto position=snap.positionQuantities.find("EUR.USD");
    const double expected=original.side=="BUY" ?
        original.quantity:-original.quantity;
    if(position==snap.positionQuantities.end() ||
       !std::isfinite(position->second) || position->second!=expected ||
       snap.risk.grossAbsolutePosition < std::fabs(expected)) {
        reason="H1_OCA_ADAPTER_PHYSICAL_FX_EXPOSURE_MISMATCH";
        return false;
    }
    observation.originalAccountAndOrderSetConsistent=true;
    // This diagnostic is separate from the working STOP witness. A matching
    // parent numeric order ID alone is not an executed parent; only the
    // original completedOrder(Filled) correlated with real execDetails
    // carrying the exact cumulative parent quantity qualifies.
    const auto& terminal=snap.terminal;
    const auto parentStatus=terminal.terminalStatusesByCorrelation.find(
        refs.parentCorrelation);
    const auto parentCumQty=
        terminal.executionCumulativeQtyByCorrelation.find(
            refs.parentCorrelation);
    observation.originalParentFilledTerminalAndQtyCorrelated =
        terminal.complete && terminal.generation!=0 &&
        terminal.connectionEpoch==m_connectionEpoch &&
        terminal.exposureGeneration<=snap.riskAbsorbedExposureGeneration &&
        terminal.HasEconomicTerminal(
            refs.parentCorrelation,original.parentOrderId,m_connectionEpoch) &&
        parentStatus!=terminal.terminalStatusesByCorrelation.end() &&
        parentStatus->second=="Filled" &&
        parentCumQty!=terminal.executionCumulativeQtyByCorrelation.end() &&
        std::isfinite(parentCumQty->second) &&
        parentCumQty->second==original.quantity;
    const auto nativeStop=m_h1NativeStopOpenOrderEchoes.find(
        original.childOrderId);
    // A native SDK echo and a global Broker-visible protective candidate
    // originate from the SAME original openOrder. Do not claim even
    // diagnostic H1 correspondence if their parent/OCA/transmit or
    // permanent identity disagrees; the two read-only projections must
    // establish the same original owner/contract/child without invention.
    const bool brokerCensusStopConsistent=[&]() {
        if(nativeStop==m_h1NativeStopOpenOrderEchoes.end()) return false;
        const IBEvent& e=nativeStop->second;
        if(e.brokerPermId<=0 ||
           e.brokerPermId!=e.nativeOrder.permanentId ||
           e.brokerParentOrderId!=e.nativeOrder.parentId ||
           e.brokerOcaGroup!=e.nativeOrder.ocaGroup ||
           e.brokerTransmit!=e.nativeOrder.transmit) return false;
        const auto owner=snap.active.activeOrderByPermanentId.find(
            e.brokerPermId);
        const auto candidate=snap.active.visibleProtectiveCandidatesByPermId.find(
            e.brokerPermId);
        if(owner==snap.active.activeOrderByPermanentId.end() ||
           candidate==snap.active.visibleProtectiveCandidatesByPermId.end())
            return false;
        const auto& b=candidate->second;
        return owner->second.first==original.clientId &&
            owner->second.second==original.childOrderId &&
            b.brokerClientId==original.clientId &&
            b.orderId==original.childOrderId &&
            b.orderRef==refs.childRef &&
            b.orderType=="STP" &&
            b.action==(original.side=="BUY" ? "SELL":"BUY") &&
            b.parentOrderId==original.parentOrderId &&
            b.ocaGroup==refs.ocaGroup && b.transmit &&
            b.sdkNativePresent && b.nativeContractId==original.contractId &&
            b.nativeOcaType==1 && b.nativeTimeInForce=="GTC" &&
            b.nativeTriggerMethod==0 && b.stopTrigger==original.stopPrice &&
            b.totalQuantity==original.quantity;
    }();
    if(brokerCensusStopConsistent &&
       hepta_ib_h1_native_stop_echo::MatchesOriginalSdkStop(
           nativeStop->second,original,refs,m_connectionEpoch)) {
        observation.sdkNativeStopEchoMatches=true;
        observation.sdkNativeStopPermanentId=
            nativeStop->second.nativeOrder.permanentId;
        const auto latestStatus=m_h1NativeStopOrderStatusEchoes.find(
            original.childOrderId);
        if(latestStatus!=m_h1NativeStopOrderStatusEchoes.end() &&
           hepta_ib_h1_native_stop_remaining::MatchesUnfilledStopStatus(
               nativeStop->second,latestStatus->second,
               original,refs,m_connectionEpoch)) {
            observation.sdkNativeStopUnfilledRemainingMatches=true;
            observation.sdkNativeStopRemainingQuantity=
                latestStatus->second.number3;
            // Exact SDK order identity AND physical position are not enough:
            // all four independent Broker evidence legs must be recent on
            // the SAME host monotonic clock, never supplied by an agent.
            constexpr std::uint64_t kMaxAgeNs = 3000000000ULL;
            const auto nowNs=static_cast<std::uint64_t>(
                std::chrono::duration_cast<std::chrono::nanoseconds>(
                    std::chrono::steady_clock::now().time_since_epoch()).count());
            auto age = [&](std::uint64_t timestamp,std::uint64_t& ageMs) {
                if(timestamp==0 || timestamp>nowNs ||
                   nowNs-timestamp>kMaxAgeNs) return false;
                ageMs=(nowNs-timestamp)/1000000ULL;
                return true;
            };
            const bool a=age(m_h1AccountWideOrderEndMonotonicNs,
                observation.activeOrderBarrierAgeMs);
            const bool b=age(m_h1CoherentRiskEndMonotonicNs,
                observation.coherentRiskBarrierAgeMs);
            const bool c=age(nativeStop->second.queueIngressMonotonicNs,
                observation.stopNativeEchoAgeMs);
            const bool d=age(latestStatus->second.queueIngressMonotonicNs,
                observation.stopRemainingStatusAgeMs);
            observation.brokerObservationClockBounded = a && b && c && d &&
                latestStatus->second.queueIngressMonotonicNs >=
                    nativeStop->second.queueIngressMonotonicNs;
        }
    }
    observation.exactStopNativeOrderId=original.childOrderId;
    observation.brokerSignedFxPosition=position->second;
    observation.connectionEpoch=m_connectionEpoch;
    observation.riskGeneration=snap.risk.generation;
    observation.activeOrdersGeneration=snap.active.generation;
    // Bounded callback freshness does not prove the stop's real trigger,
    // ongoing server-held availability or safe OCA cancellation/overfill.
    // Never qualify any actual exit from this observation.
    observation.nativeStopBrokerWorkingProven=false;
    observation.stopPriceTriggerProven=false;
    observation.accountSnapshotFreshnessProven=false;
    observation.maySendOrdinaryExit=false;
    observation.mayStopDriver=false;
    reason.clear();
    return true;
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
    m_rejectedFxCashObservationBalances.clear();
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
    // The owner audit already fenced this transport and demanded a new
    // epoch. Even a complete account/position End from this old connection
    // cannot close that fence; do not issue a misleading audit refresh.
    if (m_recoveryAuditMinimumConnectionEpoch != 0 &&
        m_connectionEpoch < m_recoveryAuditMinimumConnectionEpoch) {
        m_lastRejectReason =
            "IB_RECOVERY_AUDIT_NEW_CONNECTION_EPOCH_REQUIRED";
        return false;
    }
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
    m_h1CoherentRiskEndMonotonicNs = 0;
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
    m_pendingActiveOrderClientIds.clear();
    m_pendingActivePermIdOwners.clear();
    m_pendingUnboundManualPermIds.clear();
    m_pendingProtectiveCandidatesByPermId.clear();
    m_pendingProtectiveMetadataIncomplete = false;
    m_pendingMissingPermanentId = false;
    m_h1NativeStopOpenOrderEchoes.clear();
    m_h1NativeStopOrderStatusEchoes.clear();
    m_h1NativeStopRefreshAccountWide = accountWide;
    m_h1AccountWideOrderEndMonotonicNs = 0;
    m_correlationSnapshot.connectionEpoch = m_connectionEpoch;
    m_correlationSnapshot.generation = m_correlationGeneration;
    m_correlationSnapshot.complete = false;
    m_correlationSnapshot.reasonCode = "IB_CORRELATION_REFRESH_PENDING";
    m_correlationSnapshot.activeOrderIdsByCorrelation.clear();
    m_correlationSnapshot.activeOrderIds.clear();
    m_correlationSnapshot.activeOrderClientIdsByOrderId.clear();
    m_correlationSnapshot.activeOrderByPermanentId.clear();
    m_correlationSnapshot.unboundManualPermanentIds.clear();
    m_correlationSnapshot.visibleProtectiveCandidatesByPermId.clear();
    m_correlationSnapshot.protectiveMetadataIncomplete = false;
    m_correlationSnapshot.activeOrderMissingPermanentId = false;
    const bool requested = accountWide ?
        m_api->ReqAllOpenOrders() : m_api->ReqOpenOrders();
    if (!requested) {
        InvalidateCorrelationSnapshot("IB_CORRELATION_REFRESH_REJECTED");
        return false;
    }
    return true;
}


bool HeptaIBGatewayAdapter::BeginAccountPostFenceRead(std::string& reason) {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    if (m_postFenceReadStage != PostFenceReadStage::NotStarted) {
        reason = "IB_ACCOUNT_POST_FENCE_READ_ALREADY_ATTEMPTED";
        return false; // no replay of untagged IB openOrderEnd in this epoch
    }
    const auto initial = BuildRecoveryAuditSnapshotLocked();
    if (!initial.transportConnected || !initial.eventStreamAuthoritative ||
        !initial.active.complete || !initial.terminal.complete ||
        !initial.risk.complete || !initial.risk.coherentRefreshComplete ||
        !initial.risk.accountComplete || !initial.risk.positionsComplete ||
        !initial.risk.fxCashComplete || !initial.barrierComplete ||
        initial.postFillRiskReconciliationPending ||
        initial.active.connectionEpoch == 0 ||
        initial.active.connectionEpoch != initial.terminal.connectionEpoch ||
        initial.active.connectionEpoch != initial.risk.connectionEpoch ||
        m_correlationRefreshPending || m_accountRefreshPending ||
        m_positionsRefreshPending || m_coherentRiskRefreshPending) {
        reason = "IB_ACCOUNT_POST_FENCE_BASELINE_INCOMPLETE";
        return false;
    }
    // The policy callback already proved the ORIGINAL journal-durable
    // account fence and the coordinator's same-mutex local drain. Record
    // all Broker watermarks BEFORE issuing an actual fresh read on this
    // original API client. This cannot certify unbound manual TWS orders.
    const std::uint64_t epoch = m_connectionEpoch;
    const std::uint64_t mutation = m_brokerMutationGeneration;
    const std::uint64_t exposure = m_exposureGeneration;
    const std::uint64_t terminal = m_terminalCorrelationGeneration;
    if (!BeginOpenOrderRefresh(true)) {
        m_postFenceReadStage = PostFenceReadStage::Failed;
        m_postFenceFailureReason =
            "IB_ACCOUNT_POST_FENCE_OPEN_ORDERS_DISPATCH_FAILED";
        reason = m_postFenceFailureReason;
        return false;
    }
    m_postFenceConnectionEpoch = epoch;
    m_postFenceMutationGeneration = mutation;
    m_postFenceExposureGeneration = exposure;
    m_postFenceTerminalGeneration = terminal;
    m_postFenceActiveGeneration = m_correlationGeneration;
    m_postFenceRiskGeneration = 0;
    m_postFenceReadStage = PostFenceReadStage::AwaitActive;
    reason = "IB_ACCOUNT_POST_FENCE_OPEN_ORDER_END_PENDING";
    return true;
}

void HeptaIBGatewayAdapter::InvalidateAccountPostFenceRead(
    const std::string& reason)
{
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    if (m_postFenceReadStage == PostFenceReadStage::NotStarted)
        return;
    m_postFenceReadStage = PostFenceReadStage::Failed;
    m_postFenceFailureReason = reason.empty() ?
        "IB_ACCOUNT_POST_FENCE_LOCAL_STATE_CHANGED" : reason;
}


bool HeptaIBGatewayAdapter::BindAccountPostFenceToFreshTerminalEpoch(
    std::string& reason)
{
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    // This is called by the existing recovered owner-audit callback AFTER
    // BeginRecoveryAuditBarrier has demanded a strictly NEW connection.
    // It never issues a Broker call, disconnects, or schedules a reconnect.
    if (m_postFenceReadStage != PostFenceReadStage::Observed ||
        m_postFenceExpectedTerminalConnectionEpoch != 0 ||
        m_postFenceConnectionEpoch == 0 ||
        m_connectionEpoch != m_postFenceConnectionEpoch ||
        m_recoveryAuditMinimumConnectionEpoch != m_connectionEpoch + 1 ||
        m_connectionEpoch == std::numeric_limits<std::uint64_t>::max()) {
        reason = "IB_ACCOUNT_POST_FENCE_NEW_TERMINAL_BINDING_UNAVAILABLE";
        return false;
    }
    const auto observed = BuildRecoveryAuditSnapshotLocked();
    if (!observed.transportConnected || !observed.eventStreamAuthoritative ||
        !observed.active.complete || !observed.terminal.complete ||
        !observed.risk.complete || !observed.risk.coherentRefreshComplete ||
        observed.active.connectionEpoch != m_connectionEpoch ||
        observed.terminal.connectionEpoch != m_connectionEpoch ||
        observed.risk.connectionEpoch != m_connectionEpoch ||
        observed.active.generation != m_postFenceActiveGeneration ||
        observed.risk.generation != m_postFenceRiskGeneration ||
        m_brokerMutationGeneration != m_postFenceMutationGeneration ||
        m_exposureGeneration != m_postFenceExposureGeneration ||
        observed.postFillRiskReconciliationPending ||
        !observed.active.activeOrderIds.empty() ||
        observed.risk.grossAbsolutePosition != 0.0) {
        reason = "IB_ACCOUNT_POST_FENCE_NEW_TERMINAL_ECONOMIC_PRECONDITION";
        return false;
    }
    for (const auto& position : observed.positionQuantities)
        if (position.first.empty() || !std::isfinite(position.second) ||
            position.second != 0.0) {
            reason = "IB_ACCOUNT_POST_FENCE_NEW_TERMINAL_POSITION_PRESENT";
            return false;
        }
    m_postFenceExpectedTerminalConnectionEpoch =
        m_recoveryAuditMinimumConnectionEpoch;
    m_postFenceReadStage = PostFenceReadStage::AwaitNewTerminalEpoch;
    reason = "IB_ACCOUNT_POST_FENCE_NEW_TERMINAL_EPOCH_REQUIRED";
    return true;
}

IBAccountPostFenceReadObservation HeptaIBGatewayAdapter::PollAccountPostFenceRead() {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    IBAccountPostFenceReadObservation status;
    if (m_postFenceReadStage == PostFenceReadStage::NotStarted)
        return status;
    status.started = true;
    status.connectionEpoch = m_postFenceConnectionEpoch;
    status.activeGeneration = m_postFenceActiveGeneration;
    status.riskGeneration = m_postFenceRiskGeneration;
    status.terminalGeneration = m_postFenceTerminalGeneration;
    status.brokerMutationGeneration = m_postFenceMutationGeneration;
    status.economicExposureGeneration = m_postFenceExposureGeneration;

    // A fresh completed-orders End is available only after a separate,
    // owner-controlled IB reconnect. Preserve the PRE-fence read watermark
    // across Disconnect/Connect and accept ONLY the specifically requested
    // next connection epoch; do not infer continuity from reset generation
    // counters or an ordinary current-epoch account-risk refresh.
    if (m_postFenceReadStage == PostFenceReadStage::AwaitNewTerminalEpoch ||
        m_postFenceReadStage == PostFenceReadStage::NewTerminalEpochObserved) {
        status.terminalNewEpochRequired = true;
        status.newTerminalConnectionEpoch =
            m_postFenceNewTerminalConnectionEpoch;
        status.newTerminalGeneration = m_postFenceNewTerminalGeneration;
        status.newActiveGeneration = m_postFenceNewActiveGeneration;
        status.newRiskGeneration = m_postFenceNewRiskGeneration;
        if (m_brokerMutationGeneration != m_postFenceMutationGeneration) {
            m_postFenceReadStage = PostFenceReadStage::Failed;
            m_postFenceFailureReason =
                "IB_ACCOUNT_POST_FENCE_NEW_EPOCH_BROKER_MUTATION";
        } else if (m_postFenceExpectedTerminalConnectionEpoch == 0 ||
                   m_connectionEpoch >
                       m_postFenceExpectedTerminalConnectionEpoch) {
            m_postFenceReadStage = PostFenceReadStage::Failed;
            m_postFenceFailureReason =
                "IB_ACCOUNT_POST_FENCE_NEW_TERMINAL_EPOCH_SKIPPED";
        }
        if (m_postFenceReadStage == PostFenceReadStage::Failed) {
            status.reasonCode = m_postFenceFailureReason;
            return status;
        }
        if (m_connectionEpoch < m_postFenceExpectedTerminalConnectionEpoch ||
            !m_connected || !m_api || !m_api->IsConnected()) {
            status.reasonCode =
                "IB_ACCOUNT_POST_FENCE_NEW_TERMINAL_CONNECT_PENDING";
            return status;
        }
        const auto fresh = BuildRecoveryAuditSnapshotLocked();
        const std::uint64_t epoch =
            m_postFenceExpectedTerminalConnectionEpoch;
        if (!fresh.transportConnected || !fresh.eventStreamAuthoritative) {
            m_postFenceReadStage = PostFenceReadStage::Failed;
            m_postFenceFailureReason =
                "IB_ACCOUNT_POST_FENCE_NEW_TERMINAL_STREAM_UNTRUSTED";
            status.reasonCode = m_postFenceFailureReason;
            return status;
        }
        // An ordinary same-epoch refresh completing BETWEEN health reads
        // must not silently replace the exact new terminal proof cohort.
        if (m_postFenceReadStage ==
                PostFenceReadStage::NewTerminalEpochObserved &&
            (fresh.active.generation != m_postFenceNewActiveGeneration ||
             fresh.terminal.generation != m_postFenceNewTerminalGeneration ||
             fresh.risk.generation != m_postFenceNewRiskGeneration)) {
            m_postFenceReadStage = PostFenceReadStage::Failed;
            m_postFenceFailureReason =
                "IB_ACCOUNT_POST_FENCE_NEW_TERMINAL_GENERATION_DRIFT";
            status.reasonCode = m_postFenceFailureReason;
            return status;
        }
        if (!fresh.active.complete || !fresh.terminal.complete ||
            !fresh.risk.complete || !fresh.risk.coherentRefreshComplete ||
            !fresh.barrierComplete || fresh.postFillRiskReconciliationPending ||
            fresh.active.connectionEpoch != epoch ||
            fresh.terminal.connectionEpoch != epoch ||
            fresh.risk.connectionEpoch != epoch) {
            if (m_postFenceReadStage ==
                    PostFenceReadStage::NewTerminalEpochObserved) {
                m_postFenceReadStage = PostFenceReadStage::Failed;
                m_postFenceFailureReason =
                    "IB_ACCOUNT_POST_FENCE_NEW_TERMINAL_PROOF_INVALIDATED";
                status.reasonCode = m_postFenceFailureReason;
            } else {
                status.reasonCode =
                    "IB_ACCOUNT_POST_FENCE_NEW_TERMINAL_BARRIER_PENDING";
            }
            return status;
        }
        if (fresh.terminal.generation == 0 ||
            fresh.active.generation == 0 ||
            fresh.risk.generation == 0 ||
            fresh.terminal.exposureGeneration >
                fresh.riskAbsorbedExposureGeneration ||
            fresh.riskAbsorbedExposureGeneration !=
                fresh.exposureGeneration ||
            fresh.risk.grossAbsolutePosition < 0.0 ||
            !std::isfinite(fresh.risk.grossAbsolutePosition)) {
            m_postFenceReadStage = PostFenceReadStage::Failed;
            m_postFenceFailureReason =
                "IB_ACCOUNT_POST_FENCE_NEW_TERMINAL_ECONOMIC_DRIFT";
            status.reasonCode = m_postFenceFailureReason;
            return status;
        }
        bool flat = fresh.risk.grossAbsolutePosition == 0.0;
        for (const auto& position : fresh.positionQuantities)
            if (position.first.empty() || !std::isfinite(position.second) ||
                position.second != 0.0) flat = false;
        m_postFenceNewTerminalConnectionEpoch = epoch;
        m_postFenceNewTerminalGeneration = fresh.terminal.generation;
        m_postFenceNewActiveGeneration = fresh.active.generation;
        m_postFenceNewRiskGeneration = fresh.risk.generation;
        m_postFenceReadStage = PostFenceReadStage::NewTerminalEpochObserved;
        status.newTerminalConnectionEpoch = epoch;
        status.newTerminalGeneration = fresh.terminal.generation;
        status.newActiveGeneration = fresh.active.generation;
        status.newRiskGeneration = fresh.risk.generation;
        status.terminalNewEpochObserved = true;
        status.terminalPostFenceComplete = true;
        status.activeThenRiskObserved = true;
        status.activeReadComplete = true;
        status.riskReadComplete = true;
        status.globalActiveOrders = fresh.active.VisibleActiveOrderCount();
        status.observedUnboundManualOrders =
            fresh.active.unboundManualPermanentIds.size();
        status.observedBrokerPermanentIds =
            fresh.active.activeOrderByPermanentId.size();
        status.observedPermanentIdentityComplete =
            !fresh.active.activeOrderMissingPermanentId &&
            fresh.active.activeOrderByPermanentId.size() ==
                status.globalActiveOrders;
        status.externalManualTwsUniverseComplete = false;
        status.brokerZeroObserved =
            fresh.active.activeOrderIds.empty() && flat;
        status.maintenanceStopPermitted = false;
        status.reasonCode = status.brokerZeroObserved ?
            "IB_ACCOUNT_POST_FENCE_NEW_TERMINAL_EPOCH_ZERO_NOT_STOP_PERMIT" :
            "IB_ACCOUNT_POST_FENCE_NEW_TERMINAL_EPOCH_RISK_PRESENT";
        return status;
    }

    const auto fail = [this](const char* code) {
        m_postFenceReadStage = PostFenceReadStage::Failed;
        m_postFenceFailureReason = code;
    };
    if (m_postFenceReadStage == PostFenceReadStage::Failed) {
        status.reasonCode = m_postFenceFailureReason;
        return status;
    }
    if (!m_api || !m_api->IsConnected() || !m_connected ||
        !m_eventStreamAuthoritative ||
        m_connectionEpoch == 0 || m_connectionEpoch != m_postFenceConnectionEpoch) {
        fail("IB_ACCOUNT_POST_FENCE_CONNECTION_OR_STREAM_CHANGED");
    } else if (m_brokerMutationGeneration != m_postFenceMutationGeneration ||
               m_exposureGeneration != m_postFenceExposureGeneration) {
        fail("IB_ACCOUNT_POST_FENCE_BROKER_MUTATION_OR_FILL");
    } else if (m_correlationGeneration != m_postFenceActiveGeneration ||
               m_correlationSnapshot.connectionEpoch != m_postFenceConnectionEpoch ||
               m_terminalCorrelationGeneration != m_postFenceTerminalGeneration ||
               !m_terminalCorrelationSnapshot.complete ||
               m_terminalCorrelationSnapshot.connectionEpoch !=
                   m_postFenceConnectionEpoch) {
        fail("IB_ACCOUNT_POST_FENCE_ORDER_OR_TERMINAL_GENERATION_DRIFT");
    }
    if (m_postFenceReadStage == PostFenceReadStage::Failed) {
        status.reasonCode = m_postFenceFailureReason;
        return status;
    }

    if (m_postFenceReadStage == PostFenceReadStage::AwaitActive) {
        if (!m_correlationSnapshot.complete) {
            if (!m_correlationRefreshPending)
                fail("IB_ACCOUNT_POST_FENCE_ACTIVE_DOWNLOAD_FAILED");
            else
                status.reasonCode = "IB_ACCOUNT_POST_FENCE_OPEN_ORDER_END_PENDING";
        } else {
            // The risk snapshot must be started strictly AFTER the fresh
            // global OpenOrderEnd. This uses the original adapter API mutex;
            // competing fills, cancels, flatten and reconnect fail closed.
            if (!ReqRecoveryAuditRiskRefresh())
                fail("IB_ACCOUNT_POST_FENCE_RISK_REFRESH_REJECTED");
            else {
                m_postFenceRiskGeneration = m_riskGeneration;
                status.riskGeneration = m_postFenceRiskGeneration;
                m_postFenceReadStage = PostFenceReadStage::AwaitRisk;
            }
        }
    }
    if (m_postFenceReadStage == PostFenceReadStage::AwaitRisk) {
        status.activeReadComplete =
            m_correlationSnapshot.complete &&
            m_correlationSnapshot.generation == m_postFenceActiveGeneration;
        if (m_riskGeneration != m_postFenceRiskGeneration ||
            m_riskSnapshot.connectionEpoch != m_postFenceConnectionEpoch)
            fail("IB_ACCOUNT_POST_FENCE_RISK_GENERATION_DRIFT");
        else if (!m_riskSnapshot.complete || !m_riskSnapshot.coherentRefreshComplete) {
            if (!m_coherentRiskRefreshPending && !m_accountRefreshPending &&
                !m_positionsRefreshPending)
                fail("IB_ACCOUNT_POST_FENCE_RISK_DOWNLOAD_FAILED");
            else
                status.reasonCode = "IB_ACCOUNT_POST_FENCE_RISK_END_PENDING";
        } else {
            const auto audited = BuildRecoveryAuditSnapshotLocked();
            if (!audited.barrierComplete ||
                audited.newConnectionEpochRequired ||
                audited.postFillRiskReconciliationPending ||
                audited.riskAbsorbedExposureGeneration != m_postFenceExposureGeneration ||
                audited.terminalExposureGeneration > m_postFenceExposureGeneration)
                fail("IB_ACCOUNT_POST_FENCE_BARRIER_OR_EXPOSURE_DRIFT");
            else
                m_postFenceReadStage = PostFenceReadStage::Observed;
        }
    }
    if (m_postFenceReadStage == PostFenceReadStage::Failed) {
        status.reasonCode = m_postFenceFailureReason;
        return status;
    }
    if (m_postFenceReadStage != PostFenceReadStage::Observed)
        return status;

    const auto audited = BuildRecoveryAuditSnapshotLocked();
    if (!audited.transportConnected || !audited.eventStreamAuthoritative ||
        !audited.active.complete || !audited.terminal.complete ||
        !audited.risk.complete || !audited.risk.coherentRefreshComplete ||
        !audited.barrierComplete || audited.postFillRiskReconciliationPending ||
        audited.active.generation != m_postFenceActiveGeneration ||
        audited.risk.generation != m_postFenceRiskGeneration ||
        audited.riskAbsorbedExposureGeneration != m_postFenceExposureGeneration ||
        audited.exposureGeneration != m_postFenceExposureGeneration) {
        fail("IB_ACCOUNT_POST_FENCE_COMPLETED_PROOF_INVALIDATED");
        status.reasonCode = m_postFenceFailureReason;
        return status;
    }
    status.activeReadComplete = true;
    status.riskReadComplete = true;
    status.activeThenRiskObserved = true;
    status.globalActiveOrders = audited.active.VisibleActiveOrderCount();
    status.observedUnboundManualOrders =
        audited.active.unboundManualPermanentIds.size();
    status.observedBrokerPermanentIds =
        audited.active.activeOrderByPermanentId.size();
    status.observedPermanentIdentityComplete =
        !audited.active.activeOrderMissingPermanentId &&
        audited.active.activeOrderByPermanentId.size() ==
            status.globalActiveOrders;
    status.externalManualTwsUniverseComplete = false;
    bool exactlyFlat = audited.risk.grossAbsolutePosition == 0.0;
    for (const auto& position : audited.positionQuantities) {
        if (position.first.empty() || !std::isfinite(position.second) ||
            position.second != 0.0)
            exactlyFlat = false;
    }
    status.brokerZeroObserved = audited.active.activeOrderIds.empty() && exactlyFlat;
    // CompletedOrdersEnd cannot be safely reissued in one connection epoch
    // (untagged SDK barrier). This diagnostic NEVER claims terminal-post-
    // fence completeness, unbound TWS coverage or a maintenance STOP permit.
    status.terminalPostFenceComplete = false;
    status.maintenanceStopPermitted = false;
    status.reasonCode = status.brokerZeroObserved ?
        "IB_ACCOUNT_POST_FENCE_ACTIVE_RISK_ZERO_NOT_STOP_PERMIT" :
        "IB_ACCOUNT_POST_FENCE_ACTIVE_RISK_PRESENT_NOT_STOP_PERMIT";
    return status;
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
    m_pendingExecutionCorrelations.clear();
    m_pendingExecutionIdsByCorrelation.clear();
    m_pendingExecutionCumulativeQtyByCorrelation.clear();
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
    m_terminalCorrelationSnapshot.executionCumulativeQtyByCorrelation.clear();

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
    EmitObsEvent("market_data.request_started",
        "\"request_id\":" + std::to_string(reqId) +
        ",\"connection_epoch\":" + std::to_string(m_connectionEpoch) +
        ",\"symbol\":\"" + EscapeJson(c.symbol) + "\"" +
        ",\"security_type\":\"" + EscapeJson(c.secType) + "\"" +
        ",\"exchange\":\"" + EscapeJson(c.exchange) + "\"" +
        ",\"currency\":\"" + EscapeJson(c.currency) + "\"");
    const bool sent = m_api->ReqMktData(reqId, c);
    EmitObsEvent("market_data.request_outcome",
        "\"request_id\":" + std::to_string(reqId) +
        ",\"connection_epoch\":" + std::to_string(m_connectionEpoch) +
        ",\"dispatch_accepted\":" + (sent ? "true" : "false"));
    return sent;
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
    EmitObsEvent("market_data.cancel_started",
        "\"request_id\":" + std::to_string(reqId) +
        ",\"connection_epoch\":" + std::to_string(m_connectionEpoch));
    const bool sent = m_api->CancelMktData(reqId);
    EmitObsEvent("market_data.cancel_outcome",
        "\"request_id\":" + std::to_string(reqId) +
        ",\"connection_epoch\":" + std::to_string(m_connectionEpoch) +
        ",\"dispatch_accepted\":" + (sent ? "true" : "false"));
    return sent;
}

void HeptaIBGatewayAdapter::UpdateReferencePrice(double price) {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    if (price > 0.0) {
        m_lastReferencePrice = price;
        m_lastReferencePriceTs = std::time(nullptr);
    }
}

void HeptaIBGatewayAdapter::SetRuntimeFlattenOnly(bool enabled, const std::string& reason) {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    m_cfg.risk.flattenOnly = enabled;
    m_cachedRiskCfg.flattenOnly = enabled;
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

VenueCancelResult HeptaIBGatewayAdapter::CancelOrder(long orderId) {
    std::lock_guard<std::recursive_mutex> lock(m_apiMutex);
    try {
        const auto started = std::chrono::steady_clock::now();
        m_lastRejectReason.clear();
        const auto observe = [&](const char* stage, bool accepted,
                                 const std::string& reason) {
            std::string fields = "\"orderId\":" + std::to_string(orderId);
            if (!reason.empty())
                fields += ",\"reason\":\"" + EscapeJson(reason) + "\"";
            EmitLatency("cancel", stage,
                std::chrono::duration_cast<std::chrono::milliseconds>(
                    std::chrono::steady_clock::now() - started).count(),
                accepted, fields);
        };
        if (!m_api || !m_connected) {
            observe("api_cancel", false, "not_connected");
            return VenueCancelResult::RejectedBeforeSend("IB_CANCEL_NOT_CONNECTED");
        }
        std::string suppress;
        if (!CanCancelOrder(orderId, &suppress)) {
            if (suppress == "NO_BROKER_ACK") {
                m_pendingCancelOrderIds.insert(orderId);
                m_lastRejectReason = "IB_CANCEL_DEFERRED_UNTIL_BROKER_ACK";
                observe("deferred_until_broker_ack", true, "");
                return VenueCancelResult::Deferred();
            }
            observe("guard_block", false, suppress);
            return VenueCancelResult::RejectedBeforeSend(suppress);
        }
        if (!BeginBrokerMutation("IB_RECOVERY_AUDIT_CANCEL_MUTATION"))
            return VenueCancelResult::RejectedBeforeSend(m_lastRejectReason);
        // A bool from the SDK wrapper is not proof of a no-send rejection.
        // After invocation, false or any exception is conservatively unknown.
        if (!m_api->CancelOrder(orderId)) {
            observe("api_cancel", false, "IB_CANCEL_API_OUTCOME_UNCERTAIN");
            return VenueCancelResult::Uncertain("IB_CANCEL_API_OUTCOME_UNCERTAIN");
        }
        m_cancelSubmitTs[orderId] = std::chrono::steady_clock::now();
        observe("api_cancel", true, "");
        return VenueCancelResult::Submitted();
    } catch (...) {
        // Includes diagnostics/bookkeeping failure AFTER the SDK returned.
        return VenueCancelResult();
    }
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
    if (!status.empty() && status != "Accepted" && status != "ApiPending" &&
        status != "PendingSubmit" && status != "PreSubmitted" && status != "Submitted" &&
        status != "PartiallyFilled" && status != "PendingCancel" && status != "PreCancelled" &&
        status != "Filled")
        return;
    // PendingCancel is already an acknowledged cancellation request.  Avoid
    // issuing a second API cancel while retaining the broker's state as the
    // source of truth for final resolution.
    if (status == "PendingCancel" || status == "PreCancelled") {
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
    // Claim this deferred attempt before calling external code. A throw after
    // a possible send must not leave a queue entry that the next ACK resends.
    // A closed mutation fence above still preserves the unattempted intent.
    // The coordinator's durable cancel_pending remains unresolved throughout.
    m_pendingCancelOrderIds.erase(orderId);
    const bool sent = m_api->CancelOrder(orderId);
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
    if (m_cfg.risk.globalKillSwitch) {
        reasonCode = "RISK_GLOBAL_KILL_SWITCH_ON";
        detail = "global kill switch enabled";
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
    if (m_cfg.risk.enableOrderSubmission && !m_cfg.account.empty() && !IsPaperAccount(m_cfg.account)) {
        if (!m_cfg.risk.allowLiveTrading) {
            reasonCode = "RISK_LIVE_NOT_AUTHORIZED";
            detail = "live trading is not explicitly authorized";
            return false;
        }
        if (m_cfg.risk.liveKillSwitch) {
            reasonCode = "RISK_LIVE_KILL_SWITCH_ON";
            detail = "live kill switch is ON";
            return false;
        }
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

bool HeptaIBGatewayAdapter::ReserveResearchH1NativeIdPair(
    const std::string& exactPaperAccount,
    std::uint64_t expectedConnectionEpoch,
    const OmsJournal& exactOwnerJournal,
    const std::function<bool(long,long)>& durablePair,
    long& parentId, long& stopId, std::string& reason,
    int expectedExactBrokerClientId)
{
    parentId=-1;stopId=-1;
    std::lock_guard<std::recursive_mutex> lock(m_apiMutex);
    if (!m_api || !m_connected || !m_api->IsConnected() ||
        m_terminalTransportHalted || expectedConnectionEpoch==0 ||
        expectedConnectionEpoch!=m_connectionEpoch ||
        exactPaperAccount!=m_cfg.account || !IsPaperAccount(m_cfg.account) ||
        (expectedExactBrokerClientId != -1 &&
         (expectedExactBrokerClientId <= 0 ||
          expectedExactBrokerClientId != m_cfg.clientId)) ||
        !durablePair) {
        reason="H1_NATIVE_ADAPTER_OWNER_OR_EPOCH_UNAVAILABLE";
        return false;
    }
    long brokerNext=-1;
    try {
        brokerNext=m_api->GetLastValidOrderId();
    } catch (...) {
        reason="H1_NATIVE_BROKER_NEXT_VALID_ID_EXCEPTION";
        return false;
    }
    if (brokerNext<=0 || m_localOrderSeed<=0 ||
        brokerNext>=std::numeric_limits<int>::max()-2 ||
        m_localOrderSeed>=std::numeric_limits<int>::max()-2) {
        reason="H1_NATIVE_BROKER_NEXT_VALID_ID_UNSAFE";
        return false;
    }
    // The same API mutex protects normal NextOrderId/PlaceOrder. Before
    // leasing a new H1 group, the caller's original owner OMS must replay
    // completely, contain only paired identities, and prove that the fresh
    // broker nextValidId is beyond EVERY native H1 ID ever reserved.
    // Any POSSIBLY_SENT original child remains UNKNOWN even with a higher ID,
    // until an independent complete broker order/execution reconciliation.
    const auto history=hepta_h1_restart_floor::Inspect(exactOwnerJournal);
    if (!hepta_h1_restart_floor::AuthorizeResearchIdFloor(
            history,brokerNext,reason))
        return false;
    const long first=std::max(brokerNext,m_localOrderSeed);
    if (first>=std::numeric_limits<int>::max()-2) {
        reason="H1_NATIVE_BROKER_ID_PAIR_OVERFLOW";
        return false;
    }
    // Hold the same adapter API lock as every normal PlaceOrder path.
    // Pre-consume both IDs even on a failed/throwing durable callback:
    // ambiguous OMS/connection outcome can never reuse them in this process.
    m_localOrderSeed=first+2;
    try {
        if (!durablePair(first,first+1)) {
            reason="H1_NATIVE_PAIR_DURABLE_COMMIT_UNKNOWN";
            return false;
        }
    } catch (...) {
        reason="H1_NATIVE_PAIR_DURABLE_COMMIT_EXCEPTION_UNKNOWN";
        return false;
    }
    parentId=first;stopId=first+1;
    reason.clear();
    return true;
}

long HeptaIBGatewayAdapter::NextOrderId() {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    const long ibOrderId = GetLastValidOrderId();
    const long brokerOrderIdMax =
        static_cast<long>(std::numeric_limits<std::int32_t>::max());
    // TWS OrderId is int32 even when this Linux host's long is 64-bit.
    // Never downcast a newer broker floor or a depleted local seed into a
    // wrapped/negative native order ID. Other production risk gates remain.
    if (ibOrderId > brokerOrderIdMax) return -1;
    if (ibOrderId > 0 && m_localOrderSeed < ibOrderId)
        m_localOrderSeed = ibOrderId;
    if (m_localOrderSeed < 1 || m_localOrderSeed > brokerOrderIdMax)
        return -1;
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
    oss.exceptions(std::ios::badbit | std::ios::failbit);
    oss << ContractDuplicateKey(c) << "|"
        << o.action << "|" << o.orderType << "|" << std::fixed << std::setprecision(8)
        << o.totalQuantity;
    if (o.cashQuantity != 0.0) oss << "|cash=" << o.cashQuantity;
    std::string sigNoPrice = oss.str();

    double lastPrice = 0.0;
    std::string lastNoPrice;
    std::size_t pos = m_lastOrderSig.rfind('|');
    if (pos != std::string::npos) {
        lastNoPrice = m_lastOrderSig.substr(0, pos);
        lastPrice = std::atof(m_lastOrderSig.substr(pos + 1).c_str());
    }

    if (lastNoPrice != sigNoPrice) return false;
    if (nowTs - m_lastOrderTs > m_cfg.risk.duplicateOrderWindowSec) return false;

    double tol = m_cfg.risk.duplicatePriceTolerance;
    return std::abs(lastPrice - o.lmtPrice) <= tol;
}

void HeptaIBGatewayAdapter::RememberLastOrder(const IBContractLite& c, const IBOrderLite& o, std::time_t nowTs) {
    std::ostringstream oss;
    oss.exceptions(std::ios::badbit | std::ios::failbit);
    oss << ContractDuplicateKey(c) << "|"
        << o.action << "|" << o.orderType << "|" << std::fixed << std::setprecision(8)
        << o.totalQuantity;
    if (o.cashQuantity != 0.0) oss << "|cash=" << o.cashQuantity;
    oss << "|" << o.lmtPrice;
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
    std::transform(up.begin(), up.end(), up.begin(), [](unsigned char c) { return static_cast<char>(std::toupper(c)); });
    return up.rfind("DU", 0) == 0;
}

bool HeptaIBGatewayAdapter::IsAccountWhitelisted(const std::string& account) const {
    if (account.empty()) return false;
    if (m_cfg.risk.accountWhitelist.empty()) return false;

    std::string acc = account;
    std::transform(acc.begin(), acc.end(), acc.begin(), [](unsigned char c) { return static_cast<char>(std::toupper(c)); });

    for (const auto& rawRule : m_cfg.risk.accountWhitelist) {
        if (rawRule.empty()) continue;

        std::string rule = rawRule;
        std::transform(rule.begin(), rule.end(), rule.begin(), [](unsigned char c) { return static_cast<char>(std::toupper(c)); });

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
    } else if (m_cfg.authoritativeCryptoContracts.count(instrument)) {
        std::string reason;
        if (!ResolveAuthoritativePositionQuantity(instrument, m_cfg.authoritativeCryptoContracts.at(instrument),
                positionQuantity, reason) || positionQuantity != 0.0)
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
