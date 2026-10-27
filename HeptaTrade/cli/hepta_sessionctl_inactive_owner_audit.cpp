#include "hepta_sessionctl_inactive_owner_audit.h"
#include "../execution/unix_execution_service_client.h"
#include "../tool_host/session_supervisor_lease_store.h"
#include "../tool_host/typed_tool_protocol.h"

#include <openssl/evp.h>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <fcntl.h>
#include <iostream>
#include <limits>
#include <locale>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

namespace {
using Fields = std::map<std::string, std::string>;

std::string Quoted(const std::string& value)
{
    static const char hex[] = "0123456789abcdef";
    std::string result = "\"";
    for (unsigned char c : value) {
        if (c == '\\' || c == '"') { result += '\\'; result += c; }
        else if (c < 0x20) {
            result += "\\u00"; result += hex[c >> 4]; result += hex[c & 15];
        } else result += c;
    }
    return result + '"';
}

bool Unsigned(const std::string& value, std::uint64_t& result)
{
    result = 0;
    if (value.empty()) return false;
    for (char digit : value) {
        if (digit < '0' || digit > '9' || result >
            (std::numeric_limits<std::uint64_t>::max() - (digit - '0')) / 10)
            return false;
        result = result * 10 + (digit - '0');
    }
    return true;
}

std::string Env(const char* key)
{
    const char* value = std::getenv(key);
    return value ? value : "";
}

bool Canonical(const std::string& value, std::size_t maximum)
{
    if (value.empty() || value.size() > maximum) return false;
    for (char c : value)
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
              (c >= '0' && c <= '9') || c == '.' || c == '_' || c == '-'))
            return false;
    return true;
}

const std::string& Value(const Fields& fields, const std::string& key)
{
    static const std::string absent;
    const auto found = fields.find(key);
    return found == fields.end() ? absent : found->second;
}

bool Equal(const Fields& fields, const char* key, const std::string& expected,
           std::string& reason)
{
    if (Value(fields, key) == expected) return true;
    reason = std::string("INACTIVE_OWNER_FIELD_INVALID:") + key;
    return false;
}

bool Boolean(const Fields& fields, const char* key, std::string& reason)
{
    if (Value(fields, key) == "true" || Value(fields, key) == "false") return true;
    reason = std::string("INACTIVE_OWNER_FIELD_INVALID:") + key;
    return false;
}

bool Number(const Fields& fields, const char* key, std::uint64_t& number,
            bool positive, std::string& reason)
{
    if (Unsigned(Value(fields, key), number) && (!positive || number != 0)) return true;
    reason = std::string("INACTIVE_OWNER_FIELD_INVALID:") + key;
    return false;
}

bool EmptyArray(const Fields& fields, const char* key, std::string& reason)
{
    std::string compact;
    for (char c : Value(fields, key))
        if (c != ' ' && c != '\t' && c != '\r' && c != '\n') compact += c;
    if (compact == "[]") return true;
    reason = std::string("INACTIVE_OWNER_NONEMPTY_OR_MISSING:") + key;
    return false;
}

bool ZeroNumber(const std::string& value)
{
    // The enclosing JSON parser has already validated its grammar. Do not
    // convert to double: a tiny nonzero decimal must never underflow to zero.
    if (value.empty() || value[0] == '"') return false;
    bool digit = false;
    for (char c : value) {
        if (c == 'e' || c == 'E') break;
        if (c == '0') digit = true;
        else if (c != '-' && c != '.') return false;
    }
    return digit;
}

bool FiniteNumber(const std::string& value)
{
    if (value.empty() || value[0] == '"') return false;
    std::istringstream input(value); input.imbue(std::locale::classic());
    double number;
    return (input >> number) && input.peek() == std::char_traits<char>::eof() &&
        std::isfinite(number);
}

bool OrderIds(const std::string& value)
{
    std::string compact;
    for (char c : value)
        if (c != ' ' && c != '\t' && c != '\r' && c != '\n') compact += c;
    if (compact.size() < 2 || compact.front() != '[' || compact.back() != ']') return false;
    if (compact == "[]") return true;
    std::set<std::uint64_t> ids;
    std::size_t start = 1;
    while (start < compact.size() - 1) {
        const std::size_t comma = compact.find(',', start);
        const std::size_t end = comma == std::string::npos ? compact.size() - 1 : comma;
        std::uint64_t id;
        if (!Unsigned(compact.substr(start, end - start), id) || id == 0 ||
            id > static_cast<std::uint64_t>(std::numeric_limits<long>::max()) ||
            !ids.insert(id).second) return false;
        if (comma == std::string::npos) return true;
        start = comma + 1;
    }
    return false;
}

bool SameFile(const struct stat& a, const struct stat& b)
{
    return a.st_dev == b.st_dev && a.st_ino == b.st_ino &&
        a.st_uid == b.st_uid && a.st_gid == b.st_gid &&
        a.st_mode == b.st_mode && a.st_nlink == b.st_nlink && a.st_size == b.st_size &&
        a.st_mtim.tv_sec == b.st_mtim.tv_sec && a.st_mtim.tv_nsec == b.st_mtim.tv_nsec &&
        a.st_ctim.tv_sec == b.st_ctim.tv_sec && a.st_ctim.tv_nsec == b.st_ctim.tv_nsec;
}

struct CatalogSnapshot { struct stat metadata{}; std::string sha256; };

bool HashCatalog(const std::string& path, std::uint32_t uid,
                 CatalogSnapshot& result, std::string& reason)
{
    const int fd = ::open(path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
    if (fd < 0) { reason = "INACTIVE_OWNER_CATALOG_OPEN_FAILED"; return false; }
    struct stat before{}, after{}, named{};
    bool ok = ::fstat(fd, &before) == 0 && S_ISREG(before.st_mode) &&
        before.st_uid == uid && (before.st_mode & 07777) == 0600 &&
        before.st_nlink == 1 && before.st_size > 0 && before.st_size <= 64 * 1024 * 1024;
    EVP_MD_CTX* digest = ok ? EVP_MD_CTX_new() : nullptr;
    ok = ok && digest && EVP_DigestInit_ex(digest, EVP_sha256(), nullptr) == 1;
    char buffer[16384]; std::uint64_t bytes = 0;
    while (ok) {
        const ssize_t count = ::read(fd, buffer, sizeof(buffer));
        if (count < 0 && errno == EINTR) continue;
        if (count < 0) { ok = false; break; }
        if (count == 0) break;
        bytes += static_cast<std::uint64_t>(count);
        ok = bytes <= 64 * 1024 * 1024 &&
            EVP_DigestUpdate(digest, buffer, static_cast<std::size_t>(count)) == 1;
    }
    unsigned char hash[EVP_MAX_MD_SIZE]; unsigned int length = 0;
    ok = ok && bytes == static_cast<std::uint64_t>(before.st_size) &&
        ::fstat(fd, &after) == 0 && ::lstat(path.c_str(), &named) == 0 &&
        SameFile(before, after) && SameFile(before, named) &&
        EVP_DigestFinal_ex(digest, hash, &length) == 1 && length == 32;
    EVP_MD_CTX_free(digest); ::close(fd);
    if (!ok) { reason = "INACTIVE_OWNER_CATALOG_UNSTABLE_OR_UNSAFE"; return false; }
    static const char hex[] = "0123456789abcdef";
    result.metadata = before; result.sha256.clear();
    for (unsigned int i = 0; i != length; ++i) {
        result.sha256 += hex[hash[i] >> 4]; result.sha256 += hex[hash[i] & 15];
    }
    return true;
}

bool NoOwnerRecords(SessionSupervisorLeaseStore& store, const AgentExecutionContext& owner,
                    std::string& reason)
{
    // List includes expired, recovery, finalized and predecessor-carrying
    // records. A scope disagreement is still a record for this owner, never
    // an excuse to filter it away.
    for (const auto& record : store.List())
        if (record.agentId == owner.agentId && record.sessionId == owner.sessionId) {
            reason = "INACTIVE_OWNER_CATALOG_RECORD_PRESENT"; return false;
        }
    return true;
}

bool LoadCatalog(SessionSupervisorLeaseStore& store, const AgentOsRuntimeConfig& config,
                 const AgentExecutionContext& owner, const CatalogSnapshot& expected,
                 CatalogSnapshot& observed, std::string& reason)
{
    if (!store.LoadExisting(config.supervisorLeaseStorePath,
            config.supervisorLeaseKeyPath, config.supervisorLeaseCleanupLockPath,
            config.supervisorLeaseCleanupLockUid, config.supervisorLeaseCleanupLockGid, reason) ||
        !NoOwnerRecords(store, owner, reason) ||
        !HashCatalog(config.supervisorLeaseStorePath, ::geteuid(), observed, reason)) return false;
    if (expected.sha256 != observed.sha256 || !SameFile(expected.metadata, observed.metadata)) {
        reason = "INACTIVE_OWNER_CATALOG_CHANGED"; return false;
    }
    return true;
}

bool Health(const Fields& fields, bool held, std::string& reason)
{
    if (!Equal(fields, "source", "\"IB\"", reason)) return false;
    const char* requiredTrue[] = {"authoritative", "owner_fenced", "terminal_download_complete",
        "account_broker_sample_connected", "account_broker_sample_event_stream_authoritative",
        "account_local_drain_observation_present"};
    for (const char* field : requiredTrue)
        if (!Equal(fields, field, "true", reason)) return false;
    const char* requiredFalse[] = {"startup_recovery_read_only", "mutation_blocked",
        "account_new_entry_fenced", "mutation_ready"};
    for (const char* field : requiredFalse)
        if (!Equal(fields, field, "false", reason)) return false;
    const char* requiredZero[] = {"account_local_uncertain_commands",
        "account_local_unattributed_owned_orders", "account_local_unattributed_uncertain_commands"};
    for (const char* field : requiredZero)
        if (!Equal(fields, field, "0", reason)) return false;
    if (!Equal(fields, "authorized_connector_count", "1", reason) ||
        !Equal(fields, "native_new_risk_guard_blocked", held ? "true" : "false", reason) ||
        !Equal(fields, "mutation_reason", "\"SESSION_OWNER_FENCED\"", reason) ||
        !Boolean(fields, "owner_recovery_only", reason) ||
        !Boolean(fields, "risk_mutation_dispatch_in_flight", reason) ||
        !Boolean(fields, "account_local_hot_projection_quiescent", reason)) return false;
    if (held || Value(fields, "account_broker_sample_coherent") == "true") {
        if (!Equal(fields, "account_broker_sample_coherent", "true", reason) ||
            !Equal(fields, "account_broker_visible_permid_coverage_complete", "true", reason) ||
            !Equal(fields, "account_broker_foreign_active_order_count", "0", reason) ||
            !Equal(fields, "account_broker_visible_unbound_manual_orders", "0", reason)) return false;
    } else {
        // Every legitimate place invalidates the recovery barrier. The normal
        // orders projection and explicit configured-position observation below
        // establish their own complete evidence; do not pretend this separate
        // recovery diagnostic stayed true after FX resumed.
        if (!Equal(fields, "account_broker_sample_coherent", "false", reason) ||
            !Equal(fields, "account_broker_reason", "\"IB_ACCOUNT_BROKER_RECOVERY_BARRIER_INCOMPLETE\"", reason) ||
            !Equal(fields, "account_broker_global_active_order_count", "null", reason) ||
            !Equal(fields, "account_broker_foreign_active_order_count", "null", reason) ||
            !Equal(fields, "account_broker_visible_unbound_manual_orders", "null", reason)) return false;
    }
    const char* counts[] = {"account_local_owned_orders", "account_local_venue_dispatches_in_flight",
        "account_broker_global_active_order_count"};
    for (const char* field : counts) {
        if (!held && std::string(field) == "account_broker_global_active_order_count" &&
            Value(fields, field) == "null") continue;
        std::uint64_t value;
        if (!Number(fields, field, value, false, reason)) return false;
        if (held && value != 0) { reason = std::string("INACTIVE_OWNER_HELD_NONZERO:") + field; return false; }
    }
    if (held && !Equal(fields, "risk_mutation_dispatch_in_flight", "false", reason)) return false;
    return true;
}

bool Orders(const Fields& fields, const AgentExecutionContext& owner, bool held,
            std::uint64_t& connectionEpoch, std::string& reason)
{
    if (!Equal(fields, "source", "\"IB\"", reason)) return false;
    const char* required[] = {"authoritative", "global_active_orders_complete",
        "owner_projection_complete", "owned_active_order_ids_authoritative"};
    for (const char* field : required)
        if (!Equal(fields, field, "true", reason)) return false;
    std::uint64_t projectionEpoch, generation, projectionGeneration;
    if (!Number(fields, "active_orders_connection_epoch", connectionEpoch, true, reason) ||
        !Number(fields, "owner_projection_connection_epoch", projectionEpoch, true, reason) ||
        !Number(fields, "active_orders_generation", generation, true, reason) ||
        !Number(fields, "owner_projection_generation", projectionGeneration, true, reason)) return false;
    if (connectionEpoch != projectionEpoch || generation != projectionGeneration) {
        reason = "INACTIVE_OWNER_ORDER_PROJECTION_GENERATION_MISMATCH"; return false;
    }
    Fields scope;
    if (!TypedToolProtocol::DecodeJsonObjectFields(Value(fields, "owner_scope"), scope, reason) ||
        !Equal(scope, "agent_id", Quoted(owner.agentId), reason) ||
        !Equal(scope, "session_id", Quoted(owner.sessionId), reason) ||
        !Equal(scope, "account", Quoted(owner.account), reason) ||
        !Equal(scope, "execution_domain", Quoted(owner.executionDomain), reason) ||
        !EmptyArray(fields, "owned_active_order_ids", reason) ||
        !EmptyArray(fields, "unmapped_active_order_ids", reason)) return false;
    if (held && !EmptyArray(fields, "active_order_ids", reason)) return false;
    if (!held) {
        const std::string& active = Value(fields, "active_order_ids");
        if (!OrderIds(active)) {
            reason = "INACTIVE_OWNER_GLOBAL_ORDERS_INVALID"; return false;
        }
    }
    return true;
}

bool Positions(const Fields& fields, const AgentExecutionContext& owner,
               const ExecutionServiceIdentity& identity, std::uint64_t connectionEpoch,
               std::string& reason, const ExecutionOwnerAuditResult* upgradeAudit = nullptr)
{
    if (!Equal(fields, "source", "\"IB\"", reason) ||
        !Equal(fields, "authoritative", "true", reason) ||
        !Equal(fields, "position_scope", "\"PAPER_BASELINE_DELTA\"", reason)) return false;
    std::vector<Fields> portfolio;
    if (!TypedToolProtocol::DecodeJsonObjectArray(Value(fields, "positions"), portfolio, reason)) return false;
    std::set<std::string> instruments;
    for (const auto& row : portfolio) {
        const std::string& instrument = Value(row, "instrument");
        if ((instrument != "\"BTC.USD\"" && instrument != "\"EUR.USD\"") ||
            !instruments.insert(instrument).second || !FiniteNumber(Value(row, "quantity")) ||
            (instrument == "\"BTC.USD\"" && !ZeroNumber(Value(row, "quantity")))) {
            reason = "INACTIVE_OWNER_PORTFOLIO_INVALID_OR_CRYPTO_NONZERO"; return false;
        }
    }
    if (upgradeAudit) {
        // Only the separate maintenance audit may bootstrap an older runtime.
        // The current op15 result, not an omitted portfolio row, proves the
        // configured physical crypto position is zero. Keep the ordinary
        // portfolio in that same position generation and reject contradictory
        // or incomplete explicit evidence when the new field is present.
        std::uint64_t positionGeneration;
        if (!Number(fields, "position_generation", positionGeneration, true, reason)) return false;
        if (positionGeneration != upgradeAudit->brokerPositionGeneration) {
            reason = "INACTIVE_OWNER_UPGRADE_POSITION_GENERATION_CHANGED"; return false;
        }
        if (fields.find("configured_crypto_position_observation") == fields.end()) return true;
    }
    Fields observation;
    if (!TypedToolProtocol::DecodeJsonObjectFields(
            Value(fields, "configured_crypto_position_observation"), observation, reason)) {
        reason = "INACTIVE_OWNER_EXPLICIT_CRYPTO_POSITION_REQUIRED"; return false;
    }
    if (!Equal(observation, "source", "\"IB_REQ_POSITIONS\"", reason) ||
        !Equal(observation, "authoritative", "true", reason) ||
        !Equal(observation, "scope", "\"CONFIGURED_PHYSICAL_CRYPTO\"", reason) ||
        !Equal(observation, "mutation_authorized", "false", reason) ||
        !Equal(observation, "account", Quoted(owner.account), reason) ||
        !Equal(observation, "execution_service_epoch", Quoted(identity.serviceEpoch), reason)) return false;
    std::uint64_t epoch, positionGeneration, outerPositionGeneration, activeGeneration, terminalGeneration, sampledAt;
    if (!Number(observation, "connection_epoch", epoch, true, reason) ||
        !Number(observation, "position_generation", positionGeneration, true, reason) ||
        !Number(fields, "position_generation", outerPositionGeneration, true, reason) ||
        !Number(observation, "active_orders_generation", activeGeneration, true, reason) ||
        !Number(observation, "terminal_generation", terminalGeneration, true, reason) ||
        !Number(observation, "sampled_at_ms", sampledAt, true, reason)) return false;
    if (epoch != connectionEpoch || positionGeneration != outerPositionGeneration) {
        reason = "INACTIVE_OWNER_POSITION_GENERATION_MISMATCH"; return false;
    }
    std::vector<Fields> positions;
    if (!TypedToolProtocol::DecodeJsonObjectArray(Value(observation, "positions"), positions, reason)) return false;
    // The validated USER PAPER profile has exactly one crypto contract. An
    // omitted, duplicated, aliased or unrelated row is not a zero observation.
    if (positions.size() != 1 || Value(positions[0], "instrument") != "\"BTC.USD\"" ||
        !ZeroNumber(Value(positions[0], "quantity"))) {
        reason = "INACTIVE_OWNER_CRYPTO_POSITION_NOT_EXPLICIT_ZERO"; return false;
    }
    return true;
}

bool Audit(const ExecutionOwnerAuditResult& result, const AgentExecutionContext& owner,
           std::string& reason)
{
    if (result.status != ExecutionCommandStatus::Accepted) {
        reason = result.reasonCode.empty() ? "INACTIVE_OWNER_NATIVE_AUDIT_REJECTED" : result.reasonCode;
        return false;
    }
    if (result.reasonCode != "SESSION_CRYPTO_OWNER_ZERO_CONFIRMED" ||
        result.ownerAccount != owner.account || result.ownerExecutionDomain != owner.executionDomain ||
        !result.ownerAuditAuthoritative || !result.ownerAuditComplete || result.mutationBlocked ||
        result.affectedCount != 0 || result.ownerActiveOrderCount != 0 ||
        result.ownerUncertainCommandCount != 0 || result.brokerGlobalActiveOrderCount != 0 ||
        result.brokerPostFillRiskReconciliationPending || !result.brokerRecoveryAuditBarrierComplete ||
        result.brokerRecoveryAuditNewConnectionEpochRequired || result.brokerConnectionEpoch == 0 ||
        result.brokerActiveGeneration == 0 || result.brokerTerminalGeneration == 0 ||
        result.brokerRiskGeneration == 0 || result.brokerAccountGeneration == 0 ||
        result.brokerPositionGeneration == 0 || result.brokerFxCashGeneration == 0 ||
        result.brokerExposureGeneration != result.brokerTerminalExposureGeneration ||
        result.brokerExposureGeneration != result.brokerRiskAbsorbedExposureGeneration ||
        result.brokerPositionQuantity.empty() || result.brokerGrossAbsolutePosition.empty()) {
        reason = "INACTIVE_OWNER_NATIVE_AUDIT_INCOMPLETE_OR_NONZERO"; return false;
    }
    return true;
}

void PrintAudit(const ExecutionOwnerAuditResult& r)
{
    std::cout << "{\"native_status\":" << static_cast<int>(r.status)
        << ",\"reason_code\":" << Quoted(r.reasonCode)
        << ",\"owner_account\":" << Quoted(r.ownerAccount)
        << ",\"owner_execution_domain\":" << Quoted(r.ownerExecutionDomain)
        << ",\"owner_audit_authoritative\":" << r.ownerAuditAuthoritative
        << ",\"owner_audit_complete\":" << r.ownerAuditComplete
        << ",\"mutation_blocked\":" << r.mutationBlocked
        << ",\"affected_count\":" << r.affectedCount
        << ",\"owner_active_order_count\":" << r.ownerActiveOrderCount
        << ",\"owner_uncertain_command_count\":" << r.ownerUncertainCommandCount
        << ",\"broker_connection_epoch\":" << r.brokerConnectionEpoch
        << ",\"broker_active_generation\":" << r.brokerActiveGeneration
        << ",\"broker_terminal_generation\":" << r.brokerTerminalGeneration
        << ",\"broker_risk_generation\":" << r.brokerRiskGeneration
        << ",\"broker_account_generation\":" << r.brokerAccountGeneration
        << ",\"broker_position_generation\":" << r.brokerPositionGeneration
        << ",\"broker_fx_cash_generation\":" << r.brokerFxCashGeneration
        << ",\"broker_exposure_generation\":" << r.brokerExposureGeneration
        << ",\"broker_terminal_exposure_generation\":" << r.brokerTerminalExposureGeneration
        << ",\"broker_risk_absorbed_exposure_generation\":" << r.brokerRiskAbsorbedExposureGeneration
        << ",\"broker_global_active_order_count\":" << r.brokerGlobalActiveOrderCount
        << ",\"broker_post_fill_risk_reconciliation_pending\":" << r.brokerPostFillRiskReconciliationPending
        << ",\"broker_recovery_audit_barrier_complete\":" << r.brokerRecoveryAuditBarrierComplete
        << ",\"broker_recovery_audit_new_connection_epoch_required\":" << r.brokerRecoveryAuditNewConnectionEpochRequired
        << ",\"broker_position_quantity\":" << Quoted(r.brokerPositionQuantity)
        << ",\"broker_gross_absolute_position\":" << Quoted(r.brokerGrossAbsolutePosition) << '}';
}
} // namespace

int HeptaSessionCtlInactiveOwnerAudit::Run(int argc, char** argv,
    const AgentOsRuntimeConfig& osConfig, const ExecutionGatewayRuntimeConfig& gateway)
{
    std::string phase;
    const auto fail = [&](int code, const std::string& reason) {
        std::cout << "{\"schema\":" << Quoted(phase == "upgrade-held"
                      ? "hepta_inactive_owner_upgrade_audit_v1" : "hepta_inactive_owner_audit_v1")
                  << ",\"accepted\":false,"
                  << "\"phase\":" << Quoted(phase) << ",\"reason_code\":" << Quoted(reason) << "}\n";
        std::cerr << reason << '\n'; return code;
    };
    std::uint64_t pid = 0, startTicks = 0;
    Fields options;
    if (argc != 10) return fail(2, "INACTIVE_OWNER_AUDIT_USAGE");
    for (int i = 2; i < argc; i += 2) {
        const std::string key = argv[i];
        if ((key != "--owner" && key != "--phase" && key != "--execution-pid" &&
             key != "--execution-start-ticks") || !options.emplace(key, argv[i + 1]).second)
            return fail(2, "INACTIVE_OWNER_AUDIT_USAGE");
    }
    if (Value(options, "--owner") != "crypto" ||
        !Unsigned(Value(options, "--execution-pid"), pid) || pid == 0 ||
        pid > static_cast<std::uint64_t>(std::numeric_limits<pid_t>::max()) ||
        !Unsigned(Value(options, "--execution-start-ticks"), startTicks) || startTicks == 0 ||
        (Value(options, "--phase") != "held" && Value(options, "--phase") != "released" &&
         Value(options, "--phase") != "upgrade-held"))
        return fail(2, "INACTIVE_OWNER_AUDIT_USAGE");
    phase = options.at("--phase");
    const bool upgradeHeld = phase == "upgrade-held";
    const bool held = phase == "held" || upgradeHeld;
    const std::uint32_t uid = ::geteuid(); std::string reason;
    if (!osConfig.Validate(reason) || !gateway.Validate(reason)) return fail(2, reason);
    if (uid == 0 || !osConfig.userPaperScope || osConfig.supervisorUid != uid ||
        osConfig.agentUid != uid || gateway.mode != ExecutionGatewayMode::Paper ||
        !gateway.executionServiceUidConfigured || gateway.executionServiceUid != uid)
        return fail(2, "INACTIVE_OWNER_USER_PAPER_SCOPE_REQUIRED");
    AgentExecutionContext owner;
    owner.agentId = Env("HEPTA_TOOL_AGENT_ID");
    owner.sessionId = Env("HEPTA_TOOL_CRYPTO_SESSION_ID");
    owner.account = Env("HEPTA_TOOL_ACCOUNT");
    owner.executionDomain = Env("HEPTA_EXECUTION_DOMAIN_ID");
    owner.venue = "IB"; owner.strategy = "agent-native";
    if (!Canonical(owner.agentId, 64) || !Canonical(owner.sessionId, 64) ||
        !Canonical(owner.account, 64) || owner.executionDomain != "PAPER" ||
        Env("HEPTA_TOOL_CONTRACT_BINDINGS") !=
            "BTC.USD|BTC|CRYPTO|PAXOS|USD;EUR.USD|EUR|CASH|IDEALPRO|USD")
        return fail(2, "INACTIVE_OWNER_CONFIGURED_CRYPTO_SCOPE_REQUIRED");

    CatalogSnapshot before, loaded, after;
    SessionSupervisorLeaseStore catalog;
    if (!HashCatalog(osConfig.supervisorLeaseStorePath, uid, before, reason) ||
        !LoadCatalog(catalog, osConfig, owner, before, loaded, reason)) return fail(2, reason);
    // This shared cleanup lock prevents cleanup races, not normal Supervisor
    // provision/renewal. Fresh catalog identity and contents are rechecked
    // below; the native owner fence remains the execution authority boundary.
    UnixExecutionServiceClient client(gateway.executionSocket, gateway.ioTimeoutMs,
        gateway.maxResponseBytes, {gateway.executionServiceUid}, gateway.responseTimeoutMs,
        static_cast<std::uint32_t>(pid), startTicks);
    ExecutionServiceIdentity identity;
    if (!client.GetServiceIdentity(identity, reason)) return fail(4, reason);
    const std::string callPrefix = "inactive-owner-" + std::to_string(::getpid()) + "-" +
        std::to_string(std::chrono::steady_clock::now().time_since_epoch().count());
    ExecutionOwnerAuditResult audit;
    if (held) {
        ExecutionControlCommand command; command.context = owner;
        command.context.toolCallId = callPrefix + "-audit";
        audit = client.AuditSessionOwnerWithIdentity(command, identity);
        if (!Audit(audit, owner, reason)) return fail(4, reason);
    }
    std::map<std::string, std::string> payloads;
    const char* labels[] = {"health_before", "orders", "positions", "health_after"};
    const char* queries[] = {"system.get_health", "orders.list", "portfolio.list_positions", "system.get_health"};
    std::uint64_t brokerEpoch = 0;
    for (std::size_t i = 0; i != 4; ++i) {
        ExecutionReadCommand command; command.context = owner;
        command.context.toolCallId = callPrefix + '-' + labels[i]; command.query = queries[i];
        const auto result = client.ReadAuthoritativeStateWithIdentity(command, identity);
        if (result.status != ExecutionCommandStatus::Accepted)
            return fail(4, result.reasonCode.empty() ? "INACTIVE_OWNER_NATIVE_READ_REJECTED" : result.reasonCode);
        Fields fields;
        if (!TypedToolProtocol::DecodeJsonObjectFields(result.detail, fields, reason)) return fail(4, reason);
        bool valid;
        if (i == 0 || i == 3) valid = Health(fields, held, reason);
        else if (i == 1) valid = Orders(fields, owner, held, brokerEpoch, reason);
        else valid = Positions(fields, owner, identity, brokerEpoch, reason, upgradeHeld ? &audit : nullptr);
        if (!valid) return fail(4, reason);
        payloads[labels[i]] = result.detail;
    }
    if (held && brokerEpoch != audit.brokerConnectionEpoch)
        return fail(4, "INACTIVE_OWNER_BROKER_EPOCH_CHANGED");
    SessionSupervisorLeaseStore finalCatalog;
    if (!LoadCatalog(finalCatalog, osConfig, owner, before, after, reason)) return fail(4, reason);
    std::cout << std::boolalpha
        << "{\"schema\":" << Quoted(upgradeHeld
            ? "hepta_inactive_owner_upgrade_audit_v1" : "hepta_inactive_owner_audit_v1")
        << ",\"accepted\":true,\"state\":" << Quoted(upgradeHeld
            ? "INACTIVE_FENCED_OWNER_UPGRADE_AUDIT" : "INACTIVE_FENCED_ZERO_NO_LEASE")
        << ",\"phase\":" << Quoted(phase)
        << ",\"configured_owner\":{\"agent_id\":" << Quoted(owner.agentId)
        << ",\"session_id\":" << Quoted(owner.sessionId) << ",\"account\":" << Quoted(owner.account)
        << ",\"execution_domain\":\"PAPER\",\"instrument\":\"BTC.USD\"}"
        << ",\"catalog\":{\"owner_records\":0,\"before_sha256\":" << Quoted(before.sha256)
        << ",\"after_sha256\":" << Quoted(after.sha256) << '}'
        << ",\"execution\":{\"pid\":" << pid << ",\"start_ticks\":" << Quoted(std::to_string(startTicks))
        << ",\"service_epoch\":" << Quoted(identity.serviceEpoch)
        << ",\"service_fencing_generation\":" << identity.serviceFencingGeneration << '}'
        << ",\"audit_performed\":" << held << ",\"audit\":";
    if (held) PrintAudit(audit); else std::cout << "null";
    std::cout << ",\"reads\":{";
    for (std::size_t i = 0; i != 4; ++i) {
        if (i) std::cout << ',';
        std::cout << Quoted(labels[i]) << ':' << payloads[labels[i]];
    }
    std::cout << "},\"control_mutations\":0,\"lease_mutations\":0,\"orders_sent\":0,"
                 "\"preserves_owner_fence\":true}\n";
    return 0;
}
