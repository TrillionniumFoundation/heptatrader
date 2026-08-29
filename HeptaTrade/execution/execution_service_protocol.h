#pragma once

#include "execution_authority.h"

#include <string>

enum class ExecutionServiceOperation
{
    PlaceIbOrder = 1,
    CancelIbOrder = 2,
    QueryCommandStatus = 3,
    FenceSessionOwner = 4,
    ReleaseSessionOwnerFence = 5,
    ReconcileAuthoritativeState = 6,
    GetServiceIdentity = 7,
    ReadAuthoritativeState = 8,
    PreviewOrder = 9,
    PreviewFlattenPosition = 10,
    FlattenPosition = 11,
    RecoveryQueryCommandStatus = 12,
    RecoveryAuditOwner = 13,
    TerminalizeRecoveryOwner = 14
};

struct ExecutionServiceRequest
{
    ExecutionServiceOperation operation = ExecutionServiceOperation::PlaceIbOrder;
    IbPlaceOrderCommand place;
    IbCancelOrderCommand cancel;
    FlattenPositionCommand flatten;
    ExecutionControlCommand control;
    ExecutionReadCommand read;
    std::string expectedServiceEpoch;
    std::uint64_t expectedServiceFencingGeneration = 0;
};

class ExecutionServiceProtocol
{
public:
    static unsigned int ProtocolVersion();
    static bool EncodeRequest(const ExecutionServiceRequest& request, std::string& body, std::string& reason);
    static bool DecodeRequest(const std::string& body, ExecutionServiceRequest& request, std::string& reason);
    static bool EncodeResponse(const ExecutionCommandResult& response, std::string& body, std::string& reason);
    static bool DecodeResponse(const std::string& body, ExecutionCommandResult& response, std::string& reason);
    static bool EncodeControlResponse(const ExecutionControlResult& response,
                                      std::string& body, std::string& reason);
    static bool DecodeControlResponse(const std::string& body,
                                      ExecutionControlResult& response, std::string& reason);
};
