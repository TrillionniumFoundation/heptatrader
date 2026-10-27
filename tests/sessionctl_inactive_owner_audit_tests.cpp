#include "../HeptaTrade/cli/hepta_sessionctl_inactive_owner_audit.h"
#include "../HeptaTrade/execution/unix_execution_service_server.h"
#include "../HeptaTrade/execution/execution_coordinator.h"
#include "../HeptaTrade/tool_host/session_supervisor_lease_store.h"
#include "../HeptaTrade/tool_host/typed_tool_protocol.h"

#include <atomic>
#include <algorithm>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <functional>
#include <iostream>
#include <iterator>
#include <map>
#include <memory>
#include <poll.h>
#include <sstream>
#include <stdexcept>
#include <string>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>
#include <vector>

namespace {
using Fields = std::map<std::string, std::string>;
void Check(bool condition, const std::string& reason) {
    if (!condition) throw std::runtime_error(reason);
}
std::string Json(const Fields& fields) {
    std::string value = "{";
    for (const auto& field : fields) {
        if (value.size() != 1) value += ',';
        value += '"' + field.first + "\":" + field.second;
    }
    return value + '}';
}
Fields Decode(const std::string& json) {
    Fields fields; std::string reason;
    Check(TypedToolProtocol::DecodeJsonObjectFields(json, fields, reason), reason + ": " + json);
    return fields;
}
void Write(const std::string& path, const std::string& value, mode_t mode) {
    std::ofstream output(path.c_str(), std::ios::binary | std::ios::trunc);
    Check(output.good(), "fixture open"); output << value; output.close();
    Check(!output.fail() && ::chmod(path.c_str(), mode) == 0, "fixture write");
}
std::string Read(const std::string& path) {
    std::ifstream input(path.c_str(), std::ios::binary);
    Check(input.good(), "fixture read");
    return std::string(std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>());
}
std::string StartTicks(pid_t pid = ::getpid()) {
    const std::string stat = Read("/proc/" + std::to_string(pid) + "/stat");
    std::istringstream input(stat.substr(stat.rfind(')') + 1)); std::string field;
    for (int i = 0; i != 20; ++i) Check(bool(input >> field), "fixture proc stat");
    return field;
}
struct Environment {
    struct Saved { std::string key, value; bool present; };
    std::vector<Saved> saved;
    void Set(const std::string& key, const std::string& value) {
        const char* old = std::getenv(key.c_str());
        saved.push_back(Saved{key, old ? old : "", old != nullptr});
        Check(::setenv(key.c_str(), value.c_str(), 1) == 0, "fixture env");
    }
    ~Environment() {
        for (auto entry = saved.rbegin(); entry != saved.rend(); ++entry)
            if (entry->present) ::setenv(entry->key.c_str(), entry->value.c_str(), 1);
            else ::unsetenv(entry->key.c_str());
    }
};
struct Streams {
    std::ostringstream out, err;
    std::streambuf *savedOut, *savedErr; std::ios::fmtflags flags;
    Streams() : savedOut(std::cout.rdbuf(out.rdbuf())), savedErr(std::cerr.rdbuf(err.rdbuf())),
        flags(std::cout.flags()) {}
    ~Streams() { std::cout.rdbuf(savedOut); std::cerr.rdbuf(savedErr); std::cout.flags(flags); }
};
struct Outcome { int code; std::string out, err; };

ExecutionOwnerAuditResult AcceptedAudit() {
    ExecutionOwnerAuditResult r;
    r.status = ExecutionCommandStatus::Accepted;
    r.reasonCode = "SESSION_CRYPTO_OWNER_ZERO_CONFIRMED";
    r.ownerAccount = "DU12345"; r.ownerExecutionDomain = "PAPER";
    r.ownerAuditAuthoritative = r.ownerAuditComplete = true;
    r.brokerConnectionEpoch = 7; r.brokerActiveGeneration = 8; r.brokerTerminalGeneration = 9;
    r.brokerRiskGeneration = 10; r.brokerAccountGeneration = 11;
    r.brokerPositionGeneration = 12; r.brokerFxCashGeneration = 13;
    r.brokerExposureGeneration = r.brokerTerminalExposureGeneration = r.brokerRiskAbsorbedExposureGeneration = 14;
    r.brokerRecoveryAuditBarrierComplete = true;
    r.brokerPositionQuantity = r.brokerGrossAbsolutePosition = "0";
    return r;
}

class Authority : public ExecutionAuthority, public ExecutionControlAuthority, public ExecutionReadAuthority {
public:
    ExecutionOwnerAuditResult audit = AcceptedAudit();
    Fields health, orders, positions;
    std::atomic<int> audits{0}, reads{0}, mutations{0};
    std::vector<AgentExecutionContext> contexts;
    std::function<void(int)> onRead;
    std::string malformedHealth;
    ExecutionCommandResult Unexpected(const AgentExecutionContext& c) {
        ++mutations; ExecutionCommandResult result; result.commandId = c.toolCallId; return result;
    }
    ExecutionControlResult UnexpectedControl(const ExecutionControlCommand& c) {
        ++mutations; ExecutionControlResult result; result.commandId = c.context.toolCallId; return result;
    }
    ExecutionOwnerAuditResult AuditSessionOwner(const ExecutionControlCommand& c) override {
        ++audits; contexts.push_back(c.context);
        Check(c.recoveryIngressFence == 0, "audit supplied a recovery ingress fence");
        auto result = audit; result.commandId = c.context.toolCallId; return result;
    }
    ExecutionCommandResult ReadAuthoritativeState(const ExecutionReadCommand& c) override {
        contexts.push_back(c.context); const int index = ++reads;
        ExecutionCommandResult result; result.commandId = c.context.toolCallId;
        result.status = ExecutionCommandStatus::Accepted;
        if (c.query == "system.get_health") result.detail = malformedHealth.empty() ? Json(health) : malformedHealth;
        else if (c.query == "orders.list") result.detail = Json(orders);
        else if (c.query == "portfolio.list_positions") result.detail = Json(positions);
        else return Unexpected(c.context);
        if (onRead) onRead(index);
        return result;
    }
    ExecutionCommandResult PlaceOrder(const PlaceOrderCommand& c) override { return Unexpected(c.context); }
    ExecutionCommandResult CancelOrder(const CancelOrderCommand& c) override { return Unexpected(c.context); }
    ExecutionCommandResult FlattenPosition(const FlattenPositionCommand& c) override { return Unexpected(c.context); }
    ExecutionCommandResult PreviewOrder(const PlaceOrderCommand& c) override { return Unexpected(c.context); }
    ExecutionControlStatusResult QueryCommandStatus(const ExecutionControlCommand& c) override { return UnexpectedControl(c); }
    ExecutionControlStatusResult FenceAccountNewEntries(const ExecutionControlCommand& c) override { return UnexpectedControl(c); }
    ExecutionControlStatusResult FenceSessionOwner(const ExecutionControlCommand& c) override { return UnexpectedControl(c); }
    ExecutionControlStatusResult ReleaseSessionOwnerFence(const ExecutionControlCommand& c) override { return UnexpectedControl(c); }
    ExecutionControlStatusResult ReconcileAuthoritativeState(const ExecutionControlCommand& c) override { return UnexpectedControl(c); }
    ExecutionOwnerAuditResult AuditSessionLease(const ExecutionControlCommand& c) override { return UnexpectedControl(c); }
    ExecutionOwnerAuditResult RecoveryAuditOwner(const ExecutionControlCommand& c) override { return UnexpectedControl(c); }
    ExecutionTerminalResult TerminalizeRecoveryOwner(const ExecutionControlCommand& c) override {
        return NarrowTerminalResult(UnexpectedControl(c));
    }
};

struct Fixture {
    Authority authority;
    UnixExecutionServiceServer server, replacement;
    std::string dir, socket, key, store, token, driver, lockDir, lock;
    const std::string bearer = "inactive-owner-old-token-do-not-revive-0123456789";
    AgentOsRuntimeConfig os; ExecutionGatewayRuntimeConfig gateway;
    SessionSupervisorLeaseRecord fx;
    Fixture() : server(authority, &authority), replacement(authority, &authority) {
        char pattern[] = "/tmp/hepta-inactive-owner-XXXXXX";
        Check(::mkdtemp(pattern) != nullptr, "fixture tempdir"); dir = pattern;
        socket = dir + "/execution.sock"; key = dir + "/key"; store = dir + "/leases";
        token = dir + "/crypto.token"; driver = dir + "/driver.json";
        lockDir = dir + "/interlock"; lock = lockDir + "/cleanup.lock";
        Check(::mkdir(lockDir.c_str(), 0711) == 0 && ::chmod(lockDir.c_str(), 0711) == 0, "fixture lockdir");
        Write(key, std::string(32, 'K'), 0600); Write(lock, "", 0644);
        Write(token, bearer + '\n', 0600); Write(driver, "{\"lease_generation\":101,\"position\":0}", 0600);
        const auto uid = static_cast<std::uint32_t>(::geteuid());
        std::map<std::string, std::string> config = {
            {"HEPTA_IB_PAPER_USER_SCOPE", "1"}, {"HEPTA_EXECUTION_REMOTE_MODE", "PAPER"},
            {"HEPTA_TOOL_SUPERVISOR_LEASE_STORE", store}, {"HEPTA_TOOL_SUPERVISOR_LEASE_KEY_FILE", key},
            {"HEPTA_TOOL_SUPERVISOR_UID", std::to_string(uid)}, {"HEPTA_TOOL_AGENT_UID", std::to_string(uid)},
            {"HEPTA_EXECUTION_SOCKET", socket}, {"HEPTA_EXECUTION_EVENT_SOCKET", dir + "/events.sock"},
            {"HEPTA_EXECUTION_SERVICE_UID", std::to_string(uid)}};
        os = AgentOsRuntimeConfig::FromValues(config, ::getpid(), uid); os.supervisorLeaseCleanupLockPath = lock;
        gateway = ExecutionGatewayRuntimeConfig::FromValues(config);
        std::string reason; Check(os.Validate(reason) && gateway.Validate(reason), reason);
        fx.templateId = "paper"; fx.issuer = "hepta.os.bootstrap";
        fx.token = "active-fx-fixture-token-0123456789abcdef";
        fx.agentId = "test-agent"; fx.sessionId = "configured-fx";
        fx.ownerAccount = "DU12345"; fx.ownerExecutionDomain = "PAPER"; fx.peerUid = uid;
        fx.expiresAtMs = 1; fx.leaseGeneration = 118;
        { SessionSupervisorLeaseStore catalog;
          Check(catalog.Init(store, key, lock, uid, ::getegid(), reason), reason);
          Check(catalog.Put(fx, reason), reason); }
        Check(server.Start(socket, {uid}, reason, 32768, 1500), reason);
        authority.health = {{"source", "\"IB\""}, {"authoritative", "true"},
            {"owner_fenced", "true"}, {"owner_recovery_only", "true"},
            {"terminal_download_complete", "true"}, {"account_broker_sample_connected", "true"},
            {"account_broker_sample_event_stream_authoritative", "true"},
            {"account_broker_sample_coherent", "true"}, {"account_broker_visible_permid_coverage_complete", "true"},
            {"account_local_drain_observation_present", "true"}, {"startup_recovery_read_only", "false"},
            {"mutation_blocked", "false"}, {"account_new_entry_fenced", "false"}, {"mutation_ready", "false"},
            {"account_local_uncertain_commands", "0"}, {"account_local_unattributed_owned_orders", "0"},
            {"account_local_unattributed_uncertain_commands", "0"}, {"account_broker_foreign_active_order_count", "0"},
            {"account_broker_visible_unbound_manual_orders", "0"}, {"authorized_connector_count", "1"},
            {"native_new_risk_guard_blocked", "true"}, {"mutation_reason", "\"SESSION_OWNER_FENCED\""},
            {"risk_mutation_dispatch_in_flight", "false"}, {"account_local_hot_projection_quiescent", "false"},
            {"account_local_owned_orders", "0"}, {"account_local_venue_dispatches_in_flight", "0"},
            {"account_broker_global_active_order_count", "0"}, {"account_broker_zero_observed", "true"}};
        authority.orders = {{"source", "\"IB\""}, {"authoritative", "true"},
            {"global_active_orders_complete", "true"}, {"owner_projection_complete", "true"},
            {"owned_active_order_ids_authoritative", "true"}, {"active_orders_connection_epoch", "7"},
            {"owner_projection_connection_epoch", "7"}, {"active_orders_generation", "8"},
            {"owner_projection_generation", "8"}, {"active_order_ids", "[]"},
            {"owned_active_order_ids", "[]"}, {"unmapped_active_order_ids", "[]"},
            {"owner_scope", "{\"agent_id\":\"test-agent\",\"session_id\":\"configured-crypto\",\"account\":\"DU12345\",\"execution_domain\":\"PAPER\"}"}};
        authority.positions = {{"source", "\"IB\""}, {"authoritative", "true"},
            {"position_scope", "\"PAPER_BASELINE_DELTA\""}, {"position_generation", "12"}, {"positions", "[]"}};
        Fields explicitPosition = {{"source", "\"IB_REQ_POSITIONS\""}, {"authoritative", "true"},
            {"scope", "\"CONFIGURED_PHYSICAL_CRYPTO\""}, {"mutation_authorized", "false"},
            {"account", "\"DU12345\""}, {"execution_service_epoch", '"' + server.ServiceEpoch() + '"'},
            {"connection_epoch", "7"}, {"position_generation", "12"},
            {"active_orders_generation", "8"}, {"terminal_generation", "9"}, {"sampled_at_ms", "1"},
            {"positions", "[{\"instrument\":\"BTC.USD\",\"quantity\":0}]"}};
        authority.positions["configured_crypto_position_observation"] = Json(explicitPosition);
    }
    ~Fixture() {
        replacement.Stop(); server.Stop();
        for (const auto& path : {socket, socket + ".lock", key, store, token, driver, lock}) ::unlink(path.c_str());
        ::rmdir(lockDir.c_str()); ::rmdir(dir.c_str());
    }
    void Put(const SessionSupervisorLeaseRecord& row) {
        SessionSupervisorLeaseStore catalog; std::string reason;
        Check(catalog.LoadExisting(store, key, lock, ::geteuid(), ::getegid(), reason), reason);
        Check(catalog.Put(row, reason), reason);
    }
    void ChangeExplicit(const std::string& keyName, const std::string& raw) {
        auto fields = Decode(authority.positions.at("configured_crypto_position_observation"));
        fields[keyName] = raw; authority.positions["configured_crypto_position_observation"] = Json(fields);
    }
    void ReleasedFx() {
        authority.health["native_new_risk_guard_blocked"] = "false";
        authority.health["risk_mutation_dispatch_in_flight"] = "true";
        authority.health["account_local_owned_orders"] = "1";
        authority.health["account_local_venue_dispatches_in_flight"] = "1";
        authority.health["account_local_hot_projection_quiescent"] = "false";
        authority.health["account_broker_sample_coherent"] = "false";
        authority.health["account_broker_visible_permid_coverage_complete"] = "false";
        authority.health["account_broker_reason"] = "\"IB_ACCOUNT_BROKER_RECOVERY_BARRIER_INCOMPLETE\"";
        for (const auto& field : {"account_broker_global_active_order_count", "account_broker_foreign_active_order_count",
                                  "account_broker_visible_unbound_manual_orders"}) authority.health[field] = "null";
        authority.orders["active_order_ids"] = "[77]";
        authority.positions["positions"] = "[{\"instrument\":\"EUR.USD\",\"quantity\":123}]";
    }
    Outcome Call(const std::string& phase = "held", const std::string& scopeOverride = "",
                 const std::string& pidOverride = "", const std::string& ticksOverride = "",
                 bool catalogMayChange = false, bool phaseBeforePid = false) {
        const std::string oldStore = Read(store), oldToken = Read(token), oldKey = Read(key), oldDriver = Read(driver);
        Environment env;
        env.Set("HEPTA_TOOL_AGENT_ID", "test-agent");
        env.Set("HEPTA_TOOL_CRYPTO_SESSION_ID", "configured-crypto");
        env.Set("HEPTA_TOOL_ACCOUNT", "DU12345"); env.Set("HEPTA_EXECUTION_DOMAIN_ID", "PAPER");
        env.Set("HEPTA_TOOL_CONTRACT_BINDINGS", "BTC.USD|BTC|CRYPTO|PAXOS|USD;EUR.USD|EUR|CASH|IDEALPRO|USD");
        if (!scopeOverride.empty()) env.Set(scopeOverride, "invalid scope");
        std::vector<std::string> args = {"hepta-sessionctl", "inactive-owner-audit", "--owner", "crypto",
            "--execution-pid", pidOverride.empty() ? std::to_string(::getpid()) : pidOverride,
            "--execution-start-ticks", ticksOverride.empty() ? StartTicks() : ticksOverride, "--phase", phase};
        if (phaseBeforePid) {
            std::rotate(args.begin() + 4, args.end() - 2, args.end());
        }
        std::vector<char*> argv; for (auto& arg : args) argv.push_back(const_cast<char*>(arg.c_str()));
        Streams capture; Outcome result;
        result.code = HeptaSessionCtlInactiveOwnerAudit::Run(argv.size(), argv.data(), os, gateway);
        result.out = capture.out.str(); result.err = capture.err.str();
        Check(catalogMayChange || Read(store) == oldStore, "command changed lease store");
        Check(Read(token) == oldToken && Read(key) == oldKey && Read(driver) == oldDriver,
              "command changed old bearer, key or driver lifecycle");
        Check(result.out.find(bearer) == std::string::npos && result.err.find(bearer) == std::string::npos &&
              result.out.find(std::string(32, 'K')) == std::string::npos, "command leaked a credential");
        Check(authority.mutations == 0, "command invoked mutation/renew/resume/order authority");
        return result;
    }
};

void HeldAndReleasedPreserveAuthority() {
    Fixture f;
    // The native crypto audit allows an existing known FX position. It does
    // not describe the account's gross quantity as the crypto quantity.
    f.authority.audit.brokerPositionQuantity = f.authority.audit.brokerGrossAbsolutePosition = "123";
    f.authority.positions["positions"] = "[{\"instrument\":\"EUR.USD\",\"quantity\":123}]";
    f.authority.health["account_broker_zero_observed"] = "false";
    const auto held = f.Call();
    Check(held.code == 0, "held failed: " + held.err + held.out);
    const auto proof = Decode(held.out);
    Check(proof.at("state") == "\"INACTIVE_FENCED_ZERO_NO_LEASE\"" && proof.at("audit_performed") == "true", "held schema");
    Check(Decode(proof.at("catalog")).at("owner_records") == "0", "catalog evidence");
    Check(f.authority.audits == 1 && f.authority.reads == 4, "held operation set");
    for (const auto& c : f.authority.contexts)
        Check(c.agentId == "test-agent" && c.sessionId == "configured-crypto" && c.account == "DU12345" &&
              c.executionDomain == "PAPER" && c.venue == "IB", "native exact scope");
    f.ReleasedFx(); const auto released = f.Call("released", "", "", "", false, true);
    Check(released.code == 0, "known FX post-release rejected: " + released.err + released.out);
    const auto post = Decode(released.out);
    Check(post.at("audit_performed") == "false" && post.at("audit") == "null", "released invented a fresh op15 audit");
    Check(f.authority.audits == 1 && f.authority.reads == 8, "released reran the global audit");
}

void UpgradeHeldBootstrapsOnlyMaintenanceFromAnOldRuntime() {
    Fixture f;
    f.authority.positions.erase("configured_crypto_position_observation");
    // The op15 crypto-zero verdict does not assert an all-account flat FX
    // position. Preserve a known nonzero FX position through maintenance.
    f.authority.audit.brokerPositionQuantity = "-123";
    f.authority.audit.brokerGrossAbsolutePosition = "123";
    f.authority.positions["positions"] = "[{\"instrument\":\"EUR.USD\",\"quantity\":-123}]";
    f.authority.health["account_broker_zero_observed"] = "false";
    const auto maintenance = f.Call("upgrade-held", "", "", "", false, true);
    Check(maintenance.code == 0, "old-runtime upgrade audit failed: " + maintenance.err + maintenance.out);
    const auto proof = Decode(maintenance.out);
    Check(proof.at("schema") == "\"hepta_inactive_owner_upgrade_audit_v1\"" &&
          proof.at("state") == "\"INACTIVE_FENCED_OWNER_UPGRADE_AUDIT\"" &&
          proof.at("phase") == "\"upgrade-held\"" && proof.at("audit_performed") == "true",
          "maintenance proof aliases the native-release contract");
    Check(maintenance.out.find("INACTIVE_FENCED_ZERO_NO_LEASE") == std::string::npos &&
          f.authority.audits == 1 && f.authority.reads == 4, "maintenance proof skipped op15 or authorized release");
    const auto ordinaryHeld = f.Call("held");
    Check(ordinaryHeld.code == 4 && ordinaryHeld.out.find("EXPLICIT_CRYPTO_POSITION_REQUIRED") != std::string::npos,
          "maintenance bootstrap weakened ordinary held proof");
    Check(Decode(ordinaryHeld.out).at("schema") == "\"hepta_inactive_owner_audit_v1\"",
          "ordinary held schema changed");
    f.ReleasedFx();
    const auto released = f.Call("released");
    Check(released.code == 4 && released.out.find("EXPLICIT_CRYPTO_POSITION_REQUIRED") != std::string::npos,
          "old-runtime maintenance proof authorized native-hold release");
    Check(f.authority.audits == 2, "released proof performed an unexpected bootstrap audit");
}

void UpgradeHeldRequiresCompleteCurrentCryptoZeroAndOriginalHold() {
    for (int fault = 0; fault != 15; ++fault) {
        Fixture f; f.authority.positions.erase("configured_crypto_position_observation");
        if (fault == 0) {
            f.authority.audit.status = ExecutionCommandStatus::Rejected;
            f.authority.audit.reasonCode = "CRYPTO_POSITION_NONZERO";
        }
        if (fault == 1) f.authority.audit.ownerAuditAuthoritative = false;
        if (fault == 2) f.authority.audit.ownerAuditComplete = false;
        if (fault == 3) f.authority.audit.brokerGlobalActiveOrderCount = 1;
        if (fault == 4) f.authority.audit.ownerActiveOrderCount = 1;
        if (fault == 5) f.authority.audit.ownerUncertainCommandCount = 1;
        if (fault == 6) f.authority.audit.brokerRecoveryAuditBarrierComplete = false;
        if (fault == 7) f.authority.audit.mutationBlocked = true;
        if (fault == 8) f.authority.audit.reasonCode = "RECOVERY_OWNER_ZERO_CONFIRMED";
        if (fault == 9) f.authority.health["native_new_risk_guard_blocked"] = "false";
        if (fault == 10) f.authority.health["owner_fenced"] = "false";
        if (fault == 11) f.authority.positions["position_generation"] = "13";
        if (fault == 12) f.authority.orders["active_order_ids"] = "[77]";
        if (fault == 13) f.authority.health["risk_mutation_dispatch_in_flight"] = "true";
        if (fault == 14) f.authority.audit.brokerConnectionEpoch = 8;
        const auto rejected = f.Call("upgrade-held");
        Check(rejected.code == 4 && Decode(rejected.out).at("accepted") == "false" &&
              Decode(rejected.out).at("schema") == "\"hepta_inactive_owner_upgrade_audit_v1\"",
              "maintenance accepted incomplete crypto/hold evidence, fault " + std::to_string(fault));
    }
    Fixture existing;
    auto crypto = existing.fx; crypto.sessionId = "configured-crypto";
    crypto.token = "existing-recovery-crypto-token-0123456789"; crypto.recoveryOnly = true;
    existing.Put(crypto);
    Check(existing.Call("upgrade-held").code == 2 && existing.authority.audits == 0 &&
          existing.authority.reads == 0, "maintenance bypassed an existing crypto lease");
}

void UpgradeHeldNeverHidesContradictoryPositionEvidence() {
    for (const auto& rows : {"[{\"instrument\":\"BTC.USD\",\"quantity\":1}]",
         "[{\"instrument\":\"BTC.USD\",\"quantity\":1e-9999}]",
         "[{\"instrument\":\"BTC.USD\",\"quantity\":\"0\"}]",
         "[{\"instrument\":\"CONID:123\",\"quantity\":0}]"}) {
        Fixture f; f.authority.positions.erase("configured_crypto_position_observation");
        f.authority.positions["positions"] = rows;
        Check(f.Call("upgrade-held").code == 4, "maintenance ignored contradictory/unmapped portfolio");
    }
    for (const auto& change : std::vector<std::pair<std::string, std::string>>{
            {"authoritative", "false"}, {"positions", "[]"},
            {"positions", "[{\"instrument\":\"BTC.USD\",\"quantity\":1}]"},
            {"connection_epoch", "8"}}) {
        Fixture f; f.ChangeExplicit(change.first, change.second);
        Check(f.Call("upgrade-held").code == 4, "maintenance fell back after explicit evidence failed");
    }
    Fixture malformed;
    malformed.authority.positions["configured_crypto_position_observation"] = "null";
    Check(malformed.Call("upgrade-held").code == 4, "maintenance treated a null field as an old runtime");
}

void HeldUsesRealOwnerFenceWithoutInventingAnAccountFence() {
    Fixture f;
    Environment env; env.Set("HEPTA_OMS_ASYNC_FLUSH", "0"); env.Set("HEPTA_OMS_SYNC_CRITICAL", "1");
    const std::string journalPath = f.dir + "/coordinator.jsonl";
    {
        OmsJournal journal; Check(journal.Init(journalPath), "real coordinator journal");
        ExecutionCoordinator coordinator(journal, ExecutionCoordinatorCallbacks());
        AgentExecutionContext owner; owner.agentId = "test-agent"; owner.sessionId = "configured-crypto";
        owner.account = "DU12345"; owner.venue = "IB"; owner.executionDomain = "PAPER";
        Check(coordinator.FenceSessionOwner(owner.agentId, owner.sessionId) == 0, "real owner fence");
        const auto actual = coordinator.ObserveAccountLocalDrain(owner);
        Check(actual.present && actual.sessionOwnerFenced && !actual.accountEntryFenced &&
              !actual.localHotProjectionQuiescent && !actual.mutationBlocked &&
              actual.ownedOrders == 0 && actual.uncertainCommands == 0 &&
              actual.venueDispatchesInFlight == 0, "real held coordinator semantics");
        f.authority.health["owner_fenced"] = actual.sessionOwnerFenced ? "true" : "false";
        f.authority.health["account_new_entry_fenced"] = actual.accountEntryFenced ? "true" : "false";
        f.authority.health["account_local_hot_projection_quiescent"] = actual.localHotProjectionQuiescent ? "true" : "false";
        Check(f.Call().code == 0, "ordinary native hold incorrectly required the account-stop fence");
    }
    ::unlink(journalPath.c_str()); ::unlink((journalPath + ".lock").c_str());
}

void CatalogStatesRejectBeforeAnyNativeAuthority() {
    for (int state = 0; state != 5; ++state) {
        Fixture f; auto crypto = f.fx;
        crypto.token = "retained-crypto-fixture-token-0123456789";
        crypto.sessionId = "configured-crypto";
        if (state == 1) { crypto.fencePending = true; crypto.fenceReason = "session_expired"; }
        if (state == 2 || state == 3) crypto.recoveryOnly = true;
        if (state == 3) { crypto.predecessorToken = f.bearer; crypto.predecessorGeneration = 117; }
        if (state == 4) crypto.ownerAccount = "DU-CONFLICT";
        f.Put(crypto); const auto result = f.Call();
        Check(result.code == 2 && result.out.find("CATALOG_RECORD_PRESENT") != std::string::npos,
              "retained owner record was filtered away");
        Check(f.authority.audits == 0 && f.authority.reads == 0, "catalog failure called native authority");
    }
    for (const auto& key : {"HEPTA_TOOL_AGENT_ID", "HEPTA_TOOL_CRYPTO_SESSION_ID", "HEPTA_TOOL_ACCOUNT",
                            "HEPTA_EXECUTION_DOMAIN_ID", "HEPTA_TOOL_CONTRACT_BINDINGS"}) {
        Fixture f; Check(f.Call("held", key).code == 2, "invalid scope accepted");
        Check(f.authority.audits == 0 && f.authority.reads == 0, "invalid scope opened authority");
    }
    Fixture f; Check(f.Call("missing-phase").code == 2, "phase is not mandatory");
}

void CatalogRejectionDoesNotEvenDiscoverTheNativeService() {
    Fixture f; f.server.Stop();
    auto crypto = f.fx; crypto.sessionId = "configured-crypto";
    crypto.token = "preexisting-crypto-token-0123456789abcdef"; f.Put(crypto);
    const int fd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    Check(fd >= 0, "no-RPC probe socket"); sockaddr_un address{}; address.sun_family = AF_UNIX;
    std::memcpy(address.sun_path, f.socket.c_str(), f.socket.size() + 1);
    Check(::bind(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0 && ::listen(fd, 8) == 0,
          "no-RPC probe listener");
    const auto result = f.Call();
    pollfd probe{}; probe.fd = fd; probe.events = POLLIN;
    const bool noConnection = ::poll(&probe, 1, 0) == 0;
    ::close(fd); ::unlink(f.socket.c_str());
    Check(result.code == 2 && noConnection, "catalog refusal opened even a discovery connection");
}

void RealPeerPidAndStartTimeArePinned() {
    for (const auto& phase : {"held", "upgrade-held"}) {
        Fixture f;
        // Use a real, still-live wrong process with its actual start ticks.
        // A guessed getpid()+1 can be absent, which is a distinct preflight
        // failure and does not exercise the response-sender PID comparison.
        Check(f.Call(phase, "", std::to_string(::getppid()), StartTicks(::getppid()))
                  .out.find("PEER_PROCESS_MISMATCH") != std::string::npos,
              "different actual response sender pid accepted");
        Check(f.Call(phase, "", "", std::to_string(std::stoull(StartTicks()) + 1)).out.find("PEER_PROCESS_MISMATCH") != std::string::npos,
              "reused pid/start-time mismatch accepted");
        Check(f.authority.audits == 0 && f.authority.reads == 0, "mismatched peer received authority request");
    }
}

void ExplicitZeroIsNotAbsenceOrUnderflow() {
    for (const auto& wrong : {"[]", "[{\"instrument\":\"EUR.USD\",\"quantity\":0}]",
         "[{\"instrument\":\"BTC.USD\",\"quantity\":0.000000000000001}]",
         "[{\"instrument\":\"BTC.USD\",\"quantity\":1e-9999}]",
         "[{\"instrument\":\"BTC.USD\",\"quantity\":\"0\"}]",
         "[{\"instrument\":\"BTC.USD\",\"quantity\":0},{\"instrument\":\"BTC.USD\",\"quantity\":0}]"}) {
        Fixture f; f.ReleasedFx(); f.ChangeExplicit("positions", wrong);
        Check(f.Call("released").code == 4, "ambiguous/nonzero crypto observation accepted");
    }
    for (const auto& change : std::vector<std::pair<std::string, std::string>>{
             {"authoritative", "false"}, {"connection_epoch", "8"}, {"position_generation", "11"},
             {"account", "\"DU-OTHER\""}, {"execution_service_epoch", "\"wrong-instance\""},
             {"active_orders_generation", "0"}, {"mutation_authorized", "true"}}) {
        Fixture f; f.ReleasedFx(); f.ChangeExplicit(change.first, change.second);
        Check(f.Call("released").code == 4, "incomplete/stale explicit crypto observation accepted");
    }
    Fixture missing; missing.authority.positions.erase("configured_crypto_position_observation");
    Check(missing.Call().out.find("EXPLICIT_CRYPTO_POSITION_REQUIRED") != std::string::npos,
          "old runtime silently treated omitted BTC row as zero");
}

void FenceUnknownAndMalformedEvidenceFailClosed() {
    for (const auto& change : std::vector<std::pair<std::string, std::string>>{
             {"owner_fenced", "false"}, {"mutation_blocked", "true"}, {"account_local_uncertain_commands", "1"},
             {"account_local_unattributed_owned_orders", "1"}, {"account_local_unattributed_uncertain_commands", "1"},
             {"owner_fenced", "\"true\""}, {"account_broker_reason", "\"IB_ACCOUNT_BROKER_POSTFILL_RECONCILIATION_PENDING\""}}) {
        Fixture f; f.ReleasedFx(); f.authority.health[change.first] = change.second;
        Check(f.Call("released").code == 4, "unknown/unfenced/malformed health accepted");
    }
    for (const auto& change : std::vector<std::pair<std::string, std::string>>{
             {"owned_active_order_ids", "[77]"}, {"unmapped_active_order_ids", "[77]"},
             {"authoritative", "false"}, {"owner_projection_connection_epoch", "8"},
             {"active_order_ids", "[\"77\"]"}, {"active_order_ids", "[77,77]"}}) {
        Fixture f; f.ReleasedFx(); f.authority.orders[change.first] = change.second;
        Check(f.Call("released").code == 4, "unknown/owned/malformed orders accepted");
    }
    for (const auto& json : {"{\"authoritative\":true,\"authoritative\":false}",
                            "{\"owner_fenced\":true,\"owner_\\u0066enced\":false}",
                            "{\"x\":{\"k\":1,\"k\":2}}", "{} trailing", "{\"x\":NaN}"}) {
        Fixture f; f.authority.malformedHealth = json;
        Check(f.Call().code == 4, "malformed/duplicate native JSON accepted");
    }
    for (int fault = 0; fault != 5; ++fault) {
        Fixture f;
        if (fault == 0) f.authority.audit.ownerAuditComplete = false;
        if (fault == 1) f.authority.audit.reasonCode = "RECOVERY_OWNER_ZERO_CONFIRMED";
        if (fault == 2) f.authority.audit.brokerPostFillRiskReconciliationPending = true;
        if (fault == 3) f.authority.audit.brokerGlobalActiveOrderCount = 1;
        if (fault == 4) f.authority.audit.mutationBlocked = true;
        Check(f.Call().code == 4 && f.authority.reads == 0, "incomplete held op15 accepted");
    }
}

void ConcurrentCatalogWriteIsDetectedWithoutClaimingExclusiveLock(const std::string& phase = "held") {
    Fixture f;
    f.authority.onRead = [&](int index) {
        if (index != 4) return;
        auto row = f.fx; row.token = "concurrent-owner-token-0123456789abcdef";
        row.sessionId = "configured-crypto"; f.Put(row);
    };
    const auto result = f.Call(phase, "", "", "", true);
    Check(result.code == 4 && result.out.find("CATALOG_RECORD_PRESENT") != std::string::npos,
          "concurrent normal provisioning was hidden by a fictional exclusive lock");
}

void RealSocketEpochReplacementRejectsTheNextRead(const std::string& phase = "held") {
    Fixture f;
    f.authority.onRead = [&](int index) {
        if (index != 1) return;
        Check(::unlink(f.socket.c_str()) == 0, "replace socket pathname");
        const int fd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
        Check(fd >= 0, "replacement socket"); sockaddr_un address{}; address.sun_family = AF_UNIX;
        std::memcpy(address.sun_path, f.socket.c_str(), f.socket.size() + 1);
        Check(::bind(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0 && ::listen(fd, 8) == 0,
              "replacement bind/listen");
        std::string reason; ExecutionServiceIdentity identity;
        Check(GenerateExecutionServiceIdentity(2, identity, reason), reason);
        Check(f.replacement.StartFromFd(fd, {static_cast<std::uint32_t>(::geteuid())}, identity, reason, 32768, 1500), reason);
    };
    const auto result = f.Call(phase);
    Check(result.code == 4 && result.out.find("EPOCH_") != std::string::npos,
          "new server epoch between RPCs accepted: " + result.out + result.err);
    Check(f.authority.reads == 1, "new-epoch native authority was dispatched");
}
} // namespace

void RunExecutionClientSenderCredentialTests();
void RunExecutionClientAncillaryTests();

int main() {
    if (::geteuid() == 0 || ::getegid() == 0) {
        std::cerr << "Run native inactive-owner tests as an actual unprivileged uid/gid; no root skip.\n";
        return 1;
    }
    try {
        RunExecutionClientSenderCredentialTests(); RunExecutionClientAncillaryTests();
        HeldAndReleasedPreserveAuthority(); HeldUsesRealOwnerFenceWithoutInventingAnAccountFence();
        UpgradeHeldBootstrapsOnlyMaintenanceFromAnOldRuntime();
        UpgradeHeldRequiresCompleteCurrentCryptoZeroAndOriginalHold();
        UpgradeHeldNeverHidesContradictoryPositionEvidence();
        CatalogStatesRejectBeforeAnyNativeAuthority();
        CatalogRejectionDoesNotEvenDiscoverTheNativeService();
        RealPeerPidAndStartTimeArePinned(); ExplicitZeroIsNotAbsenceOrUnderflow();
        FenceUnknownAndMalformedEvidenceFailClosed();
        ConcurrentCatalogWriteIsDetectedWithoutClaimingExclusiveLock();
        ConcurrentCatalogWriteIsDetectedWithoutClaimingExclusiveLock("upgrade-held");
        RealSocketEpochReplacementRejectsTheNextRead();
        RealSocketEpochReplacementRejectsTheNextRead("upgrade-held");
        std::cout << "PASS inactive owner held/released and separate upgrade-held proofs, explicit zero, complete catalog, real peer/epoch and no authority mutations\n";
        return 0;
    } catch (const std::exception& error) { std::cerr << "FAIL " << error.what() << '\n'; return 1; }
}
