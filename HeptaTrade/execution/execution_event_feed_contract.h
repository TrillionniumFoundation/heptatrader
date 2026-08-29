#pragma once

#include "../events/execution_event_hub.h"

#include <string>

enum class ExecutionEventFeedOperation
{
    GetServiceIdentity = 1,
    Wait = 2
};

struct ExecutionEventFeedRequest
{
    ExecutionEventFeedOperation operation = ExecutionEventFeedOperation::Wait;
    std::string executionDomain;
    std::string agentId;
    std::string sessionId;
    ExecutionServiceIdentity expectedServiceIdentity;
    std::uint64_t afterSequence = 0;
    int timeoutMs = 0;
};

class ExecutionEventFeedProtocol
{
public:
    static unsigned int ProtocolVersion();
    static bool EncodeRequest(const ExecutionEventFeedRequest& request,
                              std::string& body,
                              std::string& reason);
    static bool DecodeRequest(const std::string& body,
                              ExecutionEventFeedRequest& request,
                              std::string& reason);
    static bool EncodeResponse(const ExecutionEventReadResult& response,
                               std::string& body,
                               std::string& reason);
    static bool DecodeResponse(const std::string& body,
                               ExecutionEventReadResult& response,
                               std::string& reason);
};
