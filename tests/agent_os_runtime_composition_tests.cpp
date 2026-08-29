#include "../HeptaTrade/tool_host/agent_os_runtime_composition.h"
#include "../HeptaTrade/execution/execution_coordinator.h"

#include <cassert>
#include <cstring>
#include <cstdio>
#include <string>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include <vector>

namespace
{
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

TradingToolHostSessionBinding Binding(const std::string& token)
{
    TradingToolHostSessionBinding binding;
    binding.token = token;
    binding.peerUid = static_cast<std::uint32_t>(getuid());
    binding.session.executionContext.agentId = "composition-agent";
    binding.session.executionContext.sessionId = token + "-session";
    binding.session.executionContext.account = "SIM";
    binding.session.executionContext.venue = "SIM";
    binding.session.environment = "WATCH";
    binding.session.capabilities.insert("system.read");
    binding.executionDomain = "SIM:SIM:WATCH";
    binding.expiresAtMs = static_cast<std::uint64_t>(OmsJournal::NowEpochMs()) + 60000;
    return binding;
}

int ListenSocket(const std::string& path)
{
    const int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    assert(fd >= 0);
    sockaddr_un address;
    std::memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    std::strncpy(address.sun_path, path.c_str(), sizeof(address.sun_path) - 1);
    assert(bind(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0);
    assert(listen(fd, 8) == 0);
    return fd;
}
}

int main()
{
    const std::string journalPath = TempPath("/tmp/hepta-composition-journal-XXXXXX");
    OmsJournal journal;
    assert(journal.Init(journalPath));
    ExecutionCoordinatorCallbacks callbacks;
    ExecutionCoordinator execution(journal, callbacks);
    TradingToolRegistry registry(execution);
    TradingToolHost host(registry);
    const TradingToolSessionControlPlane::Authorizer authorizer = [](
        const std::string& issuer,
        const TradingToolHostSessionBinding& binding,
        std::string& reason) {
        if (issuer != "hepta.os.bootstrap" ||
            (!binding.session.executionContext.account.empty() &&
             binding.session.executionContext.account != "SIM"))
        {
            reason = "DENIED";
            return false;
        }
        reason.clear();
        return true;
    };

    AgentOsRuntimeConfig config;
    config.toolSocket = TempPath("/tmp/hepta-composition-tool-XXXXXX");
    config.supervisorSocket = TempPath("/tmp/hepta-composition-supervisor-XXXXXX");
    config.supervisorAuditJournalPath =
        TempPath("/tmp/hepta-composition-audit-XXXXXX");
    config.supervisorUid = static_cast<std::uint32_t>(getuid());
    config.agentUid = static_cast<std::uint32_t>(getuid());
    {
        AgentOsRuntimeComposition runtime(host, config, authorizer);
        std::string reason;
        const TradingToolHostSessionBinding bootstrap = Binding("composition-bootstrap-token");
        assert(runtime.StartToolServer("hepta.os.bootstrap", bootstrap, reason));
        assert(runtime.ToolServer().IsRunning());
        TradingToolHostSessionBinding stored;
        assert(host.GetSession(bootstrap.token, stored));

        assert(runtime.StartSupervisor([](const SessionSupervisorRequest& request,
                TradingToolHostSessionBinding& binding, std::string& reason) {
            binding = Binding(request.token);
            binding.peerUid = request.peerUid;
            binding.session.executionContext.agentId = request.agentId;
            binding.session.executionContext.sessionId = request.sessionId;
            reason.clear();
            return true;
        }, reason));
        assert(runtime.Supervisor().IsRunning());
        runtime.Stop();
        assert(!runtime.ToolServer().IsRunning());
        assert(!runtime.Supervisor().IsRunning());
    }
    std::uint64_t auditRecords = 0;
    std::string auditReason;
    assert(SessionSupervisorAuditJournal::Verify(
        config.supervisorAuditJournalPath, auditRecords, auditReason));

    const std::string activatedPath = TempPath("/tmp/hepta-composition-activated-XXXXXX");
    AgentOsRuntimeConfig activatedConfig;
    activatedConfig.toolSocket = activatedPath;
    activatedConfig.toolListenFd = ListenSocket(activatedPath);
    activatedConfig.allowMissingAuditForTests = true;
    {
        AgentOsRuntimeComposition runtime(host, activatedConfig, authorizer);
        std::string reason;
        const TradingToolHostSessionBinding bootstrap = Binding("composition-activated-token");
        assert(runtime.StartToolServer("hepta.os.bootstrap", bootstrap, reason));
        assert(runtime.ToolServer().IsRunning());
        runtime.Stop();
        assert(access(activatedPath.c_str(), F_OK) == 0);
    }
    unlink(activatedPath.c_str());

    AgentOsRuntimeConfig failingConfig;
    failingConfig.toolSocket = "/proc/hepta-agent-os/tool.sock";
    failingConfig.allowMissingAuditForTests = true;
    AgentOsRuntimeComposition failingRuntime(host, failingConfig, authorizer);
    const TradingToolHostSessionBinding rolledBack = Binding("composition-rollback-token");
    std::string reason;
    assert(!failingRuntime.StartToolServer("hepta.os.bootstrap", rolledBack, reason));
    assert(!reason.empty());
    TradingToolHostSessionBinding missing;
    assert(!host.GetSession(rolledBack.token, missing));

    AgentOsRuntimeConfig missingAuditConfig;
    missingAuditConfig.toolSocket =
        TempPath("/tmp/hepta-composition-missing-audit-XXXXXX");
    AgentOsRuntimeComposition missingAuditRuntime(
        host, missingAuditConfig, authorizer);
    const TradingToolHostSessionBinding auditRequired =
        Binding("composition-audit-required-token");
    assert(!missingAuditRuntime.StartToolServer(
        "hepta.os.bootstrap", auditRequired, reason));
    assert(reason == "TOOL_DECISION_AUDIT_JOURNAL_REQUIRED");
    assert(!host.GetSession(auditRequired.token, missing));

    unlink(config.toolSocket.c_str());
    unlink(config.supervisorSocket.c_str());
    unlink(config.supervisorAuditJournalPath.c_str());
    unlink(journalPath.c_str());
    return 0;
}
