#pragma once

#include "../tool_host/typed_tool_protocol.h"

#include <string>

enum HeptaCtlExitCode
{
    HeptaCtlSuccess = 0,
    HeptaCtlUsageOrCredential = 2,
    HeptaCtlPermissionDenied = 3,
    HeptaCtlTransportOrProtocol = 4,
    HeptaCtlInvalidTool = 5,
    HeptaCtlRejected = 6,
    HeptaCtlDuplicate = 7,
    HeptaCtlUncertain = 8,
    HeptaCtlServerError = 9,
    HeptaCtlInvalidResponse = 10
};

class HeptaCtlExitCodes
{
public:
    static int FromResult(const TypedToolResultEnvelope& result);
    static int FromClientFailure(const std::string& reason);
};
