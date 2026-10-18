#include "../HeptaTrade/cli/hepta_sessionctl_owner_audit.h"
#include "../HeptaTrade/execution/unix_execution_service_server.h"
#include "../HeptaTrade/tool_host/session_supervisor_lease_store.h"
#include <atomic>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <iterator>
#include <map>
#include <mutex>
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
void Check(bool value, const std::string& detail)
{
    if (!value) throw std::runtime_error(detail);
}
void WriteFile(const std::string& path, const std::string& value, mode_t mode)
{
    std::ofstream file(path.c_str(), std::ios::binary | std::ios::trunc);
    Check(file.good(), "fixture file open");
    file.write(value.data(), static_cast<std::streamsize>(value.size()));
    file.close();
    Check(!file.fail() && ::chmod(path.c_str(), mode) == 0, "fixture private file write");
}
std::string ReadFile(const std::string& path)
{
    std::ifstream file(path.c_str(), std::ios::binary);
    Check(file.good(), "fixture file read");
    return std::string(std::istreambuf_iterator<char>(file), std::istreambuf_iterator<char>());
}
struct SavedEnvironment {
    struct Entry { std::string name; bool present; std::string value; };
    std::vector<Entry> entries;
    void Set(const std::string& name, const std::string& value)
    {
        const char* previous = std::getenv(name.c_str());
        entries.push_back(Entry{name, previous != nullptr,
            previous == nullptr ? std::string() : std::string(previous)});
        Check(::setenv(name.c_str(), value.c_str(), 1) == 0, "fixture environment");
    }
    ~SavedEnvironment()
    {
        for (auto it = entries.rbegin(); it != entries.rend(); ++it)
            if (it->present) ::setenv(it->name.c_str(), it->value.c_str(), 1);
            else ::unsetenv(it->name.c_str());
    }
};
struct CapturedStreams {
    std::ostringstream out, err;
    std::streambuf* oldOut;
    std::streambuf* oldErr;
    std::ios::fmtflags flags;
    CapturedStreams() : oldOut(std::cout.rdbuf(out.rdbuf())),
        oldErr(std::cerr.rdbuf(err.rdbuf())), flags(std::cout.flags()) {}
    ~CapturedStreams()
    {
        std::cout.rdbuf(oldOut); std::cerr.rdbuf(oldErr); std::cout.flags(flags);
    }
};
struct Outcome { int code; std::string out, err; };
struct NoRpcProbe {
    int fd;
    std::string path;
    explicit NoRpcProbe(const std::string& socketPath) :
        fd(::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0)), path(socketPath)
    {
        Check(fd >= 0, "probe socket");
        sockaddr_un address{}; address.sun_family = AF_UNIX;
        Check(path.size() < sizeof(address.sun_path), "probe path length");
        std::memcpy(address.sun_path, path.c_str(), path.size() + 1);
        Check(::bind(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0 &&
              ::listen(fd, 8) == 0, "probe bind/listen");
    }
    ~NoRpcProbe() { ::close(fd); ::unlink(path.c_str()); }
    void AssertNoConnection() const
    {
        pollfd state{}; state.fd = fd; state.events = POLLIN;
        Check(::poll(&state, 1, 0) == 0, "scope rejection opened a native RPC connection");
    }
};
class AuditAuthority : public ExecutionAuthority,
                       public ExecutionControlAuthority,
                       public ExecutionReadAuthority {
    std::mutex mutex;
    ExecutionOwnerAuditResult reply;
    ExecutionControlCommand seen;
    ExecutionControlResult OtherControl(const ExecutionControlCommand& command)
    {
        ++others;
        ExecutionControlResult result;
        result.commandId = command.context.toolCallId;
        result.reasonCode = "TEST_UNEXPECTED_CONTROL";
        return result;
    }
    ExecutionCommandResult OtherCommand(const AgentExecutionContext& context)
    {
        ++others;
        ExecutionCommandResult result;
        result.commandId = context.toolCallId;
        result.reasonCode = "TEST_UNEXPECTED_COMMAND";
        return result;
    }
public:
    std::atomic<int> audits{0}, others{0};
    void SetReply(const ExecutionOwnerAuditResult& value)
    { std::lock_guard<std::mutex> lock(mutex); reply = value; }
    ExecutionControlCommand Seen()
    { std::lock_guard<std::mutex> lock(mutex); return seen; }
    ExecutionOwnerAuditResult AuditSessionOwner(const ExecutionControlCommand& command) override
    {
        std::lock_guard<std::mutex> lock(mutex);
        ++audits; seen = command;
        ExecutionOwnerAuditResult result = reply;
        result.commandId = command.context.toolCallId;
        return result;
    }
    ExecutionCommandResult PlaceOrder(const PlaceOrderCommand& c) override
    { return OtherCommand(c.context); }
    ExecutionCommandResult CancelOrder(const CancelOrderCommand& c) override
    { return OtherCommand(c.context); }
    ExecutionCommandResult FlattenPosition(const FlattenPositionCommand& c) override
    { return OtherCommand(c.context); }
    ExecutionCommandResult PreviewOrder(const PlaceOrderCommand& c) override
    { return OtherCommand(c.context); }
    ExecutionCommandResult ReadAuthoritativeState(const ExecutionReadCommand& c) override
    { return OtherCommand(c.context); }
    ExecutionControlStatusResult QueryCommandStatus(const ExecutionControlCommand& c) override
    { return OtherControl(c); }
    ExecutionControlStatusResult FenceSessionOwner(const ExecutionControlCommand& c) override
    { return OtherControl(c); }
    ExecutionControlStatusResult FenceAccountNewEntries(const ExecutionControlCommand& c) override
    { return OtherControl(c); }
    ExecutionControlStatusResult ReleaseSessionOwnerFence(const ExecutionControlCommand& c) override
    { return OtherControl(c); }
    ExecutionControlStatusResult ReconcileAuthoritativeState(const ExecutionControlCommand& c) override
    { return OtherControl(c); }
    ExecutionOwnerAuditResult AuditSessionLease(const ExecutionControlCommand& c) override
    { return OtherControl(c); }
    ExecutionOwnerAuditResult RecoveryAuditOwner(const ExecutionControlCommand& c) override
    { return OtherControl(c); }
    ExecutionTerminalResult TerminalizeRecoveryOwner(const ExecutionControlCommand& c) override
    { return NarrowTerminalResult(OtherControl(c)); }
};
ExecutionOwnerAuditResult AcceptedReply()
{
    ExecutionOwnerAuditResult result;
    result.status = ExecutionCommandStatus::Accepted;
    result.reasonCode = "TEST_OWNER_ZERO_CONFIRMED";
    result.ownerAuditAuthoritative = true;
    result.ownerAuditComplete = true;
    result.ownerAccount = "DU12345";
    result.ownerExecutionDomain = "PAPER";
    result.mutationBlocked = true;
    result.brokerConnectionEpoch = 7;
    result.brokerActiveGeneration = 8;
    result.brokerTerminalGeneration = 9;
    result.brokerRiskGeneration = 10;
    result.brokerAccountGeneration = 11;
    result.brokerPositionGeneration = 12;
    result.brokerFxCashGeneration = 13;
    result.brokerExposureGeneration = 14;
    result.brokerTerminalExposureGeneration = 14;
    result.brokerRiskAbsorbedExposureGeneration = 14;
    result.brokerRecoveryAuditBarrierComplete = true;
    result.brokerPositionQuantity = "0";
    result.brokerGrossAbsolutePosition = "0";
    return result;
}
struct Fixture {
    AuditAuthority authority;
    UnixExecutionServiceServer server;
    std::string directory, interlock, lockPath, keyPath, storePath, tokenPath, socketPath;
    const std::string token = "owner-audit-fixture-token-0123456789abcdef";
    const std::string keyBytes = std::string(32, 'K');
    AgentOsRuntimeConfig os;
    ExecutionGatewayRuntimeConfig gateway;
    SessionSupervisorLeaseRecord record;
    Fixture() : server(authority, &authority)
    {
        char path[] = "/tmp/hepta-owner-audit-XXXXXX";
        Check(::mkdtemp(path) != nullptr, "fixture directory");
        directory = path;
        interlock = directory + "/interlock";
        lockPath = interlock + "/cleanup.lock";
        keyPath = directory + "/key";
        storePath = directory + "/leases.hsl2";
        tokenPath = directory + "/owner.token";
        socketPath = directory + "/execution.sock";
        Check(::mkdir(interlock.c_str(), 0711) == 0 &&
              ::chmod(interlock.c_str(), 0711) == 0, "fixture interlock");
        WriteFile(lockPath, "", 0644);
        WriteFile(keyPath, keyBytes, 0600);
        WriteFile(tokenPath, token + "\n", 0600);
        const auto uid = static_cast<std::uint32_t>(::geteuid());
        std::map<std::string, std::string> values;
        values["HEPTA_IB_PAPER_USER_SCOPE"] = "1";
        values["HEPTA_EXECUTION_REMOTE_MODE"] = "PAPER";
        values["HEPTA_TOOL_SUPERVISOR_LEASE_STORE"] = storePath;
        values["HEPTA_TOOL_SUPERVISOR_LEASE_KEY_FILE"] = keyPath;
        values["HEPTA_TOOL_SUPERVISOR_UID"] = std::to_string(uid);
        values["HEPTA_TOOL_AGENT_UID"] = std::to_string(uid);
        values["HEPTA_EXECUTION_SOCKET"] = socketPath;
        values["HEPTA_EXECUTION_EVENT_SOCKET"] = directory + "/events.sock";
        values["HEPTA_EXECUTION_SERVICE_UID"] = std::to_string(uid);
        os = AgentOsRuntimeConfig::FromValues(values, ::getpid(), uid);
        os.supervisorLeaseCleanupLockPath = lockPath;
        gateway = ExecutionGatewayRuntimeConfig::FromValues(values);
        std::string reason;
        Check(os.Validate(reason) && gateway.Validate(reason), reason);
        record.templateId = "paper";
        record.issuer = "hepta.os.bootstrap";
        record.token = token;
        record.agentId = "owner-audit-test";
        record.sessionId = "existing-fx-owner";
        record.ownerAccount = "DU12345";
        record.ownerExecutionDomain = "PAPER";
        record.peerUid = uid;
        record.expiresAtMs = 1;
        record.leaseGeneration = 7;
        record.fencePending = true;
        record.fenceReason = "session_expired";
        {
            SessionSupervisorLeaseStore catalog;
            Check(catalog.Init(storePath, keyPath, lockPath, uid,
                  static_cast<std::uint32_t>(::getegid()), reason), reason);
            Check(catalog.Put(record, reason), reason);
        }
        authority.SetReply(AcceptedReply());
        Check(server.Start(socketPath, {uid}, reason, 32768, 1500), reason);
    }
    ~Fixture()
    {
        server.Stop();
        ::unlink(socketPath.c_str());
        ::unlink((socketPath + ".lock").c_str());
        ::unlink(tokenPath.c_str()); ::unlink(storePath.c_str());
        ::unlink(keyPath.c_str()); ::unlink(lockPath.c_str());
        ::rmdir(interlock.c_str()); ::rmdir(directory.c_str());
    }
    Outcome Call(const std::string& changedKey = std::string())
    {
        const std::string paths[] = {storePath, keyPath, tokenPath};
        std::vector<std::string> beforeBytes;
        std::vector<struct stat> beforeStats;
        for (const auto& path : paths) {
            struct stat value;
            Check(::lstat(path.c_str(), &value) == 0, "fixture before stat");
            beforeStats.push_back(value); beforeBytes.push_back(ReadFile(path));
        }
        SavedEnvironment env;
        env.Set("HEPTA_TOOL_AGENT_ID", record.agentId);
        env.Set("HEPTA_TOOL_ACCOUNT", record.ownerAccount);
        env.Set("HEPTA_EXECUTION_DOMAIN_ID", record.ownerExecutionDomain);
        if (!changedKey.empty()) env.Set(changedKey, "different-owner-scope");
        std::string arg0 = "hepta-sessionctl", arg1 = "owner-audit", arg2 = "--token-file";
        char* argv[] = {const_cast<char*>(arg0.c_str()), const_cast<char*>(arg1.c_str()),
                        const_cast<char*>(arg2.c_str()), const_cast<char*>(tokenPath.c_str())};
        CapturedStreams streams;
        Outcome result;
        result.code = HeptaSessionCtlOwnerAudit::Run(4, argv, os, gateway);
        result.out = streams.out.str(); result.err = streams.err.str();
        for (std::size_t i = 0; i != 3; ++i) {
            struct stat after;
            Check(::lstat(paths[i].c_str(), &after) == 0 &&
                  after.st_dev == beforeStats[i].st_dev &&
                  after.st_ino == beforeStats[i].st_ino &&
                  after.st_mode == beforeStats[i].st_mode &&
                  ReadFile(paths[i]) == beforeBytes[i],
                  "owner audit rewrote a catalog, key or token");
        }
        Check(result.out.find(token) == std::string::npos &&
              result.err.find(token) == std::string::npos &&
              result.out.find(keyBytes) == std::string::npos &&
              result.err.find(keyBytes) == std::string::npos, "credential leaked in command output");
        return result;
    }
};
void ExistingExpiredFencedOwnerUsesOnlyAudit()
{
    Fixture fixture;
    const Outcome result = fixture.Call();
    Check(result.code == 0, "expired fenced owner audit failed: " + result.err + result.out);
    Check(fixture.authority.audits == 1 && fixture.authority.others == 0,
          "audit issued an extra authority operation");
    const ExecutionControlCommand command = fixture.authority.Seen();
    Check(command.context.agentId == fixture.record.agentId &&
          command.context.sessionId == fixture.record.sessionId &&
          command.context.account == fixture.record.ownerAccount &&
          command.context.executionDomain == fixture.record.ownerExecutionDomain &&
          command.context.venue == "IB" && command.context.strategy == "agent-native",
          "audit did not use the catalog owner");
    Check(!command.context.toolCallId.empty() && command.targetCommandId.empty() &&
          command.context.decisionLeaseFencingToken == 0 &&
          command.context.decisionLeaseGeneration == 0 && command.recoveryIngressFence == 0,
          "audit created mutation or recovery authority");
    Check(result.out.find("\"schema\":\"hepta_session_owner_audit_v1\"") != std::string::npos &&
          result.out.find("\"accepted\":true") != std::string::npos &&
          result.out.find("\"mutation_blocked\":true") != std::string::npos &&
          result.out.find("\"broker_recovery_audit_barrier_complete\":true") != std::string::npos,
          "accepted audit evidence was changed or lost");
    const auto identity = fixture.server.ServiceIdentity();
    Check(!identity.serviceEpoch.empty() && identity.serviceFencingGeneration != 0 &&
          result.out.find("\"service_epoch\":\"" + identity.serviceEpoch + "\"") != std::string::npos,
          "audit did not preserve the authenticated native identity");
}
void IncorrectCatalogScopeRejectsBeforeRpc()
{
    Fixture fixture;
    fixture.server.Stop();
    NoRpcProbe probe(fixture.socketPath);
    const char* keys[] = {"HEPTA_TOOL_AGENT_ID", "HEPTA_TOOL_ACCOUNT", "HEPTA_EXECUTION_DOMAIN_ID"};
    for (const char* key : keys)
    {
        const Outcome result = fixture.Call(key);
        probe.AssertNoConnection();
        Check(result.code == 2 && result.out.empty() &&
              result.err.find("OWNER_AUDIT_CATALOG_SCOPE_MISMATCH") != std::string::npos,
              "incorrect scope was not rejected before native access");
    }
    const std::string unknown = "unregistered-owner-token-0123456789abcdef";
    WriteFile(fixture.tokenPath, unknown + "\n", 0600);
    const Outcome result = fixture.Call();
    probe.AssertNoConnection();
    Check(result.code == 2 && result.out.empty() &&
          result.err.find("OWNER_AUDIT_EXISTING_TOKEN_REQUIRED") != std::string::npos &&
          result.err.find(unknown) == std::string::npos, "unknown token was accepted or exposed");
    Check(fixture.authority.audits == 0 && fixture.authority.others == 0,
          "scope rejection reached an authority callback");
}
void IncompleteOrWrongOwnerReplyCannotSucceed()
{
    Fixture fixture;
    for (int field = 0; field != 4; ++field)
    {
        auto reply = AcceptedReply();
        if (field == 0) reply.ownerAuditAuthoritative = false;
        if (field == 1) reply.ownerAuditComplete = false;
        if (field == 2) reply.ownerAccount = "DU_OTHER";
        if (field == 3) reply.ownerExecutionDomain = "OTHER_DOMAIN";
        fixture.authority.SetReply(reply);
        const Outcome result = fixture.Call();
        const std::string expected = field < 2 ?
            "OWNER_AUDIT_RESPONSE_INCOMPLETE" : "OWNER_AUDIT_RESPONSE_SCOPE_MISMATCH";
        Check(result.code == 4 && result.out.find("\"accepted\":false") != std::string::npos &&
              result.out.find(expected) != std::string::npos,
              "incomplete or wrong-owner Accepted response succeeded");
        Check(fixture.authority.audits == field + 1 && fixture.authority.others == 0,
              "invalid response triggered an extra operation or retry");
    }
}
void NativeFailureRemainsFailureWithoutRetry()
{
    Fixture fixture;
    const ExecutionCommandStatus statuses[] = {
        ExecutionCommandStatus::Rejected, ExecutionCommandStatus::Uncertain};
    int calls = 0;
    for (const auto status : statuses)
    {
        auto reply = AcceptedReply();
        reply.status = status; reply.reasonCode = "TEST_NATIVE_AUDIT_PENDING";
        fixture.authority.SetReply(reply);
        const Outcome result = fixture.Call();
        Check(result.code == 4 && result.out.find("\"accepted\":false") != std::string::npos &&
              result.out.find("\"reason_code\":\"TEST_NATIVE_AUDIT_PENDING\"") != std::string::npos,
              "native rejection or uncertainty was lost");
        Check(fixture.authority.audits == ++calls && fixture.authority.others == 0,
              "native failure triggered retry or another operation");
    }
}
}
int main()
{
    try {
        Check(::geteuid() != 0 && ::getegid() != 0,
              "user PAPER owner audit tests require an unprivileged uid/gid");
        ExistingExpiredFencedOwnerUsesOnlyAudit();
        IncorrectCatalogScopeRejectsBeforeRpc();
        IncompleteOrWrongOwnerReplyCannotSucceed();
        NativeFailureRemainsFailureWithoutRetry();
        std::cout << "hepta_sessionctl_owner_audit_tests passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "hepta_sessionctl_owner_audit_tests: " << error.what() << '\n';
        return 1;
    }
}
