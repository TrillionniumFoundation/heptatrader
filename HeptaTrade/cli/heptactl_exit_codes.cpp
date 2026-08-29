#include "heptactl_exit_codes.h"

int HeptaCtlExitCodes::FromResult(const TypedToolResultEnvelope& result)
{
    if (result.status == "ok") return HeptaCtlSuccess;
    if (result.reasonCode == "INVALID_FRAME" || result.reasonCode == "INVALID_TYPED_REQUEST")
        return HeptaCtlTransportOrProtocol;
    if (result.status == "permission_denied") return HeptaCtlPermissionDenied;
    if (result.status == "invalid_tool") return HeptaCtlInvalidTool;
    if (result.status == "rejected") return HeptaCtlRejected;
    if (result.status == "duplicate") return HeptaCtlDuplicate;
    if (result.status == "uncertain") return HeptaCtlUncertain;
    return HeptaCtlServerError;
}

int HeptaCtlExitCodes::FromClientFailure(const std::string& reason)
{
    if (reason == "INVALID_RESULT_ENVELOPE" ||
        reason == "UNKNOWN_RESULT_STATUS" ||
        reason == "RESULT_TOOL_MISMATCH" ||
        reason.compare(0, 10, "DISCOVERY_") == 0)
        return HeptaCtlInvalidResponse;
    return HeptaCtlTransportOrProtocol;
}
