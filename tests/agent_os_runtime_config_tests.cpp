#include "../HeptaTrade/tool_host/agent_os_runtime_config.h"

#include <cassert>
#include <map>
#include <string>

int main()
{
    const AgentOsRuntimeConfig defaults = AgentOsRuntimeConfig::FromValues(
        std::map<std::string, std::string>(), 1234, 1000);
    assert(!defaults.ToolServerEnabled());
    assert(defaults.toolListenFd == -1);
    assert(!defaults.SupervisorEnabled());
    assert(defaults.toolExecutionWorkers == 4);
    assert(defaults.supervisorUid == 1000);
    assert(defaults.agentUid == 1000);
    assert(defaults.supervisorMaxTtlMs == 86400000);
    assert(defaults.valid);
    assert(!defaults.allowMissingAuditForTests);

    // Empty string paths are an intentional disabled/unset sentinel used by
    // the legacy service unit; they must not make an otherwise disabled
    // runtime invalid.  Numeric fields remain strict when explicitly empty.
    std::map<std::string, std::string> emptyPath;
    emptyPath["HEPTA_TOOL_SOCKET"] = "";
    emptyPath["HEPTA_TOOL_SUPERVISOR_SOCKET"] = "";
    const AgentOsRuntimeConfig emptyPathConfig =
        AgentOsRuntimeConfig::FromValues(emptyPath, 1234, 1000);
    assert(emptyPathConfig.valid);
    assert(!emptyPathConfig.ToolServerEnabled());
    assert(!emptyPathConfig.SupervisorEnabled());
    emptyPath["HEPTA_TOOL_SERVER_WORKERS"] = "";
    const AgentOsRuntimeConfig emptyNumericConfig =
        AgentOsRuntimeConfig::FromValues(emptyPath, 1234, 1000);
    assert(!emptyNumericConfig.valid);
    assert(emptyNumericConfig.invalidReason ==
           "AGENT_OS_RUNTIME_CONFIG_INVALID:HEPTA_TOOL_SERVER_WORKERS");

    std::map<std::string, std::string> values;
    values["HEPTA_TOOL_SOCKET"] = "/run/hepta/tool.sock";
    values["HEPTA_TOOL_SERVER_WORKERS"] = "64";
    values["HEPTA_TOOL_SERVER_MAX_PENDING"] = "1";
    values["HEPTA_TOOL_SERVER_MAX_CONCURRENT_PER_OWNER"] = "7";
    values["HEPTA_TOOL_SERVER_MAX_PENDING_PER_OWNER"] = "48";
    values["HEPTA_TOOL_SERVER_INGRESS_WORKERS"] = "1";
    values["HEPTA_TOOL_SUPERVISOR_SOCKET"] = "/run/hepta/supervisor.sock";
    values["HEPTA_TOOL_SUPERVISOR_UID"] = "2000";
    values["HEPTA_TOOL_AGENT_UID"] = "2001";
    values["HEPTA_TOOL_SUPERVISOR_MAX_TTL_SEC"] = "60";
    values["HEPTA_TOOL_SUPERVISOR_LEASE_STORE"] = "/var/lib/hepta/leases";
    values["CREDENTIALS_DIRECTORY"] = "/run/credentials/hepta";
    values["HEPTA_TOOL_ALLOW_MISSING_AUDIT_FOR_TESTS"] = "1";
    const AgentOsRuntimeConfig configured = AgentOsRuntimeConfig::FromValues(values, 1234, 1000);
    assert(configured.valid);
    assert(configured.ToolServerEnabled());
    assert(configured.SupervisorEnabled());
    assert(configured.toolExecutionWorkers == 64);
    assert(configured.toolMaxPending == 1);
    assert(configured.toolMaxConcurrentPerOwner == 7);
    assert(configured.toolMaxPendingPerOwner == 48);
    assert(configured.toolIngressWorkers == 1);
    assert(configured.supervisorUid == 2000);
    assert(configured.agentUid == 2001);
    assert(configured.supervisorMaxTtlMs == 60000);
    assert(configured.supervisorLeaseKeyPath ==
           "/run/credentials/hepta/hepta-supervisor-lease-key");
    assert(configured.supervisorLeaseCleanupLockPath ==
           "/run/hepta-agent/session-lease-terminal-cleanup.lock");
    assert(configured.supervisorLeaseCleanupLockUid == 0);
    assert(configured.supervisorLeaseCleanupLockGid == 0);
    assert(!configured.allowMissingAuditForTests);

    values.erase("HEPTA_TOOL_SUPERVISOR_SOCKET");
    values["LISTEN_PID"] = "1234";
    values["LISTEN_FDS"] = "2";
    values["LISTEN_FDNAMES"] = "hepta-tool:hepta-supervisor";
    const AgentOsRuntimeConfig activated = AgentOsRuntimeConfig::FromValues(values, 1234, 1000);
    assert(activated.toolListenFd == 3);
    assert(activated.supervisorListenFd == 4);
    assert(activated.ToolServerEnabled());
    assert(activated.SupervisorEnabled());
    assert(!activated.allowMissingAuditForTests);

    std::map<std::string, std::string> invalid = values;
    invalid["HEPTA_TOOL_SERVER_WORKERS"] = "999";
    AgentOsRuntimeConfig rejected = AgentOsRuntimeConfig::FromValues(invalid, 1234, 1000);
    assert(!rejected.valid);
    std::string reason;
    assert(!rejected.Validate(reason));
    assert(reason == "AGENT_OS_RUNTIME_CONFIG_INVALID:HEPTA_TOOL_SERVER_WORKERS");

    invalid = values;
    invalid["HEPTA_TOOL_AGENT_UID"] = "4294967296";
    rejected = AgentOsRuntimeConfig::FromValues(invalid, 1234, 1000);
    assert(!rejected.valid);
    assert(rejected.invalidReason ==
           "AGENT_OS_RUNTIME_CONFIG_INVALID:HEPTA_TOOL_AGENT_UID");

    invalid = values;
    invalid["HEPTA_TOOL_SUPERVISOR_MAX_TTL_SEC"] = "30";
    rejected = AgentOsRuntimeConfig::FromValues(invalid, 1234, 1000);
    assert(!rejected.valid);
    assert(rejected.invalidReason ==
           "AGENT_OS_RUNTIME_CONFIG_INVALID:HEPTA_TOOL_SUPERVISOR_MAX_TTL_SEC");

    invalid = values;
    invalid["HEPTA_TOOL_SERVER_MAX_PENDING"] = " 32";
    rejected = AgentOsRuntimeConfig::FromValues(invalid, 1234, 1000);
    assert(!rejected.valid);
    assert(rejected.invalidReason ==
           "AGENT_OS_RUNTIME_CONFIG_INVALID:HEPTA_TOOL_SERVER_MAX_PENDING");

    invalid = values;
    invalid["LISTEN_PID"] = "1234";
    invalid["LISTEN_FDS"] = "65";
    rejected = AgentOsRuntimeConfig::FromValues(invalid, 1234, 1000);
    assert(!rejected.valid);
    assert(rejected.invalidReason == "AGENT_OS_RUNTIME_CONFIG_INVALID:LISTEN_FDS");
    return 0;
}
