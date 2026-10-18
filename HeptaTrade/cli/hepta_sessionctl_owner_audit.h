#pragma once

#include "../tool_host/agent_os_runtime_config.h"
#include "../tool_host/execution_gateway_runtime_config.h"

class HeptaSessionCtlOwnerAudit
{
public:
    // Configuration is supplied by the ordinary production parsers in main.
    // This seam also permits isolated native-codec tests without host paths.
    static int Run(int argc, char** argv, const AgentOsRuntimeConfig& osConfig,
                   const ExecutionGatewayRuntimeConfig& gateway);
};
