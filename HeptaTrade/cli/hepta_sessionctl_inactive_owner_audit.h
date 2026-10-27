#pragma once

#include "../tool_host/agent_os_runtime_config.h"
#include "../tool_host/execution_gateway_runtime_config.h"

class HeptaSessionCtlInactiveOwnerAudit
{
public:
    // A non-authorizing observation of the exact configured crypto owner.
    // Unit policy and reviewed running-binary provenance remain the operator
    // helper's responsibility; this command never grants or renews a lease.
    static int Run(int argc, char** argv, const AgentOsRuntimeConfig& osConfig,
                   const ExecutionGatewayRuntimeConfig& gateway);
};
